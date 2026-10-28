"""Targeted F3/F5 replay. All inputs and costs are isolated synthetic fixtures."""
import tempfile
import unittest
from pathlib import Path

from cluster.core import GuardError
from cluster.handoff_hardening import SessionBudget
from cluster.release_binding import accepted_source_identities, transformed_bytes
from tests.test_release_binding_hardening import AcceptedReleaseBindingTest


class SourceAttributionTest(unittest.TestCase):
    setUp = AcceptedReleaseBindingTest.setUp
    tearDown = AcceptedReleaseBindingTest.tearDown
    prepared = AcceptedReleaseBindingTest.prepared
    register = AcceptedReleaseBindingTest.register
    bindings = AcceptedReleaseBindingTest.bindings

    def release_fixture(self, registered_url='https://example.org/original-A', sources='default', namespace='exact-task'):
        if sources == 'default':
            sources = [{'source_id':'S001','source_namespace':'exact-task','url':'https://example.org/original-A'}]
        self.task,_,files,self.validation = self.prepared(
            files={'full.md':'READY [S001] unknown remains unknown','source.txt':'Synthetic original'}, sources=sources)
        (self.release/'source.txt').write_text('Synthetic original')
        mapping=[{'release_source_id':'REL-WB-002','namespace':namespace,'source_id':'S001',
                  'original_url':registered_url,'relative_paths':['source.txt']}]
        release=self.register('READY [REL-WB-002] unknown remains unknown', options={'source_map':mapping})
        transform={'type':'utf8_reference_map_v1','version':1,'mapping':[{'from':'S001','to':'REL-WB-002','namespace':namespace}]}
        bindings=self.bindings(files[0],transform=transform)+self.bindings(files[1],filename='source.txt')
        return release,bindings

    def test_matching_accepted_namespace_id_original_url_can_bind_and_deliver(self):
        release,bindings=self.release_fixture()
        record=self.c.bind_release(self.task,release['release_id'],bindings)
        self.assertEqual(record['assurance'],'exact_accepted_scope')
        self.c.finalize(self.task);self.c.deliver(self.task)
        self.assertEqual(self.c.get(self.task)['status'],'DELIVERED')
        self.assertIn('unknown remains unknown',(self.release/'report.txt').read_text())

    def test_self_consistent_registry_cannot_reattribute_accepted_source_A_to_B(self):
        release,bindings=self.release_fixture(registered_url='https://example.org/unrelated-B')
        with self.assertRaisesRegex(GuardError,'changes accepted original_url attribution'):
            self.c.bind_release(self.task,release['release_id'],bindings)
        self.assertEqual(self.c.get(self.task)['releases'][0]['assurance'],'structure_only')
        self.c.finalize(self.task)
        with self.assertRaises(GuardError):self.c.deliver(self.task)

    def test_identity_bytes_do_not_allow_a_known_registry_url_reattribution(self):
        release,bindings=self.release_fixture(registered_url='https://example.org/unrelated-B')
        # Keep the published report identical to the accepted frozen report.
        # Registry metadata alone must still not change known URL ownership.
        self.register('READY [S001] unknown remains unknown', options={'source_map':[
            {'release_source_id':'REL-WB-002','namespace':'exact-task','source_id':'S001',
             'original_url':'https://example.org/unrelated-B','relative_paths':['source.txt']}]})
        current=self.c.get(self.task)['releases'][-1]
        bindings[0]['transform']={'type':'identity_v1','version':1}
        from cluster.core import digest
        bindings[0]['output_sha256']=digest((self.release/'report.txt').read_bytes())
        with self.assertRaisesRegex(GuardError,'changes accepted original_url attribution'):
            self.c.bind_release(self.task,current['release_id'],bindings)

    def test_missing_accepted_source_metadata_has_no_mechanical_exemption(self):
        release,bindings=self.release_fixture(sources=None)
        with self.assertRaisesRegex(GuardError,'lacks unique accepted source authority'):
            self.c.bind_release(self.task,release['release_id'],bindings)

    def test_mapping_namespace_does_not_create_authority_for_another_namespace(self):
        release,bindings=self.release_fixture(namespace='another-task')
        with self.assertRaisesRegex(GuardError,'lacks unique accepted source authority'):
            self.c.bind_release(self.task,release['release_id'],bindings)

    def test_ambiguous_accepted_identity_cannot_be_chosen_by_registry(self):
        sources=[{'source_id':'S001','source_namespace':'exact-task','url':'https://example.org/original-A'},
                 {'source_id':'S001','source_namespace':'exact-task','url':'https://example.org/unrelated-B'}]
        release,bindings=self.release_fixture(sources=sources)
        with self.assertRaisesRegex(GuardError,'lacks unique accepted source authority'):
            self.c.bind_release(self.task,release['release_id'],bindings)

    def test_caller_options_cannot_supply_the_missing_accepted_authority(self):
        mapping=[{'release_source_id':'REL-WB-002','namespace':'exact-task','source_id':'S001',
                  'original_url':'https://example.org/unrelated-B'}]
        transform={'type':'utf8_reference_map_v1','version':1,'mapping':[{'from':'S001','to':'REL-WB-002','namespace':'exact-task'}]}
        with self.assertRaisesRegex(ValueError,'lacks unique accepted source authority'):
            transformed_bytes(b'[S001]',transform,{'source_map':mapping,'accepted_source_identities':mapping})
        # Exact URL comparison deliberately does not guess trailing-slash or redirect equivalence.
        with self.assertRaisesRegex(ValueError,'changes accepted original_url attribution'):
            transformed_bytes(b'[S001]',transform,{'source_map':mapping},
                source_authority=[{'source_id':'S001','namespace':'exact-task','original_url':'https://example.org/unrelated-B/'}])

    def test_unstructured_sources_do_not_invent_namespace_or_url(self):
        for data in (b'plain accepted [S001]', b'{"sources":[{"source_id":"S001","url":"https://example.org/A"}]}',
                     b'{"sources":[{"source_id":"S001","source_namespace":"exact-task"}]}'):
            self.assertEqual(accepted_source_identities(data),[])
        self.assertEqual(transformed_bytes(b'unknown',{'type':'identity_v1','version':1}),b'unknown')


class ZeroFeeBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.budget=SessionBudget(self.root);self.budget.init('ZERO',800,500,'synthetic_native_credit')
    def tearDown(self):self.tmp.cleanup()

    def reserve(self,call,event,revision):
        return self.budget.apply('ZERO',event,revision,'reserve',{'call_id':call,'task_id':'SIM-TASK',
            'upper_bound':10,'bound_evidence':'Synthetic fixture bounded fee'})
    def settle(self,call,event,revision,evidence='Synthetic explicit zero native bill'):
        return self.budget.apply('ZERO',event,revision,'settle',{'call_id':call,'actual_cost':0,'cost_evidence':evidence})

    def test_five_distinct_zero_fee_calls_are_recorded_without_inventing_spend(self):
        state=self.budget.read('ZERO')
        for number in range(5):
            call='CALL-'+str(number);state=self.reserve(call,'RES-'+str(number),state['revision'])
            state=self.settle(call,'COST-'+str(number),state['revision'])
        self.assertEqual(state['spent'],0)
        self.assertEqual(len(state['calls']),5);self.assertEqual(len(state['events']),10)
        self.assertTrue(all(c['status']=='settled' and c['actual_cost']==0 for c in state['calls'].values()))
        # This proves fixture-ledger bookkeeping only, not any real provider's zero fee.

    def test_same_call_settle_is_idempotent_not_repeated_new_cost_events(self):
        self.reserve('C1','R1',1);first=self.settle('C1','S1',2)
        self.assertEqual(self.settle('C1','S1',2),first)
        with self.assertRaisesRegex(ValueError,'unsettled'):
            self.settle('C1','S2',first['revision'])
        with self.assertRaisesRegex(ValueError,'already reserved'):
            self.reserve('C1','R2',first['revision'])
        self.assertEqual(self.budget.read('ZERO'),first)

    def test_zero_fee_still_needs_evidence_and_unknown_fee_does_not_settle(self):
        self.reserve('C1','R1',1)
        with self.assertRaisesRegex(ValueError,'unknown'):
            self.settle('C1','S1',2,evidence='')
        with self.assertRaisesRegex(ValueError,'unknown'):
            self.budget.apply('ZERO','UNKNOWN',2,'settle',{'call_id':'C1','actual_cost':None,'cost_evidence':'Display delayed'})
        self.assertEqual(self.budget.read('ZERO')['calls']['C1']['status'],'reserved')
        with self.assertRaisesRegex(ValueError,'unsettled'):
            self.reserve('C2','R2',2)


del AcceptedReleaseBindingTest
if __name__=='__main__':unittest.main()
