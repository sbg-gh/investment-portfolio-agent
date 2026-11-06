"""Deterministic monitoring rules + public coalescing + proactive job handlers.

Written before implementation. The two evaluators read ONLY existing data (holdings,
baseline_coverage, market_observations); stale-coverage reuses valuation.FreshnessPolicy
semantics and NEVER fabricates a value. Public collection coalesces by
(security, source, time-bucket) and reveals no holder identity. The private
monitor_portfolio handler RE-AUTHORIZES the current owner of the job's owner_ref at
execution time and enqueues an owner-scoped outbox item only on a monitoring_state change;
unchanged inputs stay quiet. Timestamps are UTC ISO-8601; comparisons are Decimal.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from portfolio_agent.auth import Authenticator
from portfolio_agent.core import Store, FixtureValidator, ValidatedSecurity
from portfolio_agent.services import PortfolioService, OperatorService
from portfolio_agent.ports import Observation
from portfolio_agent.valuation import FreshnessPolicy
from portfolio_agent import monitoring
from portfolio_agent.jobs import JobRunner, build_default_handlers

NOW = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.s = Store()
        self.addCleanup(self.s.close)
        for user in ('alice', 'bob', 'admin'):
            self.s.create_user(user)
        self.auth = Authenticator(self.s)
        self.ctx = {u: self.auth.authenticate(
            self.auth.provision(u, role='operator' if u == 'admin' else 'user'))
            for u in ('alice', 'bob', 'admin')}
        records = {sym: ValidatedSecurity(sym, sym, kind, 'US', 'synthetic') for sym, kind in
                   (('STOCK', 'stock'), ('ETF', 'etf'), ('FUND', 'mutual_fund'))}
        self.app = PortfolioService(self.s, self.auth, FixtureValidator(records))
        self.ops = OperatorService(self.app)
        self.p = self.app.create_portfolio(self.ctx['alice'], 'private portfolio')
        for sym, qty in (('STOCK', '1.25'), ('ETF', '2.5'), ('FUND', '3')):
            self.app.import_holding(self.ctx['alice'], self.p, sym, qty)

    def observation(self, sym, value='10', *, age=timedelta(0), fetched_at=None, basis=None):
        return Observation(sym, Decimal(value), NOW - age, 'fixture',
                           basis or ('nav' if sym == 'FUND' else 'price'),
                           fetched_at=fetched_at or (NOW - age))

    def ingest(self, sym, **kwargs):
        return self.ops.ingest_observation(self.ctx['admin'], self.observation(sym, **kwargs))

    # --- stale / missing coverage (reuses FreshnessPolicy; never fabricated) --------
    def test_stale_and_missing_surface_via_freshness_policy(self):
        # STOCK observed 2 days ago (> 1d price window) -> stale; FUND never observed -> missing.
        self.ingest('STOCK', age=timedelta(days=2))
        self.assertEqual(
            monitoring.evaluate_stale_coverage(self.s, 'STOCK', NOW), 'stale')
        self.assertEqual(
            monitoring.evaluate_stale_coverage(self.s, 'FUND', NOW), 'missing')

    def test_fresh_within_policy_window(self):
        self.ingest('STOCK', age=timedelta(hours=1))
        self.assertEqual(
            monitoring.evaluate_stale_coverage(self.s, 'STOCK', NOW), 'fresh')

    def test_nav_uses_nav_window(self):
        # FUND observed 2 days ago: inside the 3-day nav window -> fresh (not stale).
        self.ingest('FUND', age=timedelta(days=2))
        self.assertEqual(
            monitoring.evaluate_stale_coverage(self.s, 'FUND', NOW), 'fresh')
        # Explicit tighter policy flips it to stale without fabricating a value.
        tight = FreshnessPolicy(nav_max_age=timedelta(days=1))
        self.assertEqual(
            monitoring.evaluate_stale_coverage(self.s, 'FUND', NOW, policy=tight), 'stale')

    # --- materiality crossing (deterministic Decimal) -------------------------------
    def test_materiality_crossing_and_within(self):
        # Two observations: 10 -> 12 is a 0.2 change ratio.
        self.ingest('STOCK', value='10', age=timedelta(hours=2))
        self.ingest('STOCK', value='12', age=timedelta(hours=1))
        state, ratio = monitoring.evaluate_materiality(self.s, 'STOCK', Decimal('0.1'))
        self.assertEqual(state, 'crossed')
        self.assertEqual(Decimal(ratio), Decimal('0.2'))
        state, ratio = monitoring.evaluate_materiality(self.s, 'STOCK', Decimal('0.5'))
        self.assertEqual(state, 'within')
        self.assertEqual(Decimal(ratio), Decimal('0.2'))

    def test_materiality_needs_two_observations(self):
        self.ingest('STOCK', value='10')
        state, ratio = monitoring.evaluate_materiality(self.s, 'STOCK', Decimal('0.1'))
        self.assertEqual(state, 'within')
        self.assertEqual(Decimal(ratio), Decimal('0'))

    # --- public coalescing (no holder identity) -------------------------------------
    def test_coalesce_public_collection_dedups_by_key(self):
        plan = monitoring.coalesce_public_collection(
            ['STOCK', 'STOCK', 'ETF'], 'fixture', 'bucket-1')
        self.assertEqual(plan, [('STOCK', 'fixture', 'bucket-1'), ('ETF', 'fixture', 'bucket-1')])
        # The plan carries no indication of which users hold the securities.
        flat = repr(plan)
        self.assertNotIn('alice', flat)
        self.assertNotIn('bob', flat)

    def test_public_collect_handler_runs_once_regardless_of_holders(self):
        # bob also holds STOCK; the public collect job for STOCK runs once, holder-blind.
        pb = self.app.create_portfolio(self.ctx['bob'], 'bobs portfolio')
        self.app.import_holding(self.ctx['bob'], pb, 'STOCK', '4')
        self.ingest('STOCK', value='10', age=timedelta(hours=2))
        self.ingest('STOCK', value='12', age=timedelta(hours=1))
        handlers = build_default_handlers(self.s)
        runner = JobRunner(self.s, handlers)
        jobs = runner.jobs
        jobs.enqueue('collect', idempotency_key='STOCK|fixture|b1', due_at=NOW,
                     correlation_id='c-collect', public_scope='STOCK|fixture|b1')
        claimed = runner.tick(NOW)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(self.s.db.execute(
            "SELECT status FROM jobs WHERE type='collect'").fetchone()[0], 'done')
        # No outbox row written by a public collect (no private data, no holder identity).
        self.assertEqual(self.s.db.execute(
            'SELECT COUNT(*) FROM notification_outbox').fetchone()[0], 0)

    # --- monitor handler: unchanged stays quiet -------------------------------------
    def _monitor_job(self, runner, now):
        owner_ref = monitoring.encode_owner_ref('alice', self.p)
        runner.jobs.enqueue('monitor_portfolio', idempotency_key=f'mp|{self.p}',
                            due_at=now, correlation_id='c-mon', owner_ref=owner_ref)
        return runner.tick(now)

    def test_unchanged_inputs_stay_quiet(self):
        self.ingest('STOCK', age=timedelta(days=2))  # stale -> a state change on first run
        handlers = build_default_handlers(self.s)
        runner = JobRunner(self.s, handlers)
        self._monitor_job(runner, NOW)
        first = self.s.db.execute(
            "SELECT COUNT(*) FROM notification_outbox WHERE user_id='alice'").fetchone()[0]
        self.assertGreaterEqual(first, 1)
        # Second run over identical inputs: monitoring_state already records the state,
        # so nothing new is enqueued.
        runner.jobs.enqueue('monitor_portfolio', idempotency_key=f'mp2|{self.p}',
                            due_at=NOW, correlation_id='c-mon2',
                            owner_ref=monitoring.encode_owner_ref('alice', self.p))
        runner.tick(NOW)
        second = self.s.db.execute(
            "SELECT COUNT(*) FROM notification_outbox WHERE user_id='alice'").fetchone()[0]
        self.assertEqual(second, first)

    # --- reauthorize at execution ---------------------------------------------------
    def test_reauthorize_at_execution_denies_stale_owner(self):
        self.ingest('STOCK', age=timedelta(days=2))  # would be a state change if authorized
        handlers = build_default_handlers(self.s)
        runner = JobRunner(self.s, handlers)
        owner_ref = monitoring.encode_owner_ref('alice', self.p)
        runner.jobs.enqueue('monitor_portfolio', idempotency_key=f'mp|{self.p}',
                            due_at=NOW, correlation_id='c-mon', owner_ref=owner_ref)
        # Ownership transfers away from alice via a trusted Store operation.
        with self.s.db:
            self.s.db.execute("UPDATE portfolios SET user_id='bob' WHERE id=?", (self.p,))
        runner.tick(NOW)
        # The handler re-authorizes the CURRENT owner, fails for alice, and no-ops: nothing
        # is written to alice's outbox and no monitoring_state is recorded by this run.
        self.assertEqual(self.s.db.execute(
            "SELECT COUNT(*) FROM notification_outbox WHERE user_id='alice'").fetchone()[0], 0)
        # The job still completes cleanly (no retry storm, no dead-letter).
        self.assertEqual(self.s.db.execute(
            "SELECT status FROM jobs WHERE type='monitor_portfolio'").fetchone()[0], 'done')

    def test_reauthorize_denial_writes_nothing_and_no_cross_user(self):
        self.ingest('STOCK', age=timedelta(days=2))
        handlers = build_default_handlers(self.s)
        runner = JobRunner(self.s, handlers)
        runner.jobs.enqueue('monitor_portfolio', idempotency_key=f'mp|{self.p}',
                            due_at=NOW, correlation_id='c-mon',
                            owner_ref=monitoring.encode_owner_ref('alice', self.p))
        with self.s.db:
            self.s.db.execute("UPDATE portfolios SET user_id='bob' WHERE id=?", (self.p,))
        runner.tick(NOW)
        # No notification leaks to bob either (the handler no-ops on the stale owner_ref).
        self.assertEqual(self.s.db.execute(
            "SELECT COUNT(*) FROM notification_outbox WHERE user_id='bob'").fetchone()[0], 0)

    # --- dedup / retry does not double-notify ---------------------------------------
    def test_retry_does_not_double_notify(self):
        self.ingest('STOCK', age=timedelta(days=2))
        handlers = build_default_handlers(self.s)
        runner = JobRunner(self.s, handlers)
        owner_ref = monitoring.encode_owner_ref('alice', self.p)
        runner.jobs.enqueue('monitor_portfolio', idempotency_key=f'mp|{self.p}',
                            due_at=NOW, correlation_id='c-mon', owner_ref=owner_ref)
        runner.tick(NOW)
        count = self.s.db.execute(
            "SELECT COUNT(*) FROM notification_outbox WHERE user_id='alice'").fetchone()[0]
        # A fresh monitor job over the SAME unchanged state enqueues nothing new
        # (deterministic dedup_key + unchanged monitoring_state).
        runner.jobs.enqueue('monitor_portfolio', idempotency_key=f'mp-retry|{self.p}',
                            due_at=NOW, correlation_id='c-mon-retry', owner_ref=owner_ref)
        runner.tick(NOW)
        self.assertEqual(self.s.db.execute(
            "SELECT COUNT(*) FROM notification_outbox WHERE user_id='alice'").fetchone()[0], count)


if __name__ == '__main__':
    unittest.main()
