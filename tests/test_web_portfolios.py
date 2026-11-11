"""Portfolio, holdings, receipts, valuation and sources over the request core (stdlib only)."""
import unittest
from tests.web_client import NOW, SOURCE, make_env

QTY_BAD = ['-1', '0x10', '1e5', ' 1', '', 'NaN', '1' * 41, '1.', '.5']


class PortfolioApiTests(unittest.TestCase):
    def setUp(self):
        self.env = make_env(full=True)
        self.alice = self.env.client()
        self.alice.login(self.env.tokens['alice'])
        self.bob = self.env.client()
        self.bob.login(self.env.tokens['bob'])
        self.admin = self.env.client()
        self.admin.login(self.env.tokens['admin'])

    def create(self, client, name='main'):
        reply = client.request('POST', '/api/portfolios', json={'name': name})
        self.assertEqual(reply.status, 201, reply.body)
        return reply.json()['id']

    def register(self, symbol='ACME', kind='stock', sid=None):
        sid = sid or f'op:{symbol.lower()}'
        reply = self.admin.request('POST', '/api/operator/instruments',
                                   json={'security_id': sid, 'symbol': symbol, 'kind': kind})
        self.assertEqual(reply.status, 200, reply.body)
        return sid

    def observe(self, sid, value='10', observed=NOW, basis='price'):
        return self.admin.request('POST', '/api/operator/observations', json={
            'security_id': sid, 'value': value, 'basis': basis, 'source': SOURCE,
            'observed_at': observed.isoformat(), 'fetched_at': observed.isoformat()})

    def hold(self, client, pid, symbol='ACME', qty='3', key='key-0000001'):
        return client.request('POST', f'/api/portfolios/{pid}/holdings',
                              json={'symbol': symbol, 'quantity': qty, 'idempotency_key': key})

    # --- portfolios
    def test_lists_are_per_user_and_identity_in_body_is_ignored(self):
        reply = self.alice.request('POST', '/api/portfolios', json={
            'name': 'a1', 'user_id': 'bob', 'owner': 'bob', 'role': 'operator'})
        pid = reply.json()['id']
        self.assertEqual(self.alice.request('GET', '/api/portfolios').json(),
                         [{'id': pid, 'name': 'a1'}])
        self.assertEqual(self.bob.request('GET', '/api/portfolios').json(), [])

    def test_portfolio_name_validation(self):
        for body in ({}, {'name': ''}, {'name': 5}, {'name': 'x' * 101}, {'name': ['a']}):
            self.assertEqual(self.alice.request('POST', '/api/portfolios', json=body).status, 422, body)
        self.assertEqual(self.alice.request('GET', '/api/portfolios').json(), [])

    # --- holdings
    def test_import_unknown_symbol_needs_registration_then_succeeds(self):
        pid = self.create(self.alice)
        reply = self.hold(self.alice, pid)
        self.assertEqual((reply.status, reply.json()), (422, {'error': 'invalid_request'}))
        self.register()
        reply = self.hold(self.alice, pid)
        self.assertEqual(reply.status, 200, reply.body)
        holdings = self.alice.request('GET', f'/api/portfolios/{pid}/holdings').json()
        self.assertEqual(holdings, [{'security_id': 'op:acme', 'symbol': 'ACME', 'kind': 'stock',
                                     'quantity': '3'}])

    def test_idempotent_import_replay_yields_one_receipt(self):
        pid = self.create(self.alice)
        self.register()
        first = self.hold(self.alice, pid).json()
        second = self.hold(self.alice, pid).json()
        self.assertEqual(first, second)
        receipts = self.alice.request('GET', f'/api/portfolios/{pid}/receipts').json()
        self.assertEqual(len(receipts), 1)
        self.assertEqual(first, {'security_id': 'op:acme'})
        self.assertEqual((receipts[0]['security_id'], receipts[0]['action'], receipts[0]['quantity']),
                         ('op:acme', 'upsert', '3'))

    def test_quantity_must_be_a_decimal_string(self):
        pid = self.create(self.alice)
        self.register()
        for bad in QTY_BAD:
            self.assertEqual(self.hold(self.alice, pid, qty=bad).status, 422, bad)
        for number in (b'{"symbol":"ACME","quantity":3,"idempotency_key":"key-0000001"}',
                       b'{"symbol":"ACME","quantity":3.5,"idempotency_key":"key-0000001"}'):
            reply = self.alice.request('POST', f'/api/portfolios/{pid}/holdings', body=number)
            self.assertEqual((reply.status, reply.json()), (422, {'error': 'invalid_number'}))
        self.assertEqual(self.alice.request('GET', f'/api/portfolios/{pid}/holdings').json(), [])

    def test_idempotency_key_and_symbol_validation(self):
        pid = self.create(self.alice)
        self.register()
        for key in ('short', 'x' * 129, 'has space!!', 5, None):
            reply = self.alice.request('POST', f'/api/portfolios/{pid}/holdings', json={
                'symbol': 'ACME', 'quantity': '1', 'idempotency_key': key})
            self.assertEqual(reply.status, 422, key)
        for symbol in (5, None, '', 'A' * 40):
            self.assertEqual(self.hold(self.alice, pid, symbol=symbol).status, 422, symbol)

    def test_remove_holding_with_receipt_and_replay(self):
        pid = self.create(self.alice)
        self.register()
        self.hold(self.alice, pid)
        path = f'/api/portfolios/{pid}/holdings/op:acme'
        reply = self.alice.request('DELETE', path, json={'idempotency_key': 'remove-0000001'})
        self.assertEqual(reply.status, 200, reply.body)
        again = self.alice.request('DELETE', path, json={'idempotency_key': 'remove-0000001'})
        self.assertEqual(again.json(), reply.json())
        self.assertEqual(self.alice.request('GET', f'/api/portfolios/{pid}/holdings').json(), [])
        actions = [r['action'] for r in self.alice.request('GET', f'/api/portfolios/{pid}/receipts').json()]
        self.assertEqual(actions, ['upsert', 'remove'])
        missing = self.alice.request('DELETE', path, json={'idempotency_key': 'remove-0000002'})
        self.assertEqual(missing.status, 422)

    def test_holdings_and_receipts_of_a_foreign_portfolio_look_nonexistent(self):
        pid = self.create(self.alice)
        self.register()
        self.hold(self.alice, pid)
        for method, suffix, body in (('GET', 'holdings', None), ('GET', 'receipts', None),
                                     ('POST', 'holdings', {'symbol': 'ACME', 'quantity': '1',
                                                          'idempotency_key': 'key-0000009'}),
                                     ('DELETE', 'holdings/op:acme', {'idempotency_key': 'key-0000009'}),
                                     ('GET', 'valuation', None)):
            query = f'source={SOURCE}' if suffix == 'valuation' else ''
            foreign = self.bob.request(method, f'/api/portfolios/{pid}/{suffix}', json=body, query=query)
            absent = self.bob.request(method, f'/api/portfolios/nope-{suffix[:3]}/{suffix}', json=body,
                                      query=query)
            self.assertEqual((foreign.status, foreign.body), (404, b'{"error":"not_found"}'), suffix)
            self.assertEqual((absent.status, absent.body), (foreign.status, foreign.body), suffix)
        self.assertEqual(len(self.alice.request('GET', f'/api/portfolios/{pid}/holdings').json()), 1)

    # --- valuation
    def valuation(self, pid, **params):
        query = '&'.join(f'{k}={v}' for k, v in {'source': SOURCE, 'as_of': NOW.isoformat(),
                                                 **params}.items() if v is not None).replace('+', '%2B')
        return self.alice.request('GET', f'/api/portfolios/{pid}/valuation', query=query)

    def test_total_is_withheld_unless_every_line_is_fresh(self):
        pid = self.create(self.alice)
        sid = self.register()
        self.hold(self.alice, pid, qty='3')
        missing = self.valuation(pid).json()
        self.assertEqual((missing['status'], missing['total'], missing['priced_subtotal']),
                         ('incomplete', None, '0'))
        self.assertEqual(missing['lines'][0]['status'], 'missing')
        self.assertEqual(self.observe(sid, '10').status, 200)
        done = self.valuation(pid).json()
        self.assertEqual((done['status'], done['total'], done['priced_subtotal']), ('complete', '30', '30'))
        self.assertEqual(done['lines'][0]['market_value'], '30')
        stale_at = NOW.replace(day=9)
        stale = self.valuation(pid, as_of=stale_at.isoformat()).json()
        self.assertEqual((stale['status'], stale['total'], stale['priced_subtotal']), ('stale', None, '30'))
        self.assertEqual(stale['lines'][0]['status'], 'stale')
        self.assertIsInstance(stale['policy']['price_max_age_seconds'], int)

    def test_valuation_query_validation(self):
        pid = self.create(self.alice)
        path = f'/api/portfolios/{pid}/valuation'
        bad = ['', 'as_of=2026-11-06T21:00:00', f'source={SOURCE}&as_of=2026-11-06T21:00:00',
               f'source={SOURCE}&as_of=not-a-date', f'source={SOURCE}&source={SOURCE}',
               f'source={SOURCE}&extra=1', 'source=' + 'x' * 200, f'source=&as_of={NOW.isoformat()}']
        for query in bad:
            self.assertEqual(self.alice.request('GET', path, query=query).status, 422, query)

    # --- lookups
    def test_securities_lookup_and_sources(self):
        sid = self.register()
        self.observe(sid)
        reply = self.alice.request('GET', '/api/securities', query=f'ids={sid},op:missing')
        self.assertEqual(reply.json(), [{'security_id': sid, 'symbol': 'ACME', 'kind': 'stock'}])
        self.assertEqual(self.alice.request('GET', '/api/sources').json(), [SOURCE])
        self.assertEqual(self.alice.request('GET', '/api/securities').status, 422)
        self.assertEqual(self.alice.request('GET', '/api/securities', query='ids=' + ',x' * 501).status, 422)


