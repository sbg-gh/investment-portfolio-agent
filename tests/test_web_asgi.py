"""ASGI adapter driven by a hand-rolled ASGI harness (stdlib only; no uvicorn needed)."""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from portfolio_agent.web.asgi import AsgiApp, load_static
from tests.web_client import HOST, ORIGIN, make_env


class Driver:
    """Runs one ASGI HTTP exchange and records everything the app sends."""
    def __init__(self, asgi):
        self.asgi = asgi

    def call(self, method='GET', path='/api/session', *, raw_path=None, query=b'', headers=None,
             body=b'', chunks=None, host=HOST, disconnect_after=None, scheme='http'):
        send_headers = [(b'host', host.encode())] if host else []
        for name, value in (headers or []):
            send_headers.append((name.encode(), value.encode()))
        scope = {'type': 'http', 'method': method, 'path': path,
                 'raw_path': (raw_path or path).encode(), 'query_string': query,
                 'headers': send_headers, 'client': ('127.0.0.1', 50000), 'scheme': scheme}
        parts = list(chunks) if chunks is not None else [body]
        sent = []
        stamp = []

        async def receive():
            if parts:
                data = parts.pop(0)
                return {'type': 'http.request', 'body': data, 'more_body': bool(parts)}
            if disconnect_after is not None:
                await asyncio.sleep(disconnect_after)
                return {'type': 'http.disconnect'}
            await asyncio.sleep(3600)

        async def send(message):
            stamp.append(time.monotonic())
            sent.append(message)

        asyncio.run(asyncio.wait_for(self.asgi(scope, receive, send), 10))
        start = sent[0]
        self.stamps = stamp
        self.messages = sent
        headers_out = {}
        for name, value in start['headers']:
            headers_out.setdefault(name.decode(), value.decode())
        payload = b''.join(m.get('body', b'') for m in sent[1:])
        return start['status'], headers_out, payload


def login(driver, env, name='alice'):
    status, headers, body = driver.call(
        'POST', '/api/session', body=json.dumps({'token': env.tokens[name]}).encode(),
        headers=[('origin', ORIGIN), ('content-type', 'application/json')])
    assert status == 200, body
    cookies = [v.decode().split(';')[0] for k, v in driver.messages[0]['headers'] if k == b'set-cookie']
    return cookies[0], json.loads(body)['csrf']


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.env = make_env(full=True, worker=True)
        self.addCleanup(self.env.executor.close)
        self.driver = Driver(AsgiApp(self.env.app, keepalive=0.05))

    def test_api_round_trip_with_session_cookie(self):
        cookie, csrf = login(self.driver, self.env)
        status, headers, body = self.driver.call('GET', '/api/session', headers=[('cookie', cookie)])
        self.assertEqual((status, json.loads(body)['user_id']), (200, 'alice'))
        self.assertEqual(headers['cache-control'], 'no-store')

    def test_unauthenticated_and_spoofed_host(self):
        status, _, body = self.driver.call('GET', '/api/portfolios')
        self.assertEqual((status, body), (401, b'{"error":"unauthenticated"}'))
        for host in ('evil.example', f'127.0.0.1.evil.example:8765', None):
            status, _, _ = self.driver.call('GET', '/api/portfolios', host=host)
            self.assertEqual(status, 403, host)

    def test_duplicate_singleton_headers_are_preserved_for_the_core(self):
        status, _, _ = self.driver.call('GET', '/api/portfolios', headers=[('host', HOST)])
        self.assertEqual(status, 400)

    def test_raw_path_is_used_so_encoded_slashes_do_not_match(self):
        cookie, _ = login(self.driver, self.env)
        status, _, body = self.driver.call(
            'GET', '/api/portfolios/a/b/holdings', raw_path='/api/portfolios/a%2Fb/holdings',
            headers=[('cookie', cookie)])
        self.assertEqual((status, body), (404, b'{"error":"not_found"}'))

    def test_oversized_streamed_body_is_cut_off_with_413(self):
        read = []
        chunks = [b'x' * 40000, b'x' * 40000, b'x' * 40000]
        status, _, body = self.driver.call('POST', '/api/session', chunks=chunks,
                                           headers=[('origin', ORIGIN),
                                                    ('content-type', 'application/json')])
        self.assertEqual((status, body), (413, b'{"error":"body_too_large"}'))

    def test_post_without_a_declared_matching_length_is_rejected(self):
        status, _, _ = self.driver.call('POST', '/api/session', body=b'{}', headers=[
            ('origin', ORIGIN), ('content-type', 'application/json'), ('content-length', '999')])
        self.assertEqual(status, 400)

    def test_non_http_scopes_are_refused_and_lifespan_completes(self):
        sent = []

        async def receive():
            return {'type': 'websocket.connect'}

        async def send(message):
            sent.append(message)
        asyncio.run(self.driver.asgi({'type': 'websocket', 'path': '/api/session', 'headers': []},
                                     receive, send))
        self.assertEqual(sent, [{'type': 'websocket.close', 'code': 1008}])
        messages = [{'type': 'lifespan.startup'}, {'type': 'lifespan.shutdown'}]
        out = []

        async def lreceive():
            return messages.pop(0)

        async def lsend(message):
            out.append(message['type'])
        asyncio.run(self.driver.asgi({'type': 'lifespan'}, lreceive, lsend))
        self.assertEqual(out, ['lifespan.startup.complete', 'lifespan.shutdown.complete'])


