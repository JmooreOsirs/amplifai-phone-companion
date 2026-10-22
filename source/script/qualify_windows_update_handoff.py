"""Qualify exact signed rc21 bytes against a normally closed running rc20 app on CI."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import http.client
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from amplifai_phone.workspace import ROOT_MARKER, SessionWorkspace, default_sessions_root
from build_windows_candidate import clean_child_environment, file_records
from export_windows_pillow_evidence import export as export_pillow_evidence
from installer_guard import InstallerReadGuard
from updater import BUILD, INSTALLER_VERSION, MAX_MANIFEST_BYTES, PUBLIC_KEY, VERSION, PreparedUpdate, cached_manifest, parse_manifest, verify_installer
from update_installer import READY, RuntimeReadGuard
from verify_windows_install import verify

BASELINE_BYTES = 42285215
BASELINE_SHA256 = "6c138c0c4b935908b8632f365e6f80178e1abb7e170aa59fbbab16a42b97dd0c"
FIXED_DATA_NAME = "AMPLIFai Phone Candidate"


def runner_msi(package: Path, *, remove=False) -> None:
    # Disposable runner staging/cleanup only; production helper always uses normal /i UI.
    result = subprocess.run(["msiexec.exe", "/x" if remove else "/i", str(package), "/qn", "/norestart"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=120, check=False)
    if result.returncode not in ({0, 1605, 3010} if remove else {0, 3010}):
        raise ValueError("Runner-only MSI staging or cleanup failed")


def owned_window(pid: int):
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = (callback, wintypes.LPARAM); user.EnumWindows.restype = wintypes.BOOL
    user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    found = []
    def examine(window, _context):
        owner = wintypes.DWORD(); user.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value == pid:
            title = ctypes.create_unicode_buffer(256); user.GetWindowTextW(window, title, len(title))
            if title.value.startswith("AMPLIFai Phone"):
                found.append((window, title.value))
        return True
    user.EnumWindows(callback(examine), 0)
    return found[0] if found else (None, "")


def wait_idle(process) -> None:
    deadline = time.monotonic() + 30
    while True:
        _window, title = owned_window(process.pid)
        if title.endswith(" · ready"):
            return
        if process.poll() is not None or time.monotonic() >= deadline:
            raise ValueError("Owned companion did not reach its clean idle state")
        threading.Event().wait(0.05)


def installed_status() -> dict:
    connection = http.client.HTTPConnection("127.0.0.1", 48752, timeout=3)
    try:
        connection.request("GET", "/v1/status", headers={"Origin": "https://amplifai.satoris.ai", "Host": "127.0.0.1:48752"})
        response = connection.getresponse(); data = response.read(4097)
        if response.status != 200 or len(data) > 4096:
            raise ValueError("Installed companion status was unavailable or unbounded")
        status = json.loads(data)
        expected = {"platform": "windows", "architecture": "x86_64", "channel": "windows-x64-test", "version": VERSION, "build": BUILD}
        if any(status.get(key) != value for key, value in expected.items()):
            raise ValueError("Relaunched companion identity differs from the signed rc21 candidate")
        return expected
    finally:
        connection.close()


def installed_setup_capabilities() -> dict:
    connection = http.client.HTTPConnection("127.0.0.1", 48752, timeout=3)
    try:
        connection.request("GET", "/v1/setup-capabilities", headers={
            "Origin": "https://amplifai.satoris.ai", "Host": "127.0.0.1:48752"})
        response = connection.getresponse()
        data = response.read(4097)
        expected = {"schema": 2, "product": "amplifai-phone", "platform": "windows",
                    "architecture": "x86_64", "channel": "windows-x64-test",
                    "bundleId": "ai.satoris.amplifai.phone.windows-candidate",
                    "version": VERSION, "build": BUILD, "protocols": ["phone-native-setup-v2"]}
        if response.status != 200 or len(data) > 4096 or json.loads(data) != expected:
            raise ValueError("Installed native setup discovery differs from the frozen candidate")
        return expected
    finally:
        connection.close()


def verify_frozen_plain_delivery(installed: Path, primary) -> None:
    secondary = subprocess.run([str(installed / "AmplifaiPhone.exe")], cwd=str(installed),
                               env=clean_child_environment(), stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               shell=False, timeout=20, check=False)
    if secondary.returncode != 0 or primary.poll() is not None:
        raise ValueError("Frozen plain launch did not route to the existing companion")
    installed_setup_capabilities()


def close_normally(pid: int) -> None:
    window, _title = owned_window(pid)
    if window is None:
        raise ValueError("Owned old companion window was not found")
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SendMessageTimeoutW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t))
    user.SendMessageTimeoutW.restype = ctypes.c_size_t
    result = ctypes.c_size_t()
    if not user.SendMessageTimeoutW(window, 0x10, 0, 0, 2, 5000, ctypes.byref(result)):
        raise ValueError("Owned old companion did not accept normal WM_CLOSE")


def qualify(args) -> dict:
    if os.name != "nt" or not os.environ.get("GITHUB_ACTIONS"):
        raise ValueError("This qualification is restricted to the existing disposable Windows CI runner")
    with args.manifest.open("rb") as source:
        manifest_bytes = source.read(MAX_MANIFEST_BYTES + 1)
    with args.signature.open("rb") as source:
        signature_bytes = source.read(129)
    manifest = parse_manifest(manifest_bytes, signature_bytes)
    if (manifest.version, manifest.build, manifest.installer_version) != (VERSION, BUILD, INSTALLER_VERSION):
        raise ValueError("Signed fixture is not the exact rc21 candidate identity")
    if args.msi.stat().st_size != manifest.size:
        raise ValueError("Signed rc21 MSI byte count differs from local fixture")
    package_bytes = args.msi.read_bytes(); verify_installer(package_bytes, manifest)
    if args.baseline.stat().st_size != BASELINE_BYTES or hashlib.sha256(args.baseline.read_bytes()).hexdigest() != BASELINE_SHA256:
        raise ValueError("Released rc20 MSI differs from the pinned baseline")
    installed = Path(os.environ["LOCALAPPDATA"]) / "AMPLIFaiPhone"
    sessions = default_sessions_root(); data_root = sessions.parent
    if data_root.name != FIXED_DATA_NAME or installed.exists() or data_root.exists():
        raise ValueError("Qualification requires clean runner-owned app/data paths")
    prepared = PreparedUpdate.__new__(PreparedUpdate)
    prepared.directory = Path(tempfile.mkdtemp(prefix="AmplifaiPhone-update-"))
    prepared.manifest, prepared.public_key = manifest, PUBLIC_KEY
    prepared.owner_pid, prepared.helper_pid, prepared.bootstrap_sha256 = os.getpid(), None, None
    prepared.path = prepared.directory / "AmplifaiPhone-verified.msi"
    prepared.path.write_bytes(package_bytes)
    (prepared.directory / "manifest.json").write_bytes(manifest.signed_bytes)
    (prepared.directory / "manifest.sig").write_bytes(manifest.signed_signature)
    prepared._write_marker(installing=False, installer_pid=None)
    old_app = new_app = helper = None
    cache_guard = runtime_guard = None
    moved = prepared.directory.with_name(prepared.directory.name + "-replacement")
    started = time.monotonic()
    try:
        runner_msi(args.msi)
        verify(installed, args.candidate_receipt, removed=False)
        pillow_evidence = export_pillow_evidence(installed, args.candidate_receipt,
                                                args.output.parent / "native-source-evidence", manifest.sha256)
        source = installed / "resources/phone-helper"
        records = file_records(source)
        source_guard = RuntimeReadGuard(source, records); source_guard.open()
        try:
            shutil.copytree(source, prepared.directory / "bootstrap")
        finally:
            source_guard.close()
        inventory = json.dumps(records, sort_keys=True, separators=(",", ":")).encode("utf-8")
        (prepared.directory / "bootstrap-inventory.json").write_bytes(inventory)
        prepared.bootstrap_sha256 = hashlib.sha256(inventory).hexdigest()
        runner_msi(args.msi, remove=True)
        runner_msi(args.baseline)
        old_app = subprocess.Popen([str(installed / "AmplifaiPhone.exe")], cwd=str(installed),
                                   env=clean_child_environment(), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
        wait_idle(old_app)
        with SessionWorkspace(sessions) as fixture:
            sentinel = fixture / "ci-upgrade-sentinel.txt"
            sentinel.write_bytes(b"runner-owned preserved session context")
            sentinel_hash = hashlib.sha256(sentinel.read_bytes()).hexdigest()
            cache_guard = InstallerReadGuard(prepared.directory, prepared.path); cache_guard.open()
            verify_installer(cache_guard.read(manifest.size + 1), manifest)
            runtime_guard = RuntimeReadGuard(prepared.directory / "bootstrap", records); runtime_guard.open()
            bootstrap = prepared.directory / "bootstrap/AmplifaiPhoneHelper.exe"
            helper = subprocess.Popen([str(bootstrap), "install-verified-update", "--handoff-root", str(prepared.directory)],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                      cwd=str(bootstrap.parent), env=clean_child_environment(), shell=False,
                                      close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW)
            prepared.owner_pid, prepared.helper_pid = old_app.pid, helper.pid
            prepared._write_marker(installing=True, installer_pid=None)
            helper.stdin.write(b'{"schema":1,"action":"prepare"}\n'); helper.stdin.flush()
            received = queue.Queue(maxsize=1)
            def readiness():
                try:
                    received.put(helper.stdout.readline(257))
                finally:
                    helper.stdout.close()
            threading.Thread(target=readiness, daemon=True).start()
            line = received.get(timeout=90)
            if len(line) > 256 or not line.endswith(b"\n") or json.loads(line) != READY or old_app.poll() is not None or helper.poll() is not None:
                raise ValueError("Copied frozen helper did not ready while the old app was running")
            # Probe only the frozen helper's guards, after releasing qualifier handles.
            runtime_guard.close(); runtime_guard = None
            cache_guard.close(); cache_guard = None
            try:
                with prepared.path.open("wb") as target:
                    target.write(b"guard test: invalid MSI")
            except OSError:
                pass
            else:
                raise ValueError("Ready helper did not prevent MSI edits")
            try:
                os.replace(prepared.directory, moved)
            except OSError:
                pass
            else:
                raise ValueError("Ready helper did not prevent cache directory replacement")
            helper.stdin.write(b'{"schema":1,"action":"commit"}\n'); helper.stdin.flush(); helper.stdin.close()
            parent_wait_started = time.monotonic()
            while time.monotonic() - parent_wait_started < 1.0:
                if old_app.poll() is not None or helper.poll() is not None:
                    raise ValueError("Parent or helper exited before the parent-wait observation")
                marker, _manifest = cached_manifest(prepared.directory)
                if (marker["ownerPid"] != old_app.pid or marker["helperPid"] != helper.pid
                        or not marker["installing"] or marker["installerPid"] is not None):
                    raise ValueError("Installer advanced before the running old companion closed")
                threading.Event().wait(0.05)
            parent_wait_seconds = round(time.monotonic() - parent_wait_started, 3)
            close_normally(old_app.pid)
            if old_app.wait(timeout=30) != 0:
                raise ValueError("Old companion did not close normally")
            result = helper.wait(timeout=180)
            if result not in {0, 3010}:
                raise ValueError("Normal Windows Installer handoff did not complete")
            verify(installed, args.candidate_receipt, removed=False)
            if hashlib.sha256(sentinel.read_bytes()).hexdigest() != sentinel_hash:
                raise ValueError("MSI handoff changed the existing session fixture")
        if fixture.exists():
            raise ValueError("Test-owned session did not finish cleanup before the clean relaunch check")
        new_app = subprocess.Popen([str(installed / "AmplifaiPhone.exe")], cwd=str(installed),
                                   env=clean_child_environment(), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
        wait_idle(new_app)
        observed_identity = installed_status()
        setup_capabilities = installed_setup_capabilities()
        verify_frozen_plain_delivery(installed, new_app)
        close_normally(new_app.pid)
        if new_app.wait(timeout=30) != 0:
            raise ValueError("Relaunched companion did not close normally")
        return {"status": "actual-windows-frozen-helper-running-rc20-to-rc21-handoff", "schema": 1,
                "msiSha256": manifest.sha256, "version": manifest.version, "build": manifest.build,
                "installerVersion": manifest.installer_version, "readyWhileOldAppRunning": True,
                "oldAppClosedNormally": True, "installerWaitedForParentExit": True,
                "normalInstallerUi": True, "immutableMsiAndDirectory": True,
                "immutableGuardsOwnedByFrozenHelper": True, "parentAliveAfterCommitSeconds": parent_wait_seconds,
                "sessionFixturePreserved": True,
                "installedAppStatus": observed_identity, "installedAppRelaunched": True,
                "installedSetupCapabilities": setup_capabilities,
                "frozenSameLogonPlainDelivery": True,
                "retiredCacheCleanedOnRelaunch": not prepared.directory.exists(),
                "pillowNativeFilesMatched": len(pillow_evidence["nativeFiles"]),
                "elapsedSeconds": round(time.monotonic() - started, 3),
                "consumerPcOrPhoneAccountProved": False}
    finally:
        if helper is not None and helper.stdin is not None and not helper.stdin.closed:
            helper.stdin.close()
        if runtime_guard is not None: runtime_guard.close()
        if cache_guard is not None: cache_guard.close()
        for app in (new_app, old_app):
            if app is not None and app.poll() is None:
                close_normally(app.pid); app.wait(timeout=30)
        if helper is not None and helper.poll() is None:
            helper.wait(timeout=35)
        if moved.exists(): os.replace(moved, prepared.directory)
        runner_msi(args.msi, remove=True); runner_msi(args.baseline, remove=True)
        if sessions.exists() and {path.name for path in sessions.iterdir()} == {ROOT_MARKER}:
            (sessions / ROOT_MARKER).unlink(); sessions.rmdir(); data_root.rmdir()
        if prepared.directory.exists():
            prepared.cleanup()
        else:
            prepared.directory = None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("msi", "baseline", "manifest", "signature", "candidate-receipt", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    receipt = qualify(args)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
