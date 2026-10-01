"""Local iPhone capture through pymobiledevice3's MobileBackup2 service.

This module is a source candidate, not a distributed or device-validated app.
It never changes backup encryption settings or exports source databases.
"""

from __future__ import annotations

import logging
import os
import plistlib
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .backup_watchdog import (
    BackupWatchdog,
    observe_backup_receives,
    run_backup_session,
)
from .metadata import (
    RETAINED_HISTORY_START,
    SourceResult,
    UnsupportedSchema,
    read_calls,
    read_contacts,
    read_messages,
)
from .workspace import SessionWorkspace

MAX_SOURCE_BYTES = 128 * 1024 * 1024
HOME_DOMAIN = "HomeDomain"
DATABASES = {
    "contacts": "Library/AddressBook/AddressBook.sqlitedb",
    "calls": "Library/CallHistoryDB/CallHistory.storedata",
    "messages": "Library/SMS/sms.db",
}


@dataclass(frozen=True)
class IPhoneCapture:
    contacts: SourceResult
    calls: SourceResult
    messages: SourceResult
    collected_at: str
    since: str
    missing_sources: tuple[str, ...]


def _write_private(path: Path, payload: bytes) -> None:
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(handle, "wb") as stream:
        stream.write(payload)


def _extract_database(backup: object, relative_path: str, destination: Path) -> bool:
    """Materialize only the selected DB and its available SQLite sidecars."""
    from pyiosbackup.exceptions import MissingEntryError

    found_main = False
    for suffix in ("", "-wal", "-shm"):
        try:
            entry = backup.get_entry_by_domain_and_path(
                HOME_DOMAIN, relative_path + suffix
            )
        except (MissingEntryError, KeyError, FileNotFoundError):
            continue
        if entry.size > MAX_SOURCE_BYTES:
            raise UnsupportedSchema(
                f"Selected database exceeds size limit: {Path(relative_path).name}"
            )
        try:
            payload = entry.read_bytes()
        except FileNotFoundError:
            raise UnsupportedSchema(
                f"Selected backup payload missing: {Path(relative_path).name}{suffix}"
            ) from None
        if len(payload) > MAX_SOURCE_BYTES:
            raise UnsupportedSchema(
                f"Selected database exceeds size limit: {Path(relative_path).name}"
            )
        _write_private(Path(str(destination) + suffix), payload)
        found_main = found_main or not suffix
    return found_main


def parse_selected_backup(
    backup_path: Path,
    password: str,
    now: datetime | None = None,
    bound_callback: Callable[[], None] | None = None,
) -> IPhoneCapture:
    """Read only allowlisted metadata fields, then remove decrypted DB copies."""
    from pyiosbackup import Backup
    from pyiosbackup.exceptions import BackupPasswordIsRequired
    from pyiosbackup.keybag import Keybag
    from pyiosbackup.manifest_dbs.sqlite3 import ManifestDbSqlite3
    from pyiosbackup.manifest_plist import ManifestPlist

    # pyiosbackup's keybag module emits derived key bytes at DEBUG level.
    logging.getLogger("pyiosbackup").setLevel(logging.WARNING)
    now = now or datetime.now(timezone.utc)
    since = RETAINED_HISTORY_START
    with tempfile.TemporaryDirectory(
        prefix="amplifai-db-", dir=backup_path.parent
    ) as extracted_dir:
        extracted = Path(extracted_dir)
        manifest = ManifestPlist.from_path(backup_path / ManifestPlist.NAME)
        if manifest.is_encrypted and not password:
            raise BackupPasswordIsRequired()
        keybag = (
            Keybag.from_manifest(manifest, password) if manifest.is_encrypted else None
        )
        manifest_payload = (backup_path / "Manifest.db").read_bytes()
        if manifest.is_encrypted:
            manifest_payload = keybag.decrypt(manifest_payload, manifest.manifest_key)
        if len(manifest_payload) > MAX_SOURCE_BYTES:
            raise UnsupportedSchema("Backup manifest exceeds size limit")
        manifest_path = extracted / "manifest.sqlite3"
        _write_private(manifest_path, manifest_payload)
        manifest_db = ManifestDbSqlite3(manifest_path)
        try:
            backup = Backup(
                backup_path,
                manifest_db,
                manifest,
                plistlib.loads((backup_path / "Status.plist").read_bytes()),
                plistlib.loads((backup_path / "Info.plist").read_bytes()),
                keybag,
            )
            return _parse_entries(backup, extracted, now, since, bound_callback)
        finally:
            manifest_db._conn.close()


