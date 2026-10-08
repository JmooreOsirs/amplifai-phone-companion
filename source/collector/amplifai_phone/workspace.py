"""Private, bounded backup workspaces with explicit crash-residue recovery."""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .windows_storage import (
    create_private_windows_directory,
    private_windows_directory,
    windows_process_alive,
)

ROOT_MARKER = ".amplifai-phone-sessions-v1"
SESSION_MARKER = ".amplifai-owned-session.json"
SESSION_NAME = re.compile(r"^session-[0-9a-f]{32}$")
MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024
# DeviceLink asks for the capacity of the backup receiver, not the size of the
# selected files we retain. The filtered receiver drains unrelated files; it
# cannot be represented by the physical selected-file budget. Keep this below
# signed 64-bit plist limits and enforce physical space separately per write.
STREAM_RECEIVER_CAPACITY_BYTES = 8 * 1024**4


class WorkspaceError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "workspace",
        primary_error: BaseException | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.primary_error = primary_error


@dataclass(frozen=True)
class Residue:
    name: str
    created_at: int
    bytes_on_disk: int


def default_sessions_root() -> Path:
    if os.name == "nt":
        parent = os.environ.get("LOCALAPPDATA")
        if not parent:
            raise WorkspaceError(
                "LOCALAPPDATA is unavailable", code="workspace_unavailable"
            )
        return Path(parent) / "AMPLIFai Phone Candidate" / "sessions"
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "AMPLIFai Phone Candidate"
        / "sessions"
    )


def _write_private(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)


def _owned_root(root: Path, *, create: bool) -> bool:
    if root.is_symlink() or (os.name == "nt" and root.is_junction()):
        raise WorkspaceError(
            "Sessions root cannot be a symlink", code="workspace_unsafe"
        )
    if not root.exists():
        if not create:
            return False
        parent = root.parent
        if parent.is_symlink() or (os.name == "nt" and parent.is_junction()):
            raise WorkspaceError(
                "App support directory cannot be a symlink", code="workspace_unsafe"
            )
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "nt":
            # Create with a protected owner/DACL, not mkdir followed by repair.
            # The native context pins parent/root identity through marker bytes.
            # Races and unverified paths never enter recursive rollback.
            try:
                with create_private_windows_directory(root):
                    try:
                        _write_private(
                            root / ROOT_MARKER, b"AMPLIFAI_PHONE_SESSIONS_V1\n"
                        )
                    except OSError:
                        raise WorkspaceError(
                            "The private storage root is empty or marker-only. "
                            "No phone data was written; the folder was not removed.",
                            code="workspace_marker",
                        ) from None
            except PermissionError as exc:
                raise WorkspaceError(
                    "New sessions root could not be verified; no phone data was written",
                    code="workspace_unsafe",
                ) from exc
            return True
        root.mkdir(mode=0o700)
        try:
            _write_private(root / ROOT_MARKER, b"AMPLIFAI_PHONE_SESSIONS_V1\n")
        except BaseException:
            # This root was just created here and has never held a phone session.
            try:
                shutil.rmtree(root)
            except OSError as exc:
                raise WorkspaceError(
                    "Unable to remove the newly created storage folder",
                    code="workspace_cleanup",
                ) from exc
            raise
    marker = root / ROOT_MARKER
    if not root.is_dir() or marker.is_symlink() or not marker.is_file():
        raise WorkspaceError(
            "Sessions root is not owned by this app", code="workspace_unsafe"
        )
    if marker.read_bytes() != b"AMPLIFAI_PHONE_SESSIONS_V1\n":
        raise WorkspaceError("Sessions root marker is invalid", code="workspace_unsafe")
    if os.name == "nt":
        try:
            private_windows_directory(root)
        except PermissionError as exc:
            raise WorkspaceError(
                "Sessions root permissions are too broad", code="workspace_unsafe"
            ) from exc
    elif root.stat().st_mode & 0o077:
        raise WorkspaceError(
            "Sessions root permissions are too broad", code="workspace_unsafe"
        )
    return True


def _session_marker(directory: Path) -> dict[str, object] | None:
    if (
        directory.is_symlink()
        or (os.name == "nt" and directory.is_junction())
        or not directory.is_dir()
        or not SESSION_NAME.fullmatch(directory.name)
    ):
        return None
    marker = directory / SESSION_MARKER
    if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 512:
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    if (
        not isinstance(data, dict)
        or data.get("version") != 1
        or data.get("session") != directory.name
    ):
        return None
    if not isinstance(data.get("pid"), int) or not isinstance(
        data.get("created_at"), int
    ):
        return None
    return data


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _raise_walk_error(error: OSError) -> None:
    raise error


