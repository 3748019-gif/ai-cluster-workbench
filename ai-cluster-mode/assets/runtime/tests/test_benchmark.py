import tempfile
import json
import unittest
from pathlib import Path
from cluster.benchmark import make_fixtures, aggregate, score_faults

class BenchmarkTest(unittest.TestCase):
    def test_fixture_coverage_and_no_fabricated_results(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(make_fixtures(d)['cases'],20)
            rows=json.loads((Path(d)/'results_template.json').read_text())
            self.assertEqual(len(rows),80);self.assertTrue(all(r['status']=='not_run' for r in rows))
            self.assertEqual(aggregate(rows)['status'],'insufficient_evidence')
    def test_fault_scoring_counts_precision_and_recall(self):
        truth={'errors':[{'error_id':'F1'},{'error_id':'F2'}]};review={'confirmed_errors':[{'id':'a'},{'id':'b'}],'unverified':['Scope']}
        result=score_faults(truth,review,{'a':'F1','b':'false_positive'})
        self.assertEqual(result['recall'],0.5);self.assertEqual(result['precision'],0.5)
        with self.assertRaises(ValueError):score_faults(truth,review,{'a':'F1'})
    def test_no_findings_precision_undefined(self):
        r=score_faults({'errors':[{'error_id':'F1'}]},{'confirmed_errors':[],'unverified':[]},{})
        self.assertEqual(r['recall'],0);self.assertIsNone(r['precision'])
    def test_no_completed_record_without_evidence(self):
        with self.assertRaises(ValueError):aggregate([{'status':'completed','task_id':None}])
