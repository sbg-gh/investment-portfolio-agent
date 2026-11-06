"""Authenticated ChatService enforcing THE CENTRAL SECURITY PROPERTY, written first.

model proposal -> deterministic schema validation + M1 re-authorization -> execution.
Identity comes ONLY from the AuthContext; the chat runs through PortfolioService._run with
operation 'chat' and a PAYLOAD-FREE trace. Every proposed tool call is re-validated and
re-authorized before execution; only READ-ONLY tools run; evidence reuses M2a freshness and
is never fabricated.
"""
import json
from datetime import datetime, timezone
from decimal import Decimal
import unittest

from portfolio_agent.auth import Authenticator
from portfolio_agent.core import FixtureValidator, Store, ValidatedSecurity
from portfolio_agent.services import OperatorService, PortfolioService
from portfolio_agent.ports import Observation
from portfolio_agent.model import ModelConfig, ScriptedModel
from portfolio_agent.chat import ChatService

NOW = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)
AS_OF = NOW.isoformat()


def reply(explanation, tool_calls=None):
    return json.dumps({'explanation': explanation, 'tool_calls': tool_calls or []})


class ChatServiceTests(unittest.TestCase):
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
                   for sym in ('ALICECO', 'BOBSECRET', 'STALECO')}
        self.app = PortfolioService(self.s, self.auth, FixtureValidator(records))
        self.ops = OperatorService(self.app)
        self.alice_p = self.app.create_portfolio(self.ctx['alice'], 'alice private')
        self.bob_p = self.app.create_portfolio(self.ctx['bob'], 'bob private')
        self.app.import_holding(self.ctx['alice'], self.alice_p, 'ALICECO', '3')
        self.app.import_holding(self.ctx['bob'], self.bob_p, 'BOBSECRET', '99')
        self.ops.ingest_observation(self.ctx['admin'], Observation(
            'ALICECO', Decimal('10'), NOW, 'fixture', 'price', fetched_at=NOW))
        self.config = ModelConfig.from_env(env={})

    def chat_service(self, replies):
        return ChatService(self.app, ScriptedModel(replies), config=self.config,
                           clock=lambda: NOW)

    def test_happy_path_response_shape(self):
        svc = self.chat_service([reply(
            'Here is your portfolio value.',
            [{'name': 'value_portfolio',
              'args': {'portfolio_id': self.alice_p, 'as_of': AS_OF, 'source': 'fixture'}}])])
        out = svc.chat(self.ctx['alice'], conversation_id='conv-1',
                       message='what is my portfolio worth?', portfolio_id=self.alice_p)
        self.assertEqual(set(out), {'explanation', 'evidence', 'proposed_actions',
                                    'conversation_id', 'request_id'})
        self.assertEqual(out['conversation_id'], 'conv-1')
        self.assertTrue(out['request_id'])
        self.assertEqual(out['proposed_actions'][0]['name'], 'value_portfolio')
        self.assertTrue(out['evidence'])

    def test_unauthorized_portfolio_denied(self):
        svc = self.chat_service([reply('ok', [])])
        with self.assertRaises(PermissionError):
            svc.chat(self.ctx['alice'], conversation_id='c', message='hi',
                     portfolio_id=self.bob_p)

    def test_forged_or_expired_context_denied(self):
        svc = self.chat_service([reply('ok', [])])
        from dataclasses import replace
        forged = replace(self.ctx['alice'], signature='deadbeef')
        with self.assertRaises(PermissionError):
            svc.chat(forged, conversation_id='c', message='hi', portfolio_id=self.alice_p)

    def test_trace_is_payload_free(self):
        svc = self.chat_service([reply(
            'secret body here',
            [{'name': 'list_holdings', 'args': {'portfolio_id': self.alice_p}}])])
        svc.chat(self.ctx['alice'], conversation_id='c',
                 message='ALICECO holdings please', portfolio_id=self.alice_p)
        rows = self.app.traces(self.ctx['alice'])
        chat_rows = [r for r in rows if r[2] == 'chat']
        self.assertTrue(chat_rows)
        serialized = json.dumps(rows)
        self.assertNotIn('ALICECO', serialized)
        self.assertNotIn('secret body here', serialized)
        self.assertNotIn('fixture', serialized)

    def test_stale_and_missing_evidence_not_fabricated(self):
        # STALECO has no observation -> missing; ALICECO is fresh.
        self.app.import_holding(self.ctx['alice'], self.alice_p, 'STALECO', '1')
        svc = self.chat_service([reply(
            'value',
            [{'name': 'value_portfolio',
              'args': {'portfolio_id': self.alice_p, 'as_of': AS_OF, 'source': 'fixture'}}])])
        out = svc.chat(self.ctx['alice'], conversation_id='c', message='value?',
                       portfolio_id=self.alice_p)
        statuses = {e['security_id']: e['status'] for e in out['evidence']}
        self.assertEqual(statuses['STALECO'], 'missing')
        self.assertEqual(statuses['ALICECO'], 'fresh')

    def test_unparseable_model_output_refuses_and_executes_nothing(self):
        svc = self.chat_service(['this is not json at all'])
        out = svc.chat(self.ctx['alice'], conversation_id='c', message='hi',
                       portfolio_id=self.alice_p)
        self.assertEqual(out['proposed_actions'], [])
        self.assertTrue(out['explanation'])


if __name__ == '__main__':
    unittest.main()
