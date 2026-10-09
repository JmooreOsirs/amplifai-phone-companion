"""Fictional backups exercising accepted exports and failed validation."""
import hashlib
import json
import os
import plistlib
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

from amplifai_phone.ios_backup import DATABASES, _extract_database
from amplifai_phone.metadata import read_contacts, UnsupportedSchema
try:
    from amplifai_phone.metadata import SelectedPayloadIntegrityError
except ImportError:
    SelectedPayloadIntegrityError = UnsupportedSchema  # rc3 preserves its generic error contract.
from amplifai_phone import sqlite_size_compat as compat


class SizeCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='size-compat-fictional-')
        self.root = Path(temporary.name).resolve()
        self.addCleanup(temporary.cleanup)
        previous = os.devnull
        os.devnull = str(self.root/'worker-sink')
        Path(os.devnull).touch()
        self.addCleanup(setattr, os, 'devnull', previous)

    def database(self):
        path = self.root/'fictional.sqlite'
        with sqlite3.connect(path) as db:
            db.executescript('PRAGMA page_size=4096; CREATE TABLE ABPerson(First TEXT,Last TEXT); CREATE TABLE ABMultiValue(record_id INTEGER,property INTEGER,value TEXT);')
            db.executemany('INSERT INTO ABPerson VALUES (?,?)',[('Fictional',str(i)) for i in range(3)])
        return path

    def archive(self, relative, size, digest):
        objects = ['$null', {'$class':plistlib.UID(3),'RelativePath':plistlib.UID(2),
                            'LastModified':0,'LastStatusChange':0,'Birth':0,'Size':size,
                            'Mode':0o100600,'GroupID':0,'UserID':0}, relative,
                   {'$classname':'MBFile','$classes':['MBFile','NSObject']}]
        if digest is not None:
            objects[1]['Digest'] = plistlib.UID(4)
            objects.extend([{'$class':plistlib.UID(5),'NS.data':digest},
                            {'$classname':'NSData','$classes':['NSData','NSObject']}])
        return plistlib.dumps({'$archiver':'NSKeyedArchiver','$version':100000,
                              '$top':{'root':plistlib.UID(1)},'$objects':objects},fmt=plistlib.FMT_BINARY)

    def backup(self, raw, expected=None, *, digest=True, canonical=True, wal=None, duplicate=False, archive_relative=None):
        expected = expected if expected is not None else len(raw)+4096
        relative = DATABASES['contacts']
        file_id = hashlib.sha1(('HomeDomain-'+relative).encode()).hexdigest() if canonical else hashlib.sha1(b'nonstandard').hexdigest()
        target = self.root/file_id[:2]/file_id
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(raw)
        content_digest = hashlib.sha1(raw).digest() if digest is True else None if digest is False else digest
        with sqlite3.connect(self.root/'Manifest.db') as db:
            db.execute('CREATE TABLE Files(fileID TEXT PRIMARY KEY,domain TEXT,relativePath TEXT,flags INTEGER,file BLOB)')
            db.execute('INSERT INTO Files VALUES(?,?,?,?,?)',(file_id,'HomeDomain',relative,1,self.archive(archive_relative or relative,expected,content_digest)))
            if duplicate:
                db.execute('INSERT INTO Files VALUES(?,?,?,?,?)',(hashlib.sha1(b'alias').hexdigest(),'HomeDomain',relative,1,self.archive(relative,expected,content_digest)))
            if wal is not None:
                wal_id = hashlib.sha1(('HomeDomain-'+relative+'-wal').encode()).hexdigest()
                db.execute('INSERT INTO Files VALUES(?,?,?,?,?)',(wal_id,'HomeDomain',relative+'-wal',1,self.archive(relative+'-wal',24,None)))
                if wal:
                    path = self.root/wal_id[:2]/wal_id
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(b'fictional WAL data bytes')
        manifest = ManifestDbSqlite3(self.root/'Manifest.db')
        self.addCleanup(manifest._conn.close)
        return Backup(self.root,manifest,SimpleNamespace(is_encrypted=False,product_version=Version('18.0')),
                      {'SnapshotState':'finished'},{'Product Version':'18.0'},None)

    def reject(self, backup, validation=None):
        samples=[]
        with self.assertRaises(SelectedPayloadIntegrityError) as error:
            _extract_database(backup,DATABASES['contacts'],self.root/'copy',payload_diagnostic_callback=samples.append)
        self.assertIn('length',str(error.exception))
        if validation is not None:
            self.assertEqual(samples[-1]['size_difference_validation'],validation)

    def test_live_data_preserving_compaction_is_accepted_and_contacts_parser_works(self):
        path = self.database()
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE fictional_padding(payload BLOB)')
            db.executemany('INSERT INTO fictional_padding VALUES (?)',[(b'x'*8192,) for _ in range(64)])
            db.commit()
            db.execute('DELETE FROM fictional_padding')
            db.commit()
            old_size = path.stat().st_size
            before = tuple(db.iterdump())
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('VACUUM')
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            self.assertEqual(tuple(db.iterdump()),before)
        raw = path.read_bytes()
        self.assertLess(len(raw),old_size)
        backup = self.backup(raw,old_size)
        samples=[]
        self.assertTrue(_extract_database(backup,DATABASES['contacts'],self.root/'copy',payload_diagnostic_callback=samples.append))
        self.assertEqual(samples[-1]['size_difference_validation'],'validated_contacts_tables')
        self.assertEqual(samples[-1]['size_difference_validation_scope'],'contacts_tables_v1')
        result = read_contacts(self.root/'copy')
        self.assertEqual(result.rows_seen,3)
        self.assertEqual(len(result.records),3)

    def test_missing_producer_digest_keeps_length_rejection(self):
        self.reject(self.backup(self.database().read_bytes(),digest=False),'insufficient_manifest')

    def test_wrong_producer_digest_keeps_length_rejection(self):
        self.reject(self.backup(self.database().read_bytes(),digest=b'x'*20),'checksum_mismatch')

    def test_matching_digest_does_not_accept_corrupt_application_page(self):
        path=self.database()
        raw=bytearray(path.read_bytes())
        raw[4096]=255
        self.reject(self.backup(bytes(raw)),'structural_invalid')

    def test_supported_populated_derived_fts_does_not_affect_contacts_scope(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.execute('CREATE VIRTUAL TABLE ABPersonFullTextSearch USING fts4(text,tokenize=porter)')
            db.execute('INSERT INTO ABPersonFullTextSearch VALUES (?)',('Fictional running words',))
        backup=self.backup(path.read_bytes())
        samples=[]
        self.assertTrue(_extract_database(backup,DATABASES['contacts'],self.root/'copy',payload_diagnostic_callback=samples.append))
        self.assertEqual(samples[-1]['size_difference_validation'],'validated_contacts_tables')

    def test_derived_fts_content_index_inconsistency_is_explicitly_out_of_scope(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.execute('CREATE VIRTUAL TABLE ABPersonFullTextSearch USING fts4(text,tokenize=porter)')
            db.execute('INSERT INTO ABPersonFullTextSearch VALUES (?)',('Fictional running words',))
            db.execute('UPDATE ABPersonFullTextSearch_content SET c0text=?',('Fictional replacement text',))
        with sqlite3.connect(path) as db:
            self.assertNotEqual(db.execute('PRAGMA integrity_check(1)').fetchall(),[('ok',)])
        backup=self.backup(path.read_bytes())
        samples=[]
        self.assertTrue(_extract_database(backup,DATABASES['contacts'],self.root/'copy',payload_diagnostic_callback=samples.append))
        self.assertEqual(samples[-1]['size_difference_validation'],'validated_contacts_tables')
        self.assertEqual(len(read_contacts(self.root/'copy').records),3)

    def test_missing_apple_tokenizer_in_populated_unused_cache_needs_no_substitute(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.execute('CREATE VIRTUAL TABLE ABPersonFullTextSearch USING fts4(text,tokenize=porter)')
            db.execute('INSERT INTO ABPersonFullTextSearch VALUES (?)',('Fictional running words',))
            db.execute('PRAGMA writable_schema=ON')
            db.execute('UPDATE sqlite_schema SET sql=replace(sql,?,?) WHERE name=?',
                       ('tokenize=porter','tokenize=ab_cf_tokenizer','ABPersonFullTextSearch'))
            version=db.execute('PRAGMA schema_version').fetchone()[0]
            db.execute('PRAGMA schema_version='+str(version+1))
            db.execute('PRAGMA writable_schema=OFF')
        backup=self.backup(path.read_bytes())
        samples=[]
        self.assertTrue(_extract_database(backup,DATABASES['contacts'],self.root/'copy',payload_diagnostic_callback=samples.append))
        self.assertEqual(samples[-1]['size_difference_validation'],'validated_contacts_tables')
        result=read_contacts(self.root/'copy')
        self.assertEqual(len(result.records),3)

    def test_empty_unsupported_cache_needs_no_substitute_tokenizer(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.execute('CREATE VIRTUAL TABLE ABPersonFullTextSearch USING fts4(text,tokenize=porter)')
            db.execute('PRAGMA writable_schema=ON')
            db.execute('UPDATE sqlite_schema SET sql=replace(sql,?,?) WHERE name=?',
                       ('tokenize=porter','tokenize=ab_cf_tokenizer','ABPersonFullTextSearch'))
            version=db.execute('PRAGMA schema_version').fetchone()[0]
            db.execute('PRAGMA schema_version='+str(version+1))
            db.execute('PRAGMA writable_schema=OFF')
        backup=self.backup(path.read_bytes())
        samples=[]
        self.assertTrue(_extract_database(backup,DATABASES['contacts'],self.root/'copy',payload_diagnostic_callback=samples.append))
        self.assertEqual(samples[-1]['size_difference_validation'],'validated_contacts_tables')

    def test_unrecognized_virtual_table_is_never_enabled(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.execute('CREATE VIRTUAL TABLE FictionalUnsupportedCache USING fts4(text)')
        backup=self.backup(path.read_bytes())
        samples=[]
        with self.assertRaises(SelectedPayloadIntegrityError):
            _extract_database(backup,DATABASES['contacts'],self.root/'copy',payload_diagnostic_callback=samples.append)
        self.assertEqual(samples[-1]['size_difference_validation'],'unavailable')

    def test_canonical_multivalue_page_corruption_is_rejected_with_matching_digest(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            root=db.execute("SELECT rootpage FROM sqlite_schema WHERE name='ABMultiValue'").fetchone()[0]
        raw=bytearray(path.read_bytes())
        raw[(root-1)*4096]=255
        self.reject(self.backup(bytes(raw)),'structural_invalid')

    def test_canonical_index_record_mismatch_is_rejected(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.execute('CREATE INDEX FictionalPersonName ON ABPerson(First)')
            root=db.execute("SELECT rootpage FROM sqlite_schema WHERE name='FictionalPersonName'").fetchone()[0]
        raw=bytearray(path.read_bytes())
        start=(root-1)*4096
        index=raw.find(b'Fictional',start,start+4096)
        self.assertGreaterEqual(index,start)
        raw[index]=ord('D')
        self.reject(self.backup(bytes(raw)),'structural_invalid')

    def test_orphan_parser_consumed_owner_is_rejected(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.execute('INSERT INTO ABMultiValue VALUES (?,?,?)',(999,3,'fictional-phone-value'))
        backup=self.backup(path.read_bytes())
        samples=[]
        with self.assertRaises(SelectedPayloadIntegrityError):
            _extract_database(backup,DATABASES['contacts'],self.root/'copy',payload_diagnostic_callback=samples.append)
        self.assertEqual(samples[-1]['size_difference_validation'],'contacts_relationship_invalid')

    def test_orphan_unconsumed_property_does_not_change_parser_scope(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.execute('INSERT INTO ABMultiValue VALUES (?,?,?)',(999,8,'fictional-unused-address'))
        backup=self.backup(path.read_bytes())
        self.assertTrue(_extract_database(backup,DATABASES['contacts'],self.root/'copy'))
        self.assertEqual(len(read_contacts(self.root/'copy').records),3)

    def test_blob_person_name_is_rejected_before_silent_parser_exclusion(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.execute('UPDATE ABPerson SET First=?,Last=NULL WHERE ROWID=1',(b'fictional binary name',))
        backup=self.backup(path.read_bytes())
        samples=[]
        with self.assertRaises(SelectedPayloadIntegrityError):
            _extract_database(backup,DATABASES['contacts'],self.root/'copy',payload_diagnostic_callback=samples.append)
        self.assertEqual(samples[-1]['size_difference_validation'],'contacts_types_invalid')

    def test_blob_consumed_value_is_rejected(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.execute('INSERT INTO ABMultiValue VALUES (?,?,?)',(1,3,b'fictional binary phone'))
        backup=self.backup(path.read_bytes())
        samples=[]
        with self.assertRaises(SelectedPayloadIntegrityError):
            _extract_database(backup,DATABASES['contacts'],self.root/'copy',payload_diagnostic_callback=samples.append)
        self.assertEqual(samples[-1]['size_difference_validation'],'contacts_types_invalid')

    def test_text_property_cannot_silently_bypass_numeric_parser_branch(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.executescript('DROP TABLE ABMultiValue; CREATE TABLE ABMultiValue(record_id INTEGER,property TEXT,value TEXT);')
            db.execute('INSERT INTO ABMultiValue VALUES (?,?,?)',(1,'3','fictional-phone-value'))
        backup=self.backup(path.read_bytes())
        samples=[]
        with self.assertRaises(SelectedPayloadIntegrityError):
            _extract_database(backup,DATABASES['contacts'],self.root/'copy',payload_diagnostic_callback=samples.append)
        self.assertEqual(samples[-1]['size_difference_validation'],'contacts_types_invalid')

    def test_bad_utf8_canonical_text_never_produces_partial_contacts(self):
        path=self.database()
        raw=bytearray(path.read_bytes())
        start=raw.find(b'Fictional',4096)
        self.assertGreaterEqual(start,4096)
        raw[start]=255
        backup=self.backup(bytes(raw))
        self.assertTrue(_extract_database(backup,DATABASES['contacts'],self.root/'copy'))
        # Structural/type checks are not a text decoder. The unchanged parser
        # fails the source atomically when UTF-8 decoding fails.
        with self.assertRaises(sqlite3.DatabaseError):
            read_contacts(self.root/'copy')

    def test_legacy_unscoped_worker_status_cannot_authorize_length_difference(self):
        backup=self.backup(self.database().read_bytes())
        worker=SimpleNamespace(returncode=0,communicate=lambda *args,**kwargs:('{"code":"validated"}',None),poll=lambda:0)
        samples=[]
        with patch.object(compat.subprocess,'Popen',return_value=worker):
            with self.assertRaises(SelectedPayloadIntegrityError):
                _extract_database(backup,DATABASES['contacts'],self.root/'copy',payload_diagnostic_callback=samples.append)
        self.assertEqual(samples[-1]['size_difference_validation'],'unavailable')

    def test_canonical_view_cannot_substitute_for_person_table(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.executescript('ALTER TABLE ABPerson RENAME TO FictionalUnderlying; CREATE VIEW ABPerson AS SELECT rowid,First,Last FROM FictionalUnderlying;')
        self.reject(self.backup(path.read_bytes()))

    def test_generated_parser_field_is_rejected(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.executescript("DROP TABLE ABPerson; CREATE TABLE ABPerson(base TEXT,First TEXT GENERATED ALWAYS AS (upper(base)),Last TEXT); INSERT INTO ABPerson(base,Last) VALUES ('fictional','example');")
        self.reject(self.backup(path.read_bytes()))

    def test_shadowed_rowid_identity_is_rejected(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.executescript("DROP TABLE ABPerson; CREATE TABLE ABPerson(ROWID TEXT,First TEXT,Last TEXT); INSERT INTO ABPerson VALUES ('fictional-id','Fictional','Example');")
        self.reject(self.backup(path.read_bytes()))

    def test_scoped_contacts_have_no_artificial_row_frontier(self):
        path=self.database()
        raw=path.read_bytes()
        request={'path':str(path),'actual_size':len(raw),'manifest_size':len(raw)+4096,
                 'digest':hashlib.sha1(raw).hexdigest()}
        self.assertEqual(compat._validate(request),'validated_contacts_tables')

    def test_bad_unused_derived_page_is_out_of_scope_but_contacts_remain_readable(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.execute('CREATE VIRTUAL TABLE ABPersonFullTextSearch USING fts4(text)')
            db.execute('INSERT INTO ABPersonFullTextSearch VALUES (?)',('Fictional searchable text',))
            root=db.execute("SELECT rootpage FROM sqlite_schema WHERE name='ABPersonFullTextSearch_content'").fetchone()[0]
        raw=bytearray(path.read_bytes())
        raw[(root-1)*4096]=255
        path.write_bytes(raw)
        with sqlite3.connect(path) as db:
            try:failed=db.execute('PRAGMA integrity_check(1)').fetchall()!=[('ok',)]
            except sqlite3.DatabaseError:failed=True
        self.assertTrue(failed)
        backup=self.backup(bytes(raw))
        self.assertTrue(_extract_database(backup,DATABASES['contacts'],self.root/'copy'))
        self.assertEqual(len(read_contacts(self.root/'copy').records),3)

    def test_unused_page_change_with_original_producer_digest_is_rejected(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.execute('CREATE VIRTUAL TABLE ABPersonFullTextSearch USING fts4(text)')
            db.execute('INSERT INTO ABPersonFullTextSearch VALUES (?)',('Fictional searchable text',))
        original=path.read_bytes()
        raw=bytearray(original)
        raw[-1]^=1
        backup=self.backup(bytes(raw),digest=hashlib.sha1(original).digest())
        samples=[]
        with self.assertRaises(SelectedPayloadIntegrityError):
            _extract_database(backup,DATABASES['contacts'],self.root/'copy',payload_diagnostic_callback=samples.append)
        self.assertEqual(samples[-1]['size_difference_validation'],'checksum_mismatch')

    def test_catalog_root_outside_physical_file_is_rejected(self):
        path=self.database()
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE FictionalUnused(value TEXT)')
            db.execute('PRAGMA writable_schema=ON')
            db.execute("UPDATE sqlite_schema SET rootpage=99999 WHERE name='FictionalUnused'")
            db.execute('PRAGMA writable_schema=OFF')
        self.reject(self.backup(path.read_bytes()))

    def test_matching_digest_does_not_accept_short_file_with_adjusted_header(self):
        path=self.database()
        raw=bytearray(path.read_bytes()[:8192])
        raw[28:32]=(2).to_bytes(4,'big')
        self.reject(self.backup(bytes(raw),12288))

    def test_declared_nonempty_wal_is_never_waived(self):
        self.reject(self.backup(self.database().read_bytes(),wal=True),'active_wal')

    def test_declared_missing_wal_is_never_waived(self):
        self.reject(self.backup(self.database().read_bytes(),wal=False),'active_wal')

    def test_undeclared_nonempty_wal_is_never_waived(self):
        backup=self.backup(self.database().read_bytes())
        wal_id=hashlib.sha1(('HomeDomain-'+DATABASES['contacts']+'-wal').encode()).hexdigest()
        path=self.root/wal_id[:2]/wal_id
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b'fictional WAL')
        self.reject(backup,'active_wal')

    def test_unlisted_nonempty_journal_or_shm_is_never_waived(self):
        backup=self.backup(self.database().read_bytes())
        for suffix, code in (('-journal', 'active_journal'), ('-shm', 'active_shm')):
            with self.subTest(suffix=suffix):
                file_id=hashlib.sha1(('HomeDomain-'+DATABASES['contacts']+suffix).encode()).hexdigest()
                path=self.root/file_id[:2]/file_id
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(b'fictional sidecar')
                self.reject(backup,code)
                path.unlink()
                (self.root/'copy').unlink()

    def test_manifest_listed_missing_shm_is_never_waived(self):
        backup=self.backup(self.database().read_bytes())
        relative=DATABASES['contacts']+'-shm'
        file_id=hashlib.sha1(('HomeDomain-'+relative).encode()).hexdigest()
        backup._manifest_db._conn.execute('INSERT INTO Files VALUES(?,?,?,?,?)',
            (file_id,'HomeDomain',relative,1,self.archive(relative,0,None)))
        self.reject(backup,'active_shm')

    def test_nonempty_copied_sidecar_is_never_waived(self):
        backup=self.backup(self.database().read_bytes())
        (self.root/'copy-shm').write_bytes(b'fictional stale sidecar')
        self.reject(backup,'active_shm')

    def test_duplicate_manifest_rows_are_never_waived(self):
        self.reject(self.backup(self.database().read_bytes(),duplicate=True),'insufficient_manifest')

    def test_nonstandard_identity_is_never_waived(self):
        self.reject(self.backup(self.database().read_bytes(),canonical=False),'insufficient_manifest')

    def test_inconsistent_archived_relative_path_is_never_waived(self):
        self.reject(self.backup(self.database().read_bytes(),archive_relative='fictional-other-path'),'insufficient_manifest')

    def test_declared_smaller_size_is_never_waived(self):
        raw=self.database().read_bytes()
        self.reject(self.backup(raw,len(raw)-4096))

    def test_unaligned_declared_size_is_never_waived(self):
        raw=self.database().read_bytes()
        self.reject(self.backup(raw,len(raw)+1))

    def test_bounded_worker_timeout_is_rejected_and_worker_reaped(self):
        backup=self.backup(self.database().read_bytes())
        entry=backup.get_entry_by_domain_and_path('HomeDomain',DATABASES['contacts'])
        destination=self.root/'copy'
        destination.write_bytes(entry.read_bytes())
        class Slow:
            returncode=None
            killed=False
            def communicate(self,*args,**kwargs):
                if self.killed:return ('','')
                raise subprocess.TimeoutExpired('fictional worker',kwargs.get('timeout'))
            def poll(self):return self.returncode
            def kill(self):self.killed=True;self.returncode=-9
        worker=Slow()
        with patch.object(compat.subprocess,'Popen',return_value=worker), \
             patch.object(compat,'_work_deadline',return_value=-11):
            result=compat.validate_contacts_size_difference(backup,entry,destination,destination.stat().st_size)
        self.assertEqual(result.code,'timed_out')
        self.assertTrue(worker.killed)

    def test_symlink_validation_destination_is_rejected(self):
        backup=self.backup(self.database().read_bytes())
        entry=backup.get_entry_by_domain_and_path('HomeDomain',DATABASES['contacts'])
        destination=self.root/'linked-copy'
        destination.symlink_to(entry.real_path)
        result=compat.validate_contacts_size_difference(backup,entry,destination,entry.real_path.stat().st_size)
        self.assertFalse(result.accepted)

    def test_worker_entry_dispatch_needs_no_device_dependencies(self):
        entry=Path(__file__).resolve().parents[1]/'agent_entry.py'
        result=subprocess.run([sys.executable,'-B',str(entry),compat.WORKER_FLAG],input='{}',capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0)
        self.assertEqual(json.loads(result.stdout),{'code':'unsafe'})

    def test_frozen_worker_accepts_only_its_parent_owned_private_session(self):
        home=self.root/'fictional-home'
        root=home/'Library/Application Support/AMPLIFai Phone Candidate/sessions'
        root.mkdir(parents=True,mode=0o700)
        (root/'.amplifai-phone-sessions-v1').write_bytes(b'AMPLIFAI_PHONE_SESSIONS_V1\n')
        session=root/('session-'+'a'*32)
        session.mkdir(mode=0o700)
        marker=session/'.amplifai-owned-session.json'
        marker.write_text(json.dumps({'version':1,'session':session.name,'pid':os.getppid(),'created_at':0}))
        path=session/'selected-copy'
        path.write_bytes(self.database().read_bytes())
        with patch.object(compat.sys,'frozen',True,create=True),patch.object(compat.Path,'home',return_value=home):
            self.assertTrue(compat._frozen_owned_path(path))
            marker.write_text(json.dumps({'version':1,'session':session.name,'pid':os.getppid()+1,'created_at':0}))
            self.assertFalse(compat._frozen_owned_path(path))

    def test_frozen_worker_rejects_unconfined_or_public_session(self):
        home=self.root/'fictional-home'
        root=home/'Library/Application Support/AMPLIFai Phone Candidate/sessions'
        root.mkdir(parents=True,mode=0o700)
        (root/'.amplifai-phone-sessions-v1').write_bytes(b'AMPLIFAI_PHONE_SESSIONS_V1\n')
        session=root/('session-'+'b'*32)
        session.mkdir(mode=0o700)
        (session/'.amplifai-owned-session.json').write_text(json.dumps({'version':1,'session':session.name,'pid':os.getppid(),'created_at':0}))
        path=session/'selected-copy'
        path.write_bytes(self.database().read_bytes())
        with patch.object(compat.sys,'frozen',True,create=True),patch.object(compat.Path,'home',return_value=home):
            with self.assertRaises(ValueError):compat._frozen_owned_path(self.root/'arbitrary-copy')
            session.chmod(0o755)
            self.assertFalse(compat._frozen_owned_path(path))

    def test_large_size_does_not_allocate_a_fixture(self):
        request={'path':str(self.root/'not-created'),'actual_size':2**30+4096,
                 'manifest_size':2**31+4096,'digest':'0'*40}
        with self.assertRaises(FileNotFoundError):
            compat._validate(request)
        self.assertFalse((self.root/'not-created').exists())
        self.assertGreater(compat._work_deadline(2**30+4096),
                           compat._work_deadline(512*1024**2))


if __name__=='__main__':
    unittest.main()
