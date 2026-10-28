"""A02/A03 launcher regressions; isolated local files only, no provider calls."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
KIT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('closeout_toolkit_launcher', KIT / 'scripts/toolkit.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class LauncherCloseoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='launcher_closeout_')
        self.base = Path(self.tmp.name)
        self.env = dict(os.environ)
        self.env.pop('PYTHONPATH', None)
        self.env.pop('PYTHONDONTWRITEBYTECODE', None)

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args, kit=KIT, expected=0):
        result = subprocess.run([sys.executable, '-B', '-S', str(kit / 'scripts/toolkit.py'), *args],
                                cwd=self.base, env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, expected, result.stderr)
        return json.loads(result.stdout if result.returncode == 0 else result.stderr)

    def test_core_self_test_subprocess_imports_without_user_pythonpath(self):
        outer = self
        class ChildImport(unittest.TestCase):
            def runTest(self):
                result = subprocess.run([sys.executable, '-S', '-c',
                    'import cluster,json,sys; print(json.dumps([cluster.__file__,sys.dont_write_bytecode]))'], cwd=outer.base,
                    text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                child_file, child_no_bytecode = json.loads(result.stdout)
                self.assertTrue(child_no_bytecode)
                self.assertFalse(list(tool.RUNTIME.rglob('__pycache__')))
                self.assertEqual(Path(child_file).resolve(),
                                 (tool.RUNTIME / 'cluster/__init__.py').resolve())
        original_path = list(sys.path)
        with patch.dict(os.environ, self.env, clear=True), \
             patch.object(tool, 'verify_runtime'), \
             patch.object(tool, 'freeze_expected_tests', return_value=1), \
             patch('unittest.defaultTestLoader.discover', return_value=unittest.TestSuite([ChildImport()])):
            self.assertTrue(tool.core_self_test()['passed'])
            self.assertNotIn('PYTHONPATH', os.environ)
            self.assertNotIn('PYTHONDONTWRITEBYTECODE', os.environ)
            self.assertEqual(sys.path, original_path)

    def test_import_scope_preserves_existing_pythonpath_and_restores_on_failure(self):
        sentinel = str(self.base / 'existing_pythonpath')
        original_path = list(sys.path)
        with patch.dict(os.environ, {'PYTHONPATH': sentinel, 'PYTHONDONTWRITEBYTECODE': 'old-value'}):
            with self.assertRaisesRegex(RuntimeError, 'intentional regression failure'):
                with tool.self_test_import_scope():
                    self.assertEqual(os.environ['PYTHONPATH'].split(os.pathsep)[-1], sentinel)
                    self.assertEqual(os.environ['PYTHONDONTWRITEBYTECODE'], '1')
                    raise RuntimeError('intentional regression failure')
            self.assertEqual(os.environ['PYTHONPATH'], sentinel)
            self.assertEqual(os.environ['PYTHONDONTWRITEBYTECODE'], 'old-value')
            self.assertEqual(sys.path, original_path)

    def test_external_config_init_and_import_preserve_frozen_template_and_manifest(self):
        before = tool.sha(KIT / 'templates/run-profile.json')
        manifest = tool.sha(KIT / 'MANIFEST_SHA256.json')
        self.assertTrue(self.cli('verify')['passed'])
        config = self.base / 'run-profile.json'
        shutil.copy2(KIT / 'templates/run-profile.json', config)
        data = json.loads(config.read_text())
        data['run_id'] = 'outside-profile-initial'
        config.write_text(json.dumps(data))
        self.cli('profile-validate', '--config', str(config))
        workspace = self.base / 'instance'
        self.cli('init', '--workspace', str(workspace), '--authority', 'isolated local test', '--config', str(config))
        data['run_id'] = 'outside-profile-next'
        config.write_text(json.dumps(data))
        self.cli('profile-validate', '--config', str(config))
        self.cli('profile-import', '--workspace', str(workspace), '--config', str(config))
        active = self.cli('profile-show', '--workspace', str(workspace))
        self.assertEqual(active['config']['run_id'], 'outside-profile-next')
        self.assertEqual(tool.sha(KIT / 'templates/run-profile.json'), before)
        self.assertEqual(tool.sha(KIT / 'MANIFEST_SHA256.json'), manifest)
        self.assertTrue(self.cli('verify')['passed'])

    def test_edited_frozen_template_is_rejected_without_manifest_rebuild(self):
        copy = self.base / 'changed-toolkit'
        shutil.copytree(KIT, copy)
        manifest_before = tool.sha(copy / 'MANIFEST_SHA256.json')
        template = copy / 'templates/run-profile.json'
        data = json.loads(template.read_text())
        data['run_id'] = 'wrongly-edited-in-place'
        template.write_text(json.dumps(data))
        error = self.cli('verify', kit=copy, expected=2)
        self.assertIn('Toolkit hash mismatch: templates/run-profile.json', error['error'])
        self.assertEqual(tool.sha(copy / 'MANIFEST_SHA256.json'), manifest_before)
        self.assertTrue(self.cli('verify')['passed'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
