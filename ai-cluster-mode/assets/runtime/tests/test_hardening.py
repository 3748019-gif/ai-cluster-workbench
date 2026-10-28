import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from test_core import CoreTest
from cluster.core import Cluster,GuardError,js,now

# Reuse fixtures without inheriting all test methods.
class HardeningTest(CoreTest):
    def test_create_after_partial_directory_preserves_orphan(self):
        from test_core import spec
        from datetime import datetime
        day=datetime.now().astimezone().strftime('%Y%m%d')
        orphan=self.root/'tasks'/f'TASK-{day}-0001'/'00_request'
        orphan.mkdir(parents=True);(orphan/'partial.txt').write_text('preserve')
        task=self.c.create(spec())
        self.assertTrue(task['task_id'].endswith('-0002'))
        self.assertEqual((orphan/'partial.txt').read_text(),'preserve')
    def test_receive_existing_output_after_client_recovery(self):
        from cluster.core import digest
        t=self.task(True);s,token=self.external_start(t)
        self.c.fail(t,'CLIENT_UNAVAILABLE','Window disappeared after confirmed send')
        self.assertEqual(self.c.recover(t)['status'],'CLIENT_UNAVAILABLE')
        out=self.root/'out.json';out.write_text(js({'task_id':t,'submission_id':s['submission_id'],'text':'READY'}))
        self.c.receive(t,s['submission_id'],out,token,self.proof(t,s,'output',completed=True,output_hash=digest(out.read_bytes())))
        self.assertEqual(self.c.get(t)['status'],'OUTPUT_RECEIVED')
        self.assertEqual(self.c.metrics(t)['external_calls_confirmed'],1)
    def test_degrade_before_packaging_still_requires_sources(self):
        from test_core import spec
        t=self.c.create(spec(True))['task_id']
        self.c.fail(t,'CLIENT_UNAVAILABLE','Client not available before packaging')
        self.c.degrade(t,'Use local producer')
        self.assertEqual(self.c.get(t)['status'],'CREATED')
        with self.assertRaises(GuardError):self.c.start_local(t)
    def test_ancestor_tamper_after_finalize_blocks_delivery(self):
        t=self.task();self.local_candidate(t);self.c.validate(t);self.c.finalize(t)
        m=self.c.get(t);v=self.c.artifact(m,m['validation'][-1]['artifact_id'])
        (self.c.task_path(t)/v['filename']).write_text('{}')
        with self.assertRaises(GuardError):self.c.deliver(t)
    def test_crash_after_commit_before_manifest_export_recovers(self):
        t=self.task()
        with patch.object(self.c,'export',side_effect=RuntimeError('Simulated process interruption')):
            with self.assertRaises(RuntimeError):self.c.start_local(t)
        self.assertEqual(self.c.get(t)['status'],'RUNNING')
        recovered=self.c.recover(t);self.assertEqual(recovered['status'],'RUNNING')
        self.assertEqual(json.loads((self.c.task_path(t)/'manifest.json').read_text())['status'],'RUNNING')
    def test_event_chain_tamper_detected(self):
        t=self.task();r=self.c.db.execute('SELECT seq,body FROM events WHERE task_id=? LIMIT 1',(t,)).fetchone();e=json.loads(r[1]);e['message']='tampered'
        self.c.db.execute('UPDATE events SET body=? WHERE seq=?',(js(e),r[0]));self.assertFalse(self.c.audit(t)['passed'])
    def test_source_version_latest_only(self):
        from test_core import spec
        m=self.c.create(spec());t=m['task_id'];a=self.c.add_source(t,self.source)
        self.source.write_text('READY new revision');b=self.c.add_source(t,self.source);self.c.package(t)
        self.assertEqual(self.c.get(t)['source_files'],[b['artifact_id']]);self.assertEqual(b['version'],2)
    def test_recovery_after_client_disappears_keeps_uncertain(self):
        t=self.task(True);s=self.c.prepare_submission(t,'producer');token=self.c.acquire_gui(t);self.c.begin_submission(t,s['submission_id'],token,self.proof(t,s,'preflight'));self.c.fail(t,'CLIENT_UNAVAILABLE','Lost app')
        self.assertEqual(self.c.recover(t)['status'],'SUBMISSION_UNCERTAIN')
    def test_call_budget_blocks_next_external_role(self):
        t=self.task(True);policy={'kimi':{'started_at':'2000-01-01T00:00:00+00:00','max_calls':0,'unit':'remaining_credits','baseline':100,'minimum_remaining':10,'max_observed_spend':10}}
        (self.root/'.cluster/budget_policy.json').write_text(js(policy))
        with self.assertRaisesRegex(GuardError,'call cap'):self.c.prepare_submission(t,'producer')
    def test_balance_floor_blocks_before_send(self):
        t=self.task(True);self.c.record_cost(t,{'provider':'kimi','observed_at':now(),'value':4,'unit':'remaining_credits','scope':'simulated'})
        policy={'kimi':{'started_at':'2000-01-01T00:00:00+00:00','max_calls':1,'unit':'remaining_credits','baseline':100,'minimum_remaining':10,'max_observed_spend':200}}
        (self.root/'.cluster/budget_policy.json').write_text(js(policy))
        with self.assertRaisesRegex(GuardError,'balance'):self.c.prepare_submission(t,'producer')
    def test_compact_prompt_forbidden_after_send_intent(self):
        t=self.task(True);s=self.c.prepare_submission(t,'producer');token=self.c.acquire_gui(t);self.c.begin_submission(t,s['submission_id'],token,self.proof(t,s,'preflight'))
        with self.assertRaises(GuardError):self.c.compact_prepared_prompt(t,s['submission_id'])

# unittest sees inherited tests; remove them to keep the reported count meaningful.
for name in list(vars(CoreTest)):
    if name.startswith('test_') and name not in vars(HardeningTest):setattr(HardeningTest,name,None)
del CoreTest
