from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from amplifai_phone.__main__ import review_capture
from amplifai_phone.ios_backup import DATABASES, _parse_entries
from amplifai_phone.metadata import (
    RETAINED_HISTORY_START,
    Contact,
    SourceCapacityLimit,
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
        self.now = datetime(2026, 9, 23, tzinfo=UTC)
        self.since = RETAINED_HISTORY_START

    def make_db(self, name: str, statements: list[str]) -> Path:
        path = self.root / name
        with sqlite3.connect(path) as db:
            for statement in statements:
                db.execute(statement)
        return path

    def make_calls(self, name: str, rows: list[tuple[object, ...]]) -> Path:
        path = self.make_db(
            name,
            [
                "CREATE TABLE ZCALLRECORD (ZDATE REAL, ZDURATION REAL, ZADDRESS TEXT, ZORIGINATED INTEGER, ZANSWERED INTEGER)"
            ],
        )
        with sqlite3.connect(path) as db:
            db.executemany("INSERT INTO ZCALLRECORD VALUES (?, ?, ?, ?, ?)", rows)
        return path

    def test_nonfinite_durations_preserve_good_calls_before_and_after(self) -> None:
        when = cocoa("2026-09-01T12:00:00+00:00")
        for index, duration in enumerate((float("inf"), float("-inf"))):
            with self.subTest(duration=duration):
                path = self.make_calls(
                    f"nonfinite-{index}.db",
                    [
                        (when, value, "+12025550101", 1, 1)
                        for value in (65.9, duration, 30)
                    ],
                )
                result = read_calls(path, self.since)
                self.assertEqual((result.rows_seen, result.excluded), (3, 0))
                self.assertEqual([call.source_id for call in result.records], [1, 2, 3])
                self.assertEqual(
                    [call.duration_seconds for call in result.records], [65, None, 30]
                )
                self.assertEqual(result.records[1].participants, ("+12025550101",))
                self.assertEqual(result.records[1].direction, "outgoing")

    def test_finite_duration_truncation_clamp_and_unknown_types_are_unchanged(
        self,
    ) -> None:
        cases = (
            (None, None),
            ("unknown duration", None),
            (b"65", None),
            (-1e308, 0),
            (-65.9, 0),
            (-1, 0),
            (0, 0),
            (0.9, 0),
            (65.9, 65),
            (86400, 86400),
            (86400.9, 86400),
            (86401, 86400),
            (1e308, 86400),
            (2**63 - 1, 86400),
        )
        when = cocoa("2026-09-01T12:00:00+00:00")
        path = self.make_calls(
            "finite-and-unknown.db",
            [(when, duration, "+12025550101", 0, 1) for duration, _expected in cases],
        )
        result = read_calls(path, self.since)
        self.assertEqual((result.rows_seen, result.excluded), (len(cases), 0))
        self.assertEqual(
            [call.duration_seconds for call in result.records],
            [expected for _duration, expected in cases],
        )

    def test_sqlite_bound_nan_is_null_not_an_infinity_reproduction(self) -> None:
        when = cocoa("2026-09-01T12:00:00+00:00")
        path = self.make_calls(
            "nan-as-null.db",
            [
                (when, duration, "+12025550101", 1, 1)
                for duration in (10, float("nan"), 20)
            ],
        )
        with sqlite3.connect(path) as db:
            value, storage_type = db.execute(
                "SELECT ZDURATION, typeof(ZDURATION) FROM ZCALLRECORD WHERE ROWID = ?",
                (2,),
            ).fetchone()
        self.assertIsNone(value)
        self.assertEqual(storage_type, "null")
        result = read_calls(path, self.since)
        self.assertEqual(
            [call.duration_seconds for call in result.records], [10, None, 20]
        )
        self.assertEqual(result.excluded, 0)

    def test_date_and_phone_exclusions_run_before_nonfinite_duration_conversion(
        self,
    ) -> None:
        when = cocoa("2026-09-01T12:00:00+00:00")
        rows = [
            (when, 65, "+12025550101", 1, 1),
            (None, float("inf"), "+12025550101", 1, 1),
            (float("inf"), float("-inf"), "+12025550101", 1, 1),
            ("invalid date", float("inf"), "+12025550101", 1, 1),
            (cocoa("2019-01-01T00:00:00+00:00"), float("inf"), "+12025550101", 1, 1),
            (when, float("-inf"), "person@example.test", 1, 1),
            (when, 30, "+12025550101", 0, 0),
        ]
        path = self.make_calls("excluded-before-duration.db", rows)
        result = read_calls(path, datetime(2026, 1, 1, tzinfo=UTC))
        self.assertEqual((result.rows_seen, result.excluded), (7, 5))
        self.assertEqual([call.source_id for call in result.records], [1, 7])
        self.assertEqual([call.duration_seconds for call in result.records], [65, 30])

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

    def test_contact_value_contract_cannot_silently_drop_the_twenty_first_value(self) -> None:
        for property_id, values in (
            (3, [f"+1202555{number:04d}" for number in range(21)]),
            (4, [f"address{number}@example.test" for number in range(21)]),
        ):
            with self.subTest(property_id=property_id):
                path = self.make_db(
                    f"contact-values-{property_id}.db",
                    [
                        "CREATE TABLE ABPerson (First TEXT, Last TEXT)",
                        "CREATE TABLE ABMultiValue (record_id INTEGER, property INTEGER, value TEXT)",
                        "INSERT INTO ABPerson VALUES ('Many', 'Values')",
                    ],
                )
                with sqlite3.connect(path) as db:
                    db.executemany(
                        "INSERT INTO ABMultiValue VALUES (1, ?, ?)",
                        [(property_id, value) for value in values[:20]],
                    )
                complete = read_contacts(path)
                self.assertEqual(
                    len(complete.records[0].phones if property_id == 3 else complete.records[0].emails),
                    20,
                )
                with sqlite3.connect(path) as db:
                    db.execute(
                        "INSERT INTO ABMultiValue VALUES (1, ?, ?)",
                        (property_id, values[20]),
                    )
                with self.assertRaises(SourceCapacityLimit):
                    read_contacts(path)

    def test_selection_can_exceed_the_old_fifty_contact_test_target(self) -> None:
        contacts = tuple(
            Contact(index, f"Person {index}", ("+15551234567",), ())
            for index in range(1, 52)
        )
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
        handles = [
            f"INSERT INTO handle VALUES ('555{index:07d}')" for index in range(51)
        ]
        joins = [
            f"INSERT INTO chat_handle_join VALUES (7, {index})"
            for index in range(1, 52)
        ]
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
        self.assertEqual(
            (result.rows_seen, len(result.records), result.excluded), (1, 0, 1)
        )

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
            def __init__(self, path: Path, relative: str):
                self.real_path, self.size = path, path.stat().st_size
                self.file_id = hashlib.sha1(
                    ("HomeDomain-" + relative).encode()
                ).hexdigest()
                self.encryption_key = b""

            def is_file(self) -> bool:
                return True

        class Backup:
            is_encrypted, keybag = False, None

            def get_entry_by_domain_and_path(
                self, domain: str, relative_path: str
            ) -> Entry:
                self_test.assertEqual(domain, "HomeDomain")
                if relative_path not in payloads:
                    raise MissingEntryError()
                return Entry(
                    dict(zip(DATABASES.values(), (contacts, calls, messages)))[
                        relative_path
                    ],
                    relative_path,
                )

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
