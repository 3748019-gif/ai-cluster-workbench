"""Dynamic paths: explicitly simulated GUI evidence, no external calls."""
import copy,json,tempfile,unittest
from pathlib import Path
from cluster.core import Cluster,GuardError,js,now,digest
from cluster.assignment import independent,validate_assignment
from cluster.document_review import document_statement_hash
from cluster.members import defaults

def candidate(i,member,roles=('executor','reviewer'),actor=None,session=None):
 return dict(id=i,member=member,actor_id=actor or i+'-actor',session_id=session or i+'-session',roles=list(roles),availability='available',capabilities={'drafting':'observed','local_processing':'observed','independent_review':'observed'},inputs_readable=True,outputs_writable=True,model=dict(requested=None,observed='MODEL-'+i,observation_source='LOCAL_SIM fixture',observed_at=now()),isolation=dict(status='isolated',evidence='LOCAL_SIM fresh-context fixture',inherits_actors=[],shared_context='excluded'),quota=dict(status='unknown',unit=None,value=None,observation_source=None))
def config():
 return dict(version=1,authorization_source='Current isolated test authority',preferred_member='workbuddy',candidates=[candidate('wb_exec','workbuddy'),candidate('ki_exec','kimi'),candidate('codex','codex',('executor',)),candidate('cx_review','codex',('reviewer',))])
def spec(unit='SIM-UNIT'):
 return dict(objective='Synthesize two synthetic statements',task_type='document',task_route='open_research',work_kind='local',rules=['Preserve original scope'],acceptance=['Both stable items independently checked'],validators=[{'kind':'json','required_fields':['task_id','statements']}],review_required=True,result_protocol='offline_document_v1',authorization_source='Current example authority',policy_overrides={'network_access':'offline'},assignment_v1=config(),assignment_request={'reviewer':'ki_exec','required_capabilities':['drafting'],'required_inputs':['original.txt'],'output_contract':{'producer':{'statements':'Stable A/B IDs and original values','sources':'Empty list'},'reviewer':{'passed_checks':'Every scoped claim: claim_id, supplied semantic_sha256, basis, source_artifacts','confirmed_errors':'id, claim_id, semantic_sha256, location, description, source_artifacts, evidence, suggested_fix','suspicions':'List','unverified':'List','wording_suggestions':'List','blocking':'Boolean','sources':'Empty list','claim_checks':'Empty list'}}},review_cycle_contract=dict(version=1,unit_id=unit,scope_id='SIM-STABLE',scope_item_ids=['A','B'],dependency_map={},user_rules=[],exceptions=[],required_source_artifacts=[],source_read_allowed=True,review_role='secondary_executor'))
class AssignmentTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.c=Cluster(self.root,'test');self.seq=0
 def tearDown(self):self.c.close();self.tmp.cleanup()
 def file(self,data,name=None):
  self.seq+=1;p=self.root/(name or 'input-'+str(self.seq)+'.json');p.write_text(js(data) if not isinstance(data,str) else data);return p
 def new(self,s=None):return self.c.create(s or spec())['task_id']
 def source(self,t):return self.c.add_source(t,self.file('A=3; B=4','original.txt'),'original.txt')
 def inputs(self,t):self.c.register_review_inputs(t,[dict(artifact_id=a,read_allowed=True,required=True) for a in self.c.get(t)['source_files']])
 def send(self,t,phase,body):
  m=self.c.get(t);s=self.c.prepare_submission(t,phase);token=self.c.acquire_gui(t);inst=self.c._assignment_instance(m,phase)
  def p(stage,**kw):
   d=dict(kind='simulated',task_id=t,submission_id=s['submission_id'],provider=s['provider'],stage=stage,window='LOCAL_SIM',observed_at=now(),raw_text=s['prompt'],session_id=inst['session_id'],visible_model=inst['model']['observed']);d.update(kw)
   if stage=='preflight' and s.get('session_policy'):
    base=dict(kind='simulated',source='simulated_test_fixture',observation_type='ui_state',scope='active_conversation',window='LOCAL_SIM',prior_messages_visible=False,generation_active=False,message_count=0)
    before=dict(base,observed_at=now(),raw_text='LOCAL_SIM empty conversation',composer_empty=True,composer_value='',empty_state_marker='empty conversation');after=dict(base,observed_at=now(),raw_text=s['prompt']);d['observed_at']=now();d['fresh_review_session']=dict(policy=s['session_policy'],before_input=before,after_input=after)
   return self.file(d)
  self.c.begin_submission(t,s['submission_id'],token,p('preflight',input_text=s['prompt'],package_hash=s['package_hash'],unexpected_attachments=False,client_available=True,quota_available=True,payment_required=False));self.c.confirm_submission(t,s['submission_id'],token,p('confirmation',message_visible=True));raw=self.file(body);a=self.c.receive(t,s['submission_id'],raw,token,p('output',completed=True,output_hash=digest(raw.read_bytes())));self.c.release_gui(t,token);return a
 def produce(self,t,a=3,b=4):
  self.c.package(t);return self.send(t,'producer',dict(sources=[],statements=[dict(claim_id='A',value=a),dict(claim_id='B',value=b)]))
 def review(self,t,extra=None):
  self.c.package(t,'reviewer');m=self.c.get(t);body=json.loads(self.c.read_artifact(m,m['candidate']));rows={r['claim_id']:r for r in body['statements']};d=dict(sources=[],claim_checks=[],confirmed_errors=[],suspicions=[],unverified=[],wording_suggestions=[],passed_checks=[dict(claim_id=i,semantic_sha256=document_statement_hash(rows[i]),basis='LOCAL_SIM original comparison',source_artifacts=m['source_files']) for i in m['active_review_scope']],blocking=False);d.update(extra or {});return self.send(t,'reviewer',d)
 def ready(self,s=None):
  t=self.new(s);self.source(t);self.inputs(t);self.produce(t);return t
 def follow(self,first,s=None):
  m=self.c.get(first);s=copy.deepcopy(s or m['spec']);s.pop('round_id',None);s.update(parent_task_id=m.get('parent_task_id') or first,previous_round_id=first,work_item_id=m['work_item_id'],followup_reason='New named evidence',new_work='Revise A only');return s
 def test_DR01_DR29_pair_choice_one_executor_not_fixed_brand(self):
  s=spec();s['assignment_request'].pop('reviewer');s['assignment_v1']['candidates'][2]['capabilities']['drafting']='unverified';p=self.c.assignment_plan(s);self.assertTrue(p['feasible']);self.assertEqual(p['executor']['member'],'workbuddy');self.assertEqual(p['fanout'],1);s['assignment_v1']['candidates'][0]['availability']='unavailable';self.assertEqual(self.c.assignment_plan(s)['executor']['member'],'kimi')
 def test_DR02_explicit_codex_routes_real_local_author(self):
  s=spec();s.update(assigned_provider='codex',task_route='file_processing');s['assignment_request'].update(executor='codex',reviewer='wb_exec',required_capabilities=['local_processing']);t=self.new(s);self.source(t);self.c.package(t);self.c.start_local(t);self.c.local_output(t,self.file(dict(task_id=t,statements=[dict(claim_id='A',value=3),dict(claim_id='B',value=4)])));m=self.c.get(t);self.assertEqual(m['producer'],'codex');self.assertEqual(m['author_history'][-1]['authors'][0]['member'],'codex');self.assertFalse(m['submissions'])
 def test_DR03_once_only_ban_not_global(self):
  s=spec();s['assignment_request'].update(forbidden_members=['kimi'],reviewer='cx_review');self.assertTrue(self.c.assignment_plan(s)['feasible']);self.assertEqual(self.c.assignment_plan(spec())['reviewer']['member'],'kimi')
 def test_DR04_DR08_ineligible_pair_rejected_before_creation(self):
  for field,v in [('availability','login_required'),('inputs_readable',False),('outputs_writable',False)]:
   s=spec();s['assignment_request']['executor']='wb_exec';s['assignment_v1']['candidates'][0][field]=v;self.assertFalse(self.c.assignment_plan(s)['feasible'])
   with self.assertRaisesRegex(ValueError,'ASSIGNMENT_BLOCKED'):self.new(s)
  self.assertFalse(self.c.list_tasks())
 def test_DR05_unknown_model_cost_quality_not_fabricated(self):
  s=spec();a=s['assignment_v1']['candidates'][0];a['model']=dict(requested='Target-B',observed=None,observation_source=None,observed_at=None);p=self.c.assignment_plan(s);self.assertIsNone(p['executor']['model']['observed']);self.assertIsNone(p['predicted_cost']);self.assertIsNone(p['quality_score']);a['capabilities']['drafting']='unverified';s['assignment_request']['executor']='wb_exec';self.assertFalse(self.c.assignment_plan(s)['feasible'])
 def test_DR07_local_deterministic_no_external_or_reviewer(self):
  s=spec();s.update(task_type='hash',task_route='file_processing',review_required=False);s.pop('review_cycle_contract');s['assignment_request'].pop('reviewer');s['assignment_request']['required_capabilities']=['local_processing'];t=self.new(s);self.source(t);self.c.package(t);self.c.start_local(t);self.c.local_output(t,self.file(dict(task_id=t,statements=[])));self.assertIsNone(self.c.get(t)['reviewer']);self.assertEqual(self.c.metrics(t)['external_calls_confirmed'],0)
 def test_DR09_requested_not_observed_and_mismatch_refused(self):
  s=spec();s['assignment_v1']['candidates'][0]['model']['requested']='B';t=self.new(s);self.source(t);self.c.package(t);sub=self.c.prepare_submission(t,'producer');m=self.c.get(t);i=m['assignment']['executor'];token=self.c.acquire_gui(t);p=dict(kind='simulated',task_id=t,submission_id=sub['submission_id'],provider=sub['provider'],stage='preflight',window='LOCAL_SIM',observed_at=now(),raw_text=sub['prompt'],input_text=sub['prompt'],package_hash=sub['package_hash'],unexpected_attachments=False,client_available=True,quota_available=True,session_id=i['session_id'],visible_model='B')
  with self.assertRaisesRegex(GuardError,'visible model'):self.c.begin_submission(t,sub['submission_id'],token,self.file(p))
  self.assertEqual(self.c.get(t)['status'],'READY_TO_SUBMIT');self.c.release_gui(t,token)
 def test_DR10_actor_relabel_not_independent(self):
  s=spec();s['assignment_request']['executor']='wb_exec';s['assignment_v1']['candidates'][1]['actor_id']=s['assignment_v1']['candidates'][0]['actor_id'];self.assertFalse(self.c.assignment_plan(s)['feasible'])
 def test_DR11_author_inheritance_through_other_editor(self):
  t=self.ready();self.review(t);old=self.c.get(t);s=self.follow(t);s['assignment_request'].update(executor='ki_exec',reviewer='wb_exec');self.assertFalse(self.c.assignment_plan(s)['feasible']);self.assertEqual(old['author_history'],self.c.get(t)['author_history'])
 def test_DR12_same_session_different_model_not_independent(self):
  s=spec();s['assignment_request']['executor']='wb_exec';r=candidate('wb_review','workbuddy',('reviewer',),session='wb_exec-session');s['assignment_v1']['candidates'].append(r);s['assignment_request']['reviewer']='wb_review';self.assertFalse(self.c.assignment_plan(s)['feasible'])
 def test_DR13_same_app_requires_full_isolation_evidence(self):
  s=spec();r=candidate('wb_review','workbuddy',('reviewer',));s['assignment_v1']['candidates'].append(r);s['assignment_request'].update(reviewer='wb_review',different_model=True);self.assertTrue(self.c.assignment_plan(s)['feasible']);r['isolation']['shared_context']='unverified';self.assertFalse(self.c.assignment_plan(s)['feasible'])
 def test_DR14_orchestrator_not_author_but_fact_editor_is(self):
  s=spec();s['assignment_request']['reviewer']='cx_review';t=self.ready(s);self.assertEqual({a['member'] for r in self.c.get(t)['author_history'] for a in r['authors']},{'workbuddy'});self.c._assignment_guard(self.c.get(t),'reviewer')
  with self.c._mutate(t) as m:
   a=self.c._artifact(m,b'{"statements":[]}','producer','codex','edit.json',[]);self.c._assignment_provenance(m,a,dict(statements=[]),dict(actor_id='cx_review-actor',member='codex',session_id='cx_review-session'))
  with self.assertRaisesRegex(GuardError,'author history'):self.c._assignment_guard(self.c.get(t),'reviewer')
 def test_DR15_reviewer_authored_replacement_cannot_pass_itself(self):
  t=self.ready();self.review(t,dict(authored_changes=[dict(claim_id='A',new_value=9)]));self.assertIsNotNone(self.c.get(t)['review_authored_content'])
  with self.assertRaisesRegex(GuardError,'authored substantive'):self.c._accepted_gate(self.c.get(t))
 def test_DR20_uncertain_send_cannot_reassign_new_ID_or_old_entry(self):
  t=self.new();self.source(t);self.c.package(t);sub=self.c.prepare_submission(t,'producer');token=self.c.acquire_gui(t);m=self.c.get(t);a=m['assignment']['executor'];p=dict(kind='simulated',task_id=t,submission_id=sub['submission_id'],provider=sub['provider'],stage='preflight',window='LOCAL_SIM',observed_at=now(),raw_text=sub['prompt'],input_text=sub['prompt'],package_hash=sub['package_hash'],unexpected_attachments=False,client_available=True,quota_available=True,visible_model=a['model']['observed'],session_id=a['session_id']);self.c.begin_submission(t,sub['submission_id'],token,self.file(p));self.c.release_gui(t,token);s=self.follow(t);s['assignment_request'].update(executor='ki_exec',reviewer='cx_review')
  for entry in ('create','new_work'):
   with self.assertRaisesRegex(GuardError,'uncertain|running|Inspect existing'):self.c.create(s) if entry=='create' else self.c.new_work(t,s,t)
  self.assertEqual(len(self.c.get(t)['submissions']),1);self.assertTrue(self.new(spec('OTHER')))
 def test_DR21_DR16_safe_followup_scoped_original_change_only(self):
  s=spec();s['assignment_request']['source_item_map']={'original.txt':['A','B']};first=self.ready(s);self.review(first);old=self.c.get(first);s=self.follow(first);s['assignment_request']['source_item_map']['notice.txt']=['A'];t=self.new(s);self.c.import_artifact(t,first,old['source_files'][0],'original.txt');self.c.add_source(t,self.file('A now 5'),'notice.txt');self.inputs(t);self.produce(t,a=5);p=self.c.review_cycle_packet(t);self.assertEqual(p['review_scope'],['A']);self.assertEqual(p['inherited_ids'],['B']);self.review(t);self.c._review_cycle_gate(self.c.get(t));self.assertEqual(len(self.c.review_cycle_status(t)['rounds']),2);self.assertEqual(self.c.get(first)['candidate'],old['candidate'])
 def test_DR22_native_platform_cap_not_reset_by_model_alias(self):
  t=self.ready();s=spec('OTHER');s['assignment_v1']['candidates'][0]['id']='wb_model2';s['policy_overrides']['max_calls']=1
  with self.c._mutate(t) as m:m['effective_policy']['policy_id']='same-policy'
  t2=self.new(s)
  with self.c._mutate(t2) as m:m['effective_policy']['policy_id']='same-policy'
  # Dispatch calls the actual shared platform cap, even with a new instance alias.
  with self.assertRaisesRegex(GuardError,'cap reached'):self.c._budget_guard(self.c.get(t2)['producer'],self.c.get(t2),'producer')
 def test_DR23_preflight_collects_missing_input_and_schema(self):
  s=spec();s['assignment_request']['required_inputs'].append('missing.pdf');s['assignment_request']['output_contract'].pop('producer');t=self.new(s);self.source(t)
  with self.assertRaises(ValueError) as cm:self.c.package(t)
  self.assertIn('missing.pdf',str(cm.exception));self.assertIn('output field definitions',str(cm.exception));self.assertFalse(self.c.get(t)['submissions'])
 def test_DR28_self_contained_handoff_after_reopen(self):
  t=self.new();self.source(t);self.c.package(t);before=self.c.assignment_handoff(t);self.c.close();self.c=Cluster(self.root,'test');self.assertEqual(before,self.c.assignment_handoff(t));raw=self.c.read_artifact(self.c.get(t),self.c.get(t)['packages']['producer']);self.assertIn(b'A=3; B=4',raw);self.assertIn(b'output_contract',raw)
 def test_frozen_snapshot_cannot_mutate_into_self_review(self):
  t=self.ready()
  with self.c._mutate(t) as m:m['assignment']['reviewer']['actor_id']=m['assignment']['executor']['actor_id']
  with self.assertRaises(GuardError):self.c.package(t,'reviewer')
 def test_DR17_third_round_cannot_reset_by_new_model_alias(self):
  t=self.ready();self.review(t);old=self.c.get(t);s=self.follow(t);second=self.new(s);self.c.import_artifact(second,t,old['source_files'][0],'original.txt');self.inputs(second);self.produce(second,a=5);self.review(second);s=self.follow(second);s['assignment_v1']['candidates'][0]['id']='wb_other_model';s['assignment_v1']['candidates'][0]['session_id']='fresh-session'
  try:
   third=self.new(s);self.c.import_artifact(third,second,self.c.get(second)['source_files'][0],'original.txt');self.inputs(third);self.produce(third,a=6)
   with self.assertRaisesRegex(GuardError,'REVIEW_LIMIT|third|authorization'):self.c.package(third,'reviewer')
  except GuardError as e:self.assertRegex(str(e),'REVIEW_LIMIT|third|authorization')
  self.assertEqual(len(self.c.review_cycle_status(second)['rounds']),2)
 def test_exact_projection_cannot_hide_changed_metrics_or_prose(self):
  s=spec();s['assignment_request']['statement_projections']={'metrics':{'claim_id':'A','pointer':'/value'}};t=self.new(s);self.source(t);self.inputs(t);self.c.package(t);self.send(t,'producer',dict(sources=[],metrics=99,statements=[dict(claim_id='A',value=3),dict(claim_id='B',value=4)]))
  with self.assertRaisesRegex(ValueError,'projection differs'):self.c.package(t,'reviewer')
 def test_short_schema_missing_known_required_array_rejected(self):
  s=spec();s['assignment_request']['output_contract']['reviewer'].pop('unverified');t=self.ready(s)
  with self.assertRaisesRegex(ValueError,'definition missing: unverified'):self.c.package(t,'reviewer')
 def test_DR14_isolated_local_receipts_count_separately_and_cannot_fake_GUI(self):
  s=spec();s['assignment_request']['reviewer']='cx_review';t=self.ready(s);self.c.package(t,'reviewer');sub=self.c.prepare_isolated_review(t);role=self.c.get(t)['assignment']['reviewer']
  def proof(action,**extra):
   p=dict(kind='simulated',task_id=t,submission_id=sub['submission_id'],instance_id=role['session_id'],fork_turns='none',operation=action,tool_receipt='LOCAL_SIM collaboration dispatch receipt',observed_at=now(),package_sha256=sub['package_hash']);p.update(extra);return self.file(p)
  self.c.isolated_review_event(t,sub['submission_id'],proof('begin'),'begin')
  with self.assertRaisesRegex(GuardError,'Uncertain|cannot|intent'):self.c.isolated_review_event(t,sub['submission_id'],proof('begin'),'begin')
  self.c.isolated_review_event(t,sub['submission_id'],proof('confirm'),'confirm');m=self.c.get(t);body=json.loads(self.c.read_artifact(m,m['candidate']));checks=[dict(claim_id=r['claim_id'],semantic_sha256=document_statement_hash(r),basis='LOCAL_SIM isolated original check',source_artifacts=m['source_files']) for r in body['statements']];raw=self.file(dict(sources=[],claim_checks=[],passed_checks=checks,confirmed_errors=[],suspicions=[],unverified=[],wording_suggestions=[],blocking=False));self.c.isolated_review_event(t,sub['submission_id'],proof('capture',output_sha256=digest(raw.read_bytes()),completed=True),'capture',raw);self.c.ingest_output(t,sub['submission_id']);self.c._accepted_gate(self.c.get(t));metrics=self.c.metrics(t);self.assertEqual(metrics['external_calls_confirmed'],1);self.assertEqual(metrics['isolated_local_calls_confirmed'],1)
 def test_legacy_entry_cannot_drop_dynamic_author_or_review_lineage(self):
  t=self.ready();self.review(t);s=self.follow(t);s.pop('assignment_v1')
  with self.assertRaisesRegex(GuardError,'lineage'):self.new(s)
  s=self.follow(t);s.pop('review_cycle_contract')
  with self.assertRaisesRegex(GuardError,'cycle cannot be dropped'):self.new(s)
 def test_explicit_user_provider_cannot_be_overridden_by_candidate_selector(self):
  s=spec();s['assigned_provider']='codex';s['assignment_request']['executor']='wb_exec';self.assertFalse(self.c.assignment_plan(s)['feasible'])
 def test_real_producer_transport_extras_are_audited_not_semantic_context(self):
  t=self.new();self.source(t);m=self.c.get(t);a=m['assignment']['executor'];s={'submission_id':'example'}
  raw=dict(statements=[dict(claim_id='A',value=3)],stable_unit=m['assignment']['unit_id'],phase='producer',producer_member=a['member'],producer_actor_id=a['actor_id'],session_id=a['session_id'],model_observed=a['model']['observed'],generated_at=now(),not_approved=True,work_kind='authoring',unknown_scope='must remain')
  out,repairs=self.c._assignment_transport_metadata(m,s,raw)
  self.assertEqual(out,dict(statements=[dict(claim_id='A',value=3)],unknown_scope='must remain'));self.assertEqual(raw['work_kind'],'authoring');self.assertEqual(len(repairs),9)
  raw['producer_actor_id']='not-the-author'
  with self.assertRaisesRegex(ValueError,'transport conflict'):self.c._assignment_transport_metadata(m,s,raw)
 def test_declared_business_field_never_discarded_as_transport(self):
  s=spec();s['assignment_request']['output_contract']['producer']['phase']='Actual material phase, a business fact';t=self.new(s);m=self.c.get(t)
  out,repairs=self.c._assignment_transport_metadata(m,{},dict(phase='solid',unknown_scope='2027'))
  self.assertEqual(out,dict(phase='solid',unknown_scope='2027'));self.assertFalse(repairs)
 def test_followup_handoff_carries_exact_prior_candidate_without_source_or_verdict(self):
  first=self.ready();self.review(first);old=self.c.get(first);t=self.new(self.follow(first));self.c.import_artifact(t,first,old['source_files'][0],'original.txt');self.inputs(t);self.c.package(t);m=self.c.get(t);prior=m['assignment_previous_candidate'];self.assertNotIn(prior['artifact_id'],m['source_files']);self.assertEqual(self.c.read_artifact(m,prior['artifact_id']),self.c.read_artifact(old,old['candidate']));raw=self.c.read_artifact(m,m['packages']['producer']);self.assertIn(b'EXACT PRIOR BUSINESS CANDIDATE',raw);self.assertNotIn(b'passed_checks',raw);self.assertIn(prior['artifact_id'],self.c.artifact(m,m['packages']['producer'])['input_artifacts'])
 def test_unsent_reservation_recovery_keeps_second_round_and_rejects_uncertain_cancel(self):
  first=self.ready();self.review(first);f=self.c.get(first);second=self.new(self.follow(first));self.c.import_artifact(second,first,f['source_files'][0],'original.txt');self.inputs(second);self.produce(second,a=5);self.c.package(second,'reviewer');reserved=self.c.prepare_submission(second,'reviewer');self.c.fail(second,'ATTACHMENT_FAILED','LOCAL_SIM input channel failed before send');self.c.cancel_prepared_submission(second,reserved['submission_id'],'LOCAL_SIM no send intent; safe channel replacement');self.assertEqual(self.c.get(second)['status'],'ATTACHMENT_FAILED')
  old=self.c.get(second);s=self.follow(second);s['work_kind']='review';third=self.new(s);self.c.import_artifact(third,second,old['source_files'][0],'original.txt');self.inputs(third);self.c.package(third);a=self.c.adopt_candidate(third,second,old['candidate']);self.assertEqual(a['hash'],self.c.artifact(old,old['candidate'])['hash']);self.review(third);self.c._review_cycle_gate(self.c.get(third));self.assertEqual(len(self.c.review_cycle_status(third)['rounds']),2)
  s=spec('UNCERTAIN-CANCEL');s['assignment_request']['reviewer']='cx_review';t=self.ready(s);self.c.package(t,'reviewer');sub=self.c.prepare_isolated_review(t);role=self.c.get(t)['assignment']['reviewer'];p=dict(kind='simulated',task_id=t,submission_id=sub['submission_id'],instance_id=role['session_id'],fork_turns='none',operation='begin',tool_receipt='LOCAL_SIM dispatch intent',observed_at=now(),package_sha256=sub['package_hash']);self.c.isolated_review_event(t,sub['submission_id'],self.file(p),'begin')
  with self.assertRaisesRegex(GuardError,'never-started'):self.c.cancel_prepared_submission(t,sub['submission_id'],'Cannot relabel actual uncertainty')
 def test_short_handoff_includes_verified_saved_ID_chain_and_explicit_schema(self):
  first=self.ready();self.review(first);old=self.c.get(first);t=self.new(self.follow(first));a=self.c.import_artifact(t,first,old['source_files'][0],'original.txt');self.inputs(t);h=self.c.assignment_handoff(t);chain=h['inputs'][0]['saved_byte_identity_chain'];self.assertEqual([r['artifact_id'] for r in chain],[a['artifact_id'],old['source_files'][0]]);self.assertEqual(chain[0]['sha256'],chain[1]['sha256']);self.assertEqual(h['response_schema']['protocol'],'offline_document_v1');self.assertEqual(h['response_schema']['required_business_field_types']['statements'],'array')
  with self.c._mutate(t) as m:self.c.artifact(m,a['artifact_id'])['origin_sha256']='0'*64
  with self.assertRaisesRegex(GuardError,'identity conflict'):self.c.assignment_handoff(t)
