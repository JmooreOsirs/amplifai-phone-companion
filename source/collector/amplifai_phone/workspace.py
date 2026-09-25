"""Private, bounded backup workspaces with explicit crash-residue recovery."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

ROOT_MARKER = ".amplifai-phone-sessions-v1"
SESSION_MARKER = ".amplifai-owned-session.json"
SESSION_NAME = re.compile(r"^session-[0-9a-f]{32}$")
MAX_SESSION_BYTES = 1024 * 1024 * 1024
MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024
MAX_BACKUP_SECONDS = 60 * 60


class WorkspaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Residue:
    name: str
    created_at: int
    bytes_on_disk: int


def default_sessions_root() -> Path:
    if os.name == "nt":
        parent = os.environ.get("LOCALAPPDATA")
        if not parent:
            raise WorkspaceError("LOCALAPPDATA is unavailable")
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
    if root.is_symlink():
        raise WorkspaceError("Sessions root cannot be a symlink")
    if not root.exists():
        if not create:
            return False
        parent = root.parent
        if parent.is_symlink():
            raise WorkspaceError("App support directory cannot be a symlink")
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        root.mkdir(mode=0o700)
        _write_private(root / ROOT_MARKER, b"AMPLIFAI_PHONE_SESSIONS_V1\n")
    marker = root / ROOT_MARKER
    if not root.is_dir() or marker.is_symlink() or not marker.is_file():
        raise WorkspaceError("Sessions root is not owned by this app")
    if marker.read_bytes() != b"AMPLIFAI_PHONE_SESSIONS_V1\n":
        raise WorkspaceError("Sessions root marker is invalid")
    if os.name != "nt" and root.stat().st_mode & 0o077:
        raise WorkspaceError("Sessions root permissions are too broad")
    return True


def _session_marker(directory: Path) -> dict[str, object] | None:
    if (
        directory.is_symlink()
        or not directory.is_dir()
        or not SESSION_NAME.fullmatch(directory.name)
    ):
        return None
    marker = directory / SESSION_MARKER
    if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 512:
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if data.get("version") != 1 or data.get("session") != directory.name:
        return None
    if not isinstance(data.get("pid"), int) or not isinstance(
        data.get("created_at"), int
    ):
        return None
    return data


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _directory_size(directory: Path) -> int:
    total = 0
    for current, subdirs, files in os.walk(directory, followlinks=False):
        subdirs[:] = [
            name for name in subdirs if not (Path(current) / name).is_symlink()
        ]
        for name in files:
            path = Path(current) / name
            if path.is_symlink():
                continue
            total += path.stat().st_size
            if total > MAX_SESSION_BYTES:
                return total
    return total


def abandoned_sessions(root: Path | None = None) -> tuple[Residue, ...]:
    root = root or default_sessions_root()
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


def clear_abandoned(root: Path | None = None, names: set[str] | None = None) -> int:
    """Delete only explicitly chosen, marker-verified app sessions, never backups elsewhere."""
    root = root or default_sessions_root()
    allowed = {item.name for item in abandoned_sessions(root)}
    chosen = names if names is not None else allowed
    if not chosen <= allowed:
        raise WorkspaceError(
            "A requested session is not an abandoned app-owned session"
        )
    for name in sorted(chosen):
        directory = root / name
        if _session_marker(directory) is None or directory.is_symlink():
            raise WorkspaceError(
                "Session changed during cleanup; nothing else was removed"
            )
        shutil.rmtree(directory)
    return len(chosen)


class SessionWorkspace:
    def __init__(self, root: Path | None = None):
        self.root = root or default_sessions_root()
        self.directory: Path | None = None

    def __enter__(self) -> Path:
        _owned_root(self.root, create=True)
        if shutil.disk_usage(self.root).free < MIN_FREE_BYTES:
            raise WorkspaceError("At least 2 GB of free disk space is required")
        name = "session-" + uuid.uuid4().hex
        directory = self.root / name
        directory.mkdir(mode=0o700)
        marker = {
            "version": 1,
            "session": name,
            "pid": os.getpid(),
            "created_at": int(time.time()),
        }
        _write_private(
            directory / SESSION_MARKER,
            json.dumps(marker, separators=(",", ":")).encode(),
        )
        self.directory = directory
        return directory

    def check_bound(self) -> None:
        if self.directory is None:
            raise WorkspaceError("Workspace has not started")
        if _directory_size(self.directory) > MAX_SESSION_BYTES:
            raise WorkspaceError("Selected backup data exceeded the 1 GB local limit")
        if shutil.disk_usage(self.directory).free < MIN_FREE_BYTES:
            raise WorkspaceError("Less than 2 GB free disk space remains")

    def __exit__(self, _kind: object, _value: object, _traceback: object) -> None:
        if self.directory is not None and _session_marker(self.directory) is not None:
            shutil.rmtree(self.directory)
            self.directory = None
