"""Copied frozen-helper handoff: verify, wait for normal parent exit, then install."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path, PurePosixPath

from amplifai_phone.windows_storage import windows_process_alive
from installer_guard import InstallerReadGuard, windows_installer_path
from updater import (PUBLIC_KEY, REPARSE_POINT, PreparedUpdate, UpdateError, _unique_fields,
                     cached_manifest, require_matching_cpu, verify_installer)

MAX_RUNTIME_FILES = 25000
MAX_RUNTIME_JSON = 8 * 1024 * 1024
MAX_RUNTIME_BYTES = 512 * 1024 * 1024
READY_SECONDS = 90
PARENT_EXIT_SECONDS = 30
READY = {"schema": 1, "event": "update_ready"}


def validate_runtime_records(records: object) -> list[dict]:
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_RUNTIME_FILES:
        raise UpdateError("Updater runtime inventory is invalid.")
    seen, total = set(), 0
    for row in records:
        if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}:
            raise UpdateError("Updater runtime inventory fields are invalid.")
        name, size, digest = row["path"], row["bytes"], row["sha256"]
        if (not isinstance(name, str) or not 1 <= len(name) <= 1024 or any(ord(char) < 32 for char in name)
                or any(char in name for char in '\\:<>"|?*') or name.startswith("/")
                or any(part in {".", "..", ""} for part in name.split("/"))
                or type(size) is not int or not 0 <= size <= MAX_RUNTIME_BYTES
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or name.casefold() in seen):
            raise UpdateError("Updater runtime inventory contains an unsafe file.")
        seen.add(name.casefold()); total += size
    if total > MAX_RUNTIME_BYTES or "amplifaiphonehelper.exe" not in seen or not any(path.startswith("_internal/") for path in seen):
        raise UpdateError("Matching frozen updater helper runtime is missing.")
    return records


def _regular(path: Path, *, directory: bool) -> None:
    item = path.lstat()
    if (stat.S_ISLNK(item.st_mode) or getattr(item, "st_file_attributes", 0) & REPARSE_POINT
            or not (stat.S_ISDIR(item.st_mode) if directory else stat.S_ISREG(item.st_mode))):
        raise UpdateError("Updater runtime path is missing, linked or changed.")


def validate_runtime_files(root: Path, records: list[dict]) -> None:
    validate_runtime_records(records); _regular(root, directory=True)
    actual = set()
    def refused(_error):
        raise UpdateError("Updater runtime could not be inspected.")
    for parent, directories, files in os.walk(root, followlinks=False, onerror=refused):
        for name in directories:
            _regular(Path(parent) / name, directory=True)
        for name in files:
            path = Path(parent) / name; _regular(path, directory=False)
            actual.add(path.relative_to(root).as_posix())
    if actual != {row["path"] for row in records}:
        raise UpdateError("Updater runtime file set changed.")
    for row in records:
        path = root.joinpath(*PurePosixPath(row["path"]).parts)
        if path.stat().st_size != row["bytes"]:
            raise UpdateError("Updater runtime file length changed.")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != row["sha256"]:
            raise UpdateError("Updater runtime file checksum changed.")


def read_runtime_inventory(root: Path, expected_sha256: str) -> list[dict]:
    path = root / "bootstrap-inventory.json"; _regular(path, directory=False)
    with path.open("rb") as source:
        data = source.read(MAX_RUNTIME_JSON + 1)
    if len(data) > MAX_RUNTIME_JSON or hashlib.sha256(data).hexdigest() != expected_sha256:
        raise UpdateError("Updater runtime inventory checksum changed.")
    return validate_runtime_records(json.loads(data.decode("utf-8"), object_pairs_hook=_unique_fields))


class RuntimeReadGuard:
    def __init__(self, root: Path, records: list[dict]) -> None:
        self.root, self.records, self.guards = root, records, []

    def open(self) -> None:
        validate_runtime_files(self.root, self.records)
        try:
            for row in self.records:
                path = self.root.joinpath(*PurePosixPath(row["path"]).parts)
                guard = InstallerReadGuard(path.parent, path)
                guard.open(); self.guards.append(guard)
                data = guard.read(row["bytes"] + 1)
                if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                    raise UpdateError("Pinned updater runtime changed.")
        except (OSError, ValueError):
            self.close(); raise

    def close(self) -> None:
        for guard in reversed(self.guards):
            guard.close()
        self.guards.clear()


def _write_marker(root: Path, marker: dict) -> None:
    path = root / "ownership.new"
    data = json.dumps(marker, separators=(",", ":")).encode("utf-8")
    if len(data) > 2048:
        raise UpdateError("Updater ownership marker exceeds its limit.")
    with path.open("wb") as target:
        target.write(data); target.flush(); os.fsync(target.fileno())
    os.replace(path, root / "ownership.json")


def _command(stream, expected: str) -> None:
    line = stream.readline(129)
    if isinstance(line, bytes):
        line = line.decode("utf-8")
    if len(line.encode("utf-8")) > 128 or not line.endswith("\n"):
        raise UpdateError("Updater handoff was not authorized.")
    if json.loads(line, object_pairs_hook=_unique_fields) != {"schema": 1, "action": expected}:
        raise UpdateError("Updater handoff command is invalid.")


def run_installer(root: Path, *, input_stream, output_stream, public_key: str = PUBLIC_KEY,
                  parent_alive=windows_process_alive, installer_factory=subprocess.Popen,
                  guard_factory=InstallerReadGuard) -> int:
    _command(input_stream, "prepare")
    marker, manifest = cached_manifest(root, public_key=public_key)
    if marker["helperPid"] != os.getpid() or marker["ownerPid"] == os.getpid() or not marker["installing"] or marker["bootstrapSha256"] is None:
        raise UpdateError("Updater ownership does not match this helper.")
    records = read_runtime_inventory(root, marker["bootstrapSha256"])
    runtime_guard = RuntimeReadGuard(root / "bootstrap", records)
    msi_guard = guard_factory(root, root / "AmplifaiPhone-verified.msi")
    try:
        runtime_guard.open(); msi_guard.open()
        verify_installer(msi_guard.read(manifest.size + 1), manifest, public_key)
        installer = windows_installer_path()
        if not installer.is_file():
            raise UpdateError("Normal Windows Installer is unavailable.")
        output_stream.write(json.dumps(READY, separators=(",", ":")) + "\n"); output_stream.flush()
        _command(input_stream, "commit")
        if input_stream.read(1) not in {"", b""}:
            raise UpdateError("Updater handoff did not close normally.")
        deadline = time.monotonic() + PARENT_EXIT_SECONDS
        while parent_alive(marker["ownerPid"]):
            if time.monotonic() >= deadline:
                raise UpdateError("Companion has not closed; installation was refused.")
            threading.Event().wait(0.05)
        process = installer_factory([str(installer), "/i", str(root / "AmplifaiPhone-verified.msi")],
                                    shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, cwd=str(root))
        marker["installerPid"] = process.pid
        receipt_error = None
        try:
            _write_marker(root, marker)
        except OSError as error:
            receipt_error = error
        # A receipt failure cannot release immutable handles while MSI is running.
        result = process.wait()
        marker["installing"] = False; marker["installerPid"] = None
        try:
            _write_marker(root, marker)
        except OSError as error:
            receipt_error = error
        if receipt_error is not None:
            raise UpdateError("Installer exited but its ownership receipt could not be recorded.") from receipt_error
        return result
    finally:
        msi_guard.close(); runtime_guard.close()


class Handoff:
    def __init__(self, process, lifetime: ExitStack) -> None:
        self.process, self.lifetime = process, lifetime

    def commit(self) -> None:
        try:
            self.process.stdin.write(b'{"schema":1,"action":"commit"}\n'); self.process.stdin.flush()
        finally:
            self.lifetime.close()

    def abort(self) -> None:
        self.lifetime.close()


def require_frozen_windows() -> None:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise UpdateError("The matching frozen Windows updater helper is required.")
    require_matching_cpu()


def start_handoff(prepared: PreparedUpdate, helper: Path) -> Handoff:
    require_frozen_windows()
    try:
        from _windows_update_bootstrap import HELPER_RECORDS
    except ImportError as error:
        raise UpdateError("The frozen updater bootstrap inventory is missing.") from error
    records = validate_runtime_records(HELPER_RECORDS)
    cached_manifest(prepared.directory, public_key=prepared.public_key)
    if not helper.is_absolute() or helper.name != "AmplifaiPhoneHelper.exe":
        raise UpdateError("The matching frozen updater helper is missing.")
    try:
        with ExitStack() as lifetime:
            cache_guard = InstallerReadGuard(prepared.directory, prepared.path)
            cache_guard.open(); lifetime.callback(cache_guard.close)
            verify_installer(cache_guard.read(prepared.manifest.size + 1), prepared.manifest, prepared.public_key)
            source_guard = RuntimeReadGuard(helper.parent, records)
            source_guard.open()
            destination = prepared.directory / "bootstrap"
            try:
                shutil.copytree(helper.parent, destination)
            finally:
                source_guard.close()
            inventory = json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if len(inventory) > MAX_RUNTIME_JSON:
                raise UpdateError("Updater bootstrap inventory exceeds its limit.")
            (prepared.directory / "bootstrap-inventory.json").write_bytes(inventory)
            prepared.bootstrap_sha256 = hashlib.sha256(inventory).hexdigest()
            copied_guard = RuntimeReadGuard(destination, records)
            copied_guard.open(); lifetime.callback(copied_guard.close)
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None); environment.pop("PYTHONHOME", None)
            environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            process = subprocess.Popen([str(destination / helper.name), "install-verified-update", "--handoff-root", str(prepared.directory)],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       shell=False, close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW,
                                       cwd=str(destination), env=environment)
            lifetime.callback(process.stdin.close)
            prepared.helper_pid = process.pid
            prepared._write_marker(installing=True, installer_pid=None)
            process.stdin.write(b'{"schema":1,"action":"prepare"}\n'); process.stdin.flush()
            received = queue.Queue(maxsize=1)
            def read_ready():
                try:
                    received.put(process.stdout.readline(257))
                finally:
                    process.stdout.close()
            threading.Thread(target=read_ready, daemon=True).start()
            line = received.get(timeout=READY_SECONDS)
            if len(line) > 256 or not line.endswith(b"\n") or json.loads(line.decode("utf-8"), object_pairs_hook=_unique_fields) != READY or process.poll() is not None:
                raise UpdateError("Copied updater helper did not confirm verified readiness.")
            return Handoff(process, lifetime.pop_all())
    except (OSError, ValueError, queue.Empty) as error:
        raise UpdateError("The verified updater helper could not prepare. The companion remains open.") from error


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        require_frozen_windows()
        result = run_installer(args.handoff_root, input_stream=sys.stdin, output_stream=sys.stdout)
        marker, manifest = cached_manifest(args.handoff_root)
        if not marker["installing"] and marker["helperPid"] == os.getpid():
            path = args.handoff_root / "AmplifaiPhone-verified.msi"
            _regular(path, directory=False)
            with path.open("rb") as source:
                verify_installer(source.read(manifest.size + 1), manifest)
            path.unlink()
        return result
    except (OSError, ValueError):
        print('{"schema":1,"event":"update_failed"}', flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
