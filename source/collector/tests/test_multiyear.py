from __future__ import annotations

from contextlib import closing

import http.client
import json
import sqlite3
import tempfile
import time
import tracemalloc
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from amplifai_phone.bridge import BridgeServer, selected_metadata
from amplifai_phone.ios_backup import IPhoneCapture
from amplifai_phone.metadata import (
    RETAINED_HISTORY_START,
    read_calls,
    read_contacts,
    read_messages,
)


def cocoa(date: datetime) -> int:
    return int(date.timestamp() - 978307200)


class MultiYearPhoneTest(unittest.TestCase):
    def test_three_year_source_to_paged_local_handoff_reconciles_without_content(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-synthetic-history-") as directory:
            root = Path(directory)
            contacts_path, calls_path, messages_path = (
                root / "contacts.db", root / "calls.db", root / "messages.db"
            )
            base = datetime(2023, 1, 1, tzinfo=timezone.utc)
            phone = lambda index: f"555{index + 1:07d}"
            with closing(sqlite3.connect(contacts_path)) as db, db:
                db.execute("CREATE TABLE ABPerson (First TEXT, Last TEXT)")
                db.execute("CREATE TABLE ABMultiValue (record_id INTEGER, property INTEGER, value TEXT)")
                db.executemany("INSERT INTO ABPerson VALUES (?, ?)",
                               ((f"Person {index}", "Synthetic") for index in range(500)))
                db.executemany("INSERT INTO ABMultiValue VALUES (?, 3, ?)",
                               ((index + 1, phone(index)) for index in range(500)))
            with closing(sqlite3.connect(calls_path)) as db, db:
                db.execute("CREATE TABLE ZCALLRECORD (ZDATE REAL, ZDURATION REAL, ZADDRESS TEXT, ZORIGINATED INTEGER, ZANSWERED INTEGER)")
                db.executemany("INSERT INTO ZCALLRECORD VALUES (?, 42, ?, 0, 1)",
                               ((cocoa(base + timedelta(days=index % 1200)), phone(index % 500))
                                for index in range(12_000)))
            with closing(sqlite3.connect(messages_path)) as db, db:
                db.execute("CREATE TABLE handle (id TEXT)")
                db.execute("CREATE TABLE message (date INTEGER, service TEXT, is_from_me INTEGER, handle_id INTEGER, text TEXT)")
                db.executemany("INSERT INTO handle VALUES (?)", ((phone(index),) for index in range(500)))
                db.executemany("INSERT INTO message VALUES (?, 'SMS', 0, ?, 'PRIVATE-BODY-NEVER-TRANSFER')",
                               ((cocoa(base + timedelta(days=index % 1200)) * 1_000_000_000,
                                 index % 500 + 1) for index in range(12_000)))

            started = time.perf_counter()
            tracemalloc.start()
            try:
                contacts = read_contacts(contacts_path)
                calls = read_calls(calls_path, RETAINED_HISTORY_START)
                messages = read_messages(messages_path, RETAINED_HISTORY_START)
                capture = IPhoneCapture(contacts, calls, messages,
                                        "2026-09-28T00:00:00+00:00",
                                        RETAINED_HISTORY_START.isoformat(), ())
                selected = set(range(1, 401))
                payload = selected_metadata(capture, selected)
                _, peak_bytes = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            elapsed_seconds = time.perf_counter() - started
            self.assertEqual((contacts.rows_seen, calls.rows_seen, messages.rows_seen),
                             (500, 12_000, 12_000))
            parsed = json.loads(payload)
            self.assertEqual(len(parsed["contacts"]), 400)
            self.assertEqual(len(parsed["interactions"]), 19_200)
            self.assertEqual(sum(row["kind"] == "call" for row in parsed["interactions"]), 9_600)
            self.assertEqual(sum(row["kind"] == "message" for row in parsed["interactions"]), 9_600)
            self.assertLess(min(row["occurredAt"] for row in parsed["interactions"]), "2024-09-28")
            self.assertNotIn("PRIVATE-BODY-NEVER-TRANSFER", payload.decode())
            self.assertLess(len(payload), 32 * 1024 * 1024)
            print(f"synthetic iPhone history: 24,000 rows, 19,200 selected, {len(payload)} handoff bytes, {peak_bytes} peak traced bytes, {elapsed_seconds:.2f}s parse/serialize")

            bridge = BridgeServer(capture, selected, port=0)
            bridge.start()
            try:
                def request(method: str, path: str, body: bytes | None = None,
                            token: str | None = None) -> tuple[int, bytes]:
                    connection = http.client.HTTPConnection("127.0.0.1", bridge.port, timeout=5)
                    headers = {"Origin": bridge.origin, "Host": f"127.0.0.1:{bridge.port}"}
                    if body is not None:
                        headers["Content-Type"] = "application/json"
                    if token is not None:
                        headers["Authorization"] = f"Bearer {token}"
                    connection.request(method, path, body, headers)
                    response = connection.getresponse()
                    result = response.status, response.read()
                    connection.close()
                    return result

                status, body = request("POST", "/v1/pair", json.dumps({"code": bridge.code}).encode())
                self.assertEqual(status, 200)
                token = json.loads(body)["token"]
                chunks = []
                cursor = 0
                while True:
                    suffix = "" if cursor == 0 else f"?cursor={cursor}"
                    status, body = request("GET", "/v1/metadata" + suffix, token=token)
                    self.assertEqual(status, 200)
                    page = json.loads(body)
                    self.assertEqual(page["cursor"], cursor)
                    chunks.append(page["chunk"])
                    if page["nextCursor"] is None:
                        self.assertEqual(page["totalBytes"], len(payload))
                        break
                    cursor = page["nextCursor"]
                self.assertGreater(len(chunks), 1)
                self.assertEqual("".join(chunks).encode(), payload)
                status, body = request("POST", "/v1/complete", token=token)
                self.assertEqual((status, json.loads(body)["completed"]), (200, True))
                self.assertEqual(request("GET", "/v1/metadata", token=token)[0], 410)
            finally:
                bridge.close()


if __name__ == "__main__":
    unittest.main()
