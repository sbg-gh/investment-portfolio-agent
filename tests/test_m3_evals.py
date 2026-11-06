"""Deterministic M3-proactive regression fixtures. Not held-out learning evidence.

Exercises the four M3 acceptance-gate behaviors through the authorized stack plus the
reauthorize-at-execution and dedup guarantees: a crashed/resumed job applies its effect
once; an outbox item for user A never reaches user B; two monitor runs over identical
inputs produce one notification; a stale observation surfaces as stale and is never
fabricated; a job whose owner changed after enqueue does not act on the old private data;
a retried job never double-sends. The harness is fail-closed: mutating any expected answer
drives passed to 0 and changes the dataset hash. There is no 'learning_improvement' signal.
"""
import unittest


class M3EvalTests(unittest.TestCase):
    def test_baseline_contract_and_failure_detection(self):
        from portfolio_agent.m3_evaluation import evaluate, load_cases
        cases = load_cases()
        report = evaluate(cases)
        self.assertEqual(report['suite'], 'm3-proactive-baseline-v1')
        self.assertGreaterEqual(report['total'], 6)
        self.assertEqual(report['passed'], report['total'])
        self.assertEqual(len(report['dataset_sha256']), 64)
        self.assertEqual(len({r['id'] for r in report['results']}), report['total'])
        self.assertNotIn('learning_improvement', report)
        mutated = [{**c, 'expected': 'deliberately-wrong'} for c in cases]
        failure = evaluate(mutated)
        self.assertEqual(failure['passed'], 0)
        self.assertNotEqual(failure['dataset_sha256'], report['dataset_sha256'])

    def test_unknown_and_duplicate_cases_fail_closed(self):
        from portfolio_agent.m3_evaluation import evaluate, load_cases
        with self.assertRaises(ValueError):
            evaluate([])
        with self.assertRaises(ValueError):
            evaluate([{'id': 'x', 'scenario': 'unknown', 'expected': 'ok'}])
        case = load_cases()[0]
        with self.assertRaises(ValueError):
            evaluate([case, case])

    def test_all_declared_scenarios_are_exercised(self):
        from portfolio_agent.m3_evaluation import SCENARIOS, load_cases
        covered = {c['scenario'] for c in load_cases()}
        self.assertEqual(covered, SCENARIOS)


if __name__ == '__main__':
    unittest.main()
