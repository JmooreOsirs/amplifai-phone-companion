"""Line-delimited local process protocol for the macOS collector window.

The production entrypoint has no synthetic mode or arbitrary-backup-path input.
Tests inject a synthetic collector directly into run_connect.
"""

from __future__ import annotations

import argparse
import json
import math
import signal
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TextIO
from uuid import UUID

from pymobiledevice3.exceptions import (
    ConnectionTerminatedError,
    NotEnoughDiskSpaceError,
)

from .__main__ import review_capture, review_snapshot
from .backup_stream import DeviceBackupRejected
from .backup_watchdog import (
    BackupCleanupIncomplete,
    BackupNoFileProgress,
    BackupStalled,
    BackupTimeLimit,
)
from .bridge import BridgeError, BridgeServer
from .capture_runtime import run_local_capture
from .ios_backup import IPhoneCapture, collect_iphone
from .metadata import (
    MAX_SELECTED_CONTACTS,
    BackupControlInvalid,
    Contact,
    ContactsSchemaUnsupported,
    Interaction,
    SelectedPayloadIntegrityError,
    SelectedPayloadMissing,
    SourceCapacityLimit,
    UnsupportedSchema,
)
from .record_store import RecordCollection, SelectionSnapshot
from .workspace import WorkspaceError, abandoned_sessions, clear_abandoned

MAX_COMMAND_BYTES = 600_000
CONTACT_PAGE_SIZE = 200
MAX_CONTACT_QUERY_LENGTH = 120
MAX_PASSWORD_LENGTH = 512


class CaptureCancelled(KeyboardInterrupt):
    pass


def _emit(stream: TextIO, event: dict[str, object]) -> None:
    stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    stream.flush()


def _command(stream: TextIO) -> dict[str, object]:
    line = stream.readline(MAX_COMMAND_BYTES + 1)
    if not line:
        raise CaptureCancelled()
    if len(line.encode("utf-8")) > MAX_COMMAND_BYTES or not line.endswith("\n"):
        raise ValueError("Command too large")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise TypeError("Invalid command")
    return value


def _contact_page(
    contacts: tuple[Contact | Interaction, ...] | RecordCollection, query: object, cursor: object
) -> dict[str, object]:
    if (
        not isinstance(query, str)
        or not isinstance(cursor, int)
        or isinstance(cursor, bool)
    ):
        raise TypeError("Invalid contact page request")
    if len(query) > MAX_CONTACT_QUERY_LENGTH or not 0 <= cursor <= len(contacts):
        raise ValueError("Invalid contact page request")
    needle = query.strip().casefold()
    page = []
    next_cursor = None
    if isinstance(contacts, RecordCollection):
        candidates = contacts.page(query, cursor, CONTACT_PAGE_SIZE)
    else:
        candidates = ((position, contacts[position]) for position in range(cursor, len(contacts)))
    for position, item in candidates:
        if not isinstance(item, Contact):
            raise TypeError("Invalid contact preview")
        if needle not in item.name.casefold():
            continue
        if len(page) == CONTACT_PAGE_SIZE:
            next_cursor = position
            break
        page.append(
            {
                "id": item.source_id,
                "name": item.name,
                "phoneCount": len(item.phones),
                "phoneEnds": [phone[-4:] for phone in item.phones],
            }
        )
    return {
        "contacts": page,
        "query": query,
        "cursor": cursor,
        "nextCursor": next_cursor,
        "totalContacts": len(contacts),
    }


