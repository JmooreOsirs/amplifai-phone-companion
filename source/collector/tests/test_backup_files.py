from __future__ import annotations

import hashlib
import io
import json
import plistlib
import sqlite3
import struct
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from amplifai_phone.backup_files import copy_backup_file
from amplifai_phone.backup_watchdog import BackupWatchdog
from amplifai_phone.ios_backup import DATABASES, _extract_database
from amplifai_phone.workspace import MIN_FREE_BYTES, SessionWorkspace, WorkspaceError
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.keywrap import aes_key_wrap
from packaging.version import Version
from pyiosbackup.entry import Entry
from pyiosbackup.keybag import Keybag


class StreamedFileTest(unittest.TestCase):
    def test_encrypted_copy_matches_pinned_entry_bytes_across_chunk_and_padding_boundaries(
        self,
    ) -> None:
        wrapping, key = bytes(range(32)), bytes(reversed(range(32)))
        wrapped = struct.pack("<I", 1) + aes_key_wrap(wrapping, key)
        keybag = Keybag({1: wrapping})
        for length in (16, 128 * 1024 - 1, 128 * 1024, 3 * 128 * 1024 + 13):
            with (
                self.subTest(length=length),
                tempfile.TemporaryDirectory(
                    prefix="amplifai-crypto-test-"
                ) as temporary,
            ):
                root = Path(temporary)
                plaintext = (b"synthetic-only-" * (length // 15 + 1))[:length]
                padder = padding.PKCS7(128).padder()
                padded = padder.update(plaintext) + padder.finalize()
                encryptor = Cipher(
                    algorithms.AES(key), modes.CBC(b"\x00" * 16)
                ).encryptor()
                encrypted = encryptor.update(padded) + encryptor.finalize()
                file_id = hashlib.sha1(b"synthetic-entry").hexdigest()
                path = root / file_id[:2] / file_id
                path.parent.mkdir()
                path.write_bytes(encrypted)
                backup = SimpleNamespace(
                    path=root,
                    ios_version=Version("18.0"),
                    is_encrypted=True,
                    keybag=keybag,
                )
                now = datetime.now(UTC)
                entry = Entry(
                    backup,
                    file_id,
                    "HomeDomain",
                    DATABASES["messages"],
                    now,
                    now,
                    now,
                    len(plaintext),
                    0o100600,
                    0,
                    0,
                    wrapped,
                )
                expected = entry.read_bytes()  # real pinned SDK reference, not a mock
                counts = []
                destination = root / "copy"
                copied = copy_backup_file(
                    path,
                    destination,
                    keybag=keybag,
                    encryption_key=wrapped,
                    padded=True,
                    progress_callback=counts.append,
                )
                self.assertEqual(destination.read_bytes(), expected)
                self.assertEqual(copied, len(expected))
                self.assertLessEqual(max(counts), 128 * 1024)
                self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_encrypted_manifest_matches_sdk_without_entry_unpadding(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-crypto-test-") as temporary:
            root = Path(temporary)
            source = root / "manifest"
            with sqlite3.connect(source) as db:
                db.execute("CREATE TABLE Files (fileID TEXT)")
            plaintext = source.read_bytes()
            wrapping, key = b"w" * 32, b"k" * 32
            wrapped = struct.pack("<I", 1) + aes_key_wrap(wrapping, key)
            keybag = Keybag({1: wrapping})
            encryptor = Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16)).encryptor()
            ciphertext = encryptor.update(plaintext) + encryptor.finalize()
            source.write_bytes(ciphertext)
            destination = root / "decrypted"
            copy_backup_file(source, destination, keybag=keybag, encryption_key=wrapped)
            self.assertEqual(
                destination.read_bytes(), keybag.decrypt(ciphertext, wrapped)
            )
            with sqlite3.connect(destination) as db:
                self.assertEqual(
                    db.execute("SELECT COUNT(*) FROM Files").fetchone()[0], 0
                )

    def test_truncated_cipher_and_wrong_padding_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-crypto-test-") as temporary:
            root = Path(temporary)
            keybag = Keybag({1: b"w" * 32})
            wrapped = struct.pack("<I", 1) + aes_key_wrap(b"w" * 32, b"k" * 32)
            for index, payload in enumerate((b"truncated", b"x" * 32)):
                source = root / "cipher"
                source.write_bytes(payload)
                with self.assertRaises(ValueError):
                    copy_backup_file(
                        source,
                        root / f"failed{index}",
                        keybag=keybag,
                        encryption_key=wrapped,
                        padded=True,
                    )

    def test_parsing_copy_observes_cancellation_before_next_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-crypto-test-") as temporary:
            root = Path(temporary)
            source = root / "source"
            source.write_bytes(b"synthetic" * 100_000)
            watchdog = BackupWatchdog()
            destination = root / "copy"
            import asyncio

            with self.assertRaises(asyncio.CancelledError):
                copy_backup_file(
                    source,
                    destination,
                    bound_callback=watchdog.raise_if_aborted,
                    progress_callback=lambda _: watchdog.abort(),
                )
            self.assertEqual(destination.stat().st_size, 128 * 1024)

    def test_real_sdk_nonregular_manifest_modes_reject_before_copy(self) -> None:
        from unittest.mock import Mock

        from amplifai_phone.metadata import UnsupportedSchema

        for mode in (0o040700, 0o120700):
            with (
                self.subTest(mode=mode),
                tempfile.TemporaryDirectory(prefix="amplifai-entry-test-") as temporary,
            ):
                root = Path(temporary)
                file_id = hashlib.sha1(b"synthetic-nonregular").hexdigest()
                path = root / file_id[:2] / file_id
                path.parent.mkdir()
                path.write_bytes(b"regular-bytes-with-nonregular-manifest")
                backup = SimpleNamespace(
                    path=root,
                    ios_version=Version("18.0"),
                    is_encrypted=False,
                    keybag=None,
                )
                now = datetime.now(UTC)
                entry = Entry(
                    backup,
                    file_id,
                    "HomeDomain",
                    DATABASES["contacts"],
                    now,
                    now,
                    now,
                    path.stat().st_size,
                    mode,
                    0,
                    0,
                    b"",
                )
                backup.get_entry_by_domain_and_path = Mock(return_value=entry)
                destination = root / "extracted"
                with self.assertRaises(UnsupportedSchema):
                    _extract_database(backup, DATABASES["contacts"], destination)
                self.assertFalse(destination.exists())

    def test_workspace_decrypt_consumes_reserved_copy_and_stops_before_low_space_write(
        self,
    ) -> None:
        wrapping, key = b"w" * 32, b"k" * 32
        keybag = Keybag({1: wrapping})
        wrapped = struct.pack("<I", 1) + aes_key_wrap(wrapping, key)
        plaintext = b"s" * (3 * 128 * 1024 + 13)
        padder = padding.PKCS7(128).padder()
        encryptor = Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16)).encryptor()
        encrypted = (
            encryptor.update(padder.update(plaintext) + padder.finalize())
            + encryptor.finalize()
        )
        for low_space in (False, True):
            with (
                self.subTest(low_space=low_space),
                tempfile.TemporaryDirectory(
                    prefix="amplifai-decrypt-reserve-"
                ) as temporary,
            ):
                workspace = SessionWorkspace(Path(temporary) / "sessions")
                with workspace as directory:
                    source, destination = directory / "cipher", directory / "plaintext"
                    with workspace.open_private(source) as stream:
                        workspace.write_chunk(stream, encrypted, reserve_copy=True)
                    free = [MIN_FREE_BYTES + len(encrypted)]

                    def processed(
                        _count: int, should_limit=low_space, current_free=free
                    ) -> None:
                        if should_limit:
                            current_free[0] = MIN_FREE_BYTES - 1

                    with patch(
                        "amplifai_phone.workspace.shutil.disk_usage",
                        side_effect=lambda _, current_free=free: SimpleNamespace(
                            free=current_free[0]
                        ),
                    ):
                        if low_space:
                            with self.assertRaises(WorkspaceError) as error:
                                copy_backup_file(
                                    source,
                                    destination,
                                    keybag=keybag,
                                    encryption_key=wrapped,
                                    padded=True,
                                    workspace=workspace,
                                    progress_callback=processed,
                                )
                            self.assertEqual(
                                error.exception.code, "workspace_low_space"
                            )
                            self.assertEqual(
                                destination.stat().st_size, 128 * 1024 - 16
                            )
                        else:
                            copy_backup_file(
                                source,
                                destination,
                                keybag=keybag,
                                encryption_key=wrapped,
                                padded=True,
                                workspace=workspace,
                                progress_callback=processed,
                            )
                            self.assertEqual(destination.read_bytes(), plaintext)
                            self.assertEqual(
                                workspace._pending_copy_bytes,
                                len(encrypted) - len(plaintext),
                            )
                self.assertFalse(directory.exists())

    def test_invalid_entry_padding_cleans_production_session_before_error_without_capture(
        self,
    ) -> None:
        from amplifai_phone.agent import run_connect
        from amplifai_phone.ios_backup import parse_selected_backup

        wrapping, key = b"w" * 32, b"k" * 32
        keybag = Keybag({1: wrapping})
        wrapped = struct.pack("<I", 1) + aes_key_wrap(wrapping, key)

        def encrypt(payload: bytes) -> bytes:
            crypt = Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16)).encryptor()
            return crypt.update(payload) + crypt.finalize()

        with tempfile.TemporaryDirectory(prefix="amplifai-bad-padding-") as temporary:
            root = Path(temporary)
            manifest_source = root / "synthetic-manifest"
            with sqlite3.connect(manifest_source) as db:
                db.execute(
                    "CREATE TABLE Files (fileID TEXT, domain TEXT, relativePath TEXT, flags INTEGER, file BLOB)"
                )
            manifest_payload = encrypt(manifest_source.read_bytes())
            session = [None]

            async def collector(**_kwargs):
                workspace = SessionWorkspace(root / "sessions")
                with workspace as directory:
                    session[0] = directory
                    backup_path = directory / "synthetic-backup"
                    backup_path.mkdir()
                    for name, value in (
                        ("Manifest.plist", {"IsEncrypted": True}),
                        ("Info.plist", {}),
                        ("Status.plist", {}),
                    ):
                        (backup_path / name).write_bytes(plistlib.dumps(value))
                    with workspace.open_private(backup_path / "Manifest.db") as stream:
                        workspace.write_chunk(
                            stream, manifest_payload, reserve_copy=True
                        )
                    file_id = hashlib.sha1(b"synthetic-bad-padding").hexdigest()
                    path = backup_path / file_id[:2] / file_id
                    with workspace.open_private(path) as stream:
                        workspace.write_chunk(
                            stream, encrypt(b"x" * 32), reserve_copy=True
                        )
                    backup = SimpleNamespace(
                        path=backup_path,
                        ios_version=Version("18.0"),
                        is_encrypted=True,
                        keybag=keybag,
                    )
                    now = datetime.now(UTC)
                    entry = Entry(
                        backup,
                        file_id,
                        "HomeDomain",
                        DATABASES["contacts"],
                        now,
                        now,
                        now,
                        32,
                        0o100600,
                        0,
                        0,
                        wrapped,
                    )
                    backup.get_entry_by_domain_and_path = lambda *_: entry
                    manifest = SimpleNamespace(is_encrypted=True, manifest_key=wrapped)
                    with (
                        patch(
                            "pyiosbackup.manifest_plist.ManifestPlist.from_path",
                            return_value=manifest,
                        ),
                        patch.object(Keybag, "from_manifest", return_value=keybag),
                        patch("pyiosbackup.Backup", return_value=backup),
                    ):
                        return parse_selected_backup(
                            backup_path,
                            "synthetic-only",
                            workspace=workspace,
                            bound_callback=workspace.check_bound,
                        )

            output = io.StringIO()
            self.assertEqual(run_connect(io.StringIO(), output, collector), 1)
            events = [json.loads(line) for line in output.getvalue().splitlines()]
            self.assertFalse(any(event["kind"] == "capture" for event in events))
            self.assertEqual(
                events[-1], {"kind": "error", "code": "unsupported_schema"}
            )
            self.assertFalse(session[0].exists())
            self.assertNotIn(str(root), output.getvalue())
            self.assertNotIn("synthetic-only", output.getvalue())
