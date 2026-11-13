"""POST /api/operator/prices/fetch and its status route: operator-only background press, offline.
The fake transport stands in for Yahoo; no test touches the network. Synthetic instruments only."""
from datetime import datetime, timezone
import socket
import threading
import time
import unittest
from unittest import mock
from portfolio_agent import yahoo_chart as yc
from portfolio_agent.core import ValidatedSecurity
from tests.web_client import make_env
from tests.yahoo_fixtures import chart, ts
START = '/api/operator/prices/fetch'
FRI_AFTER = datetime(2026, 11, 13, 22, 0, tzinfo=timezone.utc)
SYMBOLS = (('s1', 'SYNA', 'stock'), ('s2', 'SYNB', 'etf'), ('f1', 'SYNFX', 'mutual_fund'))
def good(url):
    symbol = url.split('/chart/')[1].split('?')[0]
    body = chart(symbol, [(ts(2026, 11, 12), 1.5), (ts(2026, 11, 13), 2.5)])
    return yc.HttpResponse(200, 'application/json', body)
class Gate:
    """A transport that blocks until released, so a fetch can be held in flight."""
    def __init__(self):
        self.entered, self.release, self.calls = threading.Event(), threading.Event(), []
    def __call__(self, url, headers):
        self.calls.append(url)
        self.entered.set()
        self.release.wait(10)
        return good(url)
class FetchWebBase(unittest.TestCase):
    transport = staticmethod(lambda url, headers: good(url))
    def setUp(self):
        patcher = mock.patch.object(socket, 'socket', side_effect=AssertionError('network attempted'))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.options = {'transport': self.transport, 'clock': lambda: FRI_AFTER, 'sleeper': lambda s: None}
        self.env = make_env(full=True, worker=True, fetch=self.options)
        self.addCleanup(self.env.executor.close)
        self.addCleanup(self.env.fetch.close)
        self.c = {}
        for name in ('alice', 'admin'):
            self.c[name] = self.env.client()
            self.c[name].login(self.env.tokens[name])
        def seed(b):
            for sid, symbol, kind in SYMBOLS:
                b.store.register(ValidatedSecurity(sid, symbol, kind, 'US', 'synthetic-test'))
        self.env.executor.submit(seed)
    def read(self, sql, *args):
        return self.env.executor.submit(lambda b: b.store.db.execute(sql, args).fetchall())
    def count(self, table):
        return self.read(f'SELECT COUNT(*) FROM {table}')[0][0]
    def start(self, who='admin', body=None, **kw):
        return self.c[who].request('POST', START, json={'dry_run': False} if body is None else body, **kw)
    def status(self, fetch_id, who='admin'):
        return self.c[who].request('GET', f'{START}/{fetch_id}')
    def wait_done(self, fetch_id, who='admin', limit=10):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            reply = self.status(fetch_id, who)
            if reply.json()['state'] != 'running':
                return reply.json()
            time.sleep(0.02)
        self.fail('fetch did not finish')
