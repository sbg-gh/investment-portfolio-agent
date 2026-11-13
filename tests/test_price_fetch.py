"""Press orchestration: read targets, network phase with no store, one ingest. Offline, synthetic."""
from datetime import timedelta
import sqlite3
import unittest
from unittest import mock
from portfolio_agent import price_fetch, price_import, yahoo_chart as yc
from portfolio_agent.core import Store, ValidatedSecurity
from tests.test_yahoo_chart import FRI_AFTER, Fake, Offline, by_symbol, ok
from tests.yahoo_fixtures import chart, ts
class PressBase(Offline):
    def setUp(self):
        super().setUp()
        self.store = Store()
        self.addCleanup(self.store.close)
        for sid, symbol, kind in (('s1', 'SYNA', 'stock'), ('s2', 'SYNB', 'etf'), ('s3', 'SYNC', 'stock'),
                                  ('f1', 'SYNFX', 'mutual_fund')):
            self.store.register(ValidatedSecurity(sid, symbol, kind, 'US', 'synthetic-test'))
        self.events = []
        self.ingest_now = FRI_AFTER
    def count(self, table):
        return self.store.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    def ingest(self, rows, dry_run):
        self.events.append('ingest')
        return price_import.import_rows(self.store, rows, yc.SOURCE, dry_run=dry_run, now=self.ingest_now)
    def run_press(self, transport, *, dry_run=False, symbols=None):
        targets = price_fetch.read_targets(self.store) if symbols is None else symbols
        return price_fetch.run_press(targets, self.ingest, dry_run=dry_run, transport=transport,
                                     clock=lambda: FRI_AFTER, sleeper=lambda s: None,
                                     rng=mock.Mock(random=lambda: 0.0))
    def outcomes(self, result):
        return {s['symbol']: s['outcome'] for s in result['symbols']}
