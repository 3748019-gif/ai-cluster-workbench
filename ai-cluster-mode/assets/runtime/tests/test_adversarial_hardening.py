"""Implementation-independent specification probes, isolated actual controller.

The unbound-tree negative intentionally fails on the frozen old version. Other
probes protect existing positive guards; no synthetic provider is claimed real.
"""
import copy
import json
import os
import random
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
if os.environ.get('CLUSTER_TEST_SOURCE'):
    sys.path.insert(0,os.environ['CLUSTER_TEST_SOURCE'])
from cluster.core import Cluster, GuardError, digest, js
from cluster.document_review import document_statement_hash, check_document_review
from cluster.release_checks import build_manifest, check_release
from cluster.handoff import semantic_gate
from adversarial_suite import stats, impact, root_negative_r03, native_proof


class AdversarialHardeningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import cluster.core
        actual=Path(cluster.core.__file__).resolve()
        expected=os.environ.get('CLUSTER_TEST_SOURCE')
        if expected and not actual.is_relative_to(Path(expected).resolve()):
            raise RuntimeError('Wrong controller preloaded by discovery: '+str(actual))
        print('CONTROLLER_SOURCE '+json.dumps({'path':str(actual),'sha256':digest(actual.read_bytes())},sort_keys=True))

    def setUp(self):
        runtime=HERE/'test_runtime';runtime.mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=runtime);self.root=Path(self.tmp.name)
        self.c=Cluster(self.root,'test')

    def tearDown(self):self.c.close();self.tmp.cleanup()

    def task(self,review=False,external=False):
        spec={'objective':'Independent engineering negative','task_type':'document',
              'rules':['READY exact bytes'],'acceptance':['Keep complete exact candidate'],
              'review_required':review,'validators':[{'kind':'text','required':['READY']}]}
        if external:spec['external_benefit']={'provider':'kimi','problem':'synthetic lifecycle','loss_without':'state probes',
            'handoff_cost':'simulation','capability':'second_analysis','client_available':True,'quota_available':True,
            'deterministic_alternative':False}
        task=self.c.create(spec)['task_id'];p=self.root/(task+'-source.txt');p.write_text('READY original')
        self.c.add_source(task,p);self.c.package(task);return task

    def local(self,task):
        self.c.start_local(task);p=self.root/(task+'-candidate.txt');p.write_text(task+' READY exact A')
        a=self.c.local_output(task,p);self.c.validate(task);return a

    def test_unbound_unrelated_tree_cannot_final_deliver(self):
        observed=root_negative_r03(self.root/'r03')
        self.assertTrue(observed['unbound_delivery_rejected'],observed)

    def test_selector_change_enters_old_unreferenced_group(self):
        rows=[{'course_id':'A','required':True,'ai_module':True,'credits':'3','practice_hours':12},
              {'course_id':'B','required':True,'ai_module':True,'credits':'3','practice_hours':12},
              {'course_id':'C','required':True,'ai_module':False,'credits':'3','practice_hours':0},
              {'course_id':'D','required':False,'ai_module':False,'credits':'3','practice_hours':0}]
        newer=copy.deepcopy(rows);newer[-1]['required']=True
        change=impact(rows,newer);self.assertEqual(change['old_group'],['A','B','C'])
        self.assertIn('SIM-CL05',change['affected_claim_ids']);self.assertEqual(stats(newer)['required_ai_qualifier'],'达到半数')
        self.assertEqual(stats(newer)['required_ai_fraction'],'2/4')

    def citation_tree(self,parts):
        tree=self.root/'citations';tree.mkdir()
        with zipfile.ZipFile(tree/'active.docx','w') as z:
            for name,content in parts.items():
                z.writestr(name,"<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"+content+'</w:document>')
        (tree/'MANIFEST_SHA256.json').write_text(js(build_manifest(tree)))
        return tree,{'records':[{'release_source_id':'REL-SIM-001','namespace':'fictional','source_id':'SIM-S001','relative_paths':[]}]}

    def test_unknown_old_school_reference_is_active_negative(self):
        tree,mapping=self.citation_tree({'word/document.xml':'<w:p><w:r><w:t>[UCB-S999]</w:t></w:r></w:p>'})
        report=check_release(tree,source_map=mapping)
        self.assertTrue(any(x['code']=='source_reference_unknown' and x.get('reference')=='UCB-S999' for x in report['issues']),report)

    def test_split_runs_fullwidth_adjacent_registered_references(self):
        tree,mapping=self.citation_tree({'word/document.xml':'<w:p><w:r><w:t>［REL-</w:t></w:r><w:r><w:t>SIM-001］[REL-SIM-001]</w:t></w:r></w:p>'})
        self.assertTrue(check_release(tree,source_map=mapping)['passed'])

    def test_unknown_header_footer_footnote_endnote_table_and_hyperlink_text(self):
        locations=['word/header1.xml','word/footer1.xml','word/footnotes.xml','word/endnotes.xml','word/document.xml']
        parts={name:f'<w:p><w:r><w:t>[REL-SIM-{i:03d}]</w:t></w:r></w:p>' for i,name in enumerate(locations,2)}
        parts['word/document.xml']='<w:tbl><w:tr><w:tc>'+parts['word/document.xml']+'</w:tc></w:tr></w:tbl><w:p><w:hyperlink><w:r><w:t>[REL-SIM-007]</w:t></w:r></w:hyperlink></w:p>'
        tree,mapping=self.citation_tree(parts);report=check_release(tree,source_map=mapping)
        refs={x.get('reference') for x in report['issues'] if x['code']=='source_reference_unknown'}
        self.assertEqual(refs,{f'REL-SIM-{i:03d}' for i in range(2,8)})

    def test_general_ratio_and_rounding_fixed_seeds(self):
        for seed in (911,205,7103):
            r=random.Random(seed)
            for count in (3,4,7,9):
                rows=[{'course_id':str(i),'required':r.choice([True,False]),'ai_module':r.choice([True,False]),
                       'credits':str(r.randrange(1,8)/2),'practice_hours':r.choice([0,8,12,16])} for i in range(count)]
                if not any(x['required'] for x in rows):rows[0]['required']=True
                result=stats(rows);n=result['required_ai_numerator'];d=result['required_ai_denominator']
                expected='超过半数' if n*2>d else '达到半数' if n*2==d else '不足半数'
                self.assertEqual(result['required_ai_qualifier'],expected)
                self.assertEqual(result['required_count'],sum(x['required'] is True for x in rows))

    def test_seeded_ratio_guard_actual_semantic_entry(self):
        # Independent integer cross-product oracle; controller uses Fraction.
        # This is structured arithmetic, not automatic prose entailment.
        for seed in (161,2026,709):
            rng=random.Random(seed)
            for _ in range(12):
                d=rng.randint(1,12);n=rng.randint(0,d);tn=rng.randint(0,3);td=rng.randint(1,5)
                left=n*td;right=tn*d
                for relation,holds in [('lt',left<right),('le',left<=right),('eq',left==right),('ge',left>=right),('gt',left>right)]:
                    candidate={'claims':[{'claim_id':'R1','location':'ratio sentence','producer':'codex',
                        'quantitative_assertion':{'numerator':n,'denominator':d,'threshold_numerator':tn,'threshold_denominator':td,'relation':relation}}]}
                    data={'sources':[],'confirmed_errors':[],'suspicions':[],'unverified':[],'wording_suggestions':[],
                          'semantic_findings':[],'claim_checks':[{'claim_id':'R1','result':'verified','blocking':False,
                          'applicability':{'status':'matched','context_complete':True,'reason':'Synthetic exact population matched',
                          'comparisons':[{'field':'cohort','result':'matched','reason':'Same exact fixture group'}]}}]}
                    gate=semantic_gate(data,candidate)
                    self.assertEqual(gate['blocking'],not holds,(seed,n,d,tn,td,relation,gate))

    def review_data(self):
        statement={'claim_id':'C1','text':'Other degree applicability unknown','producer':'codex',
                   'evidence_status':'accurate_unknown_disclosure','underlying_fact_status':'scope_or_time_ambiguity'}
        candidate={'statements':[statement]}
        review={'sources':[],'claim_checks':[],'confirmed_errors':[],'suspicions':[],'unverified':[],
                'blocking':False,'passed_checks':[{'claim_id':'C1','semantic_sha256':document_statement_hash(statement),
                'basis':'Material describes BSc only; BA unknown disclosed accurately'}]}
        return candidate,review

    def test_stale_semantics_reject_even_one_character_changed(self):
        candidate,review=self.review_data();candidate['statements'][0]['text']+='。'
        with self.assertRaisesRegex(ValueError,'stale'):
            check_document_review(review,candidate,'workbuddy',[])

    def test_unknown_disclosure_is_not_underlying_verified(self):
        candidate,review=self.review_data();original=copy.deepcopy(candidate)
        result=check_document_review(review,candidate,'workbuddy',[])
        self.assertEqual(result['passed_claims'],1);self.assertEqual(candidate,original)
        self.assertEqual(candidate['statements'][0]['underlying_fact_status'],'scope_or_time_ambiguity')

    def test_self_review_not_independent_after_model_label_change(self):
        candidate,review=self.review_data();candidate['statements'][0]['producer']='workbuddy'
        candidate['statements'][0]['producer_model']='DS 4.1 Flash';review['reviewer_model']='DS 4 Pro'
        review['passed_checks'][0]['semantic_sha256']=document_statement_hash(candidate['statements'][0])
        with self.assertRaisesRegex(ValueError,'Self review'):
            check_document_review(review,candidate,'workbuddy',[])

    def test_missing_coverage_never_passes(self):
        candidate,review=self.review_data();review['passed_checks']=[]
        with self.assertRaisesRegex(ValueError,'cover every'):
            check_document_review(review,candidate,'workbuddy',[])

    def test_critical_unknown_not_converted_to_nonblocking(self):
        candidate,review=self.review_data();review['unverified']=review.pop('passed_checks');review['passed_checks']=[]
        with self.assertRaisesRegex(ValueError,'blocking'):
            check_document_review(review,candidate,'workbuddy',[])

    def test_uncertain_recover_never_resends_or_steals_stale_lock(self):
        task=self.task(external=True);sub=self.c.prepare_submission(task,'producer');lock=self.c.acquire_gui(task)
        self.c.begin_submission(task,sub['submission_id'],lock,native_proof(self.c,task,sub,self.root,'preflight'))
        self.c.db.execute("UPDATE locks SET acquired_at='2000-01-01T00:00:00+00:00' WHERE name='GUI_LOCK'");self.c.db.commit()
        r=self.c.recover(task);self.assertFalse(r['automatic_resend_allowed']);self.assertEqual(r['status'],'SUBMISSION_UNCERTAIN')
        self.assertIsNotNone(r['gui_lock']);other=self.task(external=True)
        with self.assertRaises(GuardError):self.c.acquire_gui(other)
        with self.assertRaises(GuardError):self.c.prepare_submission(task,'producer')
        self.assertEqual(self.c.metrics(task)['submission_intents'],1)

    def test_late_previous_task_raw_cannot_be_latest_current_candidate(self):
        old=self.task(external=True);current=self.task(external=True)
        sub=self.c.prepare_submission(current,'producer');lock=self.c.acquire_gui(current)
        self.c.begin_submission(current,sub['submission_id'],lock,native_proof(self.c,current,sub,self.root,'preflight'))
        self.c.confirm_submission(current,sub['submission_id'],lock,native_proof(self.c,current,sub,self.root,'confirmation'))
        late=self.root/'latest-output.json';late.write_text(js({'task_id':old,'submission_id':old+'-SUB-0001','text':'READY old late output'}))
        with self.assertRaises(GuardError):
            self.c.receive(current,sub['submission_id'],late,lock,native_proof(self.c,current,sub,self.root,'output',completed=True,output_hash=digest(late.read_bytes())))
        m=self.c.get(current);self.assertIsNone(m['candidate']);self.assertIn('captured_artifact',m['submissions'][-1])
        self.assertEqual(m['submissions'][-1]['status'],'confirmed');self.c.release_gui(current,lock)

    def test_adjacent_task_numbers_do_not_infer_superseded(self):
        first=self.task();second=self.task();self.local(first);self.local(second)
        self.assertEqual(int(second.rsplit('-',1)[1])-int(first.rsplit('-',1)[1]),1)
        for task in (first,second):
            m=self.c.get(task)
            self.assertFalse(m.get('handled_work'));self.assertFalse(m.get('superseded_by'))
            self.assertTrue(self.c.audit(task)['passed'])

    def test_final_bytes_and_ancestor_tamper_block(self):
        task=self.task();a=self.local(task);self.c.finalize(task)
        (self.c.task_path(task)/a['filename']).write_text('Changed candidate bytes after acceptance')
        with self.assertRaises(GuardError):self.c.deliver(task)

    def test_injected_valid_hash_final_for_other_content_rejects(self):
        task=self.task();a=self.local(task);self.c.finalize(task)
        # Test-only fault: all storage hashes valid, but final B semantically and
        # byte-wise unrelated to accepted A. This is not a production API.
        with self.c._mutate(task) as m:
            alien=self.c._artifact(m,b'UNRELATED B', 'final','codex','injected-final.txt',[a['artifact_id']])
            m['final_output']=alien['artifact_id']
        self.assertTrue(self.c.audit(task)['passed'])
        with self.assertRaisesRegex(GuardError,'current validated candidate'):
            self.c.deliver(task)

    def test_frozen_payload_tamper_blocks_audit(self):
        task=self.task();self.c.start_local(task);p=self.root/'candidate.txt';p.write_text(task+' READY')
        self.c.local_output(task,p);q=self.root/'exchange'/task/'complete.txt';q.write_text('Exact full report')
        frozen=self.c.freeze_candidate_files(task,[q]);self.c.validate(task)
        Path(frozen[0]['frozen_path']).write_text('changed after freeze')
        with self.assertRaises(GuardError):self.c.finalize(task)

    def test_optional_cleanup_denied_preserves_work(self):
        task=self.task();status=self.c.get(task)['status']
        self.assertEqual(self.c.record_optional_action(task,'delete own optional scratch','denied')['decision'],'skip_optional_action')
        self.assertEqual(self.c.get(task)['status'],status)


if __name__=='__main__':unittest.main(verbosity=2)
