"""Synthetic protocol regressions only; no real GUI or network side effects."""
import copy
import json
import unittest

from test_core import CoreTest, spec
from cluster.core import GuardError, digest, js
from cluster.handoff import MEMBER_CONTENT, build_envelope, process_member_content, semantic_gate
from cluster.research import SOURCE_FIELDS, check_review, check_sources, claim_hash, normalize_metadata


def content(provider='kimi'):
    return {'draft_markdown':'Each student chooses one course from each laboratory group.',
            'summary':'Located curriculum evidence','gaps':[],'changes':[],
            'sources':[{'source_id':'S1','url':'https://example.edu/chemistry','title':'Chemistry B.S.',
                'published_at':None,'updated_at':None,'accessed_at':'2026-09-30T00:00:00+00:00',
                'access_status':'full_text','applicable_cohort_or_version':'2026','level':'undergraduate',
                'scope':'Chemistry B.S.','locator':'Degree requirements / laboratories and exceptions',
                'quote':'Choose one physical chemistry laboratory and one synthetic chemistry laboratory; exceptions apply.',
                'excerpt_is_complete':False,'omissions_marked':True,'supports_claim_ids':['C1'],
                'limits':[],'raw_artifact_id':None}],
            'claims':[{'claim_id':'C1','text':'Choose one laboratory from each of two groups.',
                'scope':'Chemistry B.S.','time':'Fall 2026','source_ids':['S1'],'location':'summary paragraph 2'}]}


def envelope(phase='producer',provider='kimi'):
    value={'task_id':'T1','submission_id':'T1-SUB-001','work_item_id':'W1','round_id':1,
           'provider':provider,'phase':phase,'source_namespace':'T1/T1-SUB-001'}
    if phase=='reviewer':value.update(candidate_artifact_id='A1',candidate_sha256='a'*64)
    return value


def review(candidate):
    value=content();value.pop('claims');value.pop('draft_markdown')
    value.update(confirmed_errors=[],suspicions=[],unverified=[],wording_suggestions=[],
                 passed_checks=[],blocking=False,semantic_findings=[],
                 claim_checks=[{'claim_id':'C1','result':'verified','blocking':False,
                    'reason':'Original chapter and qualification context checked','source_ids':['S1'],
                    'applicability':{'status':'matched','reason':'Original page explicitly covers this Chemistry B.S. cohort and group choice',
                         'context_complete':True,'comparisons':[{'field':'degree_route','claim_value':'Chemistry B.S.',
                             'source_value':'Chemistry B.S.','result':'matched','reason':'Degree title and section match'}]}}])
    return value


