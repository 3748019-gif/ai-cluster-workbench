"""Controller-owned output paths survive compact prompts without migrating tickets."""
import copy
import json
import unittest
from pathlib import Path

from test_core import CoreTest,spec
from cluster.core import Cluster,GuardError,digest
from cluster.permissions import prompt


class AsciiOutputDirectoryTest(unittest.TestCase):
    setUp=CoreTest.setUp
    tearDown=CoreTest.tearDown
    local_candidate=CoreTest.local_candidate

    def task(self,external=True,review=False,style='ascii_short'):
        settings=spec(external,review)
        if style:settings['transport_prompt_style']=style
        task=self.c.create(settings)['task_id'];self.c.add_source(task,self.source)
        package=self.c.package(task)
        return task,package

    def test_new_producer_and_reviewer_tickets_freeze_actual_package_output_directory(self):
        for phase in ('producer','reviewer'):
            with self.subTest(phase=phase):
                task,package=self.task(external=phase=='producer',review=phase=='reviewer')
                if phase=='reviewer':
                    self.local_candidate(task);package=self.c.package(task,phase)
                directory=str(self.root.resolve()/'exchange'/task)
                self.assertEqual(package['output_directory'],directory)
                text=self.c.read_artifact(self.c.get(task),package['artifact_id']).decode()
                self.assertIn('本地授权输出目录: '+directory,text)
                submission=self.c.prepare_submission(task,phase)
                self.assertEqual(submission['output_directory'],directory)
                self.assertIn('Output directory '+json.dumps(directory),submission['prompt'])
                self.assertTrue(submission['prompt'].isascii());self.assertNotIn('\n',submission['prompt'])
                self.assertEqual(submission['prompt_hash'],digest(submission['prompt'].encode()))
                self.assertEqual(self.c.prepare_submission(task,phase),submission)
                self.assertEqual(self.c.compact_prepared_prompt(task,submission['submission_id'])['prompt'],submission['prompt'])

    def test_ordinary_prompt_and_preexisting_ascii_ticket_are_unchanged(self):
        task,_=self.task(style=None);submission=self.c.prepare_submission(task,'producer')
        expected=prompt(self.c.get(task),submission['submission_id'],submission['alias'],'producer')
        self.assertEqual(submission['prompt'],expected);self.assertNotIn('Output directory',submission['prompt'])
        task,_=self.task();submission=self.c.prepare_submission(task,'producer')
        # An isolated fixture of a ticket frozen before this feature; real
        # historical tickets, aliases, hashes and events are never rewritten.
        with self.c._mutate(task) as state:
            stored=state['submissions'][-1];stored.pop('output_directory')
            stored['prompt']=prompt(state,stored['submission_id'],stored['alias'],'producer')
            stored['prompt_hash']=digest(stored['prompt'].encode())
        old=copy.deepcopy(self.c.get(task)['submissions'][-1])
        self.assertEqual(self.c.prepare_submission(task,'producer'),old)
        self.assertEqual(self.c.compact_prepared_prompt(task,old['submission_id'])['prompt'],old['prompt'])

    def test_old_package_without_output_metadata_keeps_ascii_compatibility(self):
        task,package=self.task()
        with self.c._mutate(task) as state:
            self.c.artifact(state,package['artifact_id']).pop('output_directory')
        submission=self.c.prepare_submission(task,'producer')
        self.assertNotIn('output_directory',submission)
        self.assertNotIn('Output directory',submission['prompt'])

    def test_ascii_path_space_is_preserved_but_relative_control_unicode_and_colon_reject(self):
        state={'task_id':'TASK-20261001-9999','spec':{'transport_prompt_style':'ascii_short'}}
        value=prompt(state,'TASK-20261001-9999-SUB-001','/tmp/alias.txt','producer',output_directory='/tmp/output with spaces')
        self.assertIn('Output directory "/tmp/output with spaces"',value)
        for path in ('relative/exchange','/tmp/../escape','/tmp/line\nbreak','/tmp/tab\tpath','/tmp/数据','/tmp/colon:path'):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):prompt(state,'TASK-20261001-9999-SUB-001','/tmp/alias.txt','producer',output_directory=path)

    def test_symlink_and_wrong_frozen_directory_reject_before_submission(self):
        task,package=self.task()
        with self.c._mutate(task) as state:self.c.artifact(state,package['artifact_id'])['output_directory']='/tmp/unassigned-exchange'
        with self.assertRaises(GuardError):self.c.prepare_submission(task,'producer')
        self.assertFalse(self.c.get(task)['submissions'])
        task,_=self.task();directory=self.root/'exchange'/task;directory.rmdir()
        other=self.root/'wrong-workspace';other.mkdir();directory.symlink_to(other,target_is_directory=True)
        with self.assertRaises(GuardError):self.c.prepare_submission(task,'producer')
        self.assertFalse(self.c.get(task)['submissions'])


del CoreTest

if __name__=='__main__':unittest.main()
