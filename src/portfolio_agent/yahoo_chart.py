"""Yahoo Finance chart adapter (stdlib only, unofficial endpoint, manual end-of-day closes).

Operator-accepted use only: Yahoo's terms bar automated retrieval; see docs/DECISIONS.md. This
module only fetches and validates. It never touches the store and never logs. It sends ONLY the
ticker symbol and the fixed query `range=5d&interval=1d` plus an honest User-Agent: no cookies,
crumb, token, credential or portfolio data. It never tries to defeat bot protection: a 401/403/429
or an HTML challenge stops the whole press with a fixed code.

Hard rules: fixed host over HTTPS, certificate verification on, no redirect is ever followed (the
transport returns 3xx as-is and the adapter reports provider_error), explicit timeouts, response
size cap, content-type check, symbols validated against the registered set and URL-encoded,
spacing between requests, a request budget per press, retry only on timeouts, connection errors
and 5xx. Results carry fixed codes and parsed closes only, never provider or exception text.

Like the CSV importer it maps each session to 16:00 America/New_York (price_import.close_to_utc).
It returns bars, so it follows the MarketData port's rules (timeouts, backoff, provenance,
validation) without implementing the single-valued `latest()` interface.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN
import http.client
import json
import random
import re
import socket
import ssl
import time
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from . import __version__, valuation
from .price_import import close_to_utc
HOST = 'query1.finance.yahoo.com'
PATH = '/v8/finance/chart/'
QUERY = 'range=5d&interval=1d'
USER_AGENT = f'portfolio-agent/{__version__} personal local-first app'
SOURCE = 'yahoo-chart'
SPACING_SECONDS = 1.5
MAX_REQUESTS = 40
MAX_RETRIES = 2
BACKOFF_BASE_SECONDS = 2.0
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 10.0
TOTAL_TIMEOUT = 20.0
MAX_BYTES = 1048576
QUANTUM = Decimal('0.0001')
SUPPORTED_KINDS = ('EQUITY', 'ETF')
_SYMBOL = re.compile(r'[A-Z0-9][A-Z0-9.-]{0,14}', re.ASCII)
FIXED_CODES = ('not_found', 'unsupported_kind', 'rate_limited', 'blocked', 'provider_error',
               'invalid_response', 'skipped_budget', 'skipped_stopped')
class TransientError(Exception):
    """Timeout or connection error: retryable. Carries no provider text."""
class TransportFailure(Exception):
    """Non-retryable transport problem (certificate, forbidden URL). Carries no provider text."""
class ResponseTooLarge(Exception):
    """The response exceeded the size cap."""
@dataclass(frozen=True)
class HttpResponse:
    status: int
    content_type: str
    body: bytes
@dataclass
class SymbolFetch:
    symbol: object
    code: str                                  # 'ok' or one of FIXED_CODES
    rows: list = field(default_factory=list)   # [(YYYY-MM-DD, 4 dp close string)] for 'ok'
class HttpsTransport:
    """Default transport: http.client over verified TLS, never follows redirects."""
    def __init__(self, *, connection_factory=http.client.HTTPSConnection, max_bytes=MAX_BYTES,
                 monotonic=time.monotonic):
        self._factory, self._max, self._monotonic = connection_factory, max_bytes, monotonic
    @staticmethod
    def _target(url):
        parts = urlsplit(url)
        if (parts.scheme != 'https' or parts.hostname != HOST or parts.port is not None
                or parts.username is not None or parts.password is not None or parts.fragment
                or not parts.path.startswith(PATH) or '..' in parts.path):
            raise TransportFailure()
        return parts.path + ('?' + parts.query if parts.query else '')
    def __call__(self, url, headers):
        target = self._target(url)
        deadline = self._monotonic() + TOTAL_TIMEOUT
        context = ssl.create_default_context()   # CERT_REQUIRED and hostname checking
        conn = None
        try:
            conn = self._factory(HOST, timeout=CONNECT_TIMEOUT, context=context)
            conn.connect()
            conn.sock.settimeout(READ_TIMEOUT)
            conn.request('GET', target, headers={**headers, 'Connection': 'close'})
            reply = conn.getresponse()
            declared = reply.getheader('Content-Length')
            if declared is not None and declared.isdigit() and int(declared) > self._max:
                raise ResponseTooLarge()
            chunks, size = [], 0
            while True:
                if self._monotonic() > deadline:
                    raise TransientError()
                chunk = reply.read(8192)
                if not chunk:
                    break
                size += len(chunk)
                if size > self._max:
                    raise ResponseTooLarge()
                chunks.append(chunk)
            return HttpResponse(reply.status, reply.getheader('Content-Type') or '', b''.join(chunks))
        except (TransportFailure, TransientError, ResponseTooLarge):
            raise
        except ssl.SSLError:
            raise TransportFailure() from None
        except (OSError, http.client.HTTPException):   # includes socket.timeout, connection resets
            raise TransientError() from None
        finally:
            if conn is not None:
                conn.close()
def _dec(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError
    close = Decimal(repr(float(value))).quantize(QUANTUM, rounding=ROUND_HALF_EVEN)
    if close == 0:
        raise ValueError
    return valuation.numeric(close)
def _reject_constant(_name):
    raise ValueError
def _local_date(epoch, zone):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).astimezone(zone).date()
def _is_unfinished(day, end, now, zone):
    """True when this session's close is not yet a completed, publishable close."""
    if _local_date(int(now.timestamp()), zone) != day:
        return False
    finish = close_to_utc(day)   # 16:00 America/New_York when meta carries no usable session end
    if isinstance(end, int) and not isinstance(end, bool) and _local_date(end, zone) == day:
        finish = datetime.fromtimestamp(end, tz=timezone.utc)
    # The stored observation time is the one 16:00 New York convention, never in the future.
    return now < finish or now < close_to_utc(day)
