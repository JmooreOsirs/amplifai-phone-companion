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
    IPhoneCapture,
    collect_iphone,
    parse_selected_backup,
)
from amplifai_phone.metadata import RETAINED_HISTORY_START, SourceResult


async def fake_backup_control_files(**kwargs: object) -> None:
    directory = Path(kwargs["backup_directory"]) / "SYNTHETIC-UDID"
    directory.mkdir()
    (directory / "Manifest.plist").write_bytes(plistlib.dumps({"IsEncrypted": False}))


class ManifestFixtureIsolationTest(unittest.TestCase):
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
        service.backup = AsyncMock(side_effect=fake_backup_control_files)
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
