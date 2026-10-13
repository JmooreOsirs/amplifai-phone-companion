from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from amplifai_phone.agent import _contact_preview
from amplifai_phone.bridge import selected_metadata
from amplifai_phone.ios_backup import IPhoneCapture
from amplifai_phone.metadata import Contact, SourceResult, read_contacts
from amplifai_phone.paged_transfer import _record
from amplifai_phone.record_store import RecordStore


class ContactProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="amplifai-contact-provenance-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def source(self, organization_column=False):
        path = self.root / "contacts.db"
        with closing(sqlite3.connect(path)) as db, db:
            extra = ", Organization TEXT" if organization_column else ""
            db.execute(f"CREATE TABLE ABPerson (contact_key INTEGER PRIMARY KEY, First TEXT, Last TEXT{extra})")
            db.execute("CREATE TABLE ABMultiValue (record_id INTEGER, property INTEGER, value TEXT)")
            rows = [(7, "Ada", "Lovelace"), (19, "Prince", None),
                    (31, "Ada Lovelace", ""), (73, None, "王"), (99, " ", None)]
            db.executemany("INSERT INTO ABPerson (contact_key, First, Last) VALUES (?, ?, ?)", rows)
            db.executemany("INSERT INTO ABMultiValue VALUES (?, 3, ?)",
                           [(row[0], f"+1202555{index:04d}") for index, row in enumerate(rows)])
            if organization_column:
                db.execute("UPDATE ABPerson SET Organization = 'Fictional studio' WHERE contact_key = 19")
                db.execute("UPDATE ABPerson SET Organization = '  ' WHERE contact_key = 7")
        return path

    def test_source_fields_do_not_infer_parts_from_a_combined_name(self):
        result = read_contacts(self.source())
        self.assertEqual([item.source_id for item in result.records], [7, 19, 31, 73, 99])
        self.assertEqual([item.name for item in result.records],
                         ["Ada Lovelace", "Prince", "Ada Lovelace", "王", ""])
        self.assertEqual([item.name_fields for item in result.records],
                         ["first_and_last", "first", "first", "last", "missing"])
        self.assertTrue(all(item.organization_field is None for item in result.records))
        self.assertEqual((result.rows_seen, result.excluded), (5, 0))

    def test_optional_organization_hint_survives_local_store_and_preview(self):
        store = RecordStore(self.root / "records.db")
        self.addCleanup(store.close)
        result = read_contacts(self.source(organization_column=True), store=store)
        stored = list(result.records)
        self.assertEqual([item.source_id for item in stored], [7, 19, 31, 73, 99])
        self.assertEqual([item.organization_field for item in stored], [False, True, False, False, False])
        preview = _contact_preview(stored[1])
        self.assertEqual(preview["nameFields"], "first")
        self.assertIs(preview["organizationField"], True)
        self.assertEqual(preview["name"], "Prince")
        self.assertNotIn("Fictional studio", json.dumps(preview))

    def test_legacy_local_records_do_not_claim_name_field_provenance(self):
        store = RecordStore(self.root / "legacy.db")
        self.addCleanup(store.close)
        legacy = Contact(7, "Combined Display", ("+12025550123",), ())
        store.add("contacts", legacy)
        store.db.execute("UPDATE record SET value = ?", (json.dumps({
            "source_id": 7, "name": legacy.name, "phones": legacy.phones, "emails": []
        }),))
        store.db.commit()
        item = store.collection("contacts")[0]
        self.assertIsNone(item.name_fields)
        self.assertIsNone(item.organization_field)
        self.assertNotIn("nameFields", _contact_preview(item))
        self.assertNotIn("organizationField", _contact_preview(item))

    def test_account_payloads_keep_existing_approved_fields(self):
        contacts = read_contacts(self.source(organization_column=True))
        capture = IPhoneCapture(contacts, SourceResult(0, (), 0), SourceResult(0, (), 0),
                                "2026-10-13T00:00:00Z", "1970-01-01T00:00:00Z", ())
        payload = json.loads(selected_metadata(capture, {19}))
        expected = {"sourceId": 19, "name": "Prince", "phones": ["+12025550001"]}
        self.assertEqual(payload["contacts"], [expected])
        self.assertEqual(_record(contacts.records[1]), expected)
