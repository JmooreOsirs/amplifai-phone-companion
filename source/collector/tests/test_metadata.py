from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from amplifai_phone.__main__ import review_capture
from amplifai_phone.ios_backup import DATABASES, _parse_entries
from amplifai_phone.metadata import (
    RETAINED_HISTORY_START,
    Contact,
    SourceResult,
    UnsupportedSchema,
    interactions_for_contacts,
    normalize_phone,
    read_calls,
    read_contacts,
    read_messages,
    select_people,
)


def cocoa(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() - 978307200)


class MetadataTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="amplifai-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 9, 23, tzinfo=timezone.utc)
        self.since = RETAINED_HISTORY_START

    def make_db(self, name: str, statements: list[str]) -> Path:
        path = self.root / name
        with sqlite3.connect(path) as db:
            for statement in statements:
                db.execute(statement)
        return path

    def test_contact_selection_and_phone_normalization(self) -> None:
        path = self.make_db(
            "contacts.db",
            [
                "CREATE TABLE ABPerson (First TEXT, Last TEXT, Note TEXT)",
                "CREATE TABLE ABMultiValue (record_id INTEGER, property INTEGER, value TEXT)",
                "INSERT INTO ABPerson VALUES ('Ada', 'Test', 'SECRET-NOTE')",
                "INSERT INTO ABPerson VALUES ('Bo', 'Test', NULL)",
                "INSERT INTO ABMultiValue VALUES (1, 3, '(555) 123-4567')",
                "INSERT INTO ABMultiValue VALUES (1, 4, 'Ada@example.test')",
                "INSERT INTO ABMultiValue VALUES (2, 3, '+44 20 7946 0958')",
                "INSERT INTO ABMultiValue VALUES (1, 9, 'SECRET-OTHER')",
            ],
        )
        result = read_contacts(path)
        self.assertEqual(result.rows_seen, 2)
        self.assertEqual(
            result.records[0],
            Contact(1, "Ada Test", ("+15551234567",), ("ada@example.test",)),
        )
        self.assertEqual(select_people(result, {1}), (result.records[0],))
        with self.assertRaises(ValueError):
            select_people(result, {100})
        self.assertNotIn("SECRET", repr(result))
        self.assertEqual(normalize_phone("020 7946 0958"), None)
        self.assertEqual(normalize_phone("person5551234567@example.test"), None)

    def test_selection_can_exceed_the_old_fifty_contact_test_target(self) -> None:
        contacts = tuple(Contact(index, f"Person {index}", ("+15551234567",), ()) for index in range(1, 52))
        result = SourceResult(len(contacts), contacts, 0)
        self.assertEqual(len(select_people(result, set(range(1, 52)))), 51)

    def test_retained_multiyear_calls_and_messages_without_body(self) -> None:
        call_path = self.make_db(
            "calls.db",
            [
                "CREATE TABLE ZCALLRECORD (ZDATE REAL, ZDURATION REAL, ZADDRESS TEXT, ZORIGINATED INTEGER, ZANSWERED INTEGER)",
                f"INSERT INTO ZCALLRECORD VALUES ({cocoa('2026-09-01T12:00:00+00:00')}, 65, '5551234567', 0, 1)",
                f"INSERT INTO ZCALLRECORD VALUES ({cocoa('2021-03-01T12:00:00+00:00')}, 30, '5551234567', 1, 1)",
                f"INSERT INTO ZCALLRECORD VALUES ({cocoa('2026-09-02T12:00:00+00:00')}, 0, '5559990000', 0, 0)",
            ],
        )
        sms_path = self.make_db(
            "sms.db",
            [
                "CREATE TABLE handle (id TEXT)",
                "CREATE TABLE message (date INTEGER, service TEXT, is_from_me INTEGER, handle_id INTEGER, text TEXT, attributedBody BLOB)",
                "INSERT INTO handle VALUES ('5551234567')",
                f"INSERT INTO message VALUES ({cocoa('2026-09-01T13:00:00+00:00') * 1000000000}, 'iMessage', 1, 1, 'SECRET-BODY', x'010203')",
                f"INSERT INTO message VALUES ({cocoa('2019-02-01T13:00:00+00:00')}, 'SMS', 0, 1, 'OLD-SECRET', NULL)",
            ],
        )
        calls = read_calls(call_path, self.since)
        messages = read_messages(sms_path, self.since)
        self.assertEqual(
            (calls.rows_seen, len(calls.records), calls.excluded), (3, 3, 0)
        )
        self.assertEqual(
            (messages.rows_seen, len(messages.records), messages.excluded), (2, 2, 0)
        )
        self.assertEqual(calls.records[0].duration_seconds, 65)
        self.assertEqual(messages.records[0].transport, "imessage")
        self.assertNotIn("SECRET", repr((calls, messages)))
        chosen = (Contact(1, "Ada", ("+15551234567",), ()),)
        matched = interactions_for_contacts(chosen, (calls, messages))
        self.assertEqual(len(matched), 4)

    def test_schema_fail_closed(self) -> None:
        path = self.make_db(
            "empty.db", ["CREATE TABLE message (date INTEGER, text TEXT)"]
        )
        with self.assertRaises(UnsupportedSchema):
            read_messages(path, self.since)

    def test_group_message_participants(self) -> None:
        path = self.make_db(
            "group-sms.db",
            [
                "CREATE TABLE handle (id TEXT)",
                "CREATE TABLE message (date INTEGER, service TEXT, is_from_me INTEGER, handle_id INTEGER, text TEXT)",
                "CREATE TABLE chat_message_join (message_id INTEGER, chat_id INTEGER)",
                "CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER)",
                "INSERT INTO handle VALUES ('5551234567')",
                "INSERT INTO handle VALUES ('5559876543')",
                f"INSERT INTO message VALUES ({cocoa('2026-09-01T13:00:00+00:00')}, 'iMessage', 1, 0, 'SECRET-GROUP-BODY')",
                "INSERT INTO chat_message_join VALUES (1, 7)",
                "INSERT INTO chat_handle_join VALUES (7, 1)",
                "INSERT INTO chat_handle_join VALUES (7, 2)",
            ],
        )
        result = read_messages(path, self.since)
        self.assertEqual(len(result.records), 1)
        self.assertEqual(
            result.records[0].participants, ("+15551234567", "+15559876543")
        )
        self.assertNotIn("SECRET", repr(result))

    def test_oversized_group_is_excluded_instead_of_silently_truncated(self) -> None:
        handles = [f"INSERT INTO handle VALUES ('555{index:07d}')" for index in range(51)]
        joins = [f"INSERT INTO chat_handle_join VALUES (7, {index})" for index in range(1, 52)]
        path = self.make_db(
            "oversized-group.db",
            [
                "CREATE TABLE handle (id TEXT)",
                "CREATE TABLE message (date INTEGER, service TEXT, is_from_me INTEGER, handle_id INTEGER)",
                "CREATE TABLE chat_message_join (message_id INTEGER, chat_id INTEGER)",
                "CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER)",
                *handles,
                f"INSERT INTO message VALUES ({cocoa('2026-09-01T13:00:00+00:00')}, 'SMS', 1, 0)",
                "INSERT INTO chat_message_join VALUES (1, 7)",
                *joins,
            ],
        )
        result = read_messages(path, self.since)
        self.assertEqual((result.rows_seen, len(result.records), result.excluded), (1, 0, 1))

    def test_selected_backup_entries_to_review_without_content(self) -> None:
        from pyiosbackup.exceptions import MissingEntryError

        contacts = self.make_db(
            "source-contacts",
            [
                "CREATE TABLE ABPerson (First TEXT, Last TEXT)",
                "CREATE TABLE ABMultiValue (record_id INTEGER, property INTEGER, value TEXT)",
                "INSERT INTO ABPerson VALUES ('Ada', 'Test')",
                "INSERT INTO ABMultiValue VALUES (1, 3, '5551234567')",
            ],
        )
        calls = self.make_db(
            "source-calls",
            [
                "CREATE TABLE ZCALLRECORD (ZDATE REAL, ZDURATION REAL, ZADDRESS TEXT, ZORIGINATED INTEGER, ZANSWERED INTEGER)",
                f"INSERT INTO ZCALLRECORD VALUES ({cocoa('2026-09-01T12:00:00+00:00')}, 65, '5551234567', 1, 1)",
            ],
        )
        messages = self.make_db(
            "source-messages",
            [
                "CREATE TABLE handle (id TEXT)",
                "CREATE TABLE message (date INTEGER, service TEXT, is_from_me INTEGER, handle_id INTEGER, text TEXT)",
                "INSERT INTO handle VALUES ('5551234567')",
                f"INSERT INTO message VALUES ({cocoa('2026-09-01T13:00:00+00:00') * 1000000000}, 'SMS', 0, 1, 'SECRET-BODY')",
            ],
        )
        payloads = dict(
            zip(
                DATABASES.values(),
                (contacts.read_bytes(), calls.read_bytes(), messages.read_bytes()),
            )
        )

        class Entry:
            def __init__(self, payload: bytes):
                self.payload = payload
                self.size = len(payload)

            def read_bytes(self) -> bytes:
                return self.payload

        class Backup:
            def get_entry_by_domain_and_path(
                self, domain: str, relative_path: str
            ) -> Entry:
                self_test.assertEqual(domain, "HomeDomain")
                if relative_path not in payloads:
                    raise MissingEntryError()
                return Entry(payloads[relative_path])

        self_test = self
        extracted = self.root / "extracted"
        extracted.mkdir()
        capture = _parse_entries(Backup(), extracted, self.now, self.since)
        review = review_capture(capture, "1")
        self.assertEqual(review["selected_contacts"], 1)
        self.assertEqual(review["matched_calls"], 1)
        self.assertEqual(review["matched_messages"], 1)
        self.assertEqual(review["missing_sources"], ())
        self.assertNotIn("SECRET-BODY", repr(capture))
        self.assertNotIn("SECRET-BODY", repr(review))

if __name__ == "__main__":
    unittest.main()