class FetchRouteTests(FetchWebBase):
    def test_unauthenticated_is_401_and_non_operator_is_403(self):
        self.assertEqual(self.env.client().request('POST', START, json={'dry_run': False}).status, 401)
        self.assertEqual(self.env.client().request('GET', f'{START}/abc').status, 401)
        reply = self.start('alice')
        self.assertEqual((reply.status, reply.body), (403, b'{"error":"forbidden"}'))
        self.assertEqual(self.c['alice'].request('GET', f'{START}/abc').status, 403)
        self.assertEqual(self.count('market_observations'), 0)
    def test_csrf_and_origin_are_required(self):
        for kw in ({'csrf': False}, {'origin': False}):
            self.assertEqual(self.start(**kw).status, 403)
        self.assertEqual(self.env.fetch.snapshot_for('admin'), None)
    def test_malformed_json_and_wrong_types(self):
        c = self.c['admin']
        self.assertEqual(c.request('POST', START, body=b'{nope').status, 400)
        self.assertEqual(c.request('POST', START, body=b'[]').status, 422)
        for body in ({}, {'dry_run': 'true'}, {'dry_run': 1}, {'dry_run': None}, {'dry_run': []}):
            self.assertEqual(c.request('POST', START, json=body).status, 422, body)
        self.assertEqual(self.count('market_observations'), 0)
    def test_body_user_id_and_extra_fields_are_not_identity(self):
        reply = self.start(body={'dry_run': True, 'user_id': 'alice', 'symbols': ['EVIL']})
        self.assertEqual(reply.status, 202)
        self.wait_done(reply.json()['fetch_id'])
    def test_press_runs_in_background_and_imports(self):
        reply = self.start()
        self.assertEqual(reply.status, 202)
        self.assertEqual(set(reply.json()), {'fetch_id'})
        done = self.wait_done(reply.json()['fetch_id'])
        self.assertEqual(done['state'], 'done')
        self.assertEqual(done['result']['counts'], {'imported': 2})
        self.assertEqual({s['symbol']: s['outcome'] for s in done['result']['symbols']},
                         {'SYNA': 'imported', 'SYNB': 'imported'})
        self.assertEqual(done['progress'], {'done': 2, 'planned': 2})
        self.assertEqual(self.count('market_observations'), 4)
        self.assertEqual(self.c['alice'].request('GET', '/api/sources').json(), ['yahoo-chart'])
        again = self.wait_done(self.start().json()['fetch_id'])
        self.assertEqual(again['result']['counts'], {'unchanged': 2})
    def test_dry_run_writes_nothing_but_reports(self):
        done = self.wait_done(self.start(body={'dry_run': True}).json()['fetch_id'])
        self.assertEqual((done['dry_run'], done['result']['counts']), (True, {'imported': 2}))
        self.assertEqual(self.count('market_observations'), 0)
    def test_status_is_json_with_decimal_strings_and_no_floats(self):
        done = self.wait_done(self.start().json()['fetch_id'])
        row = done['result']['symbols'][0]
        self.assertEqual((row['latest_date'], row['latest_close']), ('2026-11-13', '2.5000'))
    def test_logs_and_traces_carry_no_symbols_or_prices(self):
        self.wait_done(self.start().json()['fetch_id'])
        text = ' '.join(self.env.logs)
        traces = repr(self.read('SELECT operation,outcome FROM execution_traces'))
        for needle in ('SYNA', 'SYNB', '2.5', 'yahoo'):
            self.assertNotIn(needle, text)
            self.assertNotIn(needle, traces)
        self.assertIn('import_rows', traces)
    def test_blocked_press_is_a_finished_result_not_an_http_error(self):
        env = make_env(full=True, worker=True, fetch={**self.options, 'transport':
                       lambda url, headers: yc.HttpResponse(403, 'text/plain', b'no')})
        self.addCleanup(env.executor.close)
        self.addCleanup(env.fetch.close)
        env.executor.submit(lambda b: b.store.register(ValidatedSecurity('s1', 'SYNA', 'stock', 'US', 't')))
        admin = env.client()
        admin.login(env.tokens['admin'])
        reply = admin.request('POST', START, json={'dry_run': False})
        self.assertEqual(reply.status, 202)
        end = time.monotonic() + 10
        while time.monotonic() < end:
            body = admin.request('GET', f"{START}/{reply.json()['fetch_id']}").json()
            if body['state'] != 'running':
                break
            time.sleep(0.02)
        self.assertEqual((body['state'], body['result']['counts']), ('done', {'blocked': 1}))
    def test_unknown_lost_and_foreign_ids_report_unknown_uniformly(self):
        started = self.start().json()['fetch_id']
        self.wait_done(started)
        def make_operator(b):
            b.store.create_user('op2')
            return b.auth.provision('op2', role='operator', ttl_seconds=1000)
        token = self.env.executor.submit(make_operator)
        other = self.env.client()
        other.login(token)
        mine = self.status(started).json()
        theirs = other.request('GET', f'{START}/{started}')
        absent = self.status('0' * 32)
        for reply in (theirs, absent):
            self.assertEqual((reply.status, reply.json()), (200, {'state': 'unknown'}))
        self.assertEqual(mine['state'], 'done')
        for bad in ('..', 'a b', 'x' * 300, '%2e%2e', '-lead'):
            self.assertEqual(self.status(bad).status, 404, bad)
    def test_only_the_latest_record_is_kept(self):
        first = self.start().json()['fetch_id']
        self.wait_done(first)
        second = self.start().json()['fetch_id']
        self.wait_done(second)
        self.assertEqual(self.status(first).json(), {'state': 'unknown'})
        self.assertEqual(self.status(second).json()['state'], 'done')
