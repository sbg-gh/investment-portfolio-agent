"""Valuation freshness window (max_age_days): strict validation, default unchanged, no leakage."""
from datetime import timedelta
import json
import unittest
from tests.web_client import NOW, SOURCE, make_env

DAY = timedelta(days=1)


class FreshnessWindowTests(unittest.TestCase):
    def setUp(self):
        self.env = make_env(full=True)
        self.c = {}
        for name in ('alice', 'bob', 'admin'):
            self.c[name] = self.env.client()
            self.c[name].login(self.env.tokens[name])
        self.n = 0

    def setup_holding(self, who, symbol, age, *, kind='stock', observe=True, pid=None):
        if pid is None:
            pid = self.c[who].request('POST', '/api/portfolios', json={'name': 'p'}).json()['id']
        sid = f'op:{symbol.lower()}'
        self.c['admin'].request('POST', '/api/operator/instruments',
                                json={'security_id': sid, 'symbol': symbol, 'kind': kind})
        self.n += 1
        self.c[who].request('POST', f'/api/portfolios/{pid}/holdings', json={
            'symbol': symbol, 'quantity': '3', 'idempotency_key': f'win-key-{self.n:08d}'})
        if observe:
            at = (NOW - age).isoformat()
            self.c['admin'].request('POST', '/api/operator/observations', json={
                'security_id': sid, 'value': '10', 'basis': 'nav' if kind == 'mutual_fund' else 'price',
                'source': SOURCE, 'observed_at': at, 'fetched_at': at})
        return pid

    def value(self, who, pid, window=None, as_of=NOW):
        query = f'source={SOURCE}&as_of={as_of.isoformat().replace("+", "%2B")}'
        if window is not None:
            query += f'&max_age_days={window}'
        return self.c[who].request('GET', f'/api/portfolios/{pid}/valuation', query=query)

    def test_default_is_unchanged_and_window_one_equals_it(self):
        pid = self.setup_holding('alice', 'ACME', timedelta(hours=1))
        plain, one = self.value('alice', pid), self.value('alice', pid, 1)
        self.assertEqual(plain.status, 200)
        a, b = plain.json(), one.json()
        a.pop('snapshot_id'), b.pop('snapshot_id')
        self.assertEqual(a, b)
        self.assertEqual(a['policy'], {'price_max_age_seconds': 86400, 'nav_max_age_seconds': 259200})
        self.assertEqual((a['status'], a['total']), ('complete', '30'))

    def test_three_day_old_price_is_stale_by_default_and_fresh_within_a_three_day_window(self):
        pid = self.setup_holding('alice', 'ACME', 3 * DAY)
        stale = self.value('alice', pid).json()
        self.assertEqual((stale['status'], stale['total']), ('stale', None))
        fresh = self.value('alice', pid, 3).json()
        self.assertEqual((fresh['status'], fresh['total']), ('complete', '30'))
        self.assertEqual(fresh['policy'], {'price_max_age_seconds': 3 * 86400, 'nav_max_age_seconds': 259200})

    def test_boundary_exactly_n_days_is_fresh_and_one_second_more_is_stale(self):
        pid = self.setup_holding('alice', 'ACME', 2 * DAY)
        self.assertEqual(self.value('alice', pid, 2).json()['status'], 'complete')
        later = NOW + timedelta(seconds=1)
        self.assertEqual(self.value('alice', pid, 2, as_of=later).json()['status'], 'stale')

    def test_bounds_and_invalid_values(self):
        pid = self.setup_holding('alice', 'ACME', timedelta(hours=1))
        for ok in ('1', '30', '03'):
            self.assertEqual(self.value('alice', pid, ok).status, 200, ok)
        for bad in ('0', '31', '-1', 'abc', '1.5', '01x', '', '%203', '3%20', '100', '%D9%A3', '1e1'):
            reply = self.value('alice', pid, bad)
            self.assertEqual((reply.status, reply.json()), (422, {'error': 'invalid_field'}), bad)
        reply = self.c['alice'].request('GET', f'/api/portfolios/{pid}/valuation',
                                        query=f'source={SOURCE}&max_age_days=1&max_age_days=2')
        self.assertEqual((reply.status, reply.json()), (422, {'error': 'invalid_query'}))

    def test_one_stale_line_withholds_the_total_even_under_the_window(self):
        pid = self.setup_holding('alice', 'ACME', timedelta(hours=1))
        self.setup_holding('alice', 'OLDY', 4 * DAY, pid=pid)
        report = self.value('alice', pid, 3).json()
        self.assertEqual((report['status'], report['total']), ('stale', None))
        self.assertEqual(self.value('alice', pid, 5).json()['total'], '60')

    def test_missing_line_still_withholds_the_total(self):
        pid = self.setup_holding('alice', 'ACME', timedelta(hours=1))
        self.setup_holding('alice', 'NOPX', DAY, pid=pid, observe=False)
        report = self.value('alice', pid, 30).json()
        self.assertEqual((report['status'], report['total']), ('incomplete', None))

    def test_nav_window_is_never_tightened_below_three_days(self):
        pid = self.setup_holding('alice', 'FUNDX', 4 * DAY, kind='mutual_fund')
        self.assertEqual(self.value('alice', pid, 1).json()['status'], 'stale')
        wide = self.value('alice', pid, 5).json()
        self.assertEqual((wide['status'], wide['policy']['nav_max_age_seconds']), ('complete', 5 * 86400))
        self.assertEqual(self.value('alice', pid, 1).json()['policy']['nav_max_age_seconds'], 3 * 86400)

    def test_saved_snapshot_records_the_window_used(self):
        pid = self.setup_holding('alice', 'ACME', timedelta(hours=1))
        report = self.value('alice', pid, 5).json()
        row = self.env.store.db.execute('SELECT payload FROM valuation_snapshots WHERE id=?',
                                        (report['snapshot_id'],)).fetchone()
        policy = json.loads(row[0])['policy']
        self.assertEqual((policy['price_max_age_seconds'], policy['nav_max_age_seconds']),
                         (5 * 86400, 5 * 86400))

    def test_window_does_not_leak_between_requests_or_users(self):
        pa = self.setup_holding('alice', 'ACME', 3 * DAY)
        pb = self.c['bob'].request('POST', '/api/portfolios', json={'name': 'b'}).json()['id']
        self.setup_holding('bob', 'ACME', 3 * DAY, pid=pb)
        self.assertEqual(self.value('alice', pa, 7).json()['status'], 'complete')
        self.assertEqual(self.value('alice', pa).json()['status'], 'stale')
        self.assertEqual(self.value('bob', pb).json()['status'], 'stale')
        self.assertEqual(self.value('bob', pb, 3).json()['status'], 'complete')
        self.assertEqual(self.value('alice', pa).json()['policy']['price_max_age_seconds'], 86400)


if __name__ == '__main__':
    unittest.main()
