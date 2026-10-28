"""Isolated controller tests. No real member call, UI action or public download."""
import copy
import json
import math
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from cluster.core import Cluster, GuardError, digest, js
from cluster.handoff import process_member_content
from cluster.handoff_hardening import (CONTRACT_VERSION, SessionBudget, content_contract,
                                      diagnose_root_causes, material_preflight)
from tests.test_core import CoreTest, spec


class PreflightTest(unittest.TestCase):
    proof = CoreTest.proof
    setUp = CoreTest.setUp
    tearDown = CoreTest.tearDown

    def requirement(self, **kwargs):
        value = {'source_id': 'S1', 'path': str(self.source), 'sha256': digest(self.source.read_bytes()),
                 'expected_format': 'txt', 'purpose': 'Original context', 'owner': 'orchestrator', 'blocking': True}
        value.update(kwargs)
        return value

    def test_preflight_collects_missing_and_wrong_hash_before_model_intent(self):
        value = spec(True)
        value['required_materials'] = [self.requirement(path=str(self.root / 'missing.txt')),
                                       self.requirement(source_id='S2', sha256='a' * 64)]
        task = self.c.create(value)['task_id']; self.c.add_source(task, self.source)
        report = self.c.material_preflight(task)
        self.assertFalse(report['passed']); self.assertEqual(len(report['errors']), 2)
        self.assertEqual({e['owner'] for e in report['errors']}, {'orchestrator'})
        with self.assertRaisesRegex(GuardError, 'preflight'): self.c.package(task)
        self.assertEqual(self.c.metrics(task)['submission_intents'], 0)
        self.assertEqual(self.c.get(task)['status'], 'CREATED')

    def test_optional_material_does_not_block_unrelated_work(self):
        report = material_preflight(self.root, [self.requirement(path=str(self.root / 'missing.txt'), blocking=False)])
        self.assertTrue(report['passed']); self.assertEqual(len(report['errors']), 1)
        self.assertFalse(report['automatic_semantic_proof'])

    def test_fake_pdf_and_partial_qualification_block(self):
        fake = self.root / 'catalog.pdf'; fake.write_text('<html>Login required</html>')
        report = material_preflight(self.root, [self.requirement(path=str(fake), expected_format='pdf', sha256=digest(fake.read_bytes()))])
        self.assertFalse(report['passed']); self.assertIn('signature', report['errors'][0]['reason'])
        fake.write_bytes(b'%PDF-1.7\nThis is a truncated invalid PDF')
        report = material_preflight(self.root,[self.requirement(path=str(fake),expected_format='pdf',sha256=digest(fake.read_bytes()))])
        self.assertFalse(report['passed'])
        error = report['errors'][0]
        self.assertIn(error['failure_kind'], ('material_invalid','dependency_unavailable'))
        if error['failure_kind'] == 'dependency_unavailable':
            self.assertIn('pypdf', error['reason'])
        else:
            self.assertIn('Unreadable', error['reason'])
        report = material_preflight(self.root, [self.requirement(scope_status='partial', require_complete=True)])
        self.assertFalse(report['passed'])
        report = material_preflight(self.root, [self.requirement(required_text=['Missing decisive qualification'])])
        self.assertFalse(report['passed'])

    def test_symlink_escape_and_control_answers_rejected_without_reading(self):
        control = self.root / 'fixtures' / 'control'; control.mkdir(parents=True)
        answer = control / 'answer.txt'; answer.write_text('Synthetic canary, no private data')
        alias = self.root / 'alias.txt'; alias.symlink_to(self.source)
        for path in (str(alias), str(self.root / '..' / 'outside.txt'), str(answer)):
            report = material_preflight(self.root, [self.requirement(path=path)])
            self.assertFalse(report['passed'])

    def test_conflicting_identity_and_contract_version_rejected(self):
        other = self.root / 'other.txt'; other.write_text('Different bytes')
        report = material_preflight(self.root, [self.requirement(), self.requirement(path=str(other), sha256=digest(other.read_bytes()))])
        self.assertFalse(report['passed']); self.assertIn('conflicting', report['errors'][0]['reason'])
        value = spec(True); value['handoff_contract_version'] = 'future-v99'
        task = self.c.create(value)['task_id']; self.c.add_source(task, self.source)
        with self.assertRaisesRegex(GuardError, 'version mismatch'): self.c.package(task)

    def test_changed_explicit_material_between_package_and_begin_blocked(self):
        value = spec(True); value['required_materials'] = [self.requirement()]
        task = self.c.create(value)['task_id']; self.c.add_source(task, self.source); self.c.package(task)
        ticket = self.c.prepare_submission(task, 'producer'); token = self.c.acquire_gui(task)
        self.source.write_text('CHANGED original')
        with self.assertRaisesRegex(GuardError, 'preflight'):
            self.c.begin_submission(task, ticket['submission_id'], token, self.proof(task, ticket, 'preflight'))
        self.assertEqual(self.c.metrics(task)['submission_intents'], 0)
        self.c.release_gui(task, token)


