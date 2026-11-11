"""Server wiring, bind policy and operator CLI. Real-loopback tests need the ui extra and skip without it."""
import contextlib
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest

from portfolio_agent.web import server
from portfolio_agent.web.bind import REMOTE_ACK, REMOTE_ENV

HAVE_UI = all(importlib.util.find_spec(name) for name in ('uvicorn', 'h11'))


def run_cli(*argv, env=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = server.main(list(argv), env=env if env is not None else {})
        except SystemExit as exc:
            code = exc.code
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / 'p.db')

    def issue(self, user='alice', *extra):
        code, out, err = run_cli('create-user', user, '--db', self.db)
        self.assertEqual(code, 0, err)
        code, out, err = run_cli('issue-credential', user, '--db', self.db, *extra)
        self.assertEqual(code, 0, err)
        return json.loads(out), err

    def test_defaults_are_loopback_and_port_8765(self):
        args = server.parse_args(['serve', '--db', self.db])
        self.assertEqual((args.host, args.port, args.allow_non_loopback), ('127.0.0.1', 8765, False))

    def test_non_loopback_binds_are_refused_without_the_double_override(self):
        for host in ('0.0.0.0', '::', '192.168.1.5', 'example.com'):
            code, out, err = run_cli('serve', '--db', self.db, '--host', host)
            self.assertEqual(code, 2, host)
            self.assertIn('refused', err)
            self.assertEqual(out, '')
        # one half of the override is not enough, and neither is the pair without an allowed host
        code, _, err = run_cli('serve', '--db', self.db, '--host', '0.0.0.0', '--allow-non-loopback')
        self.assertEqual(code, 2)
        code, _, err = run_cli('serve', '--db', self.db, '--host', '0.0.0.0',
                               env={REMOTE_ENV: REMOTE_ACK})
        self.assertEqual(code, 2)
        code, _, err = run_cli('serve', '--db', self.db, '--host', '0.0.0.0', '--allow-non-loopback',
                               env={REMOTE_ENV: REMOTE_ACK})
        self.assertEqual(code, 2)
        self.assertFalse(Path(self.db).exists(), 'a refused bind must not create or open the database')

    def test_issue_credential_prints_the_token_once_and_stores_only_a_hash(self):
        issued, err = self.issue('alice')
        token = issued['token']
        self.assertGreaterEqual(len(token), 32)
        self.assertIn('credential_id', issued)
        self.assertNotIn(token, err)
        self.assertNotIn(token.encode(), Path(self.db).read_bytes())
        again, _ = self.issue_again('alice')
        self.assertNotEqual(again['token'], token)

    def issue_again(self, user):
        code, out, err = run_cli('issue-credential', user, '--db', self.db)
        self.assertEqual(code, 0, err)
        return json.loads(out), err

    def test_operator_role_and_ttl(self):
        issued, _ = self.issue('admin', '--role', 'operator', '--ttl-hours', '1')
        store, auth = server.open_auth(self.db)
        try:
            _, role = auth.reauthenticate(issued['credential_id'])
            self.assertEqual(role, 'operator')
            self.assertEqual(issued['expires_in_seconds'], 3600)
        finally:
            store.close()
        for bad in ('0', '-1', 'abc', '100000'):
            code, _, _ = run_cli('issue-credential', 'admin', '--db', self.db, '--ttl-hours', bad)
            self.assertNotEqual(code, 0, bad)

    def test_revoke_credential_ends_it(self):
        issued, _ = self.issue('alice')
        code, out, _ = run_cli('revoke-credential', issued['credential_id'], '--db', self.db)
        self.assertEqual(code, 0)
        store, auth = server.open_auth(self.db)
        try:
            with self.assertRaises(PermissionError):
                auth.authenticate(issued['token'])
        finally:
            store.close()

    def test_unknown_user_and_duplicate_user_fail_without_a_traceback(self):
        code, out, err = run_cli('issue-credential', 'nobody', '--db', self.db)
        self.assertNotEqual(code, 0)
        self.assertNotIn('Traceback', err)
        run_cli('create-user', 'alice', '--db', self.db)
        code, _, err = run_cli('create-user', 'alice', '--db', self.db)
        self.assertNotEqual(code, 0)
        self.assertNotIn('Traceback', err)
        self.assertNotIn(self.db, err.replace('--db', ''))

    def test_serve_without_the_ui_extra_explains_how_to_install_it(self):
        if HAVE_UI:
            self.skipTest('ui extra installed')
        code, _, err = run_cli('serve', '--db', self.db)
        self.assertEqual(code, 2)
        self.assertIn('.[ui]', err)