def _error_code(exc: BaseException) -> str:
    from cryptography.hazmat.primitives.keywrap import InvalidUnwrap
    from pyiosbackup.exceptions import BackupPasswordIsRequired
    from pymobiledevice3.exceptions import PairingError

    if isinstance(exc, CaptureCancelled):
        return "cancelled"
    if isinstance(exc, WorkspaceError):
        return exc.code
    if isinstance(exc, (BackupStalled, BackupTimeLimit, BackupNoFileProgress)):
        return exc.code
    if isinstance(exc, DeviceBackupRejected):
        return "device_backup_failed"
    if isinstance(exc, NotEnoughDiskSpaceError):
        return "backup_host_space"
    if isinstance(exc, (ConnectionTerminatedError, ConnectionError)):
        return "connection_lost"
    if isinstance(exc, BackupCleanupIncomplete):
        return (
            exc.reason
            if exc.reason
            in {
                "backup_stalled",
                "backup_no_file_progress",
                "backup_time_limit",
                "cancelled",
                "connection_timeout",
                "connection_lost",
                "device_backup_failed",
                "backup_host_space",
            }
            else "collection_failed"
        )
    if isinstance(exc, BackupControlInvalid):
        return "backup_control_invalid"
    if isinstance(exc, SelectedPayloadMissing):
        return "selected_payload_missing"
    if isinstance(exc, SelectedPayloadIntegrityError):
        return "selected_payload_invalid"
    if isinstance(exc, ContactsSchemaUnsupported):
        return "contacts_schema"
    if isinstance(exc, UnsupportedSchema):
        return "unsupported_schema"
    if isinstance(exc, SourceCapacityLimit):
        return exc.code
    if isinstance(exc, (BackupPasswordIsRequired, InvalidUnwrap)):
        return "backup_password"
    if isinstance(exc, PairingError):
        return "trust_required"
    if isinstance(exc, TimeoutError):
        return "connection_timeout"
    if isinstance(exc, RuntimeError) and (
        "exactly one trusted iPhone" in str(exc)
        or "trusted iPhone connection" in str(exc)
        or "trusted Wi-Fi pairing" in str(exc)
    ):
        return "phone_connection"
    return "collection_failed"


def _error_event(exc: BaseException) -> dict[str, object]:
    event: dict[str, object] = {"kind": "error", "code": _error_code(exc)}
    if isinstance(exc, BackupCleanupIncomplete):
        event["cleanupRequired"] = True
    elif isinstance(exc, WorkspaceError) and exc.code == "workspace_cleanup":
        if exc.primary_error is not None:
            event["code"] = _error_code(exc.primary_error)
        event["cleanupRequired"] = True
    return event


