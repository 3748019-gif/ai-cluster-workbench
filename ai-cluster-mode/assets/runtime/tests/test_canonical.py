"""Declared occurrence coverage and one-draft views through real controller state."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from cluster.core import Cluster, GuardError, js
from cluster.canonical import CanonicalMixin, INVENTORY, DRAFT, TEMPLATE


def spec(**changes):
    value = {'objective': 'Synthetic declared curriculum coverage', 'task_type': 'file_processing',
             'task_route': 'file_processing', 'rules': ['Preserve exact source restrictions'],
             'acceptance': ['One declared evidence-bound draft and two matching views'],
             'validators': [{'kind': 'json', 'required_fields': ['task_id']}],
             'canonical_workflow_v1': True}
    value.update(changes)
    return value


def curriculum_fixture(c, school='示例化学院甲', scope_id='SYN-CURRICULUM-1', spec_changes=None):
    """Transcription of public protocol fixture; expectations are never dispatched.

    Returns (task_id, inventory, draft). Caller can modify the new objects before
    registering, and can reuse this fixture in integrated document-stage tests.
    All paths point to a fresh real controller task, not a parallel toy ledger.
    """
    task = c.create(spec(**(spec_changes or {})))['task_id']
    route, version, unit = '示例本科路线', 'fixture-v1', '示例学分'
    credit = lambda value, text=None: {'value': value, 'unit': unit,
        'status': 'known' if value is not None else 'variable',
        'original': text or str(value) + '学分',
        'reason': None if value is not None else '原件未单列固定数值'}
    items = []
    courses = [('R01', '普通化学', 3, 'required', '化学理论', []),
               ('R02', '有机化学', 4, 'required', '化学理论', []),
               ('R03', '化学实验甲', 2, 'restricted_elective', '化学实验', ['LAB']),
               ('R04', '化学实验乙', 3, 'restricted_elective', '化学实验', ['LAB']),
               ('R05', '毕业研究', None, 'required', '科研训练', []),
               ('R06', '高等数学', 4, 'required', '支撑课程', []),
               ('EA', '专业选修甲类', None, 'open_elective', '专业选修', ['ELECTIVE']),
               ('EB', '专业选修乙类', None, 'open_elective', '专业选修', ['ELECTIVE'])]
    for key, name, value, required, category, groups in courses:
        kind = 'open_elective' if key in ('EA', 'EB') else 'requirement' if key == 'R05' else 'course'
        raw_credit = credit(value, '按所选研究确定' if key == 'R05' else '在共享组内按选课确定' if key in ('EA', 'EB') else None)
        items.append({'id': key, 'school': school, 'route_ids': [route], 'applicable_version': version,
                      'kind': kind, 'code': 'SYN-' + key if kind == 'course' else None,
                      'name_original': name, 'name_zh': name, 'credits': raw_credit,
                      'category': category, 'requirement': required, 'group_ids': groups,
                      'conditions': [], 'sources': [{'occurrence_id': key, 'location': 'rows.' + key}],
                      'translation_note': '', 'semantic_history': []})
    constraints = lambda credits=None, count=None, expression='': {
        'min_credits': credits, 'max_credits': None, 'min_courses': count,
        'max_courses': None, 'expression': expression}
    groups = [{'id': 'LAB', 'school': school, 'route_ids': [route], 'applicable_version': version,
               'name_zh': '化学实验选择组', 'members': [{'item_id': k, 'route_ids': [route]} for k in ('R03', 'R04')],
               'constraints': constraints(count=1, expression='化学实验甲与乙中至少选择1门，两门不是同时必修'),
               'conditions': [], 'sources': [{'occurrence_id': 'G-LAB', 'location': 'groups.LAB'}]},
              {'id': 'ELECTIVE', 'school': school, 'route_ids': [route], 'applicable_version': version,
               'name_zh': '专业选修共享额度', 'members': [{'item_id': k, 'route_ids': [route]} for k in ('EA', 'EB')],
               'constraints': constraints(credits=12, expression='甲乙两类合计至少12学分，不是每类分别12'),
               'conditions': [], 'sources': [{'occurrence_id': 'G-ELECTIVE', 'location': 'groups.ELECTIVE'}]}]
    draft = {'schema_version': DRAFT, 'school': school, 'scope_id': scope_id,
             'version': version, 'items': items, 'groups': groups, 'history': []}
    original = c.task_path(task) / 'fixture-original.json'
    original.write_text(js(draft))
    artifact = c.add_source(task, original, 'fixture-original.json')
    occurrences, mappings = [], []
    for item in items + groups:
        is_group = item['id'] in ('LAB', 'ELECTIVE')
        oid = item['sources'][0]['occurrence_id']
        facts = {k: copy.deepcopy(item[k]) for k in ('constraints', 'members', 'conditions') if k in item} if is_group else {
            k: copy.deepcopy(item[k]) for k in ('credits', 'requirement', 'conditions', 'group_ids', 'category', 'name_zh')}
        occurrences.append({'occurrence_id': oid, 'source_id': 'SRC', 'location': item['sources'][0]['location'],
                            'kind': 'group' if is_group else item['kind'],
                            'identity': {'school': school, 'code': None if is_group else item['code'],
                                         'name_original': item.get('name_original', item.get('name_zh')), 'version': version},
                            'route': route, 'required': True, 'facts': facts})
        mappings.append({'occurrence_id': oid, 'status': 'included', 'entity_id': item['id'],
                         'route_ids': [route], 'reason': None, 'evidence': []})
    stage = {'status': 'complete', 'scope': 'Exact public protocol fixture; no full-school claim', 'pending_reason': None}
    inventory = {'schema_version': INVENTORY, 'school': school, 'scope_id': scope_id,
        'sources': [{'source_id': 'SRC', 'artifact_id': artifact['artifact_id'],
                     'original_path': str(original), 'sha256': artifact['hash'], 'version': version,
                     'routes': [route], 'collection': copy.deepcopy(stage), 'reading': copy.deepcopy(stage),
                     'parse_complete': True, 'expected_occurrence_ids': [x['occurrence_id'] for x in occurrences]}],
        'occurrences': occurrences, 'mappings': mappings}
    return task, inventory, draft


# Supports the isolated module while its owning integrator adds the real mixin.
Controller = Cluster if hasattr(Cluster, 'register_inventory') else type('CanonicalController', (CanonicalMixin, Cluster), {})


class CanonicalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.c = Controller(self.temp.name, 'test')

    def tearDown(self):
        self.c.close(); self.temp.cleanup()

    def ready(self):
        task, inventory, draft = curriculum_fixture(self.c)
        self.c.register_inventory(task, inventory)
        self.c.freeze_canonical(task, draft)
        self.c.generate_canonical_views(task)
        return task, inventory, draft

    def test_T01_missing_R06_refuses_freeze_then_complete_mapping_passes(self):
        task, inv, draft = curriculum_fixture(self.c)
        missing = copy.deepcopy(inv); missing['mappings'] = [x for x in missing['mappings'] if x['occurrence_id'] != 'R06']
        r = self.c.register_inventory(task, missing)
        self.assertEqual(r['coverage']['pending'], ['R06'])
        with self.assertRaisesRegex(GuardError, 'R06'):
            self.c.freeze_canonical(task, draft)
        self.c.register_inventory(task, inv)
        missing_draft = copy.deepcopy(draft); missing_draft['items'] = [x for x in draft['items'] if x['id'] != 'R06']
        with self.assertRaisesRegex(GuardError, 'R06'):
            self.c.freeze_canonical(task, missing_draft)
        self.c.freeze_canonical(task, draft); self.c.generate_canonical_views(task)
        self.assertTrue(self.c.inspect_canonical(task)['passed'])

    def test_T01_expected_but_unextracted_occurrence_is_not_unknown_fact(self):
        task, inv, draft = curriculum_fixture(self.c)
        inv['occurrences'] = [x for x in inv['occurrences'] if x['occurrence_id'] != 'R06']
        inv['mappings'] = [x for x in inv['mappings'] if x['occurrence_id'] != 'R06']
        self.c.register_inventory(task, inv)
        with self.assertRaisesRegex(GuardError, 'R06'):
            self.c.freeze_canonical(task, draft)

    def test_T02_open_elective_preserves_category_and_shared_budget_without_invented_courses(self):
        task, inv, draft = self.ready()
        report = self.c.inspect_canonical(task)
        self.assertEqual(report['item_count'], 8)
        self.assertEqual(report['coverage']['mapping_rate'], 1)
        for view in report['views'].values():
            body = json.loads(self.c.read_artifact(self.c.get(task), view['artifact_id']))
            self.assertEqual(set(body['semantic_mapping']), {x['id'] for x in draft['items'] + draft['groups']})
            groups = body['tables'][1]['rows']
            self.assertEqual(sum('合计最低学分：12' in r['cells'][3] for r in groups), 1)
            self.assertIn('甲乙两类合计至少12学分', groups[1]['cells'][3])

    def test_T03_variable_credits_visible_in_both_views_never_zero(self):
        task, inv, draft = self.ready()
        m = self.c.get(task)
        for view in m['canonical']['views'].values():
            rows = json.loads(self.c.read_artifact(m, view['artifact_id']))['tables'][0]['rows']
            research = next(x for x in rows if x['item_ids'] == ['R05'])
            self.assertIn('按所选研究确定', ' '.join(research['cells']))
            self.assertIn('原件未单列固定数值', ' '.join(research['cells']))
        bad = copy.deepcopy(draft); bad['items'][4]['credits']['value'] = 0
        with self.assertRaisesRegex(GuardError, 'never zero'):
            self.c.freeze_canonical(task, bad)

    def test_T03_known_four_credits_cannot_be_relabelled_unknown(self):
        task, inv, draft = curriculum_fixture(self.c)
        self.c.register_inventory(task, inv)
        draft['items'][1]['credits'].update(value=None, status='unknown', reason='没有读取')
        with self.assertRaisesRegex(GuardError, 'original fact'):
            self.c.freeze_canonical(task, draft)

    def test_T04_shared_budget_cannot_be_split_and_selection_cannot_be_required(self):
        task, inv, draft = curriculum_fixture(self.c)
        self.c.register_inventory(task, inv)
        for mutate in ('count', 'required', 'double'):
            bad = copy.deepcopy(draft)
            if mutate == 'count':
                bad['groups'][0]['constraints']['min_courses'] = 2
            elif mutate == 'required':
                bad['items'][2]['requirement'] = 'required'
            else:
                bad['groups'][1]['constraints']['min_credits'] = 24
            with self.subTest(mutate=mutate), self.assertRaises(GuardError):
                self.c.freeze_canonical(task, bad)

    def test_T05_duplicate_occurrences_have_distinct_counts_and_route_links(self):
        task, inv, draft = curriculum_fixture(self.c)
        route2 = '示例第二路线'
        inv['sources'][0]['routes'].append(route2)
        occurrence = copy.deepcopy(inv['occurrences'][0]); occurrence.update(occurrence_id='R01-DUP', route=route2, location='appendix.R01')
        inv['sources'][0]['expected_occurrence_ids'].append('R01-DUP'); inv['occurrences'].append(occurrence)
        inv['mappings'].append({'occurrence_id': 'R01-DUP', 'status': 'duplicate', 'entity_id': 'R01', 'route_ids': [route2],
            'reason': '同一代码版本在第二路线再次出现', 'evidence': [], 'duplicate_of': 'R01'})
        draft['items'][0]['route_ids'].append(route2)
        draft['items'][0]['sources'].append({'occurrence_id': 'R01-DUP', 'location': 'appendix.R01'})
        self.c.register_inventory(task, inv); self.c.freeze_canonical(task, draft); self.c.generate_canonical_views(task)
        report = self.c.inspect_canonical(task)['coverage']
        self.assertEqual(report['observed_occurrences'], 11)
        self.assertEqual(report['unique_entities'], 10)
        self.assertEqual(report['route_links'], 11)

    def test_T05_same_name_different_code_or_version_cannot_merge(self):
        for field in ('code', 'version'):
            task, inv, draft = curriculum_fixture(self.c)
            occurrence = copy.deepcopy(inv['occurrences'][0]); occurrence.update(occurrence_id='DUP', location='appendix')
            occurrence['identity'][field] += '-different'
            inv['sources'][0]['expected_occurrence_ids'].append('DUP'); inv['occurrences'].append(occurrence)
            inv['mappings'].append({'occurrence_id': 'DUP', 'status': 'duplicate', 'entity_id': 'R01', 'route_ids': [occurrence['route']],
                'reason': '同名误合并', 'evidence': [], 'duplicate_of': 'R01'})
            with self.subTest(field=field), self.assertRaises(GuardError):
                self.c.register_inventory(task, inv)

    def test_T06_changed_single_view_bytes_rejected_and_raw_history_survives(self):
        task, inv, draft = self.ready(); m = self.c.get(task)
        raw = self.c.read_artifact(m, m['canonical']['draft'])
        record = m['canonical']['views']['simple']; a = self.c.artifact(m, record['artifact_id'])
        path = self.c.task_path(task) / a['filename']
        view = json.loads(path.read_text()); view['tables'][0]['rows'][0]['cells'][2] = '0学分'
        path.write_text(js(view))
        with self.assertRaisesRegex(GuardError, 'hash changed'):
            self.c._canonical_gate(self.c.get(task))
        self.assertEqual(self.c.read_artifact(m, m['canonical']['draft']), raw)

    def test_T06_author_claimed_hash_does_not_make_forged_view_valid(self):
        task, inv, draft = self.ready()
        with self.c._mutate(task) as m:
            state = m['canonical']; previous = state['views']['simple']
            view = json.loads(self.c.read_artifact(m, previous['artifact_id']))
            view['tables'][0]['rows'][0]['cells'][2] = '伪造数值0'
            a = self.c._artifact(m, js(view).encode(), 'evidence', 'codex', 'canonical-simple.json', [state['draft']])
            state['views']['simple'] = {'artifact_id': a['artifact_id'], 'sha256': a['hash'], 'semantic_sha256': view['semantic_sha256']}
        with self.assertRaisesRegex(GuardError, 'deterministic'):
            self.c._canonical_gate(self.c.get(task))

    def test_T19_group_boundary_change_is_semantic_not_layout(self):
        task, inv, draft = self.ready()
        bad = copy.deepcopy(draft); bad['groups'][1]['members'] = bad['groups'][1]['members'][:1]
        with self.assertRaises(GuardError):
            self.c.freeze_canonical(task, bad)
        simple = self.c.inspect_canonical(task)['views']['simple']['artifact_id']
        body = json.loads(self.c.read_artifact(self.c.get(task), simple))
        self.assertIn('EA', body['tables'][1]['rows'][1]['cells'][2])
        self.assertIn('EB', body['tables'][1]['rows'][1]['cells'][2])

    def test_missing_reading_parse_and_registered_source_remain_blocking(self):
        for case in ('partial', 'parse', 'unlisted'):
            task, inv, draft = curriculum_fixture(self.c)
            if case == 'partial':
                inv['sources'][0]['reading'].update(status='partial', pending_reason='末页未读')
            elif case == 'parse':
                inv['sources'][0]['parse_complete'] = False
            else:
                p = self.c.task_path(task) / 'overlooked.txt'; p.write_text('Overlooked named course')
                self.c.add_source(task, p)
            if case == 'unlisted':
                with self.assertRaisesRegex(GuardError, 'absent from source inventory'):
                    self.c.register_inventory(task, inv)
            else:
                self.c.register_inventory(task, inv)
                with self.assertRaisesRegex(GuardError, 'coverage freeze refused'):
                    self.c.freeze_canonical(task, draft)

    def test_exclusion_requires_precise_registered_evidence_and_rule(self):
        task, inv, draft = curriculum_fixture(self.c)
        mapping = inv['mappings'][5]
        mapping.update(status='excluded', entity_id=None, route_ids=[], reason='Excluded by task scope')
        with self.assertRaisesRegex(GuardError, 'exclusion rule'):
            self.c.register_inventory(task, inv)
        mapping['exclusion_rule'] = 'Explicit synthetic scope authorizes exclusion of supporting maths'
        source = inv['sources'][0]
        mapping['evidence'] = [{'artifact_id': source['artifact_id'], 'sha256': source['sha256'], 'location': 'rows.R06', 'quote': '高等数学'}]
        with self.assertRaisesRegex(GuardError, 'rule must be quoted'):
            self.c.register_inventory(task, inv)
        p = self.c.task_path(task) / 'scope-exception.txt'; p.write_text(mapping['exclusion_rule'])
        authorization = self.c.add_source(task, p)
        second_source = copy.deepcopy(source)
        second_source.update(source_id='USER-SCOPE', artifact_id=authorization['artifact_id'],
            original_path=str(p), sha256=authorization['hash'], expected_occurrence_ids=[])
        inv['sources'].append(second_source)
        mapping['evidence'] = [{'artifact_id': authorization['artifact_id'], 'sha256': authorization['hash'],
            'location': 'user-scope line 1', 'quote': mapping['exclusion_rule']}]
        draft['items'] = [x for x in draft['items'] if x['id'] != 'R06']
        self.c.register_inventory(task, inv); self.c.freeze_canonical(task, draft); self.c.generate_canonical_views(task)
        self.assertEqual(self.c.inspect_canonical(task)['coverage']['dispositions']['excluded'], 1)
        mapping['evidence'][0]['quote'] = '不存在的授权'
        with self.assertRaisesRegex(GuardError, 'not found'):
            self.c.register_inventory(task, inv)

    def test_unknown_fields_bad_source_hash_and_configuration_not_ignored(self):
        for case in ('field', 'hash', 'parameters'):
            task, inv, draft = curriculum_fixture(self.c)
            if case == 'field':
                inv['sources'][0]['silently_ignore_me'] = True
            elif case == 'hash':
                inv['sources'][0]['sha256'] = 'f' * 64
            if case == 'parameters':
                self.c.register_inventory(task, inv); self.c.freeze_canonical(task, draft)
                with self.assertRaisesRegex(GuardError, 'unknown fields'):
                    self.c.generate_canonical_views(task, parameters={'hide_restrictions': True})
            else:
                with self.assertRaises(GuardError):
                    self.c.register_inventory(task, inv)

    def test_old_task_remains_legacy_and_sending_content_is_immutable(self):
        old = self.c.create(spec(canonical_workflow_v1=False))['task_id']
        self.assertEqual(self.c.inspect_canonical(old)['status'], 'legacy_not_enabled')
        with self.assertRaisesRegex(GuardError, 'not enabled'):
            self.c.register_inventory(old, {})
        task, inv, draft = self.ready()
        with self.c._mutate(task) as m:
            m['status'] = 'SUBMISSION_UNCERTAIN'
        with self.assertRaisesRegex(GuardError, 'while sending'):
            self.c.freeze_canonical(task, draft)

    def test_recovery_keeps_exact_draft_views_and_artifact_ancestry(self):
        task, inv, draft = self.ready()
        before = self.c.inspect_canonical(task)
        self.c.close(); self.c = Controller(self.temp.name, 'test')
        after = self.c.inspect_canonical(task)
        self.assertEqual(before, after)
        self.assertTrue(self.c.audit(task)['passed'])

    def test_impact_uses_source_bytes_not_different_task_artifact_UUIDs(self):
        first, inv, draft = self.ready()
        second, inv2, draft2 = self.ready()
        impact = self.c.canonical_impact(second, first)
        self.assertFalse(impact['source_context_changed'])
        self.assertFalse(impact['semantic_changed'])
        self.assertEqual(impact['affected_ids'], [])
        self.assertTrue(impact['views']['simple']['changed_bytes'])

    def test_new_template_parameters_change_view_bytes_without_semantic_change(self):
        task, inv, draft = self.ready()
        before = self.c.inspect_canonical(task)
        self.c.generate_canonical_views(task, TEMPLATE, {'simple_show_sources': True})
        after = self.c.inspect_canonical(task)
        self.assertEqual(before['semantic_sha256'], after['semantic_sha256'])
        self.assertNotEqual(before['views']['simple']['sha256'], after['views']['simple']['sha256'])
        self.assertEqual(after['views']['full']['semantic_sha256'], after['views']['simple']['semantic_sha256'])


if __name__ == '__main__':
    unittest.main()