class ContentProtocolTest(unittest.TestCase):
    def test_local_id_alias_is_exact_opt_in_and_retains_raw_and_repair(self):
        value=content();card=value['sources'][0];card['id']=card.pop('source_id');original=copy.deepcopy(value)
        result=process_member_content(value,envelope())
        self.assertEqual(result['errors'],[]);self.assertEqual(value,original)
        self.assertEqual(result['data']['sources'][0]['id'],'S1')
        self.assertEqual(result['data']['sources'][0]['source_id'],'S1')
        record=[x for x in result['repairs'] if x.get('field')=='sources[0].source_id'][0]
        self.assertEqual(record['after'],'S1');self.assertIs(record['before_present'],False)
        self.assertEqual(result['adapter_version'],'member_content_v1.5')
        self.assertEqual(set(result['adapter_code_hashes']),{'handoff.py','research.py','handoff_hardening.py'})
        self.assertTrue(all(len(x)==64 for x in result['adapter_code_hashes'].values()))
        legacy,_=normalize_metadata(value)
        self.assertNotIn('source_id',legacy['sources'][0])
        with self.assertRaisesRegex(ValueError,'source_id'):check_sources(legacy)

    def test_local_id_alias_conflicts_duplicates_and_invalid_values_are_not_repaired(self):
        value=content();value['sources'][0]['id']='EXPLICIT-OTHER-ID'
        result=process_member_content(value,envelope())
        self.assertTrue(any(x['code']=='identity_conflict' and x['location']=='sources[0].source_id' for x in result['errors']))
        self.assertEqual(result['data']['sources'][0]['source_id'],'S1')
        for bad in (None,3,True,{},[]):
            with self.subTest(alias=bad):
                value=content();card=value['sources'][0];card.pop('source_id');card['id']=bad
                result=process_member_content(value,envelope())
                self.assertTrue(any(x['code']=='source_identity' for x in result['errors']))
        value=content();card=value['sources'][0];card['id']=card.pop('source_id')
        value['sources'].append(copy.deepcopy(card))
        self.assertTrue(any(x['code']=='source_identity' for x in process_member_content(value,envelope())['errors']))
        # Aliasing a concise card does not synthesize its nine content fields.
        value=content();card=value['sources'][0];card['id']=card.pop('source_id')
        absent=('published_at','updated_at','access_status','applicable_cohort_or_version',
                'level','scope','excerpt_is_complete','omissions_marked','limits')
        for field in absent:card.pop(field)
        result=process_member_content(value,envelope())
        missing={x['location'].rsplit('.',1)[-1] for x in result['errors'] if x['code']=='required_field'}
        self.assertEqual(missing,set(absent))
        self.assertTrue(all(field not in result['data']['sources'][0] for field in absent))

    def test_T08_collect_all_independent_errors_and_skip_prerequisites(self):
        value=content();value['sources']=['bad-object',{'source_id':'S2','url':'not-public','access_status':'unsupported'}]
        value['draft_markdown']='';value['claims'][0]['text']=3
        result=process_member_content(value,envelope())
        paths={x['location'] for x in result['errors']}
        self.assertIn('sources[0]',paths);self.assertIn('sources[1].title',paths)
        self.assertIn('sources[1].access_status',paths);self.assertIn('draft_markdown',paths)
        self.assertIn('claims[0].text',paths);self.assertTrue(result['skipped'])
        good=process_member_content(content(),envelope());self.assertEqual(good['errors'],[])

    def test_T09_false_string_is_false_without_access_upgrade(self):
        value=content();value['sources'][0].update(excerpt_is_complete='false',omissions_marked='true',access_status='ok',accessed_at='2026-09-30')
        result=process_member_content(value,envelope())
        self.assertEqual(result['errors'],[]);card=result['data']['sources'][0]
        self.assertIs(card['excerpt_is_complete'],False);self.assertIs(card['omissions_marked'],True)
        self.assertEqual(card['access_status'],'reported_access');self.assertIsNone(card['accessed_at'])
        self.assertEqual(value['sources'][0]['excerpt_is_complete'],'false')
        candidate=process_member_content(content(),envelope())['data']
        response=review(candidate);response['sources'][0]['access_status']='partial'
        self.assertTrue(any(x['code']=='original_proof' for x in process_member_content(response,envelope('reviewer','workbuddy'),candidate)['errors']))
        value['sources'][0]['excerpt_is_complete']='yes'
        self.assertTrue(any(x['code']=='boolean_required' for x in process_member_content(value,envelope())['errors']))

    def test_T10_missing_known_fields_bind_only_controller_envelope(self):
        value=content();before=copy.deepcopy(value);result=process_member_content(value,envelope())
        self.assertEqual(result['errors'],[]);self.assertEqual(value,before)
        self.assertEqual(result['data']['task_id'],'T1');self.assertEqual(result['data']['provider'],'kimi')
        self.assertEqual(result['data']['sources'][0]['source_namespace'],'T1/T1-SUB-001')
        candidate=result['data'];checked=process_member_content(review(candidate),envelope('reviewer','workbuddy'),candidate)
        self.assertEqual(checked['errors'],[]);self.assertFalse(checked['semantic_gate']['blocking'])
        self.assertEqual(checked['data']['claim_checks'][0]['claim_sha256'],claim_hash(candidate['claims'][0]))

    def test_T11_explicit_identity_candidate_and_hash_conflicts_reject(self):
        candidate=process_member_content(content(),envelope())['data']
        for path,value in [('task_id','different'),('submission_id','different'),('provider','kimi'),
                           ('candidate_artifact_id','old'),('candidate_sha256','b'*64),('round_id',True)]:
            response=review(candidate);response[path]=value
            result=process_member_content(response,envelope('reviewer','workbuddy'),candidate)
            self.assertTrue(any(x['code']=='identity_conflict' and x['location']==path for x in result['errors']),path)
            self.assertEqual(result['data'][path],value)
        response=review(candidate);response['claim_checks'][0]['claim_sha256']='b'*64
        result=process_member_content(response,envelope('reviewer','workbuddy'),candidate)
        self.assertTrue(any(x['location']=='claim_checks[0].claim_sha256' for x in result['errors']))
        self.assertEqual(result['data']['claim_checks'][0]['claim_sha256'],'b'*64)

    def test_T11_nested_identity_conflict_cannot_hide(self):
        value=content();value['envelope']={'provider':'workbuddy'}
        self.assertTrue(any(x['location']=='envelope.provider' for x in process_member_content(value,envelope())['errors']))

    def test_T12_namespace_distinguishes_same_short_id_but_duplicate_rejects(self):
        one=process_member_content(content(),envelope());other=envelope();other.update(task_id='T2',submission_id='T2-SUB-001',source_namespace='T2/T2-SUB-001')
        two=process_member_content(content(),other)
        self.assertEqual(one['errors'],[]);self.assertEqual(two['errors'],[])
        self.assertNotEqual(one['data']['sources'][0]['source_namespace'],two['data']['sources'][0]['source_namespace'])
        duplicated=content();duplicated['sources'].append(copy.deepcopy(duplicated['sources'][0]))
        self.assertTrue(any(x['code']=='source_identity' for x in process_member_content(duplicated,envelope())['errors']))
        wrong=content();wrong['sources'][0]['source_namespace']='other'
        self.assertTrue(any(x['code']=='identity_conflict' for x in process_member_content(wrong,envelope())['errors']))

    def test_T13_and_T30_substantive_wording_cannot_escape(self):
        candidate=process_member_content(content(),envelope())['data'];response=review(candidate)
        response['wording_suggestions']=[{'id':'W-LAB','location':'summary','severity':'nonblocking','meaning_changed':True,
             'reason':'All four required must become one from each group','blocking':False}]
        result=process_member_content(response,envelope('reviewer','workbuddy'),candidate)
        self.assertEqual(result['errors'],[]);self.assertTrue(result['semantic_gate']['blocking'])
        self.assertFalse(result['data']['blocking'])  # producer/reviewer original verdict not rewritten
        response['wording_suggestions'][0].update(meaning_changed=False,reason='Punctuation only; conditions and object remain identical',severity='editorial')
        self.assertFalse(process_member_content(response,envelope('reviewer','workbuddy'),candidate)['semantic_gate']['blocking'])
        # Correct grouped-choice claim has no keyword alarm.
        self.assertFalse(process_member_content(review(candidate),envelope('reviewer','workbuddy'),candidate)['semantic_gate']['blocking'])

    def test_T13_unstructured_wording_is_pending_not_opposite_fact(self):
        candidate=process_member_content(content(),envelope())['data'];response=review(candidate)
        response['wording_suggestions']=['May need a scope qualification']
        gate=process_member_content(response,envelope('reviewer','workbuddy'),candidate)['semantic_gate']
        self.assertTrue(gate['blocking']);self.assertTrue(all(x['automatic_fact_verdict'] is False for x in gate['findings']))

    def test_T14_wrong_college_route_is_gap_correct_scope_passes(self):
        value=content();value['claims'][0]['applicability']={'unit':'College B','degree_route':'Chemistry B.S.'}
        value['sources'][0]['applicability']={'unit':'College B','degree_route':'Chemistry B.S.'}
        candidate=process_member_content(value,envelope())['data'];response=review(candidate)
        response['sources'][0]['applicability']={'unit':'College A','degree_route':'Chemistry B.A.'}
        gate=process_member_content(response,envelope('reviewer','workbuddy'),candidate)['semantic_gate']
        self.assertTrue(gate['blocking']);self.assertTrue(any(x['category']=='scope_or_time_ambiguity' for x in gate['findings']))
        response['sources'][0]['applicability']=copy.deepcopy(candidate['claims'][0]['applicability'])
        self.assertFalse(process_member_content(response,envelope('reviewer','workbuddy'),candidate)['semantic_gate']['blocking'])

    def test_T15_edit_date_and_outreach_must_not_gain_academic_scope(self):
        value=content();value['sources'][0].update(updated_at='2018-03-01',applicable_cohort_or_version=None,level='outreach')
        value['sources'][0]['applicability']={'audience':'public outreach','cohort':None}
        value['claims'][0]['applicability']={'audience':'undergraduate','cohort':'2018 Spring'}
        candidate=process_member_content(value,envelope())['data'];response=review(candidate)
        response['sources'][0]=copy.deepcopy(value['sources'][0]);response['sources'][0].pop('collected_by',None)
        gate=process_member_content(response,envelope('reviewer','workbuddy'),candidate)['semantic_gate']
        self.assertTrue(gate['blocking']);self.assertIsNone(candidate['sources'][0]['applicable_cohort_or_version'])
        self.assertEqual(candidate['sources'][0]['updated_at'],'2018-03-01')

    def test_T16_exact_ratio_neighbor(self):
        value=content();value['claims'][0]['quantitative_assertion']={'numerator':2,'denominator':4,'threshold_numerator':1,'threshold_denominator':2,'relation':'lt'}
        candidate=process_member_content(value,envelope())['data']
        self.assertTrue(process_member_content(review(candidate),envelope('reviewer','workbuddy'),candidate)['semantic_gate']['blocking'])
        candidate['claims'][0]['quantitative_assertion']['relation']='ge'
        self.assertFalse(process_member_content(review(candidate),envelope('reviewer','workbuddy'),candidate)['semantic_gate']['blocking'])

    def test_T17_trusted_contribution_not_model_name_prevents_self_review(self):
        candidate=process_member_content(content(),envelope())['data'];response=review(candidate)
        result=process_member_content(response,envelope('reviewer','workbuddy'),candidate,contributors={'C1':['kimi','workbuddy']})
        self.assertTrue(any(x['code']=='self_review' for x in result['errors']))
        candidate['claims'][0]['producer']='kimi';candidate['model']='a different model'
        result=process_member_content(response,envelope('reviewer','workbuddy'),candidate,contributors={'C1':['workbuddy']})
        self.assertTrue(any(x['code']=='self_review' for x in result['errors']))
        # An independent evidence proposer is not automatically a content author.
        result=process_member_content(response,envelope('reviewer','workbuddy'),candidate,contributors={'C1':['kimi']})
        self.assertEqual(result['errors'],[])

    def test_T18_closed_revision_must_cover_all_declared_affected_outputs(self):
        candidate=process_member_content(content(),envelope())['data'];response=review(candidate)
        response['candidate_sha256']='a'*64
        response['semantic_findings']=[{'id':'F01','location':'summary+comparison','severity':'blocking','meaning_changed':True,
             'status':'resolved','reason':'The choice condition changed','claim_ids':['C1'],'affected_outputs':['summary','comparison','glossary'],
             'resolution':{'candidate_sha256':'a'*64,'independently_verified':True,'reason':'Exact current wording checked',
                  'source_ids':['S1'],'checked_outputs':['summary']}}]
        self.assertTrue(process_member_content(response,envelope('reviewer','workbuddy'),candidate)['semantic_gate']['blocking'])
        response['semantic_findings'][0]['resolution']['checked_outputs']=['summary','comparison','glossary']
        self.assertFalse(process_member_content(response,envelope('reviewer','workbuddy'),candidate)['semantic_gate']['blocking'])
        response['semantic_findings'][0]['resolution']['candidate_sha256']='b'*64
        result=process_member_content(response,envelope('reviewer','workbuddy'),candidate)
        self.assertTrue(any(x['code']=='identity_conflict' for x in result['errors']))

    def test_optional_scope_values_allow_asserted_equivalence_without_hiding_explicit_conflict(self):
        value=content();value['claims'][0]['scope']='UCB Chemistry B.S.'
        value['sources'][0]['title']='University of California, Berkeley Chemistry B.S. requirements'
        value['sources'][0]['quote']='University of California, Berkeley: '+value['sources'][0]['quote']
        candidate=process_member_content(value,envelope())['data'];response=review(candidate)
        response['sources'][0]=copy.deepcopy(value['sources'][0])
        original_quote=response['sources'][0]['quote']
        comparison=response['claim_checks'][0]['applicability']['comparisons'][0]
        comparison.update(field='school',claim_value='UCB',source_value='University of California, Berkeley',
                          reason='Reviewer checked original school identity; acronym and full name refer to the same university')
        self.assertTrue(process_member_content(response,envelope('reviewer','workbuddy'),candidate)['semantic_gate']['blocking'])
        # Optional raw strings may be omitted after the actual equivalence
        # judgment; direct evidence remains in the unmodified source card.
        comparison.pop('claim_value');comparison.pop('source_value')
        result=process_member_content(response,envelope('reviewer','workbuddy'),candidate)
        self.assertFalse(result['semantic_gate']['blocking']);self.assertEqual(result['data']['sources'][0]['quote'],original_quote)
        comparison.update(claim_value='University of California, Berkeley',source_value='University of California, Berkeley')
        self.assertFalse(process_member_content(response,envelope('reviewer','workbuddy'),candidate)['semantic_gate']['blocking'])
        comparison.update(result='unknown',reason='Original scope qualification could not be established')
        self.assertTrue(process_member_content(response,envelope('reviewer','workbuddy'),candidate)['semantic_gate']['blocking'])

    def test_failed_access_with_no_body_can_be_diagnostic_but_important_fact_stays_blocking(self):
        candidate=process_member_content(content(),envelope())['data']
        for absence in (None,''):
            with self.subTest(absence=absence):
                response=review(candidate);source=response['sources'][0]
                source.update(access_status='access_failed',quote=absence,locator='Actual browser response: 403 Forbidden',
                              limits=['Original page body could not be obtained; no excerpt exists'],excerpt_is_complete=False)
                response['blocking']=True
                response['claim_checks'][0].update(result='access_failed',blocking=True,
                       reason='Original body was unavailable; important fact not verified',
                       applicability={'status':'unresolved','reason':'Original qualification context unavailable',
                                      'context_complete':False,'comparisons':[]})
                raw=copy.deepcopy(response)
                result=process_member_content(response,envelope('reviewer','workbuddy'),candidate)
                self.assertEqual(result['errors'],[]);self.assertTrue(result['semantic_gate']['blocking'])
                self.assertTrue(result['data']['blocking']);self.assertEqual(result['data']['sources'][0]['access_status'],'access_failed')
                self.assertEqual(result['data']['sources'][0]['quote'],'');self.assertEqual(response,raw)
                if absence is None:
                    self.assertTrue(any(x.get('field')=='quote' and x.get('before') is None and x.get('after')=='' for x in result['repairs']))
                # The same failure card can never verify or establish an error.
                for verdict in ('verified','confirmed_error'):
                    asserted=copy.deepcopy(response);asserted['claim_checks'][0]['result']=verdict
                    rejected=process_member_content(asserted,envelope('reviewer','workbuddy'),candidate)
                    self.assertTrue(any(x['code']=='original_proof' for x in rejected['errors']))

    def test_failed_diagnostic_exception_never_applies_to_partial_or_full_body_or_legacy(self):
        for status in ('full_text','partial_text','search_snippet','reported_access'):
            for absence in (None,'','  '):
                with self.subTest(status=status,absence=absence):
                    value=content();value['sources'][0].update(access_status=status,quote=absence,limits=['No body obtained'],locator='Title only')
                    result=process_member_content(value,envelope())
                    self.assertTrue(any(x['code']=='source_text' and x['location']=='sources[0].quote' for x in result['errors']))
                    self.assertEqual(result['data']['sources'][0]['access_status'],status)
        value=content();value['sources'][0].update(collected_by='kimi',access_status='access_failed',quote=None,limits=['403'],locator='403 response')
        with self.assertRaisesRegex(ValueError,'quote'):check_sources(value)  # Old schema unchanged.
        unchanged,_=normalize_metadata(value);self.assertIsNone(unchanged['sources'][0]['quote'])
        self.assertEqual(check_sources(value,protocol=MEMBER_CONTENT),{'S1'})
        for field,bad_value in (('limits',[]),('limits',[' ']),('locator',''),('locator',None)):
            with self.subTest(field=field,bad_value=bad_value):
                incomplete=copy.deepcopy(value);incomplete['sources'][0][field]=bad_value
                result=process_member_content(incomplete,envelope())
                self.assertTrue(any(x['code']=='access_failure_diagnostic' for x in result['errors']))

    def test_semantic_resolution_identity_omission_and_malformed_fields(self):
        candidate=process_member_content(content(),envelope())['data'];response=review(candidate)
        response['semantic_findings']=[{'id':'F1','location':'summary','severity':'blocking','meaning_changed':True,
            'status':'resolved','reason':'Exact current choice condition revised','claim_ids':['C1'],'affected_outputs':['summary'],
            'resolution':{'independently_verified':True,'reason':'Original context checked','source_ids':['S1'],'checked_outputs':['summary']}}]
        result=process_member_content(response,envelope('reviewer','workbuddy'),candidate)
        self.assertEqual(result['errors'],[]);self.assertFalse(result['semantic_gate']['blocking'])
        self.assertEqual(result['data']['semantic_findings'][0]['resolution']['candidate_sha256'],'a'*64)
        response['wording_suggestions']=[{'id':'F1','location':'summary','meaning_changed':True,'severity':'blocking',
             'reason':'Original grouped-choice omission advice retained verbatim'}]
        result=process_member_content(response,envelope('reviewer','workbuddy'),candidate)
        self.assertFalse(result['semantic_gate']['blocking'])  # preserved advice closed by independently located exact-version proof
        del response['semantic_findings'][0]['claim_ids']
        self.assertTrue(process_member_content(response,envelope('reviewer','workbuddy'),candidate)['semantic_gate']['blocking'])
        response['semantic_findings'][0]['claim_ids']=['C1']
        response['semantic_findings'][0]['resolution'].update(source_ids=[{}],checked_outputs=3)
        result=process_member_content(response,envelope('reviewer','workbuddy'),candidate)
        self.assertTrue(any(x['location'].endswith('source_ids') for x in result['errors']))
        self.assertTrue(any(x['location'].endswith('checked_outputs') for x in result['errors']))

    def test_typed_context_change_changes_hash_but_old_hashes_stable(self):
        original=content()['claims'][0];before=claim_hash(original);changed=copy.deepcopy(original)
        changed['applicability']={'requirement_type':'all_required'}
        self.assertNotEqual(before,claim_hash(changed));changed['applicability']['requirement_type']='choose_one_each_group'
        self.assertNotEqual(before,claim_hash(changed))
        changed=copy.deepcopy(original);changed['producer']='a display alias'
        self.assertEqual(before,claim_hash(changed))  # authorship is guarded by trustworthy contribution metadata

    def test_missing_applicability_is_pending_but_explicit_unknown_never_verified(self):
        candidate=process_member_content(content(),envelope())['data'];response=review(candidate)
        del response['claim_checks'][0]['applicability']
        result=process_member_content(response,envelope('reviewer','workbuddy'),candidate)
        self.assertEqual(result['errors'],[]);self.assertTrue(result['semantic_gate']['blocking'])
        self.assertEqual(result['data']['claim_checks'][0]['result'],'verified')  # not silently modified

    def test_gate_direct_call_and_legacy_neighbor(self):
        candidate=process_member_content(content(),envelope())['data'];result=process_member_content(review(candidate),envelope('reviewer','workbuddy'),candidate)
        check_review(result['data'],candidate,'workbuddy',protocol=MEMBER_CONTENT)
        result['data']['wording_suggestions']=['Unclassified possible semantic change']
        with self.assertRaisesRegex(ValueError,'adjudication'):check_review(result['data'],candidate,'workbuddy',protocol=MEMBER_CONTENT)
        check_review(result['data'],candidate,'workbuddy')  # legacy behavior is explicitly unchanged

    def test_legacy_boolean_adapter_remains_opt_in(self):
        value=content();value['sources'][0]['excerpt_is_complete']='false'
        old,_=normalize_metadata(value);self.assertEqual(old['sources'][0]['excerpt_is_complete'],'false')

    def test_unconfirmed_envelope_cannot_bind(self):
        m={'task_id':'T','work_item_id':'W','round_id':1};s={'status':'prepared','package_id':'A'}
        with self.assertRaisesRegex(ValueError,'confirmed'):build_envelope(m,s,{'artifact_id':'A'})


