"""Every registered route is exercised for the same security properties, generated from the registry."""
import unittest
from portfolio_agent.web.routes import default_routes
from tests.web_client import NOW, SOURCE, make_env

UNAUTH = b'{"error":"unauthenticated"}'
NOT_FOUND = b'{"error":"not_found"}'


def samples(pid):
    """(method, template) -> (path, json body, query). A route without a sample fails the build."""
    base = f'/api/portfolios/{pid}'
    holding = {'symbol': 'ACME', 'quantity': '1', 'idempotency_key': 'matrix-0000001'}
    return {
        ('POST', '/api/session'): ('/api/session', {'token': 'x'}, ''),
        ('GET', '/api/session'): ('/api/session', None, ''),
        ('DELETE', '/api/session'): ('/api/session', None, ''),
        ('GET', '/api/portfolios'): ('/api/portfolios', None, ''),
        ('POST', '/api/portfolios'): ('/api/portfolios', {'name': 'm'}, ''),
        ('GET', '/api/portfolios/{id}/holdings'): (base + '/holdings', None, ''),
        ('POST', '/api/portfolios/{id}/holdings'): (base + '/holdings', holding, ''),
        ('DELETE', '/api/portfolios/{id}/holdings/{security_id}'):
            (base + '/holdings/op:acme', {'idempotency_key': 'matrix-0000002'}, ''),
        ('GET', '/api/portfolios/{id}/receipts'): (base + '/receipts', None, ''),
        ('GET', '/api/portfolios/{id}/valuation'): (base + '/valuation', None, f'source={SOURCE}'),
        ('GET', '/api/securities'): ('/api/securities', None, 'ids=op:acme'),
        ('GET', '/api/sources'): ('/api/sources', None, ''),
        ('POST', '/api/operator/instruments'): ('/api/operator/instruments', {
            'security_id': 'op:m', 'symbol': 'MTX', 'kind': 'stock'}, ''),
        ('POST', '/api/operator/observations'): ('/api/operator/observations', {
            'security_id': 'op:acme', 'value': '1', 'basis': 'price', 'source': SOURCE,
            'observed_at': NOW.isoformat()}, ''),
        ('POST', '/api/operator/starter/instruments'): ('/api/operator/starter/instruments', {}, ''),
        ('POST', '/api/starter/sample-portfolio'): ('/api/starter/sample-portfolio', {}, ''),
        ('POST', '/api/chat'): ('/api/chat', {'message': 'hi'}, ''),
    }


