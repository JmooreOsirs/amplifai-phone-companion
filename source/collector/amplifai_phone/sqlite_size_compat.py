"""Checksum-backed validation of Contacts parser inputs in a smaller export.

The whole copied file must match the producer checksum and have no active WAL.
Integrity checks cover the catalog/freelist, ABPerson, ABMultiValue and their
indexes, plus parser field types and owner links. Unused search caches are out
of scope: success is not a claim of whole-database integrity. Only fixed statuses
leave the worker; contact values are never printed or uploaded.
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

CONTACTS_PATH = 'Library/AddressBook/AddressBook.sqlitedb'
DOMAIN = 'HomeDomain'
WORKER_FLAG = '--internal-contacts-size-validation'
MAX_ARCHIVE_BYTES = 65536
CONTACTS_SCOPE = 'contacts_tables_v1'
OUTCOMES = {'insufficient_manifest', 'active_wal', 'active_journal', 'active_shm', 'unsafe',
            'checksum_mismatch', 'structural_invalid', 'timed_out', 'unavailable',
            'unsupported_tokenizer', 'validated_contacts_tables',
            'contacts_relationship_invalid', 'contacts_types_invalid',
            }


def _work_deadline(size: int) -> int:
    """Finite, size-proportional validation budget; no whole-source ceiling."""
    return 120 + ((size + 1024**2 - 1) // 1024**2) * 4


def _sidecar_status(connection, backup_path, destination, relative, *, workspace=None):
    """Require one coherent main-file snapshot before immutable SQLite reads."""
    for suffix, code in (('-wal', 'active_wal'), ('-journal', 'active_journal'),
                         ('-shm', 'active_shm')):
        declared = _row(connection, relative + suffix)
        file_id = hashlib.sha1((DOMAIN + '-' + relative + suffix).encode()).hexdigest()
        raw = Path(backup_path) / file_id[:2] / file_id
        copied = Path(str(destination) + suffix)
        if declared is not None and declared[0] != 0:
            return code
        for path in (raw, copied):
            if workspace is not None:
                workspace.checked_path(path)
            if path.exists() or path.is_symlink():
                if _regular_info(path).st_size != 0:
                    return code
            elif path == raw and declared is not None:
                return code
    return None


def _copied_sidecar_status(path):
    for suffix, code in (('-wal', 'active_wal'), ('-journal', 'active_journal'),
                         ('-shm', 'active_shm')):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            if _regular_info(sidecar).st_size:
                return code
    return None


@dataclass(frozen=True)
class Validation:
    code: str
    scope: str = CONTACTS_SCOPE

    @property
    def accepted(self) -> bool:
        return self.code == 'validated_contacts_tables'


def _resolve(objects, value):
    if isinstance(value, plistlib.UID):
        if not 0 <= value.data < len(objects):
            raise ValueError()
        return objects[value.data]
    return value


def _archive(raw, relative):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_ARCHIVE_BYTES:
        raise ValueError()
    payload = plistlib.loads(raw)
    if payload.get('$archiver') != 'NSKeyedArchiver' or payload.get('$version') != 100000:
        raise ValueError()
    objects = payload['$objects']
    if not isinstance(objects, list) or not 1 <= len(objects) <= 256:
        raise ValueError()
    root = _resolve(objects, payload['$top']['root'])
    if not isinstance(root, dict):
        raise ValueError()
    kind = _resolve(objects, root['$class'])
    if not isinstance(kind, dict) or kind.get('$classname') != 'MBFile':
        raise ValueError()
    if _resolve(objects, root['RelativePath']) != relative:
        raise ValueError()
    size, mode = root['Size'], root['Mode']
    if type(size) is not int or not 0 <= size < 2**63 or type(mode) is not int or not stat.S_ISREG(mode):
        raise ValueError()
    if 'EncryptionKey' in root:
        raise ValueError()
    digest = _resolve(objects, root.get('Digest'))
    if isinstance(digest, dict):
        kind = _resolve(objects, digest['$class'])
        if not isinstance(kind, dict) or kind.get('$classname') not in {'NSData', 'NSMutableData'}:
            raise ValueError()
        digest = digest.get('NS.data')
    return size, digest


def _row(connection, relative):
    rows = connection.execute(
        'SELECT fileID,flags,length(file) FROM Files WHERE domain=? AND relativePath=? LIMIT 2',
        (DOMAIN, relative),
    ).fetchall()
    if len(rows) > 1:
        raise ValueError()
    if not rows:
        return None
    file_id, flags, length = rows[0]
    expected_id = hashlib.sha1((DOMAIN+'-'+relative).encode()).hexdigest()
    if file_id != expected_id or type(flags) is not int or flags != 1 or type(length) is not int or not 0 < length <= MAX_ARCHIVE_BYTES:
        raise ValueError()
    raw = connection.execute('SELECT file FROM Files WHERE fileID=? AND domain=? AND relativePath=?',
                             (expected_id, DOMAIN, relative)).fetchone()[0]
    return _archive(raw, relative)


def _regular_info(path):
    path = Path(path)
    if not path.is_absolute() or path != path.resolve() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError()
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError()
    return info


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def validate_contacts_size_difference(backup, entry, destination, actual_size, *, workspace=None, bound_callback=None):
    """Return a fixed status; unsuccessful validation preserves the length error."""
    result = Validation('insufficient_manifest')
    worker = None
    try:
        if backup.is_encrypted is not False or entry.encryption_key or entry.relative_path != CONTACTS_PATH:
            return result
        expected_id = hashlib.sha1((DOMAIN+'-'+CONTACTS_PATH).encode()).hexdigest()
        if entry.file_id != expected_id or Path(entry.real_path) != Path(backup.path)/expected_id[:2]/expected_id:
            return result
        if type(actual_size) is not int or not 512 <= actual_size < entry.size:
            return result
        connection = backup._manifest_db._conn
        if not isinstance(connection, sqlite3.Connection):
            return result
        proof = _row(connection, CONTACTS_PATH)
        if proof is None or proof[0] != entry.size or not isinstance(proof[1], bytes) or len(proof[1]) != 20:
            return result
        sidecar = _sidecar_status(connection, backup.path, destination, CONTACTS_PATH,
                                  workspace=workspace)
        if sidecar is not None:
            return Validation(sidecar)
        destination = Path(destination).absolute()
        if workspace is not None:
            workspace.checked_path(destination)
        before = _regular_info(destination)
        if before.st_size != actual_size:
            return Validation('unsafe')
        if bound_callback is not None:
            bound_callback()
        request = json.dumps({'path':str(destination), 'actual_size':actual_size,
                              'manifest_size':entry.size, 'digest':proof[1].hex()})
        if len(request.encode()) > 2048:
            return result
        command = ([sys.executable, WORKER_FLAG] if getattr(sys, 'frozen', False) else
                   [sys.executable, '-I', '-B', str(Path(__file__).resolve())])
        worker = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True)
        # Recheck the active capture authority during long, size-scaled scans.
        output = None
        expires_at = time.monotonic() + _work_deadline(actual_size) + 10
        while output is None:
            remaining = expires_at - time.monotonic()
            if remaining <= 0:
                return Validation('timed_out')
            try:
                output, _ = worker.communicate(request, timeout=min(1, remaining))
            except subprocess.TimeoutExpired:
                request = None
                if bound_callback is not None:
                    bound_callback()
                continue
        if worker.returncode or len(output) > 256:
            return Validation('unavailable')
        payload = json.loads(output)
        code = payload.get('code') if isinstance(payload, dict) else None
        if not isinstance(code, str) or code not in OUTCOMES:
            return Validation('unavailable')
        if _identity(before) != _identity(_regular_info(destination)):
            return Validation('unsafe')
        if bound_callback is not None:
            bound_callback()
        return Validation(code)
    except subprocess.TimeoutExpired:
        return Validation('timed_out')
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError,
            RecursionError, sqlite3.DatabaseError, plistlib.InvalidFileException):
        return result
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


def _frozen_owned_path(path):
    """Frozen worker accepts only this parent's marked, private session copy."""
    if not getattr(sys, 'frozen', False):
        return True
    parent = Path(os.environ['LOCALAPPDATA']) if os.name == 'nt' else Path.home()/'Library/Application Support'
    root = parent/'AMPLIFai Phone Candidate/sessions'
    relative = path.relative_to(root)
    if len(relative.parts) < 2 or not re.fullmatch(r'session-[0-9a-f]{32}', relative.parts[0]):
        return False
    root_marker = root/'.amplifai-phone-sessions-v1'
    if root_marker.is_symlink() or root_marker.stat().st_nlink != 1 or root_marker.read_bytes() != b'AMPLIFAI_PHONE_SESSIONS_V1\n':
        return False
    session = root/relative.parts[0]
    marker = session/'.amplifai-owned-session.json'
    if marker.is_symlink() or marker.stat().st_nlink != 1 or marker.stat().st_size > 512:
        return False
    data = json.loads(marker.read_text())
    if data.get('version') != 1 or data.get('session') != session.name or data.get('pid') != os.getppid():
        return False
    return os.name == 'nt' or not ((root.stat().st_mode | session.stat().st_mode) & 0o077)


