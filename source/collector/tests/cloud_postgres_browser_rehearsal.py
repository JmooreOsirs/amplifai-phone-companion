"""One disposable local PG + browser bridge + fake HTTP object rehearsal.

Run explicitly with --web-checkout and --scale. No provider or owner account is used.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import plistlib
import resource
import sqlite3
import subprocess
import tempfile
import threading
import time
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import test_offline_capture_streaming_volume as volume_fixture
import test_offline_cloud_browser_bridge as broker_fixture
import test_offline_cloud_capture as capture_fixture
import test_offline_single_stage_capture as stage_fixture
from amplifai_phone.backup_watchdog import BackupWatchdog
from amplifai_phone.ios_backup import DATABASES, HOME_DOMAIN
from amplifai_phone.metadata import read_calls, read_contacts, read_messages
from amplifai_phone.offline_cloud_browser_bridge import (
    BrowserGateway,
    CloudBrowserBridge,
)
from amplifai_phone.offline_cloud_signed_transport import (
    ExactCapabilityTransport,
    ObjectHttpReply,
)
from amplifai_phone.offline_grouped_object_vfs import open_grouped_wal_source
from amplifai_phone.offline_object_vfs import ObjectVfsError
from amplifai_phone.offline_single_stage_capture import (
    PROCESSING_CIPHER_CACHE_BYTES,
    ProcessingRanges,
    SingleStageCaptureLink,
)
from amplifai_phone.record_store import RecordStore
from amplifai_phone.workspace import SessionWorkspace
from large_backup_probe import archived_entry

PG_BIN = Path("/opt/homebrew/Cellar/postgresql@17/17.11/bin")
PG_SHARE = Path("/opt/homebrew/share/postgresql@17")
PG_LIB = Path("/opt/homebrew/lib/postgresql@17")
HOST = "br-round-haze-av0a2y4r.storage.c-11.us-east-1.aws.neon.tech"
BUCKET = "amplifai-offline-fixture"
SIGNATURE = "a" * 64


def command(args: list[str], timeout: int = 45) -> str:
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise RuntimeError(f"local_postgres_command_failed:{result.returncode}")
    return result.stdout.strip()


class LocalPostgres:
    def __init__(self, root: Path, web: Path) -> None:
        self.root = root
        self.web = web
        self.data = root / "pgdata"
        self.socket = root / "pgsocket"
        self.socket.mkdir()
        self.calls = 0
        self.setup_migrations = 0
        self.base = [
            str(PG_BIN / "psql"),
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-h",
            str(self.socket),
            "-p",
            "61357",
        ]

    def start(self, owner: str, receipt: str) -> None:
        command(
            [
                str(PG_BIN / "initdb"),
                "-A",
                "trust",
                "-D",
                str(self.data),
                "--no-instructions",
                "-L",
                str(PG_BIN.parent / "share/postgresql"),
            ]
        )
        command(
            [
                str(PG_BIN / "pg_ctl"),
                "-D",
                str(self.data),
                "-l",
                str(self.root / "postgres.log"),
                "-o",
                f"-k {self.socket} -p 61357 -h ''",
                "-w",
                "start",
            ]
        )
        self.query(
            "CREATE ROLE amplifai_app NOLOGIN NOINHERIT NOBYPASSRLS; "
            "CREATE ROLE amplifai_runtime NOLOGIN NOINHERIT NOBYPASSRLS; "
            "CREATE ROLE amplifai_owner LOGIN NOINHERIT NOBYPASSRLS",
            database="postgres",
        )
        self.query(
            "CREATE DATABASE cloud_rehearsal OWNER amplifai_owner", database="postgres"
        )
        self.query(
            "SET ROLE amplifai_owner; CREATE SCHEMA neon_auth; "
            'CREATE TABLE neon_auth."user" (id uuid PRIMARY KEY, name text, email text UNIQUE, '
            '"emailVerified" boolean)'
        )
        for migration in sorted(
            (self.web / "db/neon").glob("[0-9][0-9][0-9][0-9]_*.sql")
        ):
            command(
                self.base
                + [
                    "-d",
                    "cloud_rehearsal",
                    "-U",
                    "amplifai_owner",
                    "-f",
                    str(migration),
                ]
            )
            self.setup_migrations += 1
        self.query(
            f'SET ROLE amplifai_owner; INSERT INTO neon_auth."user"(id,"emailVerified") '
            f"VALUES ('{owner}',true)"
        )
        self.query(
            "INSERT INTO app.collection_consent "
            "(id,user_id,purpose,disclosure_version,scope,accept_request_id,generation,retention_epoch) "
            f"VALUES ('{receipt}','{owner}','local-phone-metadata-collection','phone-collection-v2',"
            "'selected-contacts-available-call-sms-metadata',gen_random_uuid(),1,1)",
            owner=owner,
        )
        self.calls = 0

    def query(
        self,
        statement: str,
        *,
        owner: str | None = None,
        worker: bool = False,
        database: str = "cloud_rehearsal",
    ) -> str:
        rows = self.rows(statement, owner=owner, worker=worker, database=database)
        return rows[-1] if rows else ""

    def rows(
        self,
        statement: str,
        *,
        owner: str | None = None,
        worker: bool = False,
        database: str = "cloud_rehearsal",
    ) -> list[str]:
        if owner:
            statement = (
                "BEGIN; SET LOCAL ROLE amplifai_runtime; "
                f"SELECT set_config('app.user_id','{owner}',true); "
                + statement
                + "; COMMIT;"
            )
        elif worker:
            statement = "SET ROLE amplifai_owner; " + statement
        self.calls += 1
        output = command(self.base + ["-d", database, "-c", statement])
        return output.splitlines() if output else []

    def stop(self) -> None:
        if (self.data / "postmaster.pid").exists():
            command(
                [
                    str(PG_BIN / "pg_ctl"),
                    "-D",
                    str(self.data),
                    "-m",
                    "immediate",
                    "-w",
                    "stop",
                ]
            )


class PgGateway:
    def __init__(
        self,
        pg: LocalPostgres,
        admission,
        start: str,
        terminal: str,
        objects: stage_fixture.FinalObjects,
    ) -> None:
        self.pg = pg
        self.admission = admission
        self.start_challenge = start
        self.terminal_challenge = terminal
        self.objects = objects
        self.gateway_calls = 0
        self.verify_gets = 0
        self.batch_deletes = 0
        self.list_readbacks = 0

    def _sql(self, statement: str) -> str:
        self.gateway_calls += 1
        return self.pg.query(statement, owner=self.admission.owner_id)

    @staticmethod
    def _url(key: str, method: str) -> str:
        headers = "host%3Brange" if method == "GET" else "content-length%3Bhost"
        return (
            f"https://{HOST}/{BUCKET}/{key}?X-Amz-Expires=30&"
            f"X-Amz-SignedHeaders={headers}&X-Amz-Signature={SIGNATURE}"
        )

    def handle(self, operation: str, value: dict) -> dict:
        if (
            value.get("attemptId") != self.admission.attempt_id
            or value.get("phoneId") != self.admission.phone_id
        ):
            raise ObjectVfsError("cloud_attempt_changed")
        attempt = self.admission.attempt_id
        phone = self.admission.phone_id
        if operation == "start":
            repeated = self._sql(
                f"SELECT app.cloud_source_start('{attempt}','{phone}',"
                f"'{value['challenge']}','{value['claimSha256']}',"
                f"'{value['captureMac']}','{value['sessionNonce']}')"
            )
            return {"repeated": repeated == "t"}
        if operation == "reserve":
            row = self._sql(
                f"SELECT * FROM app.cloud_source_reserve_group('{attempt}','{phone}',"
                f"'{value['category']}','{value['role']}',{value['groupIndex']},"
                f"'{value['groupId']}','{value['firstNonce']}',{value['cipherBytes']},"
                f"'{value['cipherSha256']}')"
            )
            key, repeated = row.split("|")
            return {"objectKey": key, "repeated": repeated == "t"}
        if operation == "grant":
            row = self._sql(
                f"SELECT object_key,cipher_bytes,cipher_sha256,"
                f"extract(epoch from expires_at) FROM app.cloud_source_group_grant("
                f"'{attempt}','{phone}','{value['category']}','{value['role']}',"
                f"{value['groupIndex']},'{value['groupId']}')"
            )
            key, size, digest, expiry = row.split("|")
            return {
                "objectKey": key,
                "cipherBytes": int(size),
                "cipherSha256": digest,
                "expiresAt": float(expiry),
                "uploadUrl": self._url(key, "PUT"),
            }
        if operation == "verify":
            key = value["objectKey"]
            payload = self.objects.get_exact(key)
            self.verify_gets += 1
            if (
                len(payload) != value["cipherBytes"]
                or hashlib.sha256(payload).hexdigest() != value["cipherSha256"]
            ):
                raise ObjectVfsError("cloud_object_integrity")
            repeated = self._sql(
                f"SELECT app.cloud_source_verify_group('{attempt}','{phone}',"
                f"'{key}',{value['cipherBytes']},'{value['cipherSha256']}')"
            )
            return {"repeated": repeated == "t"}
        if operation == "terminal":
            repeated = self._sql(
                f"SELECT app.cloud_source_terminal('{attempt}','{phone}',"
                f"'{value['challenge']}','{value['claimSha256']}',"
                f"'{value['sourceSetSha256']}','{value['selectedInventorySha256']}')"
            )
            return {"repeated": repeated == "t"}
        if operation == "dispose":
            repeated = self._sql(
                f"SELECT app.cloud_source_dispose('{attempt}','{phone}',"
                f"'{value['category']}','{value['disposition']}',"
                f"{value['groupCount']},{value['cipherBytes']},"
                f"'{value['descriptorSha256']}')"
            )
            return {"repeated": repeated == "t"}
        if operation == "seal":
            # Local fake HTTP PUTs are synchronous. This fixture alone can prove no
            # in-flight ingress and inspect the complete disposable object set.
            # A real provider has no qualified barrier/setter in this candidate.
            expected = set(
                self.pg.rows(
                    "SELECT g.object_key FROM app.cloud_source_group g "
                    "JOIN app.cloud_source_attempt a ON a.id=g.attempt_id "
                    f"WHERE a.public_id='{attempt}' AND a.user_id='{self.admission.owner_id}' "
                    "AND g.state='verified'",
                    worker=True,
                )
            )
            if not expected or expected != set(self.objects.keys()):
                raise ObjectVfsError("cloud_synthetic_ingress_not_quiescent")
            self.pg.query(
                "UPDATE app.cloud_source_attempt SET "
                "last_grant_expires_at=clock_timestamp()-interval '1 second', "
                "ingress_barrier_at=clock_timestamp() "
                f"WHERE public_id='{attempt}' AND user_id='{self.admission.owner_id}' "
                "AND state='terminal'",
                worker=True,
            )
            self._sql(f"SELECT app.cloud_source_seal('{attempt}','{phone}')")
            return {"sealed": True}
        if operation == "read":
            key, size, digest = self._sql(
                f"SELECT object_key,cipher_bytes,cipher_sha256 FROM app.cloud_source_read_group("
                f"'{attempt}','{phone}','{value['category']}','{value['role']}',"
                f"{value['groupIndex']},'{value['groupId']}')"
            ).split("|")
            if (
                value["start"] < 0
                or value["end"] >= int(size)
                or value["end"] - value["start"] + 1 > 1_048_704
            ):
                raise ObjectVfsError("cloud_range_denied")
            return {
                "objectKey": key,
                "cipherBytes": int(size),
                "cipherSha256": digest,
                "start": value["start"],
                "end": value["end"],
                "readUrl": self._url(key, "GET"),
                "expiresAt": time.time() + 30,
            }
        if operation == "revoke":
            self._sql(f"SELECT app.cloud_source_revoke('{attempt}','{phone}')")
            return {"revoked": True}
        raise ObjectVfsError("cloud_operation_unavailable")

    def processing_group(self, admission, epoch, category, group_id):
        if admission != self.admission or epoch != 1:
            raise ObjectVfsError("cloud_processing_epoch_changed")
        row = self.pg.query(
            "SELECT g.object_key,g.cipher_bytes,g.cipher_sha256 FROM app.cloud_source_group g "
            "JOIN app.cloud_source_attempt a ON a.id=g.attempt_id WHERE "
            f"a.public_id='{admission.attempt_id}' AND a.user_id='{admission.owner_id}' "
            f"AND a.state='sealed' AND g.category='{category}' AND g.group_id='{group_id}' "
            "AND g.state='verified'",
            worker=True,
        )
        key, size, digest = row.split("|")
        return key, int(size), digest

    def observe_cleanup(self) -> dict[str, int | bool]:
        attempt = self.admission.attempt_id
        phone = self.admission.phone_id
        self._sql(f"SELECT app.cloud_source_revoke('{attempt}','{phone}')")
        claim = self.pg.query(
            "SELECT * FROM app.cloud_source_cleanup_claim(1)", worker=True
        )
        claimed, epoch = claim.split("|")
        if claimed != attempt:
            raise AssertionError("Wrong cleanup lease")
        cursor = 0
        while True:
            rows = self.pg.rows(
                f"SELECT * FROM app.cloud_source_cleanup_page('{attempt}',{epoch},"
                f"{cursor},1000)",
                worker=True,
            )
            if not rows or rows == [""]:
                break
            exact = [row.split("|") for row in rows]
            if len(exact) > 1000:
                raise AssertionError("Cleanup page unbounded")
            for _row_id, key in exact:
                self.objects.delete(key)
            self.batch_deletes += 1
            self.list_readbacks += 1
            if any(self.objects._path(key).exists() for _row_id, key in exact):
                raise AssertionError("Exact key remains visible")
            for row_id, _key in exact:
                self.pg.query(
                    f"SELECT app.cloud_source_cleanup_deleted('{attempt}',{epoch},{row_id})",
                    worker=True,
                )
            last = int(exact[-1][0])
            self.pg.query(
                f"SELECT app.cloud_source_cleanup_advance('{attempt}',{epoch},{cursor},{last})",
                worker=True,
            )
            cursor = last
        self.pg.query(
            f"SELECT app.cloud_source_cleanup_observed('{attempt}',{epoch})",
            worker=True,
        )
        state = self.pg.query(
            f"SELECT state || '|' || erasure_state FROM app.cloud_source_attempt "
            f"WHERE public_id='{attempt}'",
            worker=True,
        )
        return {
            "currently_absent": not self.objects.keys(),
            "pending": state == "revoked|pending",
        }


class BrowserBroker:
    def __init__(
        self, bridge: CloudBrowserBridge, token: str, gateway: PgGateway
    ) -> None:
        self.bridge = bridge
        self.token = token
        self.gateway = gateway
        self.calls = 0
        self.failure: str | None = None
        self.stop_requested = False
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_requested = True
        self.thread.join(timeout=5)

    def run(self) -> None:
        while not self.stop_requested:
            try:
                status, request = broker_fixture.http(
                    self.bridge, "/cloud/v1/next", token=self.token
                )
                if status == 204:
                    continue
                if status == 410:
                    return
                if status != 200:
                    raise RuntimeError("browser_poll_denied")
                self.calls += 1
                result = self.gateway.handle(request["operation"], request["input"])
                if request["operation"] == "reserve":
                    result = {"objectKey": result["objectKey"]}
                reply = {
                    "requestId": request["requestId"],
                    "operation": request["operation"],
                    "attemptId": request["attemptId"],
                    "response": result,
                }
                accepted, _ = broker_fixture.http(
                    self.bridge,
                    "/cloud/v1/reply",
                    method="POST",
                    token=self.token,
                    body=reply,
                )
                if accepted != 200:
                    raise RuntimeError("browser_reply_denied")
            except Exception as error:  # noqa: BLE001 - synthetic broker must fail closed
                operation = (
                    request.get("operation", "poll")
                    if "request" in locals()
                    else "poll"
                )
                self.failure = f"{operation}:{type(error).__name__}"
                self.bridge.close()
                return


def fake_http(
    gateway: BrowserGateway, objects: stage_fixture.FinalObjects, counts: dict[str, int]
):
    def request(method: str, signed: str, headers: dict[str, str], body: bytes | None):
        url = urlsplit(signed)
        key = url.path.removeprefix(f"/{BUCKET}/")
        gateway._signed_url(signed, key, method)
        if method == "PUT":
            if body is None or headers.get("Content-Length") != str(len(body)):
                raise ObjectVfsError("cloud_upload_length_changed")
            objects.put_exact(key, body, hashlib.sha256(body).hexdigest())
            counts["put"] += 1
            return ObjectHttpReply(204, None, 0, ())
        if body is not None or method != "GET":
            raise ObjectVfsError("cloud_http_method_denied")
        start, end = map(int, headers["Range"].removeprefix("bytes=").split("-"))
        reply = objects.get_range(key, start, end, 1_048_704)
        counts["range"] += 1
        chunks = tuple(
            reply.body[index : index + 128 * 1024]
            for index in range(0, len(reply.body), 128 * 1024)
        )
        return ObjectHttpReply(
            reply.status, reply.content_range, reply.content_length, chunks
        )

    return request


async def run_rehearsal(
    web: Path, scale: int, profile: str = "baseline"
) -> dict[str, object]:
    fixture = capture_fixture.CaptureFixture(
        "test_real_dispatch_projection_readback_and_parser_parity"
    )
    fixture.setUp()
    started = time.monotonic()
    root = fixture.root
    postgres_temp = tempfile.TemporaryDirectory(prefix="af-pg-", dir="/private/tmp")
    postgres_root = Path(postgres_temp.name)
    pg = LocalPostgres(postgres_root, web)
    bridge = None
    broker = None
    try:
        pg.start(fixture.admission.owner_id, fixture.admission.collection_receipt_id)
        admission_row = pg.query(
            "SELECT attempt_id,phone_id,cloud_consent_id,start_challenge,terminal_challenge "
            f"FROM app.cloud_source_admit('{fixture.admission.action_id}',"
            f"'{fixture.admission.collection_receipt_id}',1,'phone-cloud-source-v1',"
            "ARRAY['calls','contacts','messages'])",
            owner=fixture.admission.owner_id,
        )
        attempt, phone, consent, start_challenge, terminal_challenge = (
            admission_row.split("|")
        )
        fixture.admission = replace(
            fixture.admission,
            attempt_id=attempt,
            phone_id=phone,
            consent_receipt_id=consent,
        )
        fixture.authority.admission = fixture.admission
        files: list[tuple[str, str, Path | bytes]] = []
        source_lengths: dict[str, dict[str, int]] = {}
        main_blocks, wal_blocks, added_rows = (
            (6 * scale, 12 * scale, 1_000)
            if profile == "wal-over-budget"
            else (12 * scale, 8 * scale, 0)
        )
        manifest = root / "Manifest.db"
        with closing(sqlite3.connect(manifest)) as control, control:
            control.execute(
                "CREATE TABLE Files (fileID TEXT, domain TEXT, relativePath TEXT, flags INTEGER, file BLOB)"
            )
            control.execute("CREATE TABLE unrelated (private TEXT)")
            control.execute("INSERT INTO unrelated VALUES ('LOCAL-ONLY-INVENTORY')")
            for category in ("contacts", "calls", "messages"):
                source = fixture.source(category)
                db = fixture.connections[-1]
                db.execute("CREATE TABLE stream_volume (payload BLOB)")
                db.executemany(
                    "INSERT INTO stream_volume VALUES (zeroblob(65536))",
                    [()] * main_blocks,
                )
                db.commit()
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                db.executemany(
                    "INSERT INTO stream_volume VALUES (zeroblob(65536))",
                    [()] * wal_blocks,
                )
                if added_rows:
                    if category == "contacts":
                        db.executemany(
                            "INSERT INTO ABPerson VALUES (?, ?)",
                            (
                                ("Synthetic", f"Contact {index}")
                                for index in range(added_rows)
                            ),
                        )
                        db.executemany(
                            "INSERT INTO ABMultiValue VALUES (?, 3, ?)",
                            (
                                (index + 2, f"+1202{1_000_000 + index:07d}")
                                for index in range(added_rows)
                            ),
                        )
                    elif category == "calls":
                        db.executemany(
                            "INSERT INTO ZCALLRECORD VALUES (?, 65, ?, 1, 1)",
                            (
                                (800_000_001 + index, f"+1202{1_000_000 + index:07d}")
                                for index in range(added_rows)
                            ),
                        )
                    else:
                        db.executemany(
                            "INSERT INTO handle VALUES (?)",
                            (
                                (f"+1202{1_000_000 + index:07d}",)
                                for index in range(added_rows)
                            ),
                        )
                        db.executemany(
                            "INSERT INTO message VALUES (?, 'SMS', 0, ?, 'SYNTHETIC-PRIVATE-BODY')",
                            (
                                (800_000_001 + index, index + 2)
                                for index in range(added_rows)
                            ),
                        )
                db.commit()
                source_lengths[category] = {}
                for role, suffix in (("main", ""), ("wal", "-wal")):
                    member = Path(str(source) + suffix)
                    source_lengths[category][role] = member.stat().st_size
                    relative = DATABASES[category] + suffix
                    device = HOME_DOMAIN + "-" + relative
                    file_id = hashlib.sha1(device.encode()).hexdigest()
                    control.execute(
                        "INSERT INTO Files VALUES (?, ?, ?, 1, ?)",
                        (
                            file_id,
                            HOME_DOMAIN,
                            relative,
                            archived_entry(relative, member.stat().st_size),
                        ),
                    )
                    files.append((device, f"{phone}/{file_id[:2]}/{file_id}", member))
        for name, content in (
            ("Manifest.db", manifest),
            ("Manifest.plist", plistlib.dumps({"IsEncrypted": False})),
            ("Info.plist", plistlib.dumps({})),
            ("Status.plist", plistlib.dumps({"SnapshotState": "finished"})),
        ):
            files.append((name, f"{phone}/{name}", content))
        files.append(
            (
                "CameraDomain-Unrelated",
                f"{phone}/unselected/raw.db",
                b"UNSELECTED-SYNTHETIC-BYTES",
            )
        )
        objects = stage_fixture.FinalObjects(root / "final-objects", attempt)
        pg_gateway = PgGateway(
            pg, fixture.admission, start_challenge, terminal_challenge, objects
        )
        bridge = CloudBrowserBridge(lambda: None, port=0, poll_seconds=0.02)
        bridge.start()
        pair_status, pair = broker_fixture.http(
            bridge,
            "/cloud/v1/pair",
            method="POST",
            body={
                "code": bridge.code,
                "admission": broker_fixture.envelope(
                    fixture.admission, start_challenge, terminal_challenge
                ),
            },
        )
        if pair_status != 200:
            raise AssertionError("Synthetic browser pairing failed")
        browser_gateway = BrowserGateway(
            bridge, storage_host=HOST, storage_bucket=BUCKET
        )
        broker = BrowserBroker(bridge, pair["token"], pg_gateway)
        broker.start()
        counts = {"put": 0, "range": 0}
        transport = ExactCapabilityTransport(
            fixture.admission,
            browser_gateway,
            fake_http(browser_gateway, objects, counts),
        )
        index_root = root / "private-index"
        index_root.mkdir(mode=0o700)
        wire = volume_fixture.LazyWire(files, replacement=False)
        workspace = SessionWorkspace(root / "workspace")
        with workspace:
            watchdog = BackupWatchdog()
            watchdog.start()
            link = SingleStageCaptureLink(
                wire,
                workspace,
                watchdog,
                fixture.admission,
                fixture.authority,
                fixture.object_key,
                fixture.receipt_key,
                browser_gateway,
                transport,
                index_root,
                start_challenge,
                terminal_challenge,
                "A1B2C3D4E5F6",
            )
            try:
                await link.run_capture()
            except BaseException as error:
                if broker.failure:
                    raise AssertionError(
                        "Synthetic broker failed: " + broker.failure
                    ) from error
                raise
            receipt, sources = link.finalize()
            if broker.failure:
                raise AssertionError(
                    "Synthetic browser broker failed: " + broker.failure
                )
            if set(sources) != {"calls", "contacts", "messages"}:
                raise AssertionError("Selected source coverage changed")
            # Local parser invokes the qualified grouped WAL VFS. Worker epoch/result import
            # remain separate; this direct descriptor port is not a browser cookie or save ACK.
            link.gateway = pg_gateway
            virtual = root / "virtual"
            virtual.mkdir()
            seen = {}
            peak_group_cache = 0
            live_ranges = {
                category: ProcessingRanges(link, category, 1)
                for category in ("contacts", "calls", "messages")
            }
            if len({id(reader.cache) for reader in live_ranges.values()}) != 1:
                raise AssertionError("Source readers did not share the attempt cache")
            for category, reader in (
                ("contacts", read_contacts),
                ("calls", read_calls),
                ("messages", read_messages),
            ):
                baseline = RecordStore(root / f"baseline-{category}.sqlite3")
                review = RecordStore(root / f"review-{category}.sqlite3")
                try:
                    source = sources[category]
                    ranges = live_ranges[category]
                    with patch(
                        "amplifai_phone.metadata._open_readonly",
                        side_effect=lambda _path, source=source, ranges=ranges: (
                            open_grouped_wal_source(
                                source.source_set,
                                source.main_index,
                                source.wal_index,
                                ranges,
                                fixture.object_key,
                                fixture.receipt_key,
                            )
                        ),
                    ):
                        try:
                            if category == "contacts":
                                observed = reader(virtual / category, None, review)
                            else:
                                observed = reader(
                                    virtual / category,
                                    datetime(2020, 1, 1, tzinfo=UTC),
                                    store=review,
                                )
                        except BaseException as error:
                            if broker.failure:
                                raise AssertionError(
                                    "Synthetic broker failed: " + broker.failure
                                ) from error
                            raise
                    local_path = root / f"{category}.sqlite3"
                    if category == "contacts":
                        local = reader(local_path, None, baseline)
                    else:
                        local = reader(
                            local_path, datetime(2020, 1, 1, tzinfo=UTC), store=baseline
                        )
                    if observed.rows_seen != local.rows_seen or list(
                        observed.records
                    ) != list(local.records):
                        raise AssertionError("Local parser parity changed: " + category)
                    seen[category] = observed.rows_seen
                    if added_rows and (
                        observed.rows_seen != added_rows + 1
                        or len(observed.records) != added_rows + 1
                    ):
                        raise AssertionError(
                            "WAL-only history was omitted: " + category
                        )
                    peak_group_cache = max(
                        peak_group_cache, ranges.peak_group_cache_bytes
                    )
                finally:
                    baseline.close()
                    review.close()
            selected_bytes = sum(file.size for file in receipt.files)
            if profile == "wal-over-budget" and (
                selected_bytes > 1024**3
                or any(
                    lengths["wal"] <= PROCESSING_CIPHER_CACHE_BYTES
                    for lengths in source_lengths.values()
                )
            ):
                raise AssertionError(
                    "WAL workload did not cross the intended cache boundary"
                )
            if len(receipt.files) != 6 or link.backend.native_groups != len(
                objects.keys()
            ):
                raise AssertionError("One final namespace or file count changed")
            late_key = next(iter(objects.keys()))
            late_ciphertext = objects._path(late_key).read_bytes()
            late_digest = hashlib.sha256(late_ciphertext).hexdigest()
            first_cleanup = pg_gateway.observe_cleanup()
            if not first_cleanup["currently_absent"] or not first_cleanup["pending"]:
                raise AssertionError("First visibility sweep changed")
            # A previously issued PUT can complete after revocation and a first sweep.
            # This is a synthetic injection, not a claim about provider completion bounds.
            objects.put_exact(late_key, late_ciphertext, late_digest)
            counts["put"] += 1
            second_cleanup = pg_gateway.observe_cleanup()
            if not second_cleanup["currently_absent"] or not second_cleanup["pending"]:
                raise AssertionError("Resurrection sweep changed")
            if objects.keys():
                raise AssertionError("Disposable objects survived exact cleanup")
            local_control = link.backend.local_control_bytes
            local_index = sum(
                path.stat().st_size for path in index_root.glob("*.index")
            )
            local_ledger = sum(
                path.stat().st_size for path in index_root.glob("*.ledger.sqlite3")
            )
            local_review = sum(
                path.stat().st_size for path in root.glob("review-*.sqlite3")
            )
            return {
                "profile": profile,
                "source_lengths": source_lengths,
                "wal_only_rows_added_per_source": added_rows,
                "selected_bytes": selected_bytes,
                "selected_files": len(receipt.files),
                "native_groups": link.backend.native_groups,
                "final_objects_before_cleanup": link.backend.native_groups,
                "parser_rows_seen": seen,
                "browser_loopback_operations": broker.calls,
                "application_gateway_calls": pg_gateway.gateway_calls,
                "real_local_sql_round_trips": pg.calls,
                "neon_provider_control_calls": 0,
                "simulated_s3_put": counts["put"],
                "simulated_s3_late_put_included": 1,
                "simulated_s3_full_get": pg_gateway.verify_gets,
                "simulated_s3_range_get": counts["range"],
                "simulated_s3_batch_delete": pg_gateway.batch_deletes,
                "simulated_s3_list_readback": pg_gateway.list_readbacks,
                "simulated_s3_response_bytes": objects.get_bytes
                + transport.response_bytes,
                "max_device_read_bytes": wire.largest_read,
                "max_virtual_buffer_bytes": link.store.max_buffered_bytes,
                "max_backend_live_buffer_bytes": link.backend.max_buffered_bytes,
                "max_signed_transport_buffer_bytes": transport.peak_buffered_bytes,
                "max_processing_group_cache_bytes": peak_group_cache,
                "attempt_cipher_cache_budget_bytes": link.processing_cache.budget_bytes,
                "attempt_cipher_cache_current_after_cleanup_bytes": link.processing_cache.current_bytes,
                "local_control_bytes": local_control,
                "local_index_bytes": local_index,
                "local_ledger_bytes": local_ledger,
                "local_review_bytes": local_review,
                "temporary_ciphertext_high_water_bytes": objects.high_water,
                "whole_process_peak_rss_bytes": resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss,
                "local_wall_seconds": round(time.monotonic() - started, 3),
                "cleanup_currently_absent_tombstone_pending": True,
                "synthetic_late_put_reswept": True,
                "saved_account_source": False,
                "fixture_cleanup": True,
            }
    finally:
        if broker is not None:
            broker.stop()
        if bridge is not None:
            bridge.close()
        pg.stop()
        postgres_temp.cleanup()
        fixture.tearDown()
        fixture.temporary.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--web-checkout", type=Path, required=True)
    parser.add_argument("--scale", type=int, required=True)
    parser.add_argument(
        "--profile", choices=("baseline", "wal-over-budget"), default="baseline"
    )
    args = parser.parse_args()
    if (
        not 1 <= args.scale <= 250
        or not (
            args.web_checkout / "db/neon/0025_cloud_source_attempt_journal.sql"
        ).is_file()
    ):
        raise SystemExit("invalid_synthetic_rehearsal_scope")
    created = []
    for path, target in (
        (PG_SHARE, PG_BIN.parent / "share/postgresql"),
        (PG_LIB, PG_BIN.parent / "lib/postgresql"),
    ):
        if not path.exists():
            path.symlink_to(target)
            created.append(path)
    try:
        print(
            json.dumps(
                asyncio.run(run_rehearsal(args.web_checkout, args.scale, args.profile)),
                sort_keys=True,
            )
        )
    finally:
        for path in reversed(created):
            path.unlink()


if __name__ == "__main__":
    main()
