"""Exercise the detached installer's trust and shutdown boundaries with real signatures."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import update_installer
from installer_guard import InstallerGuardError
from updater import BUILD, CHANNEL, INSTALLER_VERSION, VERSION, UpdateError


PREPARE = '{"schema":1,"action":"prepare"}\n'
COMMIT = '{"schema":1,"action":"commit"}\n'
READY = '{"schema":1,"event":"update_ready"}\n'


class UpdateHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "AmplifaiPhone-update-abcdef12"
        self.root.mkdir()
        self.bootstrap = self.root / "bootstrap"
        self.bootstrap.mkdir()
        (self.bootstrap / "AmplifaiPhoneHelper.exe").write_bytes(b"frozen-helper-fixture")
        (self.bootstrap / "_internal").mkdir()
        (self.bootstrap / "_internal" / "python313.dll").write_bytes(b"runtime-fixture")
        self.inventory = [
            {"path": path.relative_to(self.bootstrap).as_posix(), "bytes": path.stat().st_size,
             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted(self.bootstrap.rglob("*")) if path.is_file()
        ]
        inventory_bytes = json.dumps(self.inventory, separators=(",", ":")).encode()
        (self.root / "bootstrap-inventory.json").write_bytes(inventory_bytes)
        self.marker = {
            "schema": 2, "owner": "amplifai-phone-windows-update-v2", "directory": self.root.name,
            "ownerPid": 987654, "helperPid": os.getpid(), "installing": True, "installerPid": None,
            "bootstrapSha256": hashlib.sha256(inventory_bytes).hexdigest(),
        }
        self.write_marker()
        self.key = Ed25519PrivateKey.generate()
        self.public_key = base64.b64encode(self.key.public_key().public_bytes_raw()).decode()
        self.package = b"synthetic-signed-msi-handoff-fixture"
        self.msi = self.root / "AmplifaiPhone-verified.msi"
        self.msi.write_bytes(self.package)
        self.manifest = {
            "schema": 1, "platform": "windows", "architecture": "x86_64", "channel": CHANNEL,
            "version": VERSION, "build": BUILD, "installerVersion": INSTALLER_VERSION,
            "installerUrl": "https://github.com/JmooreOsirs/amplifai-phone-companion/releases/download/windows-test-fixture/AmplifaiPhone-fixture-unsigned.msi",
            "installerBytes": len(self.package), "installerSha256": hashlib.sha256(self.package).hexdigest(),
            "installerEdSignature": base64.b64encode(self.key.sign(self.package)).decode(),
            "installerTrust": "unsigned-test",
        }
        self.sign_manifest()
        self.system_installer = Path(self.temporary.name) / "msiexec.exe"
        self.system_installer.write_bytes(b"system-installer-fixture")
        self.events = []
        self.output = self.ReadyOutput()
        self.msi_guard = self.Guard(self.root, self.msi, self.events, "msi")
        self.runtime_guard = update_installer.RuntimeReadGuard(self.bootstrap, self.inventory)
        self.runtime_file_guards = []
        self.guard_factory = Mock(return_value=self.msi_guard)
        self.runtime_factory = Mock(return_value=self.runtime_guard)
        self.installer_factory = Mock(side_effect=self.launch_installer)

    class ReadyOutput(io.StringIO):
        def __init__(self):
            super().__init__()
            self.flushed = False

        def flush(self):
            self.flushed = True
            super().flush()

    class Guard:
        def __init__(self, root, path, events, label):
            self.root, self.path, self.events, self.label = root, path, events, label
            self.opened = False

        def open(self):
            self.opened = True
            self.events.append(self.label + "-open")

        def read(self, limit):
            if not self.opened:
                raise AssertionError("installer bytes read without an open guard")
            return self.path.read_bytes()[:limit]

        def close(self):
            self.opened = False
            self.events.append(self.label + "-close")

    def write_marker(self):
        (self.root / "ownership.json").write_text(json.dumps(self.marker), encoding="utf-8")

    def sign_manifest(self):
        data = json.dumps(self.manifest, separators=(",", ":")).encode()
        (self.root / "manifest.json").write_bytes(data)
        (self.root / "manifest.sig").write_bytes(base64.b64encode(self.key.sign(data)) + b"\n")

    def launch_installer(self, *args, **kwargs):
        self.events.append("installer-launch")
        self.assertTrue(self.msi_guard.opened)
        self.assertTrue(self.runtime_file_guards)
        self.assertTrue(all(guard.opened for guard in self.runtime_file_guards))
        process = Mock(pid=456789)

        def wait():
            self.events.append("installer-wait")
            self.assertTrue(self.msi_guard.opened, "MSI guard was released before installer exit")
            self.assertTrue(all(guard.opened for guard in self.runtime_file_guards),
                            "bootstrap guard was released before installer exit")
            return 0

        process.wait.side_effect = wait
        return process

    def runtime_file_guard(self, root, path):
        guard = self.Guard(root, path, self.events, "runtime")
        self.runtime_file_guards.append(guard)
        return guard

    def run_installer(self, commands=PREPARE + COMMIT, *, parent_alive=None, input_stream=None):
        with patch.object(update_installer, "RuntimeReadGuard", self.runtime_factory), \
                patch.object(update_installer, "InstallerReadGuard", side_effect=self.runtime_file_guard), \
                patch.object(update_installer, "windows_installer_path", return_value=self.system_installer), \
                patch("updater.tempfile.gettempdir", return_value=self.temporary.name):
            return update_installer.run_installer(
                self.root, input_stream=input_stream or io.StringIO(commands), output_stream=self.output,
                public_key=self.public_key, parent_alive=parent_alive or (lambda _pid: False),
                installer_factory=self.installer_factory, guard_factory=self.guard_factory,
            )

    def assert_refused(self, *args, before_ready=False, **kwargs):
        with self.assertRaises((UpdateError, InstallerGuardError, ValueError)):
            self.run_installer(*args, **kwargs)
        self.installer_factory.assert_not_called()
        if before_ready:
            self.assertEqual(self.output.getvalue(), "")
        self.assertFalse(self.msi_guard.opened)
        self.assertTrue(all(not guard.opened for guard in self.runtime_file_guards))

    def test_ready_commit_parent_exit_and_guarded_normal_installer_handoff(self):
        class Commands(io.StringIO):
            def readline(stream, *args):
                if stream.tell() == len(PREPARE):
                    self.assertEqual(self.output.getvalue(), READY)
                    self.assertTrue(self.output.flushed, "readiness must reach the parent before waiting for commit")
                return super().readline(*args)

        observations = []

        def parent_alive(pid):
            self.assertEqual(pid, self.marker["ownerPid"])
            observations.append(pid)
            alive = len(observations) == 1
            if not alive:
                self.events.append("parent-exited")
            return alive

        self.assertEqual(self.run_installer(input_stream=Commands(PREPARE + COMMIT), parent_alive=parent_alive), 0)
        self.assertEqual(self.output.getvalue(), READY)
        self.assertGreaterEqual(len(observations), 2)
        self.assertLess(self.events.index("parent-exited"), self.events.index("installer-launch"))
        self.installer_factory.assert_called_once()
        self.assertEqual(self.installer_factory.call_args.args, ([str(self.system_installer), "/i", str(self.msi)],))
        self.assertIs(self.installer_factory.call_args.kwargs["shell"], False)
        for stream in ("stdin", "stdout", "stderr"):
            self.assertEqual(self.installer_factory.call_args.kwargs[stream], subprocess.DEVNULL)
        self.guard_factory.assert_called_once_with(self.root, self.msi)
        self.assertLess(self.events.index("installer-wait"), self.events.index("msi-close"))
        self.assertFalse(self.msi_guard.opened)
        self.assertTrue(all(not guard.opened for guard in self.runtime_file_guards))

    def test_commit_requires_explicit_complete_command_and_eof(self):
        for commands in ("", PREPARE, PREPARE + COMMIT.rstrip("\n"), PREPARE + COMMIT + PREPARE):
            with self.subTest(commands=commands):
                self.output.seek(0); self.output.truncate()
                self.assert_refused(commands)

    def test_ownership_receipt_failure_after_launch_still_holds_guard_until_exit(self):
        write_marker = update_installer._write_marker

        def fail_after_launch(root, marker):
            if self.installer_factory.called:
                raise OSError("fixture ownership receipt failure")
            return write_marker(root, marker)

        with patch.object(update_installer, "_write_marker", side_effect=fail_after_launch):
            try:
                self.run_installer()
            except (OSError, UpdateError):
                pass
        self.installer_factory.assert_called_once()
        self.assertIn("installer-wait", self.events, "running installer must exit before releasing guards")
        self.assertLess(self.events.index("installer-wait"), self.events.index("msi-close"))
        self.assertFalse(self.msi_guard.opened)
        self.assertTrue(all(not guard.opened for guard in self.runtime_file_guards))

    def test_manifest_exact_bytes_channel_and_cpu_are_checked_before_ready(self):
        data = (self.root / "manifest.json").read_bytes()
        (self.root / "manifest.json").write_bytes(data + b" ")
        self.assert_refused(before_ready=True)
        for field, value in (("channel", "windows-x64"), ("architecture", "arm64")):
            with self.subTest(field=field):
                original = self.manifest[field]
                self.manifest[field] = value
                self.sign_manifest()
                self.assert_refused(before_ready=True)
                self.manifest[field] = original

    def test_installer_is_independently_verified_before_ready(self):
        self.msi.write_bytes(b"X" + self.package[1:])
        self.assert_refused(before_ready=True)
        self.msi.write_bytes(self.package)
        self.manifest["installerEdSignature"] = base64.b64encode(self.key.sign(b"different installer")).decode()
        self.sign_manifest()
        self.assert_refused(before_ready=True)

    def test_bootstrap_inventory_rejects_changed_missing_and_unlisted_runtime(self):
        runtime = self.bootstrap / "_internal" / "python313.dll"
        original = runtime.read_bytes()
        for change in ("modified", "missing", "unlisted", "inventory"):
            with self.subTest(change=change):
                if change == "modified":
                    runtime.write_bytes(b"X" + original[1:])
                elif change == "missing":
                    runtime.unlink()
                elif change == "unlisted":
                    (self.bootstrap / "unexpected.dll").write_bytes(b"unlisted runtime")
                else:
                    with (self.root / "bootstrap-inventory.json").open("ab") as stream:
                        stream.write(b" ")
                self.assert_refused(before_ready=True)
                runtime.write_bytes(original)
                (self.bootstrap / "unexpected.dll").unlink(missing_ok=True)

    def test_read_guard_failure_never_readies_or_launches_installer(self):
        with patch.object(self.msi_guard, "open", side_effect=InstallerGuardError("fixture guard refusal")):
            self.assert_refused(before_ready=True)

    def test_parent_still_running_after_deadline_never_launches_installer(self):
        with patch.object(update_installer, "PARENT_EXIT_SECONDS", 0):
            self.assert_refused(parent_alive=lambda _pid: True)
        self.assertEqual(self.output.getvalue(), READY)


if __name__ == "__main__":
    unittest.main()
