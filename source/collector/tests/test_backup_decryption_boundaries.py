"""Focused repair acceptance with fully fictional encrypted backup fixtures."""

import io
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amplifai_phone.agent import CaptureCancelled
from amplifai_phone.backup_files import copy_backup_file
from amplifai_phone.metadata import SelectedPayloadIntegrityError
from amplifai_phone.workspace import WorkspaceError
from cryptography.hazmat.primitives.keywrap import aes_key_wrap
from pyiosbackup.keybag import Keybag
from synthetic_encrypted_backup_fixture import FILE_KEY, WRAPPING, encrypt, probe
from windows.session import Session


class PasswordRepair(unittest.TestCase):
    def test_correct_password_reads_all_sources(self):
        result = probe()
        self.assertEqual(result["exit"], 0, result)
        self.assertEqual(
            result["capture"],
            {
                "totalContacts": 1,
                "availableCalls": 1,
                "availableMessages": 1,
                "missing": [],
                "unavailableReasons": {},
            },
        )

    def test_wrong_password_or_bad_keybag_still_fail_closed_with_stage(self):
        for variant in ["wrong_password", "keybag_corrupt"]:
            with self.subTest(variant=variant):
                r = probe(
                    variant,
                    entered="Wrong synthetic password"
                    if variant == "wrong_password"
                    else None,
                )
                self.assertEqual(
                    r["error"],
                    {"kind": "error", "code": "backup_password", "stage": "processing"},
                )
                self.assertIsNone(r["capture"])

    def test_manifest_key_failure_is_fatal_control_error(self):
        for variant in [
            "manifest_key",
            "manifest_short_key",
            "manifest_missing_class",
            "manifest_truncated_cipher",
        ]:
            with self.subTest(variant=variant):
                r = probe(variant)
                self.assertEqual(
                    r["error"],
                    {
                        "kind": "error",
                        "code": "backup_control_invalid",
                        "stage": "processing",
                    },
                )
                self.assertIsNone(r["capture"])

    def test_bad_contacts_key_still_fails_closed(self):
        for variant in [
            "contacts_key",
            "contacts_short_key",
            "contacts_missing_class",
            "contacts_wal_key",
            "contacts_shm_key",
        ]:
            with self.subTest(variant=variant):
                r = probe(variant)
                self.assertEqual(
                    r["error"],
                    {
                        "kind": "error",
                        "code": "selected_payload_crypto",
                        "stage": "processing",
                    },
                )
                self.assertIsNone(r["capture"])

    def test_optional_key_failure_keeps_other_sources_with_explicit_unavailable(self):
        for source in ["calls", "messages"]:
            for failure in [
                "key",
                "short_key",
                "missing_class",
                "padding",
                "wal_key",
                "shm_key",
            ]:
                with self.subTest(source=source, failure=failure):
                    r = probe(f"{source}_{failure}")
                    self.assertEqual(r["exit"], 0, r)
                    capture = r["capture"]
                    self.assertEqual(capture["totalContacts"], 1)
                    self.assertEqual(capture["missing"], [source])
                    self.assertEqual(
                        capture["unavailableReasons"],
                        {source: "selected_payload_crypto"},
                    )
                    self.assertEqual(
                        capture[
                            "availableMessages"
                            if source == "calls"
                            else "availableCalls"
                        ],
                        1,
                    )
                    self.assertTrue(
                        any(
                            d["source"] == source
                            and d["reason"] == "selected_payload_crypto"
                            for d in r["diagnostics"]
                        )
                    )

    def test_both_optional_keys_unavailable_is_explicit_not_a_zero_history(self):
        r = probe("both_optional_keys")
        self.assertEqual(r["exit"], 0, r)
        self.assertEqual(r["capture"]["totalContacts"], 1)
        self.assertEqual(r["capture"]["missing"], ["calls", "messages"])
        self.assertEqual(
            r["capture"]["unavailableReasons"],
            {"calls": "selected_payload_crypto", "messages": "selected_payload_crypto"},
        )

    def test_cancellation_and_storage_errors_propagate_unchanged(self):
        for error in [
            CaptureCancelled(),
            WorkspaceError("Synthetic storage error", code="workspace_low_space"),
        ]:
            with (
                self.subTest(kind=type(error).__name__),
                tempfile.TemporaryDirectory(
                    prefix="amplifai-fictional-boundary-"
                ) as tmp,
            ):
                source = Path(tmp) / "cipher"
                source.write_bytes(encrypt(b"Fictional payload"))
                dest = Path(tmp) / "out"
                wrapped = struct.pack("<I", 1) + aes_key_wrap(WRAPPING, FILE_KEY)

                def fail():
                    raise error

                with self.assertRaises(type(error)) as caught:
                    copy_backup_file(
                        source,
                        dest,
                        keybag=Keybag({1: WRAPPING}),
                        encryption_key=wrapped,
                        padded=True,
                        bound_callback=fail,
                    )
                self.assertIs(caught.exception, error)

    def test_unicode_spaces_and_max_length_passwords_preserved(self):
        for password in [
            "café 漢字 🔐",
            "cafe\u0301",
            " leading and trailing ",
            "x" * 512,
        ]:
            with self.subTest(length=len(password)):
                self.assertEqual(probe(password=password)["exit"], 0)
        self.assertEqual(
            probe(password="café", entered="cafe\u0301")["error"]["code"],
            "backup_password",
        )

    def test_windows_session_preserves_utf8_password_exactly(self):
        for password in [
            "café 漢字 🔐",
            "cafe\u0301",
            " leading and trailing ",
            "x" * 512,
        ]:
            with self.subTest(length=len(password)):
                state = Session()
                state.begin("inspect")
                state.handle({"kind": "residue", "sessions": []})
                state.exited(0)
                state.approve(True)
                state.begin("connect")
                state.handle({"kind": "state", "state": "password_required"})
                output = io.StringIO()
                state.write_password(password, output)
                self.assertEqual(json.loads(output.getvalue())["value"], password)

    def test_malformed_key_rejected_before_output_created(self):
        for key in [b"", b"x", b"\x01\0\0\0", None]:
            with (
                self.subTest(key_type=type(key).__name__),
                tempfile.TemporaryDirectory(prefix="amplifai-fictional-key-") as tmp,
            ):
                source = Path(tmp) / "cipher"
                source.write_bytes(b"x" * 16)
                dest = Path(tmp) / "out"
                with self.assertRaises(SelectedPayloadIntegrityError) as ctx:
                    copy_backup_file(
                        source, dest, keybag=Keybag({1: WRAPPING}), encryption_key=key
                    )
                self.assertEqual(ctx.exception.code, "selected_payload_crypto")
                self.assertFalse(dest.exists())

    def test_unrelated_runtime_error_is_not_downgraded_to_optional_source(self):
        with patch(
            "cryptography.hazmat.primitives.keywrap.aes_key_unwrap",
            side_effect=RuntimeError("Synthetic sentinel"),
        ):
            r = probe()
            self.assertEqual(r["error"]["code"], "collection_failed")
            self.assertIsNone(r["capture"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
