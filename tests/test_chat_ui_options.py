"""Opt-in ChatService options for the UI; the default behavior and key set are unchanged."""
import json
from datetime import datetime, timezone
from decimal import Decimal
import unittest

from portfolio_agent.auth import Authenticator
from portfolio_agent.chat import ChatService
from portfolio_agent.core import FixtureValidator, Store, ValidatedSecurity
from portfolio_agent.model import ModelConfig, ScriptedModel
from portfolio_agent.ports import Observation
from portfolio_agent.services import OperatorService, PortfolioService

NOW = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)
DEFAULT_KEYS = {'explanation', 'evidence', 'proposed_actions', 'conversation_id', 'request_id'}


class RaisingModel:
    def generate(self, prompt, *, max_tokens):
        raise TimeoutError('secret-internal-host:11434 timed out')


class RecordingModel(ScriptedModel):
    def generate(self, prompt, *, max_tokens):
        self.prompt = prompt
        return super().generate(prompt, max_tokens=max_tokens)


def reply(text):
    return json.dumps({'explanation': text, 'tool_calls': []})


class ChatUiOptionTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        self.addCleanup(self.store.close)
        for user in ('alice', 'admin'):
            self.store.create_user(user)
        self.auth = Authenticator(self.store)
        self.alice = self.auth.authenticate(self.auth.provision('alice'))
        self.admin = self.auth.authenticate(self.auth.provision('admin', role='operator'))
        records = {'ACO': ValidatedSecurity('ACO', 'ACO', 'stock', 'US', 'synthetic')}
        self.app = PortfolioService(self.store, self.auth, FixtureValidator(records))
        self.pid = self.app.create_portfolio(self.alice, 'p')
        self.app.import_holding(self.alice, self.pid, 'ACO', '3')
        self.config = ModelConfig.from_env(env={})

    def observe(self, source):
        OperatorService(self.app).ingest_observation(self.admin, Observation(
            'ACO', Decimal('10'), NOW, source, 'price', fetched_at=NOW))

    def service(self, model, **kwargs):
        return ChatService(self.app, model, config=self.config, clock=lambda: NOW, **kwargs)

    def test_default_raises_on_model_failure_and_keeps_key_set(self):
        with self.assertRaises(TimeoutError):
            self.service(RaisingModel()).chat(self.alice, conversation_id='c', message='hi',
                                              portfolio_id=self.pid)
        out = self.service(ScriptedModel([reply('ok')])).chat(
            self.alice, conversation_id='c', message='hi', portfolio_id=self.pid)
        self.assertEqual(set(out), DEFAULT_KEYS)

    def test_facts_mode_degrades_to_deterministic_explanation_with_evidence(self):
        self.observe('fixture')
        out = self.service(RaisingModel(), on_model_error='facts').chat(
            self.alice, conversation_id='c', message='hi', portfolio_id=self.pid)
        self.assertEqual(out['model_status'], 'unavailable')
        self.assertEqual(out['proposed_actions'], [])
        self.assertEqual(out['evidence'][0]['status'], 'fresh')
        self.assertNotIn('secret-internal-host', json.dumps(out))
        self.assertIn('unavailable', out['explanation'].lower())

    def test_facts_mode_without_portfolio_has_no_evidence(self):
        out = self.service(RaisingModel(), on_model_error='facts').chat(
            self.alice, conversation_id='c', message='hi')
        self.assertEqual(out['evidence'], [])
        self.assertEqual(out['model_status'], 'unavailable')

    def test_facts_mode_marks_ok_when_model_answers(self):
        out = self.service(ScriptedModel([reply('fine')]), on_model_error='facts').chat(
            self.alice, conversation_id='c', message='hi', portfolio_id=self.pid)
        self.assertEqual(out['model_status'], 'ok')
        self.assertEqual(set(out), DEFAULT_KEYS | {'model_status'})

    def test_facts_mode_still_denies_foreign_portfolio(self):
        with self.assertRaises(PermissionError):
            self.service(RaisingModel(), on_model_error='facts').chat(
                self.admin, conversation_id='c', message='hi', portfolio_id=self.pid)

    def test_unknown_error_mode_rejected(self):
        with self.assertRaises(ValueError):
            self.service(RaisingModel(), on_model_error='swallow')

    def test_valuation_source_is_used_for_evidence(self):
        self.observe('manual')
        default = self.service(ScriptedModel([reply('a')])).chat(
            self.alice, conversation_id='c', message='hi', portfolio_id=self.pid)
        self.assertEqual(default['evidence'][0]['status'], 'missing')
        custom = self.service(ScriptedModel([reply('a')]), valuation_source='manual').chat(
            self.alice, conversation_id='c', message='hi', portfolio_id=self.pid)
        self.assertEqual(custom['evidence'][0]['status'], 'fresh')

    def test_prompt_preamble_is_prepended_as_system_text_only(self):
        model = RecordingModel([reply('a')])
        self.service(model, prompt_preamble='Reply as JSON.').chat(
            self.alice, conversation_id='c', message='hello', portfolio_id=self.pid)
        self.assertTrue(model.prompt.startswith('Reply as JSON.\n'))
        self.assertIn('[USER_MESSAGE]\nhello', model.prompt)
        plain = RecordingModel([reply('a')])
        self.service(plain).chat(self.alice, conversation_id='c', message='hello',
                                 portfolio_id=self.pid)
        self.assertTrue(plain.prompt.startswith('You are a read-only portfolio assistant.'))

    def test_invalid_options_rejected(self):
        for kwargs in ({'valuation_source': ''}, {'prompt_preamble': 5}):
            with self.assertRaises(ValueError):
                self.service(RaisingModel(), **kwargs)


if __name__ == '__main__':
    unittest.main()
