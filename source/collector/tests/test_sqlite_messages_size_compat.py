"""Fictional Messages exports only; no phone or provider connections."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from packaging.version import Version
from pyiosbackup import Backup
from pyiosbackup.manifest_dbs.sqlite3 import ManifestDbSqlite3
import test_sqlite_size_compat as contact_fixtures
from amplifai_phone.ios_backup import DATABASES,_extract_database
from amplifai_phone.metadata import RETAINED_HISTORY_START,read_messages,UnsupportedSchema
try:
    from amplifai_phone.metadata import SelectedPayloadIntegrityError
except ImportError:
    SelectedPayloadIntegrityError = UnsupportedSchema  # rc3 preserves its generic error contract.
from amplifai_phone import sqlite_messages_size_compat as compat

class MessagesCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='messages-compat-fictional-')
        self.root=Path(temporary.name).resolve();self.addCleanup(temporary.cleanup)
        previous=os.devnull;os.devnull=str(self.root/'worker-sink');Path(os.devnull).touch()
        self.addCleanup(setattr,os,'devnull',previous)
    def database(self):
        path=self.root/'fictional.sqlite'
        with sqlite3.connect(path) as db:
            db.executescript('PRAGMA page_size=4096; CREATE TABLE message(date INTEGER,service TEXT,is_from_me INTEGER,handle_id INTEGER,text TEXT); CREATE TABLE handle(id TEXT);')
            db.execute('INSERT INTO handle VALUES (?)',('+12025550110',))
            db.execute('INSERT INTO message VALUES (?,?,?,?,?)',(100000,'SMS',1,1,'FICTIONAL_PRIVATE_BODY_SENTINEL'))
        return path
    def backup(self,raw,*,expected=None,digest=True,canonical=True,wal=None,duplicate=False,archive_relative=None,journal=None,encrypted=False):
        expected=expected if expected is not None else len(raw)+4096
        relative=DATABASES['messages'];file_id=hashlib.sha1(('HomeDomain-'+relative).encode()).hexdigest() if canonical else hashlib.sha1(b'fictional-alias').hexdigest()
        path=self.root/file_id[:2]/file_id;path.parent.mkdir(exist_ok=True);path.write_bytes(raw)
        hashed=hashlib.sha1(raw).digest() if digest is True else None if digest is False else digest
        archive=lambda rel,size,value:contact_fixtures.SizeCompatibilityTests.archive(self,rel,size,value)
        with sqlite3.connect(self.root/'Manifest.db') as db:
            db.execute('CREATE TABLE Files(fileID TEXT PRIMARY KEY,domain TEXT,relativePath TEXT,flags INTEGER,file BLOB)')
            db.execute('INSERT INTO Files VALUES(?,?,?,?,?)',(file_id,'HomeDomain',relative,1,archive(archive_relative or relative,expected,hashed)))
            if duplicate:
                db.execute('INSERT INTO Files VALUES(?,?,?,?,?)',(hashlib.sha1(b'duplicate').hexdigest(),'HomeDomain',relative,1,archive(relative,expected,hashed)))
            for suffix,value in [('-wal',wal),('-journal',journal)]:
                if value is None:continue
                identifier=hashlib.sha1(('HomeDomain-'+relative+suffix).encode()).hexdigest()
                db.execute('INSERT INTO Files VALUES(?,?,?,?,?)',(identifier,'HomeDomain',relative+suffix,1,archive(relative+suffix,24,None)))
                if value:
                    sidecar=self.root/identifier[:2]/identifier;sidecar.parent.mkdir(exist_ok=True);sidecar.write_bytes(b'fictional sidecar')
        manifest=ManifestDbSqlite3(self.root/'Manifest.db');self.addCleanup(manifest._conn.close)
        return Backup(self.root,manifest,SimpleNamespace(is_encrypted=encrypted,product_version=Version('18.0')),
                      {'SnapshotState':'finished'},{'Product Version':'18.0'},None)
    def extract(self,backup):
        samples=[]
        accepted=_extract_database(backup,DATABASES['messages'],self.root/'copy',payload_diagnostic_callback=samples.append)
        return accepted,samples
    def reject(self,backup,code):
        samples=[]
        with self.assertRaises(SelectedPayloadIntegrityError) as error:
            _extract_database(backup,DATABASES['messages'],self.root/'copy',payload_diagnostic_callback=samples.append)
        self.assertIn('length',str(error.exception))
        self.assertEqual(samples[-1]['size_difference_validation'],code)
    def test_checksum_backed_smaller_valid_messages_is_accepted(self):
        raw=self.database().read_bytes();backup=self.backup(raw)
        accepted,samples=self.extract(backup)
        self.assertTrue(accepted);self.assertEqual(samples[-1]['size_difference_validation'],'validated_messages_database')
        self.assertEqual(samples[-1]['size_difference_validation_scope'],'messages_database_v1')
        parsed=read_messages(self.root/'copy',RETAINED_HISTORY_START)
        self.assertEqual((parsed.rows_seen,len(parsed.records)),(1,1))
    def test_valid_compaction_preserves_normal_metadata_parser(self):
        path=self.database();before=read_messages(path,RETAINED_HISTORY_START)
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE FictionalPadding(value BLOB)')
            db.executemany('INSERT INTO FictionalPadding VALUES (?)',[(b'x'*4096,) for _ in range(64)])
            db.commit();db.execute('DELETE FROM FictionalPadding');db.commit();previous=path.stat().st_size
            db.execute('VACUUM')
        self.assertLess(path.stat().st_size,previous)
        self.assertTrue(self.extract(self.backup(path.read_bytes(),expected=previous))[0])
        self.assertEqual(read_messages(self.root/'copy',RETAINED_HISTORY_START),before)
    def test_missing_checksum_is_rejected(self):self.reject(self.backup(self.database().read_bytes(),digest=False),'insufficient_manifest')
    def test_wrong_checksum_is_rejected(self):self.reject(self.backup(self.database().read_bytes(),digest=b'x'*20),'checksum_mismatch')
    def test_noncanonical_identity_is_rejected(self):self.reject(self.backup(self.database().read_bytes(),canonical=False),'insufficient_manifest')
    def test_duplicate_identity_is_rejected(self):self.reject(self.backup(self.database().read_bytes(),duplicate=True),'insufficient_manifest')
    def test_archived_relative_path_mismatch_is_rejected(self):self.reject(self.backup(self.database().read_bytes(),archive_relative='fictional-wrong-path'),'insufficient_manifest')
    def test_declared_nonempty_wal_is_rejected(self):self.reject(self.backup(self.database().read_bytes(),wal=True),'active_wal')
    def test_declared_missing_wal_is_rejected(self):self.reject(self.backup(self.database().read_bytes(),wal=False),'active_wal')
    def test_declared_journal_is_rejected(self):self.reject(self.backup(self.database().read_bytes(),journal=True),'active_journal')
    def test_undeclared_nonempty_wal_is_rejected(self):
        backup=self.backup(self.database().read_bytes());identifier=hashlib.sha1(('HomeDomain-'+DATABASES['messages']+'-wal').encode()).hexdigest()
        path=self.root/identifier[:2]/identifier;path.parent.mkdir(exist_ok=True);path.write_bytes(b'fictional WAL')
        self.reject(backup,'active_wal')
    def test_corrupt_message_page_is_rejected_despite_matching_checksum(self):
        raw=bytearray(self.database().read_bytes());raw[4096]=255
        self.reject(self.backup(bytes(raw)),'structural_invalid')
    def test_corrupt_unused_table_is_also_rejected(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE FictionalUnused(value TEXT)');root=db.execute("SELECT rootpage FROM sqlite_schema WHERE name='FictionalUnused'").fetchone()[0]
        raw=bytearray(path.read_bytes());raw[(root-1)*4096]=255
        self.reject(self.backup(bytes(raw)),'structural_invalid')
    def test_canonical_index_inconsistency_is_rejected(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.execute('CREATE INDEX FictionalSender ON handle(id)');root=db.execute("SELECT rootpage FROM sqlite_schema WHERE name='FictionalSender'").fetchone()[0]
        raw=bytearray(path.read_bytes());offset=raw.find(b'+12025550110',(root-1)*4096,root*4096);self.assertGreaterEqual(offset,0);raw[offset]=ord('!')
        self.reject(self.backup(bytes(raw)),'structural_invalid')
    def test_header_matching_truncation_still_rejects(self):
        raw=bytearray(self.database().read_bytes()[:-4096]);raw[28:32]=(len(raw)//4096).to_bytes(4,'big')
        self.reject(self.backup(bytes(raw)),'structural_invalid')
    def test_unsupported_schema_is_rejected(self):
        path=self.database()
        with sqlite3.connect(path) as db:db.execute('ALTER TABLE message RENAME TO FictionalWrongTable')
        self.reject(self.backup(path.read_bytes()),'structural_invalid')
    def test_optional_join_schema_is_checked_when_used(self):
        path=self.database()
        with sqlite3.connect(path) as db:db.executescript('CREATE TABLE chat_message_join(message_id INTEGER);CREATE TABLE chat_handle_join(chat_id INTEGER,handle_id INTEGER);')
        self.reject(self.backup(path.read_bytes()),'structural_invalid')
    def test_unknown_virtual_table_dependency_is_not_bypassed(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.execute('CREATE VIRTUAL TABLE FictionalSearch USING fts4(text)');db.execute('PRAGMA writable_schema=ON')
            db.execute("UPDATE sqlite_schema SET sql=replace(sql,'fts4','fictional_unavailable_module') WHERE name='FictionalSearch'");db.execute('PRAGMA writable_schema=OFF')
        self.reject(self.backup(path.read_bytes()),'unavailable')
    def test_unknown_cache_tokenizer_is_not_replaced(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.execute('CREATE VIRTUAL TABLE FictionalSearch USING fts4(text,tokenize=simple)')
            db.execute('PRAGMA writable_schema=ON')
            db.execute("UPDATE sqlite_schema SET sql=replace(sql,'tokenize=simple','tokenize=fictional_unavailable') WHERE name='FictionalSearch'")
            db.execute('PRAGMA writable_schema=OFF')
        self.reject(self.backup(path.read_bytes()),'unsupported_tokenizer')
    def test_large_size_does_not_allocate_fixture(self):
        request={'path':str(self.root/'not-created'),'actual_size':1024**3+4096,'manifest_size':2**31,'digest':'0'*40}
        with self.assertRaises(FileNotFoundError):compat._validate(request)
        self.assertFalse((self.root/'not-created').exists())
    def test_worker_timeout_is_killed_and_reaped(self):
        backup=self.backup(self.database().read_bytes())
        class Hung:
            returncode=None
            def communicate(self,*args,**kwargs):
                if self.returncode is None:raise subprocess.TimeoutExpired('fictional',.1)
                return ('','')
            def poll(self):return self.returncode
            def kill(self):self.returncode=-9
        worker=Hung()
        with patch.object(compat.subprocess,'Popen',return_value=worker), \
             patch.object(compat.proof,'_work_deadline',return_value=-11):
            self.reject(backup,'timed_out')
        self.assertEqual(worker.returncode,-9)
    def test_contact_success_cannot_authorize_messages_size_difference(self):
        backup=self.backup(self.database().read_bytes())
        class WrongWorker:
            returncode=0
            def communicate(self,*args,**kwargs):return (json.dumps({'code':'validated_contacts_tables'}),'')
            def poll(self):return 0
        with patch.object(compat.subprocess,'Popen',return_value=WrongWorker()):self.reject(backup,'unavailable')
    def test_worker_dispatch_needs_no_device_sdk(self):
        entry=Path(__file__).resolve().parents[1]/'agent_entry.py'
        ran=subprocess.run([sys.executable,'-B',str(entry),compat.WORKER_FLAG],input='{}',capture_output=True,text=True,timeout=5)
        self.assertEqual(ran.returncode,0);self.assertEqual(json.loads(ran.stdout),{'code':'unsafe'})
    def test_no_record_body_or_payload_hash_in_diagnostics(self):
        raw=self.database().read_bytes();accepted,samples=self.extract(self.backup(raw))
        self.assertTrue(accepted);serialized=json.dumps(samples)
        self.assertNotIn('FICTIONAL_PRIVATE_BODY_SENTINEL',serialized);self.assertNotIn(hashlib.sha1(raw).hexdigest(),serialized)
    def test_exact_size_path_does_not_use_new_exception(self):
        raw=self.database().read_bytes();accepted,samples=self.extract(self.backup(raw,expected=len(raw)))
        self.assertTrue(accepted);self.assertNotIn('size_difference_validation',samples[-1])
    def test_encrypted_difference_never_starts_validation_worker(self):
        raw=self.database().read_bytes();backup=self.backup(raw,encrypted=True)
        target=self.root/'copy';target.write_bytes(raw)
        entry=backup.get_entry_by_domain_and_path('HomeDomain',DATABASES['messages'])
        with patch.object(compat.subprocess,'Popen') as worker:
            result=compat.validate_messages_size_difference(backup,entry,target,len(raw))
        self.assertFalse(result.accepted);self.assertEqual(result.code,'insufficient_manifest');worker.assert_not_called()
    def test_unaligned_declared_size_is_rejected(self):
        raw=self.database().read_bytes();self.reject(self.backup(raw,expected=len(raw)+1),'structural_invalid')
    def test_copied_nonempty_wal_cannot_be_ignored(self):
        raw=self.database().read_bytes();backup=self.backup(raw);(self.root/'copy-wal').write_bytes(b'fictional WAL')
        self.reject(backup,'active_wal')
    def test_undeclared_nonempty_journal_cannot_be_ignored(self):
        backup=self.backup(self.database().read_bytes());identifier=hashlib.sha1(('HomeDomain-'+DATABASES['messages']+'-journal').encode()).hexdigest()
        path=self.root/identifier[:2]/identifier;path.parent.mkdir(exist_ok=True);path.write_bytes(b'fictional journal')
        self.reject(backup,'active_journal')
    def test_undeclared_nonempty_shm_cannot_be_ignored(self):
        backup=self.backup(self.database().read_bytes());identifier=hashlib.sha1(('HomeDomain-'+DATABASES['messages']+'-shm').encode()).hexdigest()
        path=self.root/identifier[:2]/identifier;path.parent.mkdir(exist_ok=True);path.write_bytes(b'fictional shm')
        self.reject(backup,'active_shm')
    def test_schema_view_cannot_replace_canonical_messages_table(self):
        path=self.database()
        with sqlite3.connect(path) as db:db.executescript('ALTER TABLE message RENAME TO FictionalBase;CREATE VIEW message AS SELECT * FROM FictionalBase;')
        self.reject(self.backup(path.read_bytes()),'structural_invalid')
    def test_destination_change_after_worker_result_is_rejected(self):
        raw=self.database().read_bytes();backup=self.backup(raw);target=self.root/'copy'
        class ChangedWorker:
            returncode=0
            def communicate(self,*args,**kwargs):
                replacement=target.with_name('replacement');replacement.write_bytes(raw);replacement.replace(target)
                return (json.dumps({'code':'validated_messages_database'}),'')
            def poll(self):return 0
        with patch.object(compat.subprocess,'Popen',return_value=ChangedWorker()):self.reject(backup,'unsafe')