class InFlightTests(FetchWebBase):
    def setUp(self):
        self.gate = self.transport = Gate()
        super().setUp()
        self.addCleanup(self.gate.release.set)
    def test_second_press_is_refused_while_one_runs_and_allowed_after(self):
        first = self.start()
        self.assertTrue(self.gate.entered.wait(5))
        second = self.start()
        self.assertEqual((second.status, second.json()), (409, {'error': 'busy'}))
        self.assertEqual(self.status(first.json()['fetch_id']).json()['state'], 'running')
        self.gate.release.set()
        self.wait_done(first.json()['fetch_id'])
        self.assertEqual(self.start(body={'dry_run': True}).status, 202)
    def test_dry_run_counts_as_a_press_for_the_busy_rule(self):
        self.assertEqual(self.start(body={'dry_run': True}).status, 202)
        self.assertEqual(self.start().status, 409)
    def test_other_requests_including_logout_are_served_while_in_flight(self):
        self.start()
        self.assertTrue(self.gate.entered.wait(5))
        started = time.monotonic()
        portfolios = self.c['alice'].request('GET', '/api/portfolios')
        sources = self.c['alice'].request('GET', '/api/sources')
        pid = self.c['alice'].request('POST', '/api/portfolios', json={'name': 'p'}).json()['id']
        valuation = self.c['alice'].request('GET', f'/api/portfolios/{pid}/valuation', query='source=yahoo-chart')
        logout = self.c['admin'].request('DELETE', '/api/session')
        self.assertEqual([portfolios.status, sources.status, valuation.status, logout.status], [200, 200, 200, 200])
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(self.gate.release.is_set(), False)   # the fetch is still blocked in the network phase
    def test_credential_revoked_mid_fetch_writes_nothing(self):
        reply = self.start()
        self.assertTrue(self.gate.entered.wait(5))
        self.env.executor.submit(lambda b: b.auth.revoke(
            b.store.db.execute("SELECT id FROM credentials WHERE user_id='admin'").fetchone()[0]))
        self.gate.release.set()
        state = self.wait_snapshot()
        self.assertEqual((state['state'], state['error']), ('failed', 'forbidden'))
        self.assertEqual(self.count('market_observations'), 0)
    def test_operator_role_removed_mid_fetch_writes_nothing(self):
        self.start()
        self.assertTrue(self.gate.entered.wait(5))
        self.env.executor.submit(lambda b: b.store.db.execute(
            "UPDATE credentials SET role='user' WHERE user_id='admin'") and b.store.db.commit())
        self.gate.release.set()
        state = self.wait_snapshot()
        self.assertEqual((state['state'], state['error']), ('failed', 'forbidden'))
        self.assertEqual(self.count('market_observations'), 0)
    def wait_snapshot(self):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            snap = self.env.fetch.snapshot_for('admin')
            if snap and snap['state'] != 'running':
                return snap
            time.sleep(0.02)
        self.fail('fetch did not finish')
    def test_shutdown_during_the_network_phase_is_clean_and_writes_nothing(self):
        self.start()
        self.assertTrue(self.gate.entered.wait(5))
        started = time.monotonic()
        self.env.fetch.close(join_timeout=0.2)
        self.assertLess(time.monotonic() - started, 2)
        self.gate.release.set()
        end = time.monotonic() + 5
        while self.env.fetch.thread_alive() and time.monotonic() < end:
            time.sleep(0.02)
        self.assertFalse(self.env.fetch.thread_alive())
        self.assertEqual(self.count('market_observations'), 0)
        self.assertEqual(self.env.fetch.snapshot_for('admin')['error'], 'interrupted')
        self.assertEqual(self.start().status, 503)   # closed manager accepts no new press
class InterruptibleSleepTests(unittest.TestCase):
    def test_close_interrupts_the_spacing_sleep_promptly(self):
        env = make_env(full=True, worker=True, fetch={'transport': lambda url, headers: good(url),
                                                      'clock': lambda: FRI_AFTER})
        self.addCleanup(env.executor.close)
        env.executor.submit(lambda b: [b.store.register(ValidatedSecurity(i, s, k, 'US', 't')) for i, s, k in SYMBOLS])
        admin = env.client()
        admin.login(env.tokens['admin'])
        self.assertEqual(admin.request('POST', START, json={'dry_run': False}).status, 202)
        time.sleep(0.2)   # first request done; the manager now sleeps the 1.5 s spacing
        started = time.monotonic()
        env.fetch.close()
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(env.fetch.thread_alive())
        self.assertEqual(env.executor.submit(lambda b: b.store.db.execute(
            'SELECT COUNT(*) FROM market_observations').fetchone()[0]), 0)
class ServerAssemblyTests(unittest.TestCase):
    def test_closing_the_assembled_app_during_a_fetch_is_clean(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        from portfolio_agent.auth import Authenticator
        from portfolio_agent.core import Store
        from portfolio_agent.web.server import build_asgi
        from tests.web_client import Client
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        db = str(Path(tmp.name) / 'p.db')
        store = Store(db)
        store.create_user('admin')
        token = Authenticator(store).provision('admin', role='operator', ttl_seconds=1000)
        with store.transaction():
            store.register(ValidatedSecurity('s1', 'SYNA', 'stock', 'US', 'synthetic-test'))
        store.close()
        gate = Gate()
        asgi = build_asgi(db, port=8765, model=None, valuation_source='x', static_dir=Path(tmp.name),
                          log=lambda line: None, fetch_options={'transport': gate, 'clock': lambda: FRI_AFTER,
                                         'join_timeout': 0.3})
        client = Client(asgi.app)
        client.login(token)
        self.assertEqual(client.request('POST', START, json={'dry_run': False}).status, 202)
        self.assertTrue(gate.entered.wait(5), asgi.app.fetch.snapshot_for('admin'))
        started = time.monotonic()
        asgi.close()                      # server shutdown while the press is in the network phase
        self.assertLess(time.monotonic() - started, 3)
        gate.release.set()
        time.sleep(0.3)
        con = sqlite3.connect(db)
        self.addCleanup(con.close)
        self.assertEqual(con.execute('SELECT COUNT(*) FROM market_observations').fetchone()[0], 0)
if __name__ == '__main__':
    unittest.main()