class ContentIngestionTest(unittest.TestCase):
    setUp=CoreTest.setUp
    tearDown=CoreTest.tearDown
    proof=CoreTest.proof
    external_start=CoreTest.external_start

    def research_task(self):
        s=spec();s.update(task_type='research',work_kind='research',assigned_provider='kimi',result_protocol=MEMBER_CONTENT,
                          validators=[{'kind':'research'}])
        t=self.c.create(s)['task_id'];self.c.add_source(t,self.source);self.c.package(t);return t

    def capture(self,t,value,phase='producer'):
        submission,lock=self.external_start(t,phase);path=self.root/'member.json';path.write_text(js(value))
        raw=path.read_bytes();a=self.c.capture_output(t,submission['submission_id'],path,lock,
                 self.proof(t,submission,'output',completed=True,output_hash=digest(raw)))
        self.c.release_gui(t,lock);return submission,a,raw

    def test_raw_preserved_repair_record_hashes_and_namespace(self):
        t=self.research_task();value=content();value['sources'][0]['excerpt_is_complete']='false'
        s,a,raw=self.capture(t,value);out=self.c.ingest_output(t,s['submission_id']);m=self.c.get(t)
        self.assertEqual(self.c.read_artifact(m,a['artifact_id']),raw)
        repaired=json.loads(self.c.read_artifact(m,out['artifact_id']));self.assertEqual(repaired['task_id'],t)
        receipt=json.loads(self.c.read_artifact(m,m['last_repair_record']))
        self.assertEqual(receipt['raw_sha256'],digest(raw));self.assertEqual(receipt['adapted_sha256'],out['hash'])
        self.assertTrue(receipt['changes']);self.assertFalse(receipt['research_content_rewritten'])
        cards=[x for x in m['artifacts'] if x.get('source_global_id')]
        self.assertEqual(len(cards),1);self.assertEqual(cards[0]['source_global_id'],t+'/'+s['submission_id']+'/S1')

    def test_failure_report_retains_every_error_and_no_new_send(self):
        t=self.research_task();value=content();del value['sources'][0]['title'];del value['sources'][0]['quote'];value['draft_markdown']=''
        s,a,raw=self.capture(t,value)
        with self.assertRaises(GuardError):self.c.ingest_output(t,s['submission_id'])
        m=self.c.get(t);report=json.loads(self.c.read_artifact(m,m['last_ingestion_report']))
        paths={x['location'] for x in report['errors']};self.assertIn('sources[0].title',paths);self.assertIn('sources[0].quote',paths);self.assertIn('draft_markdown',paths)
        self.assertEqual(self.c.read_artifact(m,a['artifact_id']),raw);self.assertEqual(len(m['submissions']),1)
        with self.assertRaises(GuardError):self.c.prepare_submission(t,'producer')


