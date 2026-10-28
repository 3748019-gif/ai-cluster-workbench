import json
import tempfile
import unittest
from pathlib import Path
from cluster.core import Cluster, GuardError, digest


class AttachmentHandoffTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.c=Cluster(self.root,'test')
        self.task=self.c.create({'objective':'Rolling attachment fixture','task_type':'local','rules':['Validate originals without deleting them'],
            'acceptance':['Version and evidence required'],'review_required':False,
            'validators':[{'kind':'json','required_fields':['task_id']}]})['task_id']
    def tearDown(self): self.c.close();self.tmp.cleanup()
    def request(self,**kw):
        r={'url':'https://example.edu/catalog','version':'2026','scope':'Chemistry BS','expected_format':'json','requester':'kimi','purpose':'degree evidence'}
        r.update(kw);return self.c.request_attachment(self.task,r)
    def test_two_members_one_version_and_other_year_distinct(self):
        a=self.request();b=self.request(requester='workbuddy',purpose='independent applicability')
        self.assertEqual(a['request_id'],b['request_id']);self.assertEqual(len(b['subscriptions']),2)
        self.assertNotEqual(a['request_id'],self.request(version='2025')['request_id'])
        self.assertNotEqual(a['request_id'],self.request(scope='Chemistry BA')['request_id'])
    def test_partial_batch_rolling_receipt_no_fake_read(self):
        a=self.request();b=self.request(version='2025')
        p=self.root/'original.json';p.write_text('{"actual":1}');artifact=self.c.add_source(self.task,p,'original.json')
        with self.c._mutate(self.task) as m:self.c.artifact(m,artifact['artifact_id'])['acquisition_identity']={k:a[k] for k in ('url','version','scope','expected_format')}
        self.c.attachment_result(self.task,a['request_id'],{'status':'reused','path':str(p),'sha256':digest(p.read_bytes()),
            'provenance':{'task_id':self.task,'artifact_id':artifact['artifact_id']}})
        self.c.attachment_result(self.task,b['request_id'],{'status':'failed','reason':'Actual page refused access'})
        receipt=self.c.publish_attachment_receipt(self.task);data=json.loads(self.c.read_artifact(self.c.get(self.task),receipt['artifact_id']))
        self.assertEqual({x['status'] for x in data['items']},{'reused','failed'});self.assertEqual(data['consumption'],'unknown')
        self.assertFalse(data['items'][0]['current']['fact_verified'])
        with self.assertRaises(GuardError):self.c.attachment_result(self.task,b['request_id'],{'status':'reused','path':str(p),'sha256':digest(p.read_bytes()),
            'provenance':{'task_id':self.task,'artifact_id':artifact['artifact_id']}})
        with self.assertRaises(GuardError):self.c.acknowledge_receipt(self.task,receipt['artifact_id'],receipt['artifact_id'])
    def test_bad_original_and_missing_actual_completion_rejected(self):
        a=self.request(expected_format='pdf');p=self.root/'catalog.pdf';p.write_text('<html>Forbidden</html>')
        with self.assertRaises(GuardError):self.c.attachment_result(self.task,a['request_id'],{'status':'downloaded','path':str(p),'sha256':digest(p.read_bytes())})
        a=self.request();p=self.root/'same-name.json';p.write_text('{}')
        with self.assertRaises(GuardError):self.c.attachment_result(self.task,a['request_id'],{'status':'downloaded','path':str(p),'sha256':digest(p.read_bytes())})
        p.write_text('{')
        with self.assertRaises(ValueError):self.c.attachment_result(self.task,a['request_id'],{'status':'downloaded','path':str(p),'sha256':digest(p.read_bytes())})
    def test_uncertain_is_not_completed_or_auto_retry(self):
        a=self.request();self.c.attachment_result(self.task,a['request_id'],{'status':'uncertain','reason':'Download UI disappeared'})
        self.assertEqual(self.c.get(self.task)['attachment_queue'][a['request_id']]['status'],'uncertain')
    def test_decision_preserves_failed_attempt_and_explicit_replacement(self):
        a=self.request();b=self.request(scope='Different explicitly confirmed scope')
        self.c.attachment_result(self.task,a['request_id'],{'status':'failed','reason':'Initial identity incomplete'})
        with self.c._mutate(self.task) as m:m['attachment_queue'][a['request_id']]['handled_by_request']=b['request_id']
        r=next(x for x in self.c.decision_packet(self.task)['attachment_queue'] if x['request_id']==a['request_id'])
        self.assertEqual(r['status'],'failed');self.assertEqual(r['handled_by_request'],b['request_id'])
    def test_optional_denial_differs_from_user_stop_and_key_access(self):
        self.assertEqual(self.c.record_optional_action(self.task,'optional own-temp cleanup','denied')['decision'],'skip_optional_action')
        self.assertEqual(self.c.record_optional_action(self.task,'user canceled','user_stop')['decision'],'stop_affected_action')
        self.assertEqual(self.c.record_optional_action(self.task,'original inaccessible','denied',True)['decision'],'stop_affected_action')