def _directory_size(directory: Path) -> int:
    total = 0
    for current, subdirs, files in os.walk(
        directory, followlinks=False, onerror=_raise_walk_error
    ):
        subdirs[:] = [
            name
            for name in subdirs
            if not (Path(current) / name).is_symlink()
            and not (os.name == "nt" and (Path(current) / name).is_junction())
        ]
        for name in files:
            path = Path(current) / name
            if path.is_symlink():
                continue
            total += path.stat().st_size
    return total


def abandoned_sessions(root: Path | None = None) -> tuple[Residue, ...]:
    root = root or default_sessions_root()
    try:
        if not _owned_root(root, create=False):
            return ()
        result = []
        for directory in root.iterdir():
            marker = _session_marker(directory)
            if marker is not None and not _pid_alive(marker["pid"]):
                result.append(
                    Residue(
                        directory.name, marker["created_at"], _directory_size(directory)
                    )
                )
        return tuple(sorted(result, key=lambda item: item.created_at))
    except OSError as exc:
        raise WorkspaceError(
            "Backup workspace storage is unavailable", code="workspace_unavailable"
        ) from exc


def clear_abandoned(root: Path | None = None, names: set[str] | None = None) -> int:
    """Delete only explicitly chosen, marker-verified app sessions, never backups elsewhere."""
    root = root or default_sessions_root()
    allowed = {item.name for item in abandoned_sessions(root)}
    chosen = names if names is not None else allowed
    if not chosen <= allowed:
        raise WorkspaceError(
            "A requested session is not an abandoned app-owned session",
            code="workspace_unsafe",
        )
    try:
        for name in sorted(chosen):
            directory = root / name
            if _session_marker(directory) is None or directory.is_symlink():
                raise WorkspaceError(
                    "Session changed during cleanup; nothing else was removed",
                    code="workspace_unsafe",
                )
            shutil.rmtree(directory)
    except OSError as exc:
        raise WorkspaceError(
            "Unable to remove an abandoned backup workspace", code="workspace_cleanup"
        ) from exc
    return len(chosen)


