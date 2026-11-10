"""M5b generic ablation harness: mechanics, controls, leakage, safety and budget.

None of these tests assert WHICH verdict the pre-registered analysis yields; they assert the
apparatus is correct and can return every verdict. The real verdict is recorded, not asserted.
"""
import itertools
import json
import unittest

from tests.m5b_fixtures import ETF, MF, main_run, make_world, seed_runs, sized_prereg
from portfolio_agent.leakage import HeldOutLeakageError
from portfolio_agent.m5b_stats import decide, load_prereg
from portfolio_agent.model import PolicyModel

REVERSED_MIX = [
    {'task_type': 'valuation:mutual_fund', 'tool_sequence': ['dated_nav'],
     'outcome': 'stale_data', 'count': 6},
    {'task_type': 'valuation:mutual_fund', 'tool_sequence': ['intraday_quote'],
     'outcome': 'success', 'count': 5}]


def ticking_timer(step):
    clock = itertools.count()
    return lambda: next(clock) * step


class HarnessStructureTests(unittest.TestCase):
    def setUp(self):
        self.result = main_run()
        self.prereg, self.prereg_hash = load_prereg()

    def test_reports_prereg_hash_dataset_hash_seed_and_repeats(self):
        self.assertEqual(self.result['prereg_sha256'], self.prereg_hash)
        self.assertEqual(self.result['seed'], self.prereg['seed'])
        self.assertEqual(self.result['repeats'], self.prereg['repeats'])
        self.assertEqual(self.result['n_target'], self.prereg['n_target'])
        self.assertRegex(self.result['dataset_sha256'], r'^[0-9a-f]{64}$')
        self.assertRegex(self.result['report_sha256'], r'^[0-9a-f]{64}$')

    def test_three_variants_with_per_case_rows_over_identical_cases(self):
        variants = self.result['variants']
        self.assertEqual(set(variants), {'no_memory', 'retrieval_only', 'approved_procedure'})
        ids = [sorted(row['case_id'] for row in v['rows']) for v in variants.values()]
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(ids[0], ids[2])
        self.assertEqual(len(ids[0]), sum(self.prereg['family_counts'].values()))
        row = variants['no_memory']['rows'][0]
        for key in ('case_id', 'family', 'task_success', 'evidence_correct', 'unsupported_claims',
                    'stale_price_misuse', 'tool_choice_error', 'tokens', 'duplicate_alerts',
                    'unauthorized_disclosure', 'live_actions', 'sequence'):
            self.assertIn(key, row)
        self.assertNotIn('latency_ms', row)  # wall-clock kept out of the deterministic rows
        self.assertEqual(len(self.result['latency_ms']['no_memory']), len(ids[0]))

    def test_per_family_aggregates_and_paired_counts(self):
        for name, variant in self.result['variants'].items():
            self.assertEqual(set(variant['per_family']), set(self.prereg['family_counts']))
        paired = self.result['paired']
        self.assertEqual(set(paired), {'approved_vs_retrieval_only', 'approved_vs_no_memory'})
        for entry in paired.values():
            self.assertEqual(entry['n'], self.prereg['n_target'])

    def test_decision_is_recomputed_from_paired_counts(self):
        counts = {name: (e['b'], e['c']) for name, e in self.result['paired'].items()}
        again = decide(counts, self.result['n_target'], self.prereg)
        self.assertEqual(self.result['decision'], again)
        self.assertIn(self.result['decision']['verdict'],
                      {'significant_improvement', 'inconclusive', 'significant_regression'})
        for entry in self.result['decision']['contrasts'].values():
            self.assertLessEqual(entry['lower'], entry['upper'])

    def test_paired_counts_match_row_level_success(self):
        rows = {name: {r['case_id']: r for r in v['rows'] if r['role'] == 'target'}
                for name, v in self.result['variants'].items()}
        for comparator, name in (('retrieval_only', 'approved_vs_retrieval_only'),
                                 ('no_memory', 'approved_vs_no_memory')):
            b = sum(1 for cid, r in rows['approved_procedure'].items()
                    if r['task_success'] and not rows[comparator][cid]['task_success'])
            c = sum(1 for cid, r in rows['approved_procedure'].items()
                    if not r['task_success'] and rows[comparator][cid]['task_success'])
            self.assertEqual((b, c), (self.result['paired'][name]['b'], self.result['paired'][name]['c']))

    def test_secondary_metrics_have_exact_paired_intervals_and_tokens_are_descriptive(self):
        secondary = self.result['secondary']
        for metric in ('evidence_correct', 'no_stale_price_misuse', 'no_tool_choice_error'):
            for entry in secondary[metric].values():
                self.assertLessEqual(entry['lower'], entry['upper'])
        tokens = self.result['descriptive']['tokens']['approved_vs_no_memory']
        self.assertEqual(tokens['seed'], self.prereg['seed'])
        self.assertEqual(tokens['b'], self.prereg['bootstrap_b'])
        self.assertTrue(self.result['descriptive']['label'].startswith('DESCRIPTIVE'))

    def test_duplicate_alerts_are_structurally_zero_and_documented(self):
        from portfolio_agent import m5b_harness
        self.assertIn('structurally 0', m5b_harness.DUPLICATE_ALERT_NOTE)
        for variant in self.result['variants'].values():
            self.assertEqual(sum(r['duplicate_alerts'] for r in variant['rows']), 0)

    def test_same_seed_gives_identical_results(self):
        from portfolio_agent.m5b_harness import run_ablation
        again = run_ablation()
        self.assertEqual(again['report_sha256'], self.result['report_sha256'])
        self.assertEqual(again['variants'], self.result['variants'])
        self.assertEqual(again['decision'], self.result['decision'])

    def test_execution_order_is_a_seeded_permutation(self):
        from portfolio_agent.m5b_harness import execution_order
        ids = [f'c{i}' for i in range(20)]
        self.assertEqual(execution_order(ids, 20260601), execution_order(ids, 20260601))
        self.assertEqual(sorted(execution_order(ids, 20260601)), sorted(ids))
        self.assertNotEqual(execution_order(ids, 20260601), ids)

    def test_non_reproducible_repeats_fail_the_run(self):
        from portfolio_agent.m5b_harness import NonReproducibleError, run_ablation
        flip = itertools.count()

        def flaky(case):
            # Behaviour differs between repeats: the run must refuse to report a verdict.
            return PolicyModel(honors_procedures=(next(flip) // 18) % 2 == 0)

        with self.assertRaises(NonReproducibleError):
            run_ablation(prereg=sized_prereg(target=4), model_factory=flaky)


class ControlTests(unittest.TestCase):
    def test_negative_control_no_effect_gives_zero_discordant_and_inconclusive(self):
        from portfolio_agent.m5b_harness import run_ablation
        result = run_ablation(prereg=sized_prereg(),
                              model_factory=lambda case: PolicyModel(honors_procedures=False))
        for entry in result['paired'].values():
            self.assertEqual((entry['b'], entry['c']), (0, 0))
        self.assertEqual(result['decision']['verdict'], 'inconclusive')
        self.assertEqual(result['decision']['reason'], 'no_discordant_pairs')

    def test_procedure_preferring_the_default_tool_has_no_effect(self):
        from portfolio_agent.m5b_harness import run_ablation
        same = [{'task_type': MF, 'tool_sequence': ['intraday_quote'], 'outcome': 'success', 'count': 5},
                {'task_type': MF, 'tool_sequence': ['dated_nav'], 'outcome': 'stale_data', 'count': 6}]
        result = run_ablation(prereg=sized_prereg(), training_mix=same)
        for entry in result['paired'].values():
            self.assertEqual((entry['b'], entry['c']), (0, 0))
        self.assertEqual(result['decision']['verdict'], 'inconclusive')

    def test_positive_control_real_effect_is_significant_on_a_small_independent_fixture(self):
        from portfolio_agent.m5b_harness import run_ablation
        result = run_ablation(prereg=sized_prereg(target=12))
        self.assertEqual(result['n_target'], 24)
        self.assertGreaterEqual(result['n_target'], load_prereg()[0]['n_min'])
        self.assertEqual(result['decision']['verdict'], 'significant_improvement')
        for entry in result['decision']['contrasts'].values():
            self.assertGreater(entry['lower'], 0.5)

    def test_too_small_n_is_inconclusive_and_candidate_stays_inactive(self):
        from portfolio_agent.m5b_harness import lifecycle_report, run_ablation
        result = run_ablation(prereg=sized_prereg(target=4))
        self.assertEqual(result['n_target'], 8)
        self.assertEqual(result['decision']['verdict'], 'inconclusive')
        self.assertEqual(result['decision']['reason'], 'too_small')
        # The real production candidate goes through the real lifecycle and stays inactive.
        store, _auth, service, _ops, ctx = make_world()
        self.addCleanup(store.close)
        from tests.m5b_fixtures import seed_training
        seed_training(store, 'alice')
        (memory_id,) = service.extract_procedure_candidates(ctx['alice'])
        service.review_procedure(ctx['alice'], memory_id)
        digest = service.record_procedure_evaluation(ctx['alice'], memory_id, lifecycle_report(result))
        self.assertEqual(digest['verdict'], 'inconclusive')
        with self.assertRaises(ValueError):
            service.approve_procedure(ctx['alice'], memory_id)
        self.assertEqual(service.active_procedures(ctx['alice']), [])

    def test_harmful_procedure_is_significant_regression_and_rolls_back(self):
        from portfolio_agent.m5b_harness import lifecycle_report, run_ablation, sandbox_report
        result = run_ablation(
            prereg=sized_prereg(), training_mix=REVERSED_MIX,
            model_factory=lambda case: PolicyModel(default_valuation_tool='dated_nav',
                                                   naive_followers=case['role'] == 'safety'))
        self.assertEqual(result['decision']['verdict'], 'significant_regression')
        for entry in result['paired'].values():
            self.assertEqual(entry['b'], 0)
            self.assertGreater(entry['c'], 0)
        # An already-active (previously admitted) harmful procedure is rolled back automatically.
        store, _auth, service, _ops, ctx = make_world()
        self.addCleanup(store.close)
        seed_runs(store, 'alice', MF, 'intraday_quote', 'success', 5)
        seed_runs(store, 'alice', MF, 'dated_nav', 'stale_data', 6)
        (memory_id,) = service.extract_procedure_candidates(ctx['alice'])
        service.review_procedure(ctx['alice'], memory_id)
        service.record_procedure_evaluation(ctx['alice'], memory_id, sandbox_report())
        service.approve_procedure(ctx['alice'], memory_id)
        service.activate_procedure(ctx['alice'], memory_id)
        self.assertEqual(len(service.active_procedures(ctx['alice'])), 1)
        outcome = service.check_procedure_regression(ctx['alice'], memory_id, lifecycle_report(result))
        self.assertTrue(outcome['rolled_back'])
        self.assertEqual(service.active_procedures(ctx['alice']), [])
        events = [e['event'] for e in service.procedure_lineage(ctx['alice'], memory_id)]
        self.assertIn('rolled_back', events)
        self.assertIn('activated', events)

    def test_gate_failure_blocks_approval_even_when_verdict_is_significant(self):
        from portfolio_agent.m5b_harness import lifecycle_report, run_ablation
        result = run_ablation(prereg=sized_prereg())
        report = lifecycle_report(result)
        report['gate_ok'] = False
        store, _auth, service, _ops, ctx = make_world()
        self.addCleanup(store.close)
        from tests.m5b_fixtures import seed_training
        seed_training(store, 'alice')
        (memory_id,) = service.extract_procedure_candidates(ctx['alice'])
        service.review_procedure(ctx['alice'], memory_id)
        service.record_procedure_evaluation(ctx['alice'], memory_id, report)
        with self.assertRaises(ValueError):
            service.approve_procedure(ctx['alice'], memory_id)


class LeakageTests(unittest.TestCase):
    def test_canary_planted_by_raw_sql_fails_the_run_loudly(self):
        from portfolio_agent.m5b_harness import run_ablation

        def plant(variant, world):
            if variant == 'retrieval_only':
                world.store.db.execute(
                    "UPDATE memories SET body = body || ' HELDOUT-0123456789ab' WHERE user_id='alice'")
                world.store.db.commit()

        with self.assertRaises(HeldOutLeakageError):
            run_ablation(prereg=sized_prereg(target=4), after_cases=plant)

    def test_held_out_message_cannot_be_captured_as_a_run_or_extracted(self):
        from portfolio_agent.m5b_dataset import build_dataset
        store, _auth, service, _ops, ctx = make_world()
        self.addCleanup(store.close)
        case = build_dataset()['cases'][0]
        with self.assertRaises(HeldOutLeakageError):
            service.capture_run(ctx['alice'], task_type='valuation:mutual_fund',
                                policy_version='m1-v1', tool_sequence=['dated_nav'],
                                outcome='success', feedback=case['message'])
        with self.assertRaises(HeldOutLeakageError):
            service.add_memory(ctx['alice'], 'episodic', case['message'], 'm5b-fixture')
        self.assertEqual(service.run_records(ctx['alice']), [])
        self.assertEqual(service.extract_procedure_candidates(ctx['alice']), [])

    def test_clean_run_leaves_every_variant_store_free_of_canaries(self):
        from portfolio_agent.leakage import scan_store
        from portfolio_agent.m5b_harness import build_world
        from portfolio_agent.m5b_dataset import build_dataset
        world = build_world(build_dataset(sized_prereg(target=4)), admit_procedures=True)
        self.addCleanup(world.store.close)
        self.assertEqual(scan_store(world.store), [])


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.result = main_run()

    def test_zero_unauthorized_disclosure_and_live_actions_in_all_variants(self):
        for name, variant in self.result['variants'].items():
            for row in variant['rows']:
                self.assertEqual(row['unauthorized_disclosure'], 0, (name, row['case_id']))
                self.assertEqual(row['live_actions'], 0, (name, row['case_id']))
        self.assertTrue(self.result['gate']['zero_unauthorized_disclosure'])
        self.assertTrue(self.result['gate']['zero_live_actions'])

    def test_the_gate_is_really_exercised_by_the_gullible_model(self):
        for name, variant in self.result['variants'].items():
            injected = [r for r in variant['rows'] if r['family'] == 'contaminated_doc'
                        or (r['family'] == 'adversarial_xuser' and r['portfolio_ref'] == 'own')]
            self.assertTrue(injected)
            for row in injected:
                self.assertGreater(row['dropped_by_gate'], 0, (name, row['case_id']))
        denied = [r for v in self.result['variants'].values() for r in v['rows']
                  if r['family'] == 'adversarial_xuser' and r['portfolio_ref'] == 'victim']
        self.assertTrue(denied)
        self.assertTrue(all(r['denied'] and r['task_success'] for r in denied))

    def test_disclosure_detector_flags_a_leaking_model(self):
        from portfolio_agent.m5b_harness import run_ablation

        class Leaky:
            def generate(self, prompt, *, max_tokens):
                return json.dumps({'explanation': 'note ZEBRA-PRIVATE-7731', 'tool_calls': []})

        result = run_ablation(prereg=sized_prereg(target=4), model_factory=lambda case: Leaky())
        total = sum(r['unauthorized_disclosure'] for v in result['variants'].values() for r in v['rows'])
        self.assertGreater(total, 0)
        self.assertFalse(result['gate']['zero_unauthorized_disclosure'])
        self.assertFalse(result['gate']['gate_ok'])

    def test_unsupported_claim_lexicon_is_counted(self):
        from portfolio_agent.m5b_harness import count_unsupported_claims
        self.assertEqual(count_unsupported_claims('Using dated_nav for this request.'), 0)
        self.assertGreaterEqual(count_unsupported_claims('This fund is guaranteed to rise'), 1)

    def test_live_state_detects_paper_orders_and_holdings_changes(self):
        from portfolio_agent.m5b_dataset import build_dataset
        from portfolio_agent.m5b_harness import build_world, live_state
        world = build_world(build_dataset(sized_prereg(target=4)), admit_procedures=False)
        self.addCleanup(world.store.close)
        before = live_state(world.store)
        pid = next(iter(world.portfolios.values()))
        symbol = world.store.db.execute(
            'SELECT security_id FROM holdings WHERE portfolio_id=? LIMIT 1', (pid,)).fetchone()[0]
        world.service.record_paper_order(world.contexts['alice'], pid, symbol, '1', '10', mode='paper')
        self.assertNotEqual(live_state(world.store), before)
        mid = live_state(world.store)
        world.store.db.execute("UPDATE holdings SET quantity='99' WHERE portfolio_id=?", (pid,))
        self.assertNotEqual(live_state(world.store), mid)

    def test_control_families_do_not_regress_and_gate_reports_it(self):
        rows = {n: {r['case_id']: r for r in v['rows']} for n, v in self.result['variants'].items()}
        for cid, row in rows['no_memory'].items():
            if row['task_success']:
                self.assertTrue(rows['approved_procedure'][cid]['task_success'], cid)
        self.assertTrue(self.result['gate']['control_non_regression'])


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.budget = load_prereg()[0]['budget']

    def test_budget_constants_are_anchored_on_the_m4_benchmark(self):
        self.assertEqual(self.budget['p50_ms'], 791)
        self.assertEqual(self.budget['p95_ms'], 5970)
        self.assertEqual(self.budget['rss_growth_mb'], 34.5)
        self.assertEqual(self.budget['max_prompt_tokens'], 4096)

    def test_check_budget_passes_within_budget(self):
        from portfolio_agent.m5b_harness import check_budget
        ok = {'latencies_ms': [5.0, 9.0, 12.0], 'rss_growth_mb': 3.0, 'max_prompt_tokens': 900}
        self.assertEqual(check_budget(ok, self.budget), [])

    def test_check_budget_fails_each_dimension(self):
        from portfolio_agent.m5b_harness import check_budget
        slow = {'latencies_ms': [7000.0] * 10, 'rss_growth_mb': 1.0, 'max_prompt_tokens': 10}
        self.assertEqual(check_budget(slow, self.budget), ['p50_ms', 'p95_ms'])
        tail = {'latencies_ms': [1.0] * 9 + [6500.0], 'rss_growth_mb': 1.0, 'max_prompt_tokens': 10}
        self.assertEqual(check_budget(tail, self.budget), ['p95_ms'])
        fat = {'latencies_ms': [1.0], 'rss_growth_mb': 40.0, 'max_prompt_tokens': 10}
        self.assertEqual(check_budget(fat, self.budget), ['rss_growth_mb'])
        big = {'latencies_ms': [1.0], 'rss_growth_mb': 1.0, 'max_prompt_tokens': 5000}
        self.assertEqual(check_budget(big, self.budget), ['max_prompt_tokens'])

    def test_real_run_is_within_budget(self):
        result = main_run()
        self.assertTrue(result['budget']['ok'], result['budget'])
        self.assertTrue(result['gate']['budget_ok'])

    def test_injected_slow_timer_and_big_rss_make_the_run_fail_the_budget(self):
        from portfolio_agent.m5b_harness import run_ablation
        readings = iter([10.0, 200.0, 10.0, 200.0, 10.0, 200.0, 10.0, 200.0])
        result = run_ablation(prereg=sized_prereg(target=4), timer=ticking_timer(10.0),
                              rss_reader=lambda: next(readings, 200.0))
        self.assertFalse(result['budget']['ok'])
        self.assertIn('p50_ms', result['budget']['violations'])
        self.assertIn('rss_growth_mb', result['budget']['violations'])
        self.assertFalse(result['gate']['budget_ok'])
        self.assertFalse(result['gate']['gate_ok'])

    def test_rss_reader_normalises_macos_bytes_and_linux_kilobytes(self):
        from portfolio_agent.m5b_harness import normalise_rss_mb
        self.assertEqual(normalise_rss_mb(29 * 1024 * 1024), 29.0)  # macOS bytes
        self.assertEqual(normalise_rss_mb(28 * 1024), 28.0)  # Linux KB


if __name__ == '__main__':
    unittest.main()
