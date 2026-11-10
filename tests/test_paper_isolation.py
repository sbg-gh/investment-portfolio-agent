"""THE CENTRAL M6 PROPERTY, written first: paper activity can never touch real holdings.

Paper state lives only in paper_accounts/paper_ledger. These tests prove it four ways:
byte-identical real-state dumps, a SQLite authorizer that denies every read/write of the
real tables, the converse (no copying in either direction), and structural checks. Fixtures
are obviously synthetic.
"""
import ast
import hashlib
import inspect
import json
import os
import re
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from portfolio_agent import paper
from portfolio_agent.auth import Authenticator
from portfolio_agent.chat import ChatService
from portfolio_agent.core import FixtureValidator, Store, ValidatedSecurity
from portfolio_agent.model import ModelConfig, ScriptedModel
from portfolio_agent.ports import Observation
from portfolio_agent.services import OperatorService, PortfolioService
from portfolio_agent.tools import ProposalRejected, ToolRegistry, validate_proposal
from tests.paper_fixtures import NOW, make_store, observe

REAL_TABLES = ('holdings', 'transactions', 'import_receipts', 'holding_events', 'baseline_coverage',
               'valuation_snapshots', 'paper_orders')
RECORDS = {'STOCK': ValidatedSecurity('STOCK', 'STOCK', 'stock', 'US', 'synthetic')}
FEES = (('none', '0'), ('flat', '1'), ('per_share', '0.01'), ('percentage', '0.001'))


def dump(db):
    out = {}
    for table in REAL_TABLES:
        rows = db.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
        text = json.dumps(rows, default=str, sort_keys=True)
        out[table] = (len(rows), hashlib.sha256(text.encode()).hexdigest())
    return out


def seed_market(store):
    """Operator-side market data (synthetic): fresh STOCK/ETF/FUND, a stale OLD, none for NOOBS/NOFUND."""
    store.register(ValidatedSecurity('OLD', 'OLD', 'stock', 'US', 'synthetic'))
    store.register(ValidatedSecurity('NOOBS', 'NOOBS', 'stock', 'US', 'synthetic'))
    store.register(ValidatedSecurity('NOFUND', 'NOFUND', 'mutual_fund', 'US', 'synthetic'))
    store.db.commit()
    observe(store, 'STOCK', '10')
    observe(store, 'ETF', '20')
    observe(store, 'FUND', '30')
    observe(store, 'OLD', '5', age=timedelta(days=5))


def paper_workload(app, ctx, portfolio):
    """Broad paper activity for one user; returns the rejection reasons observed."""
    reasons = []

    def attempt(*args, **kwargs):
        try:
            app.submit_paper_order(ctx, *args, as_of=NOW, mode='paper', **kwargs)
        except paper.PaperRejected as exc:
            reasons.append(exc.reason)

    accounts = []
    for kind, amount in FEES:
        acct = app.create_paper_account(ctx, portfolio, source='fixture', fee_kind=kind,
                                        fee_amount=amount, mode='paper')
        app.paper_deposit(ctx, acct, '10000', source='fixture', idempotency_key='dep', as_of=NOW, mode='paper')
        app.submit_paper_order(ctx, acct, 'STOCK', 'buy', '10', as_of=NOW, idempotency_key='b1', mode='paper')
        accounts.append(acct)
    main = accounts[0]
    app.submit_paper_order(ctx, main, 'STOCK', 'buy', '5', as_of=NOW, idempotency_key='b2', mode='paper')
    app.submit_paper_order(ctx, main, 'STOCK', 'sell', '12', as_of=NOW, idempotency_key='s1', mode='paper')
    app.submit_paper_order(ctx, main, 'ETF', 'buy', '1.5', as_of=NOW, idempotency_key='e1', mode='paper')
    app.submit_paper_order(ctx, main, 'FUND', 'buy', '2', as_of=NOW, idempotency_key='f1', mode='paper')
    app.submit_paper_order(ctx, main, 'STOCK', 'buy', '10', as_of=NOW, idempotency_key='b1', mode='paper')  # retry
    attempt(main, 'STOCK', 'buy', '1000000', idempotency_key='r1')
    attempt(main, 'ETF', 'sell', '99', idempotency_key='r2')
    attempt(main, 'OLD', 'buy', '1', idempotency_key='r3')
    attempt(main, 'NOOBS', 'buy', '1', idempotency_key='r4')
    attempt(main, 'NOFUND', 'buy', '1', idempotency_key='r5')
    for acct in accounts:
        app.paper_ledger(ctx, acct)
        app.paper_positions(ctx, acct)
        app.value_paper_account(ctx, acct, as_of=NOW)
        app.reconcile_paper_account(ctx, acct)
    return reasons, accounts


