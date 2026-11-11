"""Deterministic web-security regression fixtures. Not held-out learning evidence.
Each scenario drives an isolated in-memory Store through the framework-neutral web core (no
network, no third-party package) and observes one security property: loopback bind, session
forgery/expiry/revocation, identity only from the session, operator gate, CSRF/Origin/Host,
throttling, denial parity, headers, log scrubbing, strict wire format, injection containment and
generic errors. The harness is fail-closed: mutating any expected answer drives passed to 0.
"""
import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest import mock

BASELINE = Path(__file__).resolve().parents[1] / 'evals' / 'baselines' / 'm7-web-security-v1.json'


class WebSecurityEvalTests(unittest.TestCase):
    def test_baseline_contract_and_failure_detection(self):
        from portfolio_agent.web_security_evaluation import evaluate, load_cases
        cases = load_cases()
        report = evaluate(cases)
        self.assertEqual(report['suite'], 'm7-web-security-baseline-v1')
        self.assertGreaterEqual(report['total'], 18)
        self.assertEqual(report['passed'], report['total'], [r for r in report['results'] if not r['passed']])
        self.assertEqual(len(report['dataset_sha256']), 64)
        self.assertEqual(len({r['id'] for r in report['results']}), report['total'])
        self.assertNotIn('learning_improvement', report)
        self.assertNotIn('held_out_evaluation', report)
        mutated = [{**c, 'expected': 'deliberately-wrong'} for c in cases]
        failure = evaluate(mutated)
        self.assertEqual(failure['passed'], 0)
        self.assertNotEqual(failure['dataset_sha256'], report['dataset_sha256'])

    def test_unknown_and_duplicate_cases_fail_closed(self):
        from portfolio_agent.web_security_evaluation import evaluate, load_cases
        with self.assertRaises(ValueError):
            evaluate([])
        with self.assertRaises(ValueError):
            evaluate([{'id': 'x', 'scenario': 'unknown', 'expected': 'ok'}])
        case = load_cases()[0]
        with self.assertRaises(ValueError):
            evaluate([case, case])

    def test_all_declared_scenarios_are_exercised(self):
        from portfolio_agent.web_security_evaluation import SCENARIOS, load_cases
        self.assertEqual({c['scenario'] for c in load_cases()}, SCENARIOS)

    def test_every_scenario_detects_its_own_failure(self):
        """Each scenario must be able to fail: a wrong expectation for one case fails only that case."""
        from portfolio_agent.web_security_evaluation import evaluate, load_cases
        cases = load_cases()
        for index in range(len(cases)):
            mutated = [dict(c) for c in cases]
            mutated[index]['expected'] = 'deliberately-wrong'
            report = evaluate(mutated)
            self.assertEqual(report['passed'], report['total'] - 1, cases[index]['scenario'])

    def test_baseline_file_equals_current_report(self):
        from portfolio_agent.web_security_evaluation import evaluate
        self.assertEqual(evaluate(), json.loads(BASELINE.read_text()))

    def test_cli_eval_prints_nine_suites_and_fails_closed_on_a_mutation(self):
        from portfolio_agent import cli, web_security_evaluation
        out = io.StringIO()
        with mock.patch('sys.argv', ['portfolio_agent', 'eval']), contextlib.redirect_stdout(out):
            cli.main()
        reports = json.loads(out.getvalue())
        self.assertEqual(len(reports), 9)
        self.assertEqual(reports[8]['suite'], 'm7-web-security-baseline-v1')
        self.assertTrue(all(r['passed'] == r['total'] for r in reports))
        cases = [{**c, 'expected': 'deliberately-wrong'} for c in web_security_evaluation.load_cases()]
        with mock.patch('sys.argv', ['portfolio_agent', 'eval']), \
                mock.patch.object(web_security_evaluation, 'load_cases', return_value=cases), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                cli.main()
        self.assertEqual(raised.exception.code, 1)


if __name__ == '__main__':
    unittest.main()
