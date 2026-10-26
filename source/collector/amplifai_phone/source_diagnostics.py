"""Strict, nonpersonal source-failure facts and private previous-run history.

History is never current-capture metadata. Callers must attach only their current
attempt's diagnostics to a capture or Save, and label this history separately.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import stat
import time
from typing import TypedDict
from uuid import UUID

from .workspace import default_sessions_root

MAX_RECORDS = 12
MAX_ATTEMPTS = 10
MAX_AGE_SECONDS = 30 * 24 * 60 * 60
MAX_BYTES = 65_536
MAX_SAFE_INTEGER = 9_007_199_254_740_991
SOURCES = frozenset({"contacts", "calls", "messages"})
COMPONENTS = frozenset({"database", "wal", "shm", "journal"})
REASONS = frozenset({
    "absent", "selected_payload_missing", "selected_payload_invalid",
    "selected_payload_size", "selected_payload_identity", "selected_payload_file_type",
    "selected_payload_length", "selected_payload_crypto", "source_integrity",
    "unsupported_schema", "source_capacity", "source_invalid",
    "selected_contacts_integrity", "contacts_schema",
})
RESULTS = frozenset({"length_mismatch", "copy_failed", "unavailable"})
VALIDATION_CODES = frozenset({
    "insufficient_manifest", "active_wal", "active_journal", "active_shm", "unsafe",
    "checksum_mismatch", "structural_invalid", "timed_out", "unavailable",
    "unsupported_tokenizer", "validated_contacts_tables", "contacts_relationship_invalid",
    "contacts_types_invalid", "validated_messages_database", "copy_checksum_mismatch",
    "sidecar_present", "schema_unsupported", "validated_call_database", "unsupported_size_growth",
    "validated_messages_one_page_growth", "validated_messages_two_page_growth",
})
VALIDATION_SCOPES = frozenset({
    "contacts_tables_v1", "messages_database_v1", "encrypted_contacts_tables_v1",
    "encrypted_calls_database_v1", "encrypted_messages_database_v1",
})
RECORD_FIELDS = (
    "source", "component", "reason", "result", "declaredBytes", "rawBytes", "copiedBytes",
    "encrypted", "entryKeyPresent", "validationCode", "validationScope", "producerDigest",
    "copyDigest", "sidecars",
)
_ENUMS = {
    "source": SOURCES, "component": COMPONENTS, "reason": REASONS, "result": RESULTS,
    "producerDigest": frozenset({"matched", "mismatched", "absent", "unknown"}),
    "copyDigest": frozenset({"matched", "mismatched", "unknown"}),
    "sidecars": frozenset({"present", "absent", "unknown"}),
}
MARKER = ".amplifai-phone-diagnostics-v1"
_MARKER_BYTES = b"AMPLIFAI_PHONE_DIAGNOSTICS_V1\n"
_HISTORY = "history.json"
_LOCK = ".history.lock"
_TEMPORARY = ".history.next"
_ALLOWED_FILES = frozenset({MARKER, _HISTORY, _LOCK, _TEMPORARY})


class SourceDiagnostic(TypedDict):
    source: str
    component: str
    reason: str
    result: str
    declaredBytes: int | None
    rawBytes: int | None
    copiedBytes: int | None
    encrypted: bool | None
    entryKeyPresent: bool | None
    validationCode: str | None
    validationScope: str | None
    producerDigest: str
    copyDigest: str
    sidecars: str


class DiagnosticAttempt(TypedDict):
    attemptId: str
    createdAt: int
    sourceDiagnostics: list[SourceDiagnostic]


class DiagnosticsStorageError(RuntimeError):
    code = "diagnostics_unavailable"

    def __init__(self) -> None:
        super().__init__("Private diagnostics are unavailable")


def _invalid() -> None:
    raise ValueError("Invalid source diagnostics")


def _integer(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_SAFE_INTEGER


def _encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")


def validate_source_diagnostics(value: object) -> list[SourceDiagnostic]:
    """Return a fresh strict v1 array; reject successful, unknown or personal fields."""
    if type(value) is not list or len(value) > MAX_RECORDS:
        _invalid()
    result = []
    seen = set()
    for record in value:
        if type(record) is not dict or set(record) != set(RECORD_FIELDS):
            _invalid()
        for field, allowed in _ENUMS.items():
            if type(record[field]) is not str or record[field] not in allowed:
                _invalid()
        for field in ("declaredBytes", "rawBytes", "copiedBytes"):
            if record[field] is not None and not _integer(record[field]):
                _invalid()
        for field in ("encrypted", "entryKeyPresent"):
            if record[field] is not None and type(record[field]) is not bool:
                _invalid()
        for field, allowed in (("validationCode", VALIDATION_CODES), ("validationScope", VALIDATION_SCOPES)):
            if record[field] is not None and (type(record[field]) is not str or record[field] not in allowed):
                _invalid()
        key = record["source"], record["component"]
        if key in seen:
            _invalid()
        seen.add(key)
        result.append({field: record[field] for field in RECORD_FIELDS})
    return result


def _attempt_id(value: object, *, normalize: bool = False) -> str:
    if type(value) is not str or len(value) not in ({32, 36} if normalize else {36}):
        _invalid()
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        _invalid()
    canonical = str(parsed)
    if parsed.version != 4 or parsed.variant != "specified in RFC 4122" or (not normalize and canonical != value):
        _invalid()
    return canonical


def validate_source_diagnostic_history(value: object) -> list[DiagnosticAttempt]:
    """Validate previous-run facts only; no age-based inference or current-source merge."""
    if type(value) is not list or len(value) > MAX_ATTEMPTS:
        _invalid()
    result = []
    seen = set()
    for entry in value:
        if type(entry) is not dict or set(entry) != {"attemptId", "createdAt", "sourceDiagnostics"}:
            _invalid()
        identity = _attempt_id(entry["attemptId"])
        if identity in seen or not _integer(entry["createdAt"]):
            _invalid()
        seen.add(identity)
        diagnostics = validate_source_diagnostics(entry["sourceDiagnostics"])
        if not diagnostics:
            _invalid()
        result.append({"attemptId": identity, "createdAt": entry["createdAt"], "sourceDiagnostics": diagnostics})
    result.sort(key=lambda entry: (entry["createdAt"], entry["attemptId"]), reverse=True)
    if len(_encoded(result)) > MAX_BYTES:
        _invalid()
    return result


def merge_source_diagnostics(*groups: object) -> list[SourceDiagnostic]:
    """Latest failure facts replace that component; omitted/successful sources erase nothing."""
    merged = {}
    for group in groups:
        for record in validate_source_diagnostics(group):
            merged[record["source"], record["component"]] = record
    return validate_source_diagnostics(list(merged.values()))


def diagnostic_from_sample(value: object, reason: str | None = None) -> SourceDiagnostic | None:
    """Whitelist raw iOS callback facts, including a later failure after a good copy."""
    if type(value) is not dict:
        return None
    result = value.get("result")
    if type(result) is not str:
        return None
    if reason is None:
        reason = value.get("reason")
    if reason is None:
        reason = {"length_mismatch": "selected_payload_length", "copy_failed": "selected_payload_invalid", "unavailable": "source_invalid"}.get(result)
    if type(reason) is not str or reason not in REASONS:
        return None
    if result in {"match", "accepted_compatibility", "copying"}:
        result = "unavailable"
    if type(result) is not str or result not in RESULTS:
        return None
    try:
        present = value.get("producer_digest_present")
        matched = value.get("producer_digest_matches")
        copied = value.get("plaintext_copy_checksum_matches")
        sidecars = value.get("sidecars_absent")
        for flag in (present, matched, copied, sidecars):
            if flag is not None and type(flag) is not bool:
                _invalid()
        producer = "absent" if present is False else "matched" if matched is True else "mismatched" if matched is False else "unknown"
        raw = value.get("raw_after_bytes")
        if raw is None:
            raw = value.get("raw_before_bytes")
        record = {
            "source": value.get("source"), "component": value.get("component"),
            "reason": reason, "result": result,
            "declaredBytes": value.get("manifest_bytes"), "rawBytes": raw,
            "copiedBytes": value.get("copied_plaintext_bytes"),
            "encrypted": value.get("encrypted"), "entryKeyPresent": value.get("entry_key_present"),
            "validationCode": value.get("size_difference_validation"),
            "validationScope": value.get("size_difference_validation_scope"),
            "producerDigest": producer,
            "copyDigest": "matched" if copied is True else "mismatched" if copied is False else "unknown",
            "sidecars": "absent" if sidecars is True else "present" if sidecars is False else "unknown",
        }
        return validate_source_diagnostics([record])[0]
    except (TypeError, ValueError):
        return None


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _decode(data: bytes) -> list[DiagnosticAttempt]:
    if len(data) > MAX_BYTES:
        _invalid()
    document = json.loads(data.decode("ascii"), object_pairs_hook=_pairs)
    if type(document) is not dict or set(document) != {"version", "attempts"} or type(document["version"]) is not int or document["version"] != 1:
        _invalid()
    return validate_source_diagnostic_history(document["attempts"])


def _retained(entries: list[DiagnosticAttempt], now: int) -> list[DiagnosticAttempt]:
    result = [entry for entry in entries if now - MAX_AGE_SECONDS <= entry["createdAt"] <= now]
    result.sort(key=lambda entry: (entry["createdAt"], entry["attemptId"]), reverse=True)
    result = result[:MAX_ATTEMPTS]
    while result and len(_encoded({"version": 1, "attempts": result})) > MAX_BYTES:
        result.pop()
    return result


def _private_file(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        _invalid()
    if os.name != "nt" and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
        _invalid()
    if getattr(info, "st_file_attributes", 0) & 0x400:
        _invalid()


class _Directory:
    def __init__(self, path: Path, descriptor: int | None = None, native=None, principals=None, check_root=None):
        self.path, self.descriptor = path, descriptor
        self.native, self.principals = native, principals
        self.check_root = check_root

    def open(self, name: str, *, create: bool = False, writable: bool = False) -> int:
        if name not in _ALLOWED_FILES:
            _invalid()
        if self.check_root is not None:
            self.check_root()
        if self.descriptor is not None:
            flags = os.O_RDWR if writable else os.O_RDONLY
            flags |= getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
            if create:
                flags |= os.O_CREAT | os.O_EXCL
            descriptor = os.open(name, flags, 0o600, dir_fd=self.descriptor)
        else:
            import ctypes
            import msvcrt
            from .windows_storage import _FileAttributeTagInfo, _SecurityAttributes
            buffers = self.native.descriptor(self.principals) if create else None
            attributes = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), ctypes.addressof(buffers[0]), False) if create else None
            access = (0xC0000000 if writable else 0x80000000) | 0x00020080
            handle = self.native.kernel.CreateFileW(str(self.path / name), access, 0x03 if name == _LOCK else 0x01,
                ctypes.byref(attributes) if attributes is not None else None, 1 if create else 3, 0x00200080, None)
            if handle in (None, 0, ctypes.c_void_p(-1).value):
                code = ctypes.get_last_error()
                if code in (2, 3):
                    raise FileNotFoundError()
                if code in (80, 183):
                    raise FileExistsError()
                raise OSError()
            try:
                info = _FileAttributeTagInfo()
                if not self.native.kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(info), ctypes.sizeof(info)) or info.Attributes & (0x10 | 0x400) or info.ReparseTag:
                    _invalid()
                self.native.verify(handle, self.principals)
                descriptor = msvcrt.open_osfhandle(handle, (os.O_RDWR if writable else os.O_RDONLY) | os.O_BINARY)
            except BaseException:
                self.native.close(handle)
                raise
        try:
            _private_file(os.fstat(descriptor))
            self.same(name, descriptor)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def same(self, name: str, descriptor: int) -> None:
        current = os.stat(name, dir_fd=self.descriptor, follow_symlinks=False) if self.descriptor is not None else (self.path / name).lstat()
        _private_file(current)
        opened = os.fstat(descriptor)
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            _invalid()

    def read(self, name: str, maximum: int) -> bytes:
        descriptor = self.open(name)
        try:
            if os.fstat(descriptor).st_size > maximum:
                _invalid()
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                data = stream.read(maximum + 1)
            if len(data) > maximum:
                _invalid()
            self.same(name, descriptor)
            return data
        finally:
            os.close(descriptor)

    def unlink(self, name: str) -> None:
        if self.descriptor is not None:
            os.unlink(name, dir_fd=self.descriptor)
        else:
            (self.path / name).unlink()

    def replace(self) -> None:
        if self.descriptor is not None:
            os.replace(_TEMPORARY, _HISTORY, src_dir_fd=self.descriptor, dst_dir_fd=self.descriptor)
            os.fsync(self.descriptor)
        else:
            os.replace(self.path / _TEMPORARY, self.path / _HISTORY)

    def names(self) -> set[str]:
        return set(os.listdir(self.descriptor if self.descriptor is not None else self.path))


@contextmanager
def _posix_directory(path: Path, create: bool):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptors = [os.open(path.anchor, flags)]
    edges = []
    try:
        for component in path.parent.parts[1:]:
            parent = descriptors[-1]
            try:
                child = os.open(component, flags, dir_fd=parent)
            except FileNotFoundError:
                if not create:
                    yield None
                    return
                os.mkdir(component, 0o700, dir_fd=parent)
                os.fsync(parent)
                child = os.open(component, flags, dir_fd=parent)
            descriptors.append(child)
            edges.append((parent, component, child))
        parent = descriptors[-1]
        info = os.fstat(parent)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            _invalid()
        created = False
        try:
            root = os.open(path.name, flags, dir_fd=parent)
        except FileNotFoundError:
            if not create:
                yield None
                return
            os.mkdir(path.name, 0o700, dir_fd=parent)
            os.fsync(parent)
            root = os.open(path.name, flags, dir_fd=parent)
            created = True
        descriptors.append(root)
        edges.append((parent, path.name, root))

        def check_root():
            for ancestor, name, opened in edges:
                current = os.stat(name, dir_fd=ancestor, follow_symlinks=False)
                original = os.fstat(opened)
                if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
                    _invalid()
            for opened in (parent, root):
                current = os.fstat(opened)
                if current.st_uid != os.geteuid() or current.st_mode & 0o077:
                    _invalid()

        check_root()
        directory = _Directory(path, root, check_root=check_root)
        if created:
            marker = directory.open(MARKER, create=True, writable=True)
            try:
                os.write(marker, _MARKER_BYTES)
                os.fsync(marker)
            finally:
                os.close(marker)
        yield directory
        check_root()
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _windows_parent(path: Path, create: bool) -> bool:
    from .windows_storage import create_private_windows_directory
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            return False
        if path.parent == path or not _windows_parent(path.parent, create):
            _invalid()
        with create_private_windows_directory(path):
            pass
        return True
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        _invalid()
    return True


@contextmanager
def _windows_directory(path: Path, create: bool):
    from .windows_storage import _NativeSecurity, create_private_windows_directory
    if not _windows_parent(path.parent, create):
        yield None
        return
    with ExitStack() as lifetime:
        api = _NativeSecurity()
        principals = api.principals()
        try:
            path.lstat()
            exists = True
        except FileNotFoundError:
            exists = False
        if not exists:
            if not create:
                yield None
                return
            lifetime.enter_context(create_private_windows_directory(path))
            created = True
        else:
            created = False
        api.pin_parents(path, lifetime)
        handle = api.open_directory(path, lifetime)
        api.verify(handle, principals)
        directory = _Directory(path, native=api, principals=principals)
        if created:
            marker = directory.open(MARKER, create=True, writable=True)
            try:
                os.write(marker, _MARKER_BYTES)
                os.fsync(marker)
            finally:
                os.close(marker)
        yield directory
        api.verify(handle, principals)


@contextmanager
def _locked(directory: _Directory):
    try:
        descriptor = directory.open(_LOCK, writable=True)
    except FileNotFoundError:
        try:
            descriptor = directory.open(_LOCK, create=True, writable=True)
        except FileExistsError:
            descriptor = directory.open(_LOCK, writable=True)
    try:
        size = os.fstat(descriptor).st_size
        if size not in (0, 1) or (size == 1 and os.read(descriptor, 2) != b"1"):
            _invalid()
        if os.name == "nt":
            import msvcrt
            if size == 0:
                os.write(descriptor, b"1")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        directory.same(_LOCK, descriptor)
        yield
        directory.same(_LOCK, descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _marker(directory: _Directory):
    descriptor = directory.open(MARKER)
    def verify():
        directory.same(MARKER, descriptor)
        if os.fstat(descriptor).st_size != len(_MARKER_BYTES):
            _invalid()
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.read(descriptor, len(_MARKER_BYTES) + 1) != _MARKER_BYTES or not directory.names() <= _ALLOWED_FILES:
            _invalid()
    try:
        verify()
        yield
        verify()
    finally:
        os.close(descriptor)


class DiagnosticHistory:
    def __init__(self, sessions_root: Path | None = None):
        self._sessions_root = sessions_root

    @property
    def root(self) -> Path:
        try:
            sessions = Path(self._sessions_root) if self._sessions_root is not None else default_sessions_root()
            return sessions.absolute().parent / "diagnostics"
        except Exception:
            raise DiagnosticsStorageError() from None

    def _read(self, directory: _Directory) -> list[DiagnosticAttempt]:
        try:
            return _decode(directory.read(_HISTORY, MAX_BYTES))
        except FileNotFoundError:
            return []

    def _clean_temporary(self, directory: _Directory) -> None:
        try:
            descriptor = directory.open(_TEMPORARY)
        except FileNotFoundError:
            return
        try:
            if os.fstat(descriptor).st_size > MAX_BYTES:
                _invalid()
            directory.same(_TEMPORARY, descriptor)
        finally:
            os.close(descriptor)
        directory.unlink(_TEMPORARY)

    def _write(self, directory: _Directory, entries: list[DiagnosticAttempt]) -> None:
        payload = _encoded({"version": 1, "attempts": entries})
        if len(payload) > MAX_BYTES:
            _invalid()
        try:
            old = directory.open(_HISTORY)
        except FileNotFoundError:
            pass
        else:
            os.close(old)
        descriptor = directory.open(_TEMPORARY, create=True, writable=True)
        identity = os.fstat(descriptor)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(descriptor)
            directory.same(_TEMPORARY, descriptor)
            os.close(descriptor)
            descriptor = None
            directory.replace()
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                current = directory.open(_TEMPORARY)
            except FileNotFoundError:
                pass
            else:
                info = os.fstat(current)
                os.close(current)
                if (info.st_dev, info.st_ino) == (identity.st_dev, identity.st_ino):
                    directory.unlink(_TEMPORARY)

    def _operate(self, attempt_id=None, diagnostics=None, created_at=None):
        try:
            root = self.root
            if any(part in {".", ".."} for part in root.parts):
                _invalid()
            now = int(time.time())
            if not _integer(now):
                _invalid()
            if attempt_id is not None:
                identity = _attempt_id(attempt_id, normalize=True)
                current = validate_source_diagnostics(diagnostics)
                created = now if created_at is None else created_at
                if not _integer(created) or created > now:
                    _invalid()
            guard = _windows_directory if os.name == "nt" else _posix_directory
            with guard(root, attempt_id is not None) as directory:
                if directory is None:
                    return []
                with _marker(directory), _locked(directory):
                    self._clean_temporary(directory)
                    previous = self._read(directory)
                    entries = _retained(previous, now)
                    if attempt_id is not None and current:
                        old = next((entry for entry in entries if entry["attemptId"] == identity), None)
                        merged = merge_source_diagnostics(old["sourceDiagnostics"] if old else [], current)
                        entries = [entry for entry in entries if entry["attemptId"] != identity]
                        entries.append({"attemptId": identity, "createdAt": old["createdAt"] if old else created, "sourceDiagnostics": merged})
                        entries = _retained(entries, now)
                    if entries != previous or (attempt_id is not None and current):
                        self._write(directory, entries)
                    return validate_source_diagnostic_history(entries)
        except Exception:
            # This boundary contains only diagnostic validation/storage. Its sole
            # public failure is the fixed native storage state, never OS details.
            raise DiagnosticsStorageError() from None

    def record(self, attempt_id: str, diagnostics: object, *, created_at: int | None = None) -> list[DiagnosticAttempt]:
        """Merge this attempt's failure facts; an empty/partial success erases nothing."""
        return self._operate(attempt_id, diagnostics, created_at)

    def load(self) -> list[DiagnosticAttempt]:
        """Return bounded previous-run history only; never apply it to a capture."""
        return self._operate()
