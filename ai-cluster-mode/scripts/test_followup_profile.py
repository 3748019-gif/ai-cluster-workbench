"""A01 public profile entry integration; all send evidence is simulated."""
import json
import unittest
import test_run_profile as base
from cluster.core import Cluster, GuardError, now

tool=base.tool;rp=base.rp
class FollowupProfileTests(unittest.TestCase):
    setUp=base.ProfileTests.setUp
    tearDown=base.ProfileTests.tearDown
    save_cfg=base.ProfileTests.save_cfg
    activate=base.ProfileTests.activate
    def create(self,**kw):
        spec={'objective':'READY synthetic first question','task_type':'document','rules':['READY'],
              'acceptance':['READY'],'validators':[{'kind':'text','required':['READY']}],
              'assigned_provider':'codex','review_required':False}
        spec.update(kw);tool.write_json(self.request,spec)
        return rp.create_task(tool,self.workspace,self.request,'test')['task']
    def package(self,c,m):
        source=self.workspace/'inputs/source.txt';source.write_text('READY synthetic fixture')
        c.add_source(m['task_id'],source);c.package(m['task_id'])
    def proof(self,m,s):
        p=self.workspace/'inputs/proof.json';tool.write_json(p,{'kind':'simulated','task_id':m['task_id'],
          'submission_id':s['submission_id'],'provider':s['provider'],'stage':'preflight','observed_at':now(),
          'window':'Local fixture only','raw_text':s['prompt'],'input_text':s['prompt'],'package_hash':s['package_hash'],
          'unexpected_attachments':False,'client_available':True,'quota_available':True})
        return p
    def begin(self,c,m):
        s=tool.run_ctl(self.workspace,['prepare',m['task_id']],mode='test');token=c.acquire_gui(m['task_id'])
        try:tool.run_ctl(self.workspace,['begin',m['task_id'],'--submission',s['submission_id'],'--token',token,'--proof',str(self.proof(m,s))],mode='test')
        finally:c.release_gui(m['task_id'],token)
        return s
    def follow(self,parent,m):
        return dict(parent_task_id=parent['task_id'],assigned_provider='kimi',previous_round_id=m['task_id'],
                    work_item_id=m['work_item_id'],round_id=m['round_id']+1,new_work=True,
                    objective='READY a distinct new question',followup_reason='Review newly supplied evidence')
    def completed(self,c,parent):
        m=self.create(parent_task_id=parent['task_id']);self.package(c,m);c.start_local(m['task_id'])
        out=self.workspace/'outputs/local.txt';out.write_text(m['task_id']+' READY');c.local_output(m['task_id'],out)
        self.assertTrue(c.validate(m['task_id'])['passed']);c.finalize(m['task_id']);c.deliver(m['task_id'])
        return c.get(m['task_id'])
    def test_uncertain_public_followup_rejected_and_independent_work_proceeds(self):
        self.activate();parent=self.create();m=self.create(parent_task_id=parent['task_id'],assigned_provider='kimi')
        c=Cluster(self.workspace,'test')
        try:
            self.package(c,m);self.begin(c,m);before=c.get(m['task_id']);n=len(c.list_tasks())
            with self.assertRaisesRegex(ValueError,'existing submission'):self.create(**self.follow(parent,m))
            with self.assertRaisesRegex(GuardError,'existing submission'):c.new_work(parent['task_id'],dict(base_spec(),**self.follow(parent,m)),m['task_id'])
            self.assertEqual(len(c.list_tasks()),n);self.assertEqual(c.get(m['task_id']),before)
            independent=self.create(parent_task_id=parent['task_id'],assigned_provider='kimi');self.package(c,independent);self.begin(c,independent)
            same=[t for t in c.list_tasks() if t['work_item_id']==m['work_item_id']]
            self.assertEqual(sum(c.metrics(t['task_id'])['submission_intents'] for t in same),1)
            self.assertEqual(c.metrics(independent['task_id'])['submission_intents'],1)
        finally:c.close()
    def test_running_cannot_escape_with_new_profile_or_without_previous(self):
        self.activate();parent=self.create();m=self.create(parent_task_id=parent['task_id'],assigned_provider='kimi');c=Cluster(self.workspace,'test')
        try:
            self.package(c,m);s=self.begin(c,m);token=c.acquire_gui(m['task_id'])
            p=self.proof(m,s);data=json.loads(p.read_text());data.update(stage='confirmation',message_visible=True);tool.write_json(p,data)
            try:c.confirm_submission(m['task_id'],s['submission_id'],token,p)
            finally:c.release_gui(m['task_id'],token)
            c.mark_running(m['task_id'],s['submission_id']);cfg=base.config();cfg['run_id']='different-run';self.activate(cfg)
            with self.assertRaisesRegex(ValueError,'existing submission'):self.create(**self.follow(parent,m))
            follow=self.follow(parent,m);follow.pop('previous_round_id');follow['round_id']=1
            with self.assertRaisesRegex(ValueError,'existing submission'):self.create(**follow)
            self.assertEqual(c.metrics(m['task_id'])['submission_intents'],1)
        finally:c.close()
    def test_completed_public_followup_runs_with_inherited_identity(self):
        self.activate();parent=self.create();c=Cluster(self.workspace,'test')
        try:
            m=self.completed(c,parent);follow=self.follow(parent,m);follow.pop('work_item_id');follow.pop('round_id')
            n=self.create(**follow);self.assertEqual(n['round_id'],2);self.assertEqual(n['work_item_id'],m['work_item_id'])
            self.package(c,n);self.begin(c,n);self.assertEqual(c.metrics(n['task_id'])['submission_intents'],1)
            self.assertEqual(c.get(m['task_id'])['status'],'DELIVERED')
        finally:c.close()
    def test_public_wrong_parent_work_item_round_and_mode_rejected(self):
        self.activate();parent=self.create();other=self.create();c=Cluster(self.workspace,'test')
        try:
            m=self.completed(c,parent)
            for change in ({'parent_task_id':other['task_id']},{'work_item_id':'WRONG'},{'round_id':9}):
                args=self.follow(parent,m);args.update(change)
                with self.subTest(change=change),self.assertRaises(ValueError):self.create(**args)
            args=dict(base_spec(),**self.follow(parent,m));tool.write_json(self.request,args)
            with self.assertRaisesRegex(ValueError,'mode mismatch'):rp.create_task(tool,self.workspace,self.request,'live')
        finally:c.close()
    def test_public_prepare_and_begin_recheck_durable_previous_state(self):
        self.activate();parent=self.create();c=Cluster(self.workspace,'test')
        try:
            m=self.completed(c,parent);n=self.create(**self.follow(parent,m));self.package(c,n)
            s=tool.run_ctl(self.workspace,['prepare',n['task_id']],mode='test')
            # Isolated legacy/late-state fixture; never used as a runtime repair.
            with c._mutate(m['task_id']) as old:c._move(old,'SUBMISSION_UNCERTAIN','TEST_LATE_STATE','test','Synthetic restored state')
            with self.assertRaisesRegex(ValueError,'existing submission'):tool.run_ctl(self.workspace,['prepare',n['task_id']],mode='test')
            token=c.acquire_gui(n['task_id'])
            try:
                with self.assertRaisesRegex(ValueError,'existing submission'):
                    tool.run_ctl(self.workspace,['begin',n['task_id'],'--submission',s['submission_id'],'--token',token,'--proof',str(self.proof(n,s))],mode='test')
            finally:c.release_gui(n['task_id'],token)
            self.assertEqual(c.metrics(n['task_id'])['submission_intents'],0)
        finally:c.close()

def base_spec():
    return {'task_type':'document','rules':['READY'],'acceptance':['READY'],'validators':[{'kind':'text','required':['READY']}],'review_required':False}

if __name__=='__main__':unittest.main(verbosity=2)