class IsolationBase(unittest.TestCase):
    def build(self, store, validator=None):
        for user in ('alice', 'bob', 'admin'):
            if not store.db.execute('SELECT 1 FROM users WHERE id=?', (user,)).fetchone():
                store.create_user(user)
        auth = Authenticator(store)
        tokens = {u: auth.provision(u, role='operator' if u == 'admin' else 'user') for u in ('alice', 'bob', 'admin')}
        app = PortfolioService(store, auth, validator or FixtureValidator(RECORDS))
        return auth, tokens, app

    def real_state(self, app, ctx):
        """Real holdings, a transaction, coverage, a valuation snapshot and an M0 paper order."""
        portfolio = app.create_portfolio(ctx, 'real')
        app.import_holding(ctx, portfolio, 'STOCK', '4')
        app.import_holding_with_provenance(ctx, portfolio, 'STOCK', '4', source='fixture',
                                           market_as_of=NOW, idempotency_key='imp')
        app.record_transaction(ctx, portfolio, 'STOCK', 'buy', '4', source='fixture',
                               idempotency_key='tx', occurred_at=NOW)
        app.value_portfolio(ctx, portfolio, as_of=NOW, source='fixture')
        app.record_paper_order(ctx, portfolio, 'STOCK', '1', '10', mode='paper')
        return portfolio


class ByteIdenticalRealStateTests(IsolationBase):
    def test_broad_paper_activity_leaves_real_state_byte_identical(self):
        store = make_store()
        self.addCleanup(store.close)
        seed_market(store)
        auth, tokens, app = self.build(store)
        ctx = {u: auth.authenticate(t) for u, t in tokens.items()}
        portfolios = {u: self.real_state(app, ctx[u]) for u in ('alice', 'bob')}
        before = dump(store.db)
        self.assertTrue(all(count > 0 for count, _ in before.values()), before)
        recon_before = {u: app.reconcile(ctx[u], portfolios[u]) for u in portfolios}
        holdings_before = {u: app.holdings(ctx[u], portfolios[u]) for u in portfolios}
        for user in ('alice', 'bob'):
            reasons, _ = paper_workload(app, ctx[user], portfolios[user])
            self.assertEqual(reasons, ['insufficient_cash', 'insufficient_position', 'price_stale',
                                       'price_missing', 'nav_missing'])
        self.assertEqual(dump(store.db), before)
        self.assertEqual(store.reconcile_baseline_coverage(), [])
        self.assertEqual({u: app.reconcile(ctx[u], portfolios[u]) for u in portfolios}, recon_before)
        self.assertEqual({u: app.holdings(ctx[u], portfolios[u]) for u in portfolios}, holdings_before)
        self.assertGreater(store.db.execute('SELECT COUNT(*) FROM paper_ledger').fetchone()[0], 20)


class AuthorizerIsolationTests(IsolationBase):
    def test_paper_workload_needs_no_access_to_real_tables(self):
        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        setup = make_store(path)
        self.addCleanup(setup.close)
        seed_market(setup)
        setup_auth, tokens, setup_app = self.build(setup)
        portfolios = {u: self.real_state(setup_app, setup_auth.authenticate(tokens[u])) for u in ('alice', 'bob')}
        setup.db.commit()
        # Fresh paper connection: the authorizer is installed before any statement is prepared.
        store = Store(path)
        self.addCleanup(store.close)
        denied = []

        def authorizer(action, arg1, arg2, dbname, source):
            if arg1 in REAL_TABLES and action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE,
                                                  sqlite3.SQLITE_DELETE, sqlite3.SQLITE_READ):
                denied.append((action, arg1))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        store.db.set_authorizer(authorizer)
        auth = Authenticator(store)
        app = PortfolioService(store, auth, FixtureValidator(RECORDS))
        ctx = {u: auth.authenticate(tokens[u]) for u in ('alice', 'bob')}
        for user in ('alice', 'bob'):
            reasons, accounts = paper_workload(app, ctx[user], portfolios[user])
            self.assertEqual(len(reasons), 5)
        self.assertEqual(denied, [])
        # Control: the authorizer is live, so a real-holdings read on this connection fails.
        with self.assertRaises(sqlite3.DatabaseError):
            app.holdings(ctx['alice'], portfolios['alice'])
        self.assertTrue(denied)


