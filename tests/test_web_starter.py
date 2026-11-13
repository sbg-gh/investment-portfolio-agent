"""HTTP routes for starter data: operator-only instruments, session-identity sample portfolio."""
import unittest
from web_client import SOURCE, make_env
INSTRUMENTS = '/api/operator/starter/instruments'
SAMPLE = '/api/starter/sample-portfolio'
class StarterWebTests(unittest.TestCase):
    def setUp(self):
        self.env = make_env(full=True)
        self.c = {}
        for name in ('alice', 'bob', 'admin'):
            self.c[name] = self.env.client()
            self.c[name].login(self.env.tokens[name])
    def count(self, table):
        return self.env.store.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    def test_unauthenticated_is_401(self):
        anon = self.env.client()
        for path in (INSTRUMENTS, SAMPLE):
            self.assertEqual(anon.request('POST', path, json={}).status, 401)
    def test_instruments_forbidden_for_non_operator_and_nothing_registered(self):
        self.assertEqual(self.c['alice'].request('POST', INSTRUMENTS, json={}).status, 403)
        self.assertEqual(self.count('securities'), 0)
    def test_csrf_and_origin_required_on_both(self):
        for who, path in (('admin', INSTRUMENTS), ('alice', SAMPLE)):
            self.assertEqual(self.c[who].request('POST', path, json={}, csrf=False).status, 403)
            self.assertEqual(self.c[who].request('POST', path, json={}, origin=False).status, 403)
        self.assertEqual(self.count('securities'), 0)
    def test_operator_registers_twenty_and_retry_is_idempotent(self):
        reply = self.c['admin'].request('POST', INSTRUMENTS, json={})
        self.assertEqual((reply.status, reply.json()), (200, {'registered': 20, 'already_present': 0, 'symbols': 20}))
        again = self.c['admin'].request('POST', INSTRUMENTS, json={})
        self.assertEqual((again.status, again.json()['registered']), (200, 0))
        self.assertEqual(self.count('securities'), 20)
        self.assertEqual(self.count('market_observations'), 0)
    def test_sample_needs_instruments_returns_409_and_creates_nothing(self):
        reply = self.c['alice'].request('POST', SAMPLE, json={})
        self.assertEqual((reply.status, reply.json()), (409, {'error': 'starter_instruments_missing'}))
        self.assertEqual(self.count('portfolios'), 0)
        self.assertEqual(self.count('securities'), 0)
    def test_sample_created_for_session_user_only_and_retry_is_idempotent(self):
        self.c['admin'].request('POST', INSTRUMENTS, json={})
        # A body user_id is ignored: identity comes from the session.
        reply = self.c['alice'].request('POST', SAMPLE, json={'user_id': 'bob'})
        self.assertEqual(reply.status, 201)
        body = reply.json()
        self.assertEqual((body['name'], body['created']), ('Sample portfolio (synthetic)', True))
        again = self.c['alice'].request('POST', SAMPLE, json={})
        self.assertEqual((again.status, again.json()['id'], again.json()['created']),
                         (200, body['id'], False))
        self.assertEqual(self.count('portfolios'), 1)
        self.assertEqual(self.count('import_receipts'), 20)
        mine = self.c['alice'].request('GET', '/api/portfolios').json()
        self.assertEqual([p['id'] for p in mine], [body['id']])
        self.assertEqual(self.c['bob'].request('GET', '/api/portfolios').json(), [])
        self.assertEqual(self.c['bob'].request('GET', f"/api/portfolios/{body['id']}/holdings").status, 404)
        holdings = self.c['alice'].request('GET', f"/api/portfolios/{body['id']}/holdings").json()
        self.assertEqual(len(holdings), 20)
        receipts = self.c['alice'].request('GET', f"/api/portfolios/{body['id']}/receipts").json()
        self.assertEqual({r['source'] for r in receipts}, {'starter-sample-synthetic'})
    def test_single_operator_credential_can_do_both_steps_and_valuation_has_no_total(self):
        self.assertEqual(self.c['admin'].request('POST', SAMPLE, json={}).status, 409)
        self.assertEqual(self.c['admin'].request('POST', INSTRUMENTS, json={}).status, 200)
        pid = self.c['admin'].request('POST', SAMPLE, json={}).json()['id']
        report = self.c['admin'].request('GET', f'/api/portfolios/{pid}/valuation',
                                         query=f'source={SOURCE}').json()
        self.assertIsNone(report['total'])
        self.assertEqual({x['status'] for x in report['lines']}, {'missing'})
        self.assertEqual(self.count('market_observations'), 0)
    def test_access_log_has_no_symbols_ids_or_bodies(self):
        self.c['admin'].request('POST', INSTRUMENTS, json={})
        self.c['alice'].request('POST', SAMPLE, json={'user_id': 'bob'})
        joined = '\n'.join(str(line) for line in self.env.logs)
        for needle in ('AMD', 'op:amd', 'bob', 'Sample portfolio'):
            self.assertNotIn(needle, joined)
if __name__ == '__main__':
    unittest.main()
