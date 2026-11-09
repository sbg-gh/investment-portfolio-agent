"""Deterministic M5a memory-substrate regression fixtures. NOT held-out learning evidence.

This sixth suite is a SCOPE/CAPTURE/RETRIEVAL correctness gate only. It is kept DISTINCT from
any held-out learning evaluation and makes NO learning/adaptation/improvement claim: there is
no `learning_improvement` key. The harness is fail-closed — mutating any expected answer drives
passed to 0 and changes the dataset hash. Fixtures are obviously synthetic.
"""
import unittest


class M5aEvalTests(unittest.TestCase):
    def test_baseline_contract_and_failure_detection(self):
        from portfolio_agent.m5a_evaluation import evaluate, load_cases, SCENARIOS
        cases = load_cases()
        report = evaluate(cases)
        self.assertEqual(report['suite'], 'm5a-memory-baseline-v1')
        self.assertGreaterEqual(report['total'], len(SCENARIOS))
        self.assertEqual(report['passed'], report['total'])
        self.assertEqual(len(report['dataset_sha256']), 64)
        self.assertEqual(len({r['id'] for r in report['results']}), report['total'])
        self.assertNotIn('learning_improvement', report)
        mutated = [{**c, 'expected': 'deliberately-wrong'} for c in cases]
        failure = evaluate(mutated)
        self.assertEqual(failure['passed'], 0)
        self.assertNotEqual(failure['dataset_sha256'], report['dataset_sha256'])

    def test_unknown_and_duplicate_cases_fail_closed(self):
        from portfolio_agent.m5a_evaluation import evaluate, load_cases
        with self.assertRaises(ValueError):
            evaluate([])
        with self.assertRaises(ValueError):
            evaluate([{'id': 'x', 'scenario': 'unknown', 'expected': 'ok'}])
        case = load_cases()[0]
        with self.assertRaises(ValueError):
            evaluate([case, case])

    def test_all_declared_scenarios_are_exercised(self):
        from portfolio_agent.m5a_evaluation import SCENARIOS, load_cases
        covered = {c['scenario'] for c in load_cases()}
        self.assertEqual(covered, SCENARIOS)


if __name__ == '__main__':
    unittest.main()
