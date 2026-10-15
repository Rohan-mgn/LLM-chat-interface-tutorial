"""Prevent partial or incomparable benchmark artifacts from becoming passes."""
from copy import deepcopy
import unittest
from compare_storage_evaluations import compare,retrieval


class ComparisonTests(unittest.TestCase):
    def fixture(self):
        report={field:"same" for field in ("corpus_fingerprint","context_tokens","claim_review_mode","settings_fingerprint","processor","python","platform")}
        report["model_digests_at_start"]={"llama3.2:3b":"chat","embeddinggemma:latest":"embedding"}
        report["cases"]=[{"id":str(i),"family":"family","category":"direct","split":"held_out",
            "status":"completed","expected_files":["a","b"],"ranked_files":["a","b"],"hit_at_k":1} for i in range(40)]
        return report

    def test_missing_relevant_file_reduces_ndcg(self):
        result=retrieval({"expected_files":["a","b"],"ranked_files":["a"],"hit_at_k":1})
        self.assertLess(result["ndcg"],1)
        self.assertEqual(result["recall_at_k"],.5)

    def test_rejects_partial_runs_and_model_changes(self):
        baseline=self.fixture();candidate=deepcopy(baseline)
        candidate["cases"].pop()
        self.assertFalse(compare(baseline,candidate)["comparable"])
        candidate=deepcopy(baseline);candidate["model_digests_at_start"]["embeddinggemma:latest"]="changed"
        self.assertFalse(compare(baseline,candidate)["comparable"])

    def test_frozen_regression_tolerance_and_category_reporting(self):
        baseline=self.fixture();candidate=deepcopy(baseline)
        self.assertTrue(compare(baseline,candidate)["passed"])
        for case in candidate["cases"][:2]:
            case["ranked_files"]=[]
        result=compare(baseline,candidate)
        self.assertFalse(result["passed"])
        self.assertIn("direct",result["categories"])


if __name__=="__main__":
    unittest.main()
