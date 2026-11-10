"""Deterministic M5b suite: validates the learning-evaluation APPARATUS and lifecycle.

This seventh suite is the ONLY place a `held_out_evaluation` block exists. It is labelled as a
held-out result with its pre-registered analysis, and it can say inconclusive. The m1..m5a
regression suites must never gain a learning key. Mutating an expected answer fails closed.
"""
import contextlib
import io
import json
import unittest
from importlib.resources import files
from pathlib import Path
from unittest import mock

BASELINE = Path(__file__).resolve().parents[1] / 'evals' / 'baselines' / 'm5b-learning-v1.json'
LEARNING_KEYS = ('learning_improvement', 'held_out_evaluation')


class M5bEvalTests(unittest.TestCase):
    def test_baseline_contract_and_failure_detection(self):
        from portfolio_agent.m5b_evaluation import evaluate, load_cases, SCENARIOS
        cases = load_cases()
        report = evaluate(cases)
        self.assertEqual(report['suite'], 'm5b-learning-baseline-v1')
        self.assertGreaterEqual(report['total'], len(SCENARIOS))
        self.assertEqual(report['passed'], report['total'], [r for r in report['results'] if not r['passed']])
        self.assertEqual(len(report['dataset_sha256']), 64)
        self.assertEqual(len({r['id'] for r in report['results']}), report['total'])
        mutated = [{**c, 'expected': 'deliberately-wrong'} for c in cases]
        failure = evaluate(mutated)
        self.assertEqual(failure['passed'], 0)
        self.assertNotEqual(failure['dataset_sha256'], report['dataset_sha256'])

    def test_single_mutated_expected_answer_fails_only_that_case(self):
        from portfolio_agent.m5b_evaluation import evaluate, load_cases
        cases = load_cases()
        cases[-1] = {**cases[-1], 'expected': 'deliberately-wrong'}
        report = evaluate(cases)
        self.assertEqual(report['passed'], report['total'] - 1)

    def test_unknown_empty_and_duplicate_cases_fail_closed(self):
        from portfolio_agent.m5b_evaluation import evaluate, load_cases
        with self.assertRaises(ValueError):
            evaluate([])
        with self.assertRaises(ValueError):
            evaluate([{'id': 'x', 'scenario': 'unknown', 'expected': 'ok'}])
        case = load_cases()[0]
        with self.assertRaises(ValueError):
            evaluate([case, case])

    def test_all_declared_scenarios_are_exercised(self):
        from portfolio_agent.m5b_evaluation import SCENARIOS, load_cases
        self.assertEqual({c['scenario'] for c in load_cases()}, SCENARIOS)
        self.assertEqual(len(SCENARIOS), 23)

    def test_case_file_ships_as_package_data(self):
        text = files('portfolio_agent').joinpath('m5b_cases.json').read_text()
        self.assertIn('predeclared_analysis_verdict', text)
        self.assertIn('m5b_cases.json', (Path(__file__).resolve().parents[1] / 'pyproject.toml').read_text())


class HeldOutBlockTests(unittest.TestCase):
    def setUp(self):
        from portfolio_agent.m5b_evaluation import evaluate
        self.report = evaluate()
        self.block = self.report['held_out_evaluation']

    def test_block_is_labelled_and_carries_the_preregistered_analysis(self):
        from portfolio_agent.m5b_stats import load_prereg
        prereg, digest = load_prereg()
        block = self.block
        self.assertIn('held-out', block['label'].lower())
        self.assertEqual(block['prereg_sha256'], digest)
        self.assertRegex(block['dataset_sha256'], r'^[0-9a-f]{64}$')
        self.assertEqual(block['n_target'], prereg['n_target'])
        self.assertIn(block['verdict'], {'significant_improvement', 'inconclusive', 'significant_regression'})
        for name in ('approved_vs_retrieval_only', 'approved_vs_no_memory'):
            entry = block['contrasts'][name]
            for key in ('b', 'c', 'n', 'lower', 'upper', 'risk_difference'):
                self.assertIn(key, entry)
        self.assertIsInstance(block['budget_ok'], bool)
        self.assertIn('assumption', block)

    def test_block_has_no_wall_clock_numbers(self):
        text = json.dumps(self.report)
        for forbidden in ('latency_ms', 'p50_ms', 'p95_ms', 'rss_growth_mb', 'measured'):
            self.assertNotIn(forbidden, text)

    def test_report_is_deterministic(self):
        from portfolio_agent.m5b_evaluation import evaluate
        self.assertEqual(evaluate(), self.report)

    def test_regression_suites_never_gain_a_learning_key(self):
        from portfolio_agent.evaluation import evaluate as safety
        from portfolio_agent.import_evaluation import evaluate as imports
        from portfolio_agent.m2_evaluation import evaluate as m2
        from portfolio_agent.m3_evaluation import evaluate as m3
        from portfolio_agent.m4_evaluation import evaluate as m4
        from portfolio_agent.m5a_evaluation import evaluate as m5a
        for report in (safety(), imports(), m2(), m3(), m4(), m5a()):
            for key in LEARNING_KEYS:
                self.assertNotIn(key, report)
                self.assertNotIn(key, json.dumps(report))

    def test_only_the_m5b_report_has_the_block(self):
        self.assertIn('held_out_evaluation', self.report)
        self.assertNotIn('learning_improvement', self.report)


