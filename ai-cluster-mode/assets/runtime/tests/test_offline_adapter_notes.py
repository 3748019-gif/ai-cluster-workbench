"""Portable replay of the actual synthetic WorkBuddy round-two raw shape.

The original pilot raw bytes are never changed. These fixed public synthetic
values replay the exact R02 wrapper/view hash distinction without GUI calls.
"""
import copy
import json
import unittest
from cluster.offline_adapter import bind_offline_review
from cluster.document_review import check_document_review,document_statement_hash

LIVE_RAW = '{\n  "task_id": "TASK-20261016-0001",\n  "submission_id": "TASK-20261016-0001-SUB-002",\n  "candidate_artifact_id": "TASK-20261016-0001-ART-6f3e15dea7ee",\n  "candidate_sha256": "44640ee3a16d640d3220f0ef1063727f33851cb2c4b0ef4c3b2303dc4d2396b3",\n  "sources": [],\n  "claim_checks": [],\n  "confirmed_errors": [],\n  "suspicions": [],\n  "unverified": [\n    {\n      "check_id": "CHK-HASH-RECOMPUTE",\n      "claim_id": "R02",\n      "semantic_sha256": "7bedf918a5393e9f127a63b50fbe3418141998bd1c4ad1015b720d54dbeb4d02",\n      "basis": "本工作单元离线且无哈希复算授权，未运行任何哈希重算工具。候选视图 canonical_view_documents.full/simple 中 semantic_mapping.R02.semantic_sha256=7bedf918a5393e9f127a63b50fbe3418141998bd1c4ad1015b720d54dbeb4d02、视图级 semantic_sha256=109db6ef49dcf0cb686b0998e46dd45573ce9b9f611ae5a9638e1a30ca80b988、canonical_views.full=53e54f3b1c3ef4b2daa231f7624456f259e527c88eac1b7f3a58a74447431378、canonical_views.simple=2482bd5095c00f10222c4a58704c7dc9a117e698c96c433c2a61cd8d6be692d6、canonical_draft_sha256=5d452567b4831fcc50c90c432ee9e897c3f2b057395fabc505506311a009a077、canonical_inventory_sha256=4f5be3f81ccba930c0410018225a8ea2cf277ca321847e4414fd2e44c517bb7c 均为控制器/候选给定值，本次仅按其字面引用，未独立复算其字节。",\n      "affected_ids": ["R02"],\n      "blocking": false\n    },\n    {\n      "check_id": "CHK-INHERITED-SCOPE",\n      "claim_id": null,\n      "basis": "本轮为增量轮：review_scope 与 document_review_scope 均为 [R02]，changed_ids=[R02]，open_issue_ids=[CE-R02-01]；inherited_ids=[R01,R03,R04,R05,R06,EA,EB,LAB,ELECTIVE] 属于继承范围，本工作单元未重审其内容，故不计为通过，也不对其报问题。控制器文档语义哈希索引仅给出 R02 一项，未提供上述继承ID的语义哈希，无法据以逐项核验。",\n      "affected_ids": ["R01", "R03", "R04", "R05", "R06", "EA", "EB", "LAB", "ELECTIVE"],\n      "blocking": false\n    },\n    {\n      "check_id": "CHK-PREV-VERSION-DIFF",\n      "claim_id": "R02",\n      "semantic_sha256": "d932168fbd371f1eb2acd9b6c4849c89f309d7e208af197c80ed37683c26b5b8",\n      "basis": "本包只提供当前精确候选（candidate_artifact_id=TASK-20261016-0001-ART-6f3e15dea7ee），未提供上一版候选字节或 CE-R02-01 的原始报错条目内容，故无法做版本差异比对，只能就当前 R02 内容与其必需原件逐字段核对并据此判断 CE-R02-01 在当前版本中是否仍存在。",\n      "affected_ids": ["R02"],\n      "blocking": false\n    },\n    {\n      "check_id": "CHK-VISUAL-QA",\n      "claim_id": null,\n      "basis": "未运行 Word/WPS 视觉验收、渲染或界面操作（角色边界明确：视觉 QA 属后续 Codex 阶段；本单元为离线 JSON 文本与结构核对）。未运行的工具：哈希重算工具、办公软件渲染/视觉检查工具、网络检索。",\n      "affected_ids": ["R02"],\n      "blocking": false\n    }\n  ],\n  "wording_suggestions": [],\n  "passed_checks": [\n    {\n      "claim_id": "R02",\n      "semantic_sha256": "d932168fbd371f1eb2acd9b6c4849c89f309d7e208af197c80ed37683c26b5b8",\n      "source_artifacts": ["TASK-20261016-0001-ART-f9fb5919c996"],\n      "candidate_artifact_id": "TASK-20261016-0001-ART-6f3e15dea7ee",\n      "basis": "原件 TASK-20261016-0001-ART-f9fb5919c996（sha256=5d452567b4831fcc50c90c432ee9e897c3f2b057395fabc505506311a009a077）items 中 id=R02 的条目，与候选 full 视图 items 表第 2 行（semantic_mapping.R02.locations=[{table_id:items,row:1}]，item_ids=[R02]）、simple 视图 items 表第 2 行、以及 statements[claim_id=R02].canonical_item 逐字段一致：name_zh=有机化学、name_original=有机化学、kind=course、category=化学理论、requirement=required、code=SYN-R02、credits.original=4学分、credits.value=4、credits.unit=示例学分、credits.status=known、credits.reason=null、route_ids=[示例本科路线]、applicable_version=fixture-v1、school=示例化学院甲、group_ids=[]（视图写作“无共享组”）、conditions=[] 与 translation_note=\\"\\"（视图写作“无另加条件”）、sources=[{location:rows.R02,occurrence_id:R02}]（视图写作“R02：rows.R02”）。full 视图对应行渲染为“4学分（单位：示例学分）”，与原件 original+unit 的组合规则及其他条目（R01/R03/R04/R06）一致，未改变计量单位。两视图 R02 行互相匹配（simple 视图按模板省略“原文名称”“来源定位”两列，其余单元格逐字符相同）；控制器文档语义哈希索引给定 R02=d932168fbd371f1eb2acd9b6c4849c89f309d7e208af197c80ed37683c26b5b8，与本条 claim 对应。按 scope 要求未将范围外条目计入通过。",\n      "result": "pass"\n    }\n  ],\n  "blocking": false\n}\n'
LIVE_R02 = {'canonical_item': {'applicable_version': 'fixture-v1', 'category': '化学理论', 'code': 'SYN-R02', 'conditions': [], 'credits': {'original': '4学分', 'reason': None, 'status': 'known', 'unit': '示例学分', 'value': 4}, 'group_ids': [], 'id': 'R02', 'kind': 'course', 'name_original': '有机化学', 'name_zh': '有机化学', 'requirement': 'required', 'route_ids': ['示例本科路线'], 'school': '示例化学院甲', 'semantic_history': [], 'sources': [{'location': 'rows.R02', 'occurrence_id': 'R02'}], 'translation_note': ''}, 'claim_id': 'R02', 'producer': 'codex'}