class ContractAndDiagnosticsTest(unittest.TestCase):
    def envelope(self, phase='producer', provider='kimi'):
        result = {'task_id': 'T', 'submission_id': 'SUB', 'work_item_id': 'W', 'round_id': 1,
                  'provider': provider, 'phase': phase, 'source_namespace': 'T/SUB'}
        if phase == 'reviewer': result.update(candidate_artifact_id='A', candidate_sha256='a' * 64)
        return result

    def test_generated_contracts_have_disjoint_minimums_and_complete_examples(self):
        contracts = {kind: content_contract(kind) for kind in ('research', 'review', 'download', 'receipt')}
        for kind, contract in contracts.items():
            self.assertEqual(contract['version'], CONTRACT_VERSION)
            self.assertEqual(len(contract['sha256']), 64)
            self.assertTrue(set(contract['required']) <= set(contract['example']))
        self.assertNotIn('sources', contracts['receipt']['required'])
        self.assertNotIn('sources', contracts['download']['required'])
        research = process_member_content(contracts['research']['example'], self.envelope())
        self.assertEqual(research['errors'], [])
        review = process_member_content(contracts['review']['example'], self.envelope('reviewer', 'workbuddy'), research['data'])
        self.assertEqual(review['errors'], []); self.assertTrue(review['semantic_gate']['blocking'])
        self.assertFalse(review['semantic_gate']['automatic_semantic_proof'])

    def test_one_missing_source_ten_impacts_one_root_keeps_original(self):
        raw = content_contract('research')['example']; raw['claims'] = [
            {'claim_id': 'C' + str(i), 'text': 'Claim', 'scope': 'Scope', 'time': None,
             'source_ids': ['S-MISSING'], 'location': 'Draft ' + str(i)} for i in range(10)]
        original = copy.deepcopy(raw)
        report = process_member_content(raw, self.envelope())
        self.assertEqual(raw, original)
        roots = report['diagnostics']['root_causes']
        root = next(r for r in roots if r['root_cause_id'] == 'missing_source:S-MISSING')
        self.assertEqual(len(root['impacts']), 10)
        self.assertEqual(report['diagnostics']['root_cause_count'], 1)
        self.assertEqual(report['diagnostics']['error_count'], 10)

    def test_alias_conflict_false_string_and_unknown_date_are_not_guessed(self):
        data = content_contract('research')['example']; original = copy.deepcopy(data)
        data['sources'][0].update(id='conflicting-id', excerpt_is_complete='false', accessed_at='2026-10-02')
        response = process_member_content(data, self.envelope())
        self.assertTrue(any(e['code'] == 'identity_conflict' for e in response['errors']))
        self.assertIs(response['data']['sources'][0]['excerpt_is_complete'], False)
        self.assertIsNone(response['data']['sources'][0]['accessed_at'])
        self.assertEqual(response['data']['sources'][0]['reported_accessed_at'], '2026-10-02')
        self.assertEqual(data['sources'][0]['excerpt_is_complete'], 'false')
        self.assertEqual(original['sources'][0]['accessed_at'], None)

    def test_schema_complete_review_unknown_still_blocking(self):
        research = process_member_content(content_contract('research')['example'], self.envelope())['data']
        response = process_member_content(content_contract('review')['example'], self.envelope('reviewer', 'workbuddy'), research)
        self.assertEqual(response['errors'], [])
        self.assertTrue(response['semantic_gate']['blocking'])
        self.assertEqual(response['data']['claim_checks'][0]['result'], 'evidence_gap')


