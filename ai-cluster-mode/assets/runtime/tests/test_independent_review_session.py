"""Synthetic UI-state guard regressions; never evidence of real provider isolation."""
import copy
import json
import unittest
from datetime import datetime, timedelta

from test_core import CoreTest
from test_collaboration_protocol import ReviewerPackagingTest, content, envelope, review
from cluster.core import GuardError, js
from cluster.handoff import (MEMBER_CONTENT, FRESH_REVIEW_SESSION,
                             check_fresh_review_session, process_member_content)


def simulated_session(proof, submission):
    """Explicit test-only snapshots; not reused to create live GUI proof."""
    stamp = proof['observed_at']
    frame = {'kind': 'simulated', 'source': 'simulated_test_fixture',
             'observation_type': 'ui_state', 'scope': 'active_conversation',
             'window': proof['window'], 'observed_at': stamp,
             'prior_messages_visible': False, 'generation_active': False,
             'message_count': 0}
    return {'policy': FRESH_REVIEW_SESSION,
            'before_input': dict(frame, raw_text='Test empty conversation; Composer value: ',
                                 empty_state_marker='Test empty conversation',
                                 composer_empty=True, composer_value=''),
            'after_input': dict(frame, raw_text=submission['prompt'])}


class FreshReviewSessionTest(unittest.TestCase):
    setUp = CoreTest.setUp
    tearDown = CoreTest.tearDown
    proof = CoreTest.proof
    external_start = CoreTest.external_start
    capture = ReviewerPackagingTest.capture
    packaged = ReviewerPackagingTest.packaged
    marker = ReviewerPackagingTest.marker

    def ticket(self, protocol=MEMBER_CONTENT):
        task, _, _, _, _, _ = self.packaged(protocol=protocol)
        submission = self.c.prepare_submission(task, 'reviewer')
        return task, submission, self.c.acquire_gui(task)

    def fresh_proof(self, task, submission):
        path = self.proof(task, submission, 'preflight')
        proof = json.loads(path.read_text())
        proof['fresh_review_session'] = simulated_session(proof, submission)
        return path, proof

    def reject(self, task, submission, token, proof):
        path = self.root/'fresh-session-invalid.json'
        path.write_text(js(proof))
        with self.assertRaises(GuardError):
            self.c.begin_submission(task, submission['submission_id'], token, path)
        state = self.c.get(task)
        self.assertEqual(state['submissions'][-1]['status'], 'prepared')
        self.assertEqual(state['status'], 'REVIEW_PENDING')
        self.assertEqual(self.c.metrics(task)['submission_intents'], 0)

    def test_new_review_requires_retained_empty_state_not_new_task_click(self):
        task, submission, token = self.ticket()
        self.assertEqual(self.c.get(task)['spec']['independent_review_session_policy'], FRESH_REVIEW_SESSION)
        self.assertEqual(submission['session_policy'], FRESH_REVIEW_SESSION)
        path, proof = self.fresh_proof(task, submission)
        missing = copy.deepcopy(proof);missing.pop('fresh_review_session')
        self.reject(task, submission, token, missing)
        clicked = copy.deepcopy(proof)
        clicked['fresh_review_session']['before_input'].update(observation_type='action_receipt',
                    raw_text='New Task click succeeded', empty_state_marker='New Task click succeeded')
        self.reject(task, submission, token, clicked)
        path.write_text(js(proof))
        self.c.begin_submission(task, submission['submission_id'], token, path)
        self.assertEqual(self.c.get(task)['status'], 'SUBMISSION_UNCERTAIN')
        a = self.c.get(task)['artifacts'][-1]
        retained = json.loads(self.c.read_artifact(self.c.get(task), a['artifact_id']))
        self.assertEqual(retained['fresh_review_session'], proof['fresh_review_session'])
        self.assertEqual(self.c.metrics(task)['submission_intents'], 1)

    def test_old_history_generation_wrong_composer_and_unobserved_draft_reject(self):
        task, submission, token = self.ticket();_, valid = self.fresh_proof(task, submission)
        mutations = [
            ('before_input', 'raw_text', valid['fresh_review_session']['before_input']['raw_text']+' TASK-20260930-0080-SUB-001'),
            ('after_input', 'raw_text', submission['prompt']+' TASK-20260930-0080'),
            ('before_input', 'prior_messages_visible', True),
            ('after_input', 'generation_active', True),
            ('before_input', 'message_count', True),
            ('after_input', 'message_count', 1),
            ('before_input', 'composer_value', 'old unsent text'),
            ('before_input', 'empty_state_marker', 'not visible in observed text'),
            ('after_input', 'raw_text', 'controller expected draft only'),
            ('after_input', 'raw_text', 'observed only here: '+submission['prompt']),
        ]
        for frame, field, value in mutations:
            with self.subTest(frame=frame, field=field):
                bad = copy.deepcopy(valid);bad['fresh_review_session'][frame][field] = value
                self.reject(task, submission, token, bad)

    def test_stale_wrong_mode_and_predicted_source_reject(self):
        task, submission, token = self.ticket();_, valid = self.fresh_proof(task, submission)
        future = (datetime.fromisoformat(valid['observed_at'])+timedelta(seconds=1)).isoformat()
        mutations = [('source', 'controller_prediction'), ('kind', 'gui_observation'),
                     ('scope', 'whole_sidebar'), ('window', 'different client'),
                     ('observed_at', '2000-01-01T00:00:00+00:00'),
                     ('observed_at', future)]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                bad = copy.deepcopy(valid);bad['fresh_review_session']['before_input'][field] = value
                self.reject(task, submission, token, bad)
        # Even internally consistent synthetic frames are never accepted as live.
        with self.assertRaisesRegex(ValueError, 'actual observation'):
            check_fresh_review_session(valid, task, submission['submission_id'], submission['prompt'], 'gui_observation')

    def test_live_mode_checker_keeps_visible_session_and_hidden_memory_distinct(self):
        task, submission, token = self.ticket();_, proof = self.fresh_proof(task, submission)
        proof['kind'] = 'gui_observation'
        for frame in proof['fresh_review_session'].values():
            if isinstance(frame, dict):frame.update(kind='gui_observation', source='computer_use')
        result = check_fresh_review_session(proof, task, submission['submission_id'], submission['prompt'], 'gui_observation')
        self.assertTrue(result['visible_session_empty'])
        self.assertFalse(result['hidden_provider_memory_verified'])
        # Pure helper exercise only: these fixtures are not persisted as live proof.
        self.assertEqual(self.c.get(task)['submissions'][-1]['status'], 'prepared')
        self.c.release_gui(task, token)

    def test_legacy_and_prepolicy_historical_ticket_remain_compatible(self):
        task, submission, token = self.ticket(protocol=None)
        self.assertNotIn('session_policy', submission)
        self.c.begin_submission(task, submission['submission_id'], token, self.proof(task, submission, 'preflight'))
        self.c.release_gui(task, token)
        task, submission, token = self.ticket()
        # Isolated fixture models an already stored pre-policy ticket; no real
        # historical manifest, raw artifact or task is migrated or rewritten.
        with self.c._mutate(task) as m:
            m['spec'].pop('independent_review_session_policy')
            m['submissions'][-1].pop('session_policy')
        self.c.recover(task)
        self.assertNotIn('independent_review_session_policy', self.c.get(task)['spec'])
        self.c.begin_submission(task, submission['submission_id'], token, self.proof(task, submission, 'preflight'))
        self.assertEqual(self.c.get(task)['status'], 'SUBMISSION_UNCERTAIN')

    def test_raw_artifact_fabrication_still_rejected_even_exact_controller_id(self):
        candidate = process_member_content(content(), envelope())['data']
        response = review(candidate)
        self.assertEqual(process_member_content(response, envelope('reviewer','workbuddy'), candidate)['errors'], [])
        for identity in ('ART-CONTEXT-001', 'null', 'ART-REGISTERED-EXACT'):
            with self.subTest(identity=identity):
                bad = copy.deepcopy(response);bad['sources'][0]['raw_artifact_id'] = identity
                result = process_member_content(bad, envelope('reviewer','workbuddy'), candidate,
                                                source_artifact_ids=['ART-REGISTERED-EXACT'])
                self.assertTrue(any(e['category']=='identity' and e['location']=='sources[0].raw_artifact_id'
                                    for e in result['errors']))


del CoreTest, ReviewerPackagingTest

if __name__ == '__main__':unittest.main()