class FreezeAndBaselineTests(unittest.TestCase):
    def test_prereg_hash_is_frozen(self):
        from portfolio_agent.m5b_evaluation import PREREG_SHA256
        from portfolio_agent.m5b_stats import load_prereg
        self.assertEqual(load_prereg()[1], PREREG_SHA256)

    def test_baseline_records_the_current_preregistration_and_report(self):
        from portfolio_agent.m5b_evaluation import evaluate
        from portfolio_agent.m5b_stats import load_prereg
        baseline = json.loads(BASELINE.read_text())
        self.assertEqual(baseline['held_out_evaluation']['prereg_sha256'], load_prereg()[1])
        self.assertEqual(baseline['suite'], 'm5b-learning-baseline-v1')
        self.assertEqual(baseline['passed'], baseline['total'])
        # The analysis is deterministic: today's run must reproduce the recorded one exactly.
        self.assertEqual(evaluate(), baseline)

    def test_cli_eval_runs_eight_suites_and_fails_closed_on_a_mutation(self):
        from portfolio_agent import cli, m5b_evaluation
        out = io.StringIO()
        with mock.patch('sys.argv', ['portfolio_agent', 'eval']), contextlib.redirect_stdout(out):
            cli.main()
        reports = json.loads(out.getvalue())
        self.assertEqual(len(reports), 8)
        self.assertEqual(reports[6]['suite'], 'm5b-learning-baseline-v1')
        self.assertEqual(reports[7]['suite'], 'm6-paper-baseline-v1')
        self.assertTrue(all(r['passed'] == r['total'] for r in reports))
        cases = [{**c, 'expected': 'deliberately-wrong'} for c in m5b_evaluation.load_cases()]
        with mock.patch('sys.argv', ['portfolio_agent', 'eval']), \
                mock.patch.object(m5b_evaluation, 'load_cases', return_value=cases), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                cli.main()
        self.assertEqual(raised.exception.code, 1)


class RollbackDemonstrationTests(unittest.TestCase):
    def test_activate_changes_behaviour_rollback_reverts_and_lineage_is_retained(self):
        from portfolio_agent.m5b_evaluation import rollback_demonstration
        demo = rollback_demonstration()
        self.assertEqual(demo['before_activation'], ['intraday_quote'])
        self.assertEqual(demo['while_active'], ['dated_nav'])
        self.assertEqual(demo['after_rollback'], ['intraday_quote'])
        events = demo['lineage_events']
        for expected in ('extracted', 'reviewed', 'evaluated', 'approved', 'activated', 'rolled_back'):
            self.assertIn(expected, events)
        self.assertLess(events.index('activated'), events.index('rolled_back'))
        self.assertTrue(demo['evaluation_digest_retained'])
        self.assertEqual(demo['evaluation_source'], 'preregistered_analysis'
                         if demo['preregistered_verdict'] == 'significant_improvement' else 'positive_control')

    def test_demonstration_is_deterministic(self):
        from portfolio_agent.m5b_evaluation import rollback_demonstration
        self.assertEqual(rollback_demonstration(), rollback_demonstration())


if __name__ == '__main__':
    unittest.main()
