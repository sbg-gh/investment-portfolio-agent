"""Store-level deterministic baseline-coverage bookkeeping. Written before implementation.

Every security held by at least one portfolio retains baseline coverage, independent of the
enhanced-monitoring tier. Coverage is maintained atomically with holding import/removal and
reconcile_baseline_coverage asserts covered-set == distinct-held-securities-set.
"""
from datetime import datetime, timezone
import unittest
from portfolio_agent.core import FixtureValidator, Store, ValidatedSecurity

AS_OF = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)


class BaselineCoverageTests(unittest.TestCase):
    def setUp(self):
        self.s = Store()
        self.addCleanup(self.s.close)
        for user in ('alice', 'bob'):
            self.s.create_user(user)
        self.p = self.s.create_portfolio('alice', 'Private')
        self.q = self.s.create_portfolio('bob', 'Private')
        self.v = FixtureValidator({'DEMO': ValidatedSecurity('s0', 'DEMO', 'stock', 'US', 'synthetic')})

    def security(self, n):
        return ValidatedSecurity(f'c{n}', f'C{n}', 'stock', 'US', 'fixture')

    def test_import_holding_grants_baseline_coverage(self):
        self.s.import_holding('alice', self.p, 'DEMO', '1.5', self.v)
        self.assertEqual(self.s.baseline_coverage('s0'), 1)

    def test_import_with_provenance_grants_baseline_coverage(self):
        self.s.import_holding_with_provenance(
            'alice', self.p, 'DEMO', '2', self.v,
            source='fixture', market_as_of=AS_OF, idempotency_key='k1')
        self.assertEqual(self.s.baseline_coverage('s0'), 1)

    def test_outside_universe_holding_keeps_coverage_while_tier_and_queue_full(self):
        # Saturate the enhanced tier (75 admitted) and the admission queue.
        for n in range(75 + 25):
            with self.s.db:
                self.s.register(self.security(n))
            self.s.propose_enhancement(f'c{n}', reason='fixture', operator='admin', now=AS_OF)
        # A valid holding of yet another security still imports and retains coverage.
        self.s.import_holding('alice', self.p, 'DEMO', '1', self.v)
        self.assertEqual(self.s.baseline_coverage('s0'), 1)

    def test_shared_security_keeps_coverage_until_last_holder_removed(self):
        self.s.import_holding_with_provenance(
            'alice', self.p, 'DEMO', '1', self.v,
            source='fixture', market_as_of=AS_OF, idempotency_key='ka')
        self.s.import_holding_with_provenance(
            'bob', self.q, 'DEMO', '1', self.v,
            source='fixture', market_as_of=AS_OF, idempotency_key='kb')
        self.assertEqual(self.s.baseline_coverage('s0'), 1)
        # Alice removes; bob still holds -> still covered.
        self.s.remove_holding('alice', self.p, 's0', source='fixture',
                              idempotency_key='ra', market_as_of=AS_OF)
        self.assertEqual(self.s.baseline_coverage('s0'), 1)
        # Bob removes the last holder -> coverage dropped (row gone).
        self.s.remove_holding('bob', self.q, 's0', source='fixture',
                              idempotency_key='rb', market_as_of=AS_OF)
        self.assertIsNone(self.s.baseline_coverage('s0'))

    def test_reconcile_passes_on_populated_store(self):
        self.s.import_holding('alice', self.p, 'DEMO', '1', self.v)
        v1 = FixtureValidator({'C1': self.security(1)})
        self.s.import_holding('bob', self.q, 'C1', '3', v1)
        self.assertEqual(self.s.reconcile_baseline_coverage(), [])

    def test_reconcile_detects_held_security_without_coverage(self):
        self.s.import_holding('alice', self.p, 'DEMO', '1', self.v)
        with self.s.db:
            self.s.db.execute('DELETE FROM baseline_coverage WHERE security_id=?', ('s0',))
        self.assertIn('s0', [row[0] for row in self.s.reconcile_baseline_coverage()])

    def test_reconcile_detects_coverage_for_unheld_security(self):
        self.s.import_holding('alice', self.p, 'DEMO', '1', self.v)
        with self.s.db:
            self.s.register(self.security(2))
            self.s.db.execute(
                "INSERT INTO baseline_coverage(security_id,covered,since) VALUES ('c2',1,?)",
                (AS_OF.isoformat(),))
        discrepancies = [row[0] for row in self.s.reconcile_baseline_coverage()]
        self.assertIn('c2', discrepancies)


if __name__ == '__main__':
    unittest.main()
