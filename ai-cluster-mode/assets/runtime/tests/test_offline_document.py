import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path
from test_core import CoreTest, spec
from cluster.core import GuardError, js, digest
from cluster.document_review import document_statement_hash, check_document_review


class OfflineDocumentTest(unittest.TestCase):
    setUp=CoreTest.setUp
    tearDown=CoreTest.tearDown
    proof=CoreTest.proof
    external_start=CoreTest.external_start

    def offline_spec(self,review=True,**kw):
        s=spec(review=review)
        s.update(result_protocol='offline_document_v1',transport_prompt_style='ascii_short',
                 policy_overrides={'network_access':'offline'},authorization_source='Synthetic offline test authorization')
        s.update(kw);return s

    def start(self,s=None):
        t=self.c.create(s or self.offline_spec())['task_id']
        self.csv=self.c.add_source(t,self.source,'courses.csv')
        self.policy=self.c.add_source(t,self.source,'policy.md')
        self.c.package(t);return t

    def candidate(self,t):
        data={'task_id':t,'draft_markdown':'BODY_SENTINEL READY','custom_rows':[{'id':'row-a','value':1}],
              'sources':[],'statements':[{'claim_id':'C1','text':'READY alpha','depends_on':['row-a'],'location':'p1'},
                                        {'claim_id':'C2','text':'READY beta','depends_on':['row-b'],'location':'p2'}]}
        self.c.start_local(t);out=self.root/'candidate.json';out.write_text(js(data))
        return self.c.local_output(t,out),data

    def review(self,t,a,data,scope=None):
        selected=data['statements'] if scope is None else [x for x in data['statements'] if x['claim_id'] in scope]
        return {'task_id':t,'submission_id':'replaced-for-transport','candidate_artifact_id':a['artifact_id'],
                'candidate_sha256':a['hash'],'confirmed_errors':[],'suspicions':[],'unverified':[],
                'wording_suggestions':[],'passed_checks':[{'claim_id':x['claim_id'],
                    'semantic_sha256':document_statement_hash(x),'basis':'Original courses.csv row matched'} for x in selected],
                'blocking':False,'sources':[],'claim_checks':[]}

    def send_review(self,t,review):
        s,lock=self.external_start(t,'reviewer');review['submission_id']=s['submission_id']
        out=self.root/'review.json';out.write_text(js(review))
        a=self.c.receive(t,s['submission_id'],out,lock,self.proof(t,s,'output',completed=True,output_hash=digest(out.read_bytes())))
        self.c.release_gui(t,lock);return a

    def test_offline_package_preserves_complete_candidate_and_no_web_instructions(self):
        t=self.start();a,data=self.candidate(t);p=self.c.package(t,'reviewer');m=self.c.get(t)
        text=self.c.read_artifact(m,p['artifact_id']).decode()
        self.assertIn(js(data),text);self.assertIn('custom_rows',text)
        self.assertIn('控制器文档语义哈希索引',text)
        self.assertIn(document_statement_hash(data['statements'][0]),text)
        for aid in (p['artifact_id'],m['packages']['producer']):
            package=self.c.read_artifact(m,aid).decode()
            for token in ('sources每项字段','supports_claim_ids','accessed_at','可以主动联网补证','原始链接'):
                self.assertNotIn(token,package)

    def test_old_research_package_still_has_source_card_protocol(self):
        s=spec();s.update(task_type='research',work_kind='research')
        t=self.c.create(s)['task_id'];self.c.add_source(t,self.source);p=self.c.package(t)
        text=self.c.read_artifact(self.c.get(t),p['artifact_id']).decode()
        self.assertIn('sources每项字段',text);self.assertIn('accessed_at',text)

    def test_offline_review_complete_delivery_preserves_existing_gates(self):
        t=self.start();a,data=self.candidate(t);self.c.package(t,'reviewer')
        self.send_review(t,self.review(t,a,data));self.assertTrue(self.c.validate(t)['passed'])
        self.c.finalize(t);self.assertTrue(self.c.deliver(t).exists());self.assertTrue(self.c.audit(t)['passed'])

    def test_frozen_candidate_comparison_evidence_passes_ingest_and_final_gate(self):
        t=self.start();a,data=self.candidate(t);self.c.package(t,'reviewer')
        review=self.review(t,a,data)
        review['passed_checks'][0]['source_artifacts']=[self.csv['artifact_id'],self.policy['artifact_id'],a['artifact_id']]
        self.send_review(t,review);self.assertTrue(self.c.validate(t)['passed'])
        self.c.finalize(t);self.assertTrue(self.c.deliver(t).exists())

    def test_foreign_or_old_candidate_comparison_evidence_is_rejected(self):
        t=self.start();a,data=self.candidate(t);sources=list(self.c.get(t)['source_files'])
        other=self.start();foreign,_=self.candidate(other)
        review=self.review(t,a,data)
        review['passed_checks'][0]['source_artifacts']=[foreign['artifact_id']]
        with self.assertRaisesRegex(ValueError,'frozen package'):
            check_document_review(review,data,'workbuddy',sources,candidate_artifact_id=a['artifact_id'])
        # Reuse the original registered version as stale evidence for a new exact candidate.
        with self.c._mutate(t) as m:m['status']='CONFLICT_RESOLUTION'
        out=self.root/'new-candidate.json';out.write_text(js(data)+'\n')
        current=self.c.local_output(t,out)
        review=self.review(t,current,data);review['passed_checks'][0]['source_artifacts']=[a['artifact_id']]
        with self.assertRaisesRegex(ValueError,'frozen package'):
            check_document_review(review,data,'workbuddy',sources,candidate_artifact_id=current['artifact_id'])

    def test_scope_only_index_and_exact_coverage(self):
        t=self.start(self.offline_spec(document_review_scope=['C2']));a,data=self.candidate(t)
        p=self.c.package(t,'reviewer');text=self.c.read_artifact(self.c.get(t),p['artifact_id']).decode()
        index=text.split('【控制器文档语义哈希索引】')[1]
        self.assertIn(document_statement_hash(data['statements'][1]),index)
        self.assertNotIn(document_statement_hash(data['statements'][0]),index)
        review=self.review(t,a,data,['C2']);review['passed_checks'].append({'check_id':'row-consistency','basis':'Two rows retained'})
        check_document_review(review,data,'workbuddy',[self.csv['artifact_id']],['C2'])
        with self.assertRaisesRegex(ValueError,'scope'):
            check_document_review(self.review(t,a,data),data,'workbuddy',[],['C2'])

    def test_offline_missing_duplicate_unknown_and_stale_semantics_rejected(self):
        t=self.start();a,data=self.candidate(t);good=self.review(t,a,data)
        mutations=[lambda r:r['passed_checks'].pop(),lambda r:r['passed_checks'].append(copy.deepcopy(r['passed_checks'][0])),
                   lambda r:r['passed_checks'][0].update(claim_id='UNKNOWN'),
                   lambda r:r['passed_checks'][0].update(semantic_sha256='wrong'),
                   lambda r:r['passed_checks'][0].update(basis='')]
        for mutate in mutations:
            bad=copy.deepcopy(good);mutate(bad)
            with self.assertRaises(ValueError):check_document_review(bad,data,'workbuddy',[])
        bad=copy.deepcopy(good);bad['unverified']=[copy.deepcopy(bad['passed_checks'][0])]
        with self.assertRaises(ValueError):check_document_review(bad,data,'workbuddy',[])

    def test_nonempty_web_channels_and_unreviewed_claims_rejected(self):
        t=self.start();a,data=self.candidate(t);review=self.review(t,a,data)
        for field in ('sources','claim_checks'):
            bad=copy.deepcopy(review);bad[field]=[{}]
            with self.assertRaisesRegex(ValueError,'requires'):check_document_review(bad,data,'workbuddy',[])
        bad=copy.deepcopy(review);bad['unverified']=[bad['passed_checks'].pop()]
        with self.assertRaisesRegex(ValueError,'blocking'):check_document_review(bad,data,'workbuddy',[])
        bad['blocking']=True;check_document_review(bad,data,'workbuddy',[])

    def test_undisclosed_source_rejected_and_default_offline_policy_required(self):
        t=self.start();a,data=self.candidate(t);self.c.select_inputs(t,[self.csv['artifact_id']]);p=self.c.package(t,'reviewer')
        review=self.review(t,a,data);review['passed_checks'][0]['source_artifacts']=[self.policy['artifact_id']]
        with self.assertRaisesRegex(ValueError,'frozen package'):
            check_document_review(review,data,'workbuddy',p['source_artifact_ids'])
        s=self.offline_spec();s.pop('policy_overrides')
        with self.assertRaisesRegex(GuardError,'offline policy'):self.c.create(s)

    def test_ascii_prompt_exact_begin_confirm_lock_and_ids(self):
        t=self.start(self.offline_spec(review=False,assigned_provider='kimi'))
        s=self.c.prepare_submission(t,'producer');self.assertTrue(s['prompt'].isascii())
        self.assertNotIn(':',s['prompt']);self.assertNotIn('\n',s['prompt'])
        self.assertIn(t,s['prompt']);self.assertIn(s['submission_id'],s['prompt'])
        self.assertEqual(digest(Path(s['alias']).read_bytes()),s['package_hash'])
        self.assertEqual(self.c.compact_prepared_prompt(t,s['submission_id'])['prompt'],s['prompt'])
        lock=self.c.acquire_gui(t)
        with self.assertRaisesRegex(GuardError,'ownership'):
            self.c.begin_submission(t,s['submission_id'],'wrong',self.proof(t,s,'preflight'))
        with self.assertRaises(GuardError):
            self.c.begin_submission(t,s['submission_id'],lock,self.proof(t,s,'preflight',input_text=s['prompt']+' '))
        with self.assertRaises(GuardError):
            self.c.begin_submission(t,s['submission_id'],lock,self.proof(t,s,'preflight',task_id='TASK-20000101-9999'))
        self.c.begin_submission(t,s['submission_id'],lock,self.proof(t,s,'preflight'))
        with self.assertRaises(GuardError):self.c.compact_prepared_prompt(t,s['submission_id'])
        with self.assertRaises(GuardError):self.c.prepare_submission(t,'producer')
        with self.assertRaises(GuardError):
            self.c.confirm_submission(t,s['submission_id'],lock,self.proof(t,s,'confirmation',raw_text=s['prompt'].replace('Read ','Sent ')))
        with self.assertRaises(GuardError):
            self.c.confirm_submission(t,s['submission_id'],lock,self.proof(t,s,'confirmation',submission_id=t+'-SUB-999'))
        self.c.confirm_submission(t,s['submission_id'],lock,self.proof(t,s,'confirmation'))
        self.assertEqual(self.c.get(t)['status'],'SUBMITTED')

    def test_identity_and_candidate_hash_tamper_retains_raw_without_completing(self):
        for field in ('task_id','submission_id','candidate_artifact_id','candidate_sha256'):
            t=self.start();a,data=self.candidate(t);self.c.package(t,'reviewer');s,lock=self.external_start(t,'reviewer')
            review=self.review(t,a,data);review['submission_id']=s['submission_id'];review[field]='WRONG'
            out=self.root/'wrong.json';out.write_text(js(review))
            with self.assertRaises(GuardError):
                self.c.receive(t,s['submission_id'],out,lock,self.proof(t,s,'output',completed=True,output_hash=digest(out.read_bytes())))
            m=self.c.get(t);self.assertIsNone(m['review']);self.assertEqual(m['submissions'][-1]['status'],'confirmed')
            self.assertIn('captured_artifact',m['submissions'][-1]);self.c.release_gui(t,lock)

    def test_decision_is_short_private_text_free_read_only_and_current_validation_only(self):
        t=self.start(self.offline_spec(review=False,objective='PRIVATE_SENTINEL',rules=['PRIVATE_SENTINEL']))
        a,data=self.candidate(t);self.c.validate(t)
        m=self.c.get(t);before=(m['revision'],len(self.c.events(t)));packet=self.c.decision_packet(t)
        self.assertNotIn('PRIVATE_SENTINEL',js(packet));self.assertNotIn('BODY_SENTINEL',js(packet))
        self.assertTrue(packet['validation']['passed']);self.assertEqual(packet['candidate']['artifact_id'],a['artifact_id'])
        self.assertEqual(before,(self.c.get(t)['revision'],len(self.c.events(t))))
        proc=subprocess.run([sys.executable,'-m','cluster','--root',str(self.root),'--mode','test','decision',t],capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(proc.stdout),packet)
        with self.c._mutate(t) as changed:changed['candidate']=None
        self.assertIsNone(self.c.decision_packet(t)['validation'])

    def test_decision_uncertain_failure_requires_inspection_not_resend(self):
        t=self.start(self.offline_spec(review=False,assigned_provider='kimi'));s=self.c.prepare_submission(t,'producer');lock=self.c.acquire_gui(t)
        self.c.begin_submission(t,s['submission_id'],lock,self.proof(t,s,'preflight'));self.c.fail(t,'CLIENT_UNAVAILABLE','PRIVATE_SENTINEL')
        packet=self.c.decision_packet(t);self.assertTrue(packet['blocked'])
        self.assertEqual(packet['next_action'],'inspect_existing_client_record_do_not_resend')
        self.assertNotIn('PRIVATE_SENTINEL',js(packet))

    def test_review_helper_imports_only_original_names_and_exact_candidate(self):
        parent=self.c.create(self.offline_spec(review=False))['task_id']
        csv=self.c.add_source(parent,self.source,'courses.csv');policy=self.c.add_source(parent,self.source,'policy.md')
        self.c.add_source(parent,self.source,'old_verdict.json')
        origin=self.c.new_work(parent,self.offline_spec(review=False))['task_id'];self.c.add_source(origin,self.source,'producer_extra.txt');self.c.package(origin)
        a,data=self.candidate(origin);result=self.c.prepare_document_review(parent,origin,['C2'])
        m=self.c.get(result['task_id']);self.assertEqual([x['logical_name'] for x in result['sources']],['courses.csv','policy.md'])
        self.assertEqual({x['origin_artifact_id'] for x in result['sources']},{csv['artifact_id'],policy['artifact_id']})
        self.assertEqual(result['candidate']['hash'],a['hash']);self.assertEqual(result['scope'],['C2'])
        self.assertEqual(m['spec']['result_protocol'],'offline_document_v1');self.assertFalse(result['impact_computed']);self.assertFalse(result['prior_review_reused'])
        self.assertIn('Submission '+result['submission']['submission_id'],result['submission']['prompt'])


del CoreTest
