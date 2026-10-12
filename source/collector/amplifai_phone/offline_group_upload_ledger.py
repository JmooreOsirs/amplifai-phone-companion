"""Private durable group identities for one offline captured-source attempt."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .offline_grouped_object_vfs import MAX_DESCRIPTOR_PAGE
from .offline_object_vfs import ObjectVfsError


class GroupUploadLedger:
    def __init__(
        self,
        path: Path,
        actor: tuple[str, str, str],
        source_set_id: str,
        capture_binding: str,
        consent_id: str,
        object_key: bytes,
        receipt_key: bytes,
        *,
        reopen: bool = False,
    ) -> None:
        if (
            path.exists() != reopen
            or not path.parent.is_dir()
            or path.is_symlink()
            or path.parent.is_symlink()
            or len(actor) != 3
            or len(object_key) != 32
            or len(receipt_key) != 32
        ):
            raise ObjectVfsError("cloud_upload_ledger_invalid")
        self.path = path
        self._receipt_key = receipt_key
        self.identity = (
            *actor,
            source_set_id,
            capture_binding,
            consent_id,
            hashlib.sha256(object_key + receipt_key).hexdigest(),
        )
        with self._db() as db:
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS identity (owner TEXT, phone TEXT, "
                "attempt TEXT, source_set TEXT, capture_binding TEXT, consent TEXT, "
                "key_digest TEXT, state TEXT NOT NULL CHECK(state IN "
                "('active','complete','erasure_pending','erased')))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS grp (seq INTEGER PRIMARY KEY, "
                "role TEXT NOT NULL, group_id TEXT NOT NULL UNIQUE, "
                "size INTEGER NOT NULL, digest TEXT NOT NULL, "
                "descriptors BLOB NOT NULL, object_key TEXT, "
                "state TEXT NOT NULL CHECK(state IN "
                "('prepared','reserved','verified')))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS capture_event (seq INTEGER PRIMARY KEY, "
                "role TEXT NOT NULL, generation INTEGER NOT NULL, kind TEXT NOT NULL, "
                "group_start INTEGER NOT NULL, group_end INTEGER NOT NULL, "
                "path_sha256 TEXT NOT NULL, file_size INTEGER, file_sha256 TEXT, "
                "previous_mac TEXT NOT NULL, mac TEXT NOT NULL)"
            )
            if not reopen:
                db.execute(
                    "INSERT INTO identity VALUES (?, ?, ?, ?, ?, ?, ?, 'active')",
                    self.identity,
                )
            else:
                row = db.execute("SELECT * FROM identity").fetchone()
                if row is None or row[:7] != self.identity:
                    raise ObjectVfsError("cloud_upload_ledger_binding")
        os.chmod(path, 0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path)
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @property
    def state(self) -> str:
        with self._db() as db:
            return str(db.execute("SELECT state FROM identity").fetchone()[0])

    def prepare(
        self,
        seq: int,
        role: str,
        group_id: str,
        size: int,
        digest: str,
        descriptors: tuple[tuple[object, ...], ...],
    ) -> None:
        if (
            type(seq) is not int
            or seq < 0
            or role not in ("main", "wal", "shm")
            or type(size) is not int
            or not 0 < size <= 8 * (128 * 1024 + 16)
            or len(digest) != 64
            or not isinstance(descriptors, tuple)
            or not 1 <= len(descriptors) <= 8
        ):
            raise ObjectVfsError("cloud_upload_group_invalid")
        payload = json.dumps(descriptors, separators=(",", ":")).encode("ascii")
        if len(payload) > MAX_DESCRIPTOR_PAGE:
            raise ObjectVfsError("cloud_upload_group_invalid")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT state FROM identity").fetchone() != ("active",):
                raise ObjectVfsError("cloud_upload_group_invalid")
            row = db.execute(
                "SELECT role, group_id, size, digest, descriptors FROM grp WHERE seq=?",
                (seq,),
            ).fetchone()
            identity = (role, group_id, size, digest, payload)
            if row is None:
                next_seq = db.execute("SELECT count(*) FROM grp").fetchone()[0]
                if seq != next_seq:
                    raise ObjectVfsError("cloud_upload_group_gap")
                db.execute(
                    "INSERT INTO grp VALUES (?, ?, ?, ?, ?, ?, NULL, 'prepared')",
                    (seq, *identity),
                )
            elif row != identity:
                raise ObjectVfsError("cloud_upload_group_changed")

    def reserved(self, seq: int, group_id: str, object_key: str) -> None:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT state FROM identity").fetchone() != ("active",):
                raise ObjectVfsError("cloud_upload_group_invalid")
            row = db.execute(
                "SELECT group_id, object_key, state FROM grp WHERE seq=?", (seq,)
            ).fetchone()
            if (
                row is None
                or row[0] != group_id
                or (row[1] is not None and row[1] != object_key)
            ):
                raise ObjectVfsError("cloud_upload_group_changed")
            db.execute(
                "UPDATE grp SET object_key=?, state=CASE WHEN state='verified' "
                "THEN state ELSE 'reserved' END WHERE seq=?",
                (object_key, seq),
            )

    def verified(self, seq: int) -> None:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT state FROM identity").fetchone() != ("active",):
                raise ObjectVfsError("cloud_upload_group_invalid")
            row = db.execute("SELECT state FROM grp WHERE seq=?", (seq,)).fetchone()
            if row is None or row[0] not in ("reserved", "verified"):
                raise ObjectVfsError("cloud_upload_group_unreserved")
            db.execute("UPDATE grp SET state='verified' WHERE seq=?", (seq,))

    def finish(self) -> None:
        with self._db() as db:
            missing = db.execute(
                "SELECT count(*) FROM grp WHERE state!='verified'"
            ).fetchone()[0]
            state = db.execute("SELECT state FROM identity").fetchone()[0]
            if state != "active" or missing:
                raise ObjectVfsError("cloud_upload_groups_incomplete")
            db.execute("UPDATE identity SET state='complete'")

    def mark_erasure_pending(self) -> None:
        with self._db() as db:
            db.execute(
                "UPDATE identity SET state='erasure_pending' WHERE state!='erased'"
            )

    def mark_erased(self) -> None:
        with self._db() as db:
            if (
                db.execute("SELECT state FROM identity").fetchone()[0]
                != "erasure_pending"
            ):
                raise ObjectVfsError("cloud_upload_erasure_invalid")
            db.execute("UPDATE identity SET state='erased'")

    def object_keys(self) -> tuple[str, ...]:
        with self._db() as db:
            return tuple(
                row[0]
                for row in db.execute(
                    "SELECT object_key FROM grp WHERE object_key IS NOT NULL ORDER BY seq"
                )
            )

    def append_capture_event(
        self,
        role: str,
        generation: int,
        kind: str,
        group_start: int,
        group_end: int,
        path_sha256: str,
        file_size: int | None = None,
        file_sha256: str | None = None,
    ) -> str:
        if (
            role not in ("main", "wal", "shm")
            or kind not in ("start", "complete", "delete")
            or type(generation) is not int
            or generation < 1
            or type(group_start) is not int
            or type(group_end) is not int
            or not 0 <= group_start <= group_end
            or len(path_sha256) != 64
            or (file_size is not None and (type(file_size) is not int or file_size < 0))
            or (file_sha256 is not None and len(file_sha256) != 64)
        ):
            raise ObjectVfsError("cloud_capture_event_invalid")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT state FROM identity").fetchone() != ("active",):
                raise ObjectVfsError("cloud_capture_event_closed")
            row = db.execute(
                "SELECT seq, mac FROM capture_event ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            seq, previous = (row[0] + 1, row[1]) if row else (0, "0" * 64)
            fields = (
                seq,
                role,
                generation,
                kind,
                group_start,
                group_end,
                path_sha256,
                file_size,
                file_sha256,
                previous,
            )
            mac = hmac.new(
                self._receipt_key,
                json.dumps((self.identity, fields), separators=(",", ":")).encode(
                    "ascii"
                ),
                hashlib.sha256,
            ).hexdigest()
            db.execute(
                "INSERT INTO capture_event VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (*fields, mac),
            )
            return mac

    def capture_evidence(self) -> tuple[int, int, str]:
        """Verify and stream the immutable event chain; no whole-event array."""
        previous = "0" * 64
        count = generation_count = 0
        covered_groups = 0
        pending: tuple[str, int, int, str] | None = None
        latest_generation: dict[str, int] = {}
        with self._db() as db:
            for row in db.execute("SELECT * FROM capture_event ORDER BY seq"):
                fields = row[:-1]
                expected = hmac.new(
                    self._receipt_key,
                    json.dumps((self.identity, fields), separators=(",", ":")).encode(
                        "ascii"
                    ),
                    hashlib.sha256,
                ).hexdigest()
                if (
                    row[0] != count
                    or row[-2] != previous
                    or not hmac.compare_digest(expected, row[-1])
                ):
                    raise ObjectVfsError("cloud_capture_event_changed")
                if row[3] == "start":
                    if (
                        pending is not None
                        or row[4] != covered_groups
                        or row[5] != covered_groups
                        or row[2] != latest_generation.get(row[1], 0) + 1
                    ):
                        raise ObjectVfsError("cloud_capture_generation_changed")
                    latest_generation[row[1]] = row[2]
                    pending = (row[1], row[2], row[4], row[6])
                    generation_count += 1
                elif row[3] == "complete":
                    if (
                        pending != (row[1], row[2], row[4], row[6])
                        or row[4] != covered_groups
                        or row[7] is None
                        or row[8] is None
                    ):
                        raise ObjectVfsError("cloud_capture_generation_changed")
                    matched = db.execute(
                        "SELECT count(*) FROM grp WHERE seq>=? AND seq<? AND role=? AND state='verified'",
                        (row[4], row[5], row[1]),
                    ).fetchone()[0]
                    if matched != row[5] - row[4]:
                        raise ObjectVfsError("cloud_capture_group_changed")
                    covered_groups = row[5]
                    pending = None
                elif row[3] == "delete":
                    if (
                        pending is not None
                        or row[2] != latest_generation.get(row[1])
                        or row[4] != covered_groups
                        or row[5] != covered_groups
                    ):
                        raise ObjectVfsError("cloud_capture_generation_changed")
                else:
                    raise ObjectVfsError("cloud_capture_event_invalid")
                previous = row[-1]
                count += 1
            if (
                pending is not None
                or covered_groups
                != db.execute("SELECT count(*) FROM grp").fetchone()[0]
            ):
                raise ObjectVfsError("cloud_capture_group_changed")
        return count, generation_count, previous

    def group(
        self, group_id: str
    ) -> tuple[int, str, int, str, tuple[tuple, ...], str | None, str]:
        with self._db() as db:
            row = db.execute(
                "SELECT seq, role, size, digest, descriptors, object_key, state "
                "FROM grp WHERE group_id=?",
                (group_id,),
            ).fetchone()
        if row is None:
            raise ObjectVfsError("cloud_upload_group_missing")
        return (*row[:4], tuple(tuple(entry) for entry in json.loads(row[4])), *row[5:])

    def iter_groups(self, *, order_by_role: bool = False):
        with self._db() as db:
            cursor = db.execute(
                "SELECT seq, role, group_id, size, digest, descriptors, object_key, state "
                + (
                    "FROM grp ORDER BY role, seq"
                    if order_by_role
                    else "FROM grp ORDER BY seq"
                )
            )
            while rows := cursor.fetchmany(128):
                for row in rows:
                    yield (
                        *row[:5],
                        tuple(tuple(entry) for entry in json.loads(row[5])),
                        *row[6:],
                    )

    @property
    def group_count(self) -> int:
        with self._db() as db:
            return int(db.execute("SELECT count(*) FROM grp").fetchone()[0])