class PressTests(PressBase):
    def test_targets_are_registered_stocks_and_etfs_only(self):
        self.assertEqual(price_fetch.read_targets(self.store), ['SYNA', 'SYNB', 'SYNC'])
    def test_imports_every_completed_bar_with_source_and_provenance(self):
        fake = Fake(default=by_symbol)
        result = self.run_press(fake)
        self.assertEqual(self.outcomes(result), {'SYNA': 'imported', 'SYNB': 'imported', 'SYNC': 'imported'})
        self.assertEqual(result['source'], 'yahoo-chart')
        self.assertEqual(result['counts'], {'imported': 3})
        self.assertEqual(self.count('market_observations'), 6)
        rows = self.store.db.execute('SELECT source,basis,currency,observed_at,value,fetched_at FROM market_observations '
                                     'WHERE security_id=? ORDER BY observed_at', ('s1',)).fetchall()
        self.assertEqual([r[:3] for r in rows], [('yahoo-chart', 'price', 'USD')] * 2)
        self.assertEqual([r[3] for r in rows], ['2026-11-12T21:00:00+00:00', '2026-11-13T21:00:00+00:00'])
        self.assertEqual([r[4] for r in rows], ['1.5', '2.5'])
        self.assertEqual(rows[0][5], FRI_AFTER.isoformat())
        first = result['symbols'][0]
        self.assertEqual((first['latest_date'], first['latest_close'], first['dates']), ('2026-11-13', '2.5000', 2))
    def test_repeat_press_is_unchanged_and_writes_nothing_new(self):
        self.run_press(Fake(default=by_symbol))
        again = self.run_press(Fake(default=by_symbol))
        self.assertEqual(again['counts'], {'unchanged': 3})
        self.assertEqual(self.count('market_observations'), 6)
    def test_changed_value_is_corrected_and_old_row_kept(self):
        self.run_press(Fake(default=by_symbol))
        self.ingest_now = FRI_AFTER + timedelta(hours=1)   # a later fetch time, as in real use
        def changed(url):
            symbol = url.split('/chart/')[1].split('?')[0]
            return ok(chart(symbol, [(ts(2026, 11, 12), 1.5), (ts(2026, 11, 13), 2.75)]))
        result = self.run_press(Fake(default=changed))
        self.assertEqual(result['counts'], {'corrected': 3})
        self.assertEqual(self.count('market_observations'), 9)
    def test_later_press_with_newer_dates_adds_only_new_dates(self):
        self.run_press(Fake(default=lambda url: ok(chart(url.split('/chart/')[1].split('?')[0], [(ts(2026, 11, 12), 1.5)]))))
        result = self.run_press(Fake(default=by_symbol))
        self.assertEqual(result['counts'], {'imported': 3})   # one new date per symbol, one unchanged date
        self.assertEqual(self.count('market_observations'), 6)
    def test_dry_run_fetches_and_validates_but_writes_nothing(self):
        fake = Fake(default=by_symbol)
        result = self.run_press(fake, dry_run=True)
        self.assertEqual((result['dry_run'], result['counts']), (True, {'imported': 3}))
        self.assertEqual(len(fake.calls), 3)
        self.assertEqual(self.count('market_observations'), 0)
    def test_failed_symbols_write_nothing_and_keep_prior_observations(self):
        self.run_press(Fake(default=by_symbol))
        before = self.count('market_observations')
        fake = Fake(ok(b'{}', 400), ok(chart('SYNB', [(ts(2026, 11, 12), 0)])), by_symbol)
        result = self.run_press(fake)
        self.assertEqual(self.outcomes(result), {'SYNA': 'provider_error', 'SYNB': 'invalid_response', 'SYNC': 'unchanged'})
        self.assertEqual(self.count('market_observations'), before)
        self.assertIsNone(result['symbols'][0]['latest_date'])
    def test_blocked_press_reports_stopped_and_skips_ingest_when_nothing_fetched(self):
        result = self.run_press(Fake(ok(b'no', 403, 'text/plain'), by_symbol))
        self.assertEqual(self.outcomes(result), {'SYNA': 'blocked', 'SYNB': 'skipped_stopped', 'SYNC': 'skipped_stopped'})
        self.assertEqual(self.events, [])
        self.assertEqual(self.count('market_observations'), 0)
    def test_ingest_rejections_map_to_fixed_codes(self):
        def ingest(rows, dry_run):
            return {'rows': [{'line': r.line, 'symbol': r.symbol, 'outcome': o}
                             for r, o in zip(rows, ['conflict', 'unknown_symbol', 'unsupported_kind', 'imported', 'weird'] * 3)]}
        fake = Fake(default=lambda url: ok(chart(url.split('/chart/')[1].split('?')[0], [(ts(2026, 11, 13), 2.5)])))
        result = price_fetch.run_press(['SYNA', 'SYNB', 'SYNC'], ingest, dry_run=False, transport=fake,
                                       clock=lambda: FRI_AFTER, sleeper=lambda s: None)
        self.assertEqual(self.outcomes(result), {'SYNA': 'invalid_response', 'SYNB': 'not_found', 'SYNC': 'unsupported_kind'})
    def test_corrected_outranks_imported_outranks_unchanged(self):
        self.assertEqual([price_fetch.merge(a) for a in (['unchanged', 'imported'], ['unchanged', 'corrected', 'imported'],
                                                         ['unchanged'])], ['imported', 'corrected', 'unchanged'])
    def test_result_has_only_fixed_codes_counts_and_known_keys(self):
        result = self.run_press(Fake(ok(b'{"error":"SECRET"}', 400), by_symbol, by_symbol))
        allowed = {'imported', 'unchanged', 'corrected', *yc.FIXED_CODES}
        self.assertTrue({s['outcome'] for s in result['symbols']} <= allowed)
        self.assertEqual(set(result), {'dry_run', 'source', 'counts', 'symbols'})
        self.assertNotIn('SECRET', repr(result))
    def test_network_phase_never_touches_the_store(self):
        # The transport runs with the store's sqlite connection unusable from here: any use would raise.
        def transport(url, headers):
            self.events.append('network')
            with self.assertRaises(Exception):
                self.store.db.execute('SELECT 1')  # connection is closed for the duration below
            return by_symbol(url)
        targets = price_fetch.read_targets(self.store)
        real = self.store.db
        class Closed:
            def __getattr__(self, name):
                raise sqlite3.ProgrammingError('store used during the network phase')
        self.store.db = Closed()
        try:
            def ingest(rows, dry_run):
                self.store.db = real
                return self.ingest(rows, dry_run)
            result = price_fetch.run_press(targets, ingest, dry_run=False, transport=transport,
                                           clock=lambda: FRI_AFTER, sleeper=lambda s: None)
        finally:
            self.store.db = real
        self.assertEqual(self.events, ['network'] * 3 + ['ingest'])
        self.assertEqual(result['counts'], {'imported': 3})
    def test_no_targets_makes_no_request_and_no_ingest(self):
        fake = Fake(default=by_symbol)
        result = self.run_press(fake, symbols=[])
        self.assertEqual((fake.calls, self.events, result['counts'], result['symbols']), ([], [], {}, []))
if __name__ == '__main__':
    unittest.main()
