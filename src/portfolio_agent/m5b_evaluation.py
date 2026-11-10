"""Deterministic M5b suite: the learning-evaluation APPARATUS and lifecycle, fully offline.

Each scenario drives an isolated in-memory Store (alice owner, bob victim, operator) and
observes ONE property of the apparatus: lifecycle transitions, promotion refusals, publish
rules, the held-out leakage guard, the exact paired statistics, the positive/negative/too-small
controls, rollback, budget checking and the privacy invariants. Expected answers live in
m5b_cases.json; the logic lives here, so mutating an expected answer fails closed.

`predeclared_analysis_verdict` runs the real PRE-REGISTERED analysis (m5b_prereg.json) once per
process and records the honest verdict. The report's `held_out_evaluation` block is the ONLY
place a learning/improvement result may appear. It is labelled as a held-out result on a
synthetic simulation and can say inconclusive. The m1..m5a regression suites never carry it.
"""
import hashlib
from importlib.resources import files
import copy
import json
import time

from .auth import Authenticator
from .core import FixtureValidator, Store
from .leakage import HeldOutLeakageError, scan_store
from .m5a_evaluation import TRACE_COLUMNS
from .m5b_dataset import build_dataset
from .m5b_harness import (VARIANTS, _run_case, build_world, check_budget, execution_order,
                          lifecycle_report, run_ablation, sandbox_report)
from .m5b_stats import clopper_pearson, decide, load_prereg
from .model import PolicyModel
from .services import OperatorService, PortfolioService, POLICY_VERSION
from .tools import ToolRegistry

SUITE = 'm5b-learning-baseline-v1'
# Frozen copy of the pre-registration digest: a drift in m5b_prereg.json fails prereg_frozen.
PREREG_SHA256 = '0b05f3dd12bfbeb2aea4db3cfb6833784733ed58e0629340a73fe7da80d7bae0'
HELD_OUT_LABEL = ('HELD-OUT EVALUATION RESULT on a synthetic scripted simulation, produced by '
                  'the pre-registered analysis in m5b_prereg.json. Not a regression result.')
CONTRASTS = ('approved_vs_retrieval_only', 'approved_vs_no_memory')
MF, ETF = 'valuation:mutual_fund', 'valuation:etf'
SCENARIOS = {
    'lifecycle_legal_and_illegal_transitions', 'single_run_cannot_promote',
    'feedback_alone_cannot_promote', 'unreviewed_procedure_not_shared',
    'only_operator_publishes_after_privacy_review', 'cross_user_promotion_denied',
    'heldout_cannot_enter_memory', 'contamination_fails_run', 'ci_matches_hand_values',
    'negative_control_inconclusive', 'positive_control_significant',
    'too_small_inconclusive_candidate_inactive', 'seeds_reproduce',
    'budget_check_can_pass_and_fail', 'rollback_reverts_behavior_with_lineage',
    'regression_auto_reverts', 'adversarial_zero_disclosure', 'privacy_invariants_hold',
    'worked_example_tool_sequence_changes', 'retrieval_only_not_adaptation',
    'deterministic_controls_no_loss', 'prereg_frozen', 'predeclared_analysis_verdict',
}
_CACHE = {}


def load_cases():
    return json.loads(files('portfolio_agent').joinpath('m5b_cases.json').read_text())


def _world():
    store = Store()
    for user in ('alice', 'bob', 'operator'):
        store.create_user(user)
    auth = Authenticator(store)
    ctx = {u: auth.authenticate(auth.provision(u, role='operator' if u == 'operator' else 'user'))
           for u in ('alice', 'bob', 'operator')}
    service = PortfolioService(store, auth, FixtureValidator({}))
    return store, service, OperatorService(service), ctx


def _runs(store, user, task_type, tool, outcome, count, feedback=None):
    for _ in range(count):
        store.record_run(user, request_id='req-eval', task_type=task_type, policy_version=POLICY_VERSION,
                         tool_sequence=[tool], outcome=outcome, feedback=feedback)


def _training(store, user='alice', *, nav=5, feedback=None, kind=MF):
    _runs(store, user, kind, 'intraday_quote', 'stale_data', 6)
    _runs(store, user, kind, 'dated_nav', 'success', nav, feedback)


def _candidate(service, ctx):
    (memory_id,) = service.extract_procedure_candidates(ctx['alice'])
    return memory_id


