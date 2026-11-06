import unittest

class EvaluationTests(unittest.TestCase):
    def test_baseline_contract_and_failure_detection(self):
        from portfolio_agent.evaluation import evaluate, load_cases
        cases=load_cases()
        report=evaluate(cases)
        self.assertGreaterEqual(report['total'],6)
        self.assertEqual(report['passed'],report['total'])
        self.assertEqual(report['policy_version'],'m1-v1')
        self.assertEqual(len(report['dataset_sha256']),64)
        self.assertEqual(len({r['id'] for r in report['results']}),report['total'])
        self.assertNotIn('learning_improvement',report)
        mutated=[{**c,'expected':'deliberately-wrong'} for c in cases]
        failure=evaluate(mutated)
        self.assertEqual(failure['passed'],0)
        self.assertNotEqual(failure['dataset_sha256'],report['dataset_sha256'])

    def test_unknown_and_duplicate_cases_fail_closed(self):
        from portfolio_agent.evaluation import evaluate, load_cases
        with self.assertRaises(ValueError):evaluate([])
        with self.assertRaises(ValueError):evaluate([{'id':'x','scenario':'unknown','expected':'success'}])
        case=load_cases()[0]
        with self.assertRaises(ValueError):evaluate([case,case])