class HandledWorkTest(unittest.TestCase):
    """T25 identity/state boundaries, not automatic business-scope matching."""
    setUp=AttachmentHandoffTest.setUp
    tearDown=AttachmentHandoffTest.tearDown

    def fixture(self,delivered=True):
        value={'objective':'Synthetic explicitly mapped maintenance work','task_type':'local',
               'rules':['Keep original history'],'acceptance':['Exact registered evidence'],
               'review_required':False,'validators':[{'kind':'json','required_fields':['task_id']}]}
        original=self.root/'original.txt';original.write_bytes(b'Historical original bytes, not a scope-equivalence answer.')
        old=self.c.create(value)['task_id'];self.c.add_source(old,original)
        self.c.fail(old,'FAILED','Synthetic historical failure retained')
        replacement=self.c.create(value)['task_id'];self.c.add_source(replacement,original)
        if delivered:
            self.c.package(replacement);self.c.start_local(replacement)
            output=self.root/'replacement.json';output.write_text(json.dumps({'task_id':replacement,'actual_scope':'synthetic explicitly declared scope'}))
            self.c.local_output(replacement,output);self.assertTrue(self.c.validate(replacement)['passed'])
            self.c.finalize(replacement);self.c.deliver(replacement)
        # Registered parent evidence, obtained through the public receipt API.
        # An empty synthetic receipt proves registration, not semantic coverage.
        receipt=self.c.publish_attachment_receipt(self.task)
        return old,replacement,receipt['artifact_id']

    def test_T25_append_relationship_without_rewriting_failed_history(self):
        old,replacement,evidence=self.fixture()
        old_manifest=self.c.get(old);old_events=self.c.events(old)
        old_bytes={a['artifact_id']:self.c.read_artifact(old_manifest,a['artifact_id']) for a in old_manifest['artifacts']}
        self.assertTrue(self.c.audit(replacement)['passed'])
        reason='Explicit fixture scope mapping by caller; no semantic equivalence inferred'
        record=self.c.record_handled_work(self.task,old,replacement,reason,[evidence])
        self.assertEqual(record['original_status'],'FAILED');self.assertEqual(record['handled_by'],replacement)
        self.assertEqual(record['evidence'],[evidence]);self.assertEqual(record['consumption'],'unknown')
        self.assertEqual(self.c.get(old),old_manifest);self.assertEqual(self.c.events(old),old_events)
        for aid,data in old_bytes.items():self.assertEqual(self.c.read_artifact(self.c.get(old),aid),data)
        parent=self.c.get(self.task);self.assertEqual(len(parent['handled_work']),1)
        receipt=self.c.artifact(parent,parent['handled_work'][0]['receipt_artifact'])
        self.assertEqual(receipt['role'],'evidence');self.assertEqual(receipt['input_artifacts'],[evidence])
        self.assertTrue(json.loads(self.c.read_artifact(parent,receipt['artifact_id']))['historical_state_unchanged'])

    def test_T25_undelivered_replacement_is_not_handled(self):
        old,replacement,evidence=self.fixture(delivered=False)
        before=self.c.get(self.task);old_before=self.c.get(old)
        with self.assertRaisesRegex(GuardError,'delivered and audited'):
            self.c.record_handled_work(self.task,old,replacement,'Explicit fixture mapping',[evidence])
        self.assertEqual(self.c.get(self.task),before);self.assertEqual(self.c.get(old),old_before)

    def test_T25_unknown_parent_evidence_and_unauditable_replacement_reject(self):
        old,replacement,evidence=self.fixture();before=self.c.get(self.task)
        with self.assertRaises(GuardError):
            self.c.record_handled_work(self.task,old,replacement,'Explicit fixture mapping',['UNKNOWN-PARENT-ARTIFACT'])
        self.assertEqual(self.c.get(self.task),before)
        newer=self.c.get(replacement);final=self.c.artifact(newer,newer['final_output'])
        (self.c.task_path(replacement)/final['filename']).write_bytes(b'Changed after delivered')
        self.assertFalse(self.c.audit(replacement)['passed'])
        with self.assertRaisesRegex(GuardError,'delivered and audited'):
            self.c.record_handled_work(self.task,old,replacement,'Explicit fixture mapping',[evidence])
        self.assertEqual(self.c.get(self.task),before);self.assertEqual(self.c.get(old)['status'],'FAILED')

if __name__=='__main__': unittest.main()
