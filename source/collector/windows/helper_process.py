"""Private pipe boundary; never interpret TerminateProcess as graceful cleanup."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

MAX_EVENT_BYTES = 16 * 1024 * 1024
MAX_COMMAND_BYTES = 600_000


def _resource_kind(path: Path, *, directory: bool) -> None:
    entry = path.lstat()
    if stat.S_ISLNK(entry.st_mode) or getattr(entry, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("Linked package resource")
    if not (stat.S_ISDIR(entry.st_mode) if directory else stat.S_ISREG(entry.st_mode)):
        raise ValueError("Missing package resource")


def _unreadable_runtime(_error: OSError) -> None:
    raise ValueError("Unreadable package resource")


def bundled_resources(executable: Path, *, frozen: bool) -> tuple[Path, Path]:
    """Validate the fixed onedir shape, not its unqualified build/signature/hash."""
    try:
        if not frozen or not executable.is_absolute() or executable.name != "AmplifaiPhone.exe":
            raise ValueError("Not the frozen application")
        _resource_kind(executable.parent, directory=True)
        _resource_kind(executable, directory=False)
        base = executable.parent.resolve(strict=True)
        resources = base / "resources"
        bundle = resources / "phone-helper"
        runtime = bundle / "_internal"
        helper = bundle / "AmplifaiPhoneHelper.exe"
        logo = resources / "amplifai-by-nexus-original.png"
        for directory in (resources, bundle, runtime):
            _resource_kind(directory, directory=True)
        for resource in (helper, logo):
            _resource_kind(resource, directory=False)
            resource.resolve(strict=True).relative_to(base)
        # Do not accept a matching directory name whose DLLs/assets escape it.
        for parent, directories, files in os.walk(runtime, followlinks=False, onerror=_unreadable_runtime):
            for name in directories:
                _resource_kind(Path(parent) / name, directory=True)
            for name in files:
                _resource_kind(Path(parent) / name, directory=False)
    except (OSError, ValueError, RuntimeError):
        raise ValueError("Matching contained onedir helper and original logo are required; no fallback is launched.") from None
    return helper, logo


class HelperProcess:
    def __init__(self, command: list[str], emit: Callable[[dict], None]) -> None:
        if not command or not Path(command[0]).is_absolute():
            raise ValueError("An absolute owned helper executable is required")
        self.command = tuple(command)
        self.emit = emit
        self.process: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self._input_lock = threading.RLock()

    def start(self, mode: str) -> None:
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError("Owned helper is still running")
        if mode not in {"connect", "inspect", "clear-residue"}:
            raise ValueError("Invalid helper mode")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
        # Public PyInstaller control; never mutate its private _PYI_* variables.
        environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        self.process = subprocess.Popen(
            [*self.command, mode], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", shell=False,
            close_fds=True, creationflags=flags,
            cwd=str(Path(self.command[0]).parent), env=environment,
        )
        self.thread = threading.Thread(target=self._read, args=(self.process,), daemon=True)
        self.thread.start()

    def _read(self, process: subprocess.Popen) -> None:
        try:
            while True:
                line = process.stdout.readline(MAX_EVENT_BYTES + 1)
                if not line:
                    break
                if not line.endswith("\n") or len(line.encode("utf-8")) > MAX_EVENT_BYTES:
                    raise ValueError("Invalid helper frame")
                packet = json.loads(line)
                if not isinstance(packet, dict) or packet.get("kind") == "process-exit":
                    raise ValueError("Invalid helper packet")
                self.emit(packet)
        except (ValueError, OSError, RecursionError):
            self.emit({"kind": "error", "code": "protocol", "cleanupRequired": True})
            self._close_input(process)
            # EOF is cooperative, not an immediate capture interruption. Drain
            # without parsing/displaying further private output until real exit.
            stream = getattr(process.stdout, "buffer", process.stdout)
            try:
                while stream.read(64 * 1024):
                    pass
            except (OSError, ValueError):
                pass  # Broken output does not authorize force-stop or cleanup.
        finally:
            process.stdout.close()
            self._close_input(process)
            self.emit({"kind": "process-exit", "code": process.wait()})

    @property
    def stdin(self):
        if self.process is None or self.process.poll() is not None or self.process.stdin.closed:
            raise RuntimeError("Private helper pipe is unavailable")
        return self.process.stdin

    def write(self, packet: dict) -> None:
        line = json.dumps(packet, ensure_ascii=False) + "\n"
        if len(line.encode("utf-8")) > MAX_COMMAND_BYTES:
            raise ValueError("Command exceeds helper limit")
        with self._input_lock:
            self.stdin.write(line)
            self.stdin.flush()

    def _close_input(self, process: subprocess.Popen) -> None:
        with self._input_lock:
            try:
                if not process.stdin.closed:
                    process.stdin.close()
            except (OSError, ValueError):
                pass  # A broken/closed pipe still never proves engine cleanup.

    def close_input(self) -> None:
        if self.process is not None:
            self._close_input(self.process)

    def force_stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()

    def join(self, timeout: float) -> None:
        if self.thread:
            self.thread.join(timeout)
            if self.thread.is_alive():
                raise TimeoutError("Helper did not finish")
