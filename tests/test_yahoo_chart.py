"""Yahoo chart adapter and parser, fully offline (fake transport, injected clock/sleeper/rng).
Fixtures: tests/yahoo_fixtures.py (recorded 2026-11-13 from a public endpoint plus constructed cases)."""
from datetime import datetime, timezone
import json
import socket
import ssl
import unittest
from unittest import mock
from portfolio_agent import yahoo_chart as yc
from tests.yahoo_fixtures import AMD_RECORDED, HTML_CHALLENGE, NOT_FOUND_404, chart, ts
def utc(*parts):
    return datetime(*parts, tzinfo=timezone.utc)
FRI_AFTER = utc(2026, 11, 13, 22, 0)   # Friday 17:00 EST, session over
JSON = 'application/json;charset=utf-8'
class Offline(unittest.TestCase):
    """No test may touch the network: any socket creation fails the test."""
    def setUp(self):
        patcher = mock.patch.object(socket, 'socket', side_effect=AssertionError('network attempted'))
        patcher.start()
        self.addCleanup(patcher.stop)
class ParseTests(Offline):
    def parse(self, body, now=FRI_AFTER, symbol='SYNA'):
        return yc.parse_chart(symbol, body if isinstance(body, bytes) else body.encode(), now)
    def test_recorded_amd_fixture(self):
        code, rows = self.parse(AMD_RECORDED, symbol='AMD')
        self.assertEqual(code, 'ok')
        self.assertEqual(rows, [('2026-11-10', '515.2100'), ('2026-11-11', '552.9000'),
                                ('2026-11-12', '585.3000')])
    def test_symbol_match_is_case_insensitive(self):
        self.assertEqual(self.parse(AMD_RECORDED, symbol='amd')[0], 'ok')
    def test_quantizes_float_repr_to_four_places_half_even(self):
        bars = [(ts(2026, 11, 9), 508.5799865722656), (ts(2026, 11, 10), 10.00005),
                (ts(2026, 11, 11), 10.00015), (ts(2026, 11, 12), 7)]
        code, rows = self.parse(chart(bars=bars))
        self.assertEqual((code, [r[1] for r in rows]), ('ok', ['508.5800', '10.0000', '10.0002', '7.0000']))
    def test_uses_quote_close_not_adjclose(self):
        def raw(body):
            body['chart']['result'][0]['indicators']['adjclose'][0]['adjclose'] = [1.0]
            return json.dumps(body).encode()
        code, rows = self.parse(chart(bars=[(ts(2026, 11, 12), 100.5)], raw=raw))
        self.assertEqual(rows, [('2026-11-12', '100.5000')])
    def test_null_closes_are_skipped_not_filled(self):
        bars = [(ts(2026, 11, 10), 1.5), (ts(2026, 11, 11), None), (ts(2026, 11, 12), 2.5)]
        code, rows = self.parse(chart(bars=bars))
        self.assertEqual((code, [r[0] for r in rows]), ('ok', ['2026-11-10', '2026-11-12']))
    def test_rows_are_sorted_by_date(self):
        bars = [(ts(2026, 11, 12), 2.0), (ts(2026, 11, 10), 1.0)]
        self.assertEqual([r[0] for r in self.parse(chart(bars=bars))[1]], ['2026-11-10', '2026-11-12'])
    def test_bad_values_fail_the_whole_symbol(self):
        for bad in (0, 0.0, -1.5, 0.00001, 1e30, True, '5', [1], {'a': 1}):
            body = chart(bars=[(ts(2026, 11, 11), 1.5), (ts(2026, 11, 12), bad)])
            self.assertEqual(self.parse(body), ('invalid_response', []), repr(bad))
    def test_nan_infinity_and_overflow_literals_are_rejected(self):
        for literal in ('NaN', 'Infinity', '-Infinity', '1e999'):
            body = chart(bars=[(ts(2026, 11, 12), 1.5)]).replace(b'1.5', literal.encode())
            self.assertEqual(self.parse(body), ('invalid_response', []), literal)
    def test_structure_failures_are_invalid_response(self):
        good = json.loads(chart(bars=[(ts(2026, 11, 12), 1.5)]))
        def mutate(fn):
            body = json.loads(json.dumps(good))
            fn(body)
            return json.dumps(body).encode()
        cases = {
            'not json': b'not json', 'array': b'[]', 'empty': b'', 'no chart': b'{}',
            'error set': mutate(lambda b: b['chart'].update(error={'code': 'x'})),
            'result null': mutate(lambda b: b['chart'].update(result=None)),
            'result empty': mutate(lambda b: b['chart'].update(result=[])),
            'result two': mutate(lambda b: b['chart']['result'].append(b['chart']['result'][0])),
            'no meta': mutate(lambda b: b['chart']['result'][0].pop('meta')),
            'no timestamp': mutate(lambda b: b['chart']['result'][0].pop('timestamp')),
            'no indicators': mutate(lambda b: b['chart']['result'][0].pop('indicators')),
            'no quote': mutate(lambda b: b['chart']['result'][0]['indicators'].update(quote=[])),
            'no close': mutate(lambda b: b['chart']['result'][0]['indicators']['quote'][0].pop('close')),
            'length mismatch': mutate(lambda b: b['chart']['result'][0]['timestamp'].append(1)),
            'timestamp float': mutate(lambda b: b['chart']['result'][0].update(timestamp=[1.5])),
            'timestamp bool': mutate(lambda b: b['chart']['result'][0].update(timestamp=[True])),
            'symbol mismatch': chart('OTHR', [(ts(2026, 11, 12), 1.5)]),
            'lookalike symbol': chart('SYN\u0410', [(ts(2026, 11, 12), 1.5)]),
            'currency': chart(currency='EUR', bars=[(ts(2026, 11, 12), 1.5)]),
            'no currency': mutate(lambda b: b['chart']['result'][0]['meta'].pop('currency')),
            'no tz': mutate(lambda b: b['chart']['result'][0]['meta'].pop('exchangeTimezoneName')),
            'bad tz': chart(tz='Mars/Base', bars=[(ts(2026, 11, 12), 1.5)]),
            'duplicate session': chart(bars=[(ts(2026, 11, 12), 1.5), (ts(2026, 11, 12, 10), 1.6)]),
            'future bar': chart(bars=[(ts(2026, 11, 16), 1.5)]),
            'only null closes': chart(bars=[(ts(2026, 11, 12), None)]),
            'deep nesting': b'[' * 100000 + b']' * 100000,
        }
        for name, body in cases.items():
            self.assertEqual(self.parse(body), ('invalid_response', []), name)
    def test_non_equity_kinds_are_unsupported(self):
        for kind in ('MUTUALFUND', 'INDEX', 'CRYPTOCURRENCY', 'OPTION', 'CURRENCY', 'FUTURE', ''):
            body = chart(kind=kind, bars=[(ts(2026, 11, 12), 1.5)])
            self.assertEqual(self.parse(body), ('unsupported_kind', []), kind)
        self.assertEqual(self.parse(chart(kind='ETF', bars=[(ts(2026, 11, 12), 1.5)]))[0], 'ok')
    def test_not_found_body_codes(self):
        self.assertEqual(self.parse(NOT_FOUND_404), ('not_found', []))
        other = json.dumps({'chart': {'result': None, 'error': {'code': 'Bad Request'}}})
        self.assertEqual(self.parse(other), ('invalid_response', []))
    def test_session_date_uses_the_exchange_timezone_not_utc(self):
        # 2026-11-10 09:00 Tokyo is 2026-11-10 00:00Z; 2026-11-11 00:30 Tokyo is still 11-10 in UTC.
        bars = [(ts(2026, 11, 11, 0, 30, 'Asia/Tokyo'), 1.5)]
        code, rows = self.parse(chart(tz='Asia/Tokyo', bars=bars))
        self.assertEqual(rows, [('2026-11-11', '1.5000')])
        # A New York bar at 21:00 local is the same New York date although it is the next UTC day.
        late = [(ts(2026, 11, 10, 21, 0), 1.5)]
        self.assertEqual(self.parse(chart(bars=late))[1], [('2026-11-10', '1.5000')])