class WiringTests(unittest.TestCase):
    """The assembled ASGI app over a real database file, with no model configured."""
    def test_login_and_chat_degrade_when_no_model_is_configured(self):
        from tests.test_web_asgi import Driver
        from tests.web_client import ORIGIN
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = str(Path(tmp.name) / 'p.db')
        run_cli('create-user', 'alice', '--db', db)
        token = json.loads(run_cli('issue-credential', 'alice', '--db', db)[1])['token']
        asgi = server.build_asgi(db, port=8765, model=None, valuation_source='operator-entered',
                                 static_dir=Path(tmp.name) / 'none', log=lambda line: None)
        self.addCleanup(asgi.close)
        driver = Driver(asgi)
        status, _, body = driver.call('POST', '/api/session', body=json.dumps({'token': token}).encode(),
                                      headers=[('origin', ORIGIN), ('content-type', 'application/json')])
        self.assertEqual(status, 200, body)
        cookie = [v.decode().split(';')[0] for k, v in driver.messages[0]['headers'] if k == b'set-cookie'][0]
        csrf = json.loads(body)['csrf']
        status, _, body = driver.call('POST', '/api/chat', body=b'{"message":"hello"}', headers=[
            ('cookie', cookie), ('origin', ORIGIN), ('content-type', 'application/json'),
            ('x-csrf-token', csrf)])
        self.assertEqual(status, 200)
        self.assertIn('"model_status":"unavailable"', body.decode())
        self.assertNotIn('Traceback', body.decode())


