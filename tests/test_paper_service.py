"""Authorized-boundary tests for the M6 paper service methods. Written before implementation.
Identity comes only from the AuthContext; there is no user/owner parameter. Traces are
payload-free and a rejected order leaves nothing behind.
"""
from contextlib import redirect_stdout
from dataclasses import replace
from decimal import Decimal
import inspect
import io
import json
import sqlite3
import unittest
from portfolio_agent import cli, paper
from portfolio_agent.auth import Authenticator
from portfolio_agent.core import FixtureValidator
from portfolio_agent.services import OperatorService, PortfolioService
from tests.paper_fixtures import NOW, make_store, observe

METHODS = ('create_paper_account', 'paper_deposit', 'submit_paper_order', 'paper_ledger',
           'paper_positions', 'value_paper_account', 'reconcile_paper_account')
TRACE_COLUMNS = ['id', 'user_id', 'request_id', 'operation', 'outcome', 'policy_version',
                 'duration_ms', 'created_at']


class PaperServiceBase(unittest.TestCase):
    def setUp(self):
        self.s = make_store()
        self.addCleanup(self.s.close)
        self.s.create_user('admin')
        self.auth = Authenticator(self.s)
        self.ctx = {u: self.auth.authenticate(self.auth.provision(u, role='operator' if u == 'admin' else 'user'))
                    for u in ('alice', 'bob', 'admin')}
        self.app = PortfolioService(self.s, self.auth, FixtureValidator({}))
        self.ops = OperatorService(self.app)
        observe(self.s, 'STOCK', '10')
        self.alice_p = self.app.create_portfolio(self.ctx['alice'], 'alice private')
        self.bob_p = self.app.create_portfolio(self.ctx['bob'], 'bob private')
        self.acct = self.app.create_paper_account(self.ctx['alice'], self.alice_p, source='fixture', mode='paper')

    def fund(self, amount='1000', key='d1'):
        return self.app.paper_deposit(self.ctx['alice'], self.acct, amount, source='fixture',
                                      idempotency_key=key, as_of=NOW, mode='paper')

    def buy(self, qty='2', key='o1', **kwargs):
        return self.app.submit_paper_order(self.ctx['alice'], self.acct, 'STOCK', 'buy', qty, as_of=NOW,
                                           idempotency_key=key, mode='paper', **kwargs)

    def count(self, table):
        return self.s.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def calls(self, ctx, account, portfolio):
        """One zero-argument callable per paper method, bound to the given scope."""
        return {
            'create_paper_account': lambda: self.app.create_paper_account(ctx, portfolio, source='fixture', mode='paper'),
            'paper_deposit': lambda: self.app.paper_deposit(ctx, account, '5', source='fixture', idempotency_key='x',
                                                            as_of=NOW, mode='paper'),
            'submit_paper_order': lambda: self.app.submit_paper_order(ctx, account, 'STOCK', 'buy', '1', as_of=NOW,
                                                                     idempotency_key='x', mode='paper'),
            'paper_ledger': lambda: self.app.paper_ledger(ctx, account),
            'paper_positions': lambda: self.app.paper_positions(ctx, account),
            'value_paper_account': lambda: self.app.value_paper_account(ctx, account, as_of=NOW),
            'reconcile_paper_account': lambda: self.app.reconcile_paper_account(ctx, account),
        }