def run_connect(
    source: TextIO,
    sink: TextIO,
    collector: Callable[..., Awaitable[IPhoneCapture]] = collect_iphone,
) -> int:
    _emit(sink, {"kind": "state", "state": "connecting"})
    capture_stage = "connecting"

    def password_provider() -> str:
        nonlocal capture_stage
        capture_stage = "processing"
        _emit(sink, {"kind": "state", "state": "password_required"})
        command = _command(source)
        if command.get("action") != "password":
            raise ValueError("Expected password response")
        password = command.get("value")
        if (
            not isinstance(password, str)
            or not 0 < len(password) <= MAX_PASSWORD_LENGTH
        ):
            raise ValueError("Invalid password response")
        _emit(sink, {"kind": "state", "state": "processing"})
        return password

    def progress(value: float) -> None:
        if math.isfinite(value):
            _emit(
                sink, {"kind": "progress", "value": max(0.0, min(100.0, float(value)))}
            )

    def transfer(value: dict[str, object]) -> None:
        nonlocal capture_stage
        if value.get("stage") in ("backup", "processing"):
            capture_stage = str(value["stage"])
        # The collector publishes only numeric counters and one allowlisted stage.
        _emit(sink, {"kind": "transfer", **value})

    def connection(transport: str) -> None:
        _emit(sink, {"kind": "connection", "transport": transport})

    bridge: BridgeServer | None = None
    capture: IPhoneCapture | None = None
    selected_ids: set[int] | SelectionSnapshot | None = None
    try:
        capture = run_local_capture(
            collector(
                password_provider=password_provider,
                progress_callback=progress,
                connection_callback=connection,
                transfer_callback=transfer,
            )
        )
        capture_stage = "review"
        _emit(
            sink,
            {
                "kind": "capture",
                **_contact_page(capture.contacts.records, "", 0),
                "availableCalls": len(capture.calls.records),
                "availableMessages": len(capture.messages.records),
                "since": capture.since,
                "missing": capture.missing_sources,
            },
        )
        while True:
            command = _command(source)
            action = command.get("action")
            if action == "disconnect":
                _emit(sink, {"kind": "state", "state": "disconnected"})
                return 0
            if action == "revoke":
                if bridge is not None:
                    bridge.close()
                    bridge = None
                _emit(sink, {"kind": "state", "state": "pairing_revoked"})
                continue
            if action == "contacts":
                page = _contact_page(
                    capture.contacts.records,
                    command.get("query"),
                    command.get("cursor"),
                )
                _emit(sink, {"kind": "contacts", **page})
                continue
            if action in {"handoff-status", "finish"}:
                if (
                    set(command) != {"action", "handoffId"}
                    or bridge is None
                    or command.get("handoffId") != bridge.handoff_id
                ):
                    _emit(sink, {"kind": "error", "code": "handoff_unconfirmed"})
                    continue
                state = bridge.handoff_state()
                if action == "finish":
                    if state != "saved":
                        _emit(sink, {"kind": "error", "code": "handoff_unconfirmed"})
                        continue
                    _emit(sink, {"kind": "state", "state": "completed"})
                    return 0
                _emit(
                    sink,
                    {"kind": "handoff", "state": state, "handoffId": bridge.handoff_id},
                )
                continue
            if action == "pair":
                if selected_ids is None:
                    _emit(sink, {"kind": "error", "code": "selection"})
                    continue
                if bridge is not None:
                    bridge.close()
                    bridge = None
                try:
                    bridge = BridgeServer(capture, selected_ids)
                    bridge.start()
                except (BridgeError, OSError, ValueError):
                    if bridge is not None:
                        bridge.close()
                        bridge = None
                    _emit(sink, {"kind": "error", "code": "bridge_unavailable"})
                    continue
                _emit(
                    sink,
                    {
                        "kind": "pairing",
                        "pairCode": bridge.code,
                        "port": bridge.port,
                        "expiresInSeconds": 300,
                        "handoffId": bridge.handoff_id,
                        "payloadSha256": bridge.payload_sha256,
                    },
                )
                continue
            if action == "review-page":
                ids = command.get("ids")
                review_id = command.get("reviewId")
                cursor = command.get("cursor")
                total = command.get("total")
                try:
                    if (set(command) != {"action", "ids", "reviewId", "cursor", "total"}
                            or capture.record_store is None
                            or not isinstance(ids, list) or not 0 < len(ids) <= 1000
                            or not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0
                            or not isinstance(total, int) or isinstance(total, bool) or total < 1
                            or not isinstance(review_id, str) or str(UUID(review_id)) != review_id):
                        raise ValueError("Invalid selection page")
                    if cursor == 0:
                        if bridge is not None:
                            bridge.close()
                            bridge = None
                        selected_ids = None
                    snapshot = capture.record_store.append_selection(review_id, cursor, ids, total)
                except ValueError:
                    _emit(sink, {"kind": "error", "code": "selection"})
                    continue
                _emit(sink, {"kind": "review-page", "reviewId": review_id,
                             "nextCursor": cursor + len(ids)})
                if snapshot is not None:
                    selected_ids = snapshot
                    _emit(sink, {"kind": "review", "reviewId": review_id,
                                 **review_snapshot(capture, snapshot)})
                continue
            if action != "review":
                raise ValueError("Unexpected action")
            ids = command.get("ids")
            review_id = command.get("reviewId")
            if (
                not set(command) <= {"action", "ids", "reviewId"}
                or not isinstance(ids, list)
                or not 0 < len(ids) <= MAX_SELECTED_CONTACTS
                or any(
                    not isinstance(item, int) or isinstance(item, bool) for item in ids
                )
            ):
                _emit(sink, {"kind": "error", "code": "selection"})
                continue
            if review_id is not None:
                try:
                    if (
                        not isinstance(review_id, str)
                        or str(UUID(review_id)) != review_id
                    ):
                        raise ValueError("Invalid review binding")
                except ValueError:
                    _emit(sink, {"kind": "error", "code": "selection"})
                    continue
            if bridge is not None:
                bridge.close()
                bridge = None
            selected_ids = None
            try:
                review = review_capture(capture, ",".join(str(item) for item in ids))
            except ValueError:
                _emit(sink, {"kind": "error", "code": "selection"})
                continue
            selected_ids = set(ids)
            _emit(
                sink,
                {
                    "kind": "review",
                    **review,
                    **({"reviewId": review_id} if review_id else {}),
                },
            )
    except CaptureCancelled:
        _emit(sink, {"kind": "state", "state": "cancelled"})
        return 130
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - do not expose device errors
        event = _error_event(exc)
        if event["code"] in {
            "connection_lost", "device_backup_failed", "backup_host_space",
            "collection_failed", "backup_control_invalid", "selected_payload_missing",
            "selected_payload_invalid", "contacts_schema", "unsupported_schema",
            "source_capacity_limit", "source_read_capacity_limit", "contacts_capacity_limit",
            "backup_control_frame_limit", "backup_control_metadata_limit",
            "backup_control_path_limit",
        }:
            event["stage"] = capture_stage
        device_failure = (
            exc if isinstance(exc, DeviceBackupRejected)
            else exc.__cause__ if isinstance(exc.__cause__, DeviceBackupRejected)
            else None
        )
        if device_failure is not None and device_failure.device_code is not None:
            event["deviceCode"] = device_failure.device_code
        _emit(sink, event)
        return 1
    finally:
        try:
            if bridge is not None:
                bridge.close()
        finally:
            if capture is not None:
                capture.close()


