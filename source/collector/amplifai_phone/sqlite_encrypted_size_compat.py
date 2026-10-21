"""Strict, source-local proof for a smaller encrypted selected SQLite main file.

The producer digest is compared with the exact ciphertext consumed by the
decrypting copy. The private copy is independently hashed while written and
again inside a bounded read-only worker. No sidecar may accompany the main.
Only fixed validation codes cross the worker boundary.

A larger encrypted Messages copy can collect the same proof facts for diagnosis,
but is never admitted. Ciphertext/copy checksums cannot explain a contradictory
manifest size, even when the difference happens to be one SQLite page.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import plistlib
import re
import sqlite3
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from amplifai_phone import sqlite_messages_size_compat as messages
from amplifai_phone import sqlite_size_compat as contacts

WORKER_FLAG = "--internal-encrypted-size-validation"
PATHS = {
    "contacts": contacts.CONTACTS_PATH,
    "calls": "Library/CallHistoryDB/CallHistory.storedata",
    "messages": messages.MESSAGES_PATH,
}
SCOPES = {
    "contacts": "encrypted_contacts_tables_v1",
    "calls": "encrypted_calls_database_v1",
    "messages": "encrypted_messages_database_v1",
}
SUCCESS = {
    "contacts": "validated_contacts_tables",
    "calls": "validated_call_database",
    "messages": "validated_messages_database",
}
OUTCOMES = contacts.OUTCOMES | messages.OUTCOMES | {
    "copy_checksum_mismatch", "sidecar_present", "schema_unsupported",
    "validated_call_database", "unsupported_size_growth",
}


@dataclass(frozen=True)
class Validation:
    code: str
    scope: str
    producer_checksum_matches: bool | None = None
    plaintext_copy_checksum_matches: bool | None = None
    producer_digest_present: bool | None = None
    sidecars_absent: bool | None = None

    @property
    def accepted(self) -> bool:
        return (
            any(self.scope == SCOPES[category] and self.code == SUCCESS[category]
                for category in PATHS)
            and self.producer_checksum_matches is True
            and self.plaintext_copy_checksum_matches is True
        )


def _data(objects: list, value: object) -> object:
    value = contacts._resolve(objects, value)
    if isinstance(value, dict):
        kind = contacts._resolve(objects, value["$class"])
        if not isinstance(kind, dict) or kind.get("$classname") not in {"NSData", "NSMutableData"}:
            raise ValueError("Invalid archived data")
        value = value.get("NS.data")
    return value


def _manifest_proof(connection: sqlite3.Connection, relative: str) -> tuple[int, bytes | None, bytes] | None:
    rows = connection.execute(
        "SELECT fileID,flags,length(file) FROM Files WHERE domain=? AND relativePath=? LIMIT 2",
        (contacts.DOMAIN, relative),
    ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError("Ambiguous source")
    file_id, flags, length = rows[0]
    expected = hashlib.sha1(f"{contacts.DOMAIN}-{relative}".encode()).hexdigest()
    if file_id != expected or type(flags) is not int or flags != 1 or type(length) is not int or not 0 < length <= contacts.MAX_ARCHIVE_BYTES:
        raise ValueError("Invalid source identity")
    raw = connection.execute(
        "SELECT file FROM Files WHERE fileID=? AND domain=? AND relativePath=?",
        (expected, contacts.DOMAIN, relative),
    ).fetchone()[0]
    if not isinstance(raw, bytes) or len(raw) != length:
        raise ValueError("Invalid source archive")
    archive = plistlib.loads(raw)
    if not isinstance(archive, dict) or archive.get("$archiver") != "NSKeyedArchiver" or archive.get("$version") != 100000:
        raise ValueError("Invalid source archive")
    objects = archive["$objects"]
    if not isinstance(objects, list) or not 1 <= len(objects) <= 256:
        raise ValueError("Invalid source archive")
    root = contacts._resolve(objects, archive["$top"]["root"])
    if not isinstance(root, dict):
        raise ValueError("Invalid source archive")
    kind = contacts._resolve(objects, root["$class"])
    if not isinstance(kind, dict) or kind.get("$classname") != "MBFile" or contacts._resolve(objects, root["RelativePath"]) != relative:
        raise ValueError("Invalid source archive")
    size, mode = root["Size"], root["Mode"]
    digest = _data(objects, root.get("Digest"))
    key = _data(objects, root.get("EncryptionKey"))
    if type(size) is not int or not 0 <= size < 2**63 or type(mode) is not int or not stat.S_ISREG(mode):
        raise ValueError("Invalid source archive")
    if digest is not None and (not isinstance(digest, bytes) or len(digest) != 20):
        raise ValueError("Invalid producer digest")
    if not isinstance(key, bytes) or not key:
        raise ValueError("Missing producer proof")
    return size, digest, key


def _sidecars_absent(connection: sqlite3.Connection, backup_path: Path, destination: Path,
                     relative: str, workspace) -> bool:
    for suffix in ("-wal", "-shm", "-journal"):
        if _manifest_proof(connection, relative + suffix) is not None:
            return False
        file_id = hashlib.sha1(f"{contacts.DOMAIN}-{relative}{suffix}".encode()).hexdigest()
        for path in (backup_path / file_id[:2] / file_id, Path(str(destination) + suffix)):
            workspace.checked_path(path)
            if path.exists() or path.is_symlink():
                return False
    return True


def validate_encrypted_size_difference(
    category: str, backup, entry, destination: Path, actual_size: int,
    *, workspace, source_before, raw_size: int, raw_digest: bytes,
    copied_digest: bytes, bound_callback=None,
) -> Validation:
    if category not in PATHS:
        raise ValueError("Unsupported source")
    scope = SCOPES[category]
    producer_match: bool | None = None
    copy_match: bool | None = None
    digest_present: bool | None = None
    sidecars_absent: bool | None = None
    worker = None

    def result(code: str) -> Validation:
        return Validation(code, scope, producer_match, copy_match,
                          digest_present, sidecars_absent)

    try:
        relative = PATHS[category]
        expected_id = hashlib.sha1(f"{contacts.DOMAIN}-{relative}".encode()).hexdigest()
        source = Path(backup.path) / expected_id[:2] / expected_id
        if backup.is_encrypted is not True or workspace is None or entry.relative_path != relative:
            return result("insufficient_manifest")
        if entry.file_id != expected_id or Path(entry.real_path) != source or not isinstance(entry.encryption_key, bytes) or not entry.encryption_key:
            return result("unsafe")
        if (type(actual_size) is not int or type(entry.size) is not int
                or not 512 <= actual_size < 2**63 or not 512 <= entry.size < 2**63
                or actual_size == entry.size):
            return result("insufficient_manifest")
        growing = actual_size > entry.size
        if growing and category != "messages":
            return result("insufficient_manifest")
        if type(raw_size) is not int or raw_size != (actual_size // 16 + 1) * 16:
            return result("unsafe")
        if not isinstance(raw_digest, bytes) or len(raw_digest) != 20 or not isinstance(copied_digest, bytes) or len(copied_digest) != 32:
            return result("unsafe")
        connection = backup._manifest_db._conn
        if not isinstance(connection, sqlite3.Connection):
            return result("insufficient_manifest")
        proof = _manifest_proof(connection, relative)
        if proof is None or proof[0] != entry.size or not hmac.compare_digest(proof[2], entry.encryption_key):
            return result("insufficient_manifest")
        digest_present = proof[1] is not None
        if not digest_present:
            return result("insufficient_manifest")
        sidecars_absent = _sidecars_absent(connection, Path(backup.path), destination, relative, workspace)
        if not sidecars_absent:
            return result("sidecar_present")
        workspace.checked_path(source)
        workspace.checked_path(destination)
        before_copy = contacts._regular_info(destination)
        if before_copy.st_size != actual_size or raw_size != source_before.st_size:
            return result("unsafe")
        if contacts._identity(source_before) != contacts._identity(contacts._regular_info(source)):
            return result("unsafe")
        producer_match = hmac.compare_digest(raw_digest, proof[1])
        if not producer_match:
            return result("checksum_mismatch")
        if bound_callback is not None:
            bound_callback()
        request = json.dumps({
            "category": category, "path": str(destination.absolute()),
            "actual_size": actual_size, "manifest_size": entry.size,
            "copied_sha256": copied_digest.hex(),
        })
        if len(request.encode()) > 2048:
            return result("unsafe")
        command = ([sys.executable, WORKER_FLAG] if getattr(sys, "frozen", False) else
                   [sys.executable, "-I", "-B", str(Path(__file__).resolve())])
        worker = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True)
        output = None
        expires = time.monotonic() + contacts._work_deadline(actual_size) + 10
        while output is None:
            remaining = expires - time.monotonic()
            if remaining <= 0:
                return result("timed_out")
            try:
                output, _ = worker.communicate(request, timeout=min(1, remaining))
            except subprocess.TimeoutExpired:
                request = None
                if bound_callback is not None:
                    bound_callback()
        if worker.returncode or len(output) > 256:
            return result("unavailable")
        payload = json.loads(output)
        if not isinstance(payload, dict) or set(payload) != {"code", "copy_checksum_matches"}:
            return result("unavailable")
        code, copy_match = payload["code"], payload["copy_checksum_matches"]
        if code not in OUTCOMES or type(copy_match) is not bool:
            return result("unavailable")
        if code in SUCCESS.values() and code != SUCCESS[category]:
            return result("unavailable")
        # Growth is diagnostic-only, including if a faulty worker reports a
        # success code. Never let it widen the existing acceptance boundary.
        if growing and code in SUCCESS.values():
            return result("unavailable")
        if contacts._identity(before_copy) != contacts._identity(contacts._regular_info(destination)):
            return result("unsafe")
        if contacts._identity(source_before) != contacts._identity(contacts._regular_info(source)):
            return result("unsafe")
        sidecars_absent = _sidecars_absent(connection, Path(backup.path), destination, relative, workspace)
        if not sidecars_absent:
            return result("sidecar_present")
        if bound_callback is not None:
            bound_callback()
        return result(code)
    except subprocess.TimeoutExpired:
        return result("timed_out")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError,
            RecursionError, sqlite3.DatabaseError, plistlib.InvalidFileException):
        return result("insufficient_manifest")
    finally:
        if worker is not None and worker.poll() is None:
            try:
                worker.kill()
            except ProcessLookupError:
                pass
            try:
                worker.communicate(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass


def _call_integrity(path: Path, actual_size: int, manifest_size: int, deadline: float) -> str:
    with path.open("rb") as stream:
        header = stream.read(100)
    if len(header) != 100 or header[:16] != b"SQLite format 3\0":
        return "structural_invalid"
    page = int.from_bytes(header[16:18], "big")
    page = 65536 if page == 1 else page
    pages = int.from_bytes(header[28:32], "big")
    if not 512 <= page <= 65536 or page & (page - 1) or not pages or header[24:28] != header[92:96] or pages * page != actual_size or manifest_size % page:
        return "structural_invalid"
    db = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True, timeout=0.1)
    try:
        db.enable_load_extension(False)
        for limit, value in ((sqlite3.SQLITE_LIMIT_LENGTH, 16 * 1024**2),
                             (sqlite3.SQLITE_LIMIT_SQL_LENGTH, 4096),
                             (sqlite3.SQLITE_LIMIT_COLUMN, 512),
                             (sqlite3.SQLITE_LIMIT_ATTACHED, 0),
                             (sqlite3.SQLITE_LIMIT_VDBE_OP, 20000)):
            db.setlimit(limit, value)
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA mmap_size=0")
        db.execute("PRAGMA cache_size=-4096")
        heap = db.execute("PRAGMA hard_heap_limit=67108864").fetchone()
        if heap is None or type(heap[0]) is not int or not 0 < heap[0] <= 67108864:
            return "unavailable"
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        catalog = db.execute("PRAGMA table_list").fetchmany(129)
        if len(catalog) > 128 or any(row[2] in {"virtual", "shadow"} for row in catalog):
            return "schema_unsupported"
        table = [row for row in catalog if row[0] == "main" and row[1] == "ZCALLRECORD"]
        if len(table) != 1 or table[0][2] != "table" or table[0][4] != 0:
            return "schema_unsupported"
        fields = db.execute("PRAGMA table_xinfo(ZCALLRECORD)").fetchall()
        if not {"ZDATE", "ZDURATION", "ZADDRESS", "ZORIGINATED", "ZANSWERED"} <= {row[1] for row in fields if row[6] == 0}:
            return "schema_unsupported"
        if db.execute("PRAGMA integrity_check(1)").fetchmany(2) != [("ok",)]:
            return "structural_invalid"
        if db.execute("PRAGMA page_count").fetchone()[0] != pages:
            return "structural_invalid"
    finally:
        db.close()
    return "validated_call_database" if time.monotonic() <= deadline else "timed_out"


def _validate(request: object) -> tuple[str, bool | None]:
    if not isinstance(request, dict) or set(request) != {"category", "path", "actual_size", "manifest_size", "copied_sha256"}:
        return "unsafe", None
    category, actual, declared = request["category"], request["actual_size"], request["manifest_size"]
    digest = request["copied_sha256"]
    if (category not in PATHS or type(actual) is not int or type(declared) is not int
            or not 512 <= actual < 2**63 or not 512 <= declared < 2**63
            or actual == declared or (actual > declared and category != "messages")
            or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
        return "unsafe", None
    path = Path(request["path"])
    if not contacts._frozen_owned_path(path):
        return "unsafe", None
    before = contacts._regular_info(path)
    if before.st_size != actual or contacts._copied_sidecar_status(path) is not None:
        return "unsafe", None
    deadline = time.monotonic() + contacts._work_deadline(actual)
    sha256 = hashlib.sha256()
    sha1 = hashlib.sha1()
    with path.open("rb") as stream:
        while chunk := stream.read(256 * 1024):
            if time.monotonic() > deadline:
                return "timed_out", None
            sha256.update(chunk)
            sha1.update(chunk)
    matched = hmac.compare_digest(sha256.hexdigest(), digest)
    if not matched:
        return "copy_checksum_mismatch", False
    if actual > declared:
        # Do not parse or truncate the contradictory copy. This worker proves
        # only that its bytes still match the copy made during decryption.
        code = "unsupported_size_growth"
    elif category == "calls":
        code = _call_integrity(path, actual, declared, deadline)
    else:
        code = (contacts if category == "contacts" else messages)._validate({
            "path": str(path), "actual_size": actual,
            "manifest_size": declared, "digest": sha1.hexdigest(),
        })
    if contacts._identity(before) != contacts._identity(contacts._regular_info(path)):
        return "unsafe", True
    return code, True


def worker_main() -> int:
    def deny(event, args):
        if event.startswith("socket."):
            raise PermissionError()
        if event == "open" and isinstance(args[2], int) and args[2] & (
            os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        ):
            raise PermissionError()
    sys.addaudithook(deny)
    code, copy_match = "unsafe", None
    try:
        code, copy_match = _validate(json.loads(sys.stdin.read(2049)))
    except sqlite3.DatabaseError as error:
        number = getattr(error, "sqlite_errorcode", 0) & 255
        code = ("timed_out" if number == sqlite3.SQLITE_INTERRUPT else
                "unavailable" if number == sqlite3.SQLITE_NOMEM else "structural_invalid")
    except subprocess.TimeoutExpired:
        code = "timed_out"
    except Exception:
        pass
    print(json.dumps({"code": code if code in OUTCOMES else "unsafe",
                      "copy_checksum_matches": copy_match}))
    return 0


if __name__ == "__main__":
    raise SystemExit(worker_main())
