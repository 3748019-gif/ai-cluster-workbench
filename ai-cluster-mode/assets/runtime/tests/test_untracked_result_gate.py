"""Unknown result payloads vs explicit hash-bound non-deliverables; no clients."""
import json
import tempfile
import unittest
from pathlib import Path
from cluster.core import Cluster, GuardError, digest, js
from cluster.release_checks import build_manifest


class UntrackedResultGateTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.c=Cluster(self.root,'test')
        spec={'objective':'Synthetic result namespace boundary','task_type':'document','risk_level':'low',
            'rules':['Only registered exact candidate is an output'],'acceptance':['READY'],
            'review_required':False,'validators':[{'kind':'text','required':['READY']}]}
        self.task=self.c.create(spec)['task_id'];source=self.root/'source.txt';source.write_text('Original READY')
        self.c.add_source(self.task,source);self.c.package(self.task);self.c.start_local(self.task)
        p=self.root/'candidate.txt';p.write_text(self.task+' READY accepted A; underlying scope unknown')
        self.c.local_output(self.task,p);self.c.validate(self.task)

    def tearDown(self):self.c.close();self.temp.cleanup()

    def put(self,relative,data='Unregistered B'):
        p=self.c.task_path(self.task)/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(data);return p

    def test_unknown_producer_result_blocks_finalize_preserves_bytes(self):
        p=self.put('03_producer/injected.txt')
        audit=self.c.audit(self.task)
        self.assertFalse(audit['passed'])
        self.assertIn('03_producer/injected.txt',audit['untracked_files'])
        with self.assertRaises(GuardError):self.c.finalize(self.task)
        self.assertEqual(p.read_text(),'Unregistered B')

    def test_late_final_result_blocks_deliver(self):
        self.c.finalize(self.task);p=self.put('06_final/injected.txt')
        with self.assertRaises(GuardError):self.c.deliver(self.task)
        self.assertEqual(p.read_text(),'Unregistered B')
        self.assertEqual(self.c.get(self.task)['status'],'FINALIZING')

    def test_nested_reviewer_result_is_not_missed(self):
        self.put('04_reviewer/nested/injected.md')
        self.assertFalse(self.c.audit(self.task)['passed'])

    def test_logs_and_material_residuals_are_non_deliverables_not_global_block(self):
        self.put('logs/debug.tmp','Controller scratch');self.put('01_sources/unregistered-not-input.txt','Extra material')
        report=self.c.audit(self.task);self.assertTrue(report['passed'])
        self.assertEqual(len(report['untracked_files']),2)
        self.assertTrue(all(not x['blocking'] and not x['included_in_artifact_inputs'] for x in report['untracked_file_details']))
        self.c.finalize(self.task);output=self.c.deliver(self.task)
        self.assertNotIn(b'Extra material',output.read_bytes())

    def test_mac_metadata_not_output(self):
        self.put('03_producer/.DS_Store','Incidental macOS metadata')
        report=self.c.audit(self.task);self.assertTrue(report['passed'])
        self.assertEqual(report['untracked_file_details'][0]['classification'],'mac_metadata')
        self.c.finalize(self.task);self.c.deliver(self.task)

    def test_explicit_non_deliverable_tmp_exclusion_preserves_and_allows_exact_A(self):
        p=self.put('03_producer/scratch.tmp','Local formatting scratch')
        sha=digest(p.read_bytes())
        rec=self.c.exclude_untracked_file(self.task,'03_producer/scratch.tmp',sha,'Local scratch; no publication or input')
        self.assertEqual(rec['disposition'],'non_deliverable')
        self.assertTrue(self.c.audit(self.task)['passed'])
        self.c.finalize(self.task);output=self.c.deliver(self.task)
        self.assertNotIn(b'Local formatting scratch',output.read_bytes())
        self.assertEqual(digest(p.read_bytes()),sha)

    def test_hash_bound_exclusion_is_idempotent_but_new_bytes_block(self):
        p=self.put('03_producer/scratch.tmp');sha=digest(p.read_bytes())
        first=self.c.exclude_untracked_file(self.task,'03_producer/scratch.tmp',sha,'Fictional scratch')
        self.assertEqual(self.c.exclude_untracked_file(self.task,'03_producer/scratch.tmp',sha,'Fictional scratch'),first)
        self.assertEqual(len([e for e in self.c.events(self.task) if e['event']=='UNTRACKED_FILE_EXCLUDED']),1)
        p.write_text('New unknown result')
        self.assertFalse(self.c.audit(self.task)['passed'])

    def test_registered_candidate_cannot_be_excluded(self):
        m=self.c.get(self.task);a=self.c.artifact(m,m['candidate'])
        with self.assertRaises(GuardError):self.c.exclude_untracked_file(self.task,a['filename'],a['hash'],'Trying to exempt actual candidate')

    def test_missing_reason_wrong_hash_and_escape_rejected(self):
        p=self.put('03_producer/scratch.tmp');sha=digest(p.read_bytes())
        for rel,h,reason in [('03_producer/scratch.tmp',sha,''),('03_producer/scratch.tmp','0'*64,'Scratch'),('../outside.txt',sha,'Scratch')]:
            with self.subTest(path=rel,reason=reason):
                with self.assertRaises(GuardError):self.c.exclude_untracked_file(self.task,rel,h,reason)
        self.assertFalse(self.c.audit(self.task)['passed'])

    def test_unknown_result_symlink_blocks_without_using_target(self):
        canary=self.root/'canary.txt';canary.write_text('Fictional canary, not secret')
        link=self.c.task_path(self.task)/'06_final/scratch';link.symlink_to(canary)
        report=self.c.audit(self.task);self.assertFalse(report['passed'])
        with self.assertRaises(GuardError):self.c.exclude_untracked_file(self.task,'06_final/scratch',digest(canary.read_bytes()),'Symlink cannot be exempted')

    def test_excluded_scratch_cannot_be_bound_as_accepted_release_input(self):
        p=self.put('03_producer/scratch.tmp','Unreviewed result B');sha=digest(p.read_bytes())
        rec=self.c.exclude_untracked_file(self.task,'03_producer/scratch.tmp',sha,'Not a deliverable')
        release=self.root/'release';release.mkdir();out=release/'scratch.txt';out.write_bytes(p.read_bytes())
        (release/'MANIFEST_SHA256.json').write_text(js(build_manifest(release)))
        registered=self.c.register_release(self.task,release)
        fake=[{'acceptance_id':self.c.get(self.task)['validation'][-1]['artifact_id'], 'input_task_id':self.task,
            'input_artifact_id':rec['receipt_artifact'],'input_sha256':self.c.artifact(self.c.get(self.task),rec['receipt_artifact'])['hash'],
            'output_path':'scratch.txt','output_sha256':sha,'transform':{'type':'identity_v1','version':1}}]
        with self.assertRaises(GuardError):self.c.bind_release(self.task,registered['release_id'],fake)


if __name__=='__main__':unittest.main()
