"""End-to-end native receipt protocol simulation; DOES NOT test Word/WPS GUI.

PDFs below are synthetic blank pages. All observations are explicitly simulated
and can enter only Cluster(mode='test'). Actual native export/page inspection is
a separate acceptance step and may not be inferred from this suite passing.
"""
import copy
import io
import json
from pathlib import Path
import shutil
import sys
import unittest

KIT = Path(__file__).resolve().parents[1]
RUNTIME = KIT / 'assets' / 'runtime'
for directory in (RUNTIME, RUNTIME / 'tests'):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

import test_workflow_closure as workflow_helpers
import test_canonical as canonical_helpers
import test_review_cycle as review_helpers
from cluster.core import GuardError, js, digest, now
from cluster.document_stage import write_view_docx
from cluster.release_checks import build_manifest
from pypdf import PdfWriter


CHECKS = ('fonts', 'widths', 'clipping', 'blank_pages', 'page_numbers', 'footnotes', 'group_boundaries')


class NativeDocumentStageSimulation(unittest.TestCase):
    def setUp(self):
        # Imported as a module rather than TestCase alias: its tests are not
        # accidentally collected again as part of this separate runner.
        self.helper = workflow_helpers.WorkflowClosureTest(methodName='runTest')
        self.helper.setUp()
        self.c, self.root = self.helper.c, self.helper.root
        self.task, self.candidate, self.content = self.helper.ready()
        self.helper.independent_pass(self.task, self.candidate, self.content)
        self.c.freeze_content(self.task)
        self.directory = self.c._output_directory(self.task, True)

    def tearDown(self):
        self.helper.tearDown()

    def evidence(self, name, body, inputs=None):
        with self.c._mutate(self.task) as m:
            a = self.c._artifact(m, js(body).encode(), 'evidence', 'codex', name, inputs or [])
        return a['artifact_id']

    def register(self, variant, widths=None, key=None):
        key = key or variant
        request = self.c.request_document_export(self.task, {'key': key, 'view': variant,
            'purpose': 'final', 'title': '合成协议课程清单'})
        m = self.c.get(self.task)
        view = json.loads(self.c.read_artifact(m, m['canonical']['views'][variant]['artifact_id']))
        path = write_view_docx(view, self.directory / (key + '.docx'), '合成协议课程清单', widths)
        return self.c.register_document(self.task, {'request_id': request['request_id'], 'docx_path': str(path)})

    def receipt(self, record, revision='initial', pages=2):
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=842, height=595)
        writer.add_metadata({'/Title': 'SIMULATED PROTOCOL PDF; NO GUI', '/Subject': revision})
        stream = io.BytesIO(); writer.write(stream)
        pdf = self.directory / (record['key'] + '.pdf'); pdf.write_bytes(stream.getvalue())
        pdf_sha = digest(pdf.read_bytes())
        native = self.evidence('sim-native-' + record['key'] + '-' + revision + '.json', {
            'kind': 'simulated', 'actor': 'codex', 'application': 'Word',
            'docx_sha256': record['docx_sha256'], 'pdf_sha256': pdf_sha,
            'native_save_and_export_observed': True, 'observed_at': now(),
            'raw_ui_text': 'SIMULATED protocol fixture; no Word/WPS process or GUI was opened.'},
            [record['docx_artifact']])
        checks = []
        for page in range(1, pages + 1):
            artifact = self.evidence('sim-page-' + record['key'] + '-' + revision + '-' + str(page) + '.json', {
                'kind': 'simulated', 'actor': 'codex', 'page': page, 'pdf_sha256': pdf_sha,
                'observed_at': now(), 'visual_observation': 'SIMULATED receipt; blank test PDF is not a visual acceptance result.'})
            checks.append({'page': page, 'evidence_artifact': artifact,
                           'checks': {key: True for key in CHECKS}})
        return {'key': record['key'], 'application': 'Word', 'actor': 'codex', 'kind': 'simulated',
                'docx_sha256': record['docx_sha256'], 'pdf_path': str(pdf),
                'native_evidence': native, 'pages': checks}

    def accepted_documents(self):
        result = {}
        for variant in ('full', 'simple'):
            record = self.register(variant)
            result[variant] = self.c.record_native_document_qa(self.task, self.receipt(record))
            self.assertTrue(result[variant]['native_export_succeeded'])
            export = next(item for item in self.c.document_stage_status(self.task)['exports']
                          if item['request_id'] == result[variant]['request_id'])
            self.assertTrue(export['native_export_succeeded'])
        self.c._document_stage_gate(self.c.get(self.task))
        return result

    def test_T20_semantic_review_never_implies_native_visual_acceptance(self):
        for variant in ('full', 'simple'):
            rec = self.register(variant)
            self.assertEqual(rec['visual_status'], 'not_verified')
        with self.assertRaisesRegex(GuardError, 'not verified'):
            self.c._accepted_gate(self.c.get(self.task))
        self.assertEqual(len(self.c.review_cycle_status(self.task)['rounds']), 1)

    def test_T20_simulated_receipts_must_not_claim_real_GUI(self):
        record = self.register('full'); receipt = self.receipt(record)
        receipt['kind'] = 'observed_gui'
        with self.assertRaisesRegex(GuardError, 'Simulation cannot enter'):
            self.c.record_native_document_qa(self.task, receipt)
        self.assertEqual(self.c.document_stage_status(self.task)['documents']['full']['visual_status'], 'not_verified')

    def test_T20_omitted_page_or_failed_check_blocks_receipt(self):
        record = self.register('full'); receipt = self.receipt(record)
        bad = copy.deepcopy(receipt); bad['pages'].pop()
        with self.assertRaisesRegex(GuardError, 'Every final PDF page'):
            self.c.record_native_document_qa(self.task, bad)
        bad = copy.deepcopy(receipt); bad['pages'][0]['checks']['group_boundaries'] = False
        with self.assertRaisesRegex(GuardError, 'incomplete or failed'):
            self.c.record_native_document_qa(self.task, bad)
        bad = copy.deepcopy(receipt); bad['pages'][0]['checks'].pop('clipping')
        with self.assertRaisesRegex(GuardError, 'incomplete or failed'):
            self.c.record_native_document_qa(self.task, bad)
        self.c.record_native_document_qa(self.task, receipt)

    def test_T24_wrong_hash_or_stale_observation_blocks_receipt(self):
        record = self.register('full'); receipt = self.receipt(record)
        bad = copy.deepcopy(receipt); bad['docx_sha256'] = '0' * 64
        with self.assertRaisesRegex(GuardError, 'another DOCX'):
            self.c.record_native_document_qa(self.task, bad)
        path = Path(receipt['pdf_path']); original = path.read_bytes()
        writer = PdfWriter(); writer.add_blank_page(width=842, height=595)
        stream = io.BytesIO(); writer.write(stream); path.write_bytes(stream.getvalue())
        with self.assertRaisesRegex(GuardError, 'Every final PDF page|stale'):
            self.c.record_native_document_qa(self.task, receipt)
        path.write_bytes(original)
        self.c.record_native_document_qa(self.task, receipt)

    def test_T18_one_layout_change_only_rechecks_affected_current_file(self):
        initial = self.accepted_documents()
        # Twenty separately registered synthetic files share the two canonical
        # views. This is a protocol-scale regression, not twenty real GUI exports.
        for number in range(1, 19):
            variant = 'full' if number % 2 else 'simple'
            key = variant + '-extra-' + str(number).zfill(2)
            record = self.register(variant, key=key)
            initial[key] = self.c.record_native_document_qa(self.task, self.receipt(record))
        self.c._document_stage_gate(self.c.get(self.task))
        others_before = copy.deepcopy({key: record for key, record in
            self.c.get(self.task)['native_documents'].items() if key != 'full'})
        self.assertEqual(len(others_before), 19)
        exports_before = len(self.c.document_stage_status(self.task)['exports'])
        m = self.c.get(self.task)
        view = json.loads(self.c.read_artifact(m, m['canonical']['views']['full']['artifact_id']))
        widths = {'items': [2200] + [1500] * (len(view['tables'][0]['columns']) - 1)}
        write_view_docx(view, self.directory / 'full.docx', '合成协议课程清单', widths)
        with self.assertRaisesRegex(GuardError, 'changed'):
            self.c._document_stage_gate(self.c.get(self.task))
        changed = self.register('full', widths)
        self.assertTrue(changed['inherited_semantics'])
        self.assertNotEqual(changed['docx_sha256'], initial['full']['docx_sha256'])
        with self.assertRaisesRegex(GuardError, 'not verified'):
            self.c._document_stage_gate(self.c.get(self.task))
        revised = self.c.record_native_document_qa(self.task, self.receipt(changed, 'width-change'))
        self.c._accepted_gate(self.c.get(self.task))
        self.assertNotEqual(revised['pdf_sha256'], initial['full']['pdf_sha256'])
        others_after = {key: record for key, record in
            self.c.get(self.task)['native_documents'].items() if key != 'full'}
        self.assertEqual(others_after, others_before)
        self.assertEqual(len(self.c.document_stage_status(self.task)['exports']), exports_before + 1)
        self.assertEqual(len(self.c.review_cycle_status(self.task)['rounds']), 1)

    def test_T24_changed_DOCX_PDF_and_frozen_artifacts_fail_current_gate(self):
        records = self.accepted_documents()
        for key in ('docx_path', 'pdf_path'):
            path = Path(records['full'][key]); data = path.read_bytes()
            path.write_bytes(data + b'CHANGED AFTER QA')
            with self.subTest(key=key), self.assertRaisesRegex(GuardError, 'changed'):
                self.c._document_stage_gate(self.c.get(self.task))
            path.write_bytes(data)
        m = self.c.get(self.task)
        a = self.c.artifact(m, records['full']['qa_artifact'])
        frozen = self.c.task_path(self.task) / a['filename']; original = frozen.read_bytes()
        frozen.write_bytes(original + b'CHANGED RECEIPT')
        with self.assertRaisesRegex(GuardError, 'hash changed'):
            self.c._document_stage_gate(self.c.get(self.task))
        frozen.write_bytes(original)
        self.c._accepted_gate(self.c.get(self.task))

    def test_T18_T24_freeze_final_files_release_delivery_and_audit(self):
        records = self.accepted_documents()
        # Native-stage outputs are produced only after semantic content freeze;
        # this exercises the unified controller's real post-content file binding.
        paths = [Path(records[name][field]) for name in ('full', 'simple') for field in ('docx_path', 'pdf_path')]
        files = self.c.freeze_candidate_files(self.task, paths)
        validation = self.c.validate(self.task)
        self.assertTrue(validation['passed'])
        self.c.finalize(self.task)
        release = self.root / 'release'; release.mkdir()
        for path in paths:
            shutil.copy2(path, release / path.name)
        (release / 'MANIFEST_SHA256.json').write_text(js(build_manifest(release)))
        registered = self.c.register_release(self.task, release)
        m = self.c.get(self.task)
        bindings = [{'output_path': rec['logical_filename'], 'output_sha256': rec['sha256'],
                     'input_task_id': self.task, 'input_artifact_id': rec['artifact_id'],
                     'input_sha256': rec['sha256'], 'acceptance_id': m['review'],
                     'transform': {'type': 'identity_v1', 'version': 1}} for rec in files]
        self.c.bind_release(self.task, registered['release_id'], bindings)
        self.assertTrue(self.c.deliver(self.task).is_file())
        self.assertEqual(self.c.get(self.task)['status'], 'DELIVERED')
        self.assertTrue(self.c.audit(self.task)['passed'])
        # Publishing authority is bound to current released bytes, too.
        path = release / 'full.pdf'; original = path.read_bytes(); path.write_bytes(original + b'RELEASE CHANGED')
        with self.assertRaises(GuardError):
            self.c._release_delivery_gate(self.c.get(self.task))

    def test_T16_T20_ten_units_eight_content_seven_simulated_native_two_waiting(self):
        """Count real controller outcomes without promoting a whole batch.

        Eight canonical units receive complete independent content reports. Of
        those, seven receive explicitly simulated page receipts and the eighth
        remains awaiting visual inspection. Two separate units retain an old
        substantive issue after a second, genuinely changed, local review.
        """
        parent = self.task  # setUp already independently froze the first unit.
        content_tasks = [parent]
        for number in range(2, 9):
            unit = 'SYNTHETIC-BATCH-SCHOOL-' + str(number)
            spec = workflow_helpers.integrated_spec(unit=unit)
            spec['parent_task_id'] = parent
            task, inventory, draft = canonical_helpers.curriculum_fixture(
                self.c, school=unit, scope_id=unit + '-SCOPE', spec_changes=spec)
            self.c.register_inventory(task, inventory)
            self.c.freeze_canonical(task, draft)
            self.c.generate_canonical_views(task)
            self.c.register_review_inputs(task, [
                {'artifact_id': row['artifact_id'], 'read_allowed': True}
                for row in inventory['sources']])
            self.c.package(task)
            self.c.start_local(task)
            content = self.c.canonical_candidate(task)
            path = self.root / (unit + '-candidate.json')
            path.write_text(js(content))
            candidate = self.c.local_output(task, path)
            self.helper.independent_pass(task, candidate, content)
            self.c.freeze_content(task)
            content_tasks.append(task)

        original_task, original_directory = self.task, self.directory
        try:
            for task in content_tasks[:7]:
                self.task = task
                self.directory = self.c._output_directory(task, True)
                self.accepted_documents()
        finally:
            self.task, self.directory = original_task, original_directory

        # Reuse the established synthetic offline contract through public task
        # and review entry points; no database state is assigned to "waiting".
        reviewer = review_helpers.ReviewCycleTest(methodName='runTest')
        reviewer.c, reviewer.root = self.c, self.root
        reviewer.source = self.root / 'batch-original.txt'
        reviewer.source.write_text('READY original synthetic evidence')
        waiting_tasks = []
        for number in (9, 10):
            task = self.c.create(reviewer.offline_spec(
                parent_task_id=parent,
                review_cycle_contract=review_helpers.contract(
                    unit='SYNTHETIC-BATCH-SCHOOL-' + str(number))))['task_id']
            sources = [self.c.add_source(task, reviewer.source, name)
                       for name in ('courses.csv', 'policy.md')]
            self.c.register_review_inputs(task, [
                {'artifact_id': source['artifact_id'], 'read_allowed': True}
                for source in sources])
            self.c.package(task)
            candidate, content = reviewer.candidate(task)
            reviewer.round(task, candidate, content, {'OLD': 'OLD-GAP-' + str(number)})
            candidate, content = reviewer.revise(
                task, content, {'R01': 'READY consolidated local correction ' + str(number)})
            reviewer.round(task, candidate, content, scope=['R01'])
            self.assertTrue(self.c.review_cycle_status(task)['waiting_user'])
            with self.assertRaisesRegex(GuardError, 'OLD-GAP'):
                self.c.freeze_review_content(task)
            waiting_tasks.append(task)

        parent_before = self.c.get(parent)['status']
        batch = self.c.review_batch_status(parent)
        self.assertEqual(batch['total_units'], 10)
        self.assertEqual(batch['content_complete'], 8)
        self.assertEqual(batch['native_pages_complete'], 7)
        self.assertEqual(batch['waiting_user'], 2)
        self.assertEqual(batch['parent_status_unchanged'], parent_before)
        self.assertEqual(self.c.get(parent)['status'], parent_before)
        self.assertFalse(batch['automatic_parent_delivery'])
        self.assertNotEqual(parent_before, 'DELIVERED')
        by_task = {unit['task_id']: unit for unit in batch['units']}
        for task in content_tasks:
            self.assertTrue(by_task[task]['content_complete'])
        self.assertFalse(by_task[content_tasks[-1]]['native_pages_complete'])
        for task in waiting_tasks:
            self.assertFalse(by_task[task]['content_complete'])
            self.assertFalse(by_task[task]['native_pages_complete'])
            self.assertTrue(by_task[task]['open_issue_ids'])
            with self.assertRaisesRegex(GuardError, 'No changed or unresolved scope'):
                self.c.package(task, 'reviewer')
            self.assertEqual(len(self.c.review_cycle_status(task)['rounds']), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
