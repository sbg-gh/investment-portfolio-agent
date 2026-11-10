"""Paper fill acceptance contract: reproducible, cutoff-bound, never substituted."""
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from portfolio_agent import paper
from portfolio_agent.ports import Observation
from portfolio_agent.valuation import FreshnessPolicy, ingest_observation
from tests.paper_fixtures import NOW, make_store, open_account, observe, order


class PaperFillTests(unittest.TestCase):
    def setUp(self):
        self.s = make_store()
        self.addCleanup(self.s.close)

    def rows(self, account):
        return len(paper.ledger(self.s, 'alice', account))

    def test_stock_and_etf_fill_at_cutoff_observation_with_provenance(self):
        account = open_account(self.s, cash='1000')
        obs = observe(self.s, 'STOCK', '10.5', age=timedelta(hours=2))
        observe(self.s, 'ETF', '20', age=timedelta(hours=1))
        fill = order(self.s, 'alice', account, 'STOCK', 'buy', '2', 'o1')
        self.assertEqual((fill['status'], fill['side'], fill['kind'], fill['basis']), ('filled', 'buy', 'stock', 'price'))
        self.assertEqual((fill['unit_price'], fill['gross'], fill['fee'], fill['cash_delta']), ('10.5', '21', '0', '-21'))
        self.assertEqual(fill['observation_id'], obs)
        self.assertEqual(fill['fill_model'], 'last_observation_at_or_before_cutoff')
        self.assertEqual(fill['fee_model'], {'kind': 'none', 'amount': '0'})
        self.assertIsNone(fill['nav_date'])
        self.assertEqual(fill['price_as_of'], (NOW - timedelta(hours=2)).isoformat())
        self.assertEqual(len(fill['fill_digest']), 64)
        self.assertFalse(fill['replayed'])
        etf = order(self.s, 'alice', account, 'ETF', 'buy', '1', 'o2')
        self.assertEqual(etf['kind'], 'etf')
        self.assertEqual(paper.positions(self.s, 'alice', account)['cash'], '959')

    def test_later_and_later_fetched_observations_are_ignored(self):
        account = open_account(self.s, cash='1000')
        early = observe(self.s, 'STOCK', '10', age=timedelta(hours=3))
        observe(self.s, 'STOCK', '99', age=timedelta(hours=-1), fetched_at=NOW + timedelta(hours=1))
        observe(self.s, 'STOCK', '50', age=timedelta(hours=1), fetched_at=NOW + timedelta(minutes=1))
        fill = order(self.s, 'alice', account, 'STOCK', 'buy', '1', 'o1')
        self.assertEqual((fill['observation_id'], fill['unit_price']), (early, '10'))

    def test_wrong_source_is_ignored(self):
        account = open_account(self.s, cash='1000')
        observe(self.s, 'STOCK', '10', source='other')
        with self.assertRaises(paper.PaperRejected) as ctx:
            order(self.s, 'alice', account, 'STOCK', 'buy', '1', 'o1')
        self.assertEqual(ctx.exception.reason, 'price_missing')

    def test_identical_inputs_give_identical_fills_in_separate_stores(self):
        fills = []
        for _ in range(2):
            s = make_store()
            self.addCleanup(s.close)
            account = open_account(s, fee=paper.FeeModel('percentage', Decimal('0.001')), cash='1000')
            observe(s, 'STOCK', '12.34')
            fills.append(order(s, 'alice', account, 'STOCK', 'buy', '3', 'o1'))
        for key in ('fill_digest', 'unit_price', 'fee', 'cash_delta', 'observation_id'):
            self.assertEqual(fills[0][key], fills[1][key], key)

    def test_stale_and_missing_price_reject_without_ledger_row(self):
        account = open_account(self.s, cash='1000')
        before = self.rows(account)
        with self.assertRaises(paper.PaperRejected) as ctx:
            order(self.s, 'alice', account, 'STOCK', 'buy', '1', 'o1')
        self.assertEqual(ctx.exception.reason, 'price_missing')
        observe(self.s, 'STOCK', '10', age=timedelta(days=1, seconds=1))
        observe(self.s, 'ETF', '10', age=timedelta(days=5))
        with self.assertRaises(paper.PaperRejected) as ctx:
            order(self.s, 'alice', account, 'STOCK', 'buy', '1', 'o2')
        self.assertEqual(ctx.exception.reason, 'price_stale')
        self.assertIsInstance(ctx.exception, ValueError)
        self.assertEqual(self.rows(account), before)

    def test_freshness_boundary_exactly_at_max_age_fills(self):
        account = open_account(self.s, cash='1000')
        observe(self.s, 'STOCK', '10', age=timedelta(days=1))
        self.assertEqual(order(self.s, 'alice', account, 'STOCK', 'buy', '1', 'o1')['status'], 'filled')

    def test_custom_policy_is_honored(self):
        account = open_account(self.s, cash='1000')
        observe(self.s, 'STOCK', '10', age=timedelta(hours=2))
        with self.assertRaises(paper.PaperRejected):
            order(self.s, 'alice', account, 'STOCK', 'buy', '1', 'o1', policy=FreshnessPolicy(price_max_age=timedelta(hours=1)))

    def test_fund_fills_only_from_nav_and_records_nav_date(self):
        account = open_account(self.s, cash='1000')
        with self.assertRaises(paper.PaperRejected) as ctx:
            order(self.s, 'alice', account, 'FUND', 'buy', '1', 'o1')
        self.assertEqual(ctx.exception.reason, 'nav_missing')
        with self.assertRaises(ValueError):
            ingest_observation(self.s, Observation('FUND', Decimal('5'), NOW, SOURCE_NAME, 'price', fetched_at=NOW))
        with self.assertRaises(paper.PaperRejected) as ctx:
            order(self.s, 'alice', account, 'FUND', 'buy', '1', 'o1b')
        self.assertEqual(ctx.exception.reason, 'nav_missing')
        nav = observe(self.s, 'FUND', '5.5', age=timedelta(days=2))
        fill = order(self.s, 'alice', account, 'FUND', 'buy', '2', 'o2')
        self.assertEqual((fill['basis'], fill['observation_id'], fill['unit_price']), ('nav', nav, '5.5'))
        self.assertEqual(fill['nav_date'], (NOW - timedelta(days=2)).date().isoformat())
        self.assertEqual(fill['kind'], 'mutual_fund')

    def test_stale_nav_rejected(self):
        account = open_account(self.s, cash='1000')
        observe(self.s, 'FUND', '5', age=timedelta(days=3, seconds=1))
        with self.assertRaises(paper.PaperRejected) as ctx:
            order(self.s, 'alice', account, 'FUND', 'buy', '1', 'o1')
        self.assertEqual(ctx.exception.reason, 'nav_stale')

    def test_fee_models_are_exact(self):
        cases = ((paper.FeeModel('none', Decimal(0)), '0'),
                 (paper.FeeModel('flat', Decimal('4.95')), '4.95'),
                 (paper.FeeModel('per_share', Decimal('0.005')), '0.0375'),
                 (paper.FeeModel('percentage', Decimal('0.001')), '0.075'))
        for model, expected in cases:
            s = make_store()
            self.addCleanup(s.close)
            account = open_account(s, fee=model, cash='1000')
            observe(s, 'STOCK', '10')
            fill = order(s, 'alice', account, 'STOCK', 'buy', '7.5', 'o1')
            self.assertEqual(fill['fee'], expected, model.kind)
            self.assertEqual(Decimal(fill['cash_delta']), -(Decimal('75') + Decimal(expected)))

    def test_overdraft_including_fee_rejected_and_exact_cash_succeeds(self):
        account = open_account(self.s, fee=paper.FeeModel('flat', Decimal('1')), cash='100')
        observe(self.s, 'STOCK', '10')
        with self.assertRaises(paper.PaperRejected) as ctx:
            order(self.s, 'alice', account, 'STOCK', 'buy', '10', 'o1')
        self.assertEqual(ctx.exception.reason, 'insufficient_cash')
        self.assertEqual(paper.positions(self.s, 'alice', account)['cash'], '100')
        self.assertEqual(self.rows(account), 1)
        fill = order(self.s, 'alice', account, 'STOCK', 'buy', '9.9', 'o2')
        self.assertEqual(fill['cash_delta'], '-100')
        self.assertEqual(paper.positions(self.s, 'alice', account)['cash'], '0')

    def test_oversell_rejected(self):
        account = open_account(self.s, cash='100')
        observe(self.s, 'STOCK', '10')
        order(self.s, 'alice', account, 'STOCK', 'buy', '2', 'o1')
        before = self.rows(account)
        with self.assertRaises(paper.PaperRejected) as ctx:
            order(self.s, 'alice', account, 'STOCK', 'sell', '2.000000000001', 'o2')
        self.assertEqual(ctx.exception.reason, 'insufficient_position')
        observe(self.s, 'ETF', '10')
        with self.assertRaises(paper.PaperRejected) as ctx:
            order(self.s, 'alice', account, 'ETF', 'sell', '1', 'o3')
        self.assertEqual(ctx.exception.reason, 'insufficient_position')
        self.assertEqual(self.rows(account), before)

    def test_sell_fee_exceeding_proceeds_with_zero_cash_rejected(self):
        account = open_account(self.s, fee=paper.FeeModel('flat', Decimal('50')), cash='60')
        observe(self.s, 'STOCK', '1')
        order(self.s, 'alice', account, 'STOCK', 'buy', '10', 'o1')
        self.assertEqual(paper.positions(self.s, 'alice', account)['cash'], '0')
        with self.assertRaises(paper.PaperRejected) as ctx:
            order(self.s, 'alice', account, 'STOCK', 'sell', '1', 'o2')
        self.assertEqual(ctx.exception.reason, 'insufficient_cash')

    def test_sell_credits_net_proceeds(self):
        account = open_account(self.s, fee=paper.FeeModel('flat', Decimal('1')), cash='100')
        observe(self.s, 'STOCK', '10')
        order(self.s, 'alice', account, 'STOCK', 'buy', '5', 'o1')
        fill = order(self.s, 'alice', account, 'STOCK', 'sell', '5', 'o2')
        self.assertEqual((fill['gross'], fill['fee'], fill['cash_delta']), ('50', '1', '49'))
        self.assertEqual(paper.positions(self.s, 'alice', account)['cash'], '98')

    def test_unknown_security_and_bad_side_and_quantity(self):
        account = open_account(self.s, cash='100')
        observe(self.s, 'STOCK', '10')
        for sec, side, qty in (('NOPE', 'buy', '1'), ('', 'buy', '1'), (None, 'buy', '1'),
                               ('STOCK', 'short', '1'), ('STOCK', 'SELL', '1'), ('STOCK', None, '1'),
                               ('STOCK', 'buy', 1.0), ('STOCK', 'buy', '0'), ('STOCK', 'buy', '-1'),
                               ('STOCK', 'buy', 'NaN'), ('STOCK', 'buy', '1.0000000000001')):
            with self.assertRaises(ValueError, msg=(sec, side, qty)) as ctx:
                order(self.s, 'alice', account, sec, side, qty, 'o1')
            self.assertNotIsInstance(ctx.exception, paper.PaperRejected)
        self.assertEqual(self.rows(account), 1)

    def test_empty_idempotency_key_and_naive_time_rejected(self):
        account = open_account(self.s, cash='100')
        observe(self.s, 'STOCK', '10')
        with self.assertRaises(ValueError):
            order(self.s, 'alice', account, 'STOCK', 'buy', '1', '')
        with self.assertRaises(ValueError):
            order(self.s, 'alice', account, 'STOCK', 'buy', '1', 'k', as_of=datetime(2026, 11, 6))

    def test_idempotent_replay_returns_prior_fill_without_reevaluation(self):
        account = open_account(self.s, cash='100')
        observe(self.s, 'STOCK', '10')
        first = order(self.s, 'alice', account, 'STOCK', 'buy', '1', 'o1')
        observe(self.s, 'STOCK', '11', age=timedelta(minutes=-1), at=NOW)
        again = order(self.s, 'alice', account, 'STOCK', 'buy', '1', 'o1', as_of=NOW + timedelta(days=30))
        self.assertTrue(again['replayed'])
        expected = dict(first, replayed=True)
        self.assertEqual(again, expected)
        self.assertEqual(self.rows(account), 2)

    def test_retry_of_rejected_order_after_data_arrives_reevaluates(self):
        account = open_account(self.s, cash='100')
        with self.assertRaises(paper.PaperRejected):
            order(self.s, 'alice', account, 'STOCK', 'buy', '1', 'o1')
        observe(self.s, 'STOCK', '10')
        fill = order(self.s, 'alice', account, 'STOCK', 'buy', '1', 'o1')
        self.assertFalse(fill['replayed'])
        self.assertEqual(fill['unit_price'], '10')

    def test_key_reuse_across_entry_types_is_a_conflict(self):
        account = open_account(self.s, cash='100')
        observe(self.s, 'STOCK', '10')
        with self.assertRaises(ValueError):
            order(self.s, 'alice', account, 'STOCK', 'buy', '1', 'dep-100-0')


SOURCE_NAME = 'fixture'

if __name__ == '__main__':
    unittest.main()
