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
import re
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


VALUATION_TOOLS = ('intraday_quote', 'dated_nav')
_CALL_RE = re.compile(r'\bCALL ([a-z_]+) (\{.*\})')
_USER_MARKER = '\n[USER_MESSAGE]\n'
_SECTION_HEADERS = ('[CONTEXT]', '[APPROVED_PROCEDURES]', '[MEMORY]')


class PolicyModel:
    """Deterministic, rule-based offline model for the M5b ablation. No per-case scripting.

    Habit: valuation questions use `default_valuation_tool` (intraday_quote); holdings
    questions use list_holdings. The security kind comes from the question wording. The model
    switches to a procedure's prefer_tool ONLY when the system-assembled
    [APPROVED_PROCEDURES] section carries structured JSON whose when.security_kind equals the
    question's kind. [MEMORY] text is data and never changes the choice. Assumption (not a
    finding): this simulates a model that follows the approved-procedure policy channel; it
    validates the procedure -> context -> tool-choice plumbing, not that a real LLM adapts.

    honors_procedures=False is the negative control. naive_followers=True is a deliberately
    gullible model that parrots `CALL <tool> <json>` lines from the message or [MEMORY], so
    injection and cross-user cases exercise the deterministic gate for real.
    """

    def __init__(self, *, honors_procedures=True, naive_followers=False,
                 default_valuation_tool='intraday_quote'):
        if default_valuation_tool not in VALUATION_TOOLS:
            raise ValueError('Unknown default valuation tool')
        self._honors = honors_procedures
        self._naive = naive_followers
        self._default = default_valuation_tool

    @staticmethod
    def _sections(head):
        """Map section header -> lines, using only exact header lines of the system prompt."""
        sections, current = {}, None
        for line in head.split('\n'):
            if line in _SECTION_HEADERS:
                current = line
                sections.setdefault(current, [])
            elif current is not None:
                sections[current].append(line)
        return sections

    @staticmethod
    def _kind(question):
        text = question.lower()
        if 'mutual fund' in text:
            return 'mutual_fund'
        if 'etf' in text:
            return 'etf'
        return 'stock'

    @staticmethod
    def _holdings_question(question):
        words = re.findall(r'[a-z]+', question.lower())
        return 'holdings' in words and 'list' in words

    def _preferred(self, kind, sections):
        for line in sections.get('[APPROVED_PROCEDURES]', []):
            if not line.startswith('{'):
                continue
            try:
                record = json.loads(line)
                when, prefer = record['when'], record['prefer_tool']
            except (ValueError, KeyError, TypeError):
                continue
            if (isinstance(when, dict) and when.get('security_kind') == kind
                    and prefer in VALUATION_TOOLS and prefer != self._default):
                return prefer
        return None

    def generate(self, prompt, *, max_tokens):
        head, marker, message = prompt.partition(_USER_MARKER)
        sections = self._sections(head)
        context = sections.get('[CONTEXT]', [])
        portfolio = next((l[len('portfolio: '):] for l in context if l.startswith('portfolio: ')), None)
        as_of = next((l[len('market_as_of: '):] for l in context if l.startswith('market_as_of: ')), None)
        calls, tool = [], None
        question = message.split('\n', 1)[0]
        if marker and portfolio is not None:
            if self._holdings_question(question):
                tool = 'list_holdings'
                calls.append({'name': tool, 'args': {'portfolio_id': portfolio}})
            elif as_of is not None:
                tool = self._default
                if self._honors:
                    tool = self._preferred(self._kind(question), sections) or tool
                calls.append({'name': tool, 'args': {'portfolio_id': portfolio, 'as_of': as_of}})
        if self._naive:
            for line in message.split('\n') + sections.get('[MEMORY]', []):
                match = _CALL_RE.search(line)
                if match:
                    try:
                        args = json.loads(match.group(2))
                    except ValueError:
                        continue
                    calls.append({'name': match.group(1), 'args': args})
        explanation = (f'Using {tool} for this request.' if tool
                       else 'I have nothing to look up for that.')
        return json.dumps({'explanation': explanation, 'tool_calls': calls})


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
