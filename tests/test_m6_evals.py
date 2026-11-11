"""Deterministic M6 paper-simulation regression fixtures. Not held-out learning evidence.
Each scenario drives an isolated in-memory Store through the authorized services and observes
one paper-simulation correctness property (isolation, mode gate, rejections, reproducible fills,
dated NAV, fees, FIFO, reconciliation, idempotency, authorization, withheld totals). The harness
is fail-closed: mutating any expected answer drives passed to 0 and changes the dataset hash.
"""
import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest import mock

BASELINE = Path(__file__).resolve().parents[1] / 'evals' / 'baselines' / 'm6-paper-v1.json'


class M6EvalTests(unittest.TestCase):
    def test_baseline_contract_and_failure_detection(self):
        from portfolio_agent.m6_evaluation import evaluate, load_cases
        cases = load_cases()
        report = evaluate(cases)
        self.assertEqual(report['suite'], 'm6-paper-baseline-v1')
        self.assertGreaterEqual(report['total'], 14)
        self.assertEqual(report['passed'], report['total'])
        self.assertEqual(len(report['dataset_sha256']), 64)
        self.assertEqual(len({r['id'] for r in report['results']}), report['total'])
        self.assertNotIn('learning_improvement', report)
        self.assertNotIn('held_out_evaluation', report)
        mutated = [{**c, 'expected': 'deliberately-wrong'} for c in cases]
        failure = evaluate(mutated)
        self.assertEqual(failure['passed'], 0)
        self.assertNotEqual(failure['dataset_sha256'], report['dataset_sha256'])

    def test_unknown_and_duplicate_cases_fail_closed(self):
        from portfolio_agent.m6_evaluation import evaluate, load_cases
        with self.assertRaises(ValueError):
            evaluate([])
        with self.assertRaises(ValueError):
            evaluate([{'id': 'x', 'scenario': 'unknown', 'expected': 'ok'}])
        case = load_cases()[0]
        with self.assertRaises(ValueError):
            evaluate([case, case])

    def test_all_declared_scenarios_are_exercised(self):
        from portfolio_agent.m6_evaluation import SCENARIOS, load_cases
        covered = {c['scenario'] for c in load_cases()}
        self.assertEqual(covered, SCENARIOS)

    def test_baseline_file_equals_current_report(self):
        from portfolio_agent.m6_evaluation import evaluate
        self.assertEqual(evaluate(), json.loads(BASELINE.read_text()))

    def test_cli_eval_prints_m6_last_and_fails_closed_on_a_mutation(self):
        from portfolio_agent import cli, m6_evaluation
        out = io.StringIO()
        with mock.patch('sys.argv', ['portfolio_agent', 'eval']), contextlib.redirect_stdout(out):
            cli.main()
        reports = json.loads(out.getvalue())
        self.assertEqual(len(reports), 9)
        self.assertEqual(reports[7]['suite'], 'm6-paper-baseline-v1')
        self.assertTrue(all(r['passed'] == r['total'] for r in reports))
        cases = [{**c, 'expected': 'deliberately-wrong'} for c in m6_evaluation.load_cases()]
        with mock.patch('sys.argv', ['portfolio_agent', 'eval']), \
                mock.patch.object(m6_evaluation, 'load_cases', return_value=cases), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                cli.main()
        self.assertEqual(raised.exception.code, 1)


if __name__ == '__main__':
    unittest.main()