class SessionWorkspace:
    def __init__(self, root: Path | None = None):
        self.root = root or default_sessions_root()
        self.directory: Path | None = None
        self._preserve_for_recovery = False
        self._pending_copy_bytes = 0

    def __enter__(self) -> Path:
        try:
            _owned_root(self.root, create=True)
            if shutil.disk_usage(self.root).free < MIN_FREE_BYTES:
                raise WorkspaceError(
                    "At least 2 GB of free disk space is required",
                    code="workspace_low_space",
                )
            name = "session-" + uuid.uuid4().hex
            directory = self.root / name
            directory.mkdir(mode=0o700)
        except OSError as exc:
            raise WorkspaceError(
                "Backup workspace storage is unavailable", code="workspace_unavailable"
            ) from exc
        marker = {
            "version": 1,
            "session": name,
            "pid": os.getpid(),
            "created_at": int(time.time()),
        }
        try:
            _write_private(
                directory / SESSION_MARKER,
                json.dumps(marker, separators=(",", ":")).encode(),
            )
        except BaseException as exc:
            try:
                shutil.rmtree(directory)
            except OSError as cleanup_error:
                raise WorkspaceError(
                    "Unable to remove the newly created backup workspace",
                    code="workspace_cleanup",
                ) from cleanup_error
            if isinstance(exc, OSError):
                raise WorkspaceError(
                    "Backup workspace storage is unavailable",
                    code="workspace_unavailable",
                ) from exc
            raise
        self.directory = directory
        self._preserve_for_recovery = False
        self._pending_copy_bytes = 0
        return directory

    def preserve_for_recovery(self) -> None:
        if self.directory is None:
            raise WorkspaceError("Workspace has not started")
        self._preserve_for_recovery = True

    def check_bound(self, required_bytes: int = 0, *, copy_delta: int = 0) -> None:
        """Check real free space, including the outstanding parsing-copy budget.

        No directory walk is performed in the transfer hot path. Logical backup
        size is not a capacity ceiling; actual free space and a 2 GiB reserve are.
        """
        if self.directory is None:
            raise WorkspaceError("Workspace has not started")
        if (
            isinstance(required_bytes, bool)
            or not isinstance(required_bytes, int)
            or required_bytes < 0
        ):
            raise ValueError("Invalid write size")
        try:
            if _session_marker(self.directory) is None:
                raise WorkspaceError(
                    "Private backup workspace changed", code="workspace_unsafe"
                )
            pending = max(0, self._pending_copy_bytes + copy_delta)
            if (
                shutil.disk_usage(self.directory).free
                < MIN_FREE_BYTES + pending + required_bytes
            ):
                raise WorkspaceError(
                    "Not enough free disk space for this write, parsing copies and the 2 GB reserve",
                    code="workspace_low_space",
                )
        except OSError as exc:
            raise WorkspaceError(
                "Backup workspace storage is unavailable", code="workspace_unavailable"
            ) from exc

    def checked_path(self, path: Path, *, create_parents: bool = False) -> Path:
        """Require a non-link path beneath this app-owned session, never its root."""
        if self.directory is None:
            raise WorkspaceError("Workspace has not started")
        if path.is_absolute() != self.directory.is_absolute():
            path = path.absolute()
        try:
            parts = path.relative_to(self.directory).parts
        except ValueError:
            raise WorkspaceError(
                "Backup path is outside this session", code="workspace_unsafe"
            ) from None
        if not parts or len(parts) > 64 or any(part in (".", "..") for part in parts):
            raise WorkspaceError("Invalid backup path", code="workspace_unsafe")
        self.check_bound()
        current = self.directory
        try:
            for index, part in enumerate(parts):
                current = current / part
                if current.is_symlink() or (os.name == "nt" and current.is_junction()):
                    raise WorkspaceError(
                        "Backup path contains a link", code="workspace_unsafe"
                    )
                if index < len(parts) - 1 and create_parents:
                    current.mkdir(mode=0o700, exist_ok=True)
            return current
        except OSError as exc:
            code = (
                "workspace_low_space"
                if exc.errno in {errno.ENOSPC, errno.EDQUOT}
                else "workspace_unavailable"
            )
            raise WorkspaceError(
                "Private backup storage is unavailable", code=code
            ) from exc

    def open_private(self, path: Path) -> BinaryIO:
        """Open an owned regular file without following links or truncating first."""
        import stat

        path = self.checked_path(path, create_parents=True)
        handle = None
        try:
            handle = os.open(
                path, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
            )
            info = os.fstat(handle)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise WorkspaceError(
                    "Backup destination is not a private file", code="workspace_unsafe"
                )
            if os.name != "nt":
                os.fchmod(handle, 0o600)
            os.ftruncate(handle, 0)
            result = os.fdopen(handle, "wb")
            handle = None
            return result
        except OSError as exc:
            code = (
                "workspace_low_space"
                if exc.errno in {errno.ENOSPC, errno.EDQUOT}
                else "workspace_unavailable"
            )
            raise WorkspaceError(
                "Private backup storage is unavailable", code=code
            ) from exc
        finally:
            if handle is not None:
                os.close(handle)

    def write_chunk(
        self,
        stream: BinaryIO,
        payload: bytes,
        *,
        reserve_copy: bool = False,
        consume_copy: bool = False,
    ) -> None:
        """Enforce disk headroom at the actual write, not after a whole file."""
        if reserve_copy and consume_copy:
            raise ValueError("A write cannot both reserve and consume a copy")
        delta = (
            len(payload)
            if reserve_copy
            else -min(len(payload), self._pending_copy_bytes)
            if consume_copy
            else 0
        )
        self.check_bound(len(payload), copy_delta=delta)
        try:
            if stream.write(payload) != len(payload):
                raise OSError(errno.EIO, "Incomplete backup write")
        except OSError as exc:
            code = (
                "workspace_low_space"
                if exc.errno in {errno.ENOSPC, errno.EDQUOT}
                else "workspace_unavailable"
            )
            raise WorkspaceError(
                "Private backup write could not complete", code=code
            ) from exc
        self._pending_copy_bytes = max(0, self._pending_copy_bytes + delta)

    def release_copy_budget(self, byte_count: int) -> None:
        if (
            isinstance(byte_count, bool)
            or not isinstance(byte_count, int)
            or byte_count < 0
        ):
            raise ValueError("Invalid copy budget")
        self._pending_copy_bytes = max(0, self._pending_copy_bytes - byte_count)

    def available_transfer_bytes(self) -> int:
        """Actual selected-file budget after reserve and pending parsing copies."""
        self.check_bound()
        return max(
            0,
            shutil.disk_usage(self.directory).free
            - MIN_FREE_BYTES
            - self._pending_copy_bytes,
        )

    def advertised_stream_capacity_bytes(self) -> int:
        """Logical DeviceLink stream capacity, never a physical-write allowance.

        A full iPhone backup still streams nonselected files through this app,
        but those bytes are discarded instead of occupying the host volume.
        The device's full-backup size preflight must not compare those bytes to
        the much smaller selected-file budget. Every retained write and copy
        still passes check_bound and preserves the real 2 GiB disk reserve.
        """
        return max(STREAM_RECEIVER_CAPACITY_BYTES, self.available_transfer_bytes())

    def __exit__(self, _kind: object, _value: object, _traceback: object) -> None:
        if self._preserve_for_recovery:
            return
        try:
            if (
                self.directory is not None
                and _session_marker(self.directory) is not None
            ):
                shutil.rmtree(self.directory)
                self.directory = None
        except OSError as exc:
            raise WorkspaceError(
                "Unable to remove the current backup workspace",
                code="workspace_cleanup",
                primary_error=_value if isinstance(_value, BaseException) else None,
            ) from exc