def _validate(request):
    if not isinstance(request, dict) or set(request) != {'path', 'actual_size', 'manifest_size', 'digest'}:
        return 'unsafe'
    actual, expected, digest = request['actual_size'], request['manifest_size'], request['digest']
    if type(actual) is not int or not 512 <= actual < expected or type(expected) is not int or expected >= 2**63:
        return 'unsafe'
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{40}', digest):
        return 'unsafe'
    path = Path(request['path'])
    if not _frozen_owned_path(path):
        return 'unsafe'
    before = _regular_info(path)
    if before.st_size != actual:
        return 'unsafe'
    sidecar = _copied_sidecar_status(path)
    if sidecar is not None:
        return sidecar
    deadline = time.monotonic()+_work_deadline(actual)
    with path.open('rb') as stream:
        raw = stream.read(100)
        if len(raw) != 100 or raw[:16] != b'SQLite format 3\0':
            return 'structural_invalid'
        page = int.from_bytes(raw[16:18], 'big')
        page = 65536 if page == 1 else page
        pages = int.from_bytes(raw[28:32], 'big')
        if not 512 <= page <= 65536 or page & (page-1) or not pages or raw[24:28] != raw[92:96] or pages*page != actual or expected % page:
            return 'structural_invalid'
        stream.seek(0)
        hasher = hashlib.sha1()
        while chunk := stream.read(256*1024):
            if time.monotonic() > deadline:
                return 'timed_out'
            hasher.update(chunk)
    if not hmac.compare_digest(hasher.hexdigest(), digest):
        return 'checksum_mismatch'
    db = sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1', uri=True, timeout=0.1)
    try:
        db.enable_load_extension(False)
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 16*1024**2)
        db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 4096)
        db.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 512)
        db.setlimit(sqlite3.SQLITE_LIMIT_VDBE_OP, 20000)
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        db.execute('PRAGMA mmap_size=0')
        db.execute('PRAGMA cache_size=-4096')
        heap_limit = db.execute('PRAGMA hard_heap_limit=67108864').fetchone()
        if heap_limit is None or type(heap_limit[0]) is not int or not 0 < heap_limit[0] <= 67108864:
            return 'unavailable'
        required = {'ABPerson':{'First','Last'}, 'ABMultiValue':{'record_id','property','value'}}
        # Expected derived caches are never instantiated or queried. Reject
        # unknown virtual modules/names rather than granting them SQL access.
        virtual = db.execute(
            "SELECT name,sql FROM sqlite_schema WHERE type='table' AND upper(sql) LIKE 'CREATE VIRTUAL TABLE%'"
        ).fetchall()
        virtual_names = {name for name, _ in virtual}
        for name, sql in virtual:
            if name not in {'ABPersonFullTextSearch', 'ABPersonSmartDialerFullTextSearch'}:
                return 'unavailable'
            if not isinstance(sql, str) or not re.search(r'\bUSING\s+fts[34]\s*\(', sql, re.I):
                return 'unavailable'
        catalog = db.execute('SELECT type,rootpage,name FROM sqlite_schema').fetchall()
        for kind, root, name in catalog:
            if kind not in {'table','index','view','trigger'} or type(root) is not int or not 0 <= root <= pages:
                return 'structural_invalid'
            if kind in {'table','index'} and root == 0 and name not in virtual_names:
                return 'structural_invalid'
        phase = 'catalog'
        consumed = {'ABPerson':{'rowid','first','last',''},
                    'ABMultiValue':{'record_id','property','value',''}}
        def authorize(action, one, two, _database, _trigger):
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ and one in {'sqlite_master','sqlite_schema','sqlite_temp_master','sqlite_temp_schema'}:
                return sqlite3.SQLITE_OK
            if (action == sqlite3.SQLITE_READ and phase == 'contacts_queries' and
                    one in consumed and (_database == 'main' or (two == '' and _database is None)) and
                    isinstance(two, str) and two.casefold() in consumed[one]):
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION and phase == 'contacts_queries' and two in {'typeof','count'}:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_PRAGMA:
                if one == 'integrity_check' and two in {'sqlite_schema',*required}:
                    return sqlite3.SQLITE_OK
                if one == 'page_count' and two is None:
                    return sqlite3.SQLITE_OK
                if one in {'table_list','table_xinfo'} and two in required:
                    return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        db.set_authorizer(authorize)
        db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        for table, columns in required.items():
            kinds = db.execute('PRAGMA table_list('+table+')').fetchmany(2)
            if len(kinds) != 1 or kinds[0][0] != 'main' or kinds[0][2] != 'table' or kinds[0][4] != 0:
                return 'structural_invalid'
            fields = db.execute('PRAGMA table_xinfo('+table+')').fetchall()
            stored = {row[1] for row in fields if row[6] == 0}
            if not columns <= stored:
                return 'structural_invalid'
            if table == 'ABPerson':
                explicit_rowid = next((row for row in fields if row[1].casefold() == 'rowid'), None)
                if explicit_rowid is not None and (explicit_rowid[2].upper() != 'INTEGER' or
                        explicit_rowid[5] != 1 or explicit_rowid[6] != 0 or sum(bool(row[5]) for row in fields) != 1):
                    return 'structural_invalid'
        # Partial checks include each table's associated indexes and constraints.
        # sqlite_schema additionally checks the catalog and freelist. They do
        # not certify unused caches or cross-table page ownership globally.
        for table in ('sqlite_schema', *required):
            if db.execute('PRAGMA integrity_check('+table+')').fetchmany(2) != [('ok',)]:
                return 'structural_invalid'
        if db.execute('PRAGMA page_count').fetchone()[0] != pages:
            return 'structural_invalid'
        phase = 'contacts_queries'
        person_types = "SELECT 1 FROM ABPerson WHERE typeof(ROWID)!='integer' OR typeof(First) NOT IN ('text','null') OR typeof(Last) NOT IN ('text','null') LIMIT 1"
        value_types = "SELECT 1 FROM ABMultiValue WHERE property IN (3,4) AND value IS NOT NULL AND (typeof(record_id)!='integer' OR typeof(property)!='integer' OR typeof(value)!='text') LIMIT 1"
        if db.execute(person_types).fetchone() is not None or db.execute(value_types).fetchone() is not None:
            return 'contacts_types_invalid'
        links = 'SELECT 1 FROM ABMultiValue AS v WHERE v.property IN (3,4) AND v.value IS NOT NULL AND NOT EXISTS (SELECT 1 FROM ABPerson AS p WHERE p.ROWID=v.record_id) LIMIT 1'
        if db.execute(links).fetchone() is not None:
            return 'contacts_relationship_invalid'
    finally:
        db.close()
    if time.monotonic() > deadline:
        return 'timed_out'
    return 'validated_contacts_tables' if _identity(before) == _identity(_regular_info(path)) else 'unsafe'


def worker_main():
    """Internal fixed-status worker: no device imports or connections."""
    def deny_mutation(event, args):
        if event.startswith('socket.'):
            raise PermissionError()
        if event == 'open' and isinstance(args[2], int) and args[2] & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):
            raise PermissionError()
    sys.addaudithook(deny_mutation)
    code = 'unsafe'
    try:
        request = json.loads(sys.stdin.read(2049))
        code = _validate(request)
    except sqlite3.DatabaseError as error:
        number = getattr(error, 'sqlite_errorcode', 0) & 255
        missing_tokenizer = number == sqlite3.SQLITE_ERROR and any(
            phrase in str(error).casefold() for phrase in
            ('unknown tokenizer', 'no such tokenizer', 'unrecognized tokenizer'))
        code = ('timed_out' if number == sqlite3.SQLITE_INTERRUPT else
                'unsupported_tokenizer' if missing_tokenizer else
                'unavailable' if number == sqlite3.SQLITE_NOMEM else 'structural_invalid')
    except Exception:
        pass
    print(json.dumps({'code':code if code in OUTCOMES else 'unsafe'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(worker_main())
