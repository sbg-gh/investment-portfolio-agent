"""Deterministic M2-completion regression fixtures. Not held-out learning evidence.

Exercises the four M2 acceptance-gate behaviors through the authorized services:
valid outside-universe accepted; unsupported/ambiguous import quarantined; the 76th
enhancement rejected (queued, not active); every held security retains baseline coverage;
financial fixtures reconcile deterministically. The harness is fail-closed: mutating any
expected answer drives passed to 0 and changes the dataset hash.
"""
import unittest


class M2EvalTests(unittest.TestCase):
    def test_baseline_contract_and_failure_detection(self):
        from portfolio_agent.m2_evaluation import evaluate, load_cases
        cases = load_cases()
        report = evaluate(cases)
        self.assertEqual(report['suite'], 'm2-completion-baseline-v1')
        self.assertGreaterEqual(report['total'], 5)
        self.assertEqual(report['passed'], report['total'])
        self.assertEqual(len(report['dataset_sha256']), 64)
        self.assertEqual(len({r['id'] for r in report['results']}), report['total'])
        self.assertNotIn('learning_improvement', report)
        mutated = [{**c, 'expected': 'deliberately-wrong'} for c in cases]
        failure = evaluate(mutated)
        self.assertEqual(failure['passed'], 0)
        self.assertNotEqual(failure['dataset_sha256'], report['dataset_sha256'])

    def test_unknown_and_duplicate_cases_fail_closed(self):
        from portfolio_agent.m2_evaluation import evaluate, load_cases
        with self.assertRaises(ValueError):
            evaluate([])
        with self.assertRaises(ValueError):
            evaluate([{'id': 'x', 'scenario': 'unknown', 'expected': 'ok'}])
        case = load_cases()[0]
        with self.assertRaises(ValueError):
            evaluate([case, case])

    def test_all_declared_scenarios_are_exercised(self):
        from portfolio_agent.m2_evaluation import SCENARIOS, load_cases
        covered = {c['scenario'] for c in load_cases()}
        self.assertEqual(covered, SCENARIOS)


if __name__ == '__main__':
    unittest.main()