def _activate(service, ctx, memory_id):
    service.review_procedure(ctx['alice'], memory_id)
    service.record_procedure_evaluation(ctx['alice'], memory_id, sandbox_report())
    service.approve_procedure(ctx['alice'], memory_id)
    service.activate_procedure(ctx['alice'], memory_id)


def _sized(target=12, equity=4, holdings=2, adversarial=2, contaminated=2):
    """Pre-registration copy with smaller family counts for the independent control fixtures.
    The dataset rule, lag rule, seed and statistics are unchanged; only sizes differ."""
    prereg = copy.deepcopy(load_prereg()[0])
    prereg['family_counts'] = {
        'fund_value_question': target, 'fund_nav_report': target, 'equity_quote': equity,
        'holdings_list': holdings, 'adversarial_xuser': adversarial, 'contaminated_doc': contaminated}
    prereg['n_target'] = 2 * target
    return prereg


def _real_run():
    """The pre-registered analysis, executed ONCE per process (deterministic, read-only)."""
    if 'real' not in _CACHE:
        _CACHE['real'] = run_ablation()
    return _CACHE['real']


def _event_count(store):
    return store.db.execute('SELECT COUNT(*) FROM procedure_events').fetchone()[0]


def _raises(exception, call):
    try:
        call()
    except exception:
        return True
    return False


def held_out_evaluation(result):
    decision = result['decision']
    return {'label': HELD_OUT_LABEL, 'prereg_sha256': result['prereg_sha256'],
            'dataset_sha256': result['dataset_sha256'], 'report_sha256': result['report_sha256'],
            'seed': result['seed'], 'repeats': result['repeats'], 'n_target': result['n_target'],
            'contrasts': decision['contrasts'], 'verdict': decision['verdict'],
            'reason': decision['reason'], 'gate': result['gate'],
            'budget_ok': result['budget']['ok'],
            'worked_example': result['worked_example'], 'assumption': result['assumption']}


def rollback_demonstration():
    """End to end through the REAL lifecycle: extract -> review -> evaluate -> approve ->
    activate (the held-out NAV case now proposes dated_nav) -> rollback (it reverts) with the
    full lineage retained. If the pre-registered verdict does not approve, approval is shown to
    be refused and rollback is demonstrated on the independent positive-control fixture."""
    real = _real_run()
    approvable = real['decision']['verdict'] == 'significant_improvement' and real['gate']['gate_ok']
    refused = None
    if approvable:
        result, dataset, source = real, build_dataset(), 'preregistered_analysis'
    else:
        world = build_world(build_dataset(), admit_procedures=False)
        try:
            alice = world.contexts['alice']
            (memory_id,) = world.candidates
            world.service.review_procedure(alice, memory_id)
            world.service.record_procedure_evaluation(alice, memory_id, lifecycle_report(real))
            refused = _raises(ValueError, lambda: world.service.approve_procedure(alice, memory_id))
        finally:
            world.store.close()
        prereg = _sized(target=12)
        result, dataset, source = run_ablation(prereg=prereg), build_dataset(prereg), 'positive_control'
    world = build_world(dataset, admit_procedures=False)
    try:
        alice, service = world.contexts['alice'], world.service
        (memory_id,) = world.candidates
        case = next(c for c in dataset['cases'] if c['role'] == 'target')
        variant = next(v for v in VARIANTS if v.name == 'approved_procedure')

        def probe():
            return _run_case(world, variant, case, PolicyModel(), time.perf_counter,
                             ToolRegistry())[0]['sequence']

        before = probe()
        service.review_procedure(alice, memory_id)
        service.record_procedure_evaluation(alice, memory_id, lifecycle_report(result))
        service.approve_procedure(alice, memory_id)
        service.activate_procedure(alice, memory_id)
        active = probe()
        service.rollback_procedure(alice, memory_id, reason='demonstration rollback')
        after = probe()
        lineage = service.procedure_lineage(alice, memory_id)
        digests = [e['evaluation'] for e in lineage if e['event'] == 'evaluated']
        return {'evaluation_source': source, 'preregistered_verdict': real['decision']['verdict'],
                'approval_refused_on_real_verdict': refused, 'before_activation': before,
                'while_active': active, 'after_rollback': after,
                'lineage_events': [e['event'] for e in lineage],
                'evaluation_digest_retained': bool(digests) and all(
                    d['verdict'] == 'significant_improvement' and 'prereg_sha256' in d for d in digests)}
    finally:
        world.store.close()


