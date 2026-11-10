"""FIFO lot, cost-basis and realized P&L contract: exact Decimal, no residue."""
import unittest
from datetime import timedelta
from decimal import Decimal
from portfolio_agent import paper
from tests.paper_fixtures import NOW, make_store, open_account, observe, order


class PaperLotTests(unittest.TestCase):
    def setUp(self):
        self.s = make_store()
        self.addCleanup(self.s.close)

    def trade(self, account, sym, side, qty, price, key, fee=None):
        at = NOW + timedelta(minutes=len(paper.ledger(self.s, 'alice', account)))
        observe(self.s, sym, price, at=at)
        return order(self.s, 'alice', account, sym, side, qty, key, as_of=at)

    def position(self, account, sym='STOCK'):
        view = paper.positions(self.s, 'alice', account)
        found = [p for p in view['positions'] if p['security_id'] == sym]
        return view, (found[0] if found else None)

    def test_fifo_across_three_lots_with_partial_consumption(self):
        account = open_account(self.s, cash='10000')
        self.trade(account, 'STOCK', 'buy', '10', '10', 'b1')
        self.trade(account, 'STOCK', 'buy', '10', '12', 'b2')
        self.trade(account, 'STOCK', 'buy', '10', '14', 'b3')
        self.trade(account, 'STOCK', 'sell', '25', '15', 's1')
        view, pos = self.position(account)
        # consumed basis: 100 + 120 + half of 140 = 290; proceeds 375
        self.assertEqual(view['realized_pnl'], '85')
        self.assertEqual(pos['quantity'], '5')
        self.assertEqual(pos['cost_basis'], '70')
        self.assertEqual([(l['quantity'], l['basis']) for l in pos['lots']], [('5', '70')])
        self.assertEqual(view['cash'], str(Decimal(10000) - 100 - 120 - 140 + 375))

    def test_fee_is_capitalized_into_lot_basis_and_netted_from_proceeds(self):
        account = open_account(self.s, fee=paper.FeeModel('flat', Decimal('2')), cash='1000')
        self.trade(account, 'STOCK', 'buy', '10', '10', 'b1')
        _, pos = self.position(account)
        self.assertEqual(pos['cost_basis'], '102')
        self.trade(account, 'STOCK', 'sell', '10', '11', 's1')
        view, pos = self.position(account)
        self.assertIsNone(pos)
        self.assertEqual(view['realized_pnl'], str(Decimal(110) - 2 - 102))

    def test_non_terminating_division_leaves_exact_residue(self):
        account = open_account(self.s, cash='10000')
        self.trade(account, 'STOCK', 'buy', '100', '10', 'b1')
        self.trade(account, 'STOCK', 'sell', '3', '10', 's1')
        view, pos = self.position(account)
        self.assertEqual(pos['cost_basis'], '970')
        self.assertEqual(view['realized_pnl'], '0')
        # fee-capitalized basis 8 over 7 shares does not divide evenly for a 3-share sale
        account2 = open_account(self.s, fee=paper.FeeModel('flat', Decimal('1')), cash='10000')
        self.trade(account2, 'ETF', 'buy', '7', '1', 'b1')
        self.trade(account2, 'ETF', 'sell', '3', '2', 's1')
        view2, pos2 = self.position(account2, 'ETF')
        self.assertEqual(pos2['cost_basis'], '4.571428571429')
        self.assertEqual(view2['realized_pnl'], '1.571428571429')
        self.assertEqual(Decimal('8') - Decimal(pos2['cost_basis']) + 0, Decimal('3.428571428571'))
        self.trade(account2, 'ETF', 'sell', '4', '2', 's2')
        view3, pos3 = self.position(account2, 'ETF')
        self.assertIsNone(pos3)
        self.assertEqual(view3['realized_pnl'], '4')

    def test_fractional_shares(self):
        account = open_account(self.s, cash='1000')
        self.trade(account, 'STOCK', 'buy', '0.5', '10', 'b1')
        self.trade(account, 'STOCK', 'sell', '0.25', '12', 's1')
        view, pos = self.position(account)
        self.assertEqual((pos['quantity'], pos['cost_basis'], view['realized_pnl']), ('0.25', '2.5', '0.5'))

    def test_sell_to_zero_removes_position(self):
        account = open_account(self.s, cash='1000')
        self.trade(account, 'STOCK', 'buy', '3', '10', 'b1')
        self.trade(account, 'STOCK', 'sell', '3', '9', 's1')
        view, pos = self.position(account)
        self.assertIsNone(pos)
        self.assertEqual(view['positions'], [])
        self.assertEqual(view['realized_pnl'], '-3')
        self.assertEqual(view['ledger_entries'], 3)

    def test_replay_reports_issues_instead_of_raising(self):
        rows = [{'id': 'e1', 'entry_type': 'sell', 'security_id': 'STOCK', 'quantity': '1', 'unit_price': '10',
                 'fee': '0', 'cash_delta': '-5'}]
        result = paper._replay(rows)
        self.assertIn('oversold', result['issues'])
        self.assertIn('negative_cash', result['issues'])

    def test_replay_is_independent_of_ambient_decimal_precision(self):
        from decimal import localcontext
        account = open_account(self.s, cash='10000')
        self.trade(account, 'STOCK', 'buy', '100', '10.123456789012', 'b1')
        self.trade(account, 'STOCK', 'sell', '3', '11', 's1')
        baseline = paper.positions(self.s, 'alice', account)
        with localcontext() as ctx:
            ctx.prec = 4
            self.assertEqual(paper.positions(self.s, 'alice', account), baseline)


if __name__ == '__main__':
    unittest.main()