class ReviewerPackagingTest(unittest.TestCase):
    setUp=CoreTest.setUp
    tearDown=CoreTest.tearDown
    proof=CoreTest.proof
    external_start=CoreTest.external_start
    capture=ContentIngestionTest.capture
    marker='PRODUCER_SELF_ASSERT_ALL_CLOSED_DO_NOT_DISCLOSE'

    def packaged(self,protocol=MEMBER_CONTENT,with_files=True):
        settings=spec();settings.update(task_type='research',work_kind='research',assigned_provider='kimi',
                                      validators=[{'kind':'research'}])
        if protocol:settings['result_protocol']=protocol
        origin=self.c.create(settings)['task_id'];self.c.add_source(origin,self.source);self.c.package(origin)
        value=content();value['draft_markdown']='Full inline draft. '+self.marker
        if not protocol:
            m=self.c.get(origin)
            value.update(task_id=origin,submission_id=origin+'-SUB-001',work_item_id=m['work_item_id'],round_id=m['round_id'])
            value['sources'][0]['collected_by']='kimi';value['claims'][0]['producer']='kimi'
        submission,_,_=self.capture(origin,value)
        candidate=self.c.ingest_output(origin,submission['submission_id'])
        complete=b'COMPLETE_FROZEN_DELIVERABLE: choose one course from each laboratory group; qualification and exceptions retained.'
        if with_files:
            delivered=self.root/'exchange'/origin/'formal.txt';delivered.write_bytes(complete)
            self.c.freeze_candidate_files(origin,[delivered])
        settings.update(work_kind='review',assigned_provider='workbuddy')
        task=self.c.create(settings)['task_id'];self.c.add_source(task,self.source);self.c.package(task)
        adopted=self.c.adopt_candidate(task,origin,candidate['artifact_id'])
        package=self.c.package(task,'reviewer');m=self.c.get(task)
        return task,m,adopted,package,self.c.read_artifact(m,package['artifact_id']).decode(),complete

    def test_member_frozen_files_hide_inline_producer_assertion_keep_exact_files_claims(self):
        task,m,candidate,package,text,complete=self.packaged()
        self.assertNotIn(self.marker,text)
        self.assertIn('全文以本包冻结成果文件为准',text)
        self.assertIn('Choose one laboratory from each of two groups.',text)
        self.assertIn(candidate['artifact_id'],text);self.assertIn(candidate['hash'],text)
        self.assertIn(candidate['artifact_id'],package['input_artifacts'])
        self.assertEqual(len(m['candidate_files']),1);file=m['candidate_files'][0]
        self.assertIn(file['artifact_id'],package['input_artifacts']);self.assertIn(file['frozen_path'],text)
        self.assertIn(file['sha256'],text);self.assertEqual(file['candidate_artifact_id'],candidate['artifact_id'])
        self.assertEqual(self.c.read_artifact(m,file['artifact_id']),complete);self.assertEqual(file['sha256'],digest(complete))
        # Hiding producer commentary must not waive frozen-byte integrity.
        from pathlib import Path
        Path(file['frozen_path']).write_bytes(b'TAMPERED complete deliverable')
        with self.assertRaises(GuardError):self.c.prepare_submission(task,'reviewer')
        self.assertFalse(self.c.get(task)['submissions'])

    def test_member_without_frozen_files_preserves_actual_inline_draft(self):
        _,m,_,_,text,_=self.packaged(with_files=False)
        self.assertFalse(m.get('candidate_files'));self.assertIn('【待审正文原文】',text)
        self.assertIn('Full inline draft. '+self.marker,text)
        self.assertIn('Choose one laboratory from each of two groups.',text)

    def test_generated_member_review_controls_explain_optional_scope_values_and_exact_closure(self):
        _,_,candidate,_,text,_=self.packaged()
        self.assertIn('claim_value/source_value为可选规范值',text)
        self.assertIn('comparisons每项只需field/result/reason',text)
        self.assertNotIn('exceptions,claim_value,source_value,result,reason',text)
        self.assertIn('已知矛盾写result=mismatch，未知写unknown',text)
        self.assertIn('不能以省略值掩盖矛盾或未知',text)
        start=text.index('语义闭环结构示例')
        example=json.JSONDecoder().raw_decode(text[text.index('{',start):])[0]
        self.assertEqual(example['status'],'resolved')
        self.assertIs(example['meaning_changed'],True)
        self.assertIs(example['resolution']['independently_verified'],True)
        self.assertTrue(example['claim_ids']);self.assertTrue(example['source_ids'])
        self.assertEqual(example['affected_outputs'],example['resolution']['checked_outputs'])
        self.assertIn('candidate_sha256: '+candidate['hash'],text)
        self.assertIn('checked_outputs必须覆盖全部affected_outputs',text)
        self.assertIn('关联claim_checks必须verified且blocking=false',text)
        self.assertIn('不把历史错误当作当前confirmed_errors',text)
        self.assertIn('保留status=pending及实际原因',text)
        field_text=text.split('复核者来源卡完整字段：',1)[1].split('。',1)[0]
        self.assertEqual(field_text.split(', '),list(SOURCE_FIELDS)+['access_time_note'])
        self.assertEqual(len(field_text.split(', ')),19)
        self.assertNotIn('来源卡字段同主执行者',text)
        self.assertIn('未知精确时间填null并必须填写access_time_note',text)
        self.assertIn('不能用默认值补访问、范围、完整性或限制',text)

    def test_legacy_preserves_actual_inline_draft_with_and_without_files(self):
        for with_files in (False,True):
            with self.subTest(with_files=with_files):
                _,m,_,package,text,_=self.packaged(protocol=None,with_files=with_files)
                self.assertIn('【待审正文原文】',text);self.assertIn('Full inline draft. '+self.marker,text)
                if with_files:
                    self.assertIn(m['candidate_files'][0]['artifact_id'],package['input_artifacts'])


del CoreTest
if __name__=='__main__':unittest.main()