class PartialBarTests(Offline):
    """A fetched price must be a completed close: the unfinished session's bar is dropped."""
    def dates(self, bars, now, **kw):
        code, rows = yc.parse_chart('SYNA', chart(bars=bars, **kw), now)
        self.assertIn(code, ('ok', 'invalid_response'))   # nothing completed left is invalid_response
        return [r[0] for r in rows]
    def test_market_open_drops_todays_bar(self):
        bars = [(ts(2026, 11, 12), 1.5), (ts(2026, 11, 13), 2.5)]
        self.assertEqual(self.dates(bars, utc(2026, 11, 13, 15, 0)), ['2026-11-12'])
    def test_after_close_keeps_it(self):
        bars = [(ts(2026, 11, 12), 1.5), (ts(2026, 11, 13), 2.5)]
        self.assertEqual(self.dates(bars, utc(2026, 11, 13, 21, 30)), ['2026-11-12', '2026-11-13'])
    def test_boundary_uses_session_end_from_meta(self):
        bars = [(ts(2026, 11, 13), 2.5)]
        end = ts(2026, 11, 13, 16, 0)
        self.assertEqual(self.dates(bars, utc(2026, 11, 13, 20, 59), end=end), [])  # 15:59 EST
        self.assertEqual(self.dates(bars, utc(2026, 11, 13, 21, 0), end=end), ['2026-11-13'])
    def test_weekend_keeps_fridays_bar(self):
        bars = [(ts(2026, 11, 12), 1.5), (ts(2026, 11, 13), 2.5)]
        self.assertEqual(self.dates(bars, utc(2026, 11, 14, 15, 0)), ['2026-11-12', '2026-11-13'])
        self.assertEqual(self.dates(bars, utc(2026, 11, 15, 15, 0)), ['2026-11-12', '2026-11-13'])
    def test_holiday_with_no_bar_today_keeps_the_rest(self):
        bars = [(ts(2026, 11, 24), 1.5), (ts(2026, 11, 25), 2.5)]   # Thanksgiving is 2026-11-26
        self.assertEqual(self.dates(bars, utc(2026, 11, 26, 15, 0)), ['2026-11-24', '2026-11-25'])
    def test_dst_summer_and_winter_session_ends(self):
        edt = [(ts(2026, 10, 30), 1.5)]     # EDT: 16:00 is 20:00Z
        self.assertEqual(self.dates(edt, utc(2026, 10, 30, 19, 30)), [])
        self.assertEqual(self.dates(edt, utc(2026, 10, 30, 20, 0)), ['2026-10-30'])
        est = [(ts(2026, 11, 2), 1.5)]      # EST after the 2026-11-01 change: 16:00 is 21:00Z
        self.assertEqual(self.dates(est, utc(2026, 11, 2, 20, 30)), [])
        self.assertEqual(self.dates(est, utc(2026, 11, 2, 21, 0)), ['2026-11-02'])
    def test_dst_bars_across_the_change_get_the_right_observed_dates(self):
        bars = [(ts(2026, 10, 30), 1.5), (ts(2026, 11, 2), 2.5)]
        self.assertEqual(self.dates(bars, utc(2026, 11, 3, 15, 0)), ['2026-10-30', '2026-11-02'])
    def test_early_close_day_is_not_published_before_the_sixteen_hundred_convention(self):
        bars = [(ts(2026, 11, 27), 1.5)]
        end = ts(2026, 11, 27, 13, 0)
        self.assertEqual(self.dates(bars, utc(2026, 11, 27, 19, 0), end=end), [])   # 14:00 EST
        self.assertEqual(self.dates(bars, utc(2026, 11, 27, 21, 0), end=end), ['2026-11-27'])
    def test_session_end_of_another_day_is_ignored(self):
        bars = [(ts(2026, 11, 13), 2.5)]
        tomorrow_end = ts(2026, 11, 16, 16, 0)
        self.assertEqual(self.dates(bars, utc(2026, 11, 13, 22, 0), end=tomorrow_end), ['2026-11-13'])
        self.assertEqual(self.dates(bars, utc(2026, 11, 13, 15, 0), end=tomorrow_end), [])
    def test_only_an_unfinished_bar_leaves_nothing_to_return(self):
        code, rows = yc.parse_chart('SYNA', chart(bars=[(ts(2026, 11, 13), 2.5)]), utc(2026, 11, 13, 15, 0))
        self.assertEqual((code, rows), ('invalid_response', []))
