"""Paper account valuation and reconciliation contract."""
import unittest
from datetime import timedelta
from decimal import Decimal
from portfolio_agent import paper
from tests.paper_fixtures import NOW, make_store, open_account, observe, order


class PaperValuationTests(unittest.TestCase):
    def setUp(self):
        self.s = make_store()
        self.addCleanup(self.s.close)
        self.account = open_account(self.s, cash='1000')
        observe(self.s, 'STOCK', '10')
        observe(self.s, 'FUND', '5', age=timedelta(days=1))
        order(self.s, 'alice', self.account, 'STOCK', 'buy', '10', 'b1')
        order(self.s, 'alice', self.account, 'FUND', 'buy', '20', 'b2')

    def value(self, **kwargs):
        return paper.value_account(self.s, 'alice', self.account, as_of=NOW, **kwargs)

    def test_complete_valuation_has_exact_total_and_provenance(self):
        report = self.value()
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(report['holdings_basis'], 'paper_ledger')
        self.assertEqual((report['cash'], report['priced_subtotal'], report['total']), ('800', '200', '1000'))
        self.assertEqual(report['source'], 'fixture')
        self.assertEqual(report['market_as_of'], NOW.isoformat())
        self.assertEqual(len(report['ledger_sha256']), 64)
        lines = {l['security_id']: l for l in report['lines']}
        self.assertEqual(lines['FUND']['basis'], 'nav')
        self.assertEqual(lines['STOCK']['market_value'], '100')
        self.assertEqual(lines['STOCK']['cost_basis'], '100')
        self.assertEqual(lines['STOCK']['status'], 'fresh')
        self.assertTrue(lines['STOCK']['observation_id'])

    def test_stale_price_withholds_total_but_reports_subtotal(self):
        report = paper.value_account(self.s, 'alice', self.account, as_of=NOW + timedelta(days=2))
        self.assertEqual(report['status'], 'stale')
        self.assertIsNone(report['total'])
        self.assertEqual(report['priced_subtotal'], '200')

    def test_missing_price_is_incomplete_and_never_substituted(self):
        report = paper.value_account(self.s, 'alice', self.account, as_of=NOW - timedelta(days=30))
        self.assertEqual(report['status'], 'incomplete')
        self.assertIsNone(report['total'])
        self.assertEqual(report['priced_subtotal'], '0')
        self.assertTrue(all(l['status'] == 'missing' and l['market_value'] is None for l in report['lines']))

    def test_cutoff_excludes_later_observations(self):
        observe(self.s, 'STOCK', '1000', age=timedelta(hours=-1), fetched_at=NOW + timedelta(hours=1))
        self.assertEqual(self.value()['total'], '1000')

    def test_empty_positions_total_is_cash(self):
        account = open_account(self.s, cash='42')
        report = paper.value_account(self.s, 'alice', account, as_of=NOW)
        self.assertEqual((report['status'], report['total'], report['lines']), ('complete', '42', []))

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            paper.value_account(self.s, 'alice', self.account, as_of=NOW.replace(tzinfo=None))
        with self.assertRaises(ValueError):
            paper.value_account(self.s, 'alice', self.account, as_of=NOW, policy='nope')

    def test_valuation_writes_no_snapshots_or_ledger_rows(self):
        rows = len(paper.ledger(self.s, 'alice', self.account))
        self.value()
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM valuation_snapshots').fetchone()[0], 0)
        self.assertEqual(len(paper.ledger(self.s, 'alice', self.account)), rows)

    def test_reconcile_clean_ledger_is_ok(self):
        rows = paper.reconcile_account(self.s, 'alice', self.account)
        self.assertTrue(rows and all(r['ok'] for r in rows))
        cash = next(r for r in rows if r['check'] == 'cash')
        self.assertEqual((cash['ledger_cash_sum'], cash['recomputed_cash_from_components']), ('800', '800'))
        self.assertEqual({r['security_id'] for r in rows if r['check'] == 'position'}, {'STOCK', 'FUND'})

    def test_reconcile_flags_tampering_and_does_not_repair(self):
        self.s.db.execute('DROP TRIGGER paper_ledger_no_update')
        self.s.db.execute("UPDATE paper_ledger SET cash_delta='-1' WHERE idempotency_key='b1'")
        rows = paper.reconcile_account(self.s, 'alice', self.account)
        cash = next(r for r in rows if r['check'] == 'cash')
        self.assertFalse(cash['ok'])
        self.assertEqual(cash['ledger_cash_sum'], '899')
        self.assertEqual(cash['recomputed_cash_from_components'], '800')
        again = next(r for r in paper.reconcile_account(self.s, 'alice', self.account) if r['check'] == 'cash')
        self.assertEqual(again['ledger_cash_sum'], '899')
        self.assertEqual(self.s.db.execute("SELECT cash_delta FROM paper_ledger WHERE idempotency_key='b1'").fetchone()[0], '-1')


if __name__ == '__main__':
    unittest.main()
