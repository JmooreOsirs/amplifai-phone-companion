"""Same-acquisition unlock recovery, using fictional backups and real crypto."""

import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from amplifai_phone.agent import CaptureCancelled, run_connect
from amplifai_phone.ios_backup import collect_iphone, parse_selected_backup
from amplifai_phone.record_store import RecordStore
from amplifai_phone.workspace import SessionWorkspace, WorkspaceError
from synthetic_encrypted_backup_fixture import FILE_KEY, SYNTHETIC_PASSWORD, WRAPPING, acquisition, fixture


class UnlockRecoveryTests(unittest.TestCase):
    def run_acquisition(self, values, *, variant="valid", retry=True,
                        password=SYNTHETIC_PASSWORD, tail='{"action":"disconnect"}\n'):
        commands = "".join(json.dumps({"action": "password", "value": value}, ensure_ascii=False) + "\n"
                           for value in values) + tail
        source, sink = io.StringIO(commands), io.StringIO()
        with tempfile.TemporaryDirectory(prefix="amplifai-fictional-retry-") as temporary:
            root = Path(temporary) / "sessions"
            with acquisition(root, variant, password) as (collector, service), patch(
                "amplifai_phone.ios_backup.parse_selected_backup", wraps=parse_selected_backup
            ) as parse:
                options = {"retry_backup_unlock": True} if retry else {}
                status = run_connect(source, sink, collector, **options)
                service.backup.assert_awaited_once()
                paths = [call.args[0] for call in parse.call_args_list]
                self.assertLessEqual(len(set(paths)), 1)
                self.assertFalse([p for p in root.iterdir() if p.is_dir()], "Private workspaces must be cleaned")
        output = sink.getvalue()
        for value in values:
            self.assertNotIn(value, output)
        self.assertNotIn(str(root), output)
        for key in [WRAPPING.hex(), FILE_KEY.hex()]:
            self.assertNotIn(key, output)
        return status, [json.loads(line) for line in output.splitlines()], parse.call_count, source.read()

    def test_wrong_then_correct_unlocks_one_acquisition(self):
        status, events, calls, _ = self.run_acquisition(["Wrong fictional password", SYNTHETIC_PASSWORD])
        self.assertEqual(status, 0, events)
        self.assertEqual(calls, 2)
        prompts = [e for e in events if e.get("state") == "password_required"]
        self.assertEqual(prompts, [
            {"kind": "state", "state": "password_required", "attempt": 1, "maxAttempts": 3},
            {"kind": "state", "state": "password_required", "attempt": 2, "maxAttempts": 3,
             "reason": "backup_unlock_failed"},
        ])
        capture = next(e for e in events if e["kind"] == "capture")
        self.assertEqual((capture["totalContacts"], capture["availableCalls"], capture["availableMessages"]), (1, 1, 1))
        self.assertFalse(any(e["kind"] == "error" for e in events))

    def test_third_attempt_can_succeed_but_fourth_is_never_read(self):
        for values, expected in [
            (["Wrong first", "Wrong second", SYNTHETIC_PASSWORD], 0),
            (["Wrong first", "Wrong second", "Wrong third", SYNTHETIC_PASSWORD], 1),
        ]:
            with self.subTest(expected=expected):
                status, events, calls, remaining = self.run_acquisition(values)
                self.assertEqual(status, expected, events)
                self.assertEqual(calls, 3)
                self.assertEqual([e["attempt"] for e in events if e.get("state") == "password_required"], [1, 2, 3])
                if expected:
                    self.assertIn(SYNTHETIC_PASSWORD, remaining)
                    self.assertEqual(events[-1], {"kind": "error", "code": "backup_password", "stage": "processing"})

    def test_corrupt_keybag_remains_fatal_without_any_record_writes(self):
        for variant in ["keybag_corrupt", "keybag_second_class_corrupt"]:
            with self.subTest(variant=variant), patch.object(
                RecordStore, "add", side_effect=AssertionError("No selected records before unlock")
            ) as add:
                status, events, calls, _ = self.run_acquisition([SYNTHETIC_PASSWORD] * 3, variant=variant)
            self.assertEqual((status, calls), (1, 3))
            add.assert_not_called()
            self.assertFalse(any(e["kind"] in {"capture", "selected-payload-diagnostic"} for e in events))
            self.assertEqual(events[-1]["code"], "backup_password")

    def test_manifest_and_required_source_failures_never_retry(self):
        for variant, code in [("manifest_key", "backup_control_invalid"),
                              ("manifest_truncated_cipher", "backup_control_invalid"),
                              ("contacts_key", "selected_payload_crypto")]:
            with self.subTest(variant=variant):
                status, events, calls, remaining = self.run_acquisition([SYNTHETIC_PASSWORD] * 2, variant=variant)
                self.assertEqual((status, calls), (1, 1))
                self.assertEqual(events[-1]["code"], code)
                self.assertIn(SYNTHETIC_PASSWORD, remaining)
                self.assertFalse(any(e["kind"] == "capture" for e in events))

    def test_optional_source_crypto_remains_explicit_after_successful_retry(self):
        status, events, calls, _ = self.run_acquisition(
            ["Wrong fictional password", SYNTHETIC_PASSWORD], variant="calls_key")
        self.assertEqual((status, calls), (0, 2))
        capture = next(e for e in events if e["kind"] == "capture")
        self.assertEqual((capture["totalContacts"], capture["availableCalls"], capture["availableMessages"]), (1, 0, 1))
        self.assertEqual(capture["missing"], ["calls"])
        self.assertEqual(capture["unavailableReasons"], {"calls": "selected_payload_crypto"})

    def test_cancel_or_eof_at_retry_cleans_same_acquisition(self):
        for tail in ["", '{"action":"disconnect"}\n']:
            with self.subTest(tail=tail):
                status, events, calls, _ = self.run_acquisition(["Wrong fictional password"], tail=tail)
                self.assertEqual((status, calls), (130, 1))
                self.assertEqual(events[-1], {"kind": "state", "state": "cancelled"})
                self.assertEqual(len([e for e in events if e.get("state") == "password_required"]), 2)

    def test_legacy_default_keeps_single_prompt_and_attempt(self):
        status, events, calls, remaining = self.run_acquisition(["Wrong fictional password", SYNTHETIC_PASSWORD], retry=False)
        self.assertEqual((status, calls), (1, 1))
        self.assertEqual([e for e in events if e.get("state") == "password_required"],
                         [{"kind": "state", "state": "password_required"}])
        self.assertIn(SYNTHETIC_PASSWORD, remaining)

    def test_retry_preserves_unicode_spaces_and_maximum_length(self):
        for password in [" café 漢字 🔐 ", "cafe\u0301", "x" * 512]:
            with self.subTest(length=len(password)):
                status, events, calls, _ = self.run_acquisition(["Wrong fictional password", password], password=password)
                self.assertEqual((status, calls), (0, 2), events)

    def test_prompt_failure_never_parses_and_cleans_acquisition(self):
        for failure in [CaptureCancelled(), WorkspaceError("Synthetic storage failure", code="workspace_low_space")]:
            with self.subTest(kind=type(failure).__name__), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "sessions"
                with acquisition(root) as (_collector, service), patch(
                    "amplifai_phone.ios_backup.parse_selected_backup", wraps=parse_selected_backup
                ) as parse:
                    def provider():
                        raise failure
                    with self.assertRaises(type(failure)):
                        asyncio.run(collect_iphone(password_provider=provider, retry_backup_unlock=True, sessions_root=root))
                    parse.assert_not_called()
                    service.backup.assert_awaited_once()
                    self.assertFalse([p for p in root.iterdir() if p.is_dir()])

    def test_storage_or_cancellation_after_prompt_prevents_crypto(self):
        for failure in [CaptureCancelled(), WorkspaceError("Synthetic storage failure", code="workspace_low_space")]:
            with self.subTest(kind=type(failure).__name__), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "sessions"
                prompted = False
                original = SessionWorkspace.check_bound
                def bound(workspace, *args, **kwargs):
                    if prompted:
                        raise failure
                    return original(workspace, *args, **kwargs)
                def provider():
                    nonlocal prompted
                    prompted = True
                    return SYNTHETIC_PASSWORD
                with acquisition(root), patch.object(SessionWorkspace, "check_bound", new=bound), patch(
                    "amplifai_phone.ios_backup.parse_selected_backup", wraps=parse_selected_backup
                ) as parse:
                    with self.assertRaises(type(failure)):
                        asyncio.run(collect_iphone(password_provider=provider, retry_backup_unlock=True, sessions_root=root))
                    parse.assert_not_called()
                    self.assertFalse([p for p in root.iterdir() if p.is_dir()])

    def test_raw_unwrap_outside_keybag_is_never_retried(self):
        from cryptography.hazmat.primitives.keywrap import InvalidUnwrap
        with patch("amplifai_phone.ios_backup._parse_entries", side_effect=InvalidUnwrap("Synthetic sentinel")):
            status, events, calls, remaining = self.run_acquisition([SYNTHETIC_PASSWORD] * 2)
        self.assertEqual((status, calls), (1, 1))
        self.assertEqual(events[-1]["code"], "backup_password")
        self.assertIn(SYNTHETIC_PASSWORD, remaining)

    def test_crypto_boundary_checks_cancellation_before_and_after_unlock(self):
        from pyiosbackup.keybag import Keybag
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "backup"
            fixture(root, SYNTHETIC_PASSWORD, "valid")
            with patch.object(Keybag, "from_manifest") as unlock:
                with self.assertRaises(CaptureCancelled):
                    parse_selected_backup(root, SYNTHETIC_PASSWORD,
                                          bound_callback=MagicMock(side_effect=CaptureCancelled()))
                unlock.assert_not_called()
        for entered in [SYNTHETIC_PASSWORD, "Wrong fictional password"]:
            with self.subTest(entered=entered), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "backup"
                fixture(root, SYNTHETIC_PASSWORD, "valid")
                completed = False
                original = Keybag.from_manifest
                def unlock(*args):
                    nonlocal completed
                    try:
                        return original(*args)
                    finally:
                        completed = True
                def bound():
                    if completed:
                        raise CaptureCancelled()
                with patch.object(Keybag, "from_manifest", side_effect=unlock), self.assertRaises(CaptureCancelled):
                    parse_selected_backup(root, entered, bound_callback=bound)
                self.assertFalse(list(Path(temporary).glob("amplifai-db-*")))


if __name__ == "__main__":
    unittest.main()
