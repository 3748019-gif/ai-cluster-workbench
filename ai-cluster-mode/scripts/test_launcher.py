"""Only new launcher boundaries; no provider, GUI or network calls."""
import hashlib
import importlib.util
import json
import tempfile
import sys
import unittest
import zipfile
from pathlib import Path

sys.dont_write_bytecode = True

WORK = Path(tempfile.gettempdir())
KIT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('frozen_toolkit_launcher', KIT / 'scripts/toolkit.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='launcher_', dir=WORK)
        self.base = Path(self.tmp.name)
        self.workspace = self.base / '独立 工作实例'

    def tearDown(self):
        self.tmp.cleanup()

    def initialize(self):
        return tool.init(self.workspace, 'User requested this isolated local launcher test')

    def test_fresh_state_and_configuration_bytes_are_not_inherited_history(self):
        result = self.initialize()
        self.assertTrue(result['empty_task_state'])
        self.assertEqual(tool.run_ctl(self.workspace, ['list']), [])
        for name in ['roles.json', 'budget_policy.json', 'controller_model.json', 'collaboration_policy.json']:
            self.assertEqual((self.workspace / '.cluster' / name).read_bytes(),
                             (KIT / 'assets/runtime/.cluster' / name).read_bytes())
        self.assertEqual(list((self.workspace / 'tasks').iterdir()), [])
        self.assertFalse((self.workspace / 'outputs/四校调研').exists())

    def test_reinit_cannot_overwrite_existing_instance_or_unrelated_data(self):
        self.initialize()
        existing = self.workspace / 'inputs/keep.txt'
        existing.write_text('keep evidence')
        database = (self.workspace / '.cluster/state.sqlite3').read_bytes()
        with self.assertRaises(ValueError):
            self.initialize()
        self.assertEqual(existing.read_text(), 'keep evidence')
        self.assertEqual((self.workspace / '.cluster/state.sqlite3').read_bytes(), database)

    def test_no_activation_authority_creates_no_workspace(self):
        with self.assertRaises(ValueError):
            tool.init(self.workspace, '')
        self.assertFalse(self.workspace.exists())

    def test_business_zip_preserves_original_and_has_readable_entry(self):
        self.initialize()
        source = self.base / 'business.zip'
        with zipfile.ZipFile(source, 'w') as z:
            z.writestr('任务/START_HERE.md', '请处理数据，未知保留。')
            z.writestr('任务/data.csv', 'name,value\na,1\nb,2\n')
        receipt = tool.import_business(self.workspace, source)
        self.assertEqual(Path(receipt['copied_original']).read_bytes(), source.read_bytes())
        self.assertEqual(len(receipt['entry_candidates']), 1)
        self.assertEqual(Path(receipt['entry_candidates'][0]).read_text(), '请处理数据，未知保留。')
        for row in receipt['files']:
            self.assertEqual(hashlib.sha256((self.workspace / row['path']).read_bytes()).hexdigest(), row['sha256'])
        self.assertFalse(receipt['task_registered'])
        self.assertEqual(tool.run_ctl(self.workspace, ['list']), [])

    def test_unsafe_archive_cannot_write_outside_import_or_follow_links(self):
        self.initialize()
        cases = {'traversal': ['../outside.txt'], 'absolute': ['/outside.txt'],
                 'dot_alias': ['dir/file.txt', 'dir/./file.txt'],
                 'case_collision': ['dir/File.txt', 'dir/file.txt'],
                 'file_parent': ['dir', 'dir/file.txt'], 'windows_drive': ['C:/outside.txt']}
        for label, names in cases.items():
            with self.subTest(label=label):
                source = self.base / (label + '.zip')
                with zipfile.ZipFile(source, 'w') as z:
                    for name in names:
                        z.writestr(name, 'unexpected')
                with self.assertRaises(ValueError):
                    tool.import_business(self.workspace, source)
                self.assertFalse((self.base / 'outside.txt').exists())
        source = self.base / 'link.zip'
        with zipfile.ZipFile(source, 'w') as z:
            info = zipfile.ZipInfo('link'); info.create_system = 3
            info.external_attr = (0o120777 << 16)
            z.writestr(info, '/outside.txt')
        with self.assertRaises(ValueError):
            tool.import_business(self.workspace, source)
        self.assertFalse(list(self.workspace.rglob('extracted')))

    def test_core_tamper_and_root_override_are_rejected_before_execution(self):
        self.initialize()
        with self.assertRaises(ValueError):
            tool.run_ctl(self.workspace, ['--root', str(self.base), 'list'])
        core = self.workspace / 'cluster/core.py'
        core.write_text('raise RuntimeError("should never execute")')
        with self.assertRaises(ValueError):
            tool.run_ctl(self.workspace, ['list'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
