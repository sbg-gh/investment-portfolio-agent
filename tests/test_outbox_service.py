"""Authorized-boundary tests for per-user notification preferences and outbox isolation.

Written before implementation. Identity comes ONLY from the authenticated context; a
cross-user or forged context is denied. A failed preferences action rolls back and records
a metadata-only trace with no private payload. One user's outbox item never delivers to or
is readable by another user (no cross-user notification).
"""
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
import unittest
from portfolio_agent.auth import Authenticator
from portfolio_agent.core import FixtureValidator, Store, ValidatedSecurity
from portfolio_agent.notifier import LocalSinkNotifier
from portfolio_agent.services import PortfolioService

NOON = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)


class OutboxServiceTests(unittest.TestCase):
    def setUp(self):
        self.s = Store()
        self.addCleanup(self.s.close)
        for user in ('alice', 'bob'):
            self.s.create_user(user)
        self.auth = Authenticator(self.s)
        self.ctx = {u: self.auth.authenticate(self.auth.provision(u)) for u in ('alice', 'bob')}
        self.v = FixtureValidator({'DEMO': ValidatedSecurity('s0', 'DEMO', 'etf', 'US', 'synthetic')})
        self.app = PortfolioService(self.s, self.auth, self.v)

    def test_owner_sets_and_reads_preferences(self):
        self.app.set_preferences(
            self.ctx['alice'], quiet_start=22, quiet_end=7,
            materiality_threshold=Decimal('0.05'), cooldown_seconds=3600)
        prefs = self.app.preferences(self.ctx['alice'])
        self.assertEqual(prefs['quiet_start'], 22)
        self.assertEqual(Decimal(prefs['materiality_threshold']), Decimal('0.05'))
        self.assertEqual(prefs['cooldown_seconds'], 3600)

    def test_preferences_derive_user_from_context(self):
        # bob's context writes bob's prefs regardless of anything else.
        self.app.set_preferences(
            self.ctx['bob'], quiet_start=None, quiet_end=None,
            materiality_threshold=Decimal('0.2'), cooldown_seconds=0)
        self.assertEqual(
            Decimal(self.s.notification_preferences('bob')['materiality_threshold']),
            Decimal('0.2'))
        self.assertEqual(
            self.s.db.execute(
                "SELECT COUNT(*) FROM notification_preferences WHERE user_id='alice'").fetchone()[0],
            0)

    def test_forged_context_denied(self):
        forged = replace(self.ctx['bob'], user_id='alice')
        with self.assertRaises(PermissionError):
            self.app.set_preferences(
                forged, quiet_start=None, quiet_end=None,
                materiality_threshold=Decimal('0'), cooldown_seconds=0)
        self.assertEqual(
            self.s.db.execute('SELECT COUNT(*) FROM notification_preferences').fetchone()[0], 0)

    def test_failed_preferences_records_invalid_trace_without_payload(self):
        with self.assertRaises(ValueError):
            self.app.set_preferences(
                self.ctx['alice'], quiet_start=99, quiet_end=7,
                materiality_threshold=Decimal('0.05'), cooldown_seconds=3600)
        self.assertEqual(
            self.s.db.execute('SELECT COUNT(*) FROM notification_preferences').fetchone()[0], 0)
        traces = self.app.traces(self.ctx['alice'])
        outcomes = {r[2]: r[3] for r in traces}
        self.assertEqual(outcomes['set_preferences'], 'invalid')
        # The trace row is metadata-only: the only non-id/duration fields are the fixed
        # operation label, outcome and policy version. No preferences payload is carried.
        row = [t for t in traces if t[2] == 'set_preferences'][0]
        self.assertEqual(row[2], 'set_preferences')
        self.assertEqual(row[3], 'invalid')
        self.assertEqual(row[4], 'm1-v1')
        self.assertNotIn('0.05', str(traces))

    def test_no_cross_user_delivery_or_read(self):
        # Enqueue an item for alice directly (job runner path); bob must never see it.
        self.s.enqueue_notification(
            'alice', 'price-alert', dedup_key='k1', channel='local-sink', now=NOON)
        notifier = LocalSinkNotifier()
        self.s.deliver_pending(notifier, NOON)
        self.assertEqual([d[0] for d in notifier.deliveries], ['alice'])
        # bob cannot read alice's preferences through his own context.
        bob_prefs = self.app.preferences(self.ctx['bob'])
        self.assertEqual(
            Decimal(bob_prefs['materiality_threshold']), Decimal('0'))
        # bob's outbox (owner-scoped) is empty.
        self.assertEqual(
            self.s.db.execute(
                "SELECT COUNT(*) FROM notification_outbox WHERE user_id='bob'").fetchone()[0],
            0)


if __name__ == '__main__':
    unittest.main()
