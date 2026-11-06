"""Vendor-neutral local-model adapter contract, written before implementation.

Covers config defaults + env overrides, the chars/4 token estimator, the deterministic
offline ScriptedModel, the concurrency bound, the output-token cap rejection, and the
OpenAI-compatible HTTP adapter exercised ONLY through a monkeypatched urllib opener.
No test here touches the network or a live server.
"""
import json
import threading
import time
import unittest

from portfolio_agent import model as model_mod
from portfolio_agent.model import (
    BoundedModel,
    ModelConfig,
    OpenAICompatibleModel,
    ScriptedModel,
    estimate_tokens,
)


class ModelConfigTests(unittest.TestCase):
    def test_defaults_when_env_absent(self):
        cfg = ModelConfig.from_env(env={})
        self.assertEqual(cfg.endpoint, 'http://127.0.0.1:8080')
        self.assertEqual(cfg.max_concurrency, 1)
        self.assertEqual(cfg.context_budget, 4096)
        self.assertEqual(cfg.max_output_tokens, 512)

    def test_env_overrides(self):
        cfg = ModelConfig.from_env(env={
            'MODEL_ENDPOINT': 'http://localhost:11434/v1',
            'MODEL_NAME': 'llama3.2:3b',
            'MODEL_MAX_CONCURRENCY': '2',
            'MODEL_CONTEXT_BUDGET': '2048',
            'MODEL_MAX_OUTPUT_TOKENS': '256',
        })
        self.assertEqual(cfg.endpoint, 'http://localhost:11434/v1')
        self.assertEqual(cfg.name, 'llama3.2:3b')
        self.assertEqual(cfg.max_concurrency, 2)
        self.assertEqual(cfg.context_budget, 2048)
        self.assertEqual(cfg.max_output_tokens, 256)

    def test_rejects_nonpositive_bounds(self):
        with self.assertRaises(ValueError):
            ModelConfig.from_env(env={'MODEL_MAX_CONCURRENCY': '0'})
        with self.assertRaises(ValueError):
            ModelConfig.from_env(env={'MODEL_CONTEXT_BUDGET': '-1'})
        with self.assertRaises(ValueError):
            ModelConfig.from_env(env={'MODEL_MAX_OUTPUT_TOKENS': 'x'})


class TokenEstimateTests(unittest.TestCase):
    def test_chars_over_four_deterministic(self):
        self.assertEqual(estimate_tokens(''), 0)
        self.assertEqual(estimate_tokens('abcd'), 1)
        self.assertEqual(estimate_tokens('abcde'), 2)  # ceil(5/4)
        self.assertEqual(estimate_tokens('a' * 400), 100)


class ScriptedModelTests(unittest.TestCase):
    def test_pops_canned_replies_in_order(self):
        m = ScriptedModel(['first', 'second'])
        self.assertEqual(m.generate('p', max_tokens=10), 'first')
        self.assertEqual(m.generate('p', max_tokens=10), 'second')

    def test_exhausted_queue_raises(self):
        m = ScriptedModel(['only'])
        m.generate('p', max_tokens=10)
        with self.assertRaises(Exception):
            m.generate('p', max_tokens=10)


class BoundedModelTests(unittest.TestCase):
    def test_rejects_output_tokens_above_cap(self):
        cfg = ModelConfig.from_env(env={'MODEL_MAX_OUTPUT_TOKENS': '16'})
        m = BoundedModel(ScriptedModel(['ok']), cfg)
        with self.assertRaises(ValueError):
            m.generate('p', max_tokens=17)

    def test_within_cap_delegates(self):
        cfg = ModelConfig.from_env(env={'MODEL_MAX_OUTPUT_TOKENS': '16'})
        m = BoundedModel(ScriptedModel(['ok']), cfg)
        self.assertEqual(m.generate('p', max_tokens=16), 'ok')

    def test_concurrency_bound_serializes_calls(self):
        cfg = ModelConfig.from_env(env={'MODEL_MAX_CONCURRENCY': '1',
                                        'MODEL_MAX_OUTPUT_TOKENS': '512'})
        observed = []
        inside = threading.Lock()
        active = {'n': 0, 'max': 0}

        class SlowModel:
            def generate(self, prompt, *, max_tokens):
                with inside:
                    active['n'] += 1
                    active['max'] = max(active['max'], active['n'])
                time.sleep(0.02)
                with inside:
                    active['n'] -= 1
                return 'done'

        m = BoundedModel(SlowModel(), cfg)

        def call():
            observed.append(m.generate('p', max_tokens=10))

        threads = [threading.Thread(target=call) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(observed, ['done'] * 4)
        self.assertEqual(active['max'], 1)


class OpenAICompatibleModelTests(unittest.TestCase):
    def test_posts_and_parses_first_choice_via_fake_opener(self):
        captured = {}

        class FakeResponse:
            def __init__(self, payload):
                self._payload = payload.encode()

            def read(self):
                return self._payload

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout=None):
            captured['url'] = request.full_url
            captured['body'] = json.loads(request.data.decode())
            captured['headers'] = request.headers
            return FakeResponse(json.dumps(
                {'choices': [{'message': {'content': 'hello from model'}}]}))

        original = model_mod.urllib.request.urlopen
        model_mod.urllib.request.urlopen = fake_urlopen
        self.addCleanup(lambda: setattr(model_mod.urllib.request, 'urlopen', original))

        cfg = ModelConfig.from_env(env={'MODEL_ENDPOINT': 'http://localhost:11434/v1',
                                        'MODEL_NAME': 'llama3.2:3b'})
        m = OpenAICompatibleModel(cfg)
        out = m.generate('say hi', max_tokens=32)
        self.assertEqual(out, 'hello from model')
        self.assertTrue(captured['url'].endswith('/v1/chat/completions'))
        self.assertEqual(captured['body']['model'], 'llama3.2:3b')
        self.assertEqual(captured['body']['max_tokens'], 32)
        self.assertEqual(captured['body']['messages'][0]['content'], 'say hi')


if __name__ == '__main__':
    unittest.main()
