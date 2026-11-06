"""Deterministic M4-chat regression fixtures. Not held-out learning evidence.

Exercises the M4 chat-boundary acceptance behaviors through the authorized stack over an
injectable ScriptedModel (fully offline, no network): an offline chat turn produces the
expected response shape; prompt injection via the user message, the assembled context data,
and a replayed tool result is each refused with nothing executed; an unauthorized portfolio
is denied; stale/missing evidence surfaces as such and is never fabricated; the bounded
context budget is enforced. The harness is fail-closed: mutating any expected answer drives
passed to 0 and changes the dataset hash. There is no 'learning_improvement' signal.
"""
import unittest


class M4EvalTests(unittest.TestCase):
    def test_baseline_contract_and_failure_detection(self):
        from portfolio_agent.m4_evaluation import evaluate, load_cases, SCENARIOS
        cases = load_cases()
        report = evaluate(cases)
        self.assertEqual(report['suite'], 'm4-chat-baseline-v1')
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
        from portfolio_agent.m4_evaluation import evaluate, load_cases
        with self.assertRaises(ValueError):
            evaluate([])
        with self.assertRaises(ValueError):
            evaluate([{'id': 'x', 'scenario': 'unknown', 'expected': 'ok'}])
        case = load_cases()[0]
        with self.assertRaises(ValueError):
            evaluate([case, case])

    def test_all_declared_scenarios_are_exercised(self):
        from portfolio_agent.m4_evaluation import SCENARIOS, load_cases
        covered = {c['scenario'] for c in load_cases()}
        self.assertEqual(covered, SCENARIOS)


if __name__ == '__main__':
    unittest.main()
