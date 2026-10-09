"""Local iPhone capture through pymobiledevice3's MobileBackup2 service.

This module is a source candidate, not a distributed or device-validated app.
It never changes backup encryption settings or exports source databases.
"""

from __future__ import annotations

import logging
import plistlib
import re
import sqlite3
import tempfile
import time
import xml.parsers.expat
from collections.abc import Callable
from dataclasses import dataclass, field
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
    BackupControlInvalid,
    BackupControlMetadataLimit,
    ContactsSchemaUnsupported,
    SelectedPayloadIntegrityError,
    SelectedPayloadMissing,
    SourceReadCapacityLimit,
    SourceResult,
    UnsupportedSchema,
    read_calls,
    read_contacts,
    read_messages,
)
from .record_store import RecordStore
from .workspace import SessionWorkspace, WorkspaceError

# Info.plist can grow with the installed-application inventory. It is already a
# selected, private on-disk control file; parse it from disk under a separate
# finite object bound rather than allocating a second raw 16 MiB-limited copy.
MAX_PLIST_BYTES = 128 * 1024 * 1024
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
    review_workspace: SessionWorkspace | None = None
    record_store: RecordStore | None = None
    backup_encrypted: bool | None = field(default=None, compare=False)
    unavailable_reasons: tuple[tuple[str, str], ...] = field(default=(), compare=False)

    def close(self) -> None:
        if self.record_store is not None:
            self.record_store.close()
        if self.review_workspace is not None:
            self.review_workspace.__exit__(None, None, None)


