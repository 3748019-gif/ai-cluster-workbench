"""New config/launcher contracts, local only. Does not rebuild a frozen kit."""
import copy,importlib.util,json,sys,tempfile,unittest
from pathlib import Path
KIT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(KIT/'scripts'),str(KIT/'assets/runtime'),str(KIT/'assets/runtime/tests')]
import run_profile as rp
from test_assignment import config,spec
from cluster.members import defaults
class AssignmentProfileTest(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def cfg(self):
  c=json.loads((KIT/'templates/run-profile.json').read_text());c.update(run_id='dynamic-test',authorization_source='This test user authorization',assignment_v1=config());return c
 def load(self,c):
  p=self.base/'external.json';p.write_text(json.dumps(c));return rp.load_config(p,defaults())
 def test_dynamic_explicit_optional_policy_validates(self):self.assertEqual(self.load(self.cfg())['config']['assignment_v1']['preferred_member'],'workbuddy')
 def test_absent_policy_preserves_legacy_profile_bytes_and_roles(self):
  c=self.cfg();c.pop('assignment_v1');r=self.load(c);self.assertNotIn('assignment_v1',r['config']);self.assertEqual(r['config']['roles']['primary_executor']['member'],'kimi')
 def test_no_unregistered_service_from_candidate_config(self):
  c=self.cfg();c['assignment_v1']['candidates'][0]['member']='uninstalled-service'
  with self.assertRaisesRegex(ValueError,'Unregistered'):self.load(c)
 def test_requested_model_not_observed_or_client_switch(self):
  c=self.cfg();c['assignment_v1']['candidates'][0]['model']=dict(requested='target',observed=None,observation_source=None,observed_at=None);r=self.load(c);self.assertIsNone(r['config']['assignment_v1']['candidates'][0]['model']['observed']);self.assertFalse(r['boundary']['client_settings_changed'])
 def test_observed_identity_needs_evidence_and_quota_unit(self):
  c=self.cfg();c['assignment_v1']['candidates'][0]['model']['observation_source']=None
  with self.assertRaisesRegex(ValueError,'Observed model'):self.load(c)
  c=self.cfg();c['assignment_v1']['candidates'][0]['quota']['value']=5
  with self.assertRaisesRegex(ValueError,'native unit'):self.load(c)
 def test_unknown_assignment_fields_rejected(self):
  c=self.cfg();c['assignment_v1']['auto_purchase']=True
  with self.assertRaises(ValueError):self.load(c)