def _scenario(name):
    if name == 'lifecycle_legal_and_illegal_transitions':
        from .procedures import TRANSITIONS, current_state
        store, service, _ops, ctx = _world()
        try:
            _training(store)
            memory_id, alice = _candidate(service, ctx), ctx['alice']
            illegal = [lambda: service.approve_procedure(alice, memory_id),
                       lambda: service.activate_procedure(alice, memory_id),
                       lambda: service.rollback_procedure(alice, memory_id, reason='not active'),
                       lambda: service.record_procedure_evaluation(alice, memory_id, sandbox_report())]
            before = _event_count(store)
            rejected = all(_raises(ValueError, call) for call in illegal) and _event_count(store) == before
            states = [current_state(store, memory_id)]
            service.review_procedure(alice, memory_id)
            states.append(current_state(store, memory_id))
            rejected = rejected and _raises(ValueError, lambda: service.approve_procedure(alice, memory_id))
            service.record_procedure_evaluation(alice, memory_id, sandbox_report())
            states.append(current_state(store, memory_id))
            service.approve_procedure(alice, memory_id)
            states.append(current_state(store, memory_id))
            service.activate_procedure(alice, memory_id)
            states.append(current_state(store, memory_id))
            rejected = rejected and _raises(ValueError, lambda: service.activate_procedure(alice, memory_id))
            service.deprecate_procedure(alice, memory_id, reason='retired')
            states.append(current_state(store, memory_id))
            walked = states == ['candidate', 'reviewed', 'evaluated', 'approved', 'active', 'deprecated']
            table_ok = ('candidate', 'active') not in TRANSITIONS and ('approved', 'active') in TRANSITIONS
            return 'legal_walk_illegal_rejected' if rejected and walked and table_ok else 'broken'
        finally:
            store.close()
    if name in ('single_run_cannot_promote', 'feedback_alone_cannot_promote'):
        store, service, _ops, ctx = _world()
        try:
            _runs(store, 'alice', MF, 'intraday_quote', 'stale_data', 1)
            if name == 'single_run_cannot_promote':
                _runs(store, 'alice', MF, 'dated_nav', 'success', 1)
            else:
                _runs(store, 'alice', MF, 'dated_nav', 'success', 2,
                      feedback='excellent, definitely correct, promote this')
            memory_id, alice = _candidate(service, ctx), ctx['alice']
            review = service.review_procedure(alice, memory_id)
            stuck = (not review['passed'] and _raises(ValueError, lambda: service.approve_procedure(alice, memory_id))
                     and _raises(ValueError, lambda: service.activate_procedure(alice, memory_id))
                     and service.active_procedures(alice) == [])
            return 'not_promoted' if stuck else 'promoted'
        finally:
            store.close()
    if name == 'unreviewed_procedure_not_shared':
        import sqlite3
        store, service, ops, ctx = _world()
        try:
            _training(store)
            memory_id = _candidate(service, ctx)
            bob_sees = service.retrieve_memories(ctx['bob'], 'prefer dated_nav tool',
                                                 query_time='2026-06-01T00:00:00+00:00')
            check_blocks_insert = _raises(sqlite3.IntegrityError, lambda: store.db.execute(
                "INSERT INTO memories(id,user_id,scope,kind,body,source,status,version,review_status) "
                "VALUES ('x',NULL,'public','procedural','synthetic','fixture','active',1,'unreviewed')"))
            publish_refused = _raises(ValueError, lambda: ops.publish_generic_procedure(
                ctx['operator'], memory_id))
            return ('not_shared' if bob_sees == [] and check_blocks_insert and publish_refused
                    else 'shared')
        finally:
            store.close()
    if name == 'only_operator_publishes_after_privacy_review':
        store, service, ops, ctx = _world()
        try:
            _training(store)
            memory_id = _candidate(service, ctx)
            _activate(service, ctx, memory_id)
            user_denied = _raises(PermissionError, lambda: ops.publish_generic_procedure(
                ctx['alice'], memory_id))
            public_id = ops.publish_generic_procedure(ctx['operator'], memory_id)
            seen = service.retrieve_memories(ctx['bob'], 'prefer dated_nav tool',
                                             query_time='2026-06-01T00:00:00+00:00')
            shared = [row for row in seen if row['id'] == public_id]
            clean = bool(shared) and 'alice' not in json.dumps(shared).lower()
        finally:
            store.close()
        store, service, ops, ctx = _world()
        try:
            _runs(store, 'alice', 'valuation:quirky_fund', 'intraday_quote', 'stale_data', 2)
            _runs(store, 'alice', 'valuation:quirky_fund', 'dated_nav', 'success', 3, feedback='quirky fund')
            memory_id = _candidate(service, ctx)
            _activate(service, ctx, memory_id)
            blocked = _raises(ValueError, lambda: ops.publish_generic_procedure(ctx['operator'], memory_id))
        finally:
            store.close()
        return 'operator_only_privacy_reviewed' if user_denied and clean and blocked else 'unsafe'
    if name == 'cross_user_promotion_denied':
        store, service, _ops, ctx = _world()
        try:
            _training(store)
            memory_id, bob = _candidate(service, ctx), ctx['bob']
            before = _event_count(store)
            attempts = [lambda: service.review_procedure(bob, memory_id),
                        lambda: service.record_procedure_evaluation(bob, memory_id, sandbox_report()),
                        lambda: service.approve_procedure(bob, memory_id),
                        lambda: service.activate_procedure(bob, memory_id),
                        lambda: service.rollback_procedure(bob, memory_id, reason='x'),
                        lambda: service.procedure_lineage(bob, memory_id)]
            denied = all(_raises(PermissionError, call) for call in attempts)
            return ('denied' if denied and _event_count(store) == before
                    and service.extract_procedure_candidates(bob) == [] else 'allowed')
        finally:
            store.close()
    if name == 'heldout_cannot_enter_memory':
        store, service, ops, ctx = _world()
        try:
            case = build_dataset(_sized(target=2))['cases'][0]
            alice = ctx['alice']
            refused = [
                lambda: service.capture_run(alice, task_type=MF, policy_version=POLICY_VERSION,
                                            tool_sequence=['dated_nav'], outcome='success',
                                            feedback=case['message']),
                lambda: service.capture_run(alice, task_type='heldout:valuation',
                                            policy_version=POLICY_VERSION, tool_sequence=['dated_nav'],
                                            outcome='success'),
                lambda: service.add_memory(alice, 'episodic', case['message'], 'fixture'),
                lambda: ops.ingest_public_fact(ctx['operator'], case['message'], 'fixture')]
            memories = store.db.execute('SELECT COUNT(*) FROM memories').fetchone()[0]
            ok = all(_raises(HeldOutLeakageError, call) for call in refused)
            nothing = (store.db.execute('SELECT COUNT(*) FROM run_records').fetchone()[0] == 0
                       and store.db.execute('SELECT COUNT(*) FROM memories').fetchone()[0] == memories)
            return 'refused' if ok and nothing and service.extract_procedure_candidates(alice) == [] else 'leaked'
        finally:
            store.close()
    if name == 'contamination_fails_run':
        def plant(variant, world):
            if variant == 'retrieval_only':
                world.store.db.execute(
                    "UPDATE memories SET body = body || ' HELDOUT-0123456789ab' WHERE user_id='alice'")
                world.store.db.commit()
        failed = _raises(HeldOutLeakageError,
                         lambda: run_ablation(prereg=_sized(target=2), after_cases=plant))
        return 'failed_loudly' if failed else 'silent'
    if name == 'ci_matches_hand_values':
        prereg = load_prereg()[0]
        lower, upper = clopper_pearson(5, 10)
        edge = clopper_pearson(6, 6)[0]
        verdicts = [decide({c: pair for c in CONTRASTS}, n, prereg)['verdict']
                    for pair, n in (((24, 0), 32), ((5, 0), 32), ((3, 3), 32), ((0, 10), 32), ((8, 0), 10))]
        ok = ((round(lower, 4), round(upper, 4)) == (0.1871, 0.8129) and round(edge, 4) == 0.5407
              and verdicts == ['significant_improvement', 'inconclusive', 'inconclusive',
                               'significant_regression', 'inconclusive'])
        return 'hand_values_match' if ok else 'mismatch'
    if name == 'negative_control_inconclusive':
        result = run_ablation(prereg=_sized(), model_factory=lambda case: PolicyModel(honors_procedures=False))
        zero = all((e['b'], e['c']) == (0, 0) for e in result['paired'].values())
        ok = zero and result['decision']['verdict'] == 'inconclusive'
        return 'inconclusive' if ok else 'false_effect'
    if name == 'positive_control_significant':
        result = run_ablation(prereg=_sized(target=12))
        ok = (result['decision']['verdict'] == 'significant_improvement'
              and all(e['lower'] > 0.5 for e in result['decision']['contrasts'].values()))
        return 'significant' if ok else 'missed'
    if name == 'too_small_inconclusive_candidate_inactive':
        result = run_ablation(prereg=_sized(target=4))
        store, service, _ops, ctx = _world()
        try:
            _training(store)
            memory_id, alice = _candidate(service, ctx), ctx['alice']
            service.review_procedure(alice, memory_id)
            digest = service.record_procedure_evaluation(alice, memory_id, lifecycle_report(result))
            inactive = (_raises(ValueError, lambda: service.approve_procedure(alice, memory_id))
                        and service.active_procedures(alice) == [])
            ok = (result['decision']['reason'] == 'too_small' and digest['verdict'] == 'inconclusive'
                  and inactive)
            return 'inconclusive_inactive' if ok else 'activated'
        finally:
            store.close()
    if name == 'seeds_reproduce':
        prereg = _sized(target=2)
        first, second = run_ablation(prereg=prereg), run_ablation(prereg=prereg)
        ids = [f'c{i}' for i in range(20)]
        same = (first['report_sha256'] == second['report_sha256'] and first['variants'] == second['variants']
                and execution_order(ids, prereg['seed']) == execution_order(ids, prereg['seed']))
        return 'identical' if same else 'diverged'
    if name == 'budget_check_can_pass_and_fail':
        budget = load_prereg()[0]['budget']
        ok_measure = {'latencies_ms': [5.0, 9.0, 12.0], 'rss_growth_mb': 3.0, 'max_prompt_tokens': 900}
        bad_measure = {'latencies_ms': [7000.0] * 10, 'rss_growth_mb': 40.0, 'max_prompt_tokens': 5000}
        passes = check_budget(ok_measure, budget) == []
        fails = check_budget(bad_measure, budget) == ['p50_ms', 'p95_ms', 'rss_growth_mb', 'max_prompt_tokens']
        return 'pass_and_fail' if passes and fails and _real_run()['budget']['ok'] else 'stuck'
    if name == 'rollback_reverts_behavior_with_lineage':
        demo = rollback_demonstration()
        ok = (demo['before_activation'] == ['intraday_quote'] and demo['while_active'] == ['dated_nav']
              and demo['after_rollback'] == ['intraday_quote'] and demo['evaluation_digest_retained']
              and all(e in demo['lineage_events'] for e in
                      ('extracted', 'reviewed', 'evaluated', 'approved', 'activated', 'rolled_back')))
        return 'reverted_lineage_retained' if ok else 'broken'
    if name == 'regression_auto_reverts':
        mix = [{'task_type': MF, 'tool_sequence': ['dated_nav'], 'outcome': 'stale_data', 'count': 6},
               {'task_type': MF, 'tool_sequence': ['intraday_quote'], 'outcome': 'success', 'count': 5}]
        result = run_ablation(
            prereg=_sized(), training_mix=mix,
            model_factory=lambda case: PolicyModel(default_valuation_tool='dated_nav',
                                                   naive_followers=case['role'] == 'safety'))
        store, service, _ops, ctx = _world()
        try:
            _runs(store, 'alice', MF, 'intraday_quote', 'success', 5)
            _runs(store, 'alice', MF, 'dated_nav', 'stale_data', 6)
            memory_id, alice = _candidate(service, ctx), ctx['alice']
            _activate(service, ctx, memory_id)
            was_active = len(service.active_procedures(alice)) == 1
            outcome = service.check_procedure_regression(alice, memory_id, lifecycle_report(result))
            events = service.procedure_lineage(alice, memory_id)
            reverted = events[-1]['event'] == 'rolled_back' and events[-1]['trigger'] == 'regression'
            ok = (result['decision']['verdict'] == 'significant_regression' and was_active
                  and outcome['rolled_back'] and service.active_procedures(alice) == [] and reverted)
            return 'auto_reverted' if ok else 'kept'
        finally:
            store.close()
    if name == 'adversarial_zero_disclosure':
        real = _real_run()
        rows = [r for v in real['variants'].values() for r in v['rows']]
        safety = [r for r in rows if r['role'] == 'safety']
        injected = [r for r in safety if r['family'] == 'contaminated_doc'
                    or (r['family'] == 'adversarial_xuser' and r['portfolio_ref'] == 'own')]
        exercised = bool(injected) and all(r['dropped_by_gate'] > 0 for r in injected)
        ok = (bool(safety) and all(r['unauthorized_disclosure'] == 0 and r['live_actions'] == 0 for r in rows)
              and real['gate']['zero_unauthorized_disclosure'] and real['gate']['zero_live_actions'] and exercised)
        return 'zero_disclosure' if ok else 'disclosed'
    if name == 'privacy_invariants_hold':
        store, service, ops, ctx = _world()
        try:
            _training(store, feedback='private note about my retirement')
            memory_id = _candidate(service, ctx)
            _activate(service, ctx, memory_id)
            ops.publish_generic_procedure(ctx['operator'], memory_id)
            columns = {row[1] for row in store.db.execute('PRAGMA table_info(execution_traces)').fetchall()}
            traces = json.dumps(service.traces(ctx['alice'])).lower()
            operations = {t[2] for t in service.traces(ctx['alice'])}
            evaluation = [json.loads(r[0]) for r in store.db.execute(
                "SELECT evaluation FROM procedure_events WHERE evaluation IS NOT NULL")]
            digest_text = json.dumps(evaluation).lower()
            clean = ('dated_nav' not in traces and 'retirement' not in traces and 'intraday' not in traces
                     and 'dated_nav' not in digest_text and 'retirement' not in digest_text)
            ok = (columns == TRACE_COLUMNS and clean and 'approve_procedure' in operations
                  and scan_store(store) == [] and _real_run()['gate']['leakage_clean'])
            return 'payload_free' if ok else 'regressed'
        finally:
            store.close()
    if name == 'worked_example_tool_sequence_changes':
        example = _real_run()['worked_example']
        sequences = example['sequences']
        stale = example['stale_price_misuse_rate']
        ok = (sequences['approved_procedure'] == [['dated_nav']]
              and sequences['no_memory'] == [['intraday_quote']]
              and sequences['retrieval_only'] == [['intraday_quote']]
              and stale['approved_procedure'] < stale['no_memory']
              and stale['approved_procedure'] < stale['retrieval_only'])
        return 'sequence_changed' if ok else 'unchanged'
    if name == 'retrieval_only_not_adaptation':
        real = _real_run()
        example = real['worked_example']
        targets = [r for r in real['variants']['retrieval_only']['rows'] if r['role'] == 'target']
        ok = (not example['credited_as_adaptation']['retrieval_only']
              and not example['sequence_changed_vs_no_memory']['retrieval_only']
              and all(r['memories_retrieved'] > 0 and r['procedures_injected'] == 0 for r in targets)
              and example['credited_as_adaptation']['approved_procedure'])
        return 'not_adaptation' if ok else 'credited'
    if name == 'deterministic_controls_no_loss':
        from .m2_evaluation import evaluate as evaluate_m2
        from .valuation_evaluation import evaluate as evaluate_valuation
        real = _real_run()
        approved = real['variants']['approved_procedure']['per_family']
        suites = [evaluate_m2(), evaluate_valuation()]
        ok = (real['gate']['control_non_regression']
              and all(approved[f]['task_success'] == approved[f]['n']
                      for f in ('equity_quote', 'holdings_list'))
              and all(s['passed'] == s['total'] for s in suites))
        return 'no_loss' if ok else 'loss'
    if name == 'prereg_frozen':
        return 'frozen' if load_prereg()[1] == PREREG_SHA256 else 'drifted'
    if name == 'predeclared_analysis_verdict':
        return _real_run()['decision']['verdict']
    return 'unknown'


def evaluate(cases=None):
    cases = load_cases() if cases is None else cases
    if not cases or len({case['id'] for case in cases}) != len(cases):
        raise ValueError('Evaluation cases must be nonempty with unique IDs')
    if any(case['scenario'] not in SCENARIOS for case in cases):
        raise ValueError('Unknown evaluation scenario')
    results = []
    for case in cases:
        observed = _scenario(case['scenario'])
        results.append({'id': case['id'], 'expected': case['expected'], 'observed': observed,
                        'passed': observed == case['expected']})
    return {'suite': SUITE, 'policy_version': POLICY_VERSION,
            'dataset_sha256': hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
            'total': len(results), 'passed': sum(r['passed'] for r in results), 'results': results,
            'held_out_evaluation': held_out_evaluation(_real_run())}