class OperatorApiTests(unittest.TestCase):
    def setUp(self):
        self.env = make_env(full=True)
        self.alice = self.env.client()
        self.alice.login(self.env.tokens['alice'])
        self.admin = self.env.client()
        self.admin.login(self.env.tokens['admin'])

    def instrument(self, **over):
        body = {'security_id': 'op:zeta', 'symbol': 'ZETA', 'kind': 'etf', **over}
        return self.admin.request('POST', '/api/operator/instruments', json=body)

    def test_non_operator_gets_403_and_nothing_changes(self):
        reply = self.alice.request('POST', '/api/operator/instruments', json={
            'security_id': 'op:zeta', 'symbol': 'ZETA', 'kind': 'etf'})
        self.assertEqual((reply.status, reply.json()), (403, {'error': 'forbidden'}))
        self.assertIsNone(self.env.store.db.execute("SELECT 1 FROM securities WHERE id='op:zeta'").fetchone())
        reply = self.alice.request('POST', '/api/operator/observations', json={})
        self.assertEqual(reply.status, 403)

    def test_register_instrument_is_attested_and_validated(self):
        self.assertEqual(self.instrument().status, 200)
        row = self.env.store.db.execute("SELECT source FROM securities WHERE id='op:zeta'").fetchone()
        self.assertEqual(row[0], 'operator-attested')
        for over in ({'kind': 'crypto'}, {'symbol': 'bad symbol!'}, {'security_id': '../x'},
                     {'security_id': 5}, {'symbol': None}):
            self.assertEqual(self.instrument(**over).status, 422, over)
        self.assertEqual(self.instrument(symbol='OTHER').status, 422)  # conflicting identity

    def test_observation_validation(self):
        self.instrument()
        good = {'security_id': 'op:zeta', 'value': '10.5', 'basis': 'price', 'source': SOURCE,
                'observed_at': NOW.isoformat(), 'fetched_at': NOW.isoformat()}

        def post(**over):
            return self.admin.request('POST', '/api/operator/observations', json={**good, **over})
        self.assertEqual(post().status, 200)
        for over in ({'basis': 'nav'}, {'value': '-1'}, {'value': 'abc'}, {'value': 10},
                     {'source': ''}, {'observed_at': '2026-11-06T21:00:00'},
                     {'fetched_at': '2026-11-06T20:00:00+00:00'}, {'security_id': 'op:unknown'},
                     {'basis': 'bid'}):
            self.assertEqual(post(**over).status, 422, over)
        raw = self.admin.request('POST', '/api/operator/observations',
                                 body=b'{"security_id":"op:zeta","value":10.5}')
        self.assertEqual(raw.json(), {'error': 'invalid_number'})


if __name__ == '__main__':
    unittest.main()
