"""Bounded, owner-scoped context assembly, written before implementation.

Authorizes the portfolio through the M1 path BEFORE any read, gathers ONLY that owner's
holdings + an M2a valuation (with fresh|stale|missing), serializes to a prompt string, and
enforces estimate_tokens(prompt) <= budget. One user's data must never enter another user's
context, and an unauthorized portfolio_id is denied by re-authorization.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from portfolio_agent.auth import Authenticator
from portfolio_agent.core import FixtureValidator, Store, ValidatedSecurity
from portfolio_agent.services import OperatorService, PortfolioService
from portfolio_agent.ports import Observation
from portfolio_agent.context import BoundedContextError, assemble_context
from portfolio_agent.model import estimate_tokens

NOW = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)


class ContextAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.s = Store()
        self.addCleanup(self.s.close)
        for u in ('alice', 'bob', 'admin'):
            self.s.create_user(u)
        self.auth = Authenticator(self.s)
        self.ctx = {u: self.auth.authenticate(
            self.auth.provision(u, role='operator' if u == 'admin' else 'user'))
            for u in ('alice', 'bob', 'admin')}
        records = {sym: ValidatedSecurity(sym, sym, 'stock', 'US', 'synthetic')
                   for sym in ('ALICECO', 'BOBSECRET')}
        self.app = PortfolioService(self.s, self.auth, FixtureValidator(records))
        self.ops = OperatorService(self.app)
        self.alice_p = self.app.create_portfolio(self.ctx['alice'], 'alice private')
        self.bob_p = self.app.create_portfolio(self.ctx['bob'], 'bob private')
        self.app.import_holding(self.ctx['alice'], self.alice_p, 'ALICECO', '3')
        self.app.import_holding(self.ctx['bob'], self.bob_p, 'BOBSECRET', '99')
        self.ops.ingest_observation(self.ctx['admin'], Observation(
            'ALICECO', Decimal('10'), NOW, 'fixture', 'price', fetched_at=NOW))

    def assemble(self, who, pid, **kwargs):
        kwargs.setdefault('as_of', NOW)
        kwargs.setdefault('source', 'fixture')
        return assemble_context(self.app, self.ctx[who], pid, **kwargs)

    def test_context_never_exceeds_budget(self):
        result = self.assemble('alice', self.alice_p, budget=1000)
        self.assertLessEqual(estimate_tokens(result.prompt), 1000)

    def test_tiny_budget_raises_bounded_context_error(self):
        with self.assertRaises(BoundedContextError):
            self.assemble('alice', self.alice_p, budget=1)

    def test_cross_user_data_never_enters_context(self):
        result = self.assemble('alice', self.alice_p, budget=4096)
        self.assertIn('ALICECO', result.prompt)
        self.assertNotIn('BOBSECRET', result.prompt)
        self.assertNotIn('99', result.prompt)

    def test_unauthorized_portfolio_denied(self):
        with self.assertRaises(PermissionError):
            self.assemble('alice', self.bob_p, budget=4096)

    def test_evidence_carries_freshness_status(self):
        result = self.assemble('alice', self.alice_p, budget=4096)
        self.assertEqual(result.valuation['status'], 'complete')
        line = result.valuation['lines'][0]
        self.assertEqual(line['status'], 'fresh')


if __name__ == '__main__':
    unittest.main()
