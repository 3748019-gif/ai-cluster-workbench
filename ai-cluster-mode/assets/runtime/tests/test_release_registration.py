"""Release registration boundary tests, with isolated test-mode state only.

No real clients, network, GUI, or chemistry material are used here.  Simulated
submission evidence is limited to a temporary Cluster(..., 'test') root.
"""
import json
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cluster.core import Cluster, GuardError, digest, js, now
from cluster.release_checks import build_manifest


def task_spec(external=False):
    value = {
        'objective': 'Synthetic release registration boundary fixture',
        'task_type': 'document', 'risk_level': 'low',
        'rules': ['Keep exact registered inputs'],
        'acceptance': ['READY'], 'review_required': False,
        'validators': [{'kind': 'text', 'required': ['READY']}],
    }
    if external:
        value['external_benefit'] = {
            'provider': 'kimi', 'problem': 'Synthetic submission boundary test',
            'loss_without': 'No active submission gate coverage',
            'handoff_cost': 'Test fixture only', 'capability': 'second_analysis',
            'client_available': True, 'quota_available': True,
            'deterministic_alternative': False,
        }
    return value


class ReleaseRegistrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.c = Cluster(self.root, 'test')
        self.source = self.root / 'source.txt'
        self.source.write_text('READY synthetic authority', encoding='utf-8')
        self.release = self.root / 'release'
        self.release.mkdir()
        self.payload = self.release / 'payload.txt'
        self.payload.write_text('READY physical release bytes', encoding='utf-8')
        self.freeze_release_manifest()

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def freeze_release_manifest(self):
        (self.release / 'MANIFEST_SHA256.json').write_text(
            js(build_manifest(self.release)), encoding='utf-8')

    def task(self, external=False, parent=None):
        value = task_spec(external)
        m = self.c.new_work(parent, value) if parent else self.c.create(value)
        task = m['task_id']
        self.c.add_source(task, self.source)
        self.c.package(task)
        return task

    def registration_events(self, task):
        return [e for e in self.c.events(task) if e['event'] == 'RELEASE_REGISTERED']

    def release_checks(self, task):
        return [a for a in self.c.get(task)['artifacts']
                if a['logical_name'] == 'release_check.json']

    def local_output(self, task, passed=True):
        self.c.start_local(task)
        output = self.root / (task + '-output.txt')
        output.write_text(task + (' READY' if passed else ' NOT_VALID'), encoding='utf-8')
        result = self.c.local_output(task, output)
        self.c.validate(task)
        return result

    def delivered_child(self, parent):
        child = self.task(parent=parent)
        self.local_output(child)
        self.c.finalize(child)
        self.c.deliver(child)
        self.c.accept_work(parent, child)
        return child

    def begin_simulated_submission(self, task):
        submission = self.c.prepare_submission(task, 'producer')
        token = self.c.acquire_gui(task)
        proof = {
            'kind': 'simulated', 'task_id': task,
            'submission_id': submission['submission_id'],
            'provider': submission['provider'], 'stage': 'preflight',
            'observed_at': now(), 'window': 'Isolated synthetic client',
            'raw_text': submission['prompt'], 'input_text': submission['prompt'],
            'package_hash': submission['package_hash'],
            'unexpected_attachments': False, 'client_available': True,
            'quota_available': True, 'message_visible': True,
        }
        path = self.root / (task + '-preflight.json')
        path.write_text(js(proof), encoding='utf-8')
        self.c.begin_submission(task, submission['submission_id'], token, path)

    def test_T26_actual_tree_before_registration_and_idempotent_retry(self):
        task = self.task()
        result = self.c.register_release(task, self.release)
        self.assertEqual(result['status'], 'structurally_registered')
        self.assertEqual(result['assurance'], 'structure_only')
        self.assertFalse(result['delivery_eligible'])
        self.assertEqual(result['user_delivery'], 'not_yet_confirmed')
        self.assertEqual(self.c.register_release(task, self.release), result)
        self.assertEqual(len(self.registration_events(task)), 1)
        self.assertEqual(len(self.release_checks(task)), 1)
        evidence = json.loads(self.c.read_artifact(self.c.get(task), result['check_artifact']))
        self.assertTrue(evidence['passed'])

    def test_T26_changed_actual_tree_rejects_both_initial_and_existing_registration(self):
        task = self.task()
        original = self.payload.read_bytes()
        self.payload.write_bytes(b'changed after manifest freeze')
        with self.assertRaises(GuardError):
            self.c.register_release(task, self.release)
        self.assertFalse(self.c.get(task).get('releases'))
        self.assertFalse(self.registration_events(task))
        self.payload.write_bytes(original)
        result = self.c.register_release(task, self.release)
        self.payload.unlink()
        with self.assertRaises(GuardError):
            self.c.register_release(task, self.release)
        self.assertEqual(self.c.get(task)['releases'], [result])

    def test_T26_corrupt_manifest_rejects_and_repaired_neighbor_registers(self):
        task = self.task()
        manifest = self.release / 'MANIFEST_SHA256.json'
        original = manifest.read_bytes()
        manifest.write_text('{broken', encoding='utf-8')
        with self.assertRaises(GuardError):
            self.c.register_release(task, self.release)
        self.assertFalse(self.c.get(task).get('releases'))
        self.assertFalse(self.release_checks(task))
        manifest.write_bytes(original)
        self.assertEqual(self.c.register_release(task, self.release)['status'], 'structurally_registered')

    def test_T26_crash_after_commit_retry_has_one_registration(self):
        task = self.task()
        with patch.object(self.c, 'export', side_effect=RuntimeError('synthetic projection crash')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic projection crash'):
                self.c.register_release(task, self.release)
        prior = self.c.get(task)['releases'][0]
        self.assertEqual(self.c.register_release(task, self.release), prior)
        self.assertEqual(len(self.registration_events(task)), 1)
        self.assertEqual(len(self.release_checks(task)), 1)
        self.assertTrue(self.c.recover(task)['audit']['passed'])

    def test_T26_task_projection_corrupt_does_not_duplicate_registration(self):
        task = self.task()
        prior = self.c.register_release(task, self.release)
        projection = self.c.task_path(task) / 'manifest.json'
        projection.write_text('{broken', encoding='utf-8')
        self.assertEqual(self.c.register_release(task, self.release), prior)
        self.assertEqual(json.loads(projection.read_text(encoding='utf-8'))['task_id'], task)
        self.assertEqual(len(self.registration_events(task)), 1)

    def test_T26_changed_check_profile_keeps_distinct_check_evidence(self):
        task = self.task()
        page = self.release / 'guide.html'
        page.write_text('<html><body>' + 'READY readable synthetic content. ' * 10
                        + '</body></html>', encoding='utf-8')
        self.freeze_release_manifest()
        original = self.c.register_release(task, self.release)
        options = {'offline_html': {'guide.html': {'min_text_chars': 80}}}
        strict = self.c.register_release(task, self.release, options)
        self.assertNotEqual(original['check_artifact'], strict['check_artifact'])
        self.assertNotEqual(original['check_profile_sha256'], strict['check_profile_sha256'])
        report = json.loads(self.c.read_artifact(self.c.get(task), strict['check_artifact']))
        self.assertEqual(report['check_options'], options)
        html = next(x for x in report['classifications'] if x['path'] == 'guide.html')
        self.assertTrue(html['required_local'])
        self.assertEqual(self.c.register_release(task, self.release, options), strict)
        self.assertEqual(len(self.registration_events(task)), 2)

    def packaged_frozen_context(self):
        value = task_spec()
        value['review_required'] = True
        task = self.c.create(value)['task_id']
        self.c.add_source(task, self.source)
        self.c.package(task)
        self.c.start_local(task)
        output = self.root / (task + '-candidate.txt')
        output.write_text(task + ' READY', encoding='utf-8')
        self.c.local_output(task, output)
        context = self.root / 'exchange' / task / 'produced.txt'
        context.write_text('READY exact complete synthetic context', encoding='utf-8')
        frozen = self.c.freeze_candidate_files(task, [context])
        package = self.c.package(task, 'reviewer')
        return task, frozen[0], package

    def test_frozen_context_is_bound_to_exact_reviewer_package(self):
        task, frozen, package = self.packaged_frozen_context()
        self.assertIn(frozen['artifact_id'], package['input_artifacts'])
        submission = self.c.prepare_submission(task, 'reviewer')
        self.assertEqual(submission['package_id'], package['artifact_id'])

    def test_tampered_frozen_context_rejects_before_prepare(self):
        task, frozen, package = self.packaged_frozen_context()
        Path(frozen['frozen_path']).write_bytes(b'CHANGED exact context after packaging')
        with self.assertRaises(GuardError):
            self.c.prepare_submission(task, 'reviewer')
        self.assertFalse(self.c.get(task)['submissions'])

    def test_local_correction_freezes_new_files_and_preserves_old_version_bytes(self):
        task = self.task()
        self.c.start_local(task)
        candidate_path = self.root / (task + '-candidate.txt')
        candidate_path.write_text(task + ' NOT_VALID', encoding='utf-8')
        old_candidate = self.c.local_output(task, candidate_path)
        produced = self.c.root / 'exchange' / task / 'produced.txt'
        old_bytes = b'Old complete synthetic context'
        produced.write_bytes(old_bytes)
        old_files = self.c.freeze_candidate_files(task, [produced])
        self.assertFalse(self.c.validate(task)['passed'])
        self.c.begin_correction(task, 'Synthetic known validation correction')
        candidate_path.write_text(task + ' READY', encoding='utf-8')
        new_candidate = self.c.local_output(task, candidate_path)
        m = self.c.get(task)
        self.assertNotEqual(old_candidate['artifact_id'], new_candidate['artifact_id'])
        self.assertEqual(m['candidate_files'], [])
        self.assertEqual(m['candidate_file_sets'][old_candidate['artifact_id']], old_files)
        new_bytes = b'New corrected complete synthetic context'
        produced.write_bytes(new_bytes)
        new_files = self.c.freeze_candidate_files(task, [produced])
        self.assertEqual(new_files[0]['candidate_artifact_id'], new_candidate['artifact_id'])
        self.assertNotEqual(old_files[0]['artifact_id'], new_files[0]['artifact_id'])
        current = self.c.get(task)
        self.assertEqual(self.c.read_artifact(current, old_files[0]['artifact_id']), old_bytes)
        self.assertEqual(self.c.read_artifact(current, new_files[0]['artifact_id']), new_bytes)
        self.assertTrue(self.c.validate(task)['passed'])

    def test_active_submission_blocks_first_registration(self):
        task = self.task(external=True)
        self.begin_simulated_submission(task)
        with self.assertRaises(GuardError):
            self.c.register_release(task, self.release)
        self.assertFalse(self.c.get(task).get('releases'))

    def test_existing_release_does_not_bypass_active_submission(self):
        task = self.task(external=True)
        prior = self.c.register_release(task, self.release)
        self.begin_simulated_submission(task)
        with self.assertRaises(GuardError):
            self.c.register_release(task, self.release)
        self.assertEqual(self.c.get(task)['releases'], [prior])
        self.assertEqual(len(self.registration_events(task)), 1)

    def test_invalid_accepted_work_blocks_first_registration(self):
        task = self.task()
        child = self.delivered_child(task)
        final = self.c.artifact(self.c.get(child), self.c.get(child)['final_output'])
        (self.c.task_path(child) / final['filename']).write_bytes(b'changed accepted child')
        with self.assertRaises(GuardError):
            self.c.register_release(task, self.release)
        self.assertFalse(self.c.get(task).get('releases'))

    def test_existing_release_does_not_bypass_invalid_accepted_work(self):
        task = self.task()
        child = self.delivered_child(task)
        prior = self.c.register_release(task, self.release)
        final = self.c.artifact(self.c.get(child), self.c.get(child)['final_output'])
        (self.c.task_path(child) / final['filename']).write_bytes(b'changed accepted child')
        with self.assertRaises(GuardError):
            self.c.register_release(task, self.release)
        self.assertEqual(self.c.get(task)['releases'], [prior])

    def test_open_blocker_blocks_first_and_existing_registration(self):
        for already_registered in (False, True):
            with self.subTest(already_registered=already_registered):
                task = self.task()
                if already_registered:
                    self.c.register_release(task, self.release)
                self.local_output(task, passed=False)
                self.assertTrue(any(x['status'] == 'open' and x['blocking']
                                    for x in self.c.get(task)['open_issues']))
                with self.assertRaises(GuardError):
                    self.c.register_release(task, self.release)


class AttachmentReceiptBoundaryTest(unittest.TestCase):
    """Synthetic acquisition/consumption evidence, never actual Edge/client QA."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.c = Cluster(self.root, 'test')
        self.source = self.root / 'authority.txt'
        self.source.write_text('READY synthetic authority', encoding='utf-8')

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def task(self, external=False):
        task = self.c.create(task_spec(external))['task_id']
        self.c.add_source(task, self.source)
        return task

    def request(self, task, version='2026', requester='kimi', **fields):
        value = {'url': 'https://example.edu/synthetic/catalog', 'version': version,
                 'scope': 'Synthetic degree scope', 'expected_format': 'json',
                 'requester': requester, 'purpose': 'Isolated fixture only'}
        value.update(fields)
        return self.c.request_attachment(task, value)

    def synthetic_download(self, task):
        request = self.request(task)
        original = self.root / (task + '-original.json')
        original.write_text('{"synthetic_version":"2026"}', encoding='utf-8')
        observation = self.root / (task + '-synthetic-edge.txt')
        observation.write_text('Synthetic test-mode Edge observation; no browser used.', encoding='utf-8')
        result = self.c.attachment_result(task, request['request_id'], {
            'status': 'downloaded', 'path': str(original),
            'sha256': digest(original.read_bytes()),
            'edge_observation': {'kind': 'simulated', 'browser': 'Microsoft Edge',
                'completed': True, 'path': str(original.resolve()), 'observed_at': now(),
                'url': request['url'], 'version': request['version'],
                'evidence_path': str(observation),
                'evidence_sha256': digest(observation.read_bytes())},
        })
        return request, original, result

    def test_synthetic_download_then_second_member_reuses_exact_version(self):
        first = self.task()
        request, original, downloaded = self.synthetic_download(first)
        provenance = downloaded['current']['provenance']
        artifact = self.c.artifact(self.c.get(first), provenance['artifact_id'])
        self.assertEqual(artifact['acquisition_identity'], {
            key: request[key] for key in ('url', 'version', 'scope', 'expected_format')})
        self.assertEqual(self.c.read_artifact(self.c.get(first), artifact['artifact_id']), original.read_bytes())
        second = self.task()
        subscribed = self.request(second, requester='workbuddy')
        reused = self.c.attachment_result(second, subscribed['request_id'], {
            'status': 'reused', 'path': str(original), 'sha256': digest(original.read_bytes()),
            'provenance': provenance})
        self.assertEqual(reused['status'], 'reused')
        self.assertEqual(reused['current']['provenance'], provenance)
        self.assertFalse(reused['current']['fact_verified'])

    def test_wrong_year_reuse_rejected_without_mutating_request(self):
        first = self.task()
        request, original, downloaded = self.synthetic_download(first)
        second = self.task()
        wrong = self.request(second, version='2025', requester='workbuddy')
        with self.assertRaises(GuardError):
            self.c.attachment_result(second, wrong['request_id'], {
                'status': 'reused', 'path': str(original), 'sha256': digest(original.read_bytes()),
                'provenance': downloaded['current']['provenance']})
        item = self.c.get(second)['attachment_queue'][wrong['request_id']]
        self.assertEqual(item['status'], 'requested')
        self.assertEqual(item['attempts'], [])

    def test_old_unbound_original_not_silently_assigned_version(self):
        task = self.task()
        original = self.root / 'old-unbound.json'
        original.write_text('{}', encoding='utf-8')
        artifact = self.c.add_source(task, original)
        request = self.request(task)
        with self.assertRaises(GuardError):
            self.c.attachment_result(task, request['request_id'], {
                'status': 'reused', 'path': str(original), 'sha256': digest(original.read_bytes()),
                'provenance': {'task_id': task, 'artifact_id': artifact['artifact_id']}})
        self.assertNotIn('acquisition_identity', self.c.artifact(self.c.get(task), artifact['artifact_id']))

    def indexed_original(self, task, version='2026'):
        request = self.request(task, version=version)
        release = self.c.root / 'outputs' / 'synthetic_release'
        original = release / 'originals' / 'catalog.json'
        index = release / 'indices' / 'source_index.csv'
        original.parent.mkdir(parents=True, exist_ok=True)
        index.parent.mkdir(parents=True, exist_ok=True)
        original.write_text('{"synthetic_version":"2026"}', encoding='utf-8')
        row = {'actual_path': 'originals/catalog.json', 'sha256': digest(original.read_bytes()),
               'url': request['url'], 'version_or_year': '2026', 'degree_scope': request['scope']}
        with index.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
        return request, original, index

    def test_public_index_binds_exact_original_and_wrong_year_rejects(self):
        task = self.task()
        request, original, index = self.indexed_original(task)
        artifact = self.c.register_existing_original(task, request['request_id'], original,
                                                    index, digest(index.read_bytes()))
        reused = self.c.attachment_result(task, request['request_id'], {
            'status': 'reused', 'path': str(original), 'sha256': digest(original.read_bytes()),
            'provenance': {'task_id': task, 'artifact_id': artifact['artifact_id']}})
        self.assertEqual(reused['status'], 'reused')
        other = self.request(task, version='2025')
        with self.assertRaises(GuardError):
            self.c.register_existing_original(task, other['request_id'], original,
                                              index, digest(index.read_bytes()))

    def test_index_binding_freezes_same_verified_bytes_despite_later_file_change(self):
        task = self.task()
        request, original, index = self.indexed_original(task)
        verified = original.read_bytes()
        real_artifact = self.c._artifact
        def change_after_identity_checked(m, data, role, provider, name, inputs, extra=None):
            if name.startswith('original-index-'):
                original.write_bytes(b'CHANGED after index matching')
            return real_artifact(m, data, role, provider, name, inputs, extra)
        with patch.object(self.c, '_artifact', side_effect=change_after_identity_checked):
            artifact = self.c.register_existing_original(task, request['request_id'], original,
                                                        index, digest(index.read_bytes()))
        self.assertEqual(artifact['hash'], digest(verified))
        self.assertEqual(self.c.read_artifact(self.c.get(task), artifact['artifact_id']), verified)
        self.assertNotEqual(original.read_bytes(), verified)

    def test_download_observation_freezes_same_verified_bytes_despite_later_change(self):
        task = self.task()
        observation = self.root / (task + '-synthetic-edge.txt')
        real_artifact = self.c._artifact
        def change_after_observation_checked(m, data, role, provider, name, inputs, extra=None):
            if name.startswith('edge-observation-'):
                observation.write_bytes(b'CHANGED after observed hash verified')
            return real_artifact(m, data, role, provider, name, inputs, extra)
        with patch.object(self.c, '_artifact', side_effect=change_after_observation_checked):
            request, original, result = self.synthetic_download(task)
        original_artifact = self.c.artifact(self.c.get(task), result['current']['provenance']['artifact_id'])
        observed_artifact = self.c.artifact(self.c.get(task), original_artifact['input_artifacts'][0])
        expected = result['attempts'][0]['edge_observation']['evidence_sha256']
        self.assertEqual(observed_artifact['hash'], expected)
        self.assertNotEqual(digest(observation.read_bytes()), expected)

    def captured_ack(self, task, receipt, payload):
        self.c.package(task)
        submission = self.c.prepare_submission(task, 'producer')
        token = self.c.acquire_gui(task)
        def proof(stage, **extra):
            value = {'kind': 'simulated', 'task_id': task,
                'submission_id': submission['submission_id'], 'provider': submission['provider'],
                'stage': stage, 'observed_at': now(), 'window': 'Isolated synthetic client',
                'raw_text': submission['prompt'], 'input_text': submission['prompt'],
                'package_hash': submission['package_hash'], 'unexpected_attachments': False,
                'client_available': True, 'quota_available': True, 'message_visible': True}
            value.update(extra)
            path = self.root / (task + '-proof-' + stage + '.json')
            path.write_text(js(value), encoding='utf-8')
            return path
        self.c.begin_submission(task, submission['submission_id'], token, proof('preflight'))
        self.c.confirm_submission(task, submission['submission_id'], token, proof('confirmation'))
        raw = self.root / (task + '-raw.txt')
        raw.write_text(payload if isinstance(payload, str) else js(payload), encoding='utf-8')
        captured = self.c.capture_output(task, submission['submission_id'], raw, token,
            proof('output', completed=True, output_hash=digest(raw.read_bytes())))
        self.c.release_gui(task, token)
        return captured

    def test_structured_exact_consumed_acknowledgement_positive(self):
        task = self.task(external=True)
        self.request(task)
        receipt = self.c.publish_attachment_receipt(task)
        captured = self.captured_ack(task, receipt, {'receipt_acknowledgements': [
            {'receipt_sha256': receipt['hash'], 'status': 'consumed', 'consumed': True}]})
        acknowledged = self.c.acknowledge_receipt(task, receipt['artifact_id'], captured['artifact_id'])
        self.assertEqual(acknowledged['consumption'], 'acknowledged')
        self.assertEqual(acknowledged['evidence_artifact'], captured['artifact_id'])
        self.assertEqual(acknowledged['member'], 'kimi')

    def test_denial_or_not_consumed_and_wrong_hash_remain_unknown(self):
        factories = (
            lambda sha: 'I have NOT consumed this receipt: ' + sha,
            lambda sha: {'receipt_acknowledgements': [{'receipt_sha256': sha,
                         'status': 'denied', 'consumed': False}]},
            lambda sha: {'receipt_acknowledgements': [{'receipt_sha256': sha,
                         'status': 'consumed', 'consumed': False}]},
            lambda sha: {'receipt_acknowledgements': [{'receipt_sha256': '0' * 64,
                         'status': 'consumed', 'consumed': True}]},
        )
        for number, factory in enumerate(factories):
            with self.subTest(negative=number):
                task = self.task(external=True)
                self.request(task)
                receipt = self.c.publish_attachment_receipt(task)
                captured = self.captured_ack(task, receipt, factory(receipt['hash']))
                with self.assertRaises(GuardError):
                    self.c.acknowledge_receipt(task, receipt['artifact_id'], captured['artifact_id'])
                record = self.c.get(task)['attachment_receipts'][0]
                self.assertEqual(record['consumption'], 'unknown')


if __name__ == '__main__':
    unittest.main()
