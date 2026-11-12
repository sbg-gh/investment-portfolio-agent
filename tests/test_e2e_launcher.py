"""The e2e launcher provisions only synthetic data in a temp database and needs no ui extra to prepare."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from portfolio_agent.auth import Authenticator
from portfolio_agent.chat import ChatService
from portfolio_agent.core import Store
from portfolio_agent.model import ModelConfig
LAUNCHER = Path(__file__).resolve().parents[1] / 'ui' / 'e2e' / 'serve_for_e2e.py'
def load_launcher():
    spec = importlib.util.spec_from_file_location('serve_for_e2e', LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
    def test_prepare_issues_a_user_and_an_operator_token_in_a_temp_database(self):
        launcher = load_launcher()
        db, tokens = launcher.prepare(self.tmp.name)
        self.assertTrue(str(db).startswith(os.path.realpath(self.tmp.name)))
        store = Store(str(db))
        self.addCleanup(store.close)
        auth = Authenticator(store)
        user = auth.authenticate(tokens['user'])
        operator = auth.authenticate(tokens['operator'])
        self.assertNotEqual(user.user_id, operator.user_id)
        auth.require(user)
        auth.require(operator, operator=True)
        with self.assertRaises(PermissionError):
            auth.require(user, operator=True)
        self.assertTrue(user.user_id.startswith('e2e-') and operator.user_id.startswith('e2e-'),
                        'ids are obviously synthetic')

    def test_scripted_model_replies_parse_through_the_unchanged_chat_gate(self):
        launcher = load_launcher()
        model = launcher.E2EModel()
        raw = model.generate('prompt', max_tokens=100)
        from portfolio_agent.chat import _parse
        explanation, calls = _parse(raw)
        self.assertIn('E2E scripted reply', explanation)
        self.assertEqual(calls, [])
        # repeated turns keep answering (not a fixed-length queue that exhausts mid-run)
        for _ in range(5):
            model.generate('prompt', max_tokens=100)

    def test_launcher_refuses_a_non_temporary_database_path(self):
        launcher = load_launcher()
        with self.assertRaises(ValueError):
            launcher.prepare('/')