class OfflineAdapterNotesTest(unittest.TestCase):
    def setUp(self):
        self.raw=json.loads(LIVE_RAW)
        self.ids=['EA','EB','ELECTIVE','LAB','R01','R02','R03','R04','R05','R06']
        self.source='TASK-20261016-0001-ART-f9fb5919c996'
        self.m={'task_id':self.raw['task_id'],'source_files':[self.source],
                'spec':{'review_cycle_contract':{'scope_item_ids':self.ids}}}
        self.sub={'submission_id':self.raw['submission_id']}
        self.candidate={'artifact_id':self.raw['candidate_artifact_id'],'hash':self.raw['candidate_sha256']}
        viewhash='7bedf918a5393e9f127a63b50fbe3418141998bd1c4ad1015b720d54dbeb4d02'
        self.body={'statements':[copy.deepcopy(LIVE_R02)]+[{'claim_id':i,'text':'outside current scope'} for i in self.ids if i!='R02'],
            'canonical_view_documents':{name:{'semantic_mapping':{'R02':{'semantic_sha256':viewhash}}} for name in ('full','simple')}}
        self.scope=['R02']

    def adapt(self,data=None):
        out,repairs=bind_offline_review(self.raw if data is None else data,self.m,self.sub,self.candidate,self.body,self.scope)
        check_document_review(out,self.body,'workbuddy',[self.source],self.scope,self.candidate['artifact_id'])
        return out,repairs

    def test_actual_raw_second_round_metadata_references_normalize_without_rewriting_values(self):
        before=copy.deepcopy(self.raw)
        self.assertEqual(document_statement_hash(LIVE_R02),'d932168fbd371f1eb2acd9b6c4849c89f309d7e208af197c80ed37683c26b5b8')
        out,repairs=self.adapt()
        self.assertEqual(self.raw,before)
        self.assertEqual(out['passed_checks'],before['passed_checks'])
        self.assertEqual(out['blocking'],before['blocking'])
        self.assertEqual(len(repairs),4)
        for old,new,record in zip(before['unverified'],out['unverified'],repairs):
            for key in ('claim_id','affected_ids','semantic_sha256'):
                self.assertNotIn(key,new)
                if key in old:self.assertEqual(new['referenced_'+key],old[key])
            self.assertEqual(new['basis'],old['basis'])
            self.assertFalse(record['underlying_facts_verified'])
            self.assertFalse(record['counts_as_scoped_coverage'])
            self.assertFalse(record['body_or_fact_text_changed'])
        self.assertEqual(repairs[0]['reference_hash_proof'][0]['matched_domains'],['canonical_view:full','canonical_view:simple'])
        self.assertEqual(repairs[2]['reference_hash_proof'][0]['matched_domains'],['controller_statement'])
        self.assertEqual(out['unverified'][1]['referenced_affected_ids'],before['unverified'][1]['affected_ids'])

    def test_actual_unknown_claim_cannot_be_reclassified_as_supplementary(self):
        for mutate in (lambda r:r['passed_checks'].clear(),
                       lambda r:r['unverified'][0].pop('check_id'),
                       lambda r:r['unverified'][0].update(blocking=True),
                       lambda r:r['unverified'][0].pop('blocking')):
            raw=copy.deepcopy(self.raw);mutate(raw)
            with self.subTest(raw=raw['unverified'][0]),self.assertRaises(ValueError):self.adapt(raw)

    def test_explicitly_wrong_reference_hash_is_never_replaced_with_controller_value(self):
        raw=copy.deepcopy(self.raw);raw['unverified'][0]['semantic_sha256']='f'*64
        before=copy.deepcopy(raw)
        with self.assertRaisesRegex(ValueError,'reference hash'):self.adapt(raw)
        self.assertEqual(raw,before)

    def test_missing_correct_current_independent_hash_cannot_authorize_adaptation(self):
        for patch in ({'semantic_sha256':'f'*64},{'semantic_sha256':None},{'claim_id':'UNKNOWN'},
                      {'result':'unknown'},{'blocking':True}):
            raw=copy.deepcopy(self.raw);raw['passed_checks'][0].update(patch)
            with self.subTest(patch=patch),self.assertRaises(ValueError):self.adapt(raw)

    def test_outside_stable_scope_references_rejected(self):
        for patch in ({'claim_id':'OTHER-SCHOOL'},{'affected_ids':['OTHER-SCHOOL']},
                      {'affected_ids':['R02','R02']}):
            raw=copy.deepcopy(self.raw);raw['unverified'][0].update(patch)
            with self.subTest(patch=patch),self.assertRaises(ValueError):self.adapt(raw)

    def test_top_level_candidate_or_source_conflict_rejected(self):
        for patch in ({'candidate_sha256':'f'*64},{'candidate_artifact_id':'FOREIGN'},
                      {'task_id':'OTHER-TASK'},{'submission_id':'OTHER-SEND'},
                      {'sources':[{'source_id':'invented'}]}):
            raw=copy.deepcopy(self.raw);raw.update(patch)
            with self.subTest(patch=patch),self.assertRaises(ValueError):self.adapt(raw)

    def test_formal_check_candidate_and_source_fields_must_match_frozen_package(self):
        for patch in ({'candidate_artifact_id':'FOREIGN'},{'candidate_sha256':'f'*64},
                      {'source_artifacts':['FOREIGN']}):
            raw=copy.deepcopy(self.raw);raw['passed_checks'][0].update(patch)
            with self.subTest(patch=patch),self.assertRaises(ValueError):self.adapt(raw)

    def test_existing_reference_field_conflict_rejected(self):
        raw=copy.deepcopy(self.raw);raw['unverified'][0]['referenced_claim_id']='R01'
        with self.assertRaisesRegex(ValueError,'reference metadata conflict'):self.adapt(raw)

    def test_non_current_unknown_fact_is_not_hidden_by_a_current_pass(self):
        raw=copy.deepcopy(self.raw)
        raw['unverified'].append({'claim_id':'R01','semantic_sha256':document_statement_hash(self.body['statements'][1]),
                                  'basis':'A genuine scope-specific unverified fact','blocking':True})
        with self.assertRaises(ValueError):self.adapt(raw)

    def test_incomplete_current_scope_positive_checks_cannot_authorize_notes(self):
        self.scope=['R02','R01']
        with self.assertRaisesRegex(ValueError,'complete current independent'):self.adapt()

    def test_unknown_fact_without_positive_check_remains_a_genuine_scoped_finding(self):
        raw=copy.deepcopy(self.raw)
        raw['unverified']=[{'claim_id':'R02','semantic_sha256':document_statement_hash(LIVE_R02),
                            'basis':'Required evidence remains absent','blocking':True}]
        raw['passed_checks']=[];raw['blocking']=True
        out,repairs=self.adapt(raw)
        self.assertEqual(out['unverified'],raw['unverified'])
        self.assertTrue(out['blocking']);self.assertEqual(repairs,[])

if __name__=='__main__':unittest.main(verbosity=2)
