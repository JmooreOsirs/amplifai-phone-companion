from __future__ import annotations

import copy
import json
import multiprocessing
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from amplifai_phone import source_diagnostics as diagnostics


def record(source="messages", component="database", **fields):
    value = dict(source=source, component=component, reason="selected_payload_length",
                 result="length_mismatch", declaredBytes=10, rawBytes=20, copiedBytes=20,
                 encrypted=False, entryKeyPresent=None, validationCode=None,
                 validationScope=None, producerDigest="unknown", copyDigest="unknown",
                 sidecars="unknown")
    value.update(fields)
    return value


def hold_lock(sessions, ready, release):
    history = diagnostics.DiagnosticHistory(Path(sessions))
    guard = diagnostics._windows_directory if os.name == "nt" else diagnostics._posix_directory
    with guard(history.root, False) as directory, diagnostics._locked(directory):
        ready.set()
        release.wait(10)


class DiagnosticValidationTests(unittest.TestCase):
    def test_pinned_contract_fields_and_enums_match(self):
        contract = json.loads((Path(__file__).parents[3] / 'docs/SOURCE-DIAGNOSTIC-CONTRACT-v1.json').read_text())
        self.assertEqual(set(contract['requiredRecordFields']), set(diagnostics.RECORD_FIELDS))
        for field, values in [('sources',diagnostics.SOURCES),('components',diagnostics.COMPONENTS),
                              ('reasons',diagnostics.REASONS),('results',diagnostics.RESULTS),
                              ('nullableValidationCodes',diagnostics.VALIDATION_CODES),
                              ('nullableValidationScopes',diagnostics.VALIDATION_SCOPES)]:
            self.assertEqual(set(contract[field]), values)

    def test_valid_record_is_copied_and_nullable_zero_facts_survive(self):
        value = record(declaredBytes=0, copiedBytes=None, rawBytes=0, encrypted=None)
        result = diagnostics.validate_source_diagnostics([value])
        self.assertEqual([value], result)
        result[0]['reason'] = 'absent'
        self.assertEqual('selected_payload_length', value['reason'])

    def test_unknown_private_fields_and_success_results_are_rejected(self):
        for fields in ({'path':'/private/phone.db'}, {'digest':'a'*64}, {'error':'personal error'},
                       {'result':'match'}, {'source':'phone@example.test'}, {'component':'attachment'}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                diagnostics.validate_source_diagnostics([record(**fields)])

    def test_safe_integers_nullable_bools_and_enum_types_are_strict(self):
        for field, values in [('declaredBytes',[True,-1,2**53,1.0,float('nan'),float('inf'),'123']),
                              ('encrypted',[0,1,'true']),('entryKeyPresent',[1,'false']),
                              ('validationCode',['raw error',1]),('producerDigest',['a'*64,None]),
                              ('sidecars',[True])]:
            for value in values:
                with self.subTest(field=field,value=value), self.assertRaises(ValueError):
                    diagnostics.validate_source_diagnostics([record(**{field:value})])
        diagnostics.validate_source_diagnostics([record(declaredBytes=2**53-1)])

    def test_duplicate_components_and_oversized_arrays_fail(self):
        with self.assertRaises(ValueError): diagnostics.validate_source_diagnostics([record(),record()])
        with self.assertRaises(ValueError): diagnostics.validate_source_diagnostics([record()]*13)
        self.assertEqual(12,len(diagnostics.validate_source_diagnostics([
            record(source,component) for source in diagnostics.SOURCES for component in diagnostics.COMPONENTS])))

    def test_raw_failure_sample_maps_only_safe_facts(self):
        sample = {'source':'messages','component':'journal','result':'length_mismatch','manifest_bytes':10,
                  'raw_before_bytes':20,'raw_after_bytes':21,'copied_plaintext_bytes':22,'encrypted':True,
                  'entry_key_present':False,'producer_digest_present':True,'producer_digest_matches':False,
                  'plaintext_copy_checksum_matches':True,'sidecars_absent':False,
                  'size_difference_validation':'active_journal','size_difference_validation_scope':'encrypted_messages_database_v1',
                  'path':'/private/phone.db','name':'Private Name','raw_digest':'secret-digest','error':'private error'}
        value = diagnostics.diagnostic_from_sample(sample)
        self.assertEqual(set(diagnostics.RECORD_FIELDS),set(value))
        self.assertEqual((21,22,True,False,'mismatched','matched','present'),
                         (value['rawBytes'],value['copiedBytes'],value['encrypted'],value['entryKeyPresent'],
                          value['producerDigest'],value['copyDigest'],value['sidecars']))
        self.assertNotIn('Private',json.dumps(value))
        self.assertEqual('active_journal',value['validationCode'])

    def test_success_is_not_a_failure_until_reader_supplies_fixed_reason(self):
        for result in ('copying','match','accepted_compatibility'):
            sample={'source':'calls','component':'database','result':result,'manifest_bytes':0,
                    'raw_before_bytes':0,'copied_plaintext_bytes':0,'encrypted':False,'entry_key_present':True}
            self.assertIsNone(diagnostics.diagnostic_from_sample(sample))
            value=diagnostics.diagnostic_from_sample(sample,reason='unsupported_schema')
            self.assertEqual(('unavailable','unsupported_schema',0,0,True),
                             (value['result'],value['reason'],value['declaredBytes'],value['rawBytes'],value['entryKeyPresent']))

    def test_unrecognized_or_malformed_raw_sample_returns_none(self):
        for sample in (None,[],{}, {'result':[]}, {'result':'copy_failed','source':'messages','component':'database','manifest_bytes':True},
                       {'result':'match','source':'messages','component':'database','encrypted':'false'}):
            self.assertIsNone(diagnostics.diagnostic_from_sample(sample,reason='source_invalid'))
        self.assertIsNone(diagnostics.diagnostic_from_sample({'result':'copy_failed','source':'messages','component':'database'},reason='raw error'))

    def test_merge_retains_prior_failures_and_replaces_same_component(self):
        old=record();new=record(reason='source_invalid',result='unavailable')
        self.assertEqual([old],diagnostics.merge_source_diagnostics([old],[]))
        self.assertEqual([new,record('calls')],diagnostics.merge_source_diagnostics([old],[new,record('calls')]))

    def test_history_schema_uuid_keys_duplicates_order_and_integer_bounds(self):
        older={'attemptId':str(uuid4()),'createdAt':1,'sourceDiagnostics':[record()]}
        newer={'attemptId':str(uuid4()),'createdAt':2,'sourceDiagnostics':[record('calls')]}
        self.assertEqual([newer,older],diagnostics.validate_source_diagnostic_history([older,newer]))
        bad=[]
        for field,value in [('attemptId','account-identity'),('attemptId',older['attemptId'].upper()),
                            ('createdAt',True),('createdAt',float('nan')),('createdAt',2**53),
                            ('sourceDiagnostics',[])]:
            entry=copy.deepcopy(older);entry[field]=value;bad.append([entry])
        bad.extend([[older,older],[dict(older,device='phone')],[older]*11])
        for value in bad:
            with self.subTest(value=value),self.assertRaises(ValueError):diagnostics.validate_source_diagnostic_history(value)


class DiagnosticHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='amplifai-diagnostics-test-')
        self.addCleanup(self.temporary.cleanup)
        self.parent=Path(self.temporary.name).resolve()
        self.sessions=self.parent/'sessions'
        self.history=diagnostics.DiagnosticHistory(self.sessions)
        self.identity=str(uuid4())

    def assert_storage_error(self, operation):
        with self.assertRaises(diagnostics.DiagnosticsStorageError) as caught:operation()
        self.assertEqual('diagnostics_unavailable',caught.exception.code)
        self.assertEqual('Private diagnostics are unavailable',str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertNotIn(str(self.parent),str(caught.exception))

    def test_constructor_and_missing_load_do_not_create_paths(self):
        self.assertFalse(self.history.root.exists())
        self.assertEqual([],self.history.load())
        self.assertFalse(self.history.root.exists())
        self.assertFalse(self.sessions.exists())

    def test_private_sibling_persists_across_relaunch_and_partial_success(self):
        first=self.history.record(self.identity,[record()])
        self.assertEqual(self.parent/'diagnostics',self.history.root)
        self.assertFalse(self.sessions.exists())
        relaunched=diagnostics.DiagnosticHistory(self.sessions)
        self.assertEqual(first,relaunched.load())
        self.assertEqual(first,relaunched.record(self.identity,[]))
        merged=relaunched.record(self.identity,[record('calls')])
        self.assertEqual(2,len(merged[0]['sourceDiagnostics']))
        self.assertEqual(first[0]['createdAt'],merged[0]['createdAt'])
        merged[0]['sourceDiagnostics'][0]['reason']='absent'
        self.assertEqual('selected_payload_length',relaunched.load()[0]['sourceDiagnostics'][0]['reason'])
        if os.name!='nt':
            self.assertEqual(0,self.history.root.stat().st_mode & 0o077)
            for path in self.history.root.iterdir():self.assertEqual(0,path.stat().st_mode & 0o077)

    def test_only_normalized_failure_metadata_reaches_disk(self):
        sample={'result':'copy_failed','source':'messages','component':'database','encrypted':True,
                'phoneNumber':'+15555551234','message':'secret message','path':'/private/phone.db','digest':'deadbeef'}
        self.history.record(self.identity,[diagnostics.diagnostic_from_sample(sample)])
        payload=(self.history.root/diagnostics._HISTORY).read_text()
        for private in ('15555551234','secret message','phone.db','deadbeef','phoneNumber'):
            self.assertNotIn(private,payload)
        self.assert_storage_error(lambda:self.history.record(self.identity,[record(path='/private/phone.db')]))

    def test_attempt_age_count_bytes_and_empty_success_bounds(self):
        now=2_000_000_000
        with patch.object(diagnostics.time,'time',return_value=now):
            self.history.record(self.identity,[record()],created_at=now-diagnostics.MAX_AGE_SECONDS-1)
            self.assertEqual([],self.history.load())
            for index in range(12):
                self.history.record(str(uuid4()),[record(source,component,declaredBytes=2**53-1,
                    rawBytes=2**53-1,copiedBytes=2**53-1,reason='selected_payload_file_type',
                    validationScope='encrypted_messages_database_v1',validationCode='contacts_relationship_invalid')
                    for source in diagnostics.SOURCES for component in diagnostics.COMPONENTS],created_at=now-12+index)
            entries=self.history.load()
            self.assertLessEqual(len(entries),10)
            self.assertTrue(all(len(e['sourceDiagnostics'])==12 for e in entries))
            self.assertLessEqual((self.history.root/diagnostics._HISTORY).stat().st_size,65536)
            self.assertEqual(entries,self.history.record(str(uuid4()),[]))
            self.assert_storage_error(lambda:self.history.record(str(uuid4()),[record()],created_at=now+1))
        with patch.object(diagnostics.time,'time',return_value=now+diagnostics.MAX_AGE_SECONDS+1):
            self.assertEqual([],self.history.load())
            self.assertEqual([],json.loads((self.history.root/diagnostics._HISTORY).read_text())['attempts'])

    def test_atomic_replace_failure_preserves_previous_history_and_recovers(self):
        before=self.history.record(self.identity,[record()])
        original=(self.history.root/diagnostics._HISTORY).read_bytes()
        with patch.object(diagnostics.os,'replace',side_effect=OSError('private path')):
            self.assert_storage_error(lambda:self.history.record(str(uuid4()),[record('calls')]))
        self.assertEqual(original,(self.history.root/diagnostics._HISTORY).read_bytes())
        self.assertFalse((self.history.root/diagnostics._TEMPORARY).exists())
        self.assertEqual(before,self.history.load())
        self.assertEqual(2,len(self.history.record(str(uuid4()),[record('calls')])))

    def test_fsync_failure_preserves_previous_file_and_removes_temp(self):
        self.history.record(self.identity,[record()]);before=(self.history.root/diagnostics._HISTORY).read_bytes()
        with patch.object(diagnostics.os,'fsync',side_effect=OSError('raw storage failure')):
            self.assert_storage_error(lambda:self.history.record(str(uuid4()),[record('calls')]))
        self.assertEqual(before,(self.history.root/diagnostics._HISTORY).read_bytes())
        self.assertFalse((self.history.root/diagnostics._TEMPORARY).exists())

    def test_crash_temporary_is_not_promoted_and_history_remains_available(self):
        before=self.history.record(self.identity,[record()])
        path=self.history.root/diagnostics._TEMPORARY
        guard=diagnostics._windows_directory if os.name=='nt' else diagnostics._posix_directory
        with guard(self.history.root,False) as directory:
            with os.fdopen(directory.open(diagnostics._TEMPORARY,create=True,writable=True),'wb') as stream:
                stream.write(b'{partial crash bytes')
        self.assertEqual(before,diagnostics.DiagnosticHistory(self.sessions).load())
        self.assertFalse(path.exists())

    def test_unmarked_or_broad_directory_is_never_adopted_or_repaired(self):
        self.history.root.mkdir(mode=0o700)
        self.assert_storage_error(self.history.load)
        self.assertEqual([],list(self.history.root.iterdir()))
        if os.name!='nt':
            (self.history.root/diagnostics.MARKER).write_bytes(diagnostics._MARKER_BYTES)
            (self.history.root/diagnostics.MARKER).chmod(0o600);self.history.root.chmod(0o755)
            self.assert_storage_error(lambda:self.history.record(self.identity,[record()]))
            self.assertEqual(0o755,stat.S_IMODE(self.history.root.stat().st_mode))

    def test_corrupt_oversized_unknown_or_duplicate_json_fails_closed(self):
        self.history.record(self.identity,[record()]);path=self.history.root/diagnostics._HISTORY
        for payload in (b'{',b'x'*65537,b'{"version":1,"version":1,"attempts":[]}',
                        b'{"version":true,"attempts":[]}',b'{"version":1,"attempts":[],"phone":"private"}'):
            path.write_bytes(payload)
            self.assert_storage_error(self.history.load)
            self.assertEqual(payload,path.read_bytes())

    @unittest.skipIf(os.name=='nt','POSIX ownership/mode/no-follow descriptor checks')
    def test_wrong_owner_broad_file_symlink_and_hardlink_rejected(self):
        self.history.record(self.identity,[record()]);path=self.history.root/diagnostics._HISTORY
        original=path.read_bytes();path.chmod(0o644)
        self.assert_storage_error(self.history.load);path.chmod(0o600)
        with patch.object(diagnostics.os,'geteuid',return_value=os.geteuid()+1):self.assert_storage_error(self.history.load)
        outside=self.parent/'outside';outside.write_bytes(original);outside.chmod(0o600)
        path.unlink();path.symlink_to(outside)
        self.assert_storage_error(self.history.load);self.assertEqual(original,outside.read_bytes())
        path.unlink();os.link(outside,path)
        self.assert_storage_error(self.history.load);self.assertEqual(original,outside.read_bytes())

    @unittest.skipIf(os.name=='nt','POSIX ancestor no-follow check')
    def test_symlinked_root_and_ancestor_are_rejected(self):
        target=self.parent/'target';target.mkdir(mode=0o700);self.history.root.symlink_to(target,target_is_directory=True)
        self.assert_storage_error(lambda:self.history.record(self.identity,[record()]))
        self.assertEqual([],list(target.iterdir()))
        self.history.root.unlink();link=self.parent/'link';link.symlink_to(target,target_is_directory=True)
        self.assert_storage_error(lambda:diagnostics.DiagnosticHistory(link/'product/sessions').record(self.identity,[record()]))
        self.assertEqual([],list(target.iterdir()))

    @unittest.skipIf(os.name=='nt','POSIX namespace rename race')
    def test_root_substitution_during_write_cannot_report_retained(self):
        self.history.record(self.identity,[record()])
        original=diagnostics._Directory.replace
        moved=self.parent/'moved-private-root'
        def substitute(directory):
            self.history.root.rename(moved)
            self.history.root.mkdir(mode=0o700)
            original(directory)
        with patch.object(diagnostics._Directory,'replace',substitute):
            self.assert_storage_error(lambda:self.history.record(str(uuid4()),[record('calls')]))
        self.assertEqual([],list(self.history.root.iterdir()))
        self.assertTrue((moved/diagnostics._HISTORY).is_file())

    @unittest.skipIf(os.name=='nt','POSIX marker replacement race')
    def test_marker_replacement_during_write_cannot_report_retained(self):
        self.history.record(self.identity,[record()])
        original=diagnostics._Directory.replace
        def substitute(directory):
            marker=self.history.root/diagnostics.MARKER
            marker.unlink();marker.write_bytes(diagnostics._MARKER_BYTES);marker.chmod(0o600)
            original(directory)
        with patch.object(diagnostics._Directory,'replace',substitute):
            self.assert_storage_error(lambda:self.history.record(str(uuid4()),[record('calls')]))

    @unittest.skipIf(os.name=='nt','POSIX marker/temp no-follow checks')
    def test_marker_hardlink_and_temporary_symlink_are_rejected(self):
        self.history.record(self.identity,[record()])
        marker=self.history.root/diagnostics.MARKER
        linked=self.parent/'linked-marker';os.link(marker,linked)
        self.assert_storage_error(self.history.load)
        linked.unlink()
        outside=self.parent/'outside';outside.write_bytes(b'private fixture');outside.chmod(0o600)
        temporary=self.history.root/diagnostics._TEMPORARY;temporary.symlink_to(outside)
        self.assert_storage_error(self.history.load)
        self.assertEqual(b'private fixture',outside.read_bytes())
        self.assertTrue(temporary.is_symlink())

    def test_default_root_and_unexpected_storage_failures_use_only_fixed_code(self):
        with patch.object(diagnostics,'default_sessions_root',side_effect=RuntimeError('/private/owner detail')) as resolver:
            history=diagnostics.DiagnosticHistory()
            resolver.assert_not_called()
            self.assert_storage_error(history.load)
        self.history.record(self.identity,[record()])
        with patch.object(diagnostics._Directory,'read',side_effect=RuntimeError('/private/owner detail')):
            self.assert_storage_error(self.history.load)

    def test_unknown_file_in_owned_store_fails_without_touching_it(self):
        self.history.record(self.identity,[record()]);extra=self.history.root/'owner-file';extra.write_text('private fixture')
        self.assert_storage_error(self.history.load);self.assertEqual('private fixture',extra.read_text())

    @unittest.skipUnless(os.name=='nt','actual Windows protected owner/DACL APIs required')
    def test_windows_private_root_dacl_accepted_and_inherited_root_refused(self):
        from amplifai_phone.windows_storage import private_windows_directory
        self.history.record(self.identity,[record()])
        private_windows_directory(self.history.root)
        inherited_parent=self.parent/'inherited-product';inherited_parent.mkdir()
        inherited=diagnostics.DiagnosticHistory(inherited_parent/'sessions')
        inherited.root.mkdir()
        (inherited.root/diagnostics.MARKER).write_bytes(diagnostics._MARKER_BYTES)
        with self.assertRaises(PermissionError):private_windows_directory(inherited.root)
        self.assert_storage_error(inherited.load)
        self.assertEqual({diagnostics.MARKER},{p.name for p in inherited.root.iterdir()})

    @unittest.skipUnless(os.name=='nt','actual Windows protected file DACL readback required')
    def test_windows_inherited_history_file_dacl_is_not_adopted_or_repaired(self):
        from amplifai_phone.windows_storage import private_windows_directory
        self.history.record(self.identity,[record()])
        path=self.history.root/diagnostics._HISTORY;payload=path.read_bytes()
        path.unlink();path.write_bytes(payload)
        private_windows_directory(self.history.root)
        self.assert_storage_error(self.history.load)
        self.assertEqual(payload,path.read_bytes())
        self.assert_storage_error(lambda:self.history.record(str(uuid4()),[record('calls')]))
        self.assertEqual(payload,path.read_bytes())

    @unittest.skipUnless(os.name=='nt','actual Windows protected temporary-file DACL readback required')
    def test_windows_inherited_crash_temporary_is_refused_without_touching_history(self):
        self.history.record(self.identity,[record()])
        payload=(self.history.root/diagnostics._HISTORY).read_bytes()
        path=self.history.root/diagnostics._TEMPORARY;partial=b'{unprotected crash bytes'
        path.write_bytes(partial)
        self.assert_storage_error(self.history.load)
        self.assertEqual(payload,(self.history.root/diagnostics._HISTORY).read_bytes())
        self.assertEqual(partial,path.read_bytes())

    def test_cross_process_busy_writer_fails_safely_then_relaunch_loads(self):
        before=self.history.record(self.identity,[record()])
        context=multiprocessing.get_context('spawn');ready=context.Event();release=context.Event()
        child=context.Process(target=hold_lock,args=(str(self.sessions),ready,release));child.start()
        try:
            self.assertTrue(ready.wait(10))
            self.assert_storage_error(lambda:self.history.record(str(uuid4()),[record('calls')]))
        finally:
            release.set();child.join(10)
            if child.is_alive():child.terminate();child.join(5)
        self.assertEqual(0,child.exitcode)
        self.assertEqual(before,diagnostics.DiagnosticHistory(self.sessions).load())
        self.assertEqual(2,len(self.history.record(str(uuid4()),[record('calls')])))


if __name__=='__main__':unittest.main()