class RouteMatrixTests(unittest.TestCase):
    def boot(self):
        env = make_env(full=True, replies=['{"explanation":"x","tool_calls":[]}'] * 4)
        clients = {}
        for name in ('alice', 'bob', 'admin'):
            clients[name] = env.client()
            clients[name].login(env.tokens[name])
        pid = clients['alice'].request('POST', '/api/portfolios', json={'name': 'a'}).json()['id']
        return env, clients, pid

    def each_route(self, pid=None):
        registry = {(r.method, r.template): r for r in default_routes()}
        table = samples(pid or 'pid')
        for key, route in sorted(registry.items()):
            yield route, table[key]

    def send(self, client, route, sample, **kw):
        path, body, query = sample
        return client.request(route.method, path, json=body, query=query, **kw)

    def test_registry_and_samples_match_exactly(self):
        registry = {(r.method, r.template) for r in default_routes()}
        self.assertEqual(registry, set(samples('x')))
        self.assertEqual(len(registry), len(default_routes()), 'duplicate route')
        for route in default_routes():
            if route.method != 'GET':
                self.assertTrue(route.unsafe, route.template)  # non-GET routes must enforce CSRF
            if route.operator:
                self.assertTrue(route.private, route.template)

    def test_private_routes_reject_missing_forged_expired_and_revoked_sessions(self):
        for route, sample in self.each_route():
            if not route.private:
                continue
            label = f'{route.method} {route.template}'
            env, clients, pid = self.boot()
            sample = samples(pid)[(route.method, route.template)]
            anon = env.client()
            reply = self.send(anon, route, sample)
            self.assertEqual((reply.status, reply.body), (401, UNAUTH), label)
            forged = env.client()
            forged.cookies = {'pa_session': 'f' * 43}
            forged.csrf = 'x' * 43
            self.assertEqual(self.send(forged, route, sample).body, UNAUTH, label)
            env.clock.now += 1801  # idle timeout
            self.assertEqual(self.send(clients['alice'], route, sample).body, UNAUTH, label)
            env, clients, pid = self.boot()
            sample = samples(pid)[(route.method, route.template)]
            credential = env.store.db.execute(
                "SELECT id FROM credentials WHERE user_id='alice'").fetchone()[0]
            env.auth.revoke(credential)
            self.assertEqual(self.send(clients['alice'], route, sample).body, UNAUTH, label)

    def test_unsafe_routes_require_origin_and_a_valid_csrf_token(self):
        env, clients, pid = self.boot()
        for route, sample in self.each_route(pid):
            if not (route.private and route.unsafe):
                continue
            label = f'{route.method} {route.template}'
            client = clients['admin']  # operator, so only CSRF/Origin can be the reason
            for kwargs in ({'csrf': False}, {'headers': {'x-csrf-token': 'wrong'}}, {'origin': False},
                           {'headers': {'origin': 'http://evil.example'}},
                           {'headers': {'sec-fetch-site': 'cross-site'}}):
                reply = self.send(client, route, sample, **kwargs)
                self.assertEqual((reply.status, reply.body), (403, b'{"error":"forbidden"}'), (label, kwargs))

    def test_malformed_and_oversized_bodies(self):
        env, clients, pid = self.boot()
        for route, sample in self.each_route(pid):
            if not (route.private and route.unsafe) or route.template == '/api/session':
                continue
            label = f'{route.method} {route.template}'
            path, _, query = sample
            client = clients['admin']
            for raw, status in ((b'{', 400), (b'[]', 422), (b'"x"', 422),
                                (b'{"a":1.5}', 422), (b'{"a":1,"a":2}', 422),
                                (b'{"a":"' + b'x' * 70000 + b'"}', 413)):
                reply = client.request(route.method, path, body=raw, query=query)
                self.assertEqual(reply.status, status, (label, raw[:12]))
                self.assertNotIn(b'Traceback', reply.body)
            bad_type = client.request(route.method, path, body=b'{}', query=query,
                                      headers={'content-type': 'text/plain'})
            self.assertEqual(bad_type.status, 415, label)

    def test_operator_routes_are_forbidden_to_ordinary_users(self):
        env, clients, pid = self.boot()
        for route, sample in self.each_route(pid):
            if route.operator:
                for name in ('alice', 'bob'):
                    reply = self.send(clients[name], route, sample)
                    self.assertEqual((reply.status, reply.body), (403, b'{"error":"forbidden"}'))

    def test_foreign_and_missing_portfolio_ids_are_indistinguishable(self):
        env, clients, pid = self.boot()
        checked = 0
        for route, sample in self.each_route(pid):
            if '{id}' not in route.template:
                continue
            checked += 1
            label = f'{route.method} {route.template}'
            foreign = self.send(clients['bob'], route, sample)
            absent_sample = (sample[0].replace(pid, 'absent-portfolio'), sample[1], sample[2])
            absent = self.send(clients['bob'], route, absent_sample)
            self.assertEqual((foreign.status, foreign.body), (404, NOT_FOUND), label)
            self.assertEqual((absent.status, absent.body), (404, NOT_FOUND), label)
        self.assertGreaterEqual(checked, 5)

    def test_path_and_id_tampering_never_reaches_a_handler(self):
        env, clients, pid = self.boot()
        tampered = ['..', '../x', '%2e%2e', '%2Fetc', 'a%2Fb', 'a/b', 'a b', 'x' * 300, "x'--", '\x00',
                    '.hidden', '-lead', 'a\\b', 'a;b']
        for route, sample in self.each_route(pid):
            if '{id}' not in route.template:
                continue
            for bad in tampered:
                path = sample[0].replace(pid, bad)
                reply = clients['alice'].request(route.method, path, json=sample[1], query=sample[2])
                self.assertEqual((reply.status, reply.body), (404, NOT_FOUND), (route.template, bad))
        self.assertEqual(len(env.store.db.execute('SELECT * FROM holdings').fetchall()), 0)

    def test_every_response_is_no_store_with_security_headers(self):
        env, clients, pid = self.boot()
        for route, sample in self.each_route(pid):
            if route.template == '/api/session':
                continue
            reply = self.send(clients['admin'], route, sample)
            self.assertEqual(reply.headers['cache-control'], 'no-store', route.template)
            self.assertIn("default-src 'none'", reply.headers['content-security-policy'])
            self.assertEqual(reply.headers['x-content-type-options'], 'nosniff')
            self.assertNotIn('access-control-allow-origin', reply.headers)


if __name__ == '__main__':
    unittest.main()