class PaperServiceTests(PaperServiceBase):
    def test_happy_path_through_service(self):
        self.fund()
        fill = self.buy()
        self.assertEqual(fill['status'], 'filled')
        self.assertEqual(self.app.paper_positions(self.ctx['alice'], self.acct)['cash'], '980')
        self.assertEqual(len(self.app.paper_ledger(self.ctx['alice'], self.acct)), 2)
        self.assertEqual(self.app.value_paper_account(self.ctx['alice'], self.acct, as_of=NOW)['total'], '1000')
        self.assertTrue(all(r['ok'] for r in self.app.reconcile_paper_account(self.ctx['alice'], self.acct)))

    def test_fee_model_is_built_from_text_fields(self):
        acct = self.app.create_paper_account(self.ctx['alice'], self.alice_p, source='fixture',
                                             fee_kind='flat', fee_amount='1.5', mode='paper')
        self.app.paper_deposit(self.ctx['alice'], acct, '100', source='fixture', idempotency_key='d',
                               as_of=NOW, mode='paper')
        fill = self.app.submit_paper_order(self.ctx['alice'], acct, 'STOCK', 'buy', '1', as_of=NOW,
                                           idempotency_key='o', mode='paper')
        self.assertEqual(fill['fee'], '1.5')
        for bad in (1.5, True, 'abc', None):
            with self.assertRaises(ValueError):
                self.app.create_paper_account(self.ctx['alice'], self.alice_p, source='fixture',
                                              fee_kind='flat', fee_amount=bad, mode='paper')

    def test_no_owner_parameter_and_unexpected_kwargs_rejected(self):
        for name in METHODS:
            params = inspect.signature(getattr(PortfolioService, name)).parameters
            self.assertFalse({'user', 'user_id', 'owner', 'owner_id'} & set(params), name)
        with self.assertRaises(TypeError):
            self.app.create_paper_account(self.ctx['alice'], self.alice_p, source='fixture', mode='paper', user_id='bob')
        with self.assertRaises(TypeError):
            self.app.paper_ledger(self.ctx['alice'], self.acct, owner_id='bob')
        self.assertEqual(self.s.db.execute('SELECT user_id FROM paper_accounts WHERE id=?', (self.acct,)).fetchone()[0],
                         'alice')

    def test_ledger_rows_carry_context_user_account(self):
        self.fund()
        owners = self.s.db.execute('SELECT DISTINCT a.user_id FROM paper_ledger l JOIN paper_accounts a '
                                   'ON a.id=l.account_id').fetchall()
        self.assertEqual(owners, [('alice',)])

    def test_cross_user_denied_for_every_method(self):
        self.fund()
        before = (self.count('paper_accounts'), self.count('paper_ledger'))
        for name, call in self.calls(self.ctx['bob'], self.acct, self.alice_p).items():
            with self.assertRaises(PermissionError, msg=name) as caught:
                call()
            self.assertNotIn('alice', str(caught.exception))
        self.assertEqual((self.count('paper_accounts'), self.count('paper_ledger')), before)
        denied = {r[2:4] for r in self.app.traces(self.ctx['bob'])}
        self.assertEqual({op for op, outcome in denied if outcome == 'denied'}, set(METHODS))

    def test_forged_expired_and_revoked_contexts_denied(self):
        forged = replace(self.ctx['bob'], user_id='alice')
        expired = replace(self.ctx['alice'], expires_at=1.0)
        revoked = self.auth.authenticate(self.auth.provision('alice'))
        self.auth.revoke(revoked.credential_id)
        for ctx in (forged, expired, revoked):
            for name, call in self.calls(ctx, self.acct, self.alice_p).items():
                with self.assertRaises(PermissionError, msg=name):
                    call()
        self.assertEqual(self.count('paper_ledger'), 0)
        self.assertEqual(self.count('paper_accounts'), 1)

    def test_malformed_scope_denied_with_nothing_written(self):
        for bad in (None, 7, '', '   ', self.acct + 'x', ['a']):
            for call in (lambda b=bad: self.app.paper_deposit(self.ctx['alice'], b, '5', source='fixture',
                                                             idempotency_key='k', as_of=NOW, mode='paper'),
                         lambda b=bad: self.app.paper_ledger(self.ctx['alice'], b),
                         lambda b=bad: self.app.submit_paper_order(self.ctx['alice'], b, 'STOCK', 'buy', '1',
                                                                  as_of=NOW, idempotency_key='k', mode='paper')):
                with self.assertRaises(PermissionError):
                    call()
        for bad in (None, 7, '', '   ', self.bob_p):
            with self.assertRaises(PermissionError):
                self.app.create_paper_account(self.ctx['alice'], bad, source='fixture', mode='paper')
        self.assertEqual((self.count('paper_accounts'), self.count('paper_ledger')), (1, 0))

    def test_other_users_account_id_denied_even_with_valid_mode(self):
        bob_acct = self.app.create_paper_account(self.ctx['bob'], self.bob_p, source='fixture', mode='paper')
        with self.assertRaises(PermissionError):
            self.app.paper_deposit(self.ctx['alice'], bob_acct, '5', source='fixture', idempotency_key='k',
                                   as_of=NOW, mode='paper')
        self.assertEqual(self.count('paper_ledger'), 0)

    def test_account_creation_on_foreign_portfolio_denied_before_mode_check(self):
        for mode in ('paper', 'read_only'):
            with self.assertRaises(PermissionError) as caught:
                self.app.create_paper_account(self.ctx['alice'], self.bob_p, source='fixture', mode=mode)
            self.assertNotIn('mode', str(caught.exception).lower())
        self.assertEqual(self.count('paper_accounts'), 1)

    def test_mutators_denied_in_default_read_only_mode(self):
        with self.assertRaises(PermissionError) as caught:
            self.app.create_paper_account(self.ctx['alice'], self.alice_p, source='fixture')
        self.assertEqual(str(caught.exception), 'Paper operations require paper mode')
        with self.assertRaises(PermissionError):
            self.app.paper_deposit(self.ctx['alice'], self.acct, '5', source='fixture', idempotency_key='k', as_of=NOW)
        with self.assertRaises(PermissionError):
            self.app.submit_paper_order(self.ctx['alice'], self.acct, 'STOCK', 'buy', '1', as_of=NOW,
                                        idempotency_key='k')
        self.assertEqual((self.count('paper_accounts'), self.count('paper_ledger')), (1, 0))

    def test_operator_credentials_not_required(self):
        self.fund()
        self.assertEqual(self.ctx['alice'] is not None, True)
        self.assertEqual(len(self.app.paper_ledger(self.ctx['alice'], self.acct)), 1)

    def test_reads_do_not_require_paper_mode_and_do_not_write(self):
        self.fund()
        before = self.count('paper_ledger')
        self.app.paper_positions(self.ctx['alice'], self.acct)
        self.app.value_paper_account(self.ctx['alice'], self.acct, as_of=NOW)
        self.app.reconcile_paper_account(self.ctx['alice'], self.acct)
        self.assertEqual(self.count('paper_ledger'), before)
        self.assertEqual(self.count('valuation_snapshots'), 0)


