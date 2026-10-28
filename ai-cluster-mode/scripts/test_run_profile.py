"""Local isolated profile checks. Simulated send intent, no real GUI or models."""
import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
spec=importlib.util.spec_from_file_location('profile_toolkit',HERE/'toolkit.py')
tool=importlib.util.module_from_spec(spec); sys.modules[spec.name]=tool; spec.loader.exec_module(tool)
import run_profile as rp
sys.path.insert(0,str(tool.RUNTIME))
from cluster.core import Cluster, GuardError, now

def config():
    r={}
    for role,member in zip(rp.ROLES,('codex','kimi','workbuddy')):
        r[role]={'member':member,'requested':{'model':None,'reasoning_effort':None,'speed':'preserve_current'},
                 'max_calls':None,'budget':{},'fallback':{'authorized':False}}
    r['orchestrator']['requested']={'model':'gpt-6-astra','reasoning_effort':'high','speed':'standard'}
    return {'schema_version':1,'run_id':'demo-01','authorization_source':'Explicit local isolated profile test','roles':r}

class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='profile_')
        self.base=Path(self.tmp.name); self.workspace=self.base/'instance'
        self.cfg=self.base/'config.json'; self.request=self.base/'task.json'
        self.save_cfg(config())
        tool.init(self.workspace,'User authorized this local test')
        self.registry=tool.run_ctl(self.workspace,['roles'])
    def tearDown(self): self.tmp.cleanup()
    def save_cfg(self,data): tool.write_json(self.cfg,data)
    def activate(self,data=None):
        if data is not None: self.save_cfg(data)
        return rp.profile_import(tool,self.workspace,self.cfg)
    def create(self,provider='codex'):
        tool.write_json(self.request,{'objective':'Synthetic READY','task_type':'document','rules':['READY'],
                                     'acceptance':['READY'],'validators':[{'kind':'text','required':['READY']}],
                                     'assigned_provider':provider,'review_required':False})
        return rp.create_task(tool,self.workspace,self.request,mode='test')['task']
    def test_null_open_and_models_remain_unobserved(self):
        self.activate(); m=self.create()
        self.assertIsNone(m['effective_policy']['providers']['kimi']['max_calls'])
        self.assertEqual(m['spec']['requested_member_settings']['codex']['reasoning_effort'],'high')
        self.assertEqual(m['controller_model']['observed']['status'],'unknown')
        self.assertFalse(m['spec']['run_profile_snapshot']['boundary']['client_settings_changed'])
    def test_scheduler_cap_blocks_second_task(self):
        cfg=config(); cfg['roles']['orchestrator']['max_calls']=1
        self.activate(cfg); self.create()
        with self.assertRaisesRegex(ValueError,'scheduler'): self.create()
        self.assertEqual(len(tool.run_ctl(self.workspace,['list'])),1)
    def test_two_tasks_share_external_cap_uncertain_counts_prepared_does_not(self):
        cfg=config(); cfg['roles']['primary_executor']['max_calls']=1
        self.activate(cfg); a=self.create('kimi'); b=self.create('kimi')
        c=Cluster(self.workspace,'test')
        try:
            source=self.workspace/'inputs/source.txt'; source.write_text('READY')
            for m in (a,b): c.add_source(m['task_id'],source); c.package(m['task_id'])
            first=c.prepare_submission(a['task_id'],'producer'); second=c.prepare_submission(b['task_id'],'producer')
            self.assertEqual(c.metrics(a['task_id'])['submission_intents'],0)
            proof=self.workspace/'inputs/proof.json'
            tool.write_json(proof,{'kind':'simulated','task_id':a['task_id'],'submission_id':first['submission_id'],
                                  'provider':'kimi','stage':'preflight','observed_at':now(),'window':'Test only',
                                  'raw_text':first['prompt'],'input_text':first['prompt'],'package_hash':first['package_hash'],
                                  'unexpected_attachments':False,'client_available':True,'quota_available':True})
            token=c.acquire_gui(a['task_id']); c.begin_submission(a['task_id'],first['submission_id'],token,proof); c.release_gui(a['task_id'],token)
            with self.assertRaisesRegex(GuardError,'max_calls'): c.prepare_submission(b['task_id'],'producer')
            token=c.acquire_gui(b['task_id'])
            with self.assertRaisesRegex(GuardError,'max_calls'): c.begin_submission(b['task_id'],second['submission_id'],token,proof)
            c.release_gui(b['task_id'],token)
            self.assertEqual(c.metrics(b['task_id'])['submission_intents'],0)
            self.assertEqual(c.metrics(a['task_id'])['external_calls_confirmed'],0)
        finally: c.close()
    def test_new_profile_preserves_old_task_snapshot(self):
        self.activate(); a=self.create(); old=copy.deepcopy(a)
        cfg=config(); cfg['run_id']='demo-02'; cfg['roles']['primary_executor']['member']='workbuddy'; cfg['roles']['secondary_executor']['member']='kimi'
        self.activate(cfg); b=self.create()
        self.assertEqual(tool.run_ctl(self.workspace,['show',a['task_id']]),old)
        self.assertEqual(b['role_bindings']['primary_executor'],'workbuddy')
        self.assertNotEqual(a['policy_id'],b['policy_id'])
    def test_frozen_run_id_cannot_change_or_reactivate(self):
        self.activate(); cfg=config(); cfg['roles']['primary_executor']['max_calls']=3
        with self.assertRaisesRegex(ValueError,'cannot be changed'): self.activate(cfg)
        cfg=config(); cfg['run_id']='demo-02'; self.activate(cfg)
        with self.assertRaisesRegex(ValueError,'reactivate'): self.activate(config())
    def test_no_raw_create_bypass_or_task_override(self):
        self.activate()
        with self.assertRaisesRegex(ValueError,'create-task'): tool.run_ctl(self.workspace,['create','--file',str(self.request)])
        self.create(); data=json.loads(self.request.read_text()); data['policy_overrides']={'max_calls':None}; tool.write_json(self.request,data)
        with self.assertRaisesRegex(ValueError,'overriding'): rp.create_task(tool,self.workspace,self.request)
    def test_budget_conversion_and_missing_observation_blocks_dispatch(self):
        cfg=config(); cfg['roles']['secondary_executor']['budget']={'max_observed_spend':20,'baseline':100,'minimum_remaining':10,'unit':'native_credit'}
        self.activate(cfg); m=self.create('workbuddy')
        self.assertEqual(m['effective_policy']['providers']['workbuddy']['max_observed_spend'],20)
        c=Cluster(self.workspace,'test')
        try:
            source=self.workspace/'inputs/source.txt'; source.write_text('READY'); c.add_source(m['task_id'],source); c.package(m['task_id'])
            with self.assertRaisesRegex(GuardError,'observation'): c.prepare_submission(m['task_id'],'producer')
            c.record_cost(m['task_id'],{'provider':'workbuddy','unit':'native_credit','value':80,'observed_at':now(),'scope':'account balance'})
            with self.assertRaisesRegex(GuardError,'spend cap'): c.prepare_submission(m['task_id'],'producer')
        finally: c.close()
    def test_authorized_fallback_is_request_not_automatic_switch(self):
        cfg=config(); cfg['roles']['secondary_executor']['fallback']={'authorized':True,'authorization_source':'User permits Flash unavailable → Pro',
            'on':['model_unavailable'],'requested':{'model':'DS 4 Pro','reasoning_effort':None,'speed':None}}
        r=self.activate(cfg)
        self.assertTrue(r['config']['roles']['secondary_executor']['fallback']['authorized'])
        self.assertFalse(r['boundary']['client_settings_changed'])
    def test_percentage_has_exact_denominator_and_native_conversion(self):
        cfg=config(); cfg['roles']['primary_executor']['budget']={'max_percent':10,'quota_total':5000,'baseline':3400,'unit':'monthly_credit'}
        self.activate(cfg); m=self.create('kimi')
        self.assertEqual(m['effective_policy']['providers']['kimi']['max_observed_spend'],500)
        cfg['run_id']='demo-02'; cfg['roles']['primary_executor']['budget']['quota_total']=None
        with self.assertRaisesRegex(ValueError,'denominator'): self.activate(cfg)
    def test_confirmed_then_cancelled_still_uses_run_call_slot(self):
        cfg=config(); cfg['roles']['primary_executor']['max_calls']=1
        self.activate(cfg); a=self.create('kimi'); b=self.create('kimi')
        c=Cluster(self.workspace,'test')
        try:
            source=self.workspace/'inputs/source.txt'; source.write_text('READY')
            for m in (a,b): c.add_source(m['task_id'],source); c.package(m['task_id'])
            s=c.prepare_submission(a['task_id'],'producer')
            # Simulates preserved historical confirmed+cancelled record only;
            # no real GUI, no claim of actual external consumption.
            with c._mutate(a['task_id']) as m:
                m['submissions'][0].update(status='cancelled',started_at=now(),confirmed_at=now())
            with self.assertRaisesRegex(ValueError,'cumulative max_calls'):
                tool.run_ctl(self.workspace,['prepare',b['task_id']],mode='test')
            self.assertEqual(c.metrics(b['task_id'])['submission_intents'],0)
        finally: c.close()
    def test_invalid_limits_monthly_fallback_payment_and_unknown_fields(self):
        for field,value in [('max_calls',True),('max_calls',-1),('quota_period','daily'),('budget',{'max_observed_spend':float('nan')}),('fallback',{'authorized':False,'on':['model_unavailable'],'requested':{'model':'Pro'}}),('automatic_topup',True)]:
            cfg=config(); cfg['roles']['primary_executor'][field]=value; self.save_cfg(cfg)
            with self.subTest(field=field,value=value),self.assertRaises(ValueError): rp.load_config(self.cfg,self.registry)
    def test_symlink_profile_storage_rejected(self):
        (self.workspace/'inputs/run_profiles').symlink_to(self.base,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'symlink'): self.activate()

class ReservedProfileIdentityTest(unittest.TestCase):
    def test_active_and_binding_filenames_cannot_be_run_ids(self):
        registry=json.loads((HERE.parent/'assets/runtime/.cluster/roles.json').read_text())
        data=json.loads((HERE.parent/'templates/run-profile.json').read_text())
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'profile.json'
            for run_id in ('active','ACTIVE','demo.bindings'):
                data['run_id']=run_id;p.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError,'reserved'):
                    rp.load_config(p,registry)

if __name__=='__main__': unittest.main(verbosity=2)