class ConverseTests(IsolationBase):
    def test_no_copying_in_either_direction(self):
        store = make_store()
        self.addCleanup(store.close)
        seed_market(store)
        auth, tokens, app = self.build(store)
        ctx = auth.authenticate(tokens['alice'])
        portfolio = self.real_state(app, ctx)
        real = app.holdings(ctx, portfolio)
        self.assertTrue(real)
        acct = app.create_paper_account(ctx, portfolio, source='fixture', mode='paper')
        state = app.paper_positions(ctx, acct)
        self.assertEqual((state['positions'], state['cash']), ([], '0'))
        with self.assertRaises(paper.PaperRejected) as caught:
            app.submit_paper_order(ctx, acct, 'STOCK', 'sell', '1', as_of=NOW, idempotency_key='s', mode='paper')
        self.assertEqual(caught.exception.reason, 'insufficient_position')
        app.paper_deposit(ctx, acct, '100', source='fixture', idempotency_key='d', as_of=NOW, mode='paper')
        app.submit_paper_order(ctx, acct, 'STOCK', 'buy', '3', as_of=NOW, idempotency_key='b', mode='paper')
        self.assertEqual(app.holdings(ctx, portfolio), real)
        cash = app.paper_positions(ctx, acct)['cash']
        positions = app.paper_positions(ctx, acct)['positions']
        app.record_transaction(ctx, portfolio, 'STOCK', 'sell', '1', source='fixture',
                               idempotency_key='tx2', occurred_at=NOW)
        self.assertEqual(app.paper_positions(ctx, acct)['cash'], cash)
        self.assertEqual(app.paper_positions(ctx, acct)['positions'], positions)


class StructuralTests(unittest.TestCase):
    ALLOWED = {'contextlib', 'dataclasses', 'datetime', 'decimal', 'hashlib', 'json', 'typing', 'uuid',
               '.valuation', '.core'}

    def test_paper_module_imports_are_allow_listed(self):
        path = Path(inspect.getfile(paper))
        tree = ast.parse(path.read_text())
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found |= {alias.name.split('.')[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                found.add('.' * node.level + (node.module or ''))
        self.assertLessEqual(found, self.ALLOWED, found - self.ALLOWED)

    def test_paper_source_has_no_real_table_references(self):
        source = Path(inspect.getfile(paper)).read_text()
        pattern = r'\b(FROM|INTO|UPDATE|JOIN)\s+(' + '|'.join(REAL_TABLES) + r')\b'
        self.assertIsNone(re.search(pattern, source, re.I))

    def test_service_methods_have_no_owner_parameter_or_live_name(self):
        names = [n for n in dir(PortfolioService) if 'paper' in n]
        self.assertGreaterEqual(len(names), 8)  # seven M6 methods + the M0 record_paper_order
        for name in names:
            self.assertNotIn('live', name)
            params = inspect.signature(getattr(PortfolioService, name)).parameters
            self.assertFalse({'user', 'user_id', 'owner', 'owner_id'} & set(params), name)


class ChatBoundaryTests(IsolationBase):
    def test_chat_cannot_reach_paper_operations(self):
        allowed = ToolRegistry().allowed_names()
        for name in allowed:
            self.assertFalse(re.search(r'paper|submit|deposit|order', name), name)
        with self.assertRaises(ProposalRejected):
            validate_proposal('submit_paper_order', {'account_id': 'x'}, allowed)
        store = make_store()
        self.addCleanup(store.close)
        seed_market(store)
        auth, tokens, app = self.build(store)
        ctx = auth.authenticate(tokens['alice'])
        portfolio = app.create_portfolio(ctx, 'chat')
        acct = app.create_paper_account(ctx, portfolio, source='fixture', mode='paper')
        app.paper_deposit(ctx, acct, '100', source='fixture', idempotency_key='d', as_of=NOW, mode='paper')
        proposal = json.dumps({'explanation': 'Buying for you.', 'tool_calls': [{
            'name': 'submit_paper_order',
            'args': {'account_id': acct, 'security_id': 'STOCK', 'side': 'buy', 'quantity': '1'}}]})
        chat = ChatService(app, ScriptedModel([proposal]), config=ModelConfig.from_env(env={}),
                           clock=lambda: NOW)
        out = chat.chat(ctx, conversation_id='c1', message='buy one share', portfolio_id=portfolio)
        self.assertEqual(out['proposed_actions'], [])
        self.assertEqual(store.db.execute("SELECT COUNT(*) FROM paper_ledger WHERE entry_type!='deposit'")
                         .fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
