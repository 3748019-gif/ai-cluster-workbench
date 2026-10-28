"""Missing required templates fail before a model call; local contracts are explicit."""
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from cluster.core import Cluster,GuardError
import cluster.core as core

class RequiredTemplateTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.c=Cluster(self.root,'test')
  self.source=self.root/'original.txt';self.source.write_text('Non-sensitive original')
 def tearDown(self):self.c.close();self.tmp.cleanup()
 def spec(self,kind):
  return {'objective':'Synthetic template preflight','task_type':kind,'review_required':False,'rules':['Preserve evidence'],'acceptance':['Complete output'],'validators':[{'kind':'json','required_fields':['task_id']}]}
 def missing(self,task,phase,name):
  template=Path(core.__file__).resolve().parents[1]/'templates'/name
  original=Path.exists
  with patch.object(Path,'exists',lambda p:False if p==template else original(p)):
   with self.assertRaisesRegex(GuardError,'Required role template missing'):self.c.package(task,phase)
  self.assertEqual(self.c.metrics(task)['submission_intents'],0)
 def test_member_producer_missing_template_is_blocking(self):
  s=self.spec('research');s.update(result_protocol='member_content_v1',assigned_provider='kimi')
  t=self.c.create(s)['task_id'];self.c.add_source(t,self.source);self.missing(t,'producer','member_primary.txt')
  self.assertFalse(self.c.get(t)['packages'])
 def test_offline_document_missing_template_is_blocking(self):
  s=self.spec('document');s.update(result_protocol='offline_document_v1',policy_overrides={'network_access':'offline'},authorization_source='Synthetic local offline authority')
  t=self.c.create(s)['task_id'];self.c.add_source(t,self.source);self.missing(t,'producer','offline_document.txt')
 def test_generic_local_contract_is_disclosed(self):
  t=self.c.create(self.spec('document'))['task_id'];self.c.add_source(t,self.source);a=self.c.package(t)
  text=self.c.read_artifact(self.c.get(t),a['artifact_id']).decode()
  self.assertIn('明确通用本地契约',text)
  self.assertTrue(any(e['event']=='GENERIC_LOCAL_CONTRACT' for e in self.c.events(t)))

if __name__=='__main__':unittest.main()
