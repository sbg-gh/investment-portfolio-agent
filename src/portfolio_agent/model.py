"""Vendor-neutral local-model adapter. Stdlib only (urllib/threading); no vendor deps.

The model is an UNTRUSTED proposer: it returns text that the deterministic gate re-checks
before anything executes. Config mirrors the names reserved in .env.example. Conservative
Mac-mini defaults bound inference concurrency and context. Offline tests drive ScriptedModel;
the OpenAI-compatible HTTP path is a thin POST to /v1/chat/completions.
"""
from dataclasses import dataclass
import json
import math
import os
import threading
import urllib.request

DEFAULT_ENDPOINT = 'http://127.0.0.1:8080'
DEFAULT_MAX_CONCURRENCY = 1
DEFAULT_CONTEXT_BUDGET = 4096
DEFAULT_MAX_OUTPUT_TOKENS = 512


def estimate_tokens(text):
    """Deterministic chars/4 estimate; no tokenizer dependency."""
    if not isinstance(text, str):
        raise ValueError('Text required for token estimation')
    return math.ceil(len(text) / 4)


def _positive_int(env, key, default):
    raw = env.get(key)
    if raw is None or raw == '':
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{key} must be a positive integer') from exc
    if value <= 0:
        raise ValueError(f'{key} must be a positive integer')
    return value


@dataclass(frozen=True)
class ModelConfig:
    endpoint: str = DEFAULT_ENDPOINT
    name: str | None = None
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    context_budget: int = DEFAULT_CONTEXT_BUDGET
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        return cls(
            endpoint=env.get('MODEL_ENDPOINT') or DEFAULT_ENDPOINT,
            name=env.get('MODEL_NAME') or None,
            max_concurrency=_positive_int(env, 'MODEL_MAX_CONCURRENCY', DEFAULT_MAX_CONCURRENCY),
            context_budget=_positive_int(env, 'MODEL_CONTEXT_BUDGET', DEFAULT_CONTEXT_BUDGET),
            max_output_tokens=_positive_int(env, 'MODEL_MAX_OUTPUT_TOKENS', DEFAULT_MAX_OUTPUT_TOKENS),
        )


class ScriptedModel:
    """Offline fake implementing ports.LocalModel. Pops canned replies deterministically."""

    def __init__(self, replies):
        self._replies = list(replies)
        self._index = 0

    def generate(self, prompt, *, max_tokens):
        if self._index >= len(self._replies):
            raise RuntimeError('ScriptedModel queue exhausted')
        reply = self._replies[self._index]
        self._index += 1
        return reply


class BoundedModel:
    """Wraps a model with a concurrency semaphore and an output-token cap."""

    def __init__(self, inner, config):
        self._inner = inner
        self._config = config
        self._semaphore = threading.BoundedSemaphore(config.max_concurrency)

    def generate(self, prompt, *, max_tokens):
        if max_tokens > self._config.max_output_tokens:
            raise ValueError(
                f'Requested {max_tokens} output tokens exceeds cap {self._config.max_output_tokens}')
        with self._semaphore:
            return self._inner.generate(prompt, max_tokens=max_tokens)


class OpenAICompatibleModel:
    """Thin stdlib POST to an OpenAI-compatible /v1/chat/completions endpoint."""

    def __init__(self, config, *, timeout=30):
        self._config = config
        self._timeout = timeout

    def _url(self):
        base = self._config.endpoint.rstrip('/')
        if base.endswith('/v1'):
            return base + '/chat/completions'
        return base + '/v1/chat/completions'

    def generate(self, prompt, *, max_tokens):
        body = {
            'model': self._config.name or '',
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': max_tokens,
        }
        request = urllib.request.Request(
            self._url(), data=json.dumps(body).encode(),
            headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            payload = json.loads(response.read().decode())
        choices = payload.get('choices') or []
        if not choices:
            raise ValueError('Model response contained no choices')
        return choices[0]['message']['content']
