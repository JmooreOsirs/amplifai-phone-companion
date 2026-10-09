"""Bounded checksum and SQLite integrity validation of smaller Messages exports.

No message text/attachment query is issued. SQLite's integrity check can scan
record pages internally. Only fixed statuses leave the read-only worker.
"""
from __future__ import annotations
import hashlib
import hmac
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from . import sqlite_size_compat as proof
else:
    spec=importlib.util.spec_from_file_location('_messages_manifest_proof',Path(__file__).with_name('sqlite_size_compat.py'))
    proof=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=proof
    spec.loader.exec_module(proof)

MESSAGES_PATH='Library/SMS/sms.db'
SCOPE='messages_database_v1'
WORKER_FLAG='--internal-messages-size-validation'
OUTCOMES={'insufficient_manifest','active_wal','active_journal','active_shm','unsafe','checksum_mismatch',
          'structural_invalid','timed_out','unavailable','unsupported_tokenizer',
          'validated_messages_database'}

@dataclass(frozen=True)
class Validation:
    code:str
    scope:str=SCOPE
    @property
    def accepted(self):
        return self.code=='validated_messages_database' and self.scope==SCOPE

def validate_messages_size_difference(backup,entry,destination,actual_size,*,workspace=None,bound_callback=None):
    result=Validation('insufficient_manifest')
    worker=None
    try:
        if backup.is_encrypted is not False or entry.encryption_key or entry.relative_path!=MESSAGES_PATH:
            return result
        expected_id=hashlib.sha1((proof.DOMAIN+'-'+MESSAGES_PATH).encode()).hexdigest()
        if entry.file_id!=expected_id or Path(entry.real_path)!=Path(backup.path)/expected_id[:2]/expected_id:
            return result
        if type(actual_size) is not int or not 512<=actual_size<entry.size:
            return result
        connection=backup._manifest_db._conn
        if not isinstance(connection,sqlite3.Connection):return result
        main=proof._row(connection,MESSAGES_PATH)
        if main is None or main[0]!=entry.size or not isinstance(main[1],bytes) or len(main[1])!=20:
            return result
        sidecar=proof._sidecar_status(connection,backup.path,destination,MESSAGES_PATH,
                                     workspace=workspace)
        if sidecar is not None:return Validation(sidecar)
        destination=Path(destination).absolute()
        if workspace is not None:workspace.checked_path(destination)
        before=proof._regular_info(destination)
        if before.st_size!=actual_size:return Validation('unsafe')
        if bound_callback is not None:bound_callback()
        request=json.dumps({'path':str(destination),'actual_size':actual_size,'manifest_size':entry.size,'digest':main[1].hex()})
        if len(request.encode())>2048:return result
        command=([sys.executable,WORKER_FLAG] if getattr(sys,'frozen',False) else
                 [sys.executable,'-I','-B',str(Path(__file__).resolve())])
        worker=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
        output=None
        expires_at=time.monotonic()+proof._work_deadline(actual_size)+10
        while output is None:
            remaining=expires_at-time.monotonic()
            if remaining<=0:return Validation('timed_out')
            try:output,_=worker.communicate(request,timeout=min(1,remaining))
            except subprocess.TimeoutExpired:
                request=None
                if bound_callback is not None:bound_callback()
                continue
        if worker.returncode or len(output)>256:return Validation('unavailable')
        payload=json.loads(output)
        code=payload.get('code') if isinstance(payload,dict) else None
        if not isinstance(code,str) or code not in OUTCOMES:return Validation('unavailable')
        if proof._identity(before)!=proof._identity(proof._regular_info(destination)):return Validation('unsafe')
        if bound_callback is not None:bound_callback()
        return Validation(code)
    except subprocess.TimeoutExpired:return Validation('timed_out')
    except (OSError,ValueError,TypeError,KeyError,AttributeError,IndexError,RecursionError,sqlite3.DatabaseError):
        return result
    finally:
        if worker is not None and worker.poll() is None:
            try:worker.kill()
            except ProcessLookupError:pass
            try:worker.communicate(timeout=.5)
            except subprocess.TimeoutExpired:pass

