import copy
import json
import unittest
from pathlib import Path
from test_core import CoreTest,spec
from cluster.core import GuardError,js,digest,now
from cluster.permissions import default_policy
from cluster.research import claim_hash,check_review

class OpenResearchTest(unittest.TestCase):
    setUp=CoreTest.setUp
    tearDown=CoreTest.tearDown
    proof=CoreTest.proof
    external_start=CoreTest.external_start
    local_candidate=CoreTest.local_candidate
    def policy(self,**kw):
        p=default_policy()
        for q in p['providers'].values():q.update(kw)
        (self.root/'.cluster/budget_policy.json').write_text(js(p));return p
    def research_spec(self,provider='kimi',kind='research'):
        s=spec();s.update(task_type='research',work_kind=kind,assigned_provider=provider,
            validators=[{'kind':'research'}]);return s
    def start(self,s=None):
        t=self.c.create(s or self.research_spec())['task_id'];self.c.add_source(t,self.source);self.c.package(t);return t
    def data(self,t,s,provider='kimi'):
        return {'task_id':t,'submission_id':s['submission_id'],'work_item_id':self.c.get(t)['work_item_id'],'round_id':self.c.get(t)['round_id'],
            'summary':'Done','draft_markdown':'Public evidence summary [C1]','gaps':[],'changes':[],
            'sources':[{'source_id':'S1','collected_by':provider,'url':'https://example.edu/course','title':'Course',
                'published_at':None,'updated_at':None,'accessed_at':now(),'access_status':'full_text',
                'applicable_cohort_or_version':None,'level':'undergraduate','scope':'course','locator':'Overview',
                'quote':'A chemistry course','excerpt_is_complete':False,'omissions_marked':True,
                'supports_claim_ids':['C1'],'limits':[],'raw_artifact_id':None}],
            'claims':[{'claim_id':'C1','text':'A chemistry course','scope':'course','time':None,'producer':provider,'source_ids':['S1'],'location':'paragraph 1'}]}
    def complete(self,t,provider='kimi'):
        s,lock=self.external_start(t);data=self.data(t,s,provider);p=self.root/'reply.json';p.write_text(js(data))
        a=self.c.receive(t,s['submission_id'],p,lock,self.proof(t,s,'output',completed=True,output_hash=digest(p.read_bytes())))
        self.c.release_gui(t,lock);self.c.validate(t);return a,data
    def review_task(self,parent,origin,provider='workbuddy',previous=None):
        s=self.research_spec(provider,'review');s.update(validators=[{'kind':'research'}],followup_reason='confirm revision',new_work='read changed evidence')
        t=self.c.new_work(parent,s,previous)['task_id'];self.c.add_source(t,self.source);self.c.package(t)
        a=self.c.adopt_candidate(t,origin,self.c.get(origin)['candidate']);self.c.package(t,'reviewer');return t,a
    def finish_review(self,t,a,origin_data,result='verified',resolutions=None):
        s,lock=self.external_start(t,'reviewer');provider=s['provider'];data=self.data(t,s,provider)
        data.pop('claims');data.pop('draft_markdown')
        data.update(candidate_artifact_id=a['artifact_id'],candidate_sha256=a['hash'],confirmed_errors=[],suspicions=[],
            unverified=[],wording_suggestions=[],passed_checks=[],blocking=result!='verified',
            claim_checks=[{'claim_id':'C1','claim_sha256':claim_hash(origin_data['claims'][0]),'result':result,
                'blocking':result!='verified','reason':'Evidence checked','source_ids':['S1']}],issue_resolutions=resolutions or [])
        p=self.root/'review.json';p.write_text(js(data));self.c.receive(t,s['submission_id'],p,lock,self.proof(t,s,'output',completed=True,output_hash=digest(p.read_bytes())))
        self.c.release_gui(t,lock);self.c.validate(t);return data
    def test_T01_null_four_realistic_completed_rounds(self):
        self.policy();parent=self.c.create(spec())['task_id'];last=None
        for n in range(4):
            s=self.research_spec();s.update(followup_reason='gap '+str(n),new_work='different official version '+str(n))
            t=self.c.new_work(parent,s,last)['task_id'];self.c.add_source(t,self.source);self.c.package(t);self.complete(t);last=t
        self.assertEqual(self.c.rollup(parent)['confirmed_external_calls'],4)
        self.assertEqual(self.c.get(last)['round_id'],4)
    def test_T02_zero_positive_invalid_limits(self):
        for value in [-1,True,False,'unlimited',1.2]:
            self.policy(max_calls=value)
            with self.assertRaisesRegex(ValueError,'CONFIG_ERROR'):self.c.create(self.research_spec())
        self.policy(max_calls=0);t=self.start()
        with self.assertRaisesRegex(GuardError,'call cap'):self.c.prepare_submission(t,'producer')
        self.policy(max_calls=1);t=self.start();self.complete(t);u=self.start()
        with self.assertRaisesRegex(GuardError,'call cap'):self.c.prepare_submission(u,'producer')
    def test_T03_dynamic_permissions_and_source_quotes(self):
        self.policy();self.source.write_text('Historical quotation: 不联网 最多1次')
        t=self.start();m=self.c.get(t);pkg=self.c.read_artifact(m,m['packages']['producer']).decode()
        control=pkg.split('【生效指令区结束】')[0]
        self.assertNotIn('不联网',control);self.assertIn('不设人为固定搜索次数上限',control)
        self.assertIsNone(m['effective_policy']['providers']['kimi']['max_calls'])
        s=self.research_spec();s['rules']=['不联网']
        with self.assertRaisesRegex(ValueError,'conflicts'):self.c.create(s)
    def test_T04_compact_uses_same_compiler(self):
        self.policy();t=self.start();s=self.c.prepare_submission(t,'producer');new=self.c.compact_prepared_prompt(t,s['submission_id'])
        self.assertEqual(s['prompt'],new['prompt']);self.assertNotIn('Read only',s['prompt']);self.assertIn('no fixed search limit',s['prompt'])
    def test_T05_explicit_offline_and_no_external(self):
        self.policy();s=self.research_spec();s.update(policy_overrides={'network_access':'offline'},authorization_source='User explicitly requested offline',rules=['不联网'])
        t=self.start(s);m=self.c.get(t);self.assertIn('本工作单元离线',self.c.read_artifact(m,m['packages']['producer']).decode())
        s=self.research_spec();s['private_data_external']='forbidden'
        with self.assertRaisesRegex(ValueError,'forbids external'):self.c.create(s)
    def test_T06_json_config_error_before_external(self):
        s=self.research_spec();s['validators']=[{'kind':'json','required':['task_id']}]
        with self.assertRaisesRegex(ValueError,'CONFIG_ERROR'):self.c.create(s)
        self.assertEqual(len(self.c.list_tasks()),0)
    def test_T07_capture_survives_parse_failure_and_no_resend(self):
        self.policy();t=self.start();s,lock=self.external_start(t);p=self.root/'bad.txt';p.write_text('not json')
        captured=self.c.capture_output(t,s['submission_id'],p,lock,self.proof(t,s,'output',completed=True,output_hash=digest(p.read_bytes())))
        self.c.release_gui(t,lock)
        with self.assertRaises(GuardError):self.c.ingest_output(t,s['submission_id'])
        m=self.c.get(t);self.assertEqual(m['provider_execution_status'],'completed');self.assertEqual(m['artifact_ingestion_status'],'parse_error')
        self.assertEqual(self.c.read_artifact(m,captured['artifact_id']),b'not json')
        with self.assertRaises(GuardError):self.c.prepare_submission(t,'producer')
    def test_T08_append_sources_and_freeze_old_package(self):
        self.policy();t=self.start();m=self.c.get(t);old=self.c.read_artifact(m,m['packages']['producer']);before=list(m['source_files'])
        self.complete(t);m=self.c.get(t)
        self.assertEqual(old,self.c.read_artifact(m,m['packages']['producer']));self.assertGreater(len(m['source_files']),len(before))
        news=[a for a in m['artifacts'] if a.get('evidence_level')=='member_reported_excerpt'];self.assertEqual(len(news),1)
        self.assertIsNone(news[0]['raw_artifact_id'])
    def test_T09_gap_remains_blocking_not_confirmed_error(self):
        self.policy();parent=self.c.create(spec())['task_id'];t=self.start();a,d=self.complete(t)
        r,ra=self.review_task(parent,t);self.finish_review(r,ra,d,'evidence_gap');m=self.c.get(r)
        self.assertEqual(m['open_issues'][0]['kind'],'evidence_gap')
        self.assertEqual(self.c.metrics(r)['reviewer_reported_errors'],0)
        with self.assertRaises(GuardError):self.c.finalize(r)
    def test_T10_new_round_cannot_bypass_uncertainty(self):
        self.policy();parent=self.c.create(spec())['task_id'];s=self.research_spec();t=self.c.new_work(parent,s)['task_id'];self.c.add_source(t,self.source);self.c.package(t)
        sub=self.c.prepare_submission(t,'producer');lock=self.c.acquire_gui(t);self.c.begin_submission(t,sub['submission_id'],lock,self.proof(t,sub,'preflight'))
        s.update(followup_reason='retry',new_work='new query')
        with self.assertRaisesRegex(GuardError,'existing submission'):self.c.new_work(parent,s,t)
    def test_T11_separate_candidates_and_self_review_rejected(self):
        self.policy();a=self.start();b=self.start(self.research_spec('workbuddy'));aa,ad=self.complete(a);bb,bd=self.complete(b,'workbuddy')
        self.assertNotEqual(aa['artifact_id'],bb['artifact_id'])
        parent=self.c.create(spec())['task_id']
        with self.assertRaisesRegex(GuardError,'own candidate'):self.review_task(parent,a,'kimi')
    def test_T12_stale_claim_review_fails(self):
        self.policy();t=self.start();a,d=self.complete(t);cl=d['claims'][0]
        review={'sources':copy.deepcopy(d['sources']),'claim_checks':[{'claim_id':'C1','claim_sha256':claim_hash(cl),'result':'verified','blocking':False,'reason':'checked','source_ids':['S1']}],'blocking':False}
        review['sources'][0]['collected_by']='workbuddy';d['claims'][0]['text']='Different meaning'
        with self.assertRaisesRegex(ValueError,'stale claim'):check_review(review,d,'workbuddy')
    def test_T13_reassign_external_with_review_retained(self):
        self.policy();s=self.research_spec();s['review_required']=True;t=self.start(s);self.c.fail(t,'CLIENT_UNAVAILABLE','Actual observed unavailable')
        with self.assertRaisesRegex(GuardError,'reassign'):self.c.degrade(t,'No automatic local takeover')
        n=self.c.reassign(t,'workbuddy','Use other observed available client');self.assertEqual(n['producer'],'workbuddy');self.assertEqual(n['reviewer'],'kimi')
    def test_T14_unknown_stale_exhausted_quota(self):
        self.policy();t=self.start();self.c.prepare_submission(t,'producer')
        self.c.record_cost(t,{'provider':'kimi','observed_at':'2000-01-01T00:00:00+00:00','value':None,'unit':'unknown','scope':'no available measurement'})
        self.c.prepare_submission(t,'producer')
        self.c.record_cost(t,{'provider':'kimi','observed_at':now(),'value':0,'unit':'remaining_credits','scope':'UI refusal','availability':'exhausted'})
        with self.assertRaisesRegex(GuardError,'unavailable'):self.c.prepare_submission(t,'producer')
        self.c.record_cost(t,{'provider':'kimi','observed_at':now(),'value':None,'unit':'unknown','scope':'Later unknown is not recovery','availability':'unknown'})
        with self.assertRaisesRegex(GuardError,'unavailable'):self.c.prepare_submission(t,'producer')
    def test_phase_limits_and_unknown_phase(self):
        self.policy();s=self.research_spec();s['call_limits']={'producer':0};t=self.start(s)
        with self.assertRaisesRegex(GuardError,'phase call cap'):self.c.prepare_submission(t,'producer')
        with self.assertRaisesRegex(GuardError,'Unknown transport'):self.c.prepare_submission(t,'researcher')
        s['call_limits']={'producer':True}
        with self.assertRaisesRegex(ValueError,'CONFIG_ERROR'):self.c.create(s)
    def test_work_item_and_review_caps(self):
        self.policy(max_calls_per_work_item=1);p=self.c.create(spec())['task_id'];s=self.research_spec()
        t=self.c.new_work(p,s)['task_id'];self.c.add_source(t,self.source);self.c.package(t);self.complete(t)
        s.update(followup_reason='gap',new_work='new page');n=self.c.new_work(p,s,t)['task_id'];self.c.add_source(n,self.source);self.c.package(n)
        with self.assertRaisesRegex(GuardError,'max_calls_per_work_item'):self.c.prepare_submission(n,'producer')
    def test_unknown_quota_preflight_is_honest(self):
        self.policy();t=self.start();s=self.c.prepare_submission(t,'producer');lock=self.c.acquire_gui(t)
        with self.assertRaisesRegex(GuardError,'Unknown quota'):self.c.begin_submission(t,s['submission_id'],lock,self.proof(t,s,'preflight',quota_available=None))
        self.c.begin_submission(t,s['submission_id'],lock,self.proof(t,s,'preflight',quota_available=None,quota_status='unknown',send_enabled=True,payment_required=False))
    def test_domain_and_search_policy(self):
        self.policy(max_search_queries=0,allowed_public_domains=['example.edu']);t=self.start();m=self.c.get(t)
        text=self.c.read_artifact(m,m['packages']['producer']).decode();self.assertIn('明确禁止发起搜索查询',text)
        sub=self.c.prepare_submission(t,'producer');self.assertNotIn('no fixed search limit',sub['prompt'])
        from cluster.research import check_sources
        d=self.data(t,sub);d['sources'][0]['url']='http://127.0.0.1/private'
        with self.assertRaisesRegex(ValueError,'public URL'):check_sources(d,'kimi',['example.edu'])
    def test_unreviewed_research_cannot_enter_parent_final(self):
        self.policy();p=self.c.create(spec())['task_id'];s=self.research_spec();t=self.c.new_work(p,s)['task_id'];self.c.add_source(t,self.source);self.c.package(t);self.complete(t);self.c.finalize(t);self.c.deliver(t)
        with self.assertRaisesRegex(GuardError,'independent review'):self.c.accept_work(p,t)
    def test_search_snippet_does_not_verify_claim(self):
        self.policy();t=self.start();a,d=self.complete(t)
        review={'sources':copy.deepcopy(d['sources']),'claim_checks':[{'claim_id':'C1','claim_sha256':claim_hash(d['claims'][0]),'result':'verified','blocking':False,'reason':'checked','source_ids':['S1']}],'blocking':False}
        review['sources'][0].update(collected_by='workbuddy',access_status='search_snippet')
        with self.assertRaisesRegex(ValueError,'snippets'):check_review(review,d,'workbuddy')
    def test_date_only_and_ok_are_not_fabricated_as_full_access(self):
        from cluster.research import normalize_metadata,check_sources
        self.policy();t=self.start();s=self.c.prepare_submission(t,'producer');d=self.data(t,s)
        d['sources'][0].update(accessed_at='2026-09-29',access_status='ok')
        n,changes=normalize_metadata(d);self.assertEqual(n['draft_markdown'],d['draft_markdown']);self.assertEqual(n['claims'],d['claims'])
        self.assertIsNone(n['sources'][0]['accessed_at']);self.assertEqual(n['sources'][0]['access_status'],'reported_access')
        self.assertEqual(d['sources'][0]['access_status'],'ok');self.assertEqual(len(changes),2);check_sources(n)
    def test_provider_role_alias_is_narrow_and_original_preserved(self):
        from cluster.research import normalize_metadata,check_sources
        self.policy();t=self.start();sub=self.c.prepare_submission(t,'producer');d=self.data(t,sub)
        d['sources'][0]['collected_by']='workbuddy-reviewer'
        n,changes=normalize_metadata(d,'workbuddy');self.assertEqual(n['sources'][0]['collected_by'],'workbuddy')
        self.assertEqual(n['sources'][0]['reported_collected_by'],'workbuddy-reviewer');self.assertEqual(d['sources'][0]['collected_by'],'workbuddy-reviewer')
        n,_=normalize_metadata(d,'kimi')
        with self.assertRaisesRegex(ValueError,'collector mismatch'):check_sources(n,'kimi')
    def test_review_can_cite_context_but_needs_direct_support(self):
        self.policy();t=self.start();a,d=self.complete(t)
        sources=copy.deepcopy(d['sources']);sources[0]['collected_by']='workbuddy'
        context=copy.deepcopy(sources[0]);context.update(source_id='S2',supports_claim_ids=[],access_status='search_snippet');sources.append(context)
        review={'sources':sources,'claim_checks':[{'claim_id':'C1','claim_sha256':claim_hash(d['claims'][0]),'result':'verified','blocking':False,'reason':'S1 is direct evidence; S2 only context','source_ids':['S1','S2']}],'blocking':False}
        check_review(review,d,'workbuddy')
        review['claim_checks'][0]['source_ids']=['S2']
        with self.assertRaisesRegex(ValueError,'snippets'):check_review(review,d,'workbuddy')
    def test_input_selection_cancels_only_unsent_draft(self):
        self.policy();t=self.start();m=self.c.get(t);old=self.c.prepare_submission(t,'producer')
        self.c.select_inputs(t,m['source_files']);self.assertEqual(self.c.get(t)['submissions'][0]['status'],'cancelled')
        self.c.package(t);new=self.c.prepare_submission(t,'producer');self.assertNotEqual(old['submission_id'],new['submission_id'])
        lock=self.c.acquire_gui(t);self.c.begin_submission(t,new['submission_id'],lock,self.proof(t,new,'preflight'))
        with self.assertRaisesRegex(GuardError,'before submitting|send intent'):self.c.select_inputs(t,m['source_files'])
    def test_stable_roles_swap_only_future_tasks(self):
        self.policy();s=self.research_spec();s.pop('assigned_provider');s['review_required']=True
        first=self.c.create(s);self.assertEqual((first['producer'],first['reviewer']),('kimi','workbuddy'))
        self.c.configure_roles({'orchestrator':'codex','primary_executor':'workbuddy','secondary_executor':'kimi'},'test user swap')
        second=self.c.create(s);self.assertEqual((second['producer'],second['reviewer']),('workbuddy','kimi'))
        self.assertEqual(self.c.get(first['task_id'])['role_bindings']['primary_executor'],'kimi')
    def test_roles_reject_self_review_and_missing_authority(self):
        with self.assertRaisesRegex(ValueError,'different'):self.c.set_role('primary_executor','workbuddy','test')
        with self.assertRaisesRegex(ValueError,'authorization'):self.c.set_role('primary_executor','kimi','')
        with self.assertRaisesRegex(ValueError,'registered'):self.c.set_role('secondary_executor','unregistered','test')
    def test_new_member_requires_supervised_pilot_and_explicit_policy(self):
        self.policy();p=copy.deepcopy(default_policy()['providers']['kimi']);p['network_access']='offline'
        profile={'member_id':'sample_gui','display_name':'Sample app','app_name':'Sample app','policy':p,'authorization_source':'test future member, not a real call'}
        self.c.register_member(profile);self.c.set_role('primary_executor','sample_gui','test binding')
        s=self.research_spec();s.pop('assigned_provider');s['review_required']=True
        with self.assertRaisesRegex(ValueError,'unverified'):self.c.create(s)
        s['capability_pilot']=True;m=self.c.create(s)
        self.assertEqual(m['producer'],'sample_gui');self.assertEqual(m['effective_policy']['providers']['sample_gui']['network_access'],'offline')
        self.c.add_source(m['task_id'],self.source);self.c.package(m['task_id']);ticket=self.c.prepare_submission(m['task_id'],'producer')
        self.assertEqual(ticket['provider'],'sample_gui');self.assertNotIn('no fixed search limit',ticket['prompt'])
        with self.assertRaisesRegex(GuardError,'live capability'):self.c.verify_member('sample_gui',m['task_id'])
    def test_cli_role_configuration_and_source_selection(self):
        import subprocess,sys
        self.policy();base=[sys.executable,'-m','cluster','--root',str(self.root),'--mode','test']
        f=self.root/'bindings.json';f.write_text(js({'orchestrator':'codex','primary_executor':'kimi','secondary_executor':'workbuddy'}))
        for args in [['roles'],['configure-roles','--file',str(f),'--authority','test']]:
            r=subprocess.run(base+args,capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr)
        t=self.start();ids=self.c.get(t)['source_files'];r=subprocess.run(base+['select-inputs',t,'--artifacts']+ids,capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)
    def test_review_package_keeps_plain_complete_draft(self):
        self.policy();p=self.c.create(spec())['task_id'];t=self.start();a,d=self.complete(t)
        r,_=self.review_task(p,t);m=self.c.get(r);text=self.c.read_artifact(m,m['packages']['reviewer']).decode()
        self.assertIn('【待审正文原文】\n'+d['draft_markdown']+'\n【正文结束】',text)
        self.assertNotIn('"summary":',text)
    def test_default_revision_goes_to_secondary_with_other_reviewer(self):
        self.policy();s=self.research_spec(kind='revision');s.pop('assigned_provider');s['review_required']=True
        m=self.c.create(s);self.assertEqual((m['producer'],m['reviewer']),('workbuddy','kimi'))
    def test_rollup_imported_source_cards_deduplicate_without_metadata_url(self):
        self.policy();parent=self.c.create(spec())['task_id']
        t=self.c.new_work(parent,self.research_spec())['task_id']
        self.c.add_source(t,self.source);self.c.package(t);self.complete(t)
        m=self.c.get(t);card=next(a for a in m['artifacts'] if a.get('evidence_level')=='member_reported_excerpt')
        other=self.c.new_work(parent,self.research_spec())['task_id']
        imported=self.c.import_artifact(other,t,card['artifact_id'])
        self.assertNotIn('url',imported)
        result=self.c.rollup(parent)['unique_reported_source_urls']
        self.assertEqual(len(result),1)
        self.assertEqual(len(next(iter(result.values()))),2)
        self.assertEqual(self.c.read_artifact(self.c.get(other),imported['artifact_id']),self.c.read_artifact(m,card['artifact_id']))
    def test_T16_old_artifacts_stay_identical(self):
        self.policy();t=self.start();a,d=self.complete(t);old={x['artifact_id']:self.c.read_artifact(self.c.get(t),x['artifact_id']) for x in self.c.get(t)['artifacts']}
        self.c.recover(t);self.assertTrue(self.c.audit(t)['passed'])
        for aid,b in old.items():self.assertEqual(self.c.read_artifact(self.c.get(t),aid),b)
    def test_resolution_rejects_unrelated_parent_and_undeclared_issue(self):
        self.policy();p=self.c.create(spec())['task_id'];q=self.c.create(spec())['task_id']
        a=self.c.new_work(p,self.research_spec())['task_id'];b=self.c.new_work(q,self.research_spec())['task_id']
        with self.assertRaisesRegex(GuardError,'same parent'):self.c.resolve_from_work(a,'unknown',b,'not enough')
        b=self.c.new_work(p,self.research_spec())['task_id']
        with self.assertRaisesRegex(GuardError,'not linked'):self.c.resolve_from_work(a,'unknown',b,'not enough')
    def test_full_revision_review_closure_and_parent_acceptance(self):
        self.policy();parent=self.c.create(spec())['task_id'];self.c.add_source(parent,self.source);self.c.package(parent);self.c.start_local(parent)
        s=self.research_spec();t=self.c.new_work(parent,s)['task_id'];self.c.add_source(t,self.source);self.c.package(t);a,d=self.complete(t)
        r,ra=self.review_task(parent,t);self.finish_review(r,ra,d,'evidence_gap');issue=self.c.get(r)['open_issues'][0]['id']
        s.update(work_kind='revision',followup_reason='Missing source detail',new_work='Open detailed source and revise',origin_issue_ids=[issue])
        n=self.c.new_work(parent,s,t)['task_id'];self.c.import_artifact(n,t,a['artifact_id']);self.c.package(n);na,nd=self.complete(n)
        rr,rra=self.review_task(parent,n,previous=r)
        self.finish_review(rr,rra,nd,resolutions=[{'issue_id':issue,'status':'resolved','reason':'New source supports exact current claim','source_ids':['S1']}])
        self.c.finalize(rr);self.c.deliver(rr);self.c.resolve_from_work(r,issue,rr,'Independent new version confirmed')
        self.c.accept_work(parent,rr);self.assertEqual(self.c.get(r)['open_issues'][0]['status'],'resolved')
        self.assertEqual(self.c.rollup(parent)['confirmed_external_calls'],4)

del CoreTest
if __name__=='__main__':unittest.main()
