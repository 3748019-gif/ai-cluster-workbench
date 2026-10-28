import copy
import json
import subprocess
import sys
import unittest
from test_core import CoreTest, spec
from cluster.core import GuardError, now, js


class ControllerModelTest(unittest.TestCase):
    setUp=CoreTest.setUp
    tearDown=CoreTest.tearDown

    def observe(self,kind='turn_context',**kw):
        data={'kind':kind,'observed_at':now(),'evidence_ref':'synthetic-turn-context.json',
              'model':'gpt-6.1-sol','reasoning_effort':'ultra','service_tier':None,'speed':None}
        data.update(kw);return data

    def test_exact_presets_and_default_does_not_infer_actual_or_switch_app(self):
        presets={p['preset']:(p['model'],p['reasoning_effort']) for p in self.c.controller_model_presets()['presets']}
        self.assertEqual(presets,{'gpt-6.1-sol/current':('gpt-6.1-sol',None),
            'gpt-6.1-sol/ultra':('gpt-6.1-sol','ultra'),'gpt-6-astra/ultra':('gpt-6-astra','ultra'),
            'gpt-6-astra/xhigh':('gpt-6-astra','xhigh')})
        config=self.c.controller_model_config();self.assertFalse(config['application_model_changed'])
        self.assertFalse((self.root/'.cluster/controller_model.json').exists())
        m=self.c.create(spec());self.assertIsNone(m['controller_model']['requested']['reasoning_effort'])
        self.assertEqual(m['controller_model']['observed']['status'],'unknown')
        self.assertIsNone(m['controller_model']['observed']['model']);self.assertEqual(m['controller_model']['observations'],[])

    def test_change_only_affects_future_rounds_without_role_or_submission_changes(self):
        first=self.c.create(spec());before=copy.deepcopy(first);roles=self.c.roles();events=self.c.events(first['task_id'])
        self.c.configure_controller_model('gpt-6-astra/ultra','Synthetic user request')
        second=self.c.new_work(first['task_id'],spec())
        self.assertEqual(second['controller_model']['requested']['preset'],'gpt-6-astra/ultra')
        self.assertEqual(self.c.get(first['task_id']),before);self.assertEqual(self.c.events(first['task_id']),events)
        self.assertEqual(self.c.roles(),roles);self.assertEqual(second['submissions'],[])
        self.assertEqual((first['producer'],first['reviewer']),(second['producer'],second['reviewer']))

    def test_spec_override_requires_authority_and_freezes_exact_request(self):
        s=spec();s['controller_model_preset']='gpt-6-astra/xhigh'
        with self.assertRaisesRegex(ValueError,'authorization_source'):self.c.create(s)
        s['authorization_source']='Synthetic per-round request';m=self.c.create(s)
        self.c.configure_controller_model('gpt-6.1-sol/ultra','Different later request')
        self.assertEqual(self.c.get(m['task_id'])['controller_model']['requested']['reasoning_effort'],'xhigh')
        for bad in ('unknown',None,[],True):
            with self.assertRaises(ValueError):self.c.configure_controller_model(bad,'Test authority')
        with self.assertRaises(GuardError):self.c.configure_controller_model('gpt-6-astra/ultra','')

    def test_observed_actual_kept_separate_append_only_and_unknown_not_inferred(self):
        self.c.configure_controller_model('gpt-6-astra/xhigh','Synthetic request');t=self.c.create(spec())['task_id']
        requested=copy.deepcopy(self.c.get(t)['controller_model']['requested']);state=self.c.record_controller_model_observation(t,self.observe())
        self.assertEqual(state['requested'],requested);self.assertEqual(state['observed']['model'],'gpt-6.1-sol')
        self.assertEqual(state['observed']['reasoning_effort'],'ultra');first=copy.deepcopy(state['observations'][0]);old_events=self.c.events(t)
        unknown=self.observe('unknown',model=None,reasoning_effort=None,note='No current UI setting observed')
        state=self.c.record_controller_model_observation(t,unknown)
        self.assertEqual(state['observations'][0],first);self.assertEqual(self.c.events(t)[:-1],old_events)
        self.assertEqual(state['observed']['status'],'unknown');self.assertIsNone(state['observed']['model'])
        packet=self.c.decision_packet(t);self.assertEqual(packet['controller_model']['requested'],requested)
        self.assertIsNone(packet['controller_model']['observed']['reasoning_effort'])

    def test_latest_observation_uses_actual_time_not_offset_string_or_insertion(self):
        t=self.c.create(spec())['task_id']
        self.c.record_controller_model_observation(t,self.observe(observed_at='2026-09-30T14:00:00+08:00'))
        state=self.c.record_controller_model_observation(t,self.observe('ui',observed_at='2026-09-30T07:00:00+00:00',model='gpt-6-astra',reasoning_effort='xhigh'))
        self.assertEqual(state['observed']['model'],'gpt-6-astra')
        state=self.c.record_controller_model_observation(t,self.observe(observed_at='2026-09-30T06:30:00+00:00'))
        self.assertEqual(state['observed']['model'],'gpt-6-astra')

    def test_invalid_or_unfrozen_observation_rejected_without_mutation(self):
        t=self.c.create(spec())['task_id'];before=self.c.get(t)
        for data in (self.observe('unknown'),self.observe(kind='backend_api'),self.observe(evidence_ref=''),
                     self.observe(observed_at='2026-09-30T00:00:00'),self.observe(reasoning_effort='極高'),
                     self.observe(raw_text='PRIVATE_SENTINEL')):
            with self.assertRaises(GuardError):self.c.record_controller_model_observation(t,data)
            self.assertEqual(self.c.get(t),before)
        with self.c._mutate(t) as old:old.pop('controller_model')
        self.assertIsNone(self.c.decision_packet(t)['controller_model'])
        with self.assertRaisesRegex(GuardError,'Historical task'):self.c.record_controller_model_observation(t,self.observe())

    def test_cli_presets_configuration_and_observation(self):
        base=[sys.executable,'-m','cluster','--root',str(self.root),'--mode','test']
        def command(*args):return json.loads(subprocess.run(base+list(args),capture_output=True,text=True,check=True).stdout)
        self.assertEqual(len(command('controller-model-presets')['presets']),4)
        command('set-controller-model','--preset','gpt-6-astra/ultra','--authority','Synthetic request')
        self.assertEqual(command('controller-model-config')['requested']['model'],'gpt-6-astra')
        t=self.c.create(spec())['task_id'];path=self.root/'model-observed.json';path.write_text(js(self.observe()))
        state=command('observe-controller-model',t,'--file',str(path));self.assertEqual(state['observed']['model'],'gpt-6.1-sol')
        self.assertEqual(state['requested']['model'],'gpt-6-astra')


del CoreTest
