"""Crash-inspectable, disposable private-object stand-in for offline capture.

Only outer-GCM ciphertext reaches this directory. It models a remote object
namespace; it is not a product fallback or a local plaintext backup.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

from .offline_grouped_object_vfs import MAX_GROUP_CIPHERTEXT

MAX_CAPTURE_OBJECT_BYTES = MAX_GROUP_CIPHERTEXT + 12


class SyntheticCaptureObjects:
    """Write-once ciphertext objects with a durable cleanup inventory."""

    def __init__(self, root: Path, attempt_id: str, *, reopen: bool = False) -> None:
        self.attempt_id = str(uuid.UUID(attempt_id))
        if not root.parent.is_dir() or (root.exists() != reopen):
            raise OSError("Synthetic object namespace is unavailable")
        if not reopen:
            root.mkdir(mode=0o700)
        if (
            root.is_symlink()
            or not root.is_dir()
            or stat.S_IMODE(root.stat().st_mode) != 0o700
        ):
            raise OSError("Synthetic object namespace is not private")
        self.root = root
        self.database = root / "inventory.sqlite3"
        if self.database.is_symlink() or (reopen and not self.database.is_file()):
            raise OSError("Synthetic object inventory is missing")
        with self._db() as db:
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS object (key TEXT PRIMARY KEY, "
                "digest TEXT NOT NULL, size INTEGER NOT NULL, "
                "state TEXT NOT NULL CHECK (state IN ('pending','committed')))"
            )
            db.execute("CREATE TABLE IF NOT EXISTS identity (attempt TEXT PRIMARY KEY)")
            if not reopen:
                db.execute("INSERT INTO identity VALUES (?)", (self.attempt_id,))
            elif db.execute("SELECT attempt FROM identity").fetchone() != (
                self.attempt_id,
            ):
                raise OSError("Synthetic object attempt changed")
        os.chmod(self.database, 0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.database)
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _path(self, key: str) -> Path:
        parts = key.split("/") if isinstance(key, str) else []
        if (
            len(parts) != 3
            or len(parts[0]) != 32
            or len(parts[2]) != 32
            or any(
                character not in "0123456789abcdef"
                for part in (parts[0], parts[2])
                for character in part
            )
            or parts[1]
            not in {
                "contacts-main",
                "contacts-wal",
                "contacts-shm",
                "calls-main",
                "calls-wal",
                "calls-shm",
                "messages-main",
                "messages-wal",
                "messages-shm",
            }
        ):
            raise OSError("Synthetic object identity is invalid")
        return self.root / hashlib.sha256(key.encode("ascii")).hexdigest()

    @staticmethod
    def _read(path: Path) -> bytes:
        handle = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(handle, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise OSError("Synthetic object is not regular")
            payload = stream.read(MAX_CAPTURE_OBJECT_BYTES + 1)
        if len(payload) > MAX_CAPTURE_OBJECT_BYTES:
            raise OSError("Synthetic object is oversized")
        return payload

    def put_exact(self, key: str, payload: bytes) -> None:
        path = self._path(key)
        if (
            not isinstance(payload, bytes)
            or not 28 <= len(payload) <= MAX_CAPTURE_OBJECT_BYTES
        ):
            raise OSError("Synthetic object is oversized")
        digest = hashlib.sha256(payload).hexdigest()
        with self._db() as db:
            prior = db.execute(
                "SELECT digest, size, state FROM object WHERE key=?", (key,)
            ).fetchone()
            if prior is not None and prior[:2] != (digest, len(payload)):
                raise OSError("Synthetic object changed on retry")
            if prior is None:
                db.execute(
                    "INSERT INTO object VALUES (?, ?, ?, 'pending')",
                    (key, digest, len(payload)),
                )
        if path.exists():
            if self._read(path) != payload:
                raise OSError("Synthetic object changed on retry")
        else:
            handle = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(handle, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        with self._db() as db:
            db.execute("UPDATE object SET state='committed' WHERE key=?", (key,))

    def get_exact(self, key: str) -> bytes:
        path = self._path(key)
        with self._db() as db:
            row = db.execute(
                "SELECT digest, size, state FROM object WHERE key=?", (key,)
            ).fetchone()
        if row is None or row[2] != "committed":
            raise OSError("Synthetic object is unavailable")
        payload = self._read(path)
        if len(payload) != row[1] or hashlib.sha256(payload).hexdigest() != row[0]:
            raise OSError("Synthetic object integrity failed")
        return payload

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)
        with self._db() as db:
            db.execute("DELETE FROM object WHERE key=?", (key,))

    def keys(self) -> tuple[str, ...]:
        with self._db() as db:
            return tuple(
                row[0] for row in db.execute("SELECT key FROM object ORDER BY key")
            )

    def ciphertexts(self):
        for key in self.keys():
            yield self.get_exact(key)

    @property
    def ciphertext_bytes(self) -> int:
        with self._db() as db:
            return int(
                db.execute("SELECT coalesce(sum(size),0) FROM object").fetchone()[0]
            )

    def cleanup(self) -> None:
        for key in self.keys():
            self.delete(key)
        # A process can die after writing a blob but before committing its row.
        for path in self.root.iterdir():
            if len(path.name) == 64 and all(c in "0123456789abcdef" for c in path.name):
                path.unlink()
        directory = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
