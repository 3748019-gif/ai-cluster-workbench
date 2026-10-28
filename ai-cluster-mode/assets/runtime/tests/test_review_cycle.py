"""Real Cluster entry-point tests for two-round, evidence-bound review cycles.

Synthetic files and simulated GUI evidence only; no external AI is contacted.
"""
import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from cluster.core import Cluster,GuardError,js,digest,now
from cluster.document_review import document_statement_hash
from cluster.review_cycle import check_review_cycle_contract
import test_core as core_test
import test_offline_document as offline_test
from test_core import spec


def contract(unit='SCHOOL-1',ids=None,**kw):
    c={'version':1,'unit_id':unit,'scope_id':'chemistry-2026',
       'scope_item_ids':ids or ['R01','R02','OLD'], 'dependency_map':{},
       'user_rules':['Keep original credit units'],'exceptions':['No numeric conversion'],
       'required_source_artifacts':[],'source_read_allowed':True,'review_role':'secondary_executor'}
    c.update(kw);return c


class ReviewCycleTest(unittest.TestCase):
    setUp=core_test.CoreTest.setUp
    tearDown=core_test.CoreTest.tearDown
    proof=core_test.CoreTest.proof
    external_start=core_test.CoreTest.external_start
    offline_spec=offline_test.OfflineDocumentTest.offline_spec
    send_review=offline_test.OfflineDocumentTest.send_review

    def start(self,unit='SCHOOL-1',c=None,scope=None):
        kw={'review_cycle_contract':c or contract(unit)}
        if scope is not None:kw['document_review_scope']=scope
        t=self.c.create(self.offline_spec(**kw))['task_id']
        a=self.c.add_source(t,self.source,'courses.csv')
        b=self.c.add_source(t,self.source,'policy.md')
        self.c.register_review_inputs(t,[{'artifact_id':x['artifact_id'],'read_allowed':True} for x in (a,b)])
        self.c.package(t);return t

    def candidate(self,t,changes=None):
        changes=changes or {}
        data={'task_id':t,'draft_markdown':'READY synthetic content',
              'candidate_cells':{'第2列类别要求':'≥12','第4列选课要求':'≥1门'},
              'statements':[{'claim_id':i,'text':changes.get(i,'READY '+i),'location':i} for i in ['R01','R02','OLD']]}
        self.c.start_local(t);p=self.root/'candidate.json';p.write_text(js(data));return self.c.local_output(t,p),data

    def revise(self,t,data,changes,scope=None):
        self.c.begin_correction(t,'Concentrated correction of affected content')
        data=copy.deepcopy(data)
        for row in data['statements']:
            if row['claim_id'] in changes:row['text']=changes[row['claim_id']]
        if scope is not None:
            with self.c._mutate(t) as m:m['spec']['document_review_scope']=scope
        p=self.root/'revision.json';p.write_text(js(data));return self.c.local_output(t,p),data

    def report(self,t,a,data,errors=None,scope=None):
        errors=errors or {};selected=scope or [x['claim_id'] for x in data['statements']]
        out={'task_id':t,'submission_id':'pending','candidate_artifact_id':a['artifact_id'],'candidate_sha256':a['hash'],
             'confirmed_errors':[],'suspicions':[],'unverified':[],'wording_suggestions':[],
             'passed_checks':[],'blocking':bool(errors),'sources':[],'claim_checks':[]}
        source=self.c.get(t)['source_files'][0]
        for r in data['statements']:
            i=r['claim_id']
            if i not in selected:continue
            row={'claim_id':i,'semantic_sha256':document_statement_hash(r),'basis':'Exact original source comparison'}
            if i in errors:
                row.update(id=errors[i],location=i,description='Confirmed content conflict for '+i,
                           source_artifacts=[source],evidence='Original source requirement',suggested_fix='Correct the affected requirement',category='semantic')
                out['confirmed_errors'].append(row)
            else:out['passed_checks'].append(row)
        return out

    def round(self,t,a,data,errors=None,scope=None):
        self.c.package(t,'reviewer');raw=self.report(t,a,data,errors,scope)
        review=self.send_review(t,raw);return review,raw

    def test_contract_unknown_controls_and_source_permission_rejected(self):
        for patch in ({'max_review_rounds':2},{'version':3},{'source_read_allowed':False},{'scope_item_ids':['R01','R01']}):
            with self.subTest(patch=patch),self.assertRaises(ValueError):check_review_cycle_contract({'review_cycle_contract':contract(**patch)})
        self.assertIsNone(check_review_cycle_contract(spec()))

    def test_T08_first_pass_can_freeze_without_second(self):
        t=self.start();a,d=self.candidate(t);self.round(t,a,d)
        frozen=self.c.freeze_review_content(t)
        self.assertTrue(frozen['passed']);self.assertEqual(frozen['review_rounds'],1)
        with self.c._mutate(t) as m:
            with self.assertRaisesRegex(GuardError,'No changed'):self.c._review_cycle_guard(m,'package')
        self.assertEqual(self.c.review_cycle_status(t)['confirmed_external_messages'],1)

    def test_T12_local_pass_preserved_while_old_issue_keeps_identity(self):
        t=self.start();a,d=self.candidate(t);self.round(t,a,d,{'R01':'FIX-R01','OLD':'OLD-ISSUE-TOTAL'})
        a,d=self.revise(t,d,{'R01':'READY revised R01'},['R01']);self.round(t,a,d,scope=['R01'])
        state=self.c.review_cycle_status(t)
        self.assertEqual(set(state['passed_items']),{'R01','R02'})
        self.assertEqual(state['issues']['FIX-R01']['status'],'resolved_independently')
        self.assertEqual(state['issues']['OLD-ISSUE-TOTAL']['status'],'open')
        self.assertTrue(state['waiting_user'])
        with self.assertRaisesRegex(GuardError,'OLD-ISSUE-TOTAL'):self.c.freeze_review_content(t)
        self.c.close();self.c=Cluster(self.root,'test')
        self.assertEqual(self.c.review_cycle_status(t)['passed_items'],state['passed_items'])

    def test_T07_third_round_cannot_reset_with_task_parent_scope_or_filename(self):
        t=self.start();a,d=self.candidate(t);self.round(t,a,d,{'R01':'E1'})
        a,d=self.revise(t,d,{'R01':'READY revision still conflicting'},['R01']);self.round(t,a,d,{'R01':'E1'},['R01'])
        other=self.start(c=contract(scope_id='renamed',ids=['R01']))
        b,body=self.candidate(other,{'R01':'READY third attempt'})
        with self.assertRaisesRegex(GuardError,'REVIEW_LIMIT'):self.c.package(other,'reviewer')
        self.assertEqual(self.c.metrics(other)['submission_intents'],0)
        self.c.close();self.c=Cluster(self.root,'test')
        with self.assertRaisesRegex(GuardError,'REVIEW_LIMIT'):self.c.package(other,'reviewer')
        self.assertEqual(len(self.c.review_cycle_status(other)['rounds']),2)

    def test_T07_explicit_third_authorization_allows_bounded_review(self):
        t=self.start();a,d=self.candidate(t);self.round(t,a,d,{'R01':'E1'})
        a,d=self.revise(t,d,{'R01':'READY revision 2'},['R01']);self.round(t,a,d,{'R01':'E1'},['R01'])
        self.c.request_review_adjudication(t,'Independent source conflict remains',['R01'])
        with self.c._mutate(t) as m:
            ev=self.c._artifact(m,b'User explicitly authorizes one additional review of R01','evidence','user','user-authority.txt',[])
        self.c.authorize_review_round(t,{'authority':'explicit_user','reason':'New source authorization',
            'affected_ids':['R01'],'evidence_artifact_id':ev['artifact_id']})
        a,d=self.revise(t,d,{'R01':'READY authorized third revision'},['R01'])
        self.round(t,a,d,scope=['R01'])
        self.assertEqual(self.c.freeze_review_content(t)['review_rounds'],3)
        self.assertEqual(self.c.review_cycle_status(t)['authorizations'][0]['round_no'],3)

    def test_T14_missing_unreadable_inventory_blocks_before_send(self):
        s=self.offline_spec(review_cycle_contract=contract())
        t=self.c.create(s)['task_id'];a=self.c.add_source(t,self.source,'courses.csv')
        self.c.package(t);candidate,data=self.candidate(t)
        with self.assertRaisesRegex(GuardError,'inventory'):self.c.package(t,'reviewer')
        with self.assertRaisesRegex(GuardError,'permission'):self.c.register_review_inputs(t,[{'artifact_id':a['artifact_id'],'read_allowed':False}])
        self.assertEqual(self.c.get(t)['submissions'],[])

    def test_T10_exact_misquote_only_closes_that_issue_and_preserves_raw(self):
        t=self.start();a,d=self.candidate(t)
        report=self.report(t,a,d,{'R01':'ISSUE-MISQUOTE-1','OLD':'OTHER-SCOPE-1'})
        issue=report['confirmed_errors'][0]
        issue.update(location='第2列类别要求',quoted_location='第2列类别要求',quoted_text='≥1门')
        self.c.package(t,'reviewer');review=self.send_review(t,report)
        raw_before=self.c.read_artifact(self.c.get(t),review['artifact_id'])
        request={'action':'dismiss_exact_misquote','issue_id':'ISSUE-MISQUOTE-1',
            'authority_basis':'user_confirmed_bounded_codex','report_sha256':review['hash'],'candidate_sha256':a['hash'],
            'reason':'Exact candidate column 2 contains ≥12; reviewer quoted column 4 text.',
            'evidence':{'candidate_pointer':'/candidate_cells/第2列类别要求','quoted_location':'第2列类别要求','quoted_text':'≥1门','actual_value':'≥12'}}
        self.c.adjudicate_review_issue(t,request)
        state=self.c.review_cycle_status(t)
        self.assertEqual(state['issues']['ISSUE-MISQUOTE-1']['status'],'dismissed_exact_misquote')
        self.assertEqual(state['issues']['OTHER-SCOPE-1']['status'],'open')
        self.assertEqual(raw_before,self.c.read_artifact(self.c.get(t),review['artifact_id']))
        with self.assertRaisesRegex(GuardError,'OTHER-SCOPE-1'):self.c.freeze_review_content(t)

    def test_T10_wrong_hash_location_or_subjective_disagreement_rejected(self):
        t=self.start();a,d=self.candidate(t)
        report=self.report(t,a,d,{'R01':'E1'});report['confirmed_errors'][0].update(location='第2列类别要求',quoted_text='≥1门')
        self.c.package(t,'reviewer');review=self.send_review(t,report)
        good={'action':'dismiss_exact_misquote','issue_id':'E1','authority_basis':'user_confirmed_bounded_codex',
            'report_sha256':review['hash'],'candidate_sha256':a['hash'],'reason':'Precise column mismatch',
            'evidence':{'candidate_pointer':'/candidate_cells/第2列类别要求','quoted_location':'第2列类别要求','quoted_text':'≥1门','actual_value':'≥12'}}
        for patch in ({'report_sha256':'0'*64},{'candidate_sha256':'0'*64},{'action':'producer_disagrees'},
                      {'evidence':{**good['evidence'],'candidate_pointer':'/candidate_cells/第4列选课要求'}},
                      {'evidence':{**good['evidence'],'quoted_location':'other cell'}}):
            with self.subTest(patch=patch),self.assertRaises(GuardError):self.c.adjudicate_review_issue(t,{**good,**patch})
        self.assertEqual(self.c.review_cycle_status(t)['issues']['E1']['status'],'open')

    def test_T09_local_reingest_has_no_new_business_round_or_message(self):
        t=self.start();a,d=self.candidate(t);self.round(t,a,d)
        state=self.c.review_cycle_status(t);sub=self.c.get(t)['submissions'][-1]
        with self.c._mutate(t) as m:
            s=self.c._sub(m,sub['submission_id']);review=self.c.artifact(m,m['review'])
            raw=json.loads(self.c.read_artifact(m,review['artifact_id']))
            self.c._review_cycle_received(m,s,review,raw)
        after=self.c.review_cycle_status(t)
        self.assertEqual(after['confirmed_external_messages'],state['confirmed_external_messages'])
        self.assertEqual(len(after['rounds']),1)

    def test_T13_independently_checked_limitation_keeps_underlying_unknown(self):
        t=self.start();a,d=self.candidate(t)
        report=self.report(t,a,d)
        report['passed_checks'][0].update(underlying_fact_status='unknown',basis='Original source does not state a fixed credit; candidate retains limitation')
        self.c.package(t,'reviewer');self.send_review(t,report)
        self.c.freeze_review_content(t)
        self.assertEqual(self.c.review_cycle_status(t)['passed_items']['R01']['underlying_fact_status'],'unknown')

    def test_T16_other_school_progresses_while_current_waits(self):
        t=self.start();a,d=self.candidate(t);self.round(t,a,d,{'OLD':'OLD-GAP'})
        a,d=self.revise(t,d,{'R01':'READY local correction'},['R01']);self.round(t,a,d,scope=['R01'])
        self.assertTrue(self.c.review_cycle_status(t)['waiting_user'])
        other=self.start('SCHOOL-2');a,d=self.candidate(other);self.round(other,a,d)
        self.assertTrue(self.c.freeze_review_content(other)['passed'])
        self.assertNotEqual(self.c.review_cycle_status(t)['cycle_id'],self.c.review_cycle_status(other)['cycle_id'])

    def test_T22_uncertain_review_blocks_new_task_and_independent_school_is_allowed(self):
        t=self.start();a,d=self.candidate(t);self.c.package(t,'reviewer')
        s=self.c.prepare_submission(t,'reviewer');token=self.c.acquire_gui(t)
        try:self.c.begin_submission(t,s['submission_id'],token,self.proof(t,s,'preflight'))
        finally:self.c.release_gui(t,token)
        with self.assertRaisesRegex(GuardError,'uncertain'):self.start()
        independent=self.start('SCHOOL-2')
        self.assertNotEqual(independent,t)
        self.assertFalse(self.c.recover(t)['automatic_resend_allowed'])
        self.assertEqual(self.c.metrics(t)['submission_intents'],1)

    def test_semantic_change_invalidates_prior_independent_pass(self):
        t=self.start();a,d=self.candidate(t);self.round(t,a,d)
        with self.c._mutate(t) as m:m['status']='CONFLICT_RESOLUTION'
        a,d=self.revise(t,d,{'R01':'READY altered credit 6 instead of 4'})
        with self.assertRaisesRegex(GuardError,'missing/stale'):self.c.freeze_review_content(t)

    def test_dependency_changes_require_affected_items_in_second_scope(self):
        t=self.start(c=contract(dependency_map={'R02':['R01']}));a,d=self.candidate(t);self.round(t,a,d,{'R01':'E1'})
        a,d=self.revise(t,d,{'R01':'READY revised denominator'},['R01'])
        with self.assertRaisesRegex(GuardError,'dependencies'):self.c.package(t,'reviewer')

    def test_first_scope_cannot_be_shrunk(self):
        t=self.start(scope=['R01']);a,d=self.candidate(t)
        with self.assertRaisesRegex(GuardError,'every scoped|whole stable|First independent'):
            # The cycle guard checks explicit first-round scope before send.
            self.c.package(t,'reviewer')

    def test_T15_duplicate_exact_issue_reuses_identity_and_adjudication(self):
        t=self.start();a,d=self.candidate(t)
        report=self.report(t,a,d,{'R01':'E1'})
        report['confirmed_errors'][0].update(location='第2列类别要求',quoted_text='≥1门')
        self.c.package(t,'reviewer');review=self.send_review(t,report)
        request={'action':'dismiss_exact_misquote','issue_id':'E1','authority_basis':'user_confirmed_bounded_codex',
            'report_sha256':review['hash'],'candidate_sha256':a['hash'],'reason':'Exact literal mismatch',
            'evidence':{'candidate_pointer':'/candidate_cells/第2列类别要求','quoted_location':'第2列类别要求','quoted_text':'≥1门','actual_value':'≥12'}}
        self.c.adjudicate_review_issue(t,request)
        sub=self.c.get(t)['submissions'][-1]
        with self.c._mutate(t) as m:
            self.c._review_cycle_received(m,self.c._sub(m,sub['submission_id']),review,report)
        state=self.c.review_cycle_status(t)
        self.assertEqual(list(state['issues']),['E1'])
        self.assertEqual(state['issues']['E1']['status'],'dismissed_exact_misquote')
        self.assertEqual(len(state['issues']['E1']['observations']),1)
        self.assertTrue(self.c.freeze_review_content(t)['passed'])
        # Real new source evidence makes the previous bounded dismissal stale.
        self.source.write_text('Authority READY changed official source edition')
        other=self.start();a,d=self.candidate(other)
        self.round(other,a,d,{'R01':'E1'})
        state=self.c.review_cycle_status(other)
        self.assertEqual(state['issues']['E1']['status'],'open')
        self.assertEqual(len(state['issues']),1)
        self.assertEqual(len(state['issues']['E1']['observations']),2)

    def test_T22_concurrent_preparations_reserve_one_business_cycle(self):
        t=self.start();a,d=self.candidate(t);self.c.package(t,'reviewer')
        other=self.start();b,d2=self.candidate(other);self.c.package(other,'reviewer')
        def prepare(task):
            c=Cluster(self.root,'test')
            try:
                try:return ('ok',c.prepare_submission(task,'reviewer')['submission_id'])
                except GuardError as e:return ('blocked',str(e))
            finally:c.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(prepare,[t,other]))
        self.assertEqual(sum(r[0]=='ok' for r in results),1)
        self.assertEqual(sum(r[0]=='blocked' for r in results),1)
        self.assertEqual(sum(len(self.c.get(x)['submissions']) for x in (t,other)),1)
        self.assertEqual(sum(self.c.metrics(x)['submission_intents'] for x in (t,other)),0)

    def test_source_rules_and_exceptions_cannot_inherit_stale_pass(self):
        t=self.start();a,d=self.candidate(t);self.round(t,a,d)
        for change in ({'user_rules':['New route restriction']},{'exceptions':['New user exception']},
                       {'dependency_map':{'R02':['R01']}}):
            other=self.start(c=contract(**change));self.candidate(other)
            with self.subTest(change=change),self.assertRaisesRegex(GuardError,'missing/stale'):
                self.c.freeze_review_content(other)

    def test_candidate_cell_or_prose_change_cannot_hide_behind_unchanged_statements(self):
        t=self.start();a,d=self.candidate(t);self.round(t,a,d)
        with self.c._mutate(t) as m:m['status']='CONFLICT_RESOLUTION'
        changed=copy.deepcopy(d);changed['candidate_cells']['第2列类别要求']='each group ≥12'
        p=self.root/'changed-cell.json';p.write_text(js(changed));self.c.local_output(t,p)
        with self.assertRaisesRegex(GuardError,'missing/stale'):self.c.freeze_review_content(t)

    def test_original_inventory_cannot_silently_omit_registered_source(self):
        t=self.c.create(self.offline_spec(review_cycle_contract=contract()))['task_id']
        a=self.c.add_source(t,self.source,'courses.csv');self.c.add_source(t,self.source,'policy.md')
        with self.assertRaisesRegex(GuardError,'Missing required originals'):
            self.c.register_review_inputs(t,[{'artifact_id':a['artifact_id'],'read_allowed':True}])

    def test_T13_raw_unspecified_block_cannot_be_ignored(self):
        t=self.start();a,d=self.candidate(t);report=self.report(t,a,d)
        report['blocking']=True
        self.c.package(t,'reviewer');self.send_review(t,report)
        state=self.c.review_cycle_status(t)
        self.assertEqual(state['issues']['RAW-UNSPECIFIED-BLOCK']['status'],'open')
        with self.assertRaisesRegex(GuardError,'RAW-UNSPECIFIED-BLOCK'):self.c.freeze_review_content(t)

    def test_T24_inherited_review_bytes_tamper_blocks_current_gate(self):
        t=self.start();a,d=self.candidate(t);review,_=self.round(t,a,d)
        other=self.start();self.candidate(other)
        proof=self.c.artifact(self.c.get(t),review['artifact_id'])
        path=self.c.task_path(t)/proof['filename'];path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaisesRegex(GuardError,'hash changed'):self.c.freeze_review_content(other)

    def test_T22_not_sent_reconciliation_retains_intent_without_consuming_round(self):
        t=self.start();a,d=self.candidate(t);self.c.package(t,'reviewer')
        s=self.c.prepare_submission(t,'reviewer');token=self.c.acquire_gui(t)
        try:
            self.c.begin_submission(t,s['submission_id'],token,self.proof(t,s,'preflight'))
            self.c.reconcile_not_sent(t,s['submission_id'],token,self.proof(t,s,'reconciliation',outcome='not_submitted',reason='Affirmatively inspected empty client history'))
        finally:self.c.release_gui(t,token)
        # The root reconciliation entry invokes this hook in the same transaction.
        state=self.c.review_cycle_status(t)
        self.assertEqual(len(state['rounds']),0)
        self.assertEqual(len(state.get('reconciled_intents',[])),1)
        self.assertEqual(state['confirmed_external_messages'],0)
        self.assertEqual(self.c.metrics(t)['submission_intents'],1)

    def mechanical_case(self,before,after,category='format'):
        t=self.start();candidate,data=self.candidate(t)
        with self.c._mutate(t) as m:
            left=self.c._artifact(m,before,'evidence','local','layout-before.bin',[])
            right=self.c._artifact(m,after,'evidence','local','layout-after.bin',[])
        report=self.report(t,candidate,data,{'R01':'MECHANICAL-1'})
        report['confirmed_errors'][0].update(category=category,file_artifact_id=left['artifact_id'])
        self.c.package(t,'reviewer');review=self.send_review(t,report)
        request={'action':'close_mechanical','issue_id':'MECHANICAL-1','authority_basis':'user_confirmed_bounded_codex',
            'report_sha256':review['hash'],'candidate_sha256':candidate['hash'],'reason':'Exact controller byte or OOXML comparison',
            'evidence':{'operation':'remove_utf8_bom','before':{'artifact_id':left['artifact_id'],'sha256':left['hash']},
                        'after':{'artifact_id':right['artifact_id'],'sha256':right['hash']}}}
        return t,request

    def test_T11_exact_bom_fix_closes_only_mechanical_issue_without_new_review(self):
        t,request=self.mechanical_case(b'\xef\xbb\xbfREADY credit 4',b'READY credit 4')
        result=self.c.adjudicate_review_issue(t,request)
        self.assertFalse(result['underlying_facts_verified'])
        self.assertFalse(result['native_visual_qa_verified'])
        self.assertTrue(self.c.freeze_review_content(t)['passed'])
        self.assertEqual(self.c.review_cycle_status(t)['confirmed_external_messages'],1)

    def test_T11_small_semantic_change_or_substantive_kind_cannot_be_mechanical(self):
        for before,after,category in ((b'\xef\xbb\xbfREADY credit 4',b'READY credit 6','format'),
                                     (b'\xef\xbb\xbfREADY credit 4',b'READY credit 4','semantic')):
            # Different units isolate intentionally independent negative controls.
            self.c.close();self.tmp.cleanup();self.setUp()
            t,request=self.mechanical_case(before,after,category)
            with self.assertRaises(GuardError):self.c.adjudicate_review_issue(t,request)
            self.assertEqual(self.c.review_cycle_status(t)['issues']['MECHANICAL-1']['status'],'open')

    def test_T11_docx_width_proof_preserves_semantics(self):
        from cluster.document_stage import write_view_docx
        view={'tables':[{'id':'requirements','columns':['类别','额度'],
            'rows':[{'cells':['甲乙共享','≥12']}]}],'notes':[]}
        left=write_view_docx(view,self.root/'left.docx','合成表格')
        right=write_view_docx(view,self.root/'right.docx','合成表格',{'requirements':[2000,8000]})
        t,request=self.mechanical_case(left.read_bytes(),right.read_bytes())
        request['evidence']['operation']='docx_layout'
        self.c.adjudicate_review_issue(t,request)
        self.assertTrue(self.c.freeze_review_content(t)['passed'])

    def test_T11_docx_credit_or_group_change_cannot_use_layout_adjudication(self):
        from cluster.document_stage import write_view_docx
        view={'tables':[{'id':'requirements','columns':['类别','额度'],
            'rows':[{'cells':['甲乙共享','≥12']}]}],'notes':[]}
        changed=copy.deepcopy(view);changed['tables'][0]['rows'][0]['cells'][1]='甲≥12，乙≥12'
        left=write_view_docx(view,self.root/'left.docx','合成表格')
        right=write_view_docx(changed,self.root/'right.docx','合成表格')
        t,request=self.mechanical_case(left.read_bytes(),right.read_bytes())
        request['evidence']['operation']='docx_layout'
        with self.assertRaisesRegex(GuardError,'group boundaries'):self.c.adjudicate_review_issue(t,request)
        self.assertEqual(self.c.review_cycle_status(t)['issues']['MECHANICAL-1']['status'],'open')

    def test_T16_actual_dependency_wait_blocks_but_unrelated_school_can_finish(self):
        blocked=self.start();a,d=self.candidate(blocked);self.round(blocked,a,d,{'OLD':'OLD-GAP'})
        dependent=self.start('SCHOOL-2');self.candidate(dependent)
        m=self.c.get(blocked);artifact=self.c.artifact(m,m['candidate'])
        with self.c._mutate(dependent) as current:
            current['depends_on']=[{'task_id':blocked,'artifact_id':artifact['artifact_id'],'sha256':artifact['hash']}]
        with self.assertRaisesRegex(GuardError,'Dependent business unit'):self.c.freeze_review_content(dependent)
        independent=self.start('SCHOOL-3');a,d=self.candidate(independent);self.round(independent,a,d)
        self.assertTrue(self.c.freeze_review_content(independent)['passed'])

    def offline_general_notice_case(self,errors=None):
        ids=['EA','EB','ELECTIVE','LAB','R01','R02','R03','R04','R05','R06']
        t=self.start(c=contract(ids=ids))
        data={'task_id':t,'draft_markdown':'READY ten scoped synthetic claims',
              'statements':[{'claim_id':i,'text':'READY '+i,'location':i} for i in ids]}
        self.c.start_local(t);path=self.root/'ten-candidate.json';path.write_text(js(data))
        candidate=self.c.local_output(t,path)
        report=self.report(t,candidate,data,errors=errors)
        report['unverified']=[
            {'check_id':'UNV-HASH-01','basis':'未重算控制器提供的文件或语义哈希'},
            {'check_id':'UNV-HASH-02','basis':'候选视图和statement哈希使用不同域，未自行确认'},
            {'check_id':'UNV-VIS-01','basis':'未操作Word/WPS，原生视觉验版属于Codex'},
            {'check_id':'UNV-MEMBER-01','basis':'未附可选member_reported_excerpt，不覆盖当前scope'}]
        return t,candidate,data,report

    def test_offline_nine_pass_one_error_with_four_generic_notices_preserves_partial_pass(self):
        t,candidate,data,report=self.offline_general_notice_case({'R02':'CE-R02-01'})
        self.c.package(t,'reviewer');artifact=self.send_review(t,report)
        before=self.c.read_artifact(self.c.get(t),artifact['artifact_id'])
        state=self.c.review_cycle_status(t)
        self.assertEqual(set(state['passed_items']),set(self.c.get(t)['spec']['review_cycle_contract']['scope_item_ids'])-{'R02'})
        self.assertEqual(list(state['issues']),['CE-R02-01'])
        self.assertEqual(state['issues']['CE-R02-01']['affected_ids'],['R02'])
        self.assertEqual(len(state['notices']),4)
        self.assertTrue(all(not n['counts_as_scoped_coverage'] and not n['underlying_facts_verified'] for n in state['notices']))
        self.assertEqual([n['detail'] for n in state['notices']],report['unverified'])
        self.assertEqual(before,self.c.read_artifact(self.c.get(t),artifact['artifact_id']))
        with self.assertRaisesRegex(GuardError,'CE-R02-01'):self.c.freeze_review_content(t)

    def test_offline_raw_true_only_generic_notices_keeps_unspecified_block(self):
        t,candidate,data,report=self.offline_general_notice_case()
        report['blocking']=True
        self.c.package(t,'reviewer');self.send_review(t,report)
        state=self.c.review_cycle_status(t)
        self.assertEqual(len(state['notices']),4)
        self.assertEqual(state['issues']['RAW-UNSPECIFIED-BLOCK']['status'],'open')
        with self.assertRaisesRegex(GuardError,'RAW-UNSPECIFIED-BLOCK'):self.c.freeze_review_content(t)

    def test_offline_explicit_blocking_generic_notice_remains_scope_blocker(self):
        t,candidate,data,report=self.offline_general_notice_case()
        report['unverified'][2].update(id='GENERIC-EXPLICIT-BLOCK',blocking=True)
        report['blocking']=True
        self.c.package(t,'reviewer');self.send_review(t,report)
        state=self.c.review_cycle_status(t)
        self.assertEqual(len(state['notices']),3)
        self.assertEqual(state['issues']['GENERIC-EXPLICIT-BLOCK']['status'],'open')
        self.assertEqual(set(state['issues']['GENERIC-EXPLICIT-BLOCK']['affected_ids']),set(self.c.get(t)['spec']['review_cycle_contract']['scope_item_ids']))
        with self.assertRaisesRegex(GuardError,'GENERIC-EXPLICIT-BLOCK'):self.c.freeze_review_content(t)

    def test_offline_actual_claim_unknown_cannot_be_treated_as_generic_notice(self):
        t,candidate,data,report=self.offline_general_notice_case({'R02':'CE-R02-01'})
        unknown=next(x for x in report['passed_checks'] if x['claim_id']=='R01')
        report['passed_checks'].remove(unknown)
        report['unverified'].append({**unknown,'id':'UNKNOWN-R01','basis':'Required original value remains unresolved'})
        self.c.package(t,'reviewer');self.send_review(t,report)
        state=self.c.review_cycle_status(t)
        self.assertNotIn('R01',state['passed_items'])
        self.assertNotIn('R02',state['passed_items'])
        self.assertEqual(set(state['issues']),{'CE-R02-01','UNKNOWN-R01'})
        self.assertEqual(len(state['notices']),4)
        with self.assertRaises(GuardError):self.c.freeze_review_content(t)

if __name__=='__main__':unittest.main(verbosity=2)