class SseAdapterTests(unittest.TestCase):
    def boot(self, delay=0.0):
        env = make_env(full=True, worker=True, replies=['{"explanation":"hello","tool_calls":[]}'])
        self.addCleanup(env.executor.close)
        real_generate = env.model.generate

        def slow(prompt, *, max_tokens):
            time.sleep(delay)
            return real_generate(prompt, max_tokens=max_tokens)
        env.model.generate = slow
        self.env = env
        self.driver = Driver(AsgiApp(env.app, keepalive=0.05))
        self.cookie, self.csrf = login(self.driver, env)

    def chat(self, **kw):
        return self.driver.call('POST', '/api/chat', body=json.dumps({'message': 'hi'}).encode(),
                                headers=[('cookie', self.cookie), ('origin', ORIGIN),
                                         ('content-type', 'application/json'),
                                         ('x-csrf-token', self.csrf)], **kw)

    def test_status_event_is_sent_first_then_keepalives_then_the_result(self):
        self.boot(delay=0.3)
        status, headers, body = self.chat()
        self.assertEqual(status, 200)
        self.assertTrue(headers['content-type'].startswith('text/event-stream'))
        self.assertEqual(headers['cache-control'], 'no-store')
        bodies = [m.get('body', b'') for m in self.driver.messages[1:]]
        self.assertTrue(bodies[0].startswith(b'event: status'))
        self.assertTrue(any(b.startswith(b': keep-alive') for b in bodies), bodies)
        text = body.decode()
        self.assertLess(text.index('event: status'), text.index('event: done'))
        self.assertIn('"text":"hello"', text)
        # The status event went out before the (slow) result did.
        self.assertGreater(self.driver.stamps[-1] - self.driver.stamps[1], 0.2)
        self.assertFalse(self.driver.messages[-1]['more_body'])

    def test_errors_before_streaming_keep_their_status(self):
        self.boot()
        status, _, body = self.driver.call('POST', '/api/chat', body=b'{"message":""}', headers=[
            ('cookie', self.cookie), ('origin', ORIGIN), ('content-type', 'application/json'),
            ('x-csrf-token', self.csrf)])
        self.assertEqual((status, body), (422, b'{"error":"invalid_field"}'))

    def test_client_disconnect_stops_writing_without_error(self):
        self.boot(delay=0.4)
        self.chat(disconnect_after=0.1)
        sent_types = [m['type'] for m in self.driver.messages]
        self.assertEqual(sent_types[0], 'http.response.start')
        self.assertNotIn(b'event: done', b''.join(m.get('body', b'') for m in self.driver.messages))
        time.sleep(0.5)  # the worker thread finishes its turn harmlessly


class StaticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'assets').mkdir()
        (root / 'index.html').write_text('<!doctype html><title>app</title>')
        (root / 'assets' / 'app.js').write_text('console.log(1)')
        (root / 'assets' / 'app.css').write_text('a{}')
        (root / 'secret.map').write_text('nope')
        self.env = make_env(full=True, worker=True)
        self.addCleanup(self.env.executor.close)
        self.driver = Driver(AsgiApp(self.env.app, static=load_static(root)))

    def test_index_assets_and_content_types(self):
        status, headers, body = self.driver.call('GET', '/')
        self.assertEqual((status, body), (200, b'<!doctype html><title>app</title>'))
        self.assertTrue(headers['content-type'].startswith('text/html'))
        self.assertIn("default-src 'none'", headers['content-security-policy'])
        self.assertEqual(headers['cache-control'], 'no-cache')
        self.assertEqual(headers['x-content-type-options'], 'nosniff')
        status, headers, body = self.driver.call('GET', '/assets/app.js')
        self.assertEqual((status, body), (200, b'console.log(1)'))
        self.assertTrue(headers['content-type'].startswith('text/javascript'))
        self.assertTrue(self.driver.call('GET', '/assets/app.css')[1]['content-type'].startswith('text/css'))

    def test_spa_fallback_only_for_extensionless_paths_and_source_maps_are_not_served(self):
        self.assertEqual(self.driver.call('GET', '/portfolios/abc')[2], b'<!doctype html><title>app</title>')
        for path in ('/assets/missing.js', '/secret.map', '/assets/', '/index.html.bak'):
            self.assertEqual(self.driver.call('GET', path)[0], 404, path)

    def test_traversal_attempts_never_read_the_filesystem(self):
        for path in ('/../../etc/passwd', '/assets/../../../etc/passwd', '/%2e%2e/%2e%2e/etc/passwd',
                     '/assets/..%2f..%2fsecret.map', '/\x00', '/assets\\..\\app.js'):
            status, _, body = self.driver.call('GET', path, raw_path=path)
            self.assertIn(status, (200, 404), path)
            self.assertNotIn(b'root:', body)
            self.assertNotIn(b'nope', body)

    def test_static_checks_host_and_only_serves_get(self):
        self.assertEqual(self.driver.call('GET', '/', host='evil.example')[0], 403)
        status, _, body = self.driver.call('POST', '/', headers=[('origin', ORIGIN)])
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(body), {'error': 'not_found'})
        self.assertEqual(self.driver.call('GET', '/api/unknown')[0], 404)  # api never falls back

    def test_missing_build_serves_a_plain_notice_but_the_api_still_works(self):
        driver = Driver(AsgiApp(self.env.app, static=load_static(Path(self.tmp.name) / 'absent')))
        status, headers, body = driver.call('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn(b'npm --prefix ui run build', body)
        self.assertEqual(driver.call('GET', '/api/session')[0], 401)

    def test_static_loader_skips_source_maps_and_oversized_files(self):
        files = load_static(Path(self.tmp.name))
        self.assertEqual(sorted(files), ['/assets/app.css', '/assets/app.js', '/index.html'])


if __name__ == '__main__':
    unittest.main()
