from __future__ import annotations

import asyncio
import plistlib
import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from amplifai_phone.ios_backup import (
    DATABASES,
    IPhoneCapture,
    MAX_PLIST_BYTES,
    SelectedPayloadIntegrityError,
    SelectedPayloadMissing,
    _extract_database,
    _parse_entries,
    _manifest_plist_path,
    _read_plist,
    collect_iphone,
    parse_selected_backup,
)
from amplifai_phone.metadata import (
    BackupControlInvalid,
    ContactsSchemaUnsupported,
    RETAINED_HISTORY_START,
    Contact,
    SourceCapacityLimit,
    SourceReadCapacityLimit,
    SourceResult,
    UnsupportedSchema,
)


async def fake_backup_control_files(**kwargs: object) -> None:
    directory = Path(kwargs["backup_directory"]) / "SYNTHETIC-UDID"
    directory.mkdir()
    (directory / "Manifest.plist").write_bytes(plistlib.dumps({"IsEncrypted": False}))


async def fake_snapshot_backup_control_files(**kwargs: object) -> None:
    directory = Path(kwargs["backup_directory"]) / "SYNTHETIC-UDID"
    snapshot = directory / "Snapshot"
    snapshot.mkdir(parents=True)
    (directory / "Manifest.plist").touch()
    (snapshot / "Manifest.plist").write_bytes(
        plistlib.dumps({"IsEncrypted": True})
    )


