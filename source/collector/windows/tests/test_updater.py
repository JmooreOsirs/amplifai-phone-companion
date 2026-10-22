"""Real Ed25519 verifier tests; network is replaced only at its boundary."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import queue
import tempfile
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from session import Session
from installer_guard import InstallerGuardError, InstallerReadGuard
from updater import (BUILD, CHANNEL, FEED_URL, INSTALLER_VERSION, MAX_INSTALLER_BYTES, VERSION,
                     PreparedUpdate, RestrictedRedirects, UpdateError, _read_url, cleanup_owned_updates, parse_manifest, verify_installer)

NEXT_BUILD = str(int(BUILD) + 1)
NEXT_INSTALLER_VERSION = ".".join((*INSTALLER_VERSION.split(".")[:2], str(int(INSTALLER_VERSION.split(".")[2]) + 1)))
NEXT_VERSION = VERSION + "-next"


class SignedUpdateTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.public = base64.b64encode(self.key.public_key().public_bytes_raw()).decode()
        self.package = b"synthetic-msi-update-bytes"
        self.value = {"schema": 1, "platform": "windows", "architecture": "x86_64", "channel": CHANNEL,
                      "version": NEXT_VERSION, "build": NEXT_BUILD, "installerVersion": NEXT_INSTALLER_VERSION,
                      "installerUrl": "https://github.com/JmooreOsirs/amplifai-phone-companion/releases/download/windows-test-fixture/AmplifaiPhone-fixture-unsigned.msi",
                      "installerBytes": len(self.package), "installerSha256": hashlib.sha256(self.package).hexdigest(),
                      "installerEdSignature": base64.b64encode(self.key.sign(self.package)).decode(), "installerTrust": "unsigned-test"}

    def signed(self, data=None):
        data = json.dumps(self.value, separators=(",", ":")).encode() if data is None else data
        return data, base64.b64encode(self.key.sign(data)) + b"\n"

    def manifest(self):
        return parse_manifest(*self.signed(), public_key=self.public)

    def test_real_signature_exact_bytes_and_installer_bindings(self):
        manifest = self.manifest()
        verify_installer(self.package, manifest, self.public)
        data, signature = self.signed()
        for altered in (data + b" ", data[:-1] + b"X"):
            with self.subTest(altered=altered[-4:]), self.assertRaises(UpdateError):
                parse_manifest(altered, signature, public_key=self.public)
        with self.assertRaises(UpdateError):
            parse_manifest(data, signature)
        for package in (self.package[:-1], self.package + b"X", b"X" + self.package[1:]):
            with self.subTest(length=len(package)), self.assertRaises(UpdateError):
                verify_installer(package, manifest, self.public)
        self.value["installerEdSignature"] = base64.b64encode(self.key.sign(b"different bytes")).decode()
        with self.assertRaises(UpdateError):
            verify_installer(self.package, self.manifest(), self.public)

    def test_unsigned_json_is_never_parsed(self):
        with self.assertRaisesRegex(UpdateError, "signature"):
            parse_manifest(b"this is not json", b"", public_key=self.public)
        with self.assertRaisesRegex(UpdateError, "signature"):
            parse_manifest(b"this is not json", b"A" * 88, public_key=self.public)
        with self.assertRaisesRegex(UpdateError, "JSON"):
            parse_manifest(*self.signed(b"not json"), public_key=self.public)

    def test_signed_wrong_channel_cpu_downgrade_and_malformed_fields_fail_closed(self):
        changes = {"schema": [True, 2], "platform": ["macos"], "architecture": ["arm64"], "channel": ["windows-x64"],
                   "version": ["x" * 97, "bad\nlabel"], "build": [int(NEXT_BUILD), str(int(BUILD) - 1), "-1", "1" * 13],
                   "installerVersion": ["1.0.17", "256.0.19", "1.0.65536", "1.0"],
                   "installerBytes": [True, 0, MAX_INSTALLER_BYTES + 1], "installerSha256": ["A" * 64, "z" * 64],
                   "installerEdSignature": ["", "A" * 88], "installerTrust": ["trusted", [], None],
                   "installerUrl": ["http://github.com/JmooreOsirs/amplifai-phone-companion/releases/download/rc19/app.msi",
                                    "https://github.com/other/repo/releases/download/rc19/app.msi",
                                    "https://github.com/JmooreOsirs/amplifai-phone-companion/releases/latest/download/app.msi",
                                    "https://github.com/JmooreOsirs/amplifai-phone-companion/releases/download/latest/app.msi",
                                    self.value["installerUrl"] + "?replace=1", self.value["installerUrl"] + "#fragment"]}
        for field, bad_values in changes.items():
            original = self.value[field]
            for value in bad_values:
                self.value[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(UpdateError):
                    self.manifest()
            self.value[field] = original
        self.value["newField"] = "not allowed"
        with self.assertRaises(UpdateError): self.manifest()
        del self.value["newField"]
        del self.value["installerTrust"]
        with self.assertRaises(UpdateError): self.manifest()

    def test_duplicate_fields_and_inconsistent_build_versions_fail(self):
        data = json.dumps(self.value).encode()[:-1] + b',"build":"26101520"}'
        with self.assertRaisesRegex(UpdateError, "duplicate"):
            parse_manifest(*self.signed(data), public_key=self.public)
        self.value["build"] = BUILD
        with self.assertRaisesRegex(UpdateError, "advance together"): self.manifest()
        self.value["installerVersion"] = INSTALLER_VERSION
        self.assertEqual(self.manifest().build, BUILD)

    def test_redirects_are_https_and_host_restricted_and_feed_never_redirects(self):
        import urllib.request
        request = urllib.request.Request(self.value["installerUrl"])
        for url in ("http://release-assets.githubusercontent.com/file", "https://evil.test/file",
                    "https://github.com.evil.test/file", "https://user@release-assets.githubusercontent.com/file",
                    "https://release-assets.githubusercontent.com:444/file", "https://github.com/other/repo/file"):
            with self.subTest(url=url), self.assertRaises(UpdateError):
                RestrictedRedirects(assets=True).redirect_request(request, None, 302, "Found", {}, url)
        allowed = "https://release-assets.githubusercontent.com/github-production-release-asset/file?signature=value"
        self.assertEqual(RestrictedRedirects(assets=True).redirect_request(request, None, 302, "Found", {}, allowed).full_url, allowed)
        with self.assertRaises(UpdateError):
            RestrictedRedirects(assets=False).redirect_request(request, None, 302, "Found", {}, FEED_URL)

    def test_download_limit_truncation_and_cancel_fail(self):
        class Response(io.BytesIO):
            status = 200
            headers = {}
        for data, headers in ((b"12345", {}), (b"12", {"Content-Length": "4"}), (b"1234", {"Content-Length": "500"}),
                              (b"1234", {"Content-Encoding": "gzip"})):
            response = Response(data); response.headers = headers
            opener = Mock(); opener.open.return_value = response
            with patch("updater.urllib.request.build_opener", return_value=opener), self.assertRaises(UpdateError):
                _read_url(FEED_URL, 4)
        cancelled = threading.Event(); cancelled.set()
        opener = Mock(); opener.open.return_value = Response(b"1234")
        with patch("updater.urllib.request.build_opener", return_value=opener), self.assertRaises(UpdateError):
            _read_url(FEED_URL, 4, cancelled=cancelled)

    def test_prepared_update_preserves_signed_manifest_and_refuses_local_tampering(self):
        manifest = self.manifest()
        with patch("updater._read_url", return_value=self.package):
            prepared = PreparedUpdate(manifest, public_key=self.public)
            self.addCleanup(prepared.cleanup)
            prepared.verify_local()
            self.assertEqual((prepared.directory / "manifest.json").read_bytes(), manifest.signed_bytes)
            self.assertEqual((prepared.directory / "manifest.sig").read_bytes(), manifest.signed_signature)
            prepared.path.write_bytes(b"X" + self.package[1:])
            with self.assertRaises(UpdateError): prepared.verify_local()


    def test_install_refuses_file_replaced_after_download(self):
        manifest = self.manifest()
        with patch("updater._read_url", return_value=self.package):
            prepared = PreparedUpdate(manifest, public_key=self.public)
            self.addCleanup(prepared.cleanup)
            prepared.path.unlink()
            target = prepared.directory / "changed.msi"
            target.write_bytes(self.package)
            prepared.path.symlink_to(target)
            with self.assertRaises(UpdateError):
                prepared.verify_local()

    def test_owned_download_cleanup_preserves_active_installers_and_unmarked_directories(self):
        with tempfile.TemporaryDirectory() as root:
            manifest = self.manifest()
            with patch("updater._read_url", return_value=self.package), patch("updater.tempfile.tempdir", root):
                prepared = PreparedUpdate(manifest, public_key=self.public)
            unmarked = Path(root) / "AmplifaiPhone-update-abcdefgh"
            unmarked.mkdir(); (unmarked / "owner-file.txt").write_text("preserve")
            with patch("updater.os.name", "nt"), patch("updater.Path", type(prepared.path)), \
                    patch("updater.tempfile.gettempdir", return_value=root), patch("updater.windows_process_alive", return_value=False):
                prepared._write_marker(installing=True, installer_pid=None)
                self.assertEqual(cleanup_owned_updates(public_key=self.public), 0)
                prepared._write_marker(installing=True, installer_pid=5678)
                with patch("updater.windows_process_alive", side_effect=lambda pid: pid == 5678):
                    self.assertEqual(cleanup_owned_updates(public_key=self.public), 0)
                self.assertEqual(cleanup_owned_updates(public_key=self.public), 1)
                self.assertTrue((unmarked / "owner-file.txt").is_file())
            prepared.directory = None

    def test_pre_ready_bootstrap_failures_close_cache_and_runtime_guards(self):
        import types
        import update_installer
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source-helper"; source.mkdir()
            (source / "_internal").mkdir()
            (source / "AmplifaiPhoneHelper.exe").write_bytes(b"helper")
            (source / "_internal/runtime.dll").write_bytes(b"runtime")
            records = [{"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                       for name, data in (("AmplifaiPhoneHelper.exe", b"helper"), ("_internal/runtime.dll", b"runtime"))]
            for failure in ("copy", "inventory", "copied_guard", "spawn"):
                with self.subTest(failure=failure), patch("updater._read_url", return_value=self.package):
                    prepared = PreparedUpdate(self.manifest(), public_key=self.public)
                    cache, original, copied = Mock(), Mock(), Mock()
                    cache.read.return_value = self.package
                    copied.open.side_effect = OSError("native guard refused") if failure == "copied_guard" else None
                    with patch.dict(sys.modules, {"_windows_update_bootstrap": types.SimpleNamespace(HELPER_RECORDS=records)}), \
                            patch.object(update_installer, "require_frozen_windows"), \
                            patch.object(update_installer, "InstallerReadGuard", return_value=cache), \
                            patch.object(update_installer, "RuntimeReadGuard", side_effect=[original, copied]), \
                            patch.object(update_installer.shutil, "copytree", side_effect=OSError("copy refused") if failure == "copy" else None), \
                            patch.object(type(prepared.directory), "write_bytes", side_effect=OSError("inventory refused") if failure == "inventory" else lambda path, data: len(data), autospec=True), \
                            patch.object(update_installer.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
                            patch.object(update_installer.subprocess, "Popen", side_effect=OSError("spawn refused")) as spawn:
                        with self.assertRaises(UpdateError):
                            update_installer.start_handoff(prepared, source / "AmplifaiPhoneHelper.exe")
                        cache.close.assert_called_once()
                        original.close.assert_called_once()
                        if failure == "spawn":
                            copied.close.assert_called_once()
                            spawn.assert_called_once()
                        else:
                            spawn.assert_not_called()
                    prepared.cleanup()

    def test_native_guard_open_failure_releases_parent_handle(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root); path = directory / "verified.msi"; path.write_bytes(self.package)
            guard = InstallerReadGuard(directory, path)
            native = Mock(); native.CreateFileW.side_effect = [123, None]
            with patch.dict(sys.modules, {"msvcrt": Mock()}), patch("installer_guard.ctypes.WinDLL", return_value=native, create=True):
                with self.assertRaises(InstallerGuardError): guard.open()
            native.CloseHandle.assert_called_once_with(123)
            self.assertIsNone(guard.directory_handle)

    @unittest.skipUnless(os.name == "nt", "Real immutable MSI handle requires Windows")
    def test_real_windows_read_guard_refuses_edit_and_replacement_until_closed(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root); path = directory / "verified.msi"
            path.write_bytes(self.package)
            guard = InstallerReadGuard(directory, path)
            guard.open()
            try:
                self.assertEqual(guard.read(len(self.package) + 1), self.package)
                with self.assertRaises(OSError): path.write_bytes(b"changed")
                with self.assertRaises(OSError): path.unlink()
                replacement = directory / "replacement.msi"; replacement.write_bytes(b"changed")
                with self.assertRaises(OSError): os.replace(replacement, path)
                moved = directory.with_name(directory.name + "-replacement")
                with self.assertRaises(OSError): os.replace(directory, moved)
            finally:
                guard.close()
            path.write_bytes(b"changed")
            self.assertEqual(path.read_bytes(), b"changed")

class UpdateSafetyTests(unittest.TestCase):
    def idle(self):
        state = Session(); state.begin("inspect"); state.handle({"kind": "residue", "sessions": []}); state.exited(0)
        return state

    def test_idle_gate_requires_clean_inspection_and_preserves_unsaved_save_pairing(self):
        self.assertFalse(Session().can_update)
        state = self.idle(); self.assertTrue(state.can_update)
        for field, value in (("running", True), ("needs_inspection", True), ("inspected", False),
                             ("residue", [{"name": "session-" + "a" * 32, "bytes": 0}]),
                             ("save_receipt_pending", True), ("_pairing", True), ("_polling", True),
                             ("_finishing", True), ("_selection_change_pending", True), ("_capture_received", True)):
            original = getattr(state, field); setattr(state, field, value)
            with self.subTest(field=field): self.assertFalse(state.can_update)
            setattr(state, field, original)
        state._capture_received = True; state.saved_acknowledged = True
        self.assertTrue(state.can_update)
        state.saved_acknowledged = False; state.review_discarded = True
        self.assertTrue(state.can_update)
        state.save_receipt_pending = True
        self.assertFalse(state.can_update)

    def test_install_rechecks_live_gate_after_confirmation_and_never_discards_review(self):
        from test_gui_guards import source_window
        window = source_window(); window.state = self.idle()
        window.prepared_update = Mock(); window.prepared_update.manifest.trust = "unsigned-test"
        window.prepared_update.manifest.version = NEXT_VERSION
        window.status_owned = True; window.update_state = "available"; window.update_message = Mock()
        dialog = window.install_update.__globals__["messagebox"]
        def start_collection(*_args):
            window.state.approve(True); window.state.begin("connect")
            return True
        dialog.askyesno.side_effect = start_collection
        window.install_update()
        window.prepared_update.launch.assert_not_called()
        self.assertTrue(window.state.running)
        window.helper.close_input.assert_not_called()

    def test_ready_handoff_closes_normally_after_commit_and_transfers_cache(self):
        from test_gui_guards import source_window
        window = source_window(); window.state = self.idle()
        window.status_owned = True; window.update_state = "installing"
        window.handoff_preparing = True; window._startup_update_pending = False
        window.update_events = queue.Queue()
        window.update_lock = threading.Lock(); window.update_cancelled = threading.Event()
        window.status_server = Mock(); window.prepared_update = Mock()
        prepared, handoff = window.prepared_update, Mock()
        window.update_events.put({"kind": "handoff_ready", "handoff": handoff, "prepared": prepared})
        self.assertTrue(window.pump_updates())
        handoff.commit.assert_called_once(); handoff.abort.assert_not_called()
        window.status_server.stop.assert_called_once(); window.window.destroy.assert_called_once()
        prepared.cleanup.assert_not_called(); window.helper.close_input.assert_not_called()
        self.assertIsNone(window.prepared_update)

    def test_ready_handoff_preserves_review_when_live_gate_changes(self):
        from test_gui_guards import source_window
        window = source_window(); window.state = self.idle()
        window.state._capture_received = True; window.state.reviewed = True
        window.state.selected_ids = {1}
        window.status_owned = True; window.update_state = "installing"
        window.handoff_preparing = True; window._startup_update_pending = False
        window.update_events = queue.Queue(); window.update_lock = threading.Lock()
        window.prepared_update = Mock(); window.update_message = Mock()
        handoff = Mock()
        window.update_events.put({"kind": "handoff_ready", "handoff": handoff, "prepared": window.prepared_update})
        self.assertFalse(window.pump_updates())
        handoff.abort.assert_called_once(); handoff.commit.assert_not_called()
        window.window.destroy.assert_not_called(); window.helper.close_input.assert_not_called()
        self.assertEqual(window.state.selected_ids, {1}); self.assertTrue(window.state.reviewed)

    def test_status_conflict_blocks_collection_without_taking_over(self):
        from test_gui_guards import source_window
        window = source_window(); window.state = self.idle(); window.status_owned = False
        window.start("connect")
        window.helper.start.assert_not_called()
        self.assertFalse(window.state.running)


if __name__ == "__main__":
    unittest.main()
