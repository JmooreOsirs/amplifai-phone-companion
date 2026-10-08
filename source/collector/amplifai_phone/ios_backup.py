"""Local iPhone capture through pymobiledevice3's MobileBackup2 service.

This module is a source candidate, not a distributed or device-validated app.
It never changes backup encryption settings or exports source databases.
"""

from __future__ import annotations

import logging
import plistlib
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .backup_files import copy_backup_file
from .backup_watchdog import (
    BackupWatchdog,
    observe_backup_receives,
    run_backup_session,
)
from .metadata import (
    RETAINED_HISTORY_START,
    SourceCapacityLimit,
    SourceResult,
    UnsupportedSchema,
    read_calls,
    read_contacts,
    read_messages,
)
from .workspace import SessionWorkspace

MAX_PLIST_BYTES = 16 * 1024 * 1024
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


def _read_plist(path: Path) -> dict:
    if path.is_symlink() or path.stat().st_size > MAX_PLIST_BYTES:
        raise SourceCapacityLimit("Backup control metadata exceeds safe memory bounds")
    value = plistlib.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise UnsupportedSchema("Invalid backup control metadata")
    return value


def _manifest_plist_path(backup_path: Path) -> Path:
    # The pinned backup service may leave the root placeholder empty while the
    # device supplies the completed manifest under Snapshot/.
    root = backup_path / "Manifest.plist"
    if root.is_symlink():
        raise UnsupportedSchema("Invalid backup manifest path")
    if root.is_file() and root.stat().st_size > 0:
        return root
    snapshot = backup_path / "Snapshot" / "Manifest.plist"
    if snapshot.parent.is_symlink() or snapshot.is_symlink():
        raise UnsupportedSchema("Invalid backup manifest path")
    if snapshot.is_file() and snapshot.stat().st_size > 0:
        return snapshot
    raise UnsupportedSchema("Backup manifest was not received")


def _extract_database(
    backup: object,
    relative_path: str,
    destination: Path,
    bound_callback: Callable[[], None] | None = None,
    workspace: SessionWorkspace | None = None,
    progress_callback: Callable[[int], None] | None = None,
) -> bool:
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
        if not isinstance(entry.size, int) or entry.size < 0:
            raise UnsupportedSchema("Invalid selected payload size")
        if not re.fullmatch(r"[0-9a-f]{40}", entry.file_id) or not entry.is_file():
            raise UnsupportedSchema("Invalid selected payload identity")
        source = entry.real_path
        if workspace is not None:
            workspace.checked_path(source)
        if not source.is_file():
            raise UnsupportedSchema(
                f"Selected backup payload missing: {Path(relative_path).name}{suffix}"
            ) from None
        copied = copy_backup_file(
            source,
            Path(str(destination) + suffix),
            keybag=backup.keybag if backup.is_encrypted else None,
            encryption_key=entry.encryption_key,
            padded=backup.is_encrypted,
            workspace=workspace,
            bound_callback=bound_callback,
            progress_callback=progress_callback,
        )
        if copied != entry.size:
            raise UnsupportedSchema(
                "Selected backup payload length does not match its manifest"
            )
        found_main = found_main or not suffix
    return found_main


