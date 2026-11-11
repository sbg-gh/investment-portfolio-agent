"""Auth routes and the hand-written request core: identity only from the session."""
import unittest

from portfolio_agent.web.app import Route
from tests.web_client import HOST, ORIGIN, make_env

DENIED = {'error': 'unauthenticated'}


def whoami(request):
    return 200, {'user_id': request.user_id, 'role': request.role, 'id': request.params.get('id')}


EXTRA = [Route('GET', '/api/whoami', whoami, private=True),
         Route('POST', '/api/whoami', whoami, private=True, unsafe=True),
         Route('GET', '/api/things/{id}', whoami, private=True),
         Route('POST', '/api/operator/ping', whoami, private=True, unsafe=True, operator=True)]


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.env = make_env(EXTRA)
        self.c = self.env.client()

    def test_login_success_sets_hardened_cookie_and_returns_csrf(self):
        r = self.c.login(self.env.tokens['alice'])
        self.assertEqual(r.status, 200)
        body = r.json()
        self.assertEqual((body['user_id'], body['role']), ('alice', 'user'))
        self.assertGreaterEqual(len(body['csrf']), 32)
        cookie = r.set_cookies[0]
        for attr in ('HttpOnly', 'SameSite=Strict', 'Path=/', 'Max-Age=28800'):
            self.assertIn(attr, cookie)
        self.assertNotIn('Secure', cookie)
        self.assertNotIn(self.env.tokens['alice'], r.body.decode() + cookie)

    def test_secure_flag_only_over_https(self):
        r = self.env.client(scheme='https').login(self.env.tokens['alice'], origin=False,
                                                  headers={'origin': ORIGIN})
        self.assertEqual(r.status, 200)
        self.assertIn('Secure', r.set_cookies[0])

    def test_wrong_tokens_get_one_generic_401(self):
        bodies = set()
        for token in ('nope', '', 'x' * 1000, "' OR 1=1 --", self.env.tokens['alice'] + 'x'):
            r = self.c.request('POST', '/api/session', json={'token': token})
            self.assertEqual(r.status, 401, token[:10])
            bodies.add(r.body)
        self.assertEqual(bodies, {b'{"error":"unauthenticated"}'})
        self.assertEqual(self.c.cookies, {})

    def test_malformed_login_bodies(self):
        cases = [({'token': 5}, 422), ({'token': ['a']}, 401), ({}, 401), ({'tok': 'x'}, 401)]
        for payload, status in cases:
            self.assertEqual(self.c.request('POST', '/api/session', json=payload).status, status, payload)
        self.assertEqual(self.c.request('POST', '/api/session', body=b'{').status, 400)
        self.assertEqual(self.c.request('POST', '/api/session', body=b'[]').status, 422)
        self.assertEqual(self.c.request('POST', '/api/session', body=b'').status, 400)

    def test_login_rotates_session_and_kills_the_presented_one(self):
        self.c.login(self.env.tokens['alice'])
        old = dict(self.c.cookies)
        self.c.login(self.env.tokens['alice'])
        self.assertNotEqual(old, self.c.cookies)
        stale = self.env.client()
        stale.cookies = old
        self.assertEqual(stale.request('GET', '/api/session').status, 401)

    def test_login_as_other_user_replaces_identity(self):
        self.c.login(self.env.tokens['alice'])
        self.c.login(self.env.tokens['bob'])
        self.assertEqual(self.c.request('GET', '/api/session').json()['user_id'], 'bob')

    def test_login_throttle_blocks_after_repeated_failures_then_recovers(self):
        for _ in range(5):
            self.assertEqual(self.c.request('POST', '/api/session', json={'token': 'bad'}).status, 401)
        r = self.c.request('POST', '/api/session', json={'token': self.env.tokens['alice']})
        self.assertEqual(r.status, 429)
        self.assertEqual(r.json(), {'error': 'rate_limited'})
        self.assertRegex(r.headers['retry-after'], r'^[1-9][0-9]*$')
        self.env.clock.now += 301
        self.assertEqual(self.c.request('POST', '/api/session', json={'token': self.env.tokens['alice']}).status, 200)

    def test_throttle_is_per_peer_until_global_limit(self):
        for _ in range(5):
            self.c.request('POST', '/api/session', json={'token': 'bad'})
        other = self.env.client(peer='127.0.0.9')
        self.assertEqual(other.login(self.env.tokens['bob']).status, 200)

    def test_revoked_credential_cannot_log_in(self):
        cid = self.env.auth.authenticate(self.env.tokens['alice']).credential_id
        self.env.auth.revoke(cid)
        self.assertEqual(self.c.login(self.env.tokens['alice']).status, 401)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.env = make_env(EXTRA)
        self.c = self.env.client()
        self.c.login(self.env.tokens['alice'])

    def test_get_session_returns_identity_and_csrf(self):
        body = self.c.request('GET', '/api/session').json()
        self.assertEqual(body['user_id'], 'alice')
        self.assertEqual(body['csrf'], self.c.csrf)

    def test_no_cookie_forged_garbage_and_duplicate_cookies_are_401(self):
        for cookies in ({}, {'pa_session': 'x'}, {'pa_session': 'A' * 43}, {'pa_session': '../..'}):
            c = self.env.client()
            c.cookies = cookies
            r = c.request('GET', '/api/session')
            self.assertEqual((r.status, r.json()), (401, DENIED), cookies)
        dup = self.env.client()
        r = dup.request('GET', '/api/session', cookies=False, headers={
            'cookie': f"pa_session={self.c.cookies['pa_session']}; pa_session=AAAA"})
        self.assertEqual(r.status, 401)

    def test_idle_and_absolute_expiry(self):
        self.env.clock.now += 1801
        self.assertEqual(self.c.request('GET', '/api/session').status, 401)
        c = self.env.client()
        c.login(self.env.tokens['alice'])
        start = self.env.clock.now
        while self.env.clock.now - start + 1700 < 28800:
            self.env.clock.now += 1700
            self.assertEqual(c.request('GET', '/api/session').status, 200)
        self.env.clock.now = start + 28800
        self.assertEqual(c.request('GET', '/api/session').status, 401)

    def test_revoking_the_credential_ends_the_session_immediately(self):
        cid = self.env.auth.authenticate(self.env.tokens['alice']).credential_id
        self.env.auth.revoke(cid)
        self.assertEqual(self.c.request('GET', '/api/session').status, 401)
        self.assertEqual(self.env.sessions.invalidate_credential(cid), 0, 'dead session already removed')

    def test_credential_expiry_ends_session(self):
        self.env.clock.now += 100001
        self.assertEqual(self.c.request('GET', '/api/session').status, 401)

    def test_logout_invalidates_server_side_and_clears_cookie(self):
        saved = dict(self.c.cookies)
        r = self.c.request('DELETE', '/api/session')
        self.assertEqual(r.status, 200)
        self.assertIn('Max-Age=0', r.set_cookies[0])
        replay = self.env.client()
        replay.cookies = saved
        self.assertEqual(replay.request('GET', '/api/session').status, 401)

    def test_logout_requires_csrf_and_origin(self):
        self.assertEqual(self.c.request('DELETE', '/api/session', csrf=False).status, 403)
        self.assertEqual(self.c.request('DELETE', '/api/session', origin=False).status, 403)
        self.assertEqual(self.c.request('DELETE', '/api/session',
                                        headers={'x-csrf-token': 'wrong'}).status, 403)
        self.assertEqual(self.c.request('GET', '/api/session').status, 200)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.env = make_env(EXTRA)
        self.c = self.env.client()
        self.c.login(self.env.tokens['alice'])

    def test_client_supplied_identity_is_ignored_everywhere(self):
        for kwargs in (
                {'json': {'user_id': 'bob', 'owner': 'bob', 'role': 'operator', 'operator': True}},
                {'json': {}, 'query': 'user_id=bob&role=operator&owner=bob&operator=1'},
                {'json': {}, 'headers': {'x-user-id': 'bob', 'x-role': 'operator', 'x-forwarded-user': 'bob',
                             'authorization': 'Bearer ' + self.env.tokens['bob']}}):
            r = self.c.request('POST', '/api/whoami', **kwargs)
            self.assertEqual(r.json()['user_id'], 'alice', kwargs)
            self.assertEqual(r.json()['role'], 'user', kwargs)

    def test_login_body_identity_fields_ignored(self):
        c = self.env.client()
        r = c.request('POST', '/api/session', json={
            'token': self.env.tokens['alice'], 'user_id': 'admin', 'role': 'operator', 'operator': True})
        self.assertEqual((r.json()['user_id'], r.json()['role']), ('alice', 'user'))

    def test_operator_routes_are_forbidden_for_users_and_open_for_operators(self):
        r = self.c.request('POST', '/api/operator/ping', json={'role': 'operator'})
        self.assertEqual((r.status, r.json()), (403, {'error': 'forbidden'}))
        admin = self.env.client()
        admin.login(self.env.tokens['admin'])
        self.assertEqual(admin.request('POST', '/api/operator/ping', json={}).status, 200)

    def test_role_comes_from_the_store_each_request(self):
        self.assertEqual(self.c.request('GET', '/api/whoami').json()['role'], 'user')
        self.env.store.db.execute("UPDATE credentials SET role='operator' WHERE user_id='alice'")
        self.assertEqual(self.c.request('GET', '/api/whoami').json()['role'], 'operator')