@unittest.skipUnless(HAVE_UI, 'ui extra (uvicorn, h11) is not installed')
class LoopbackServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import uvicorn
        from portfolio_agent.model import ScriptedModel
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = str(Path(cls.tmp.name) / 'p.db')
        run_cli('create-user', 'alice', '--db', cls.db)
        cls.token = json.loads(run_cli('issue-credential', 'alice', '--db', cls.db)[1])['token']
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        cls.port = sock.getsockname()[1]
        sock.close()
        cls.model = ScriptedModel(['{"explanation":"hello over http","tool_calls":[]}'] * 3)
        real = cls.model.generate

        def slow(prompt, *, max_tokens):
            time.sleep(0.4)
            return real(prompt, max_tokens=max_tokens)
        cls.model.generate = slow
        cls.server, cls.asgi = server.build_server(
            cls.db, host='127.0.0.1', port=cls.port, model=cls.model,
            valuation_source='operator-entered', static_dir=Path(cls.tmp.name) / 'none',
            keepalive=0.1, log=lambda line: None)
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        deadline = time.time() + 10
        while not cls.server.started and time.time() < deadline:
            time.sleep(0.02)
        assert cls.server.started, 'server did not start'

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(10)
        cls.tmp.cleanup()

    def conn(self):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        self.addCleanup(connection.close)
        return connection

    def raw(self, request_bytes):
        with socket.create_connection(('127.0.0.1', self.port), timeout=10) as sock:
            sock.sendall(request_bytes)
            data = b''
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    return data
                data += chunk
                if b'\r\n\r\n' in data and len(data) > 12:
                    head, _, rest = data.partition(b'\r\n\r\n')
                    length = [int(l.split(b':')[1]) for l in head.split(b'\r\n')
                              if l.lower().startswith(b'content-length')]
                    if length and len(rest) >= length[0]:
                        return data

    def login(self):
        conn = self.conn()
        origin = f'http://127.0.0.1:{self.port}'
        conn.request('POST', '/api/session', json.dumps({'token': self.token}),
                     {'Origin': origin, 'Content-Type': 'application/json'})
        response = conn.getresponse()
        body = json.loads(response.read())
        self.assertEqual(response.status, 200, body)
        return response.getheader('Set-Cookie').split(';')[0], body['csrf'], origin

    def test_binds_loopback_only_and_hides_server_identity(self):
        names = {s.getsockname()[0] for s in self.server.servers[0].sockets}
        self.assertEqual(names, {'127.0.0.1'})
        conn = self.conn()
        conn.request('GET', '/')
        response = conn.getresponse()
        response.read()
        self.assertEqual(response.status, 200)
        self.assertIsNone(response.getheader('Server'))
        self.assertIn("default-src 'none'", response.getheader('Content-Security-Policy'))

    def test_spoofed_and_rebinding_host_headers_are_refused(self):
        for host in ('evil.example', f'localhost.evil.example:{self.port}', f'127.0.0.1:{self.port + 1}'):
            data = self.raw(f'GET /api/session HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n'.encode())
            self.assertTrue(data.startswith(b'HTTP/1.1 403'), (host, data[:40]))
        data = self.raw(b'GET /api/session HTTP/1.0\r\n\r\n')
        self.assertTrue(data.startswith(b'HTTP/1.0 403') or data.startswith(b'HTTP/1.1 403'), data[:40])

    def test_cross_origin_post_and_oversized_body_are_refused(self):
        conn = self.conn()
        conn.request('POST', '/api/session', json.dumps({'token': self.token}),
                     {'Origin': 'http://evil.example', 'Content-Type': 'application/json'})
        response = conn.getresponse()
        response.read()
        self.assertEqual(response.status, 403)
        conn = self.conn()
        origin = f'http://127.0.0.1:{self.port}'
        conn.request('POST', '/api/session', b'{"token":"' + b'x' * 70000 + b'"}',
                     {'Origin': origin, 'Content-Type': 'application/json'})
        response = conn.getresponse()
        response.read()
        self.assertEqual(response.status, 413)

    def test_malformed_requests_get_a_clean_400_or_close(self):
        for request in (b'GARBAGE\r\n\r\n', b'GET /\x00 HTTP/1.1\r\nHost: x\r\n\r\n',
                        b'GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n' + b'X: ' + b'a' * 20000 + b'\r\n\r\n'):
            data = self.raw(request)
            self.assertTrue(data == b'' or data.startswith(b'HTTP/1.1 4'), data[:60])

    def test_encoded_separator_does_not_reach_a_route(self):
        cookie, csrf, origin = self.login()
        conn = self.conn()
        conn.request('GET', '/api/portfolios/a%2Fb/holdings', headers={'Cookie': cookie})
        response = conn.getresponse()
        self.assertEqual((response.status, response.read()), (404, b'{"error":"not_found"}'))

    def test_sse_round_trip_delivers_status_before_the_slow_model_finishes(self):
        cookie, csrf, origin = self.login()
        conn = self.conn()
        started = time.monotonic()
        conn.request('POST', '/api/chat', json.dumps({'message': 'hi'}),
                     {'Cookie': cookie, 'Origin': origin, 'Content-Type': 'application/json',
                      'X-CSRF-Token': csrf})
        response = conn.getresponse()
        self.assertEqual(response.status, 200)
        self.assertTrue(response.getheader('Content-Type').startswith('text/event-stream'))
        first = response.read1(4096)  # one decoded chunk, without waiting for the whole body
        self.assertTrue(first.startswith(b'event: status\n'), first)
        self.assertLess(time.monotonic() - started, 0.35, 'status must arrive before the model returns')
        rest = response.read().decode()
        self.assertIn(': keep-alive', rest)
        self.assertIn('event: done', rest)
        self.assertIn('"text":"hello over http"', rest)

    def test_no_request_detail_is_written_to_the_logs(self):
        import logging
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logging.getLogger().addHandler(handler)
        logging.getLogger('uvicorn.access').addHandler(handler)
        self.addCleanup(logging.getLogger().removeHandler, handler)
        self.addCleanup(logging.getLogger('uvicorn.access').removeHandler, handler)
        conn = self.conn()
        conn.request('GET', '/api/portfolios/SECRET-PORTFOLIO-ID/holdings?x=SECRET-QUERY')
        conn.getresponse().read()
        self.assertNotIn('SECRET', stream.getvalue())
        self.assertFalse(self.server.config.access_log)
        self.assertFalse(self.server.config.proxy_headers)
        self.assertFalse(self.server.config.server_header)


if __name__ == '__main__':
    unittest.main()
