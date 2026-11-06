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
        self.assertEqual(set(self.allowed), {'list_holdings', 'value_portfolio'})

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


if __name__ == '__main__':
    unittest.main()