class CsrfOriginTests(unittest.TestCase):
    def setUp(self):
        self.env = make_env(EXTRA)
        self.c = self.env.client()
        self.c.login(self.env.tokens['alice'])

    def test_unsafe_private_request_needs_csrf_header(self):
        self.assertEqual(self.c.request('POST', '/api/whoami', json={}, csrf=False).status, 403)
        self.assertEqual(self.c.request('POST', '/api/whoami', json={},
                                        headers={'x-csrf-token': self.c.csrf[::-1]}).status, 403)
        self.assertEqual(self.c.request('POST', '/api/whoami', json={}).status, 200)

    def test_csrf_token_of_another_session_is_rejected(self):
        other = self.env.client()
        other.login(self.env.tokens['bob'])
        self.assertEqual(self.c.request('POST', '/api/whoami', json={},
                                        headers={'x-csrf-token': other.csrf}).status, 403)

    def test_origin_matrix_on_unsafe_requests(self):
        self.assertEqual(self.c.request('POST', '/api/whoami', json={}, origin=False).status, 403)
        for origin in ('http://evil.example', 'null', 'http://localhost', 'https://127.0.0.1:8765'):
            r = self.c.request('POST', '/api/whoami', json={}, headers={'origin': origin})
            self.assertEqual(r.status, 403, origin)
        self.assertEqual(self.c.request('POST', '/api/whoami', json={},
                                        headers={'origin': 'http://localhost:8765'}).status, 200)

    def test_cross_site_fetch_metadata_refused(self):
        r = self.c.request('POST', '/api/whoami', json={}, headers={'sec-fetch-site': 'cross-site'})
        self.assertEqual(r.status, 403)

    def test_login_also_requires_origin(self):
        c = self.env.client()
        self.assertEqual(c.request('POST', '/api/session', json={'token': self.env.tokens['alice']},
                                   origin=False).status, 403)

    def test_content_type_must_be_json_on_unsafe_requests(self):
        for value in ('text/plain', 'application/x-www-form-urlencoded', 'multipart/form-data; boundary=x'):
            r = self.c.request('POST', '/api/whoami', json={}, headers={'content-type': value})
            self.assertEqual(r.status, 415, value)
        self.assertEqual(self.c.request('POST', '/api/whoami', json={}, headers={'content-type': None}).status, 415)

    def test_denial_order_does_not_reveal_session_state_to_cross_origin_callers(self):
        anon = self.env.client()
        r = anon.request('POST', '/api/whoami', json={}, headers={'origin': 'http://evil.example'})
        self.assertEqual(r.status, 403)