def parse_chart(symbol, body, now):
    """(code, rows) for one chart body. code is 'ok' or a fixed code; rows are (date, close) strings."""
    try:
        data = json.loads(body.decode('utf-8'), parse_constant=_reject_constant)
        chart = data['chart']
        error, result = chart.get('error'), chart.get('result')
        if error is not None:
            if isinstance(error, dict) and error.get('code') == 'Not Found' and result is None:
                return 'not_found', []
            return 'invalid_response', []
        if not isinstance(result, list) or len(result) != 1:
            return 'invalid_response', []
        item = result[0]
        meta = item['meta']
        if not isinstance(meta.get('symbol'), str) or meta['symbol'].upper() != str(symbol).upper():
            return 'invalid_response', []
        if meta.get('instrumentType') not in SUPPORTED_KINDS:
            return 'unsupported_kind', []
        if meta.get('currency') != 'USD':
            return 'invalid_response', []
        zone = ZoneInfo(meta['exchangeTimezoneName'])
        stamps, closes = item['timestamp'], item['indicators']['quote'][0]['close']
        if (not isinstance(stamps, list) or not isinstance(closes, list) or len(stamps) != len(closes)):
            return 'invalid_response', []
        period = meta.get('currentTradingPeriod')
        end = period['regular'].get('end') if isinstance(period, dict) and isinstance(period.get('regular'), dict) else None
        today = _local_date(int(now.timestamp()), zone)
        found = {}
        for stamp, close in zip(stamps, closes):
            if isinstance(stamp, bool) or not isinstance(stamp, int):
                return 'invalid_response', []
            day = _local_date(stamp, zone)
            if close is None:
                continue
            value = _dec(close)
            if day > today:
                return 'invalid_response', []
            if day in found:
                return 'invalid_response', []
            found[day] = value
        rows = [(day.isoformat(), format(value, 'f')) for day, value in sorted(found.items())
                if not _is_unfinished(day, end, now, zone)]
        return ('ok', rows) if rows else ('invalid_response', [])
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OSError, RecursionError,
            ArithmeticError, ZoneInfoNotFoundError):   # UnicodeDecodeError is a ValueError
        return 'invalid_response', []