def _validate(request):
    if not isinstance(request,dict) or set(request)!={'path','actual_size','manifest_size','digest'}:return 'unsafe'
    actual,expected,digest=request['actual_size'],request['manifest_size'],request['digest']
    if type(actual) is not int or not 512<=actual<expected or type(expected) is not int or expected>=2**63:return 'unsafe'
    if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{40}',digest):return 'unsafe'
    path=Path(request['path'])
    if not proof._frozen_owned_path(path):return 'unsafe'
    before=proof._regular_info(path)
    if before.st_size!=actual:return 'unsafe'
    sidecar=proof._copied_sidecar_status(path)
    if sidecar is not None:return sidecar
    deadline=time.monotonic()+proof._work_deadline(actual)
    with path.open('rb') as stream:
        raw=stream.read(100)
        if len(raw)!=100 or raw[:16]!=b'SQLite format 3\0':return 'structural_invalid'
        page=int.from_bytes(raw[16:18],'big');page=65536 if page==1 else page
        pages=int.from_bytes(raw[28:32],'big')
        if not 512<=page<=65536 or page&(page-1) or not pages or raw[24:28]!=raw[92:96] or pages*page!=actual or expected%page:return 'structural_invalid'
        stream.seek(0);hasher=hashlib.sha1()
        while chunk:=stream.read(256*1024):
            if time.monotonic()>deadline:return 'timed_out'
            hasher.update(chunk)
    if not hmac.compare_digest(hasher.hexdigest(),digest):return 'checksum_mismatch'
    db=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True,timeout=.1)
    try:
        db.enable_load_extension(False)
        for limit,value in [(sqlite3.SQLITE_LIMIT_LENGTH,16*1024**2),(sqlite3.SQLITE_LIMIT_SQL_LENGTH,4096),
                            (sqlite3.SQLITE_LIMIT_COLUMN,512),(sqlite3.SQLITE_LIMIT_VDBE_OP,20000)]:db.setlimit(limit,value)
        db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF')
        db.execute('PRAGMA mmap_size=0');db.execute('PRAGMA cache_size=-4096')
        heap=db.execute('PRAGMA hard_heap_limit=67108864').fetchone()
        if heap is None or type(heap[0]) is not int or not 0<heap[0]<=67108864:return 'unavailable'
        db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        virtual=db.execute("SELECT sql FROM sqlite_schema WHERE type='table' AND upper(sql) LIKE 'CREATE VIRTUAL TABLE%'").fetchall()
        for (declaration,) in virtual:
            if not isinstance(declaration,str) or not re.search(r'\bUSING\s+fts[345]\s*\(',declaration,re.I):return 'unavailable'
            tokenizer=re.search(r'\btokenize\s*=\s*[\"\']?([A-Za-z_][A-Za-z0-9_]*)',declaration,re.I)
            if tokenizer is not None and tokenizer.group(1).casefold() not in {'simple','porter','unicode61','ascii','trigram'}:return 'unsupported_tokenizer'
        catalog=db.execute('SELECT type,rootpage,name FROM sqlite_schema').fetchall()
        names={name for kind,root,name in catalog if kind=='table'}
        required={'message':{'date','service','is_from_me','handle_id'},'handle':{'id'}}
        if {'chat_message_join','chat_handle_join'}<=names:
            required.update(chat_message_join={'message_id','chat_id'},chat_handle_join={'chat_id','handle_id'})
        def authorize(action,one,two,_database,_trigger):
            if action==sqlite3.SQLITE_SELECT:return sqlite3.SQLITE_OK
            if action==sqlite3.SQLITE_READ and one in {'sqlite_master','sqlite_schema','sqlite_temp_master','sqlite_temp_schema'}:return sqlite3.SQLITE_OK
            if action==sqlite3.SQLITE_PRAGMA:
                if one=='integrity_check' and two=='1':return sqlite3.SQLITE_OK
                if one=='page_count' and two is None:return sqlite3.SQLITE_OK
                if one in {'table_list','table_xinfo'} and two in required:return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        db.set_authorizer(authorize)
        for table,columns in required.items():
            kinds=db.execute('PRAGMA table_list('+table+')').fetchmany(2)
            if len(kinds)!=1 or kinds[0][0]!='main' or kinds[0][2]!='table' or kinds[0][4]!=0:return 'structural_invalid'
            fields=db.execute('PRAGMA table_xinfo('+table+')').fetchall()
            if not columns<={row[1] for row in fields if row[6]==0}:return 'structural_invalid'
            if table in {'message','handle'}:
                explicit=next((row for row in fields if row[1].casefold()=='rowid'),None)
                if explicit is not None and (explicit[2].upper()!='INTEGER' or explicit[5]!=1 or explicit[6]!=0 or sum(bool(row[5]) for row in fields)!=1):return 'structural_invalid'
        # Check the whole ordinary SQLite page/index structure, not merely the
        # header or required tables. This does not certify virtual-cache logic.
        if db.execute('PRAGMA integrity_check(1)').fetchmany(2)!=[('ok',)]:return 'structural_invalid'
        if db.execute('PRAGMA page_count').fetchone()[0]!=pages:return 'structural_invalid'
    finally:db.close()
    if time.monotonic()>deadline:return 'timed_out'
    return 'validated_messages_database' if proof._identity(before)==proof._identity(proof._regular_info(path)) else 'unsafe'

def worker_main():
    def deny(event,args):
        if event.startswith('socket.'):raise PermissionError()
        if event=='open' and isinstance(args[2],int) and args[2]&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):raise PermissionError()
    sys.addaudithook(deny)
    code='unsafe'
    try:code=_validate(json.loads(sys.stdin.read(2049)))
    except sqlite3.DatabaseError as error:
        number=getattr(error,'sqlite_errorcode',0)&255
        missing=number==sqlite3.SQLITE_ERROR and any(phrase in str(error).casefold() for phrase in ('unknown tokenizer','no such tokenizer','unrecognized tokenizer'))
        code='timed_out' if number==sqlite3.SQLITE_INTERRUPT else 'unsupported_tokenizer' if missing else 'unavailable' if number==sqlite3.SQLITE_NOMEM else 'structural_invalid'
    except Exception:pass
    print(json.dumps({'code':code if code in OUTCOMES else 'unsafe'}))
    return 0

if __name__=='__main__':raise SystemExit(worker_main())
