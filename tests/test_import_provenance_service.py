"""Authorized-boundary tests for M2 import provenance. Written before implementation.

Identity comes only from the authenticated context. Cross-user and forged-context access
is denied; traces stay private and carry operation names only.
"""
from dataclasses import replace
from datetime import datetime, timezone
import unittest
from portfolio_agent.auth import Authenticator
from portfolio_agent.core import FixtureValidator, Store, ValidatedSecurity
from portfolio_agent.services import PortfolioService

AS_OF = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)


class ImportProvenanceServiceTests(unittest.TestCase):
    def setUp(self):
        self.s = Store()
        self.addCleanup(self.s.close)
        for user in ('alice', 'bob', 'admin'):
            self.s.create_user(user)
        self.auth = Authenticator(self.s)
        self.ctx = {u: self.auth.authenticate(self.auth.provision(u, role='operator' if u == 'admin' else 'user'))
                    for u in ('alice', 'bob', 'admin')}
        self.v = FixtureValidator({'DEMO': ValidatedSecurity('s0', 'DEMO', 'etf', 'US', 'synthetic')})
        self.app = PortfolioService(self.s, self.auth, self.v)
        self.p = self.app.create_portfolio(self.ctx['alice'], 'private name')

    def test_owner_import_remove_and_private_receipts(self):
        sid = self.app.import_holding_with_provenance(
            self.ctx['alice'], self.p, 'DEMO', '2.5',
            source='fixture', market_as_of=AS_OF, idempotency_key='k1')
        self.assertEqual(sid, 's0')
        receipts = self.app.receipts(self.ctx['alice'], self.p)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0][5], 'upsert')
        self.app.remove_holding(self.ctx['alice'], self.p, 's0',
                                source='fixture', idempotency_key='rm1', market_as_of=AS_OF)
        self.assertEqual(self.app.holdings(self.ctx['alice'], self.p), [])
        self.assertEqual(len(self.app.receipts(self.ctx['alice'], self.p)), 2)

    def test_traces_are_private_and_have_no_payload(self):
        self.app.import_holding_with_provenance(
            self.ctx['alice'], self.p, 'DEMO', '2.5',
            source='sensitive-source', market_as_of=AS_OF, idempotency_key='k1')
        traces = self.app.traces(self.ctx['alice'])
        operations = {r[2] for r in traces}
        self.assertIn('import_holding_with_provenance', operations)
        self.assertNotIn('DEMO', str(traces))
        self.assertNotIn('2.5', str(traces))
        self.assertNotIn('sensitive-source', str(traces))

    def test_cross_user_denied_on_import_remove_and_receipts(self):
        for action in (
            lambda: self.app.import_holding_with_provenance(
                self.ctx['bob'], self.p, 'DEMO', '1', source='fixture', market_as_of=AS_OF, idempotency_key='k1'),
            lambda: self.app.remove_holding(
                self.ctx['bob'], self.p, 's0', source='fixture', idempotency_key='rm1', market_as_of=AS_OF),
            lambda: self.app.receipts(self.ctx['bob'], self.p),
        ):
            with self.assertRaises(PermissionError):
                action()
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM import_receipts').fetchone()[0], 0)

    def test_forged_context_denied(self):
        forged = replace(self.ctx['bob'], user_id='alice')
        with self.assertRaises(PermissionError):
            self.app.import_holding_with_provenance(
                forged, self.p, 'DEMO', '1', source='fixture', market_as_of=AS_OF, idempotency_key='k1')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM import_receipts').fetchone()[0], 0)

    def test_idempotent_retry_through_service(self):
        self.app.import_holding_with_provenance(
            self.ctx['alice'], self.p, 'DEMO', '2.5', source='fixture', market_as_of=AS_OF, idempotency_key='k1')
        self.app.import_holding_with_provenance(
            self.ctx['alice'], self.p, 'DEMO', '9', source='fixture', market_as_of=AS_OF, idempotency_key='k1')
        self.assertEqual(self.app.holdings(self.ctx['alice'], self.p), [('s0', '2.5')])
        self.assertEqual(len(self.app.receipts(self.ctx['alice'], self.p)), 1)

    def test_failed_action_rolls_back_and_records_nonsuccess_trace(self):
        with self.assertRaises(ValueError):
            self.app.import_holding_with_provenance(
                self.ctx['alice'], self.p, 'UNKNOWN', '1', source='fixture', market_as_of=AS_OF, idempotency_key='k1')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM import_receipts').fetchone()[0], 0)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM securities').fetchone()[0], 0)
        traces = self.app.traces(self.ctx['alice'])
        outcomes = {r[2]: r[3] for r in traces}
        self.assertEqual(outcomes['import_holding_with_provenance'], 'invalid')


if __name__ == '__main__':
    unittest.main()
