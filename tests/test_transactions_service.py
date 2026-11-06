"""Authorized-boundary tests for the M2 transaction ledger. Written before implementation.

Identity comes only from the authenticated context. Cross-user and forged-context access
is denied; a failed action rolls back and records a metadata-only trace with no payload.
"""
from dataclasses import replace
from datetime import datetime, timezone
import unittest
from portfolio_agent.auth import Authenticator
from portfolio_agent.core import FixtureValidator, Store, ValidatedSecurity
from portfolio_agent.services import PortfolioService

AS_OF = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)
OCCURRED = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)


class TransactionServiceTests(unittest.TestCase):
    def setUp(self):
        self.s = Store()
        self.addCleanup(self.s.close)
        for user in ('alice', 'bob'):
            self.s.create_user(user)
        self.auth = Authenticator(self.s)
        self.ctx = {u: self.auth.authenticate(self.auth.provision(u)) for u in ('alice', 'bob')}
        self.v = FixtureValidator({'DEMO': ValidatedSecurity('s0', 'DEMO', 'etf', 'US', 'synthetic')})
        self.app = PortfolioService(self.s, self.auth, self.v)
        self.p = self.app.create_portfolio(self.ctx['alice'], 'private name')
        self.app.import_holding_with_provenance(
            self.ctx['alice'], self.p, 'DEMO', '2.5',
            source='fixture', market_as_of=AS_OF, idempotency_key='imp1')

    def test_owner_record_read_and_reconcile(self):
        tid = self.app.record_transaction(
            self.ctx['alice'], self.p, 's0', 'buy', '2.5',
            source='fixture', idempotency_key='t1', occurred_at=OCCURRED)
        self.assertTrue(tid)
        rows = self.app.transactions(self.ctx['alice'], self.p)
        self.assertEqual(len(rows), 1)
        reconciled = {r[0]: r[3] for r in self.app.reconcile(self.ctx['alice'], self.p)}
        self.assertTrue(reconciled['s0'])

    def test_cross_user_denied(self):
        with self.assertRaises(PermissionError):
            self.app.record_transaction(
                self.ctx['bob'], self.p, 's0', 'buy', '1',
                source='fixture', idempotency_key='t1', occurred_at=OCCURRED)
        with self.assertRaises(PermissionError):
            self.app.transactions(self.ctx['bob'], self.p)
        with self.assertRaises(PermissionError):
            self.app.reconcile(self.ctx['bob'], self.p)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0], 0)

    def test_forged_context_denied(self):
        forged = replace(self.ctx['bob'], user_id='alice')
        with self.assertRaises(PermissionError):
            self.app.record_transaction(
                forged, self.p, 's0', 'buy', '1',
                source='fixture', idempotency_key='t1', occurred_at=OCCURRED)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0], 0)

    def test_idempotent_retry_through_service(self):
        first = self.app.record_transaction(
            self.ctx['alice'], self.p, 's0', 'buy', '2.5',
            source='fixture', idempotency_key='t1', occurred_at=OCCURRED)
        second = self.app.record_transaction(
            self.ctx['alice'], self.p, 's0', 'buy', '9',
            source='fixture', idempotency_key='t1', occurred_at=OCCURRED)
        self.assertEqual(first, second)
        self.assertEqual(len(self.app.transactions(self.ctx['alice'], self.p)), 1)

    def test_failed_action_rolls_back_and_records_invalid_trace(self):
        with self.assertRaises(ValueError):
            self.app.record_transaction(
                self.ctx['alice'], self.p, 's0', 'short', '2.5',
                source='sensitive-source', idempotency_key='t1', occurred_at=OCCURRED)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0], 0)
        traces = self.app.traces(self.ctx['alice'])
        outcomes = {r[2]: r[3] for r in traces}
        self.assertEqual(outcomes['record_transaction'], 'invalid')
        self.assertNotIn('short', str(traces))
        self.assertNotIn('2.5', str(traces))
        self.assertNotIn('sensitive-source', str(traces))


if __name__ == '__main__':
    unittest.main()
