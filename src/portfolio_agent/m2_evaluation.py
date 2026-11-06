"""Deterministic M2-completion regression fixtures. Not held-out learning evidence.

Each scenario drives an isolated in-memory Store through the authorized services and
observes one of the four M2 acceptance-gate behaviors plus financial reconciliation.
Fixtures are obviously synthetic; there is no 'learning_improvement' signal here.
"""
from datetime import datetime, timezone
import hashlib
from importlib.resources import files
import json
from .auth import Authenticator
from .core import (
    ADMISSION_QUEUE_CAPACITY, MONITORING_CAPACITY, FixtureValidator, Store, ValidatedSecurity)
from .services import OperatorService, PortfolioService, POLICY_VERSION

SCENARIOS = {
    'outside_universe_accepted',
    'ambiguous_import_quarantined',
    'enhancement_76th_rejected',
    'baseline_coverage_retained',
    'financial_fixtures_reconcile',
}

AS_OF = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)
OCCURRED = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)


def load_cases():
    return json.loads(files('portfolio_agent').joinpath('m2_cases.json').read_text())


def _build(validator):
    """Isolated store with a synthetic owner + operator and the services over it."""
    store = Store()
    store.create_user('fixture-user')
    store.create_user('fixture-operator')
    auth = Authenticator(store, clock=lambda: 1000.0)
    user_ctx = auth.authenticate(auth.provision('fixture-user'))
    operator_ctx = auth.authenticate(auth.provision('fixture-operator', role='operator'))
    app = PortfolioService(store, auth, validator)
    ops = OperatorService(app)
    return store, app, ops, user_ctx, operator_ctx


def _scenario(name):
    if name == 'outside_universe_accepted':
        # A valid security in neither research nor enhanced monitoring is still importable.
        validator = FixtureValidator({'OUT': ValidatedSecurity('out0', 'OUT', 'stock', 'US', 'synthetic')})
        store, app, _ops, ctx, _op = _build(validator)
        try:
            portfolio = app.create_portfolio(ctx, 'synthetic')
            security_id = app.import_holding(ctx, portfolio, 'OUT', '3')
            in_research = store.db.execute(
                "SELECT 1 FROM research WHERE security_id=?", (security_id,)).fetchone()
            in_monitoring = store.db.execute(
                "SELECT 1 FROM monitoring WHERE security_id=?", (security_id,)).fetchone()
            held = app.holdings(ctx, portfolio) == [('out0', '3')]
            return ('accepted' if held and in_research is None and in_monitoring is None
                    else 'rejected')
        finally:
            store.close()
    if name == 'ambiguous_import_quarantined':
        # An unknown/ambiguous symbol is never auto-invented: the import raises and no
        # holding or security is written (quarantined for reconciliation).
        validator = FixtureValidator({'KNOWN': ValidatedSecurity('k0', 'KNOWN', 'stock', 'US', 'synthetic')})
        store, app, _ops, ctx, _op = _build(validator)
        try:
            portfolio = app.create_portfolio(ctx, 'synthetic')
            try:
                app.import_holding(ctx, portfolio, 'GHOST', '1')
            except ValueError:
                no_holding = app.holdings(ctx, portfolio) == []
                no_security = store.db.execute("SELECT COUNT(*) FROM securities").fetchone()[0] == 0
                return 'quarantined' if no_holding and no_security else 'leaked'
            return 'invented'
        finally:
            store.close()
    if name == 'enhancement_76th_rejected':
        # The active tier admits MONITORING_CAPACITY distinct securities; the next distinct
        # one is queued (rejected from the active tier), never silently evicting an incumbent.
        store, app, ops, _ctx, op = _build(None)
        try:
            total = MONITORING_CAPACITY + 1
            for n in range(total):
                with store.db:
                    store.register(ValidatedSecurity(f'e{n}', f'E{n}', 'stock', 'US', 'synthetic'))
            for n in range(MONITORING_CAPACITY):
                if ops.propose_enhancement(op, f'e{n}', reason='synthetic') != 'admitted':
                    return 'misadmitted'
            state = ops.propose_enhancement(op, f'e{MONITORING_CAPACITY}', reason='synthetic')
            active = store.db.execute(
                "SELECT 1 FROM monitoring WHERE security_id=?", (f'e{MONITORING_CAPACITY}',)).fetchone()
            return 'queued' if state == 'queued' and active is None else 'admitted'
        finally:
            store.close()
    if name == 'baseline_coverage_retained':
        # A held outside-universe security retains baseline coverage even with the enhanced
        # tier (and its queue) full; coverage-set == held-set reconciles cleanly.
        validator = FixtureValidator({'HOLD': ValidatedSecurity('hold0', 'HOLD', 'etf', 'US', 'synthetic')})
        store, app, ops, ctx, op = _build(validator)
        try:
            fill = MONITORING_CAPACITY + ADMISSION_QUEUE_CAPACITY
            for n in range(fill):
                with store.db:
                    store.register(ValidatedSecurity(f'f{n}', f'F{n}', 'stock', 'US', 'synthetic'))
                ops.propose_enhancement(op, f'f{n}', reason='synthetic')
            portfolio = app.create_portfolio(ctx, 'synthetic')
            security_id = app.import_holding(ctx, portfolio, 'HOLD', '4')
            covered = store.baseline_coverage(security_id) == 1
            reconciled = store.reconcile_baseline_coverage() == []
            return 'covered' if covered and reconciled else 'uncovered'
        finally:
            store.close()
    if name == 'financial_fixtures_reconcile':
        # The signed ledger (buy +, sell -) sums to the holdings snapshot: reconciled=True.
        validator = FixtureValidator({'REC': ValidatedSecurity('rec0', 'REC', 'etf', 'US', 'synthetic')})
        store, app, _ops, ctx, _op = _build(validator)
        try:
            portfolio = app.create_portfolio(ctx, 'synthetic')
            app.import_holding(ctx, portfolio, 'REC', '7')
            app.record_transaction(ctx, portfolio, 'rec0', 'buy', '10',
                source='synthetic-feed', idempotency_key='b1', occurred_at=OCCURRED)
            app.record_transaction(ctx, portfolio, 'rec0', 'sell', '3',
                source='synthetic-feed', idempotency_key='s1', occurred_at=OCCURRED)
            rows = {r[0]: r[3] for r in app.reconcile(ctx, portfolio)}
            return 'reconciled' if rows.get('rec0') is True else 'diverged'
        finally:
            store.close()
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
    return {'suite': 'm2-completion-baseline-v1', 'policy_version': POLICY_VERSION,
            'dataset_sha256': hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
            'total': len(results), 'passed': sum(r['passed'] for r in results), 'results': results}
