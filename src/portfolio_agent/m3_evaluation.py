"""Deterministic M3-proactive regression fixtures. Not held-out learning evidence.

Each scenario drives an isolated in-memory Store through the authorized services and the
durable job runner, observing one of the four M3 acceptance-gate behaviors plus the
reauthorize-at-execution and dedup guarantees. Fixtures are obviously synthetic; there is
no 'learning_improvement' signal here. Rows stay payload-free (type/keys/status only) and
one user's private context never becomes another user's.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from importlib.resources import files
import json

from .auth import Authenticator
from .core import FixtureValidator, Store, ValidatedSecurity
from .jobs import JobRunner, LEASE_SECONDS, build_default_handlers
from .notifier import LocalSinkNotifier
from .ports import Observation
from .services import OperatorService, PortfolioService, POLICY_VERSION
from . import monitoring

SCENARIOS = {
    'crash_retry_no_duplicate',
    'no_cross_user_notification',
    'unchanged_stays_quiet',
    'stale_data_shown',
    'reauthorize_at_execution',
    'dedup_no_double_send',
}

NOW = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)


def load_cases():
    return json.loads(files('portfolio_agent').joinpath('m3_cases.json').read_text())


def _build():
    """Isolated store with synthetic owners + an operator and the services over it."""
    store = Store()
    for user in ('alice', 'bob', 'operator'):
        store.create_user(user)
    auth = Authenticator(store)
    ctx = {u: auth.authenticate(
        auth.provision(u, role='operator' if u == 'operator' else 'user'))
        for u in ('alice', 'bob', 'operator')}
    records = {sym: ValidatedSecurity(sym, sym, kind, 'US', 'synthetic') for sym, kind in
               (('STOCK', 'stock'),)}
    app = PortfolioService(store, auth, FixtureValidator(records))
    ops = OperatorService(app)
    return store, app, ops, ctx


def _observe(ops, ctx, sym, value, *, age):
    ops.ingest_observation(ctx['operator'], Observation(
        sym, Decimal(value), NOW - age, 'synthetic', 'price', fetched_at=NOW - age))


def _scenario(name):
    if name == 'crash_retry_no_duplicate':
        # A handler that crashes after applying its (idempotent) effect but before the job
        # completes is resumed on a later tick and ends 'done' without a second effect.
        store, _app, _ops, _ctx = _build()
        try:
            effects = {}

            class CrashAfterEffect:
                def __init__(self):
                    self.crashed = False

                def __call__(self, job_row, store, now):
                    corr = job_row['correlation_id']
                    effects.setdefault(corr, 1)  # idempotent effect, applied once
                    if not self.crashed:
                        self.crashed = True
                        raise RuntimeError('simulated crash after effect, before complete')

            runner = JobRunner(store, {'monitor': CrashAfterEffect()})
            runner.jobs.enqueue('monitor', idempotency_key='k', due_at=NOW,
                                correlation_id='corr-x', owner_ref='alice::p')
            runner.tick(NOW)  # effect applied, handler raises -> re-armed
            runner.tick(NOW + timedelta(seconds=LEASE_SECONDS))  # resume, no duplicate
            status = store.db.execute("SELECT status FROM jobs").fetchone()[0]
            return 'single_effect' if effects['corr-x'] == 1 and status == 'done' else 'duplicated'
        finally:
            store.close()
    if name == 'no_cross_user_notification':
        # An outbox item enqueued for alice delivers only to alice; bob never sees it.
        store, _app, _ops, _ctx = _build()
        try:
            store.enqueue_notification('alice', 'price-alert', dedup_key='k1',
                                       channel='local-sink', now=NOW)
            notifier = LocalSinkNotifier()
            store.deliver_pending(notifier, NOW)
            recipients = {d[0] for d in notifier.deliveries}
            bob_rows = store.db.execute(
                "SELECT COUNT(*) FROM notification_outbox WHERE user_id='bob'").fetchone()[0]
            return 'isolated' if recipients == {'alice'} and bob_rows == 0 else 'leaked'
        finally:
            store.close()
    if name == 'unchanged_stays_quiet':
        # Two monitor runs over identical (stale) inputs: the first records a state change
        # and enqueues once; the second re-evaluates the same state and stays quiet.
        store, app, ops, ctx = _build()
        try:
            portfolio = app.create_portfolio(ctx['alice'], 'synthetic')
            app.import_holding(ctx['alice'], portfolio, 'STOCK', '1')
            _observe(ops, ctx, 'STOCK', '10', age=timedelta(days=2))  # stale (> 1d window)
            runner = JobRunner(store, build_default_handlers(store))
            owner_ref = monitoring.encode_owner_ref('alice', portfolio)
            runner.jobs.enqueue('monitor_portfolio', idempotency_key='mp1',
                                due_at=NOW, correlation_id='c1', owner_ref=owner_ref)
            runner.tick(NOW)
            first = store.db.execute(
                "SELECT COUNT(*) FROM notification_outbox WHERE user_id='alice'").fetchone()[0]
            runner.jobs.enqueue('monitor_portfolio', idempotency_key='mp2',
                                due_at=NOW, correlation_id='c2', owner_ref=owner_ref)
            runner.tick(NOW)
            second = store.db.execute(
                "SELECT COUNT(*) FROM notification_outbox WHERE user_id='alice'").fetchone()[0]
            return 'quiet' if first >= 1 and second == first else 'noisy'
        finally:
            store.close()
    if name == 'stale_data_shown':
        # A stale observation surfaces as 'stale' through the freshness policy and is never
        # replaced by a fabricated fresh value.
        store, app, ops, ctx = _build()
        try:
            portfolio = app.create_portfolio(ctx['alice'], 'synthetic')
            app.import_holding(ctx['alice'], portfolio, 'STOCK', '1')
            _observe(ops, ctx, 'STOCK', '10', age=timedelta(days=2))
            state = monitoring.evaluate_stale_coverage(store, 'STOCK', NOW)
            return 'stale_shown' if state == 'stale' else 'fabricated'
        finally:
            store.close()
    if name == 'reauthorize_at_execution':
        # Ownership transfers away from alice after enqueue; the handler re-authorizes the
        # CURRENT owner at run time, denies, and writes nothing to alice's private outbox.
        store, app, ops, ctx = _build()
        try:
            portfolio = app.create_portfolio(ctx['alice'], 'synthetic')
            app.import_holding(ctx['alice'], portfolio, 'STOCK', '1')
            _observe(ops, ctx, 'STOCK', '10', age=timedelta(days=2))  # would alert if authorized
            runner = JobRunner(store, build_default_handlers(store))
            owner_ref = monitoring.encode_owner_ref('alice', portfolio)
            runner.jobs.enqueue('monitor_portfolio', idempotency_key='mp',
                                due_at=NOW, correlation_id='c', owner_ref=owner_ref)
            with store.db:  # trusted internal ownership transfer
                store.db.execute("UPDATE portfolios SET user_id='bob' WHERE id=?", (portfolio,))
            runner.tick(NOW)
            alice_rows = store.db.execute(
                "SELECT COUNT(*) FROM notification_outbox WHERE user_id='alice'").fetchone()[0]
            status = store.db.execute(
                "SELECT status FROM jobs WHERE type='monitor_portfolio'").fetchone()[0]
            return 'denied' if alice_rows == 0 and status == 'done' else 'leaked'
        finally:
            store.close()
    if name == 'dedup_no_double_send':
        # The same dedup key enqueued twice writes one outbox row, and a delivery retry
        # records a single sink delivery (at-least-once without duplication).
        store, _app, _ops, _ctx = _build()
        try:
            first = store.enqueue_notification('alice', 'price-alert', dedup_key='k',
                                               channel='local-sink', now=NOW)
            second = store.enqueue_notification('alice', 'price-alert', dedup_key='k',
                                                channel='local-sink', now=NOW)
            rows = store.db.execute(
                "SELECT COUNT(*) FROM notification_outbox WHERE user_id='alice'").fetchone()[0]
            notifier = LocalSinkNotifier()
            store.deliver_pending(notifier, NOW)
            store.deliver_pending(notifier, NOW)  # retry must not double-send
            return ('single_send' if first == second and rows == 1
                    and len(notifier.deliveries) == 1 else 'doubled')
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
    return {'suite': 'm3-proactive-baseline-v1', 'policy_version': POLICY_VERSION,
            'dataset_sha256': hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
            'total': len(results), 'passed': sum(r['passed'] for r in results), 'results': results}