class BodyTests(unittest.TestCase):
    def setUp(self):
        self.env = make_env(EXTRA)
        self.c = self.env.client()
        self.c.login(self.env.tokens['alice'])

    def test_oversized_body_413_even_without_content_length(self):
        r = self.c.request('POST', '/api/whoami', body=b'{"a":"' + b'x' * 70000 + b'"}')
        self.assertEqual((r.status, r.json()), (413, {'error': 'body_too_large'}))

    def test_declared_oversized_content_length_413_before_reading(self):
        r = self.c.request('POST', '/api/whoami', json={}, headers={'content-length': '99999999'})
        self.assertEqual(r.status, 413)

    def test_json_numbers_rejected_on_the_wire(self):
        for body in (b'{"quantity":5}', b'{"quantity":1.5}', b'{"quantity":NaN}'):
            r = self.c.request('POST', '/api/whoami', body=body)
            self.assertEqual((r.status, r.json()), (422, {'error': 'invalid_number'}))

    def test_malformed_and_non_utf8_json_400_without_echo(self):
        for body in (b'{"secret-token-123"', b'\xff\xfe', b'{"a":"b"}garbage'):
            r = self.c.request('POST', '/api/whoami', body=body)
            self.assertEqual(r.status, 400)
            self.assertNotIn(b'secret-token', r.body)

    def test_not_an_object_and_duplicate_keys_422(self):
        for body in (b'[]', b'"x"', b'{"a":"1","a":"2"}'):
            self.assertEqual(self.c.request('POST', '/api/whoami', body=body).status, 422)

    def test_bodyless_post_is_an_empty_object_only_when_route_allows(self):
        # whoami accepts any object; an empty body is not valid JSON
        self.assertEqual(self.c.request('POST', '/api/whoami', body=b'').status, 400)