def run_admin(mode: str, sink: TextIO, root: Path | None = None) -> int:
    try:
        if mode == "inspect":
            items = abandoned_sessions(root)
            _emit(
                sink,
                {
                    "kind": "residue",
                    "sessions": [
                        {"name": item.name, "bytes": item.bytes_on_disk}
                        for item in items
                    ],
                },
            )
        elif mode == "clear-residue":
            _emit(sink, {"kind": "cleared", "count": clear_abandoned(root)})
        elif mode == "runtime-check":
            from pyiosbackup.keybag import Keybag
            from pyiosbackup.manifest_dbs.sqlite3 import ManifestDbSqlite3
            from pymobiledevice3.lockdown import create_using_usbmux
            from pymobiledevice3.services.mobilebackup2 import Mobilebackup2Service
            from pymobiledevice3.usbmux import list_devices

            _ = (
                Keybag,
                ManifestDbSqlite3,
                create_using_usbmux,
                Mobilebackup2Service,
                list_devices,
            )
            _emit(sink, {"kind": "runtime", "ready": True})
        else:
            raise ValueError("Unsupported mode")
        return 0
    except ImportError:
        _emit(sink, {"kind": "error", "code": "runtime_missing"})
        return 1
    except WorkspaceError as exc:
        _emit(sink, _error_event(exc))
        return 1
    except OSError:
        _emit(sink, {"kind": "error", "code": "workspace_unavailable"})
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode", choices=("connect", "inspect", "clear-residue", "runtime-check")
    )
    args = parser.parse_args()
    if args.mode != "connect":
        return run_admin(args.mode, sys.stdout)

    def terminate(_signal_number: int, _frame: object) -> None:
        raise CaptureCancelled()

    signal.signal(signal.SIGTERM, terminate)
    return run_connect(sys.stdin, sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
