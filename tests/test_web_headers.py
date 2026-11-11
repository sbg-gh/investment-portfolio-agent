"""Header tampering, path and ID tampering, response headers and route-registry completeness."""
import unittest

from portfolio_agent.web.app import Route
from tests.web_client import make_env
from tests.test_web_auth import EXTRA, whoami


class HostAndHeaderTamperingTests(unittest.TestCase):
    def setUp(self):
        self.env = make_env(EXTRA)
        self.c = self.env.client()
        self.c.login(self.env.tokens['alice'])

    def test_bad_host_headers_are_403_even_with_a_valid_session(self):
        for host in ('evil.example', 'evil.example:8765', 'localhost', 'evil.localhost:8765',
                     '127.0.0.1:8765.evil.com', '0.0.0.0:8765', ''):
            r = self.c.request('GET', '/api/session', headers={'host': host})
            self.assertEqual((r.status, r.json()), (403, {'error': 'forbidden'}), host)

    def test_missing_host_is_403(self):
        self.assertEqual(self.c.request('GET', '/api/session', headers={'host': None}).status, 403)

    def test_duplicate_singleton_headers_are_400(self):
        from portfolio_agent.web.app import Request
        for name in ('host', 'origin', 'content-type', 'cookie', 'x-csrf-token'):
            headers = [('host', '127.0.0.1:8765'), ('origin', 'http://127.0.0.1:8765'),
                       ('content-type', 'application/json'), (name, 'http://127.0.0.1:8765')]
            if name != 'host':
                headers = [h for h in headers if h[0] != name] + [(name, 'a'), (name, 'b')]
            else:
                headers = [('host', '127.0.0.1:8765'), ('host', 'evil.example')]
            r = self.env.app.handle(Request('POST', '/api/session', '', headers, b'{}', '127.0.0.1', 'http'))
            self.assertEqual(r.status, 400, name)

    def test_oversized_and_too_many_headers_are_431(self):
        self.assertEqual(self.c.request('GET', '/api/session', headers={'x-big': 'v' * 9000}).status, 431)
        many = {f'x-h{i}': 'v' for i in range(100)}
        self.assertEqual(self.c.request('GET', '/api/session', headers=many).status, 431)

    def test_header_injection_characters_are_400(self):
        r = self.c.request('GET', '/api/session', headers={'x-a': 'a\r\nSet-Cookie: pa_session=evil'})
        self.assertEqual(r.status, 400)

    def test_transfer_encoding_is_refused(self):
        r = self.c.request('POST', '/api/whoami', json={}, headers={'transfer-encoding': 'chunked'})
        self.assertEqual(r.status, 400)

    def test_forwarded_headers_are_ignored(self):
        r = self.c.request('GET', '/api/session', headers={
            'x-forwarded-host': 'evil.example', 'x-forwarded-proto': 'https', 'x-forwarded-for': '1.2.3.4',
            'forwarded': 'for=1.2.3.4;proto=https'})
        self.assertEqual(r.status, 200)
        self.assertNotIn('Secure', ''.join(r.set_cookies))

    def test_no_cors_headers_and_options_is_refused(self):
        r = self.c.request('OPTIONS', '/api/session', headers={'origin': 'http://evil.example',
                                                                'access-control-request-method': 'POST'})
        self.assertEqual(r.status, 405)
        for reply in (r, self.c.request('GET', '/api/session')):
            self.assertFalse([h for h in reply.headers if h.startswith('access-control-')])

    def test_malformed_request_line_parts(self):
        from portfolio_agent.web.app import Request
        for method in ('get', 'BREW', '', 'GET\r\n', None):
            r = self.env.app.handle(Request(method, '/api/session', '', [('host', '127.0.0.1:8765')],
                                            b'', '127.0.0.1', 'http'))
            self.assertEqual(r.status, 405, repr(method))
        r = self.env.app.handle(Request('GET', '/api/session', 'q=' + 'a' * 5000,
                                        [('host', '127.0.0.1:8765')], b'', '127.0.0.1', 'http'))
        self.assertEqual(r.status, 414)


class PathTamperingTests(unittest.TestCase):
    def setUp(self):
        self.env = make_env(EXTRA)
        self.c = self.env.client()
        self.c.login(self.env.tokens['alice'])

    def get(self, path):
        return self.c.request('GET', path)

    def test_valid_id_matches_and_binds_a_parameter(self):
        r = self.get('/api/things/abc-123.X_y:z')
        self.assertEqual((r.status, r.json()['id']), (200, 'abc-123.X_y:z'))

    def test_traversal_encoding_and_odd_paths_are_uniform_404s(self):
        paths = ['/api/things/../session', '/api/things/..', '/api/things/.', '/api/./session',
                 '/api/things/%2e%2e', '/api/things/..%2fsession', '/api/things/a%2Fb',
                 '/api/things/a%5Cb', '/api/things/a\\b', '/api/things//x', '//api/things/x',
                 '/api//session', '/api/things/x/', '/api/things/', '/api/things', '/API/things/x',
                 '/api/things/x/extra', '/api/things/a b', '/api/things/a\x00b', '/api/things/é',
                 '/api/things/%00', '/api/things/' + 'a' * 129, '/api/' + 'a/' * 300, 'api/things/x',
                 '/api/things/x?y', '/api/things/x#frag', '/api/things/a;b', '/api/things/a*b',
                 '/api/session/..', '/', '/api', '/api/']
        bodies = set()
        for path in paths:
            r = self.get(path)
            self.assertEqual(r.status, 404, repr(path))
            bodies.add(r.body)
        self.assertEqual(bodies, {b'{"error":"not_found"}'})

    def test_trailing_slash_session_is_not_an_alias(self):
        self.assertEqual(self.get('/api/session/').status, 404)

    def test_wrong_method_is_405_and_lists_nothing_private(self):
        r = self.c.request('PUT', '/api/session', json={})
        self.assertEqual((r.status, r.json()), (405, {'error': 'method_not_allowed'}))

    def test_unauthenticated_callers_get_401_not_404_on_private_routes_regardless_of_id(self):
        anon = self.env.client()
        results = {anon.request('GET', f'/api/things/{i}').body for i in ('real', 'nonexistent', 'x' * 100)}
        self.assertEqual(results, {b'{"error":"unauthenticated"}'})


