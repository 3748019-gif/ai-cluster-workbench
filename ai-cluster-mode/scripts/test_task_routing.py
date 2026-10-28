"""External profile copies freeze per-task routing without changing daily roles."""
import json,unittest
from test_run_profile import ProfileTests,config,tool,rp
class RoutingProfileTests(unittest.TestCase):
 setUp=ProfileTests.setUp
 tearDown=ProfileTests.tearDown
 save_cfg=ProfileTests.save_cfg
 activate=ProfileTests.activate
 create=ProfileTests.create
 def test_external_default_local_then_explicit_research_override(self):
  cfg=config();cfg['default_task_route']='file_processing';self.activate(cfg)
  a=self.create();self.assertEqual(a['routing']['producer'],'codex');self.assertEqual(a['spec']['task_route'],'file_processing')
  d=json.loads(self.request.read_text());d.pop('assigned_provider');d['task_route']='open_research';d.pop('review_required');tool.write_json(self.request,d)
  b=rp.create_task(tool,self.workspace,self.request,mode='test')['task'];self.assertEqual(b['routing']['producer'],'kimi');self.assertEqual(b['routing']['reviewer'],'workbuddy')
  self.assertEqual(b['role_bindings']['primary_executor'],'kimi');self.assertEqual(b['role_bindings']['secondary_executor'],'workbuddy')
 def test_invalid_default_and_mixed_requires_reason(self):
  cfg=config();cfg['default_task_route']='invalid';self.save_cfg(cfg)
  with self.assertRaises(ValueError):rp.load_config(self.cfg,self.registry)
  cfg['default_task_route']='mixed';self.activate(cfg)
  with self.assertRaises(ValueError):self.create()
 def test_old_snapshot_preserved_when_next_profile_changes_route(self):
  cfg=config();cfg['default_task_route']='file_processing';self.activate(cfg);a=self.create()
  cfg['run_id']='demo-02';cfg['default_task_route']='open_research';self.activate(cfg)
  self.assertEqual(tool.run_ctl(self.workspace,['show',a['task_id']]),a)
del ProfileTests
if __name__=='__main__':unittest.main(verbosity=2)