class RateLimitTests(unittest.TestCase):
    def test_requests_are_limited_per_peer_with_retry_after(self):
        env = make_env(EXTRA, capacity=3)
        c = env.client()
        statuses = [c.request('GET', '/api/session').status for _ in range(5)]
        self.assertEqual(statuses[:3], [401, 401, 401])
        self.assertEqual(statuses[3:], [429, 429])
        r = c.request('GET', '/api/session')
        self.assertIn('retry-after', r.headers)
        self.assertEqual(r.json(), {'error': 'rate_limited'})
        self.assertEqual(env.client(peer='127.0.0.5').request('GET', '/api/session').status, 401)


class ErrorMappingTests(unittest.TestCase):
    def test_unhandled_exception_is_generic_500_and_logged_by_class_only(self):
        def boom(request):
            raise RuntimeError('db password=hunter2 /Users/gangas02/secret.sqlite')
        env = make_env([Route('GET', '/api/boom', boom)])
        r = env.client().request('GET', '/api/boom')
        self.assertEqual(r.status, 500)
        body = r.json()
        self.assertEqual(set(body), {'error', 'request_id'})
        self.assertEqual(body['error'], 'internal')
        self.assertNotIn('hunter2', r.body.decode())
        self.assertNotIn('Traceback', r.body.decode())
        self.assertTrue(any(line == f"ERROR RuntimeError {body['request_id']}" for line in env.logs))
        self.assertNotIn('hunter2', '\n'.join(env.logs))

    def test_permission_error_from_a_handler_is_the_generic_404(self):
        def deny(request):
            raise PermissionError('Portfolio unavailable: owned by bob')
        env = make_env([Route('GET', '/api/deny', deny)])
        r = env.client().request('GET', '/api/deny')
        self.assertEqual((r.status, r.json()), (404, {'error': 'not_found'}))

    def test_value_error_is_fixed_422_code(self):
        def bad(request):
            raise ValueError('quantity 5 for AAPL is invalid')
        env = make_env([Route('GET', '/api/bad', bad)])
        r = env.client().request('GET', '/api/bad')
        self.assertEqual((r.status, r.json()), (422, {'error': 'invalid_request'}))

    def test_worker_busy_is_503(self):
        from portfolio_agent.web.worker import WorkerBusy
        env = make_env()
        env.app._executor = type('Busy', (), {'submit': lambda self, fn: (_ for _ in ()).throw(WorkerBusy())})()
        r = env.client().request('POST', '/api/session', json={'token': 'x'})
        self.assertEqual((r.status, r.json()), (503, {'error': 'busy'}))


class AccessLogTests(unittest.TestCase):
    def test_log_lines_use_route_templates_and_hide_concrete_ids_and_secrets(self):
        env = make_env(EXTRA)
        c = env.client()
        c.login(env.tokens['alice'])
        c.request('GET', '/api/things/secret-portfolio-id', query='symbol=AAPL&q=1234.5')
        text = '\n'.join(env.logs)
        self.assertIn('GET /api/things/{id} 200', text)
        for secret in ('secret-portfolio-id', 'AAPL', '1234.5', env.tokens['alice'], c.csrf,
                       c.cookies['pa_session']):
            self.assertNotIn(secret, text)
        self.assertEqual(len(env.logs), 2)

    def test_unmatched_paths_log_a_dash_template(self):
        env = make_env()
        env.client().request('GET', '/api/../../etc/passwd')
        self.assertTrue(env.logs[0].startswith('GET - 404 '))


if __name__ == '__main__':
    unittest.main()
