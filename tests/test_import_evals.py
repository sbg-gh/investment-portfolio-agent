"""Deterministic import-provenance regression fixtures. Not held-out learning evidence."""
import unittest


class ImportEvalTests(unittest.TestCase):
    def test_baseline_contract_and_failure_detection(self):
        from portfolio_agent.import_evaluation import evaluate, load_cases
        cases = load_cases()
        report = evaluate(cases)
        self.assertGreaterEqual(report['total'], 4)
        self.assertEqual(report['passed'], report['total'])
        self.assertEqual(len(report['dataset_sha256']), 64)
        self.assertEqual(len({r['id'] for r in report['results']}), report['total'])
        self.assertNotIn('learning_improvement', report)
        mutated = [{**c, 'expected': 'deliberately-wrong'} for c in cases]
        failure = evaluate(mutated)
        self.assertEqual(failure['passed'], 0)
        self.assertNotEqual(failure['dataset_sha256'], report['dataset_sha256'])

    def test_unknown_and_duplicate_cases_fail_closed(self):
        from portfolio_agent.import_evaluation import evaluate, load_cases
        with self.assertRaises(ValueError):
            evaluate([])
        with self.assertRaises(ValueError):
            evaluate([{'id': 'x', 'scenario': 'unknown', 'expected': 'ok'}])
        case = load_cases()[0]
        with self.assertRaises(ValueError):
            evaluate([case, case])

    def test_all_declared_scenarios_are_exercised(self):
        from portfolio_agent.import_evaluation import SCENARIOS, load_cases
        covered = {c['scenario'] for c in load_cases()}
        self.assertEqual(covered, SCENARIOS)


if __name__ == '__main__':
    unittest.main()