class CapturedRootCauseTest(unittest.TestCase):
    setUp = CoreTest.setUp
    tearDown = CoreTest.tearDown
    proof = CoreTest.proof
    external_start = CoreTest.external_start

    def test_retained_failure_diagnosis_does_not_resubmit_or_lose_completed_generation(self):
        value = spec(True)
        value.update(task_type='research', work_kind='research', result_protocol='member_content_v1',
                     validators=[{'kind':'research'}])
        task = self.c.create(value)['task_id']; self.c.add_source(task,self.source); self.c.package(task)
        ticket, token = self.external_start(task)
        payload = content_contract('research')['example']; del payload['sources'][0]['title']
        payload['claims'] = [{'claim_id':'C'+str(i),'text':'Claim','scope':'Scope','time':None,
                             'source_ids':['S-MISSING'],'location':'Draft'} for i in range(10)]
        raw = self.root/'raw.json'; raw.write_text(js(payload))
        artifact = self.c.capture_output(task,ticket['submission_id'],raw,token,
            self.proof(task,ticket,'output',completed=True,output_hash=digest(raw.read_bytes())))
        self.c.release_gui(task,token)
        with self.assertRaises(GuardError): self.c.ingest_output(task,ticket['submission_id'])
        before = self.c.get(task)
        report = self.c.captured_output_diagnostics(task)
        self.assertEqual(report['root_cause_count'],2); self.assertEqual(report['error_count'],11)
        self.assertFalse(report['new_external_submission_required'])
        self.assertEqual(report['raw_artifact'],artifact['artifact_id'])
        packet = self.c.decision_packet(task)
        self.assertEqual(packet['provider_execution_status'],'completed')
        self.assertEqual(packet['next_action'],'repair_captured_output_locally_do_not_resend')
        self.assertTrue(packet['blocked'])
        self.assertEqual(packet['handoff_diagnostics']['root_cause_count'],2)
        self.assertEqual(self.c.get(task),before)
        self.c.recover(task)
        with self.assertRaises(GuardError): self.c.prepare_submission(task,'producer')
        self.assertEqual(self.c.metrics(task)['external_calls_confirmed'],1)
        self.assertEqual(self.c.read_artifact(self.c.get(task),artifact['artifact_id']),raw.read_bytes())


class BudgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.ledger = SessionBudget(self.root)
        self.ledger.init('SESSION', 800, 500, 'native_credit')
    def tearDown(self): self.tmp.cleanup()
    def reserve(self, event='RES1', revision=1, call='CALL1', amount=100):
        return self.ledger.apply('SESSION', event, revision, 'reserve', {
            'call_id': call, 'upper_bound': amount, 'bound_evidence': 'Synthetic fixed cost upper bound',
            'task_id': 'TASK1', 'model': 'simulated_member'})
    def settle(self, amount, event='COST1', revision=2, call='CALL1'):
        return self.ledger.apply('SESSION', event, revision, 'settle', {
            'call_id': call, 'actual_cost': amount, 'cost_evidence': 'Synthetic native bill'})

    def test_atomic_concurrent_reservations_and_stale_cas(self):
        def attempt(i):
            try: self.reserve(event='RES' + str(i), call='CALL' + str(i)); return 'reserved'
            except ValueError: return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool: result = list(pool.map(attempt, range(2)))
        self.assertEqual(result.count('reserved'), 1); self.assertEqual(result.count('rejected'), 1)
        state = self.ledger.read('SESSION'); self.assertEqual(state['revision'], 2)
        with self.assertRaisesRegex(ValueError, 'Stale'):
            self.ledger.apply('SESSION', 'OBS', 1, 'observe', {'observed_at': '2026-10-02T00:00:00+08:00', 'unit': 'native_credit', 'balance': 2000})

    def test_same_event_replay_idempotent_conflict_rejected(self):
        first = self.reserve(); self.assertEqual(self.reserve(), first)
        with self.assertRaisesRegex(ValueError, 'identity conflict'): self.reserve(amount=200)
        paid = self.settle(80); self.assertEqual(self.settle(80), paid)
        self.assertEqual(self.ledger.read('SESSION')['spent'], 80)

    def test_pending_or_unknown_cost_blocks_new_dispatch(self):
        self.reserve()
        with self.assertRaisesRegex(ValueError, 'unsettled'): self.reserve(event='RES2', revision=2, call='CALL2')
        with self.assertRaisesRegex(ValueError, 'unknown'):
            self.ledger.apply('SESSION', 'COST', 2, 'settle', {'call_id': 'CALL1', 'actual_cost': None, 'cost_evidence': 'Display delayed'})
        self.assertEqual(self.ledger.read('SESSION')['calls']['CALL1']['status'], 'reserved')

    def test_balance_increase_reset_preserves_session_and_limit(self):
        self.reserve(); self.settle(50)
        state = self.ledger.apply('SESSION', 'RESET', 3, 'observe', {
            'observed_at': '2026-10-02T00:00:00+08:00', 'unit': 'native_credit', 'balance': 9000, 'reset_suspected': True})
        self.assertEqual(state['spent'], 50); self.assertEqual(state['limit'], 800)
        with self.assertRaisesRegex(ValueError, 'cannot be replaced'):
            self.ledger.init('SESSION', 1600, 500, 'native_credit')

    def test_500_reassessment_800_stop_and_actual_overestimate_preserved(self):
        self.reserve(amount=600); state = self.settle(550)
        self.assertTrue(state['reassessment_due'])
        with self.assertRaisesRegex(ValueError, 'Reassessment'): self.reserve(event='RES2', revision=3, call='CALL2', amount=10)
        self.ledger.apply('SESSION', 'ASSESS', 3, 'reassess', {'reason': 'Synthetic meaningful remaining audit, settled'})
        with self.assertRaisesRegex(ValueError, 'limit'): self.reserve(event='RES2', revision=4, call='CALL2', amount=300)
        self.reserve(event='RES2', revision=4, call='CALL2', amount=200)
        state = self.settle(300, event='COST2', revision=5, call='CALL2')
        self.assertEqual(state['spent'], 850); self.assertTrue(state['calls']['CALL2']['upper_bound_exceeded'])
        with self.assertRaises(ValueError): self.ledger.guard('SESSION', 'CALL2', 'workbuddy')

    def test_uncertain_estimate_is_not_guaranteed_and_near_limit_rejects(self):
        payload = {'call_id': 'C1', 'task_id': 'TASK1', 'upper_bound': None, 'estimated_cost': 20,
            'uncertainty_margin': 200, 'estimate_evidence': 'Synthetic observed previous bill',
            'balance_observation': 'Synthetic native balance screenshot'}
        self.ledger.apply('SESSION', 'R1', 1, 'reserve', payload)
        guard = self.ledger.guard('SESSION', 'C1', 'workbuddy')
        self.assertIsNone(guard['guaranteed_upper_bound']); self.assertFalse(guard['hard_limit_guaranteed'])
        self.assertEqual(guard['planning_reserve'], 220)
        state = self.ledger.apply('SESSION', 'S1', 2, 'settle', {'call_id': 'C1', 'actual_cost': 700,
            'cost_evidence': 'Shared balance decrease, attribution uncertain', 'cost_kind': 'shared_balance_delta'})
        self.assertIsNone(state['calls']['C1']['exact_task_cost'])
        self.ledger.apply('SESSION', 'CHECK', 3, 'reassess', {'reason': 'No reliable next bound'})
        payload['call_id'] = 'C2'
        with self.assertRaisesRegex(ValueError, 'limit'): self.ledger.apply('SESSION', 'R2', 4, 'reserve', payload)

    def test_budget_storage_symlinks_and_malicious_session_ids_rejected(self):
        outside = self.root / 'outside'; outside.mkdir()
        directory = self.root / 'alternate'; directory.mkdir(); (directory / '.cluster').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symbolic'): SessionBudget(directory)
        ledger = self.root / '.cluster' / 'session_budgets' / 'LINK.json'; ledger.symlink_to(outside / 'victim.json')
        with self.assertRaisesRegex(ValueError, 'symbolic'): self.ledger.init('LINK', 800, 500, 'native_credit')
        lock = self.root / '.cluster' / 'session_budgets' / 'LOCK.lock'; lock.symlink_to(outside / 'victim.lock')
        with self.assertRaisesRegex(ValueError, 'symbolic'): self.ledger.init('LOCK', 800, 500, 'native_credit')
        for bad in ('../escape', 'X/Y', '..', '.'):
            with self.assertRaises((ValueError, OSError)): self.ledger.read(bad)
        self.assertFalse((outside / 'victim.json').exists())
        self.assertFalse((outside / 'victim.lock').exists())

    def test_nonfinite_or_unsubstantiated_values_rejected(self):
        for value in (float('inf'), float('nan'), True):
            with self.assertRaises(ValueError): self.reserve(amount=value)
        with self.assertRaises(ValueError):
            self.ledger.apply('SESSION', 'EMPTY', 1, 'reserve', {'call_id': 'C1', 'upper_bound': None})


