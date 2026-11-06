"""Store-level transaction ledger and Decimal reconciliation contract.

Written before implementation. The ledger reconciles AGAINST the holdings snapshot;
it never auto-mutates holdings. Fixtures are synthetic; quantities are Decimal strings
and timestamps are UTC ISO-8601.
"""
from datetime import datetime, timezone
import unittest
from portfolio_agent.core import FixtureValidator, Store, ValidatedSecurity

AS_OF = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)
OCCURRED = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)


class TransactionLedgerTests(unittest.TestCase):
    def setUp(self):
        self.s = Store()
        self.addCleanup(self.s.close)
        for user in ('alice', 'bob'):
            self.s.create_user(user)
        self.p = self.s.create_portfolio('alice', 'Private')
        self.v = FixtureValidator({'DEMO': ValidatedSecurity('s0', 'DEMO', 'stock', 'US', 'synthetic')})
        self.s.import_holding_with_provenance(
            'alice', self.p, 'DEMO', '2.5', self.v,
            source='fixture', market_as_of=AS_OF, idempotency_key='imp1')

    def _record(self, side='buy', quantity='2.5', key='t1', source='fixture', occurred_at=OCCURRED):
        return self.s.record_transaction(
            'alice', self.p, 's0', side, quantity,
            source=source, idempotency_key=key, occurred_at=occurred_at)

    def test_record_transaction_records_one_owner_scoped_row(self):
        tid = self._record()
        rows = self.s.transactions('alice', self.p)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], tid)
        self.assertEqual(rows[0][1], 's0')
        self.assertEqual(rows[0][2], 'buy')
        self.assertEqual(rows[0][3], '2.5')

    def test_idempotent_retry_applies_once(self):
        first = self._record(quantity='2.5', key='t1')
        second = self._record(quantity='9', key='t1')
        self.assertEqual(first, second)
        self.assertEqual(len(self.s.transactions('alice', self.p)), 1)

    def test_reconcile_true_when_ledger_sum_matches_holding(self):
        self._record(side='buy', quantity='2.5', key='t1')
        rows = self.s.reconcile_portfolio('alice', self.p)
        by_security = {r[0]: r for r in rows}
        security_id, ledger_sum, holding_qty, reconciled = by_security['s0']
        self.assertEqual(ledger_sum, '2.5')
        self.assertEqual(holding_qty, '2.5')
        self.assertTrue(reconciled)

    def test_reconcile_false_on_injected_divergence(self):
        self._record(side='buy', quantity='2.5', key='t1')
        self._record(side='sell', quantity='1', key='t2')
        rows = self.s.reconcile_portfolio('alice', self.p)
        by_security = {r[0]: r for r in rows}
        security_id, ledger_sum, holding_qty, reconciled = by_security['s0']
        self.assertEqual(ledger_sum, '1.5')
        self.assertEqual(holding_qty, '2.5')
        self.assertFalse(reconciled)

    def test_cross_user_record_and_reads_denied(self):
        with self.assertRaises(PermissionError):
            self.s.record_transaction('bob', self.p, 's0', 'buy', '1',
                                      source='fixture', idempotency_key='t1', occurred_at=OCCURRED)
        with self.assertRaises(PermissionError):
            self.s.transactions('bob', self.p)
        with self.assertRaises(PermissionError):
            self.s.reconcile_portfolio('bob', self.p)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0], 0)

    def test_forged_portfolio_denied(self):
        with self.assertRaises(PermissionError):
            self.s.record_transaction('alice', 'does-not-exist', 's0', 'buy', '1',
                                      source='fixture', idempotency_key='t1', occurred_at=OCCURRED)

    def test_invalid_side_raises_without_row(self):
        with self.assertRaises(ValueError):
            self._record(side='short', quantity='1', key='t1')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0], 0)

    def test_nonpositive_quantity_raises_without_row(self):
        for bad in ('0', '-1', 'nan', 'inf'):
            with self.assertRaises(ValueError):
                self._record(side='buy', quantity=bad, key='t-' + bad)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0], 0)

    def test_empty_source_or_key_raises_without_row(self):
        with self.assertRaises(ValueError):
            self._record(source=' ', key='t1')
        with self.assertRaises(ValueError):
            self._record(key=' ')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