def _parse_entries(
    backup: object,
    extracted: Path,
    now: datetime,
    since: datetime,
    bound_callback: Callable[[], None] | None = None,
) -> IPhoneCapture:
    missing = []
    available = {}
    for key, path in DATABASES.items():
        destination = extracted / key
        available[key] = _extract_database(backup, path, destination)
        if bound_callback is not None:
            bound_callback()
        if not available[key]:
            missing.append(key)
    contacts = (
        read_contacts(extracted / "contacts")
        if available["contacts"]
        else SourceResult(0, (), 0)
    )
    calls = (
        read_calls(extracted / "calls", since)
        if available["calls"]
        else SourceResult(0, (), 0)
    )
    messages = (
        read_messages(extracted / "messages", since)
        if available["messages"]
        else SourceResult(0, (), 0)
    )
    return IPhoneCapture(
        contacts, calls, messages, now.isoformat(), since.isoformat(), tuple(missing)
    )


def _connection_plan(devices: list[object]) -> object:
    """Use a connected USB device; only use Wi-Fi when no cable is present."""
    candidates = [
        device for device in devices if device.connection_type in ("Network", "USB")
    ]
    identifiers = {device.serial.replace("-", "").lower() for device in candidates}
    if len(identifiers) != 1:
        raise RuntimeError(
            f"Connect exactly one trusted iPhone; found {len(identifiers)}"
        )
    return next((device for device in candidates if device.is_usb), candidates[0])


async def collect_iphone(
    *,
    password_provider: Callable[[], str],
    progress_callback: Callable[[float], None] | None = None,
    connection_callback: Callable[[str], None] | None = None,
    now: datetime | None = None,
    sessions_root: Path | None = None,
) -> IPhoneCapture:
    """Connect one iPhone via owner-trusted USB or an existing Wi-Fi pairing.

    `password_provider` is invoked only if the device reports an encrypted
    backup. It must collect the existing password in-process, never in argv,
    browser storage, logs, or a URL.
    """
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.mobilebackup2 import Mobilebackup2Service
    from pymobiledevice3.usbmux import list_devices

    device = _connection_plan(await list_devices())
    workspace = SessionWorkspace(sessions_root)
    with workspace as root:
        lockdown = await create_using_usbmux(
            serial=device.serial,
            connection_type=device.connection_type,
            label="AMPLIFai Local Collector",
            autopair=device.is_usb,
            pair_timeout=120 if device.is_usb else None,
            pairing_records_cache_folder=root / "pairing",
        )
        if device.is_network and not lockdown.paired:
            await lockdown.close()
            raise RuntimeError("An existing trusted Wi-Fi pairing is required")
        transport = "wifi" if device.is_network else "usb"
        if connection_callback is not None:
            connection_callback(transport)
        watchdog = BackupWatchdog()

        async def capture_session() -> IPhoneCapture:
            async with lockdown, Mobilebackup2Service(lockdown) as service:
                await observe_backup_receives(service, watchdog)
                rules = service.resolve_backup_selection(
                    ("contacts", "call_history", "sms")
                )

                def bounded_progress(value: float) -> None:
                    watchdog.raise_if_aborted()
                    workspace.check_bound()
                    if progress_callback is not None:
                        progress_callback(value)

                watchdog.start()
                await service.backup(
                    full=True,
                    backup_directory=root,
                    progress_callback=bounded_progress,
                    filter_callback=service.selection_filter_callback(rules),
                    patch_manifest=False,
                    unback=False,
                )
                watchdog.backup_completed()
                workspace.check_bound()
                from pyiosbackup.manifest_plist import ManifestPlist

                backup_path = root / lockdown.udid
                encrypted = ManifestPlist.from_path(
                    backup_path / ManifestPlist.NAME
                ).is_encrypted
                password = password_provider() if encrypted else ""
                if encrypted and not password:
                    raise RuntimeError(
                        "The existing encrypted-backup password is required"
                    )
                return parse_selected_backup(
                    backup_path, password, now, workspace.check_bound
                )

        return await run_backup_session(
            capture_session(), watchdog, workspace.preserve_for_recovery
        )