class EdgeConsumerTest(unittest.TestCase):
    setUp = CoreTest.setUp
    tearDown = CoreTest.tearDown
    proof = CoreTest.proof
    external_start = CoreTest.external_start

    def setup_download(self):
        task = self.c.create(spec())['task_id']
        requests = []
        for member in ('kimi', 'workbuddy'):
            value = spec(True); value['assigned_provider'] = member
            consumer_task = self.c.create(value)['task_id']
            self.c.add_source(consumer_task, self.source); self.c.package(consumer_task)
            requests.append({'url': 'https://example.org/catalog.json', 'version': '2026', 'scope': 'Synthetic scope',
                'expected_format': 'json', 'requester': member, 'purpose': 'Synthetic evidence', 'source_id': 'S1',
                'original_page': 'https://example.org/index', 'priority': 1,
                'work_item_id': self.c.get(consumer_task)['work_item_id'], 'consumer_id': member + '-C',
                '_consumer_task': consumer_task})
        items = self.c.request_attachment_batch(task, requests)
        path = self.root / 'original.json'; path.write_text('{"actual":1}')
        registered = self.c.add_source(task, path)
        with self.c._mutate(task) as m:
            self.c.artifact(m, registered['artifact_id'])['acquisition_identity'] = {
                k: items[0][k] for k in ('url', 'version', 'scope', 'expected_format')}
        result = {'status': 'reused', 'path': str(path), 'sha256': digest(path.read_bytes()),
                  'provenance': {'task_id': task, 'artifact_id': registered['artifact_id']}}
        self.c.attachment_result(task, items[0]['request_id'], result)
        receipt = self.c.publish_attachment_receipt(task)
        return task, items[0]['request_id'], requests, result, receipt

    def capture_ack(self, task, receipt, request, consumer):
        ct = consumer['_consumer_task']; ticket, token = self.external_start(ct)
        payload = {'receipt_acknowledgements': [{'receipt_sha256': receipt['hash'], 'request_id': request,
                    'consumer_id': consumer['consumer_id'], 'status': 'consumed', 'consumed': True}]}
        path = self.root / (consumer['requester'] + '-ack.json'); path.write_text(js(payload))
        evidence = self.c.capture_output(ct, ticket['submission_id'], path, token,
            self.proof(ct, ticket, 'output', completed=True, output_hash=digest(path.read_bytes())))
        self.c.release_gui(ct, token)
        return ct, evidence['artifact_id']

    def test_two_consumers_one_original_each_ack_independent_and_no_wakeup(self):
        task, request, consumers, result, receipt = self.setup_download()
        m = self.c.get(task); self.assertEqual(len(m['attachment_queue']), 1)
        states = m['attachment_queue'][request]['consumers']; self.assertEqual(len(states), 2)
        self.assertEqual({r['status'] for r in states.values()}, {'pending'})
        ct, evidence = self.capture_ack(task, receipt, request, consumers[0])
        ack = self.c.acknowledge_attachment_consumption(task, receipt['artifact_id'], request, consumers[0]['consumer_id'], ct, evidence)
        self.assertEqual(ack['status'], 'acknowledged')
        self.assertEqual(self.c.get(task)['attachment_queue'][request]['consumers'][consumers[1]['consumer_id']]['status'], 'pending')
        with self.assertRaises(GuardError):
            self.c.acknowledge_attachment_consumption(task, receipt['artifact_id'], request, consumers[1]['consumer_id'], ct, evidence)
        self.assertEqual(self.c.metrics(task)['external_calls_confirmed'], 0)

    def test_repeat_completion_idempotent_and_modified_bytes_rejected(self):
        task, request, consumers, result, receipt = self.setup_download()
        before = self.c.get(task); self.c.attachment_result(task, request, result)
        self.assertEqual(self.c.get(task), before)
        Path(result['path']).write_text('{"actual":2}')
        with self.assertRaisesRegex(ValueError, 'changed'): self.c.attachment_result(task, request, result)

    def test_partial_batch_one_failure_keeps_success_ready(self):
        task, request, consumers, result, receipt = self.setup_download()
        other = self.c.request_attachment(task, {'url': 'https://example.org/other', 'version': '2025',
            'scope': 'Other', 'expected_format': 'pdf', 'requester': 'kimi', 'purpose': 'Other evidence'})
        fake = self.root / 'broken.pdf'; fake.write_text('<html>Login</html>')
        report = self.c.attachment_result_batch(task, [
            {'request_id': request, 'result': result}, {'request_id': other['request_id'],
             'result': {'status': 'downloaded', 'path': str(fake), 'sha256': digest(fake.read_bytes())}}])
        self.assertEqual([r['status'] for r in report['items']], ['reused', 'rejected'])
        self.assertFalse(report['automatic_member_wakeup'])
        self.assertEqual(self.c.get(task)['attachment_queue'][request]['status'], 'reused')

    def test_batch_bad_contract_reports_missing_fields_before_any_queue(self):
        task = self.c.create(spec())['task_id']
        with self.assertRaisesRegex(ValueError, 'preflight'): self.c.request_attachment_batch(task, [{'url': 'https://example.org'}])
        self.assertFalse(self.c.get(task).get('attachment_queue'))


