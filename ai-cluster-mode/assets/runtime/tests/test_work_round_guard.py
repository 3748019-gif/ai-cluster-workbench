"""A01 isolated lineage tests. No external calls or production state writes."""
import json
import tempfile
import unittest
from pathlib import Path
from cluster.core import Cluster, GuardError, now, js
from test_core import spec

class WorkRoundGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='round-guard-')
        self.root=Path(self.tmp.name);self.c=Cluster(self.root,'test')
        self.source=self.root/'source.txt';self.source.write_text('READY synthetic fixture')
        self.parent=self.c.create(spec())['task_id']
    def tearDown(self):self.c.close();self.tmp.cleanup()
    def task(self,external=False,**kw):
        s=spec(external);s.update(kw)
        t=self.c.new_work(self.parent,s)['task_id'];self.c.add_source(t,self.source);self.c.package(t)
        return t
    def follow(self,t,**kw):
        old=self.c.get(t);s=spec(True)
        s.update(parent_task_id=self.parent,previous_round_id=t,work_item_id=old['work_item_id'],round_id=old['round_id']+1,
                 followup_reason='A new evidence question',new_work='Check the revised official edition')
        s.update(kw);return s
    def proof(self,t,s):
        p=self.root/'proof.json';p.write_text(js({'kind':'simulated','task_id':t,'submission_id':s['submission_id'],
            'provider':s['provider'],'stage':'preflight','observed_at':now(),'window':'Local simulated fixture',
            'raw_text':s['prompt'],'input_text':s['prompt'],'package_hash':s['package_hash'],
            'unexpected_attachments':False,'client_available':True,'quota_available':True}))
        return p
    def begin(self,t):
        s=self.c.prepare_submission(t,'producer');token=self.c.acquire_gui(t)
        try:self.c.begin_submission(t,s['submission_id'],token,self.proof(t,s))
        finally:self.c.release_gui(t,token)
        return s
    def complete_local(self,t):
        self.c.start_local(t);p=self.root/'out.txt';p.write_text(t+' READY');self.c.local_output(t,p)
        self.assertTrue(self.c.validate(t)['passed']);self.c.finalize(t);self.c.deliver(t)
        self.assertTrue(self.c.audit(t)['passed'])
    def test_uncertain_round_rejected_by_both_core_entries(self):
        t=self.task(True);self.begin(t);before=self.c.get(t);count=len(self.c.list_tasks())
        with self.assertRaisesRegex(GuardError,'existing submission'):self.c.create(self.follow(t))
        with self.assertRaisesRegex(GuardError,'existing submission'):self.c.new_work(self.parent,self.follow(t),t)
        self.assertEqual(self.c.get(t),before);self.assertEqual(len(self.c.list_tasks()),count)
        self.assertEqual(self.c.metrics(t)['submission_intents'],1)
    def test_external_in_flight_survives_task_exception_label(self):
        t=self.task(True);s=self.begin(t);token=self.c.acquire_gui(t)
        p=json.loads(self.proof(t,s).read_text());p.update(stage='confirmation',message_visible=True)
        proof=self.root/'confirmed.json';proof.write_text(js(p))
        try:self.c.confirm_submission(t,s['submission_id'],token,proof)
        finally:self.c.release_gui(t,token)
        self.c.mark_running(t,s['submission_id']);self.c.fail(t,'FAILED','Synthetic client connection failure')
        with self.assertRaisesRegex(GuardError,'existing submission'):self.c.create(self.follow(t))
    def test_local_running_blocked_but_independent_item_can_start(self):
        t=self.task();self.c.start_local(t)
        with self.assertRaisesRegex(GuardError,'running work'):self.c.create(self.follow(t))
        independent=self.task();self.c.start_local(independent)
        self.assertNotEqual(self.c.get(t)['work_item_id'],self.c.get(independent)['work_item_id'])
        self.assertEqual(self.c.get(independent)['status'],'RUNNING')
    def test_delivered_round_allows_new_goal_and_normalizes_identity(self):
        t=self.task();self.complete_local(t);before=self.c.get(t)
        s=self.follow(t);s.pop('round_id');s.pop('work_item_id')
        next_round=self.c.create(s)
        self.assertEqual(next_round['round_id'],2);self.assertEqual(next_round['work_item_id'],before['work_item_id'])
        self.assertEqual(self.c.get(t),before)
    def test_wrong_parent_work_item_or_round_is_rejected(self):
        t=self.task();self.complete_local(t);other=self.c.create(spec())['task_id']
        for change in ({'parent_task_id':other},{'work_item_id':'WRONG-WORK'},{'round_id':1},{'round_id':3},{'round_id':True}):
            with self.subTest(change=change),self.assertRaises(GuardError):self.c.create(self.follow(t,**change))
    def test_parent_and_previous_modes_checked(self):
        t=self.task();self.complete_local(t);live=Cluster(self.root,'live')
        try:
            with self.assertRaisesRegex(GuardError,'mode mismatch'):live.create(self.follow(t))
            live_parent=live.create(spec())['task_id']
            with self.assertRaisesRegex(GuardError,'mode mismatch'):self.c.create(dict(spec(),parent_task_id=live_parent))
            with self.assertRaises(GuardError):live.new_work(live_parent,self.follow(t,parent_task_id=live_parent),t)
        finally:live.close()
    def test_new_work_arguments_cannot_silently_replace_conflicting_identity(self):
        t=self.task();self.complete_local(t);other=self.c.create(spec())['task_id']
        with self.assertRaisesRegex(GuardError,'Conflicting parent'):self.c.new_work(other,self.follow(t),t)
        with self.assertRaisesRegex(GuardError,'Conflicting previous'):self.c.new_work(self.parent,self.follow(t),other)
    def test_goal_and_captured_output_required(self):
        t=self.task()
        with self.assertRaisesRegex(GuardError,'captured output'):self.c.create(self.follow(t))
        self.complete_local(t)
        for change in ({'new_work':False},{'new_work':' '},{'followup_reason':' '},{'followup_reason':True}):
            with self.subTest(change=change),self.assertRaisesRegex(GuardError,'New round needs'):self.c.create(self.follow(t,**change))
    def test_omitting_previous_or_reusing_stale_predecessor_fails(self):
        t=self.task();self.complete_local(t);s=self.follow(t)
        no_previous=dict(s);no_previous.pop('previous_round_id');no_previous['round_id']=1
        with self.assertRaisesRegex(GuardError,'previous_round_id'):self.c.create(no_previous)
        self.c.create(s)
        with self.assertRaisesRegex(GuardError,'latest unique'):self.c.create(s)
    def _late_in_flight_fixture(self,t):
        # Isolated counterfactual restore/import fixture: models a legacy
        # concurrent transition after child creation, never a production fix.
        with self.c._mutate(t) as m:self.c._move(m,'SUBMISSION_UNCERTAIN','TEST_LATE_STATE','test','Synthetic persisted late state')
    def test_prepare_rechecks_late_previous_state_without_new_ticket(self):
        t=self.task();self.complete_local(t);n=self.c.create(self.follow(t))['task_id']
        self.c.add_source(n,self.source);self.c.package(n);self._late_in_flight_fixture(t)
        with self.assertRaisesRegex(GuardError,'existing submission'):self.c.prepare_submission(n,'producer')
        self.assertEqual(self.c.get(n)['submissions'],[])
    def test_begin_rechecks_late_previous_state_without_second_intent(self):
        t=self.task();self.complete_local(t);n=self.c.create(self.follow(t))['task_id']
        self.c.add_source(n,self.source);self.c.package(n);s=self.c.prepare_submission(n,'producer')
        self._late_in_flight_fixture(t);token=self.c.acquire_gui(n)
        try:
            with self.assertRaisesRegex(GuardError,'existing submission'):self.c.begin_submission(n,s['submission_id'],token,self.proof(n,s))
        finally:self.c.release_gui(n,token)
        self.assertEqual(self.c.metrics(n)['submission_intents'],0);self.assertEqual(self.c.get(n)['submissions'][0]['status'],'prepared')
    def test_old_round_cannot_restart_after_child_exists(self):
        t=self.task();self.complete_local(t);self.c.create(self.follow(t))
        with self.assertRaises(GuardError):self.c._work_dispatch_guard(self.c.get(t))

if __name__=='__main__':unittest.main(verbosity=2)