class ResponseHeaderTests(unittest.TestCase):
    def test_every_kind_of_response_carries_security_headers(self):
        env = make_env(EXTRA)
        c = env.client()
        replies = [c.request('GET', '/api/session'), c.login(env.tokens['alice']),
                   c.request('GET', '/api/session'), c.request('GET', '/api/nope'),
                   c.request('PUT', '/api/session', json={}),
                   c.request('POST', '/api/whoami', body=b'{'),
                   c.request('GET', '/api/session', headers={'host': 'evil'}),
                   c.request('GET', '/api/session', headers={'x-big': 'v' * 9000}),
                   c.request('GET', '/not-api'), c.request('DELETE', '/api/session')]
        for r in replies:
            self.assertEqual(r.headers['cache-control'] in ('no-store', 'no-cache'), True)
            self.assertIn("default-src 'none'", r.headers['content-security-policy'])
            self.assertEqual(r.headers['x-content-type-options'], 'nosniff')
            self.assertEqual(r.headers['referrer-policy'], 'no-referrer')
            self.assertEqual(r.headers['x-frame-options'], 'DENY')
            self.assertTrue(r.headers['x-request-id'])
            self.assertNotIn('server', r.headers)
        for r in replies[:-1]:
            if r.raw_headers and r.headers['content-type'].startswith('application/json'):
                self.assertEqual(r.headers['cache-control'], 'no-store')

    def test_all_api_responses_are_no_store_json_utf8(self):
        env = make_env()
        r = env.client().request('GET', '/api/session')
        self.assertEqual(r.headers['cache-control'], 'no-store')
        self.assertEqual(r.headers['content-type'], 'application/json; charset=utf-8')

    def test_request_ids_are_unique_uuids(self):
        env = make_env()
        ids = {env.client().request('GET', '/api/session').headers['x-request-id'] for _ in range(20)}
        self.assertEqual(len(ids), 20)


class RouteRegistryCompletenessTests(unittest.TestCase):
    """Every registered route must be covered by the generic unauth/CSRF/Origin/operator checks."""

    def setUp(self):
        self.env = make_env(EXTRA)
        self.alice = self.env.client()
        self.alice.login(self.env.tokens['alice'])

    def concrete(self, route):
        return route.template.replace('{id}', 'x1')

    def test_registry_declares_every_flag_and_unique_method_template_pairs(self):
        seen = set()
        for route in self.env.app.routes:
            self.assertIsInstance(route.private, bool)
            self.assertIsInstance(route.unsafe, bool)
            self.assertIsInstance(route.operator, bool)
            self.assertTrue(callable(route.handler))
            self.assertEqual(route.unsafe, route.method not in ('GET', 'HEAD'), route.template)
            if route.operator:
                self.assertTrue(route.private, route.template)
            self.assertNotIn((route.method, route.template), seen)
            seen.add((route.method, route.template))

    def test_private_routes_reject_anonymous_callers(self):
        anon = self.env.client()
        for route in self.env.app.routes:
            if route.private:
                r = anon.request(route.method, self.concrete(route), json={} if route.unsafe else None)
                self.assertEqual((r.status, r.body), (401, b'{"error":"unauthenticated"}'),
                                 (route.method, route.template))

    def test_unsafe_routes_require_origin_and_private_ones_csrf(self):
        for route in self.env.app.routes:
            if not route.unsafe:
                continue
            path = self.concrete(route)
            self.assertEqual(self.alice.request(route.method, path, json={}, origin=False).status, 403,
                             route.template)
            if route.private:
                self.assertEqual(self.alice.request(route.method, path, json={}, csrf=False).status, 403,
                                 route.template)

    def test_operator_routes_reject_plain_users(self):
        for route in self.env.app.routes:
            if route.operator:
                r = self.alice.request(route.method, self.concrete(route), json={})
                self.assertEqual(r.status, 403, route.template)

    def test_default_registry_contains_exactly_the_session_routes(self):
        from portfolio_agent.web.app import session_routes
        self.assertEqual({(r.method, r.template) for r in session_routes()},
                         {('POST', '/api/session'), ('GET', '/api/session'), ('DELETE', '/api/session')})
        flags = {(r.method): (r.private, r.unsafe, r.operator) for r in session_routes()}
        self.assertEqual(flags, {'POST': (False, True, False), 'GET': (True, False, False),
                                 'DELETE': (True, True, False)})


if __name__ == '__main__':
    unittest.main()