class SessionBudgetControllerTest(unittest.TestCase):
    setUp = CoreTest.setUp
    tearDown = CoreTest.tearDown
    proof = CoreTest.proof

    def test_budget_guard_is_in_real_prepare_and_begin_entries(self):
        ledger = SessionBudget(self.root); ledger.init('SESSION', 800, 500, 'native_credit')
        value = spec(True); value['assigned_provider'] = 'workbuddy'
        value['session_budget'] = {'session_id': 'SESSION', 'call_id': 'C1', 'provider': 'workbuddy'}
        task = self.c.create(value)['task_id']; self.c.add_source(task, self.source); self.c.package(task)
        with self.assertRaisesRegex(ValueError, 'reservation'): self.c.prepare_submission(task, 'producer')
        ledger.apply('SESSION', 'R1', 1, 'reserve', {'call_id': 'C1', 'task_id': task,
            'upper_bound': 100, 'bound_evidence': 'Synthetic budget bound'})
        ticket = self.c.prepare_submission(task, 'producer'); token = self.c.acquire_gui(task)
        ledger.apply('SESSION', 'S1', 2, 'settle', {'call_id': 'C1', 'actual_cost': 90, 'cost_evidence': 'Synthetic bill'})
        with self.assertRaisesRegex(ValueError, 'reservation'):
            self.c.begin_submission(task, ticket['submission_id'], token, self.proof(task, ticket, 'preflight'))
        self.assertEqual(self.c.metrics(task)['submission_intents'], 0)
        self.c.release_gui(task, token)

    def test_routed_provider_mismatch_cannot_silently_bypass_session_budget_prepare(self):
        SessionBudget(self.root).init('SESSION',800,500,'native_credit')
        value=spec(True);value['assigned_provider']='kimi'
        value['session_budget']={'session_id':'SESSION','call_id':'UNRESERVED','provider':'workbuddy'}
        task=self.c.create(value)['task_id'];self.c.add_source(task,self.source);self.c.package(task)
        with self.assertRaisesRegex(ValueError,'scope mismatch'):
            self.c.prepare_submission(task,'producer')
        self.assertEqual(self.c.get(task)['producer'],'kimi')
        self.assertFalse(self.c.get(task)['submissions'])
        self.assertEqual(self.c.metrics(task)['submission_intents'],0)

    def test_existing_prepared_mismatched_ticket_is_rejected_at_begin_before_intent(self):
        SessionBudget(self.root).init('SESSION',800,500,'native_credit')
        value=spec(True);value['assigned_provider']='kimi'
        value['session_budget']={'session_id':'SESSION','call_id':'UNRESERVED','provider':'workbuddy'}
        task=self.c.create(value)['task_id'];self.c.add_source(task,self.source);self.c.package(task)
        # Simulate a ticket persisted by the old defective prepare entrypoint.
        # The actual begin guard remains active and must reject that old ticket.
        with patch.object(self.c,'session_budget_guard',return_value=None):
            ticket=self.c.prepare_submission(task,'producer')
        token=self.c.acquire_gui(task)
        with self.assertRaisesRegex(ValueError,'scope mismatch'):
            self.c.begin_submission(task,ticket['submission_id'],token,self.proof(task,ticket,'preflight'))
        self.assertEqual(self.c.metrics(task)['submission_intents'],0)
        self.assertEqual(self.c.get(task)['submissions'][0]['status'],'prepared')
        self.c.release_gui(task,token)

    def test_reserved_call_cannot_be_reused_by_another_task(self):
        ledger = SessionBudget(self.root); ledger.init('SESSION', 800, 500, 'native_credit')
        value = spec(True); value['assigned_provider'] = 'workbuddy'
        value['session_budget'] = {'session_id': 'SESSION', 'call_id': 'C1', 'provider': 'workbuddy'}
        task = self.c.create(value)['task_id']; self.c.add_source(task, self.source); self.c.package(task)
        ledger.apply('SESSION', 'R1', 1, 'reserve', {'call_id': 'C1', 'task_id': 'OTHER', 'upper_bound': 100,
            'bound_evidence': 'Synthetic bound'})
        with self.assertRaisesRegex(ValueError, 'different task'): self.c.prepare_submission(task, 'producer')


del CoreTest
if __name__ == '__main__': unittest.main()
