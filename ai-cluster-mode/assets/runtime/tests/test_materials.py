"""Contract/route positive and negative tests use isolated real controller state."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from cluster.core import Cluster, GuardError, js
from cluster.materials import unpack, whitespace, sha, render_reading, dependency_plan
from cluster.policy import route
from cluster.release_checks import build_manifest
from cluster.release_binding import transformed_bytes

def spec(**kw):
    s={'objective':'Offline synthetic file processing','task_type':'file_processing','task_route':'file_processing',
       'rules':['Retain all declared components'],'acceptance':['Exact current files'],
       'validators':[{'kind':'json','required_fields':['task_id']} ]}
    s.update(kw);return s

class MaterialTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.c=Cluster(self.root,'test');self.n=0
    def tearDown(self):self.c.close();self.tmp.cleanup()
    def setup_material(self,raw=None,basis_extra=None,item_extra=None,adapter='catalog_v1',mutate_reading=None):
        self.n+=1;t=self.c.create(spec())['task_id'];d=self.root/str(self.n);d.mkdir()
        raw=raw if raw is not None else {'listLength':2,'data':[{'id':'a','text':'x > 6 AND charge = -1'},{'id':'b','text':'1.5 credits OR SIM-101'}]}
        data=raw if isinstance(raw,bytes) else js(raw).encode();p=d/'raw.json';p.write_bytes(data)
        a=self.c.add_source(t,p,'raw.html' if adapter=='static_html_v1' else 'raw.json')
        ctx={'entity':'SIM','version':'test-v1','cohort':'test-only'}
        basis={'scope':'exact frozen synthetic item list','scope_authority':'test suite explicit user-like contract','origin':'test_fixture',
            'expected_count':2,'expected_ids':['a','b'],'id_field':'id','fields':['text'],
            'observed_contexts':[{'source_sha256':sha(data),'context':ctx}]}
        basis.update(basis_extra or {});p=d/'basis.json';p.write_text(js(basis));b=self.c.add_source(t,p)
        it={'key':'one','adapter':adapter,'source_artifacts':[a['artifact_id']],'basis_artifact':b['artifact_id'],
            'target_context':ctx,'reading_file':'reading.html'};it.update(item_extra or {})
        self.c.configure_material_contract(t,{'schema_version':'material_contract_v1','items':[it]})
        self.c.package(t);self.c.start_local(t);d=self.c._output_directory(t,create=True);out=d/'reading.html';self.c.restore_material(t,'one',out)
        if mutate_reading:out.write_text(mutate_reading(out.read_text()))
        p=d/'candidate.json';p.write_text(js({'task_id':t,'status':'not_accepted'}));self.c.local_output(t,p);self.c.freeze_candidate_files(t,[out])
        return t,d
    def finish(self,t):
        self.assertTrue(self.c.validate(t)['passed']);self.c.finalize(t);self.c.deliver(t);self.assertTrue(self.c.audit(t)['passed'])
    def test_full_file_lifecycle_and_six_independent_dimensions(self):
        t,d=self.setup_material();self.finish(t);r=self.c.inspect_materials(t)['items'][0]
        self.assertEqual(r['collection'],'full');self.assertEqual(r['version'],'exact');self.assertEqual(r['applicability'],'not_claimed');self.assertEqual(r['delivery'],'accepted_current_candidate')
    def test_static_normal_login_lesson_and_legal_empty_body(self):
        for raw,basis in [(b'<title>HTTP 403 lesson</title><p>Learn login methods.</p>',{}),(b'<html><body></body></html>',{'allow_empty':True})]:
            with self.subTest(raw=raw):
                t,d=self.setup_material(raw,basis,adapter='static_html_v1');self.finish(t)
    def test_error_200_document_rejected_without_keyword_false_positive(self):
        t,d=self.setup_material(b'<title>Sign in</title><form>View course</form>',adapter='static_html_v1')
        r=self.c.validate(t);self.assertFalse(r['passed']);self.assertEqual(r['materials']['items'][0]['access'],'error_document')
        with self.assertRaises(GuardError):self.c.finalize(t)
    def test_shell_and_missing_required_attachment_block(self):
        for extra in ({'required_tables':2},{'required_attachments':[{'name':'required.pdf','sha256':'a'*64}]}):
            with self.subTest(extra=extra):
                t,d=self.setup_material(b'<p>Some text</p><div class="table-container"></div>',extra,adapter='static_html_v1')
                self.assertFalse(self.c.validate(t)['passed'])
    def test_duplicate_count_and_missing_ids_cannot_self_assert_complete(self):
        raw={'listLength':2,'data':[{'id':'a','text':'same'},{'id':'a','text':'same'}]}
        t,d=self.setup_material(raw);r=self.c.validate(t);self.assertFalse(r['passed']);self.assertIn('duplicate_item_id',r['materials']['items'][0]['errors'])
    def test_strict_partial_blocks_and_explicit_gap_can_be_bounded(self):
        raw={'listLength':1,'data':[{'id':'a','text':'available fragment'}]}
        t,d=self.setup_material(raw);self.assertFalse(self.c.validate(t)['passed'])
        limits={'authorization_source':'Current test user permits exactly missing b','missing_scope':'b','claim':'partial list only','user_impact':'No whole-list conclusions',
                'attempts':['Offline original inspected'],'allowed_errors':['coverage_count_mismatch','coverage_id_set_mismatch']}
        t,d=self.setup_material(raw,item_extra={'limited_delivery':limits});self.finish(t)
        r=self.c.inspect_materials(t)['items'][0];self.assertEqual(r['collection'],'partial');self.assertEqual(r['delivery'],'accepted_with_limits')
    def test_exception_cannot_hide_duplicate_id_or_format_error(self):
        raw={'listLength':2,'data':[{'id':'a','text':'same'},{'id':'a','text':'same'}]}
        limits={'authorization_source':'limited test','missing_scope':'b','claim':'partial','user_impact':'unknown', 'attempts':['fixture'],'allowed_errors':['duplicate_item_id','coverage_id_set_mismatch']}
        t,d=self.setup_material(raw,item_extra={'limited_delivery':limits});self.assertFalse(self.c.validate(t)['passed'])
    def test_source_version_unknown_or_mismatch_not_rewritten_as_exact(self):
        for observed in ([],[{'source_sha256':'f'*64,'context':{'version':'B'}}]):
            t,d=self.setup_material(basis_extra={'observed_contexts':observed});r=self.c.validate(t);self.assertFalse(r['passed']);self.assertEqual(r['materials']['items'][0]['version'],'unknown')
    def test_symbol_changes_and_table_column_swap_fail_exact_candidate(self):
        for old,new in [(' &gt; ',' ≥ '),('AND','OR'),('-1','1'),('1.5','15')]:
            with self.subTest(change=(old,new)):
                t,d=self.setup_material(mutate_reading=lambda text:text.replace(old,new))
                self.assertFalse(self.c.validate(t)['passed'])
    def test_eth_component_positions_units_and_footnote_contract(self):
        raw={'thead':{'rows':[{'cells':[{'content':'Credits'},{'content':'Condition'}]}]},'tbody':{'rows':[{'cells':[{'content':'1.5'},{'content':'A OR B ≥ 6'}]}]}}
        cells=[{'row':0,'col':0,'text':'Credits'},{'row':0,'col':1,'text':'Condition'},{'row':1,'col':0,'text':'1.5'},{'row':1,'col':1,'text':'A OR B ≥ 6'}]
        basis={'tables':[{'row_widths':[2,2],'cells':cells}]}
        t,d=self.setup_material(raw,basis,adapter='eth_table_v1');self.finish(t)
        swapped=copy.deepcopy(raw);swapped['tbody']['rows'][0]['cells'].reverse()
        records,errs,details=unpack('eth_table_v1',[js(swapped).encode()],basis);self.assertIn('table_position_or_symbol_mismatch',errs)
        _,errs,_=unpack('eth_table_v1',[js(raw).encode(),js(raw).encode()],basis);self.assertIn('table_component_mismatch',errs)
    def test_cursor_chain_full_positive_and_partial_loop_query_snapshot_negatives(self):
        pages=[];requests=[]
        for i in range(4):
            edge=[{'cursor':str(i),'node':{'id':str(i),'comment':'record '+str(i)}}]
            d={'data':{'node':{'id':'SIM','numRatings':4,'ratings':{'edges':edge,'pageInfo':{'hasNextPage':i<3,'endCursor':str(i)}}}}}
            b=js(d).encode();pages.append(b);requests.append({'cursor':None if not i else str(i-1),'query':'same','source_sha256':sha(b)})
        basis={'entity':'SIM','query':'same','page_requests':requests,'expected_count':4,'fields':['comment']}
        self.assertEqual(unpack('cursor_v1',pages,basis)[1],[])
        self.assertIn('pagination_not_exhausted',unpack('cursor_v1',pages[:1],basis)[1])
        wrong=copy.deepcopy(basis);wrong['page_requests'][2]['cursor']='0';self.assertIn('duplicate_cursor',unpack('cursor_v1',pages,wrong)[1])
        wrong=copy.deepcopy(basis);wrong['page_requests'][1]['query']='changed';self.assertIn('query_drift',unpack('cursor_v1',pages,wrong)[1])
        wrong=json.loads(pages[1]);wrong['data']['node']['numRatings']=5
        self.assertIn('snapshot_total_drift',unpack('cursor_v1',[pages[0],js(wrong).encode()]+pages[2:],basis)[1])
    def test_unknown_structure_does_not_make_successful_empty_output(self):
        self.assertEqual(unpack('unknown',[],{})[1],['unsupported_adapter'])
        t=self.c.create(spec())['task_id'];self.assertEqual(self.c.inspect_materials(t)['status'],'unknown')
    def test_task_route_research_local_mixed_and_semantic_review(self):
        self.assertEqual(route(spec(work_kind='revision'))['producer'],'codex')
        self.assertEqual(route(spec(task_route='open_research'))['producer'],'kimi')
        self.assertEqual(route(spec(task_route='mixed',route_reason='Split research and processing'))['producer'],'codex')
        r=route(spec(semantic_judgment=True,semantic_scope=['applicability']));self.assertTrue(r['review_required']);self.assertEqual(r['reviewer'],'workbuddy')
        with self.assertRaises(ValueError):route(spec(semantic_judgment=True))
        with self.assertRaises(ValueError):route(spec(task_route='unknown'))
    def test_self_review_and_contributor_reviewer_rejected(self):
        with self.assertRaises(ValueError):route(spec(review_required=True,reviewer_provider='codex'))
        with self.assertRaises(ValueError):route(spec(review_required=True,content_contributors=['workbuddy']))
    def test_html_pdf_are_never_mechanical_reference_transform(self):
        for name in ('html_recovery_v1','html_to_pdf_v1'):
            with self.assertRaises(ValueError):transformed_bytes(b'content',{'version':1,'type':name})
    def test_release_exact_accepted_file_and_wrong_file_rejection(self):
        t,d=self.setup_material();self.assertTrue(self.c.validate(t)['passed']);self.c.finalize(t)
        root=d/'release';root.mkdir();(root/'reading.html').write_bytes((d/'reading.html').read_bytes());(root/'MANIFEST_SHA256.json').write_text(js(build_manifest(root)))
        release=self.c.register_release(t,root);m=self.c.get(t);f=m['candidate_files'][0];accept=m['validation'][-1]['artifact_id']
        binding={'output_path':'reading.html','output_sha256':f['sha256'],'input_task_id':t,'input_artifact_id':f['artifact_id'],'input_sha256':f['sha256'],'acceptance_id':accept,'transform':{'type':'identity_v1','version':1}}
        self.c.bind_release(t,release['release_id'],[binding]);self.c.deliver(t)
        self.assertTrue(self.c.audit(t)['passed'])
        (root/'reading.html').write_text('Wrong but self-consistent file');(root/'MANIFEST_SHA256.json').write_text(js(build_manifest(root)))
        with self.assertRaises(GuardError):self.c._release_delivery_gate(self.c.get(t))
    def test_receipt_dependency_and_layout_only_impact(self):
        t,d=self.setup_material();self.finish(t)
        next_t,d2=self.setup_material(item_extra={'print_profile':{'font_pt':10}})
        impact=self.c.material_impact(next_t,t)['items'][0]
        self.assertIn('content',impact['reuse']);self.assertIn('pdf_text_and_geometry',impact['recheck'])
        self.assertEqual(dependency_plan({'a':'x'},{'a':'x'})['recheck'],[])
    def test_changed_candidate_files_invalidates_passing_material_validation(self):
        t,d=self.setup_material();self.assertTrue(self.c.validate(t)['passed'])
        frozen=Path(self.c.get(t)['candidate_files'][0]['frozen_path']);frozen.write_text('Wrong content')
        with self.assertRaises(GuardError):self.c.finalize(t)
    def test_scope_change_requires_new_work_and_old_records_stay_frozen(self):
        t,d=self.setup_material();before=self.c.get(t)
        with self.assertRaises(GuardError):self.c.configure_material_contract(t,{'schema_version':'material_contract_v1','items':[]})
        self.assertEqual(self.c.get(t),before)

if __name__=='__main__':unittest.main()
