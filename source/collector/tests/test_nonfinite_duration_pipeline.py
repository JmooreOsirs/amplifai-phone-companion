"""Actual SQLite/parser/review/export/cleanup with synthetic acquisition seams.

Device acquisition, pyiosbackup's archive-entry lookup and browser transport are
substituted. No metadata reader, parser, selection, serialization or cleanup is
mocked. The password prompt below is a synthetic protocol seam, not encrypted-
device proof; received/save-state assertions do not qualify a real account save.
"""

from __future__ import annotations

import hashlib
import io
import json
import plistlib
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from amplifai_phone.__main__ import review_capture
from amplifai_phone.agent import run_connect
from amplifai_phone.bridge import selected_metadata
from amplifai_phone.ios_backup import DATABASES, IPhoneCapture, parse_selected_backup
from amplifai_phone.workspace import SessionWorkspace, abandoned_sessions

NOW = datetime(2026, 10, 1, tzinfo=UTC)
WHEN = 800_000_000
PHONE_SELECTED = "+12025550101"
PHONE_UNSELECTED = "+12025550102"
PRIVATE_BODY = "SYNTHETIC-MESSAGE-BODY-NOT-FOR-EXPORT"
PRIVATE_NOTE = "SYNTHETIC-CONTACT-NOTE-NOT-FOR-EXPORT"
PRIVATE_PASSWORD = "synthetic-password-not-for-output"
RUN_ID = "11111111-1111-4111-8111-111111111111"


class FixtureEntry:
    def __init__(self, payload: bytes, path: Path, file_id: str) -> None:
        self.size, self.real_path, self.file_id = len(payload), path, file_id
        self.encryption_key = b""

    def is_file(self) -> bool:
        return True


class FixtureBackup:
    def __init__(self, payloads: dict[str, bytes], root: Path) -> None:
        self.payloads, self.root = payloads, root
        self.is_encrypted, self.keybag = False, None

    def get_entry_by_domain_and_path(
        self, domain: str, relative_path: str
    ) -> FixtureEntry:
        if domain != "HomeDomain":
            raise AssertionError("Unexpected synthetic domain")
        if relative_path not in self.payloads:
            raise KeyError(relative_path)
        file_id = hashlib.sha1((domain + "-" + relative_path).encode()).hexdigest()
        path = self.root / file_id[:2] / file_id
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(self.payloads[relative_path])
        return FixtureEntry(self.payloads[relative_path], path, file_id)


class NonfiniteDurationPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="amplifai-duration-pipeline-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.sessions_root = self.root / "sessions"
        self.session: Path | None = None
        self.payloads = self.make_sources()

    def make_sources(self) -> dict[str, bytes]:
        contacts, calls, messages = (
            self.root / name for name in ("contacts.db", "calls.db", "messages.db")
        )
        with sqlite3.connect(contacts) as db:
            db.execute("CREATE TABLE ABPerson (First TEXT, Last TEXT, Note TEXT)")
            db.execute(
                "CREATE TABLE ABMultiValue (record_id INTEGER, property INTEGER, value TEXT)"
            )
            db.executemany(
                "INSERT INTO ABPerson VALUES (?, ?, ?)",
                [("Avery", "Lindqvist", PRIVATE_NOTE), ("Tomás", "Vega", PRIVATE_NOTE)],
            )
            db.executemany(
                "INSERT INTO ABMultiValue VALUES (?, ?, ?)",
                [(1, 3, PHONE_SELECTED), (2, 3, PHONE_UNSELECTED)],
            )
        with sqlite3.connect(calls) as db:
            db.execute(
                "CREATE TABLE ZCALLRECORD (ZDATE REAL, ZDURATION REAL, ZADDRESS TEXT, ZORIGINATED INTEGER, ZANSWERED INTEGER)"
            )
            db.executemany(
                "INSERT INTO ZCALLRECORD VALUES (?, ?, ?, ?, ?)",
                [
                    (WHEN, 65.9, PHONE_SELECTED, 1, 1),
                    (WHEN, float("inf"), PHONE_UNSELECTED, 0, 1),
                    (WHEN, float("-inf"), PHONE_SELECTED, 1, 1),
                    (WHEN, 30, PHONE_SELECTED, 0, 1),
                ],
            )
        with sqlite3.connect(messages) as db:
            db.execute("CREATE TABLE handle (id TEXT)")
            db.execute(
                "CREATE TABLE message (date INTEGER, service TEXT, is_from_me INTEGER, handle_id INTEGER, text TEXT, attributedBody BLOB)"
            )
            db.executemany(
                "INSERT INTO handle VALUES (?)",
                [(PHONE_SELECTED,), (PHONE_UNSELECTED,)],
            )
            db.executemany(
                "INSERT INTO message VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (WHEN, "SMS", 0, index, PRIVATE_BODY, PRIVATE_BODY.encode())
                    for index in (1, 2)
                ],
            )
        return {
            relative: path.read_bytes()
            for relative, path in zip(DATABASES.values(), (contacts, calls, messages))
        }

    def parse_fixture(self) -> IPhoneCapture:
        workspace = SessionWorkspace(self.sessions_root)
        with workspace as session:
            self.session = session
            backup = session / "synthetic-backup"
            backup.mkdir()
            (backup / "Manifest.plist").write_bytes(
                plistlib.dumps({"IsEncrypted": False})
            )
            (backup / "Status.plist").write_bytes(plistlib.dumps({}))
            (backup / "Info.plist").write_bytes(plistlib.dumps({}))
            with sqlite3.connect(backup / "Manifest.db") as db:
                db.execute(
                    "CREATE TABLE Files (fileID TEXT, domain TEXT, relativePath TEXT, flags INTEGER, file BLOB)"
                )
            with patch(
                "pyiosbackup.Backup", return_value=FixtureBackup(self.payloads, backup)
            ):
                capture = parse_selected_backup(backup, "", NOW, workspace.check_bound)
            self.assertEqual(list(session.glob("amplifai-db-*")), [])
        self.assertFalse(session.exists())
        self.assertEqual(abandoned_sessions(self.sessions_root), ())
        return capture

    def test_actual_parser_keeps_all_sources_and_selection_export_with_unknown_duration(
        self,
    ) -> None:
        capture = self.parse_fixture()
        self.assertEqual(
            (
                len(capture.contacts.records),
                len(capture.calls.records),
                len(capture.messages.records),
            ),
            (2, 4, 2),
        )
        self.assertEqual(capture.missing_sources, ())
        self.assertEqual(
            [call.duration_seconds for call in capture.calls.records],
            [65, None, None, 30],
        )
        selected = review_capture(capture, "1")
        unselected = review_capture(capture, "2")
        self.assertEqual(
            (selected["matched_calls"], selected["matched_messages"]), (3, 1)
        )
        self.assertEqual(
            (unselected["matched_calls"], unselected["matched_messages"]), (1, 1)
        )
        payload = json.loads(selected_metadata(capture, {1}))
        self.assertEqual([person["sourceId"] for person in payload["contacts"]], [1])
        calls = [item for item in payload["interactions"] if item["kind"] == "call"]
        self.assertEqual([item["durationSeconds"] for item in calls], [65, None, 30])
        self.assertNotIn(PHONE_UNSELECTED, json.dumps(payload))
        for forbidden in (PRIVATE_BODY, PRIVATE_NOTE, PRIVATE_PASSWORD, str(self.root)):
            self.assertNotIn(
                forbidden, repr(capture) + repr(selected) + json.dumps(payload)
            )

    def test_actual_parser_to_protocol_reviews_selected_and_unselected_and_preserves_save_gate(
        self,
    ) -> None:
        async def synthetic_device(**kwargs: object) -> IPhoneCapture:
            self.assertEqual(kwargs["password_provider"](), PRIVATE_PASSWORD)
            return self.parse_fixture()

        commands = [
            {"action": "password", "value": PRIVATE_PASSWORD},
            {"action": "review", "ids": [1]},
            {"action": "review", "ids": [2]},
            {"action": "pair"},
            {"action": "handoff-status", "handoffId": RUN_ID},
            {"action": "finish", "handoffId": RUN_ID},
            {"action": "disconnect"},
        ]
        source = io.StringIO(
            "".join(json.dumps(command) + "\n" for command in commands)
        )
        sink = io.StringIO()
        bridge = MagicMock()
        bridge.code, bridge.port = "1234567890", 48751
        bridge.handoff_id, bridge.payload_sha256 = RUN_ID, "a" * 64
        bridge.handoff_state.return_value = "received"
        with patch("amplifai_phone.agent.BridgeServer", return_value=bridge) as factory:
            self.assertEqual(run_connect(source, sink, synthetic_device), 0)
        events = [json.loads(line) for line in sink.getvalue().splitlines()]
        capture = next(event for event in events if event["kind"] == "capture")
        self.assertEqual(
            (capture["availableCalls"], capture["availableMessages"]), (4, 2)
        )
        reviews = [event for event in events if event["kind"] == "review"]
        self.assertEqual(
            [(event["matched_calls"], event["matched_messages"]) for event in reviews],
            [(3, 1), (1, 1)],
        )
        self.assertIn(
            {"kind": "handoff", "state": "received", "handoffId": RUN_ID}, events
        )
        self.assertIn({"kind": "error", "code": "handoff_unconfirmed"}, events)
        self.assertEqual(events[-1], {"kind": "state", "state": "disconnected"})
        self.assertEqual(factory.call_args.args[1], {2})
        bridge.close.assert_called_once()
        for forbidden in (
            PRIVATE_BODY,
            PRIVATE_NOTE,
            PRIVATE_PASSWORD,
            str(self.root),
            PHONE_SELECTED,
            PHONE_UNSELECTED,
        ):
            self.assertNotIn(forbidden, sink.getvalue())
        self.assertFalse(self.session.exists())

    def test_missing_calls_stays_distinct_from_unknown_duration(self) -> None:
        self.payloads.pop(DATABASES["calls"])
        capture = self.parse_fixture()
        self.assertEqual(capture.missing_sources, ("calls",))
        self.assertEqual(
            (
                len(capture.contacts.records),
                len(capture.calls.records),
                len(capture.messages.records),
            ),
            (2, 0, 2),
        )
        self.assertEqual(review_capture(capture, "1")["matched_messages"], 1)

    def test_actual_schema_failure_still_fails_closed_and_cleans_up_without_private_error_details(
        self,
    ) -> None:
        with sqlite3.connect(self.root / "unsupported.db") as db:
            db.execute("CREATE TABLE ZCALLRECORD (ZDATE REAL)")
        self.payloads[DATABASES["calls"]] = (self.root / "unsupported.db").read_bytes()

        async def synthetic_device(**_kwargs: object) -> IPhoneCapture:
            return self.parse_fixture()

        sink = io.StringIO()
        self.assertEqual(run_connect(io.StringIO(), sink, synthetic_device), 1)
        events = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(events[-1], {"kind": "error", "code": "unsupported_schema"})
        self.assertFalse(any(event["kind"] == "capture" for event in events))
        self.assertFalse(self.session.exists())
        self.assertEqual(abandoned_sessions(self.sessions_root), ())
        self.assertEqual(list(self.sessions_root.glob("session-*")), [])
        for forbidden in (PRIVATE_BODY, PRIVATE_NOTE, PRIVATE_PASSWORD, str(self.root)):
            self.assertNotIn(forbidden, sink.getvalue())


if __name__ == "__main__":
    unittest.main()