def _read_plist(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise BackupControlInvalid("Required backup control metadata is unavailable")
    if path.stat().st_size > MAX_PLIST_BYTES:
        raise BackupControlMetadataLimit("Backup control metadata exceeds safe memory bounds")
    try:
        with path.open("rb") as stream:
            value = plistlib.load(stream)
    except (plistlib.InvalidFileException, ValueError, xml.parsers.expat.ExpatError) as error:
        raise BackupControlInvalid("Invalid backup control metadata") from error
    if not isinstance(value, dict):
        raise BackupControlInvalid("Invalid backup control metadata")
    return value


def _manifest_plist_path(backup_path: Path) -> Path:
    # The pinned backup service may leave the root placeholder empty while the
    # device supplies the completed manifest under Snapshot/.
    root = backup_path / "Manifest.plist"
    if root.is_symlink():
        raise BackupControlInvalid("Invalid backup manifest path")
    if root.is_file() and root.stat().st_size > 0:
        return root
    snapshot = backup_path / "Snapshot" / "Manifest.plist"
    if snapshot.parent.is_symlink() or snapshot.is_symlink():
        raise BackupControlInvalid("Invalid backup manifest path")
    if snapshot.is_file() and snapshot.stat().st_size > 0:
        return snapshot
    raise BackupControlInvalid("Backup manifest was not received")


def _extract_database(
    backup: object,
    relative_path: str,
    destination: Path,
    bound_callback: Callable[[], None] | None = None,
    workspace: SessionWorkspace | None = None,
    progress_callback: Callable[[int], None] | None = None,
    *,
    payload_diagnostic_callback: Callable[[dict[str, object]], None] | None = None,
) -> bool:
    """Materialize only the selected DB and its available SQLite sidecars."""
    from pyiosbackup.exceptions import MissingEntryError

    found_main = False
    source_kind = next(
        (key for key, path in DATABASES.items() if path == relative_path), None
    )
    for suffix in ("", "-wal", "-shm"):
        component = suffix.removeprefix("-") or "database"
        try:
            entry = backup.get_entry_by_domain_and_path(
                HOME_DOMAIN, relative_path + suffix
            )
        except (MissingEntryError, KeyError, FileNotFoundError):
            continue
        except sqlite3.DatabaseError as error:
            raise BackupControlInvalid("Backup manifest database is invalid") from error
        if (
            not isinstance(entry.size, int)
            or isinstance(entry.size, bool)
            or entry.size < 0
        ):
            raise SelectedPayloadIntegrityError(
                "Invalid selected payload size", code="selected_payload_size"
            )
        if (
            not isinstance(entry.file_id, str)
            or not re.fullmatch(r"[0-9a-f]{40}", entry.file_id)
            or not entry.is_file()
        ):
            raise SelectedPayloadIntegrityError(
                "Invalid selected payload identity", code="selected_payload_identity"
            )
        source = entry.real_path
        if workspace is not None:
            workspace.checked_path(source)
        if not source.is_file():
            raise SelectedPayloadMissing(
                f"Selected backup payload missing: {Path(relative_path).name}{suffix}"
            ) from None
        def raw_size() -> int | None:
            try:
                from stat import S_ISREG
                info = source.stat(follow_symlinks=False)
                return info.st_size if S_ISREG(info.st_mode) else None
            except OSError:
                return None

        diagnostic = None
        raw_before_size = raw_size()
        if payload_diagnostic_callback is not None:
            diagnostic = {
                "source": source_kind, "component": component,
                "manifest_bytes": entry.size,
                "raw_before_bytes": raw_before_size,
                "encrypted": backup.is_encrypted is True,
                "entry_key_present": isinstance(entry.encryption_key, bytes) and bool(entry.encryption_key),
                "result": "copying",
            }
            import hashlib
            diagnostic['standard_file_id'] = entry.file_id == hashlib.sha1((HOME_DOMAIN+'-'+relative_path+suffix).encode()).hexdigest()
            payload_diagnostic_callback(diagnostic)
        try:
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
        except SelectedPayloadIntegrityError:
            if diagnostic is not None:
                payload_diagnostic_callback({**diagnostic, "result": "copy_failed"})
            raise
        validation = None
        if copied != entry.size:
            if (source_kind == 'contacts' and not suffix and backup.is_encrypted is False
                    and raw_before_size == copied == raw_size()):
                from .sqlite_size_compat import validate_contacts_size_difference

                validation = validate_contacts_size_difference(
                    backup, entry, destination, copied, workspace=workspace,
                    bound_callback=bound_callback,
                )
            elif (source_kind == 'messages' and not suffix and backup.is_encrypted is False
                    and raw_before_size == copied == raw_size()):
                from .sqlite_messages_size_compat import validate_messages_size_difference

                validation = validate_messages_size_difference(
                    backup, entry, destination, copied, workspace=workspace,
                    bound_callback=bound_callback,
                )
        if diagnostic is not None:
            sample = {
                **diagnostic, 'raw_after_bytes': raw_size(),
                'copied_plaintext_bytes': copied,
                'result': 'match' if copied == entry.size else 'length_mismatch',
            }
            if validation is not None:
                sample['size_difference_validation'] = validation.code
                sample['size_difference_validation_scope'] = validation.scope
            payload_diagnostic_callback(sample)
        if copied != entry.size:
            if validation is None or not validation.accepted:
                raise SelectedPayloadIntegrityError(
                    "Selected backup payload length does not match its manifest", code="selected_payload_length"
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
    record_store: RecordStore | None = None,
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
                record_store,
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
    record_store: RecordStore | None = None,
) -> IPhoneCapture:
    missing = []
    reasons: dict[str, str] = {}
    available = {}
    for key, path in DATABASES.items():
        destination = extracted / key
        try:
            available[key] = _extract_database(
                backup, path, destination, bound_callback, workspace, progress_callback
            )
        except (SelectedPayloadMissing, SelectedPayloadIntegrityError) as error:
            if key == "contacts":
                raise
            available[key] = False
            reasons[key] = "absent" if isinstance(error, SelectedPayloadMissing) else "source_integrity"
        if bound_callback is not None:
            bound_callback()
        if not available[key]:
            missing.append(key)
            if key != "contacts":
                reasons.setdefault(key, "absent")
    if available["contacts"]:
        try:
            contacts = read_contacts(extracted / "contacts", bound_callback, record_store)
        except UnsupportedSchema as error:
            raise ContactsSchemaUnsupported(
                "Contact database schema is unsupported"
            ) from error
        except sqlite3.DatabaseError as error:
            raise SelectedPayloadIntegrityError(
                "Contact database is invalid", code="selected_contacts_integrity"
            ) from error
    else:
        contacts = SourceResult(0, (), 0)

    def optional_source(key: str, reader: Callable[[], SourceResult]) -> SourceResult:
        if not available[key]:
            return SourceResult(0, (), 0)
        try:
            return reader()
        except (UnsupportedSchema, SourceReadCapacityLimit, sqlite3.DatabaseError) as error:
            if record_store is not None:
                record_store.discard_category(key)
            missing.append(key)
            reasons[key] = ("source_capacity" if isinstance(error, SourceReadCapacityLimit) else
                            "unsupported_schema" if isinstance(error, UnsupportedSchema) else "source_invalid")
            return SourceResult(0, (), 0)

    calls = optional_source(
        "calls", lambda: read_calls(extracted / "calls", since, bound_callback, record_store)
    )
    messages = optional_source(
        "messages", lambda: read_messages(extracted / "messages", since, bound_callback, record_store)
    )
    encrypted_state = getattr(backup, "is_encrypted", None)
    return IPhoneCapture(
        contacts, calls, messages, now.isoformat(), since.isoformat(), tuple(missing),
        backup_encrypted=encrypted_state if isinstance(encrypted_state, bool) else None,
        unavailable_reasons=tuple((key, reasons[key]) for key in ("calls", "messages") if key in reasons),
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
    review_capture: IPhoneCapture | None = None
    try:
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
                nonlocal review_capture
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

                    review_workspace = SessionWorkspace(sessions_root)
                    review_root = review_workspace.__enter__()
                    record_store: RecordStore | None = None
                    try:
                        record_store = RecordStore(review_root / "sanitized.sqlite3", review_workspace)
                        parsed = parse_selected_backup(
                            backup_path,
                            password,
                            now,
                            parsing_bound,
                            workspace=workspace,
                            transfer_callback=transfer_callback,
                            record_store=record_store,
                        )
                        review_capture = IPhoneCapture(
                            parsed.contacts, parsed.calls, parsed.messages,
                            parsed.collected_at, parsed.since, parsed.missing_sources,
                            review_workspace, record_store,
                            parsed.backup_encrypted, parsed.unavailable_reasons,
                        )
                        return review_capture
                    except BaseException:
                        if record_store is not None:
                            record_store.close()
                        review_workspace.__exit__(None, None, None)
                        raise

            return await run_backup_session(
                capture_session(), watchdog, workspace.preserve_for_recovery
            )
    except BaseException as primary_error:
        # The raw workspace may fail while leaving its context after a parsed
        # capture was prepared. That capture has not reached the caller yet.
        if review_capture is not None:
            try:
                review_capture.close()
            except BaseException as cleanup_error:
                raise WorkspaceError(
                    "Unable to remove the prepared private review workspace",
                    code="workspace_cleanup", primary_error=primary_error,
                ) from cleanup_error
        raise