class Fake:
    """Scripted transport: items are Responses or exceptions, popped per call."""
    def __init__(self, *script, default=None):
        self.script, self.default, self.calls = list(script), default, []
    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        item = self.script.pop(0) if self.script else self.default
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            item = item(url)
        return item
def ok(body, status=200, content_type=JSON):
    return yc.HttpResponse(status, content_type, body if isinstance(body, bytes) else body.encode())
def good(symbol='SYNA'):
    return ok(chart(symbol, [(ts(2026, 11, 12), 1.5), (ts(2026, 11, 13), 2.5)]))
def by_symbol(url):
    return good(url.split('/chart/')[1].split('?')[0])
class FetchBase(Offline):
    def press(self, symbols, transport, registered=None, **kw):
        self.sleeps = []
        kw.setdefault('clock', lambda: FRI_AFTER)
        kw.setdefault('sleeper', self.sleeps.append)
        kw.setdefault('rng', mock.Mock(random=lambda: 0.5))
        return yc.fetch_press(symbols, registered=set(symbols if registered is None else registered),
                              transport=transport, **kw)
    def codes(self, result):
        return [(r.symbol, r.code) for r in result]
class FetchPressTests(FetchBase):
    def test_request_shape_only_symbol_and_range(self):
        fake = Fake(default=good())
        self.press(['SYNA'], fake)
        (url, headers), = fake.calls
        self.assertEqual(url, 'https://query1.finance.yahoo.com/v8/finance/chart/SYNA?range=5d&interval=1d')
        self.assertEqual(set(headers), {'User-Agent', 'Accept'})
        self.assertRegex(headers['User-Agent'], r'^portfolio-agent/\d+\.\d+\.\d+ personal local-first app$')
        self.assertNotIn('Mozilla', headers['User-Agent'])
        self.assertNotIn('cookie', {k.lower() for k in headers})
    def test_results_carry_rows_and_fixed_codes(self):
        result = self.press(['SYNA', 'SYNB'], Fake(by_symbol, by_symbol))
        self.assertEqual(self.codes(result), [('SYNA', 'ok'), ('SYNB', 'ok')])
        self.assertEqual(result[0].rows, [('2026-11-12', '1.5000'), ('2026-11-13', '2.5000')])
    def test_spacing_between_requests_uses_the_sleeper(self):
        self.press(['SYNA', 'SYNB', 'SYNC'], Fake(default=by_symbol))
        self.assertEqual(self.sleeps, [1.5, 1.5])
    def test_unknown_symbol_404_is_not_found_and_others_continue(self):
        fake = Fake(ok(NOT_FOUND_404, 404), by_symbol)
        result = self.press(['SYNA', 'SYNB'], fake)
        self.assertEqual(self.codes(result), [('SYNA', 'not_found'), ('SYNB', 'ok')])
    def test_blocked_and_rate_limited_stop_the_whole_press(self):
        for status, code in ((403, 'blocked'), (401, 'blocked'), (429, 'rate_limited')):
            fake = Fake(by_symbol, ok(b'denied', status, 'text/plain'), by_symbol, by_symbol)
            result = self.press(['SYNA', 'SYNB', 'SYNC', 'SYND'], fake)
            self.assertEqual(self.codes(result), [('SYNA', 'ok'), ('SYNB', code),
                                                  ('SYNC', 'skipped_stopped'), ('SYND', 'skipped_stopped')])
            self.assertEqual(len(fake.calls), 2, status)   # never retried, never continued
    def test_html_challenge_stops_the_press_even_with_status_200(self):
        fake = Fake(ok(HTML_CHALLENGE, 200, 'text/html; charset=utf-8'), by_symbol)
        result = self.press(['SYNA', 'SYNB'], fake)
        self.assertEqual(self.codes(result), [('SYNA', 'blocked'), ('SYNB', 'skipped_stopped')])
        self.assertEqual(len(fake.calls), 1)
    def test_html_body_with_json_content_type_is_still_a_block(self):
        fake = Fake(ok(HTML_CHALLENGE, 200, JSON), by_symbol)
        self.assertEqual(self.codes(self.press(['SYNA', 'SYNB'], fake))[0], ('SYNA', 'blocked'))
    def test_5xx_is_retried_with_backoff_then_provider_error(self):
        fake = Fake(default=ok(b'oops', 503, 'text/html'))
        result = self.press(['SYNA', 'SYNB'], fake)
        self.assertEqual(self.codes(result), [('SYNA', 'provider_error'), ('SYNB', 'provider_error')])
        self.assertEqual(len(fake.calls), 6)    # 1 + 2 retries each
        first_symbol_sleeps = self.sleeps[:2]
        self.assertTrue(all(s >= 1.5 for s in self.sleeps))
        self.assertLess(first_symbol_sleeps[0], first_symbol_sleeps[1])   # exponential
    def test_5xx_then_success_and_jitter_comes_from_rng(self):
        rng = mock.Mock(random=mock.Mock(return_value=0.0))
        fake = Fake(ok(b'', 502, 'text/html'), by_symbol)
        result = self.press(['SYNA'], fake, rng=rng)
        self.assertEqual(self.codes(result), [('SYNA', 'ok')])
        self.assertEqual(len(fake.calls), 2)
        rng.random.assert_called()
    def test_timeouts_and_connection_errors_are_retried(self):
        fake = Fake(yc.TransientError(), yc.TransientError(), yc.TransientError())
        self.assertEqual(self.codes(self.press(['SYNA'], fake)), [('SYNA', 'provider_error')])
        self.assertEqual(len(fake.calls), 3)
        fake = Fake(yc.TransientError(), by_symbol)
        self.assertEqual(self.codes(self.press(['SYNA'], fake)), [('SYNA', 'ok')])
    def test_4xx_and_schema_failures_are_never_retried(self):
        for response, code in ((ok(b'{}', 400), 'provider_error'), (ok(b'{}', 410), 'provider_error'),
                               (ok(b'not json'), 'invalid_response'),
                               (ok(chart('OTHR', [(ts(2026, 11, 12), 1.5)])), 'invalid_response'),
                               (ok(chart(currency='EUR', bars=[(ts(2026, 11, 12), 1.5)])), 'invalid_response')):
            fake = Fake(default=response)
            self.assertEqual(self.codes(self.press(['SYNA'], fake)), [('SYNA', code)])
            self.assertEqual(len(fake.calls), 1)
    def test_redirects_are_never_followed(self):
        for status in (301, 302, 307, 308):
            fake = Fake(ok(b'', status, 'text/html'), by_symbol)
            result = self.press(['SYNA', 'SYNB'], fake)
            self.assertEqual(self.codes(result), [('SYNA', 'provider_error'), ('SYNB', 'ok')])
            self.assertEqual(len(fake.calls), 2)   # one request each; the redirect target was not requested
            self.assertTrue(all(u.startswith('https://query1.finance.yahoo.com/') for u, _ in fake.calls))
    def test_oversized_and_wrong_content_type_are_invalid(self):
        self.assertEqual(self.codes(self.press(['SYNA'], Fake(yc.ResponseTooLarge()))), [('SYNA', 'invalid_response')])
        wrong = Fake(ok(chart('SYNA', [(ts(2026, 11, 12), 1.5)]), 200, 'text/plain'))
        self.assertEqual(self.codes(self.press(['SYNA'], wrong)), [('SYNA', 'invalid_response')])
        missing = Fake(ok(chart('SYNA', [(ts(2026, 11, 12), 1.5)]), 200, ''))
        self.assertEqual(self.codes(self.press(['SYNA'], missing)), [('SYNA', 'invalid_response')])
    def test_non_retryable_transport_failure_and_unexpected_errors_are_provider_error(self):
        for exc in (yc.TransportFailure(), ValueError('secret detail')):
            fake = Fake(exc, by_symbol)
            result = self.press(['SYNA', 'SYNB'], fake)
            self.assertEqual(self.codes(result), [('SYNA', 'provider_error'), ('SYNB', 'ok')])
            self.assertEqual(len(fake.calls), 2)
    def test_unsupported_kind_is_reported_and_continues(self):
        fund = ok(chart('SYNA', [(ts(2026, 11, 12), 1.5)], kind='MUTUALFUND'))
        result = self.press(['SYNA', 'SYNB'], Fake(fund, by_symbol))
        self.assertEqual(self.codes(result), [('SYNA', 'unsupported_kind'), ('SYNB', 'ok')])
    def test_results_never_contain_provider_text(self):
        secret = ok(b'{"chart":{"result":null,"error":{"code":"SECRET-TEXT","description":"leak"}}}', 400)
        result = self.press(['SYNA'], Fake(secret))
        self.assertNotIn('SECRET', repr(result))
    def test_request_budget_caps_and_reports_skipped_budget(self):
        names = [f'S{i:02d}' for i in range(45)]
        fake = Fake(default=by_symbol)
        result = self.press(names, fake)
        self.assertEqual(len(fake.calls), 40)
        self.assertEqual([r.code for r in result[:40]], ['ok'] * 40)
        self.assertEqual([r.code for r in result[40:]], ['skipped_budget'] * 5)
    def test_retries_count_against_the_budget(self):
        names = [f'S{i:02d}' for i in range(20)]
        fake = Fake(default=ok(b'', 500, 'text/html'))
        result = self.press(names, fake)
        self.assertEqual(len(fake.calls), 40)
        codes = [r.code for r in result]
        self.assertEqual(codes.count('skipped_budget') + codes.count('provider_error'), 20)
        self.assertIn('skipped_budget', codes)
        self.assertEqual(codes[-1], 'skipped_budget')
    def test_unregistered_or_malformed_symbols_are_never_requested(self):
        bad = ['../../etc/passwd', 'SYNA?x=1', 'SYNA/../X', 'SYN\u0410', '\uff21\uff2d\uff24', 'A' * 50, '', ' SYNA',
               'syna', 'SYNA%2f', 'SYNA#x', 'SYNA\n', 'ZZZZ', '.A', 'A\x00', 'A B', 'SYNA@evil.com', 123, None]
        fake = Fake(default=good())
        result = self.press(bad, fake, registered=['SYNA'])
        self.assertEqual(fake.calls, [])
        self.assertEqual({r.code for r in result}, {'not_found'})
        self.assertEqual(len(result), len(bad))
    def test_dotted_and_dashed_symbols_are_encoded_safely(self):
        fake = Fake(default=ok(chart('BRK-B', [(ts(2026, 11, 12), 1.5)])))
        self.press(['BRK-B'], fake)
        self.assertEqual(fake.calls[0][0],
                         'https://query1.finance.yahoo.com/v8/finance/chart/BRK-B?range=5d&interval=1d')
    def test_duplicate_symbols_are_requested_once(self):
        fake = Fake(default=by_symbol)
        result = self.press(['SYNA', 'SYNA'], fake)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(len(result), 1)
    def test_stop_request_skips_the_rest(self):
        flag = {'stop': False}
        def transport(url, headers):
            flag['stop'] = True
            return by_symbol(url)
        result = self.press(['SYNA', 'SYNB', 'SYNC'], transport, should_stop=lambda: flag['stop'])
        self.assertEqual([r.code for r in result], ['ok', 'skipped_stopped', 'skipped_stopped'])
    def test_progress_callback_reports_counts_only(self):
        seen = []
        self.press(['SYNA', 'SYNB'], Fake(default=by_symbol), on_progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(seen, [(1, 2), (2, 2)])
    def test_no_symbols_makes_no_request(self):
        fake = Fake(default=good())
        self.assertEqual(self.press([], fake), [])
        self.assertEqual(fake.calls, [])
class FakeResponse:
    def __init__(self, status=200, headers=None, chunks=(b'{}',)):
        self.status, self._headers, self._chunks = status, headers or {'Content-Type': JSON}, list(chunks)
    def getheader(self, name, default=None):
        return {k.lower(): v for k, v in self._headers.items()}.get(name.lower(), default)
    def read(self, amount=-1):
        return self._chunks.pop(0) if self._chunks else b''
class FakeConn:
    instances = []
    def __init__(self, host, **kw):
        self.host, self.kw, self.requests, self.closed = host, kw, [], False
        self.sock = mock.Mock()
        self.response = FakeConn.next_response
        FakeConn.instances.append(self)
    next_response = FakeResponse()
    error = None
    def connect(self):
        if FakeConn.error:
            raise FakeConn.error
    def request(self, method, target, headers=None):
        self.requests.append((method, target, dict(headers or {})))
    def getresponse(self):
        return self.response
    def close(self):
        self.closed = True
class HttpsTransportTests(Offline):
    URL = 'https://query1.finance.yahoo.com/v8/finance/chart/SYNA?range=5d&interval=1d'
    def setUp(self):
        super().setUp()
        FakeConn.instances, FakeConn.error, FakeConn.next_response = [], None, FakeResponse()
        self.transport = yc.HttpsTransport(connection_factory=FakeConn, monotonic=lambda: 0.0)
    def test_connects_to_the_fixed_host_with_certificate_verification(self):
        reply = self.transport(self.URL, {'User-Agent': 'x', 'Accept': 'application/json'})
        conn, = FakeConn.instances
        self.assertEqual(conn.host, 'query1.finance.yahoo.com')
        context = conn.kw['context']
        self.assertEqual((context.verify_mode, context.check_hostname), (ssl.CERT_REQUIRED, True))
        self.assertEqual(conn.kw['timeout'], yc.CONNECT_TIMEOUT)
        conn.sock.settimeout.assert_called_with(yc.READ_TIMEOUT)
        method, target, headers = conn.requests[0]
        self.assertEqual((method, target), ('GET', '/v8/finance/chart/SYNA?range=5d&interval=1d'))
        self.assertEqual(headers['Connection'], 'close')
        self.assertEqual((reply.status, reply.content_type, reply.body), (200, JSON, b'{}'))
        self.assertTrue(conn.closed)
    def test_other_hosts_schemes_ports_and_paths_are_refused_without_connecting(self):
        for url in ('http://query1.finance.yahoo.com/v8/finance/chart/A?range=5d&interval=1d',
                    'https://evil.example/v8/finance/chart/A?range=5d&interval=1d',
                    'https://query1.finance.yahoo.com.evil.example/v8/finance/chart/A',
                    'https://query1.finance.yahoo.com:8443/v8/finance/chart/A',
                    'https://user@query1.finance.yahoo.com/v8/finance/chart/A',
                    'https://query1.finance.yahoo.com/other/path', 'https://query1.finance.yahoo.com/v8/finance/chart/../x',
                    'ftp://query1.finance.yahoo.com/v8/finance/chart/A'):
            with self.assertRaises(yc.TransportFailure, msg=url):
                self.transport(url, {})
        self.assertEqual(FakeConn.instances, [])
    def test_redirect_is_returned_not_followed(self):
        FakeConn.next_response = FakeResponse(302, {'Location': 'https://evil.example/', 'Content-Type': 'text/html'}, [b''])
        reply = self.transport(self.URL, {})
        self.assertEqual(reply.status, 302)
        self.assertEqual(len(FakeConn.instances), 1)
    def test_size_cap_on_stream_and_on_declared_length(self):
        small = yc.HttpsTransport(connection_factory=FakeConn, max_bytes=10, monotonic=lambda: 0.0)
        FakeConn.next_response = FakeResponse(chunks=[b'123456', b'789012'])
        with self.assertRaises(yc.ResponseTooLarge):
            small(self.URL, {})
        FakeConn.next_response = FakeResponse(headers={'Content-Type': JSON, 'Content-Length': '11'}, chunks=[b'x'])
        with self.assertRaises(yc.ResponseTooLarge):
            small(self.URL, {})
        FakeConn.next_response = FakeResponse(chunks=[b'1234567890'])
        self.assertEqual(small(self.URL, {}).body, b'1234567890')
    def test_timeouts_and_socket_errors_are_transient(self):
        for error in (socket.timeout(), TimeoutError(), ConnectionResetError(), OSError('x')):
            FakeConn.error = error
            with self.assertRaises(yc.TransientError):
                self.transport(self.URL, {})
    def test_certificate_failure_is_not_retryable(self):
        FakeConn.error = ssl.SSLCertVerificationError('bad certificate')
        with self.assertRaises(yc.TransportFailure) as caught:
            self.transport(self.URL, {})
        self.assertNotIn('bad certificate', str(caught.exception))
    def test_overall_deadline_stops_a_slow_drip(self):
        clock = iter([0.0, 0.0, 5.0, 100.0, 100.0, 100.0])
        slow = yc.HttpsTransport(connection_factory=FakeConn, monotonic=lambda: next(clock))
        FakeConn.next_response = FakeResponse(chunks=[b'a'] * 10)
        with self.assertRaises(yc.TransientError):
            slow(self.URL, {})
if __name__ == '__main__':
    unittest.main()