def _looks_html(response):
    kind = response.content_type.split(';')[0].strip().lower()
    return kind in ('text/html', 'application/xhtml+xml') or response.body.lstrip()[:1] == b'<'
def _classify(symbol, response, now):
    """('retry'|'stop'|'done', code, rows) for one response; never reads provider text into a code."""
    status = response.status
    if status == 429:
        return 'stop', 'rate_limited', []
    if status in (401, 403):
        return 'stop', 'blocked', []
    if 500 <= status <= 599:
        return 'retry', 'provider_error', []
    if 300 <= status <= 399:   # never followed, whatever the body says
        return 'done', 'provider_error', []
    if _looks_html(response):
        return 'stop', 'blocked', []
    kind = response.content_type.split(';')[0].strip().lower()
    if kind != 'application/json':
        return 'done', 'invalid_response', []
    if status == 404:
        code, _ = parse_chart(symbol, response.body, now)
        return 'done', ('not_found' if code == 'not_found' else 'invalid_response'), []
    if status != 200:
        return 'done', 'provider_error', []
    code, rows = parse_chart(symbol, response.body, now)
    return 'done', code, rows
def _usable(symbol, registered):
    if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol) or symbol not in registered:
        return False
    return quote(symbol, safe='') == symbol
def fetch_press(symbols, *, registered, transport=None, clock=None, sleeper=None, rng=None,
                should_stop=None, on_progress=None):
    """Fetch every requested symbol (in order, deduplicated); one SymbolFetch per symbol.

    Network only: no store access. A blocked or rate-limited answer stops the whole press."""
    transport = transport or HttpsTransport()
    clock = clock or (lambda: datetime.now(timezone.utc))
    sleeper = sleeper or time.sleep
    rng = rng or random.Random()
    should_stop = should_stop or (lambda: False)
    ordered, seen = [], set()
    for symbol in symbols:
        key = symbol if isinstance(symbol, str) else id(symbol)
        if key not in seen:
            seen.add(key)
            ordered.append(symbol)
    results, requests, stopped = [], 0, False
    for position, symbol in enumerate(ordered):
        if not _usable(symbol, registered):
            results.append(SymbolFetch(symbol, 'not_found'))
        elif stopped or should_stop():
            stopped = True
            results.append(SymbolFetch(symbol, 'skipped_stopped'))
        elif requests >= MAX_REQUESTS:
            results.append(SymbolFetch(symbol, 'skipped_budget'))
        else:
            outcome, requests = _fetch_one(symbol, transport, clock, sleeper, rng, requests)
            stopped = stopped or outcome.code in ('blocked', 'rate_limited')
            results.append(outcome)
        if on_progress is not None:
            on_progress(position + 1, len(ordered))
    return results
def _fetch_one(symbol, transport, clock, sleeper, rng, requests):
    url = f'https://{HOST}{PATH}{quote(symbol, safe="")}?{QUERY}'
    headers = {'User-Agent': USER_AGENT, 'Accept': 'application/json'}
    last = 'provider_error'
    for attempt in range(MAX_RETRIES + 1):
        if requests >= MAX_REQUESTS:
            return SymbolFetch(symbol, last if attempt else 'skipped_budget'), requests
        if requests:
            delay = SPACING_SECONDS
            if attempt:
                delay = max(delay, BACKOFF_BASE_SECONDS * 2 ** (attempt - 1) + rng.random())
            sleeper(delay)
        requests += 1
        try:
            response = transport(url, headers)
        except TransientError:
            last = 'provider_error'
            continue
        except ResponseTooLarge:
            return SymbolFetch(symbol, 'invalid_response'), requests
        except Exception:   # TransportFailure or anything unexpected: fixed code, no text kept
            return SymbolFetch(symbol, 'provider_error'), requests
        verdict, code, rows = _classify(symbol, response, clock())
        if verdict == 'retry':
            last = code
            continue
        return SymbolFetch(symbol, code, rows), requests
    return SymbolFetch(symbol, last), requests