def parse_selected_backup(
    backup_path: Path,
    password: str,
    now: datetime | None = None,
    bound_callback: Callable[[], None] | None = None,
    *,
    workspace: SessionWorkspace | None = None,
    transfer_callback: Callable[[dict[str, object]], None] | None = None,
) -> IPhoneCapture:
    """Read only allowlisted metadata fields, then remove decrypted DB copies."""
    from pyiosbackup import Backup
    from pyiosbackup.exceptions import BackupPasswordIsRequired
    from pyiosbackup.keybag import Keybag
    from pyiosbackup.manifest_dbs.sqlite3 import ManifestDbSqlite3
    from pyiosbackup.manifest_plist import ManifestPlist

    # pyiosbackup's keybag module emits derived key bytes at DEBUG level.
    logging.getLogger("pyiosbackup").setLevel(logging.WARNING)
    now = now or datetime.now(UTC)
    since = RETAINED_HISTORY_START
    processed = 0
    last_reported = 0.0

    def processed_bytes(count: int) -> None:
        nonlocal processed, last_reported
        processed += count
        moment = time.monotonic()
        if transfer_callback is not None and moment - last_reported >= 0.5:
            transfer_callback({"stage": "processing", "processedBytes": processed})
            last_reported = moment

    if transfer_callback is not None:
        transfer_callback({"stage": "processing", "processedBytes": 0})
    with tempfile.TemporaryDirectory(
        prefix="amplifai-db-", dir=backup_path.parent
    ) as extracted_dir:
        extracted = Path(extracted_dir)
        manifest_path_on_disk = _manifest_plist_path(backup_path)
        _read_plist(manifest_path_on_disk)
        manifest = ManifestPlist.from_path(manifest_path_on_disk)
        if manifest.is_encrypted and not password:
            raise BackupPasswordIsRequired()
        keybag = (
            Keybag.from_manifest(manifest, password) if manifest.is_encrypted else None
        )
        manifest_path = extracted / "manifest.sqlite3"
        copy_backup_file(
            backup_path / "Manifest.db",
            manifest_path,
            keybag=keybag,
            encryption_key=manifest.manifest_key if manifest.is_encrypted else b"",
            workspace=workspace,
            bound_callback=bound_callback,
            progress_callback=processed_bytes,
        )
        manifest_db = ManifestDbSqlite3(manifest_path)
        try:
            backup = Backup(
                backup_path,
                manifest_db,
                manifest,
                _read_plist(backup_path / "Status.plist"),
                _read_plist(backup_path / "Info.plist"),
                keybag,
            )
            capture = _parse_entries(
                backup,
                extracted,
                now,
                since,
                bound_callback,
                workspace,
                processed_bytes,
            )
            if transfer_callback is not None:
                transfer_callback({"stage": "processing", "processedBytes": processed})
            return capture
        finally:
            manifest_db._conn.close()


def _parse_entries(
    backup: object,
    extracted: Path,
    now: datetime,
    since: datetime,
    bound_callback: Callable[[], None] | None = None,
    workspace: SessionWorkspace | None = None,
    progress_callback: Callable[[int], None] | None = None,
) -> IPhoneCapture:
    missing = []
    available = {}
    for key, path in DATABASES.items():
        destination = extracted / key
        available[key] = _extract_database(
            backup, path, destination, bound_callback, workspace, progress_callback
        )
        if bound_callback is not None:
            bound_callback()
        if not available[key]:
            missing.append(key)
    contacts = (
        read_contacts(extracted / "contacts", bound_callback)
        if available["contacts"]
        else SourceResult(0, (), 0)
    )
    calls = (
        read_calls(extracted / "calls", since, bound_callback)
        if available["calls"]
        else SourceResult(0, (), 0)
    )
    messages = (
        read_messages(extracted / "messages", since, bound_callback)
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
    transfer_callback: Callable[[dict[str, object]], None] | None = None,
    now: datetime | None = None,
    sessions_root: Path | None = None,
) -> IPhoneCapture:
    """Connect one iPhone via owner-trusted USB or an existing Wi-Fi pairing.

    `password_provider` is invoked only if the device reports an encrypted
    backup. It must collect the existing password in-process, never in argv,
    browser storage, logs, or a URL.
    """
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.mobilebackup2 import (
        BackupSelectionRule,
        Mobilebackup2Service,
    )
    from pymobiledevice3.usbmux import list_devices

    from .backup_stream import bind_streamed_device_link

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
                bind_streamed_device_link(
                    service, workspace, watchdog, transfer_callback
                )
                rules = (
                    *service.resolve_backup_selection(
                        ("contacts", "call_history", "sms")
                    ),
                    BackupSelectionRule(HOME_DOMAIN, DATABASES["messages"] + "-wal"),
                    BackupSelectionRule(HOME_DOMAIN, DATABASES["messages"] + "-shm"),
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
                manifest_path_on_disk = _manifest_plist_path(backup_path)
                _read_plist(manifest_path_on_disk)
                encrypted = ManifestPlist.from_path(
                    manifest_path_on_disk
                ).is_encrypted
                password = password_provider() if encrypted else ""
                if encrypted and not password:
                    raise RuntimeError(
                        "The existing encrypted-backup password is required"
                    )

                def parsing_bound() -> None:
                    watchdog.raise_if_aborted()
                    workspace.check_bound()

                return parse_selected_backup(
                    backup_path,
                    password,
                    now,
                    parsing_bound,
                    workspace=workspace,
                    transfer_callback=transfer_callback,
                )

        return await run_backup_session(
            capture_session(), watchdog, workspace.preserve_for_recovery
        )
