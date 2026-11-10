"""Paper account/ledger acceptance contract: written before src/portfolio_agent/paper.py."""
import sqlite3
import unittest
from decimal import Decimal
from portfolio_agent import paper
from tests.paper_fixtures import NOW, make_store, open_account, deposit, observe


class PaperLedgerTests(unittest.TestCase):
    def setUp(self):
        self.s = make_store()
        self.addCleanup(self.s.close)

    def test_create_account_and_deposit(self):
        account = open_account(self.s, fee=paper.FeeModel('flat', Decimal('1.5')))
        entry = deposit(self.s, 'alice', account, '1000.25', key='d1')
        self.assertEqual(entry['entry_type'], 'deposit')
        self.assertEqual(entry['cash_delta'], '1000.25')
        self.assertFalse(entry['replayed'])
        view = paper.positions(self.s, 'alice', account)
        self.assertEqual(view['cash'], '1000.25')
        self.assertEqual(view['positions'], [])

    def test_idempotent_retry_returns_same_entry_once(self):
        account = open_account(self.s)
        first = deposit(self.s, 'alice', account, '50', key='k')
        again = deposit(self.s, 'alice', account, '50', key='k')
        self.assertTrue(again['replayed'])
        self.assertEqual(again['entry_id'], first['entry_id'])
        self.assertEqual(len(paper.ledger(self.s, 'alice', account)), 1)

    def test_same_key_allowed_across_accounts(self):
        a1, a2 = open_account(self.s), open_account(self.s)
        deposit(self.s, 'alice', a1, '5', key='k')
        deposit(self.s, 'alice', a2, '5', key='k')
        self.assertEqual(len(paper.ledger(self.s, 'alice', a1)), 1)
        self.assertEqual(len(paper.ledger(self.s, 'alice', a2)), 1)

    def test_ledger_and_accounts_are_append_only(self):
        account = open_account(self.s, cash='10')
        for sql in ("UPDATE paper_ledger SET cash_delta='99'", "DELETE FROM paper_ledger",
                    "UPDATE paper_accounts SET fee_kind='flat'", "DELETE FROM paper_accounts"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.s.db.execute(sql)

    def test_trigger_rejects_account_over_foreign_portfolio(self):
        portfolio = self.s.create_portfolio('alice', 'p')
        with self.assertRaises(sqlite3.IntegrityError):
            self.s.db.execute("INSERT INTO paper_accounts(id,user_id,portfolio_id,currency,price_source,"
                              "fee_kind,fee_amount) VALUES ('x','bob',?,'USD','fixture','none','0')", (portfolio,))

    def test_other_user_cannot_create_read_or_write(self):
        portfolio = self.s.create_portfolio('alice', 'p')
        with self.assertRaises(PermissionError):
            paper.create_account(self.s, 'bob', portfolio, source='fixture',
                                 fee_model=paper.FeeModel('none', Decimal(0)), mode='paper')
        account = open_account(self.s, cash='10')
        for call in (lambda: paper.ledger(self.s, 'bob', account),
                     lambda: paper.positions(self.s, 'bob', account),
                     lambda: paper.reconcile_account(self.s, 'bob', account),
                     lambda: paper.value_account(self.s, 'bob', account, as_of=NOW),
                     lambda: deposit(self.s, 'bob', account, '1', key='z')):
            with self.assertRaisesRegex(PermissionError, 'Paper account unavailable'):
                call()
        self.assertEqual(len(paper.ledger(self.s, 'alice', account)), 1)

    def test_unknown_or_malformed_account_ids_are_generic_denials(self):
        for bad in ('missing', '', None, 7, b'x'):
            with self.assertRaisesRegex(PermissionError, 'Paper account unavailable'):
                paper.ledger(self.s, 'alice', bad)

    def test_account_with_removed_portfolio_ownership_is_denied(self):
        account = open_account(self.s)
        self.s.db.execute('UPDATE portfolios SET user_id=? WHERE id=(SELECT portfolio_id FROM paper_accounts WHERE id=?)',
                          ('bob', account))
        with self.assertRaises(PermissionError):
            paper.ledger(self.s, 'alice', account)

    def test_invalid_amounts_rejected_and_nothing_written(self):
        account = open_account(self.s)
        for bad in (1.5, True, 0, '0', '-1', 'NaN', 'Infinity', '1.0000000000001', '', 'abc', None,
                    Decimal('-1'), Decimal('NaN'), Decimal('1e30')):
            with self.assertRaises(ValueError, msg=repr(bad)):
                deposit(self.s, 'alice', account, bad, key='k')
        self.assertEqual(paper.ledger(self.s, 'alice', account), [])

    def test_empty_idempotency_key_and_naive_time_rejected(self):
        account = open_account(self.s)
        from datetime import datetime
        with self.assertRaises(ValueError):
            paper.deposit(self.s, 'alice', account, '1', source='fixture', idempotency_key=' ',
                          as_of=NOW, mode='paper')
        with self.assertRaises(ValueError):
            paper.deposit(self.s, 'alice', account, '1', source='fixture', idempotency_key='k',
                          as_of=datetime(2026, 1, 1), mode='paper')
        with self.assertRaises(ValueError):
            paper.deposit(self.s, 'alice', account, '1', source=' ', idempotency_key='k',
                          as_of=NOW, mode='paper')

    def test_fee_model_validation_and_immutability(self):
        for kind, amount in (('bogus', Decimal(1)), ('flat', Decimal(-1)), ('flat', 1),
                             ('percentage', Decimal('1.5')), ('none', Decimal(1)), ('flat', Decimal('NaN'))):
            with self.assertRaises(ValueError, msg=(kind, amount)):
                paper.FeeModel(kind, amount)
        self.assertEqual(paper.FeeModel('percentage', Decimal('0.001')).kind, 'percentage')
        account = open_account(self.s, fee=paper.FeeModel('per_share', Decimal('0.01')))
        with self.assertRaises(sqlite3.IntegrityError):
            self.s.db.execute("UPDATE paper_accounts SET fee_amount='0' WHERE id=?", (account,))

    def test_create_account_requires_source(self):
        portfolio = self.s.create_portfolio('alice', 'p')
        for source in ('', ' ', None):
            with self.assertRaises(ValueError):
                paper.create_account(self.s, 'alice', portfolio, source=source,
                                     fee_model=paper.FeeModel('none', Decimal(0)), mode='paper')

    def test_mode_gate_rejects_everything_but_exact_paper(self):
        portfolio = self.s.create_portfolio('alice', 'p')
        account = open_account(self.s, cash='100')
        observe(self.s, 'STOCK', '10')
        fee = paper.FeeModel('none', Decimal(0))
        for mode in ('read_only', 'live', 'LIVE', '', 'paper ', ' paper', 'PAPER', None, 'unexpected', b'paper'):
            before = self.s.db.execute('SELECT COUNT(*) FROM paper_ledger').fetchone()[0]
            accounts = self.s.db.execute('SELECT COUNT(*) FROM paper_accounts').fetchone()[0]
            with self.assertRaises(PermissionError, msg=repr(mode)):
                paper.create_account(self.s, 'alice', portfolio, source='fixture', fee_model=fee, mode=mode)
            with self.assertRaises(PermissionError, msg=repr(mode)):
                paper.deposit(self.s, 'alice', account, '1', source='fixture', idempotency_key='m', as_of=NOW, mode=mode)
            with self.assertRaises(PermissionError, msg=repr(mode)):
                paper.submit_order(self.s, 'alice', account, 'STOCK', 'buy', '1', as_of=NOW,
                                   idempotency_key='m', mode=mode)
            self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM paper_ledger').fetchone()[0], before)
            self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM paper_accounts').fetchone()[0], accounts)
        paper.deposit(self.s, 'alice', account, '1', source='fixture', idempotency_key='m', as_of=NOW, mode='paper')

    def test_default_mode_rejects(self):
        account = open_account(self.s)
        with self.assertRaises(PermissionError):
            paper.deposit(self.s, 'alice', account, '1', source='fixture', idempotency_key='m', as_of=NOW)
        with self.assertRaises(PermissionError):
            paper.submit_order(self.s, 'alice', account, 'STOCK', 'buy', '1', as_of=NOW, idempotency_key='m')

    def test_authorization_precedes_mode_check(self):
        account = open_account(self.s)
        with self.assertRaisesRegex(PermissionError, 'Paper account unavailable'):
            paper.deposit(self.s, 'bob', account, '1', source='fixture', idempotency_key='m', as_of=NOW, mode='live')

    def test_real_holdings_tables_untouched(self):
        account = open_account(self.s, cash='100')
        observe(self.s, 'STOCK', '10')
        paper.submit_order(self.s, 'alice', account, 'STOCK', 'buy', '2', as_of=NOW, idempotency_key='o', mode='paper')
        for table in ('holdings', 'transactions', 'import_receipts', 'holding_events', 'paper_orders'):
            self.assertEqual(self.s.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0, table)


if __name__ == '__main__':
    unittest.main()
