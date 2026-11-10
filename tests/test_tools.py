"""READ-ONLY specialist tool registry + deterministic schema gate, written first.

The model may only PROPOSE a tool call. validate_proposal is the deterministic gate that
rejects an unknown/aliased name, a missing/extra/wrong-typed arg, so NOTHING reaches
execution unless it matches a declared read-only tool's schema exactly. Tools never accept
an owner id; identity is supplied by the AuthContext at execution time.
"""
import unittest

from portfolio_agent.tools import (
    ProposalRejected,
    ToolRegistry,
    validate_proposal,
)


class ToolSchemaTests(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry()
        self.allowed = self.registry.allowed_names()

    def test_registry_exposes_only_read_only_tools(self):
        self.assertEqual(set(self.allowed), {'list_holdings', 'value_portfolio',
                                             'intraday_quote', 'dated_nav'})

    def test_wellformed_list_holdings_validates(self):
        spec = validate_proposal('list_holdings', {'portfolio_id': 'p-1'}, self.allowed)
        self.assertEqual(spec.name, 'list_holdings')
        self.assertEqual(spec.args, {'portfolio_id': 'p-1'})

    def test_wellformed_value_portfolio_validates(self):
        spec = validate_proposal('value_portfolio',
                                 {'portfolio_id': 'p-1', 'as_of': '2026-11-06T21:00:00+00:00',
                                  'source': 'fixture'}, self.allowed)
        self.assertEqual(spec.name, 'value_portfolio')

    def test_unknown_name_rejected(self):
        with self.assertRaises(ProposalRejected):
            validate_proposal('drop_table', {'portfolio_id': 'p-1'}, self.allowed)

    def test_aliased_name_rejected(self):
        with self.assertRaises(ProposalRejected):
            validate_proposal('List_Holdings', {'portfolio_id': 'p-1'}, self.allowed)

    def test_missing_arg_rejected(self):
        with self.assertRaises(ProposalRejected):
            validate_proposal('value_portfolio', {'portfolio_id': 'p-1'}, self.allowed)

    def test_extra_arg_rejected(self):
        with self.assertRaises(ProposalRejected):
            validate_proposal('list_holdings',
                              {'portfolio_id': 'p-1', 'owner': 'bob'}, self.allowed)

    def test_wrong_type_arg_rejected(self):
        with self.assertRaises(ProposalRejected):
            validate_proposal('list_holdings', {'portfolio_id': 123}, self.allowed)

    def test_owner_id_argument_is_not_part_of_any_schema(self):
        for name in self.allowed:
            self.assertNotIn('user_id', self.registry.schema(name).required)
            self.assertNotIn('owner', self.registry.schema(name).required)


class ValuationToolTests(unittest.TestCase):
    """intraday_quote / dated_nav: read-only, {portfolio_id, as_of} only, fixed sources."""

    NEW_TOOLS = ('intraday_quote', 'dated_nav')
    AS_OF = '2026-06-01T21:00:00+00:00'

    def setUp(self):
        self.allowed = ToolRegistry().allowed_names()

    def test_schema_is_exactly_portfolio_id_and_as_of(self):
        for name in self.NEW_TOOLS:
            self.assertEqual(ToolRegistry().schema(name).required,
                             {'portfolio_id': str, 'as_of': str})
            spec = validate_proposal(name, {'portfolio_id': 'p', 'as_of': self.AS_OF}, self.allowed)
            self.assertEqual(spec.name, name)

    def test_extra_missing_and_wrong_typed_args_rejected(self):
        for name in self.NEW_TOOLS:
            for args in ({'portfolio_id': 'p'}, {'as_of': self.AS_OF},
                         {'portfolio_id': 'p', 'as_of': self.AS_OF, 'source': 'x'},
                         {'portfolio_id': 'p', 'as_of': self.AS_OF, 'owner': 'bob'},
                         {'portfolio_id': 1, 'as_of': self.AS_OF},
                         {'portfolio_id': 'p', 'as_of': 20260601}):
                with self.assertRaises(ProposalRejected):
                    validate_proposal(name, args, self.allowed)

    def test_sources_are_fixed_constants(self):
        from portfolio_agent.tools import INTRADAY_SOURCE, NAV_SOURCE
        self.assertEqual(INTRADAY_SOURCE, 'synthetic-intraday-quote')
        self.assertEqual(NAV_SOURCE, 'synthetic-dated-nav')

    def test_execute_delegates_to_value_portfolio_with_fixed_source(self):
        from datetime import datetime
        from portfolio_agent.tools import INTRADAY_SOURCE, NAV_SOURCE

        class Spy:
            def __init__(self):
                self.calls = []

            def value_portfolio(self, context, portfolio_id, *, as_of, source, policy=None):
                self.calls.append((context, portfolio_id, as_of, source))
                return {'lines': []}

        spy = Spy()
        args = {'portfolio_id': 'p-1', 'as_of': self.AS_OF}
        ToolRegistry().execute('intraday_quote', args, spy, 'ctx')
        ToolRegistry().execute('dated_nav', args, spy, 'ctx')
        expected_as_of = datetime.fromisoformat(self.AS_OF)
        self.assertEqual(spy.calls, [('ctx', 'p-1', expected_as_of, INTRADAY_SOURCE),
                                     ('ctx', 'p-1', expected_as_of, NAV_SOURCE)])

    def test_bad_as_of_is_rejected_before_any_read(self):
        class Spy:
            def value_portfolio(self, *a, **k):
                raise AssertionError('must not read')

        for name in self.NEW_TOOLS:
            with self.assertRaises(ProposalRejected):
                ToolRegistry().execute(name, {'portfolio_id': 'p', 'as_of': 'nope'}, Spy(), 'ctx')

    def test_cross_user_portfolio_is_denied_at_execution(self):
        from portfolio_agent.auth import Authenticator
        from portfolio_agent.core import FixtureValidator, Store
        from portfolio_agent.services import PortfolioService
        store = Store()
        self.addCleanup(store.close)
        for user in ('alice', 'bob'):
            store.create_user(user)
        auth = Authenticator(store)
        alice = auth.authenticate(auth.provision('alice'))
        bob = auth.authenticate(auth.provision('bob'))
        service = PortfolioService(store, auth, FixtureValidator({}))
        bob_portfolio = service.create_portfolio(bob, 'bob private')
        for name in self.NEW_TOOLS:
            with self.assertRaises(PermissionError):
                ToolRegistry().execute(
                    name, {'portfolio_id': bob_portfolio, 'as_of': self.AS_OF}, service, alice)


if __name__ == '__main__':
    unittest.main()