class ManifestFixtureIsolationTest(unittest.TestCase):
    def test_large_valid_control_plist_is_read_from_disk_without_old_16m_ceiling(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-large-plist-") as temporary:
            path = Path(temporary) / "Info.plist"
            path.write_bytes(plistlib.dumps({"SyntheticApplications": "x" * (17 * 1024 * 1024)}))
            self.assertEqual(len(_read_plist(path)["SyntheticApplications"]), 17 * 1024 * 1024)

    def test_extreme_control_plist_still_has_a_finite_parsing_bound(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-large-plist-") as temporary:
            path = Path(temporary) / "Info.plist"
            with path.open("wb") as output:
                output.seek(MAX_PLIST_BYTES)
                output.write(b"x")
            with self.assertRaises(SourceCapacityLimit):
                _read_plist(path)

    def test_invalid_manifest_control_and_contacts_shape_are_source_specific(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-control-category-") as temporary:
            directory = Path(temporary)
            (directory / "Manifest.plist").write_bytes(plistlib.dumps([]))
            with self.assertRaises(BackupControlInvalid):
                parse_selected_backup(directory, "")

        with tempfile.TemporaryDirectory(prefix="amplifai-contacts-category-") as temporary:
            with (
                patch("amplifai_phone.ios_backup._extract_database", return_value=True),
                patch("amplifai_phone.ios_backup.read_contacts", side_effect=UnsupportedSchema("private column name")),
            ):
                with self.assertRaises(ContactsSchemaUnsupported):
                    _parse_entries(object(), Path(temporary), datetime.now(UTC), RETAINED_HISTORY_START)

    def test_missing_selected_payload_has_a_specific_failure_type(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-missing-selected-") as temporary:
            directory = Path(temporary)
            entry = SimpleNamespace(
                size=12,
                file_id="a" * 40,
                is_file=lambda: True,
                real_path=directory / "absent",
                encryption_key=b"",
            )
            backup = SimpleNamespace(
                get_entry_by_domain_and_path=lambda *_: entry,
                is_encrypted=False,
            )
            with self.assertRaises(SelectedPayloadMissing):
                _extract_database(backup, DATABASES["messages"], directory / "messages")

    def test_optional_missing_payload_preserves_valid_sources_and_marks_it_missing(self) -> None:
        contacts = SourceResult(1, (Contact(1, "Ada", ("+15551234567",), ()),), 0)
        calls = SourceResult(1, (), 1)

        def extract(_backup: object, path: str, *_args: object) -> bool:
            if path == DATABASES["messages"]:
                raise SelectedPayloadMissing("Selected payload missing")
            return True

        with tempfile.TemporaryDirectory(prefix="amplifai-missing-sms-") as temporary:
            with (
                patch("amplifai_phone.ios_backup._extract_database", side_effect=extract),
                patch("amplifai_phone.ios_backup.read_contacts", return_value=contacts),
                patch("amplifai_phone.ios_backup.read_calls", return_value=calls),
                patch("amplifai_phone.ios_backup.read_messages") as read_messages,
            ):
                capture = _parse_entries(
                    object(), Path(temporary), datetime(2026, 10, 8, tzinfo=UTC),
                    RETAINED_HISTORY_START,
                )
        self.assertEqual(capture.contacts, contacts)
        self.assertEqual(capture.calls, calls)
        self.assertEqual(capture.messages, SourceResult(0, (), 0))
        self.assertEqual(capture.missing_sources, ("messages",))
        read_messages.assert_not_called()

    def test_optional_payload_length_mismatch_still_fails_the_capture(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-incomplete-sms-") as temporary:
            directory = Path(temporary)
            source = directory / "selected"
            source.write_bytes(b"short")
            entry = SimpleNamespace(
                size=12,
                file_id="a" * 40,
                is_file=lambda: True,
                real_path=source,
                encryption_key=b"",
            )
            backup = SimpleNamespace(
                get_entry_by_domain_and_path=lambda *_: entry,
                is_encrypted=False,
            )
            with self.assertRaises(SelectedPayloadIntegrityError):
                _extract_database(backup, DATABASES["messages"], directory / "messages")

        with tempfile.TemporaryDirectory(prefix="amplifai-invalid-optional-") as temporary:
            def extract(_backup: object, path: str, *_args: object) -> bool:
                if path == DATABASES["messages"]:
                    raise SelectedPayloadIntegrityError("synthetic invalid selected payload")
                return True

            with patch("amplifai_phone.ios_backup._extract_database", side_effect=extract):
                with self.assertRaises(SelectedPayloadIntegrityError):
                    _parse_entries(
                        object(), Path(temporary), datetime.now(UTC), RETAINED_HISTORY_START,
                    )

    def test_malformed_manifest_entry_types_fail_as_integrity_not_unhandled_exception(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-entry-types-") as temporary:
            directory = Path(temporary)
            for size, file_id in ((True, "a" * 40), (12, None)):
                with self.subTest(size=size, file_id=file_id):
                    entry = SimpleNamespace(
                        size=size,
                        file_id=file_id,
                        is_file=lambda: True,
                        real_path=directory / "not-needed",
                        encryption_key=b"",
                    )
                    backup = SimpleNamespace(
                        get_entry_by_domain_and_path=lambda *_: entry,
                        is_encrypted=False,
                    )
                    with self.assertRaises(SelectedPayloadIntegrityError):
                        _extract_database(backup, DATABASES["messages"], directory / "messages")

    def test_optional_source_parse_failure_preserves_valid_contacts_and_calls(self) -> None:
        contacts = SourceResult(1, (Contact(1, "Ada", ("+15551234567",), ()),), 0)
        calls = SourceResult(1, (), 1)
        for failure in (
            UnsupportedSchema("unsupported message schema"),
            SourceReadCapacityLimit("message row bound"),
            sqlite3.DatabaseError("malformed message db"),
        ):
            with self.subTest(failure=type(failure).__name__):
                with tempfile.TemporaryDirectory(prefix="amplifai-partial-source-") as temporary:
                    with (
                        patch("amplifai_phone.ios_backup._extract_database", return_value=True),
                        patch("amplifai_phone.ios_backup.read_contacts", return_value=contacts),
                        patch("amplifai_phone.ios_backup.read_calls", return_value=calls),
                        patch("amplifai_phone.ios_backup.read_messages", side_effect=failure),
                    ):
                        capture = _parse_entries(
                            object(), Path(temporary), datetime(2026, 10, 8, tzinfo=UTC),
                            RETAINED_HISTORY_START,
                        )
                self.assertEqual(capture.contacts, contacts)
                self.assertEqual(capture.calls, calls)
                self.assertEqual(capture.messages, SourceResult(0, (), 0))
                self.assertEqual(capture.missing_sources, ("messages",))

        with tempfile.TemporaryDirectory(prefix="amplifai-partial-calls-") as temporary:
            with (
                patch("amplifai_phone.ios_backup._extract_database", return_value=True),
                patch("amplifai_phone.ios_backup.read_contacts", return_value=contacts),
                patch("amplifai_phone.ios_backup.read_calls", side_effect=SourceReadCapacityLimit("call row bound")),
                patch("amplifai_phone.ios_backup.read_messages", return_value=SourceResult(0, (), 0)),
            ):
                capture = _parse_entries(
                    object(), Path(temporary), datetime(2026, 10, 8, tzinfo=UTC),
                    RETAINED_HISTORY_START,
                )
        self.assertEqual(capture.missing_sources, ("calls",))
        self.assertEqual(capture.contacts, contacts)

    def test_contact_or_session_safety_failure_still_stops_capture(self) -> None:
        contacts = SourceResult(1, (Contact(1, "Ada", ("+15551234567",), ()),), 0)
        with tempfile.TemporaryDirectory(prefix="amplifai-essential-source-") as temporary:
            with (
                patch("amplifai_phone.ios_backup._extract_database", return_value=True),
                patch("amplifai_phone.ios_backup.read_contacts", side_effect=UnsupportedSchema("bad contacts")),
            ):
                with self.assertRaises(UnsupportedSchema):
                    _parse_entries(object(), Path(temporary), datetime.now(UTC), RETAINED_HISTORY_START)
            with (
                patch("amplifai_phone.ios_backup._extract_database", return_value=True),
                patch("amplifai_phone.ios_backup.read_contacts", return_value=contacts),
                patch("amplifai_phone.ios_backup.read_calls", side_effect=SourceCapacityLimit("session bound")),
            ):
                with self.assertRaisesRegex(SourceCapacityLimit, "session bound"):
                    _parse_entries(object(), Path(temporary), datetime.now(UTC), RETAINED_HISTORY_START)

    def test_real_malformed_optional_sms_is_reported_unavailable(self) -> None:
        contacts = SourceResult(1, (Contact(1, "Ada", ("+15551234567",), ()),), 0)
        with tempfile.TemporaryDirectory(prefix="amplifai-malformed-sms-") as temporary:
            extracted = Path(temporary)
            with sqlite3.connect(extracted / "messages") as db:
                db.execute("CREATE TABLE message (text TEXT)")
            with (
                patch("amplifai_phone.ios_backup._extract_database", return_value=True),
                patch("amplifai_phone.ios_backup.read_contacts", return_value=contacts),
                patch("amplifai_phone.ios_backup.read_calls", return_value=SourceResult(0, (), 0)),
            ):
                capture = _parse_entries(object(), extracted, datetime.now(UTC), RETAINED_HISTORY_START)
        self.assertEqual(capture.missing_sources, ("messages",))
        self.assertEqual(capture.contacts, contacts)
        self.assertEqual(capture.messages, SourceResult(0, (), 0))

    def test_snapshot_manifest_fallback_rejects_links_and_missing_content(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-manifest-link-") as temporary:
            backup_path = Path(temporary) / "synthetic-backup"
            backup_path.mkdir()
            external = Path(temporary) / "external.plist"
            external.write_bytes(plistlib.dumps({"IsEncrypted": False}))
            (backup_path / "Manifest.plist").symlink_to(external)
            with self.assertRaises(UnsupportedSchema):
                _manifest_plist_path(backup_path)
            (backup_path / "Manifest.plist").unlink()
            (backup_path / "Manifest.plist").touch()
            (backup_path / "Snapshot").symlink_to(Path(temporary))
            with self.assertRaises(UnsupportedSchema):
                _manifest_plist_path(backup_path)
            (backup_path / "Snapshot").unlink()
            with self.assertRaises(UnsupportedSchema):
                _manifest_plist_path(backup_path)

    def test_completed_backup_accepts_nonempty_snapshot_manifest_when_root_is_empty(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-snapshot-manifest-") as temporary:
            backup_path = Path(temporary) / "synthetic-backup"
            snapshot = backup_path / "Snapshot"
            snapshot.mkdir(parents=True)
            (backup_path / "Manifest.plist").touch()
            (snapshot / "Manifest.plist").write_bytes(
                plistlib.dumps({"IsEncrypted": False})
            )
            for name in ("Status.plist", "Info.plist"):
                (backup_path / name).write_bytes(plistlib.dumps({}))
            manifest_path = backup_path / "Manifest.db"
            with sqlite3.connect(manifest_path) as db:
                db.execute(
                    "CREATE TABLE Files (fileID TEXT, domain TEXT, relativePath TEXT, "
                    "flags INTEGER, file BLOB)"
                )

            capture = parse_selected_backup(
                backup_path, "", now=datetime(2026, 10, 8, tzinfo=UTC)
            )
            self.assertEqual(capture.missing_sources, ("contacts", "calls", "messages"))
            self.assertEqual(list(backup_path.parent.glob("amplifai-db-*")), [])

    def test_history_fixture_leaves_real_sdk_construction_and_parsing_usable(
        self,
    ) -> None:
        import pyiosbackup.manifest_dbs.sqlite3 as provider

        sdk_class = provider.ManifestDbSqlite3
        missing = object()
        allocator_before = sdk_class.__dict__.get("__new__", missing)
        fixture = IPhoneInterfaceTest(
            "test_production_parser_requests_all_retained_history"
        )
        result = fixture.run()
        self.assertTrue(result.wasSuccessful(), result.errors + result.failures)

        with tempfile.TemporaryDirectory(prefix="amplifai-sdk-isolation-") as temporary:
            backup_path = Path(temporary) / "synthetic-backup"
            backup_path.mkdir()
            for name, value in (
                ("Manifest.plist", {"IsEncrypted": False}),
                ("Status.plist", {}),
                ("Info.plist", {}),
            ):
                (backup_path / name).write_bytes(plistlib.dumps(value))
            manifest_path = backup_path / "Manifest.db"
            with sqlite3.connect(manifest_path) as db:
                db.execute(
                    "CREATE TABLE Files (fileID TEXT, domain TEXT, relativePath TEXT, "
                    "flags INTEGER, file BLOB)"
                )

            # Neither the SDK constructor nor the parser is mocked here.
            manifest_db = sdk_class(manifest_path)
            try:
                count = manifest_db._conn.execute(
                    "SELECT COUNT(*) FROM Files"
                ).fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                manifest_db._conn.close()
            capture = parse_selected_backup(
                backup_path, "", now=datetime(2026, 10, 1, tzinfo=UTC)
            )
            self.assertEqual(capture.missing_sources, ("contacts", "calls", "messages"))
            self.assertEqual(capture.since, RETAINED_HISTORY_START.isoformat())
            self.assertEqual(list(backup_path.parent.glob("amplifai-db-*")), [])

        self.assertIs(provider.ManifestDbSqlite3, sdk_class)
        self.assertIs(sdk_class.__dict__.get("__new__", missing), allocator_before)


class IPhoneInterfaceTest(unittest.IsolatedAsyncioTestCase):
    def test_production_parser_requests_all_retained_history(self) -> None:
        import pyiosbackup
        from pyiosbackup.manifest_plist import ManifestPlist

        with tempfile.TemporaryDirectory(prefix="amplifai-history-test-") as temporary:
            backup_path = Path(temporary)
            (backup_path / "Manifest.plist").write_bytes(
                plistlib.dumps({"IsEncrypted": False})
            )
            (backup_path / "Manifest.db").write_bytes(b"synthetic manifest")
            (backup_path / "Status.plist").write_bytes(plistlib.dumps({}))
            (backup_path / "Info.plist").write_bytes(plistlib.dumps({}))
            expected = IPhoneCapture(
                SourceResult(0, (), 0),
                SourceResult(0, (), 0),
                SourceResult(0, (), 0),
                "now",
                RETAINED_HISTORY_START.isoformat(),
                (),
            )
            manifest_db = MagicMock()
            with (
                patch.object(
                    ManifestPlist,
                    "from_path",
                    return_value=SimpleNamespace(is_encrypted=False),
                ),
                patch(
                    "pyiosbackup.manifest_dbs.sqlite3.ManifestDbSqlite3",
                    return_value=manifest_db,
                ) as manifest_factory,
                patch.object(pyiosbackup, "Backup", return_value=MagicMock()),
                patch(
                    "amplifai_phone.ios_backup._parse_entries", return_value=expected
                ) as parse,
            ):
                result = parse_selected_backup(
                    backup_path, "", now=datetime(2026, 9, 25, tzinfo=UTC)
                )
            self.assertIs(result, expected)
            self.assertEqual(parse.call_args.args[3], RETAINED_HISTORY_START)
            manifest_factory.assert_called_once()
            manifest_db._conn.close.assert_called_once_with()

    async def test_filtered_backup_no_unback_and_password_only_if_encrypted(
        self,
    ) -> None:
        import pymobiledevice3.lockdown
        import pymobiledevice3.services.mobilebackup2
        import pymobiledevice3.usbmux
        from pyiosbackup.manifest_plist import ManifestPlist

        device = SimpleNamespace(
            is_usb=True,
            is_network=False,
            connection_type="USB",
            serial="SYNTHETIC-UDID",
        )
        lockdown = MagicMock()
        lockdown.udid = "SYNTHETIC-UDID"
        lockdown.__aenter__ = AsyncMock(return_value=lockdown)
        lockdown.__aexit__ = AsyncMock(return_value=False)
        service = MagicMock()
        service.__aenter__ = AsyncMock(return_value=service)
        service.__aexit__ = AsyncMock(return_value=False)
        service.backup = AsyncMock(side_effect=fake_snapshot_backup_control_files)
        service.service._ensure_started = AsyncMock(
            return_value=(asyncio.StreamReader(), MagicMock())
        )
        service.resolve_backup_selection.return_value = ("selection-rules",)
        service.selection_filter_callback.return_value = "selection-filter"
        expected = IPhoneCapture(
            SourceResult(0, (), 0),
            SourceResult(0, (), 0),
            SourceResult(0, (), 0),
            "now",
            "since",
            (),
        )
        asked = []

        with (
            tempfile.TemporaryDirectory(prefix="amplifai-usb-test-") as temporary,
            patch.object(
                pymobiledevice3.usbmux,
                "list_devices",
                new=AsyncMock(return_value=[device]),
            ),
            patch.object(
                pymobiledevice3.lockdown,
                "create_using_usbmux",
                new=AsyncMock(return_value=lockdown),
            ) as connect,
            patch.object(
                pymobiledevice3.services.mobilebackup2,
                "Mobilebackup2Service",
                return_value=service,
            ),
            patch.object(
                ManifestPlist,
                "from_path",
                return_value=SimpleNamespace(is_encrypted=True),
            ),
            patch(
                "amplifai_phone.ios_backup.parse_selected_backup", return_value=expected
            ) as parse,
        ):
            result = await collect_iphone(
                password_provider=lambda: asked.append(True) or "synthetic-password",
                now=datetime(2026, 9, 23, tzinfo=UTC),
                sessions_root=Path(temporary) / "sessions",
            )
        self.assertIs(result, expected)
        self.assertEqual(asked, [True])
        connect.assert_awaited_once()
        self.assertEqual(connect.await_args.kwargs["connection_type"], "USB")
        self.assertEqual(connect.await_args.kwargs["pair_timeout"], 120)
        service.resolve_backup_selection.assert_called_once_with(
            ("contacts", "call_history", "sms")
        )
        self.assertEqual(
            service.backup.await_args.kwargs["filter_callback"], "selection-filter"
        )
        self.assertTrue(service.backup.await_args.kwargs["full"])
        self.assertFalse(service.backup.await_args.kwargs["unback"])
        self.assertFalse(service.backup.await_args.kwargs["patch_manifest"])
        self.assertEqual(parse.call_args.args[1], "synthetic-password")

    async def test_zero_devices_stops_before_pairing(self) -> None:
        import pymobiledevice3.usbmux

        with (
            patch.object(
                pymobiledevice3.usbmux, "list_devices", new=AsyncMock(return_value=[])
            ),
            self.assertRaisesRegex(RuntimeError, "exactly one"),
        ):
            await collect_iphone(password_provider=lambda: "")

    async def test_usb_precedes_existing_network_pairing(
        self,
    ) -> None:
        import pymobiledevice3.lockdown
        import pymobiledevice3.services.mobilebackup2
        import pymobiledevice3.usbmux
        from pyiosbackup.manifest_plist import ManifestPlist

        devices = [
            SimpleNamespace(
                is_usb=True,
                is_network=False,
                connection_type="USB",
                serial="SYNTHETIC-UDID",
            ),
            SimpleNamespace(
                is_usb=False,
                is_network=True,
                connection_type="Network",
                serial="SYNTHETIC-UDID",
            ),
        ]
        lockdown = MagicMock()
        lockdown.paired = True
        lockdown.udid = "SYNTHETIC-UDID"
        lockdown.__aenter__ = AsyncMock(return_value=lockdown)
        lockdown.__aexit__ = AsyncMock(return_value=False)
        service = MagicMock()
        service.__aenter__ = AsyncMock(return_value=service)
        service.__aexit__ = AsyncMock(return_value=False)
        service.backup = AsyncMock(side_effect=fake_backup_control_files)
        service.service._ensure_started = AsyncMock(
            return_value=(asyncio.StreamReader(), MagicMock())
        )
        expected = IPhoneCapture(
            SourceResult(0, (), 0),
            SourceResult(0, (), 0),
            SourceResult(0, (), 0),
            "now",
            "since",
            (),
        )
        transports: list[str] = []
        with (
            tempfile.TemporaryDirectory(prefix="amplifai-wifi-test-") as temporary,
            patch.object(
                pymobiledevice3.usbmux,
                "list_devices",
                new=AsyncMock(return_value=devices),
            ),
            patch.object(
                pymobiledevice3.lockdown,
                "create_using_usbmux",
                new=AsyncMock(return_value=lockdown),
            ) as connect,
            patch.object(
                pymobiledevice3.services.mobilebackup2,
                "Mobilebackup2Service",
                return_value=service,
            ),
            patch.object(
                ManifestPlist,
                "from_path",
                return_value=SimpleNamespace(is_encrypted=False),
            ),
            patch(
                "amplifai_phone.ios_backup.parse_selected_backup", return_value=expected
            ),
        ):
            result = await collect_iphone(
                password_provider=lambda: self.fail("password not needed"),
                connection_callback=transports.append,
                sessions_root=Path(temporary) / "sessions",
            )
        self.assertIs(result, expected)
        self.assertEqual(transports, ["usb"])
        connect.assert_awaited_once()
        self.assertEqual(connect.await_args.kwargs["connection_type"], "USB")
        self.assertTrue(connect.await_args.kwargs["autopair"])
        self.assertEqual(connect.await_args.kwargs["pair_timeout"], 120)

    async def test_usb_ignores_unpaired_network_for_same_phone(self) -> None:
        import pymobiledevice3.lockdown
        import pymobiledevice3.services.mobilebackup2
        import pymobiledevice3.usbmux
        from pyiosbackup.manifest_plist import ManifestPlist

        devices = [
            SimpleNamespace(
                is_usb=False,
                is_network=True,
                connection_type="Network",
                serial="SYNTHETIC-UDID",
            ),
            SimpleNamespace(
                is_usb=True,
                is_network=False,
                connection_type="USB",
                serial="SYNTHETIC-UDID",
            ),
        ]
        usb = MagicMock()
        usb.udid = "SYNTHETIC-UDID"
        usb.__aenter__ = AsyncMock(return_value=usb)
        usb.__aexit__ = AsyncMock(return_value=False)
        service = MagicMock()
        service.__aenter__ = AsyncMock(return_value=service)
        service.__aexit__ = AsyncMock(return_value=False)
        service.backup = AsyncMock(side_effect=fake_backup_control_files)
        service.service._ensure_started = AsyncMock(
            return_value=(asyncio.StreamReader(), MagicMock())
        )
        expected = IPhoneCapture(
            SourceResult(0, (), 0),
            SourceResult(0, (), 0),
            SourceResult(0, (), 0),
            "now",
            "since",
            (),
        )
        transports: list[str] = []
        with (
            tempfile.TemporaryDirectory(prefix="amplifai-fallback-test-") as temporary,
            patch.object(
                pymobiledevice3.usbmux,
                "list_devices",
                new=AsyncMock(return_value=devices),
            ),
            patch.object(
                pymobiledevice3.lockdown,
                "create_using_usbmux",
                new=AsyncMock(return_value=usb),
            ) as connect,
            patch.object(
                pymobiledevice3.services.mobilebackup2,
                "Mobilebackup2Service",
                return_value=service,
            ),
            patch.object(
                ManifestPlist,
                "from_path",
                return_value=SimpleNamespace(is_encrypted=False),
            ),
            patch(
                "amplifai_phone.ios_backup.parse_selected_backup", return_value=expected
            ),
        ):
            await collect_iphone(
                password_provider=lambda: "",
                connection_callback=transports.append,
                sessions_root=Path(temporary) / "sessions",
            )
        self.assertEqual(
            [call.kwargs["connection_type"] for call in connect.await_args_list],
            ["USB"],
        )
        self.assertEqual(transports, ["usb"])

    async def test_network_only_uses_existing_trusted_pairing(self) -> None:
        import pymobiledevice3.lockdown
        import pymobiledevice3.services.mobilebackup2
        import pymobiledevice3.usbmux
        from pyiosbackup.manifest_plist import ManifestPlist

        network = SimpleNamespace(
            is_usb=False,
            is_network=True,
            connection_type="Network",
            serial="SYNTHETIC-UDID",
        )
        lockdown = MagicMock(paired=True, udid="SYNTHETIC-UDID")
        lockdown.__aenter__ = AsyncMock(return_value=lockdown)
        lockdown.__aexit__ = AsyncMock(return_value=False)
        service = MagicMock()
        service.__aenter__ = AsyncMock(return_value=service)
        service.__aexit__ = AsyncMock(return_value=False)
        service.backup = AsyncMock(side_effect=fake_backup_control_files)
        service.service._ensure_started = AsyncMock(
            return_value=(asyncio.StreamReader(), MagicMock())
        )
        expected = IPhoneCapture(
            SourceResult(0, (), 0),
            SourceResult(0, (), 0),
            SourceResult(0, (), 0),
            "now",
            "since",
            (),
        )
        transports: list[str] = []
        with (
            tempfile.TemporaryDirectory(
                prefix="amplifai-network-only-test-"
            ) as temporary,
            patch.object(
                pymobiledevice3.usbmux,
                "list_devices",
                new=AsyncMock(return_value=[network]),
            ),
            patch.object(
                pymobiledevice3.lockdown,
                "create_using_usbmux",
                new=AsyncMock(return_value=lockdown),
            ) as connect,
            patch.object(
                pymobiledevice3.services.mobilebackup2,
                "Mobilebackup2Service",
                return_value=service,
            ),
            patch.object(
                ManifestPlist,
                "from_path",
                return_value=SimpleNamespace(is_encrypted=False),
            ),
            patch(
                "amplifai_phone.ios_backup.parse_selected_backup",
                return_value=expected,
            ),
        ):
            result = await collect_iphone(
                password_provider=lambda: self.fail("password not needed"),
                connection_callback=transports.append,
                sessions_root=Path(temporary) / "sessions",
            )
        self.assertIs(result, expected)
        self.assertEqual(transports, ["wifi"])
        connect.assert_awaited_once()
        self.assertEqual(connect.await_args.kwargs["connection_type"], "Network")
        self.assertFalse(connect.await_args.kwargs["autopair"])

    async def test_usb_failure_does_not_silently_collect_over_wifi(self) -> None:
        import pymobiledevice3.lockdown
        import pymobiledevice3.usbmux

        devices = [
            SimpleNamespace(
                is_usb=False,
                is_network=True,
                connection_type="Network",
                serial="SYNTHETIC-UDID",
            ),
            SimpleNamespace(
                is_usb=True,
                is_network=False,
                connection_type="USB",
                serial="SYNTHETIC-UDID",
            ),
        ]
        with (
            tempfile.TemporaryDirectory(prefix="amplifai-usb-error-test-") as temporary,
            patch.object(
                pymobiledevice3.usbmux,
                "list_devices",
                new=AsyncMock(return_value=devices),
            ),
            patch.object(
                pymobiledevice3.lockdown,
                "create_using_usbmux",
                new=AsyncMock(side_effect=OSError("synthetic USB failure")),
            ) as connect,
            self.assertRaisesRegex(OSError, "synthetic USB failure"),
        ):
            await collect_iphone(
                password_provider=lambda: "",
                sessions_root=Path(temporary) / "sessions",
            )
        connect.assert_awaited_once()
        self.assertEqual(connect.await_args.kwargs["connection_type"], "USB")

    async def test_distinct_phones_fail_before_pairing(self) -> None:
        import pymobiledevice3.lockdown
        import pymobiledevice3.usbmux

        devices = [
            SimpleNamespace(
                is_usb=False,
                is_network=True,
                connection_type="Network",
                serial="PHONE-ONE",
            ),
            SimpleNamespace(
                is_usb=True, is_network=False, connection_type="USB", serial="PHONE-TWO"
            ),
        ]
        with (
            patch.object(
                pymobiledevice3.usbmux,
                "list_devices",
                new=AsyncMock(return_value=devices),
            ),
            patch.object(
                pymobiledevice3.lockdown, "create_using_usbmux", new=AsyncMock()
            ) as connect,
            self.assertRaisesRegex(RuntimeError, "exactly one"),
        ):
            await collect_iphone(password_provider=lambda: "")
        connect.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