class PaperTraceTests(PaperServiceBase):
    def test_trace_columns_match_m1_and_hold_no_payload(self):
        cols = [r[1] for r in self.s.db.execute('PRAGMA table_info(execution_traces)')]
        self.assertEqual(cols, TRACE_COLUMNS)
        self.fund('123.456', 'secret-key')
        self.buy('3', 'secret-order')
        self.app.value_paper_account(self.ctx['alice'], self.acct, as_of=NOW)
        dump = json.dumps(self.s.db.execute('SELECT * FROM execution_traces').fetchall())
        for needle in (self.acct, 'STOCK', '123.456', 'secret-key', 'secret-order', 'fixture', self.alice_p):
            self.assertNotIn(needle, dump)
        ops = {r[2] for r in self.app.traces(self.ctx['alice'])}
        self.assertTrue({'create_paper_account', 'paper_deposit', 'submit_paper_order',
                         'value_paper_account'} <= ops)

    def test_rejected_order_writes_nothing_and_traces_invalid(self):
        self.fund('5')
        with self.assertRaises(paper.PaperRejected) as caught:
            self.buy('2')
        self.assertEqual(caught.exception.reason, 'insufficient_cash')
        self.assertEqual(self.count('paper_ledger'), 1)
        last = self.app.traces(self.ctx['alice'])[-1]
        self.assertEqual((last[2], last[3]), ('submit_paper_order', 'invalid'))

    def test_fill_and_trace_commit_atomically(self):
        self.fund()
        ledger_before, traces_before = self.count('paper_ledger'), self.count('execution_traces')

        def deny(action, arg1, arg2, dbname, source):
            if action == sqlite3.SQLITE_INSERT and arg1 == 'execution_traces':
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.s.db.set_authorizer(deny)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.buy()
        finally:
            self.s.db.set_authorizer(None)
        self.assertEqual((self.count('paper_ledger'), self.count('execution_traces')),
                         (ledger_before, traces_before))

    def test_idempotent_retry_creates_no_second_fill_and_one_more_trace(self):
        self.fund()
        first = self.buy(key='same')
        traces = self.count('execution_traces')
        second = self.buy(key='same')
        self.assertEqual(first['entry_id'], second['entry_id'])
        self.assertTrue(second['replayed'] and not first['replayed'])
        self.assertEqual(self.count('paper_ledger'), 2)
        self.assertEqual(self.count('execution_traces'), traces + 1)


class PaperRetrySemanticsTests(PaperServiceBase):
    def test_same_key_in_different_account_is_independent(self):
        other = self.app.create_paper_account(self.ctx['alice'], self.alice_p, source='fixture', mode='paper')
        a = self.fund(key='k')
        b = self.app.paper_deposit(self.ctx['alice'], other, '1000', source='fixture', idempotency_key='k',
                                   as_of=NOW, mode='paper')
        self.assertNotEqual(a['entry_id'], b['entry_id'])
        self.assertFalse(b['replayed'])

    def test_retry_after_rejection_reevaluates_with_new_data(self):
        self.fund()
        with self.assertRaises(paper.PaperRejected) as caught:
            self.app.submit_paper_order(self.ctx['alice'], self.acct, 'ETF', 'buy', '1', as_of=NOW,
                                        idempotency_key='retry', mode='paper')
        self.assertEqual(caught.exception.reason, 'price_missing')
        observe(self.s, 'ETF', '20')
        fill = self.app.submit_paper_order(self.ctx['alice'], self.acct, 'ETF', 'buy', '1', as_of=NOW,
                                           idempotency_key='retry', mode='paper')
        self.assertFalse(fill['replayed'])
        self.assertEqual(Decimal(fill['unit_price']), Decimal(20))


class PaperDemoTests(unittest.TestCase):
    def test_demo_reports_payload_free_paper_keys(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cli.demo()
        report = json.loads(out.getvalue())
        self.assertEqual(report['paper_fill_count'], 1)
        self.assertEqual(report['paper_cash_text'], '990')
        self.assertIs(report['paper_real_holdings_unchanged'], True)
        self.assertEqual(report['mode'], 'read_only')


if __name__ == '__main__':
    unittest.main()
