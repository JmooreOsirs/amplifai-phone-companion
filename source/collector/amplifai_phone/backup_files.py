"""Bounded-memory copying/decryption of the three selected SQLite sources.

AES key unwrapping, CBC IV and per-entry padding match pinned pyiosbackup 0.2.4.
The manifest is block-aligned SQLite without the entry's PKCS7 unpadding step.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
from collections.abc import Callable
from pathlib import Path

from .backup_watchdog import RECEIVE_CHUNK_BYTES
from .metadata import SelectedPayloadIntegrityError
from .workspace import MIN_FREE_BYTES, SessionWorkspace, WorkspaceError


def copy_backup_file(
    source: Path,
    destination: Path,
    *,
    keybag=None,
    encryption_key: bytes = b"",
    padded: bool = False,
    workspace: SessionWorkspace | None = None,
    bound_callback: Callable[[], None] | None = None,
    progress_callback: Callable[[int], None] | None = None,
) -> int:
    decryptor = unpadder = None
    if keybag is not None:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.keywrap import aes_key_unwrap
        from pyiosbackup.keybag import encryption_key_struct

        parsed = encryption_key_struct.parse(encryption_key)
        key = aes_key_unwrap(keybag.get_key(parsed.class_), parsed.key)
        decryptor = Cipher(algorithms.AES(key), modes.CBC(b"\x00" * 16)).decryptor()
        unpadder = padding.PKCS7(128).unpadder() if padded else None

    count = 0

    def write(stream, payload: bytes) -> None:
        nonlocal count
        if not payload:
            return
        if bound_callback is not None:
            bound_callback()
        if workspace is not None:
            workspace.write_chunk(stream, payload, consume_copy=True)
        else:
            if shutil.disk_usage(destination.parent).free < MIN_FREE_BYTES + len(
                payload
            ):
                raise WorkspaceError(
                    "Insufficient space for a parsing copy", code="workspace_low_space"
                )
            if stream.write(payload) != len(payload):
                raise OSError(errno.EIO, "Incomplete parsing copy")
        count += len(payload)
        if progress_callback is not None:
            progress_callback(len(payload))

    source_handle = destination_handle = None
    try:
        # No symlink payloads; production also validates manifest file IDs and
        # the entire app-owned ancestor path before reaching this seam.
        source_handle = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        if not stat.S_ISREG(os.fstat(source_handle).st_mode):
            raise SelectedPayloadIntegrityError(
                "Selected payload is not a regular file", code="selected_payload_file_type"
            )
        reader = os.fdopen(source_handle, "rb")
        source_handle = None
        with reader:
            if workspace is not None:
                writer = workspace.open_private(destination)
            else:
                destination_handle = os.open(
                    destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                writer = os.fdopen(destination_handle, "wb")
                destination_handle = None
            with writer:
                while payload := reader.read(RECEIVE_CHUNK_BYTES):
                    if decryptor is not None:
                        payload = decryptor.update(payload)
                    if unpadder is not None:
                        payload = unpadder.update(payload)
                    write(writer, payload)
                if decryptor is not None:
                    try:
                        tail = decryptor.finalize()
                        if unpadder is not None:
                            tail = unpadder.update(tail) + unpadder.finalize()
                    except ValueError:
                        raise SelectedPayloadIntegrityError(
                            "Encrypted selected payload was incomplete or invalid",
                            code="selected_payload_crypto",
                        ) from None
                    write(writer, tail)
        return count
    except OSError as exc:
        code = (
            "workspace_low_space"
            if exc.errno in {errno.ENOSPC, errno.EDQUOT}
            else "workspace_unavailable"
        )
        raise WorkspaceError(
            "Selected backup storage is unavailable", code=code
        ) from exc
    finally:
        if source_handle is not None:
            os.close(source_handle)
        if destination_handle is not None:
            os.close(destination_handle)
