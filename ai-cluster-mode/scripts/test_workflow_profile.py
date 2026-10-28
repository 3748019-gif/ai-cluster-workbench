"""Workflow profile and unified-entry regression; local files only, no GUI.

Run --pure while a maintainer is authoring the package. The default full suite
requires a finalized, integrity-valid toolkit and never rebuilds its manifests.
External configuration copies and fresh temporary instances are used throughout.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
KIT = HERE.parent
RUNTIME = KIT / 'assets/runtime'
sys.path.insert(0, str(HERE))
import run_profile as rp

FLOW = {'canonical_required': True, 'native_documents_required': True,
        'automatic_review_rounds': 2, 'hard_timeout_minutes': None}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def configuration(run_id='workflow-public-test'):
    cfg = json.loads((KIT / 'templates/run-profile.json').read_text())
    cfg.update(run_id=run_id, authorization_source='User-authorized isolated workflow regression',
               workflow_v1=copy.deepcopy(FLOW))
    return cfg


def task_spec():
    return {'objective': 'One synthetic curriculum item', 'task_type': 'document',
            'task_route': 'file_processing', 'assigned_provider': 'codex',
            'review_required': True, 'reviewer_provider': 'workbuddy',
            'rules': ['Preserve synthetic source facts and original units'],
            'acceptance': ['Evidence-bound draft and independent content review'],
            'validators': [{'kind': 'json', 'required_fields': ['task_id']}],
            'review_cycle_contract': {'version': 1, 'unit_id': 'SYNTHETIC-SCHOOL',
                'scope_id': 'SYNTHETIC-SCOPE', 'scope_item_ids': ['R01'],
                'dependency_map': {}, 'user_rules': ['Do not turn unknown into zero'],
                'exceptions': [], 'required_source_artifacts': [],
                'source_read_allowed': True, 'review_role': 'secondary_executor'}}


class PureWorkflowProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='workflow_profile_pure_')
        self.base = Path(self.tmp.name)
        self.config = self.base / 'external-profile.json'
        self.registry = json.loads((RUNTIME / '.cluster/roles.json').read_text())

    def tearDown(self):
        self.tmp.cleanup()

    def load(self, cfg):
        write_json(self.config, cfg)
        return rp.load_config(self.config, self.registry)

    def test_valid_workflow_preserves_role_limits_and_client_settings(self):
        cfg = configuration()
        cfg['roles']['primary_executor']['max_calls'] = 0
        cfg['roles']['secondary_executor']['max_search_queries'] = None
        result = self.load(cfg)
        self.assertEqual(result['config']['workflow_v1'], FLOW)
        self.assertEqual(result['config']['roles']['primary_executor']['max_calls'], 0)
        self.assertIsNone(result['config']['roles']['secondary_executor']['max_search_queries'])
        self.assertFalse(result['boundary']['client_settings_changed'])
        self.assertFalse(result['boundary']['paid_actions_allowed'])

    def test_explicit_per_run_network_scope_validated_without_changing_defaults(self):
        cfg=configuration()
        for role in ('primary_executor','secondary_executor'):cfg['roles'][role]['network_access']='offline'
        result=self.load(cfg)
        self.assertEqual(result['config']['roles']['secondary_executor']['network_access'],'offline')
        cfg['roles']['secondary_executor']['network_access']='bypass_tls'
        with self.assertRaisesRegex(ValueError,'network_access'):self.load(cfg)
        unchanged=self.load(configuration())
        self.assertNotIn('network_access',unchanged['config']['roles']['secondary_executor'])

    def test_workflow_unknown_fields_and_invalid_shapes_are_rejected(self):
        for flow in ([], True, 'workflow', {**FLOW, 'reset_review_by_filename': True},
                     {**FLOW, 'max_review_rounds': 2}):
            with self.subTest(flow=flow):
                cfg = configuration(); cfg['workflow_v1'] = flow
                with self.assertRaises(ValueError):
                    self.load(cfg)

    def test_canonical_and_native_require_actual_booleans(self):
        for field in ('canonical_required', 'native_documents_required'):
            for value in (0, 1, None, 'true', [], {}):
                with self.subTest(field=field, value=value):
                    cfg = configuration(); cfg['workflow_v1'][field] = value
                    with self.assertRaises(ValueError):
                        self.load(cfg)
        for canonical, native in ((False, False), (True, False), (True, True)):
            cfg = configuration(); cfg['workflow_v1'].update(canonical_required=canonical,
                                                            native_documents_required=native)
            self.load(cfg)
        cfg = configuration(); cfg['workflow_v1']['canonical_required'] = False
        with self.assertRaisesRegex(ValueError, 'canonical'):
            self.load(cfg)

    def test_exactly_two_automatic_rounds_and_no_hard_timer(self):
        for value in (True, False, 1, 3, 2.0, '2', None):
            with self.subTest(rounds=value):
                cfg = configuration(); cfg['workflow_v1']['automatic_review_rounds'] = value
                with self.assertRaises(ValueError):
                    self.load(cfg)
        for value in (0, 1, True, False, 'null', [], {}):
            with self.subTest(timer=value):
                cfg = configuration(); cfg['workflow_v1']['hard_timeout_minutes'] = value
                with self.assertRaises(ValueError):
                    self.load(cfg)
        self.load(configuration())

    def test_business_two_rounds_does_not_replace_provider_call_permissions(self):
        cfg = configuration()
        cfg['roles']['secondary_executor']['max_review_rounds'] = None
        cfg['roles']['secondary_executor']['max_calls'] = None
        cfg['roles']['secondary_executor']['max_search_queries'] = None
        result = self.load(cfg)['config']
        self.assertEqual(result['workflow_v1']['automatic_review_rounds'], 2)
        for field in ('max_review_rounds', 'max_calls', 'max_search_queries'):
            self.assertIsNone(result['roles']['secondary_executor'][field])

    def test_profile_validate_works_at_root_and_outside_by_absolute_path(self):
        write_json(self.config, configuration())
        original_template = (KIT / 'templates/run-profile.json').read_bytes()
        original_manifest = (KIT / 'MANIFEST_SHA256.json').read_bytes()
        env = dict(os.environ); env.pop('PYTHONPATH', None)
        for cwd in (KIT, self.base):
            with self.subTest(cwd=cwd):
                run = subprocess.run([sys.executable, '-B', str(HERE / 'toolkit.py'),
                    'profile-validate', '--config', str(self.config)], cwd=cwd,
                    env=env, text=True, capture_output=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(json.loads(run.stdout)['config']['workflow_v1'], FLOW)
        self.assertEqual((KIT / 'templates/run-profile.json').read_bytes(), original_template)
        self.assertEqual((KIT / 'MANIFEST_SHA256.json').read_bytes(), original_manifest)


class FrozenWorkflowEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='workflow_profile_frozen_')
        self.base = Path(self.tmp.name)
        self.workspace = self.base / 'instance'
        self.config = self.base / 'external-profile.json'
        self.request = self.base / 'business.json'
        self.before_template = (KIT / 'templates/run-profile.json').read_bytes()
        self.before_manifest = (KIT / 'MANIFEST_SHA256.json').read_bytes()
        # This suite cannot trust a source tree whose final freeze is unfinished.
        self.cli('verify')
        shutil.copy2(KIT / 'templates/run-profile.json', self.config)
        write_json(self.config, configuration())
        self.cli('profile-validate', '--config', str(self.config))
        self.cli('init', '--workspace', str(self.workspace), '--authority',
                 'Explicit isolated workflow launcher test', '--config', str(self.config))

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args, expected=0, cwd=None):
        env = dict(os.environ); env.pop('PYTHONPATH', None)
        run = subprocess.run([sys.executable, '-B', str(HERE / 'toolkit.py'), *args],
            cwd=cwd or self.base, env=env, text=True, capture_output=True)
        self.assertEqual(run.returncode, expected,
                         'cwd=' + str(cwd or self.base) + '\nargv=' + repr(run.args) +
                         '\nstdout=' + run.stdout + '\nstderr=' + run.stderr)
        return json.loads(run.stdout if expected == 0 else run.stderr)

    def ctl(self, *args, expected=0, cwd=None):
        return self.cli('ctl', '--workspace', str(self.workspace), '--mode', 'test',
                        '--', *args, expected=expected, cwd=cwd)

    def create(self, spec=None, expected=0):
        write_json(self.request, task_spec() if spec is None else spec)
        return self.cli('create-task', '--workspace', str(self.workspace), '--mode', 'test',
                        '--file', str(self.request), expected=expected)

    def test_external_copy_import_preserves_package_verify_and_template(self):
        cfg = configuration('workflow-next-profile')
        cfg['workflow_v1']['native_documents_required'] = False
        write_json(self.config, cfg)
        self.cli('profile-validate', '--config', str(self.config), cwd=KIT)
        self.cli('profile-import', '--workspace', str(self.workspace), '--config', str(self.config))
        for cwd in (KIT, self.base):
            active = self.cli('profile-show', '--workspace', str(self.workspace), cwd=cwd)
            self.assertEqual(active['config']['workflow_v1']['native_documents_required'], False)
            self.assertTrue(self.cli('verify', cwd=cwd)['passed'])
        self.assertEqual((KIT / 'templates/run-profile.json').read_bytes(), self.before_template)
        self.assertEqual((KIT / 'MANIFEST_SHA256.json').read_bytes(), self.before_manifest)

    def test_create_task_applies_profile_and_blocks_raw_or_flag_bypass(self):
        created = self.create()['task']
        self.assertTrue(created['spec']['canonical_workflow_v1'])
        self.assertTrue(created['spec']['native_document_workflow_v1'])
        self.assertEqual(created['spec']['run_profile_snapshot']['config']['workflow_v1'], FLOW)
        for field in ('canonical_workflow_v1', 'native_document_workflow_v1'):
            bad = task_spec(); bad[field] = False
            self.assertIn('bypass', self.create(bad, expected=2)['error'])
        bad = task_spec(); bad.pop('review_cycle_contract')
        self.assertIn('review_cycle_contract', self.create(bad, expected=2)['error'])
        write_json(self.request, task_spec())
        self.assertIn('create-task', self.ctl('create', '--file', str(self.request), expected=2)['error'])
        self.assertEqual(len(self.ctl('list')), 1)

    def test_contract_validation_and_source_inventory_commands_use_unified_ctl(self):
        bad = task_spec(); bad['review_cycle_contract']['source_read_allowed'] = False
        self.assertIn('read', self.create(bad, expected=2)['error'])
        task = self.create()['task']['task_id']
        contract = self.ctl('handoff-contract', 'review')
        self.assertIsInstance(contract, dict)
        self.assertTrue(contract)
        source_path = self.base / 'source.json'
        route = 'Synthetic route'; school = 'Synthetic school'; version = 'fixture-v1'
        credit = {'value': 4, 'unit': 'fixture credit', 'status': 'known',
                  'original': '4 credits', 'reason': None}
        item = {'id': 'R01', 'school': school, 'route_ids': [route],
                'applicable_version': version, 'kind': 'course', 'code': 'SYN101',
                'name_original': 'Synthetic chemistry', 'name_zh': '示例化学',
                'credits': credit, 'category': 'Core', 'requirement': 'required',
                'group_ids': [], 'conditions': [],
                'sources': [{'occurrence_id': 'R01', 'location': 'rows.R01'}],
                'translation_note': '', 'semantic_history': []}
        draft = {'schema_version': 'canonical_draft_v1', 'school': school,
                 'scope_id': 'SYNTHETIC-SCOPE', 'version': version,
                 'items': [item], 'groups': [], 'history': []}
        write_json(source_path, draft)
        source = self.ctl('source', task, '--file', str(source_path), '--name', 'source.json')
        stage = {'status': 'complete', 'scope': 'One synthetic fixture only', 'pending_reason': None}
        inv = {'schema_version': 'canonical_inventory_v1', 'school': school,
               'scope_id': 'SYNTHETIC-SCOPE', 'sources': [{'source_id': 'SRC',
                   'artifact_id': source['artifact_id'], 'original_path': str(source_path),
                   'sha256': source['hash'], 'version': version, 'routes': [route],
                   'collection': copy.deepcopy(stage), 'reading': copy.deepcopy(stage),
                   'parse_complete': True, 'expected_occurrence_ids': ['R01']}],
               'occurrences': [{'occurrence_id': 'R01', 'source_id': 'SRC',
                   'location': 'rows.R01', 'kind': 'course',
                   'identity': {'school': school, 'code': 'SYN101',
                                'name_original': 'Synthetic chemistry', 'version': version},
                   'route': route, 'required': True,
                   'facts': {k: item[k] for k in ('credits', 'requirement', 'conditions',
                                                'group_ids', 'category', 'name_zh')}}],
               'mappings': [{'occurrence_id': 'R01', 'status': 'included',
                   'entity_id': 'R01', 'route_ids': [route], 'reason': None, 'evidence': []}]}
        inv_path = self.base / 'inventory.json'; write_json(inv_path, inv)
        registered = self.ctl('canonical-inventory', task, '--file', str(inv_path))
        self.assertEqual(registered['coverage']['mapping_rate'], 1)
        self.ctl('canonical-freeze', task, '--file', str(source_path))
        views_path = self.base / 'views.json'; write_json(views_path, {})
        views = self.ctl('canonical-views', task, '--file', str(views_path))
        self.assertEqual(set(views['views']), {'full', 'simple'})
        inspected = self.ctl('canonical-check', task)
        self.assertTrue(inspected['passed'])
        reads_path = self.base / 'reads.json'
        write_json(reads_path, [{'artifact_id': source['artifact_id'], 'read_allowed': True,
                               'required': True, 'purpose': 'One exact synthetic original'}])
        self.ctl('review-inputs', task, '--file', str(reads_path))
        cycle = self.ctl('review-cycle', task)
        self.assertEqual(cycle['unit_id'], 'SYNTHETIC-SCHOOL')
        self.assertEqual(cycle['rounds'], [])
        self.assertTrue(self.cli('verify')['passed'])


if __name__ == '__main__':
    pure = '--pure' in sys.argv
    if pure:
        sys.argv.remove('--pure')
    unittest.main(defaultTest='PureWorkflowProfileTests' if pure else None, verbosity=2)
