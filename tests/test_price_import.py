"""Operator CSV price import (core). All fixtures are SYNTHETIC: tickers and prices are made up
in code for tests, never packaged, never real market data."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import unittest
from unittest import mock
from zoneinfo import ZoneInfoNotFoundError

from portfolio_agent import price_import, valuation
from portfolio_agent.core import Store, ValidatedSecurity
from portfolio_agent.price_import import FileError, import_prices, parse_csv

NOW = datetime(2026, 11, 6, 21, tzinfo=timezone.utc)   # Friday 16:00 EST
LATER = NOW + timedelta(days=1)
HEAD = 'symbol,date,close\n'


def make_store():
    store = Store()
    for sid, symbol, kind in (('s1', 'SYNA', 'stock'), ('s2', 'SYNB', 'etf'), ('s3', 'SYNC', 'stock'),
                              ('f1', 'SYNFX', 'mutual_fund')):
        store.register(ValidatedSecurity(sid, symbol, kind, 'US', 'synthetic-test'))
    return store


class Base(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        self.addCleanup(self.store.close)

    def count(self, table):
        return self.store.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def run_import(self, text, *, source='synthetic-file', dry_run=False, now=NOW):
        return import_prices(self.store, text, source, dry_run=dry_run, now=now)

    def outcomes(self, text, **kw):
        return [row['outcome'] for row in self.run_import(text, **kw)['rows']]

    def one(self, line, **kw):
        return self.outcomes(HEAD + line + '\n', **kw)[0]

    def error(self, data, source='synthetic-file'):
        with self.assertRaises(FileError) as caught:
            import_prices(self.store, data, source, dry_run=True, now=NOW)
        return caught.exception.code

    def observations(self):
        return self.store.db.execute('SELECT security_id,value,currency,basis,source,observed_at,fetched_at '
                                     'FROM market_observations ORDER BY rowid').fetchall()


class FileLevelTests(Base):
    def test_empty_and_header_only_files(self):
        self.assertEqual(self.error(''), 'empty_file')
        self.assertEqual(self.error(b''), 'empty_file')
        self.assertEqual(self.error(HEAD), 'empty_file')
        self.assertEqual(self.error(HEAD + '\n\n'), 'empty_file')

    def test_missing_each_required_column(self):
        for header in ('date,close', 'symbol,close', 'symbol,date', 'price,date,symbol'):
            self.assertEqual(self.error(header + '\nA,2026-11-06,1\n'), 'invalid_header', header)

    def test_duplicate_required_column(self):
        self.assertEqual(self.error('symbol,date,close,Close\nSYNA,2026-11-06,1,1\n'), 'invalid_header')

    def test_header_case_whitespace_and_order(self):
        text = ' CLOSE , Symbol ,DATE\n1.5,SYNA,2026-11-06\n'
        self.assertEqual(self.outcomes(text), ['imported'])

    def test_extra_columns_are_ignored_listed_and_never_stored(self):
        text = 'symbol,date,close,Volume,note\u00e9,' + 'x' * 40 + '\nSYNA,2026-11-06,1.5,999,hello,zzz\n'
        result = self.run_import(text)
        self.assertEqual(result['ignored_columns'], ['volume', 'note?', 'x' * 32])
        rows = self.observations()
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 7)
        self.assertNotIn('999', json.dumps(rows))
        self.assertNotIn('hello', json.dumps(result))

    def test_ignored_columns_are_capped_at_twenty_names(self):
        header = 'symbol,date,close,' + ','.join(f'c{i}' for i in range(30))
        self.assertEqual(len(import_prices(self.store, header + '\nSYNA,2026-11-06,1\n', 's',
                                           dry_run=True, now=NOW)['ignored_columns']), 20)

    def test_bom_crlf_quotes_and_whitespace(self):
        text = '\ufeffsymbol,date,close\r\n" syna ", 2026-11-06 ,"1.25"\r\n'
        self.assertEqual(self.outcomes(text), ['imported'])
        self.assertEqual(self.outcomes(text.encode('utf-8'), dry_run=True), ['unchanged'])

    def test_invalid_encoding(self):
        self.assertEqual(self.error(HEAD.encode() + 'SYN\u00e9,2026-11-06,1\n'.encode('latin-1')),
                         'invalid_encoding')

    def test_invalid_csv(self):
        self.assertEqual(self.error('symbol,date,close\nA"b,2026-11-06,1\n"unclosed,2026-11-06,1\n'),
                         'invalid_csv')
        self.assertEqual(self.error('symbol,date,close\nSYNA\x00,2026-11-06,1\n'), 'invalid_csv')

    def test_size_cap_boundary(self):
        rows = ['SYNA,2026-11-06,1,' + 'x' * 1000 for _ in range(1000)]
        text = 'symbol,date,close,pad\n' + '\n'.join(rows) + '\n'
        pad = price_import.MAX_BYTES - len(text.encode())
        self.assertGreater(pad, 0)
        text = text[:-1] + 'y' * (pad + 1) + '\n'
        self.assertEqual(len(text.encode()), price_import.MAX_BYTES + 1)
        self.assertEqual(self.error(text), 'file_too_large')
        self.assertEqual(self.error(text.encode()), 'file_too_large')
        ok = text[:-2] + '\n'
        self.assertEqual(len(ok.encode()), price_import.MAX_BYTES)
        self.assertEqual(len(import_prices(self.store, ok, 's', dry_run=True, now=NOW)['rows']), 1000)

    def test_row_cap_boundary(self):
        body = lambda n: HEAD + 'SYNA,2026-11-06,1\n' * n
        self.assertEqual(import_prices(self.store, body(10000), 's', dry_run=True, now=NOW)['total_rows'], 10000)
        self.assertEqual(self.error(body(10001)), 'too_many_rows')

    def test_source_validation(self):
        good = HEAD + 'SYNA,2026-11-06,1\n'
        for source in ('', 'x' * 65, 'has space', 'tab\tx', 'new\nline', '-dash', '.dot', 'a/b', 'caf\u00e9', 5, None):
            self.assertEqual(self.error(good, source=source), 'invalid_source', repr(source))
        self.assertEqual(import_prices(self.store, good, 'a' + 'b' * 63, dry_run=True, now=NOW)['total_rows'], 1)
        self.assertEqual(import_prices(self.store, good, 'My_src-1.2', dry_run=True, now=NOW)['total_rows'], 1)


class RowCodeTests(Base):
    def test_invalid_format_closes(self):
        for close in ('1,000.50', '$1', '1e3', 'NaN', 'inf', '-1', '+1', '', '1.', '.5', '1 2', '1' * 31):
            self.assertEqual(self.one(f'SYNA,2026-11-06,"{close}"'), 'invalid_format', close)

    def test_invalid_format_symbols_and_missing_cells(self):
        for line in ('SY NA,2026-11-06,1', ',2026-11-06,1', 'TOOLONGSYMBOL12345,2026-11-06,1', 'SYNA'):
            self.assertEqual(self.one(line), 'invalid_format', line)

    def test_invalid_value(self):
        for close in ('0', '0.0', '1.0000000000001', '1' * 29, '1' + '0' * 19):
            self.assertEqual(self.one(f'SYNA,2026-11-06,{close}'), 'invalid_value', close)
        self.assertEqual(self.one('SYNA,2026-11-06,1.000000000001'), 'imported')

    def test_invalid_date(self):
        for date_text in ('2026-02-30', '26-01-02', '2026/01/02', '', '2026-1-2', '2026-11-06T00:00'):
            self.assertEqual(self.one(f'SYNA,{date_text},1'), 'invalid_date', date_text)

    def test_future_date_uses_the_injected_clock(self):
        self.assertEqual(self.one('SYNA,2026-11-07,1'), 'future_date')
        before_close = NOW - timedelta(seconds=1)
        self.assertEqual(self.one('SYNA,2026-11-06,1', now=before_close), 'future_date')
        self.assertEqual(self.one('SYNA,2026-11-06,1', now=NOW), 'imported')

    def test_duplicates_reject_every_occurrence_even_with_symbol_case_differences(self):
        text = HEAD + 'SYNA,2026-11-05,1\nsyna,2026-11-05,2\nSYNB,2026-11-05,3\n SYNA ,2026-11-05,1\n'
        result = self.run_import(text)
        self.assertEqual([r['outcome'] for r in result['rows']],
                         ['duplicate_row', 'duplicate_row', 'imported', 'duplicate_row'])
        self.assertEqual(self.count('market_observations'), 1)

    def test_unknown_symbol_is_never_registered(self):
        self.assertEqual(self.one('NOPE,2026-11-06,1'), 'unknown_symbol')
        self.assertEqual(self.count('securities'), 4)
        self.assertEqual(self.count('market_observations'), 0)

    def test_mutual_funds_are_unsupported(self):
        self.assertEqual(self.one('SYNFX,2026-11-06,1'), 'unsupported_kind')
        self.assertEqual(self.count('market_observations'), 0)

    def test_precedence(self):
        self.assertEqual(self.one('NOPE,2026-02-30,$1'), 'invalid_format')
        self.assertEqual(self.one('NOPE,2026-02-30,0'), 'invalid_date')
        self.assertEqual(self.one('NOPE,2026-11-07,0'), 'future_date')
        self.assertEqual(self.one('NOPE,2026-11-06,0'), 'invalid_value')
        self.assertEqual(self.one('SYNFX,2026-11-06,1'), 'unsupported_kind')
        text = HEAD + 'NOPE,2026-11-06,$1\nNOPE,2026-11-06,$1\nNOPE,2026-11-06,1\nNOPE,2026-11-06,1\n'
        self.assertEqual(self.outcomes(text), ['invalid_format', 'invalid_format', 'duplicate_row', 'duplicate_row'])

    def test_line_numbers_symbols_and_blank_lines(self):
        text = 'symbol,date,close\n\nSYNA,2026-11-06,1\n"SY\nNA",2026-11-06,1\nsynb,2026-11-06,2\n'
        rows = self.run_import(text, dry_run=True)['rows']
        self.assertEqual([(r['line'], r['symbol']) for r in rows], [(3, 'SYNA'), (4, None), (6, 'SYNB')])
        self.assertEqual(set(rows[0]), {'line', 'symbol', 'outcome'})


class ObservedAtTests(Base):
    def observed(self, day):
        self.run_import(HEAD + f'SYNA,{day},1\n')
        return self.observations()[-1][5]

    def test_close_is_16_00_new_york_in_utc_across_dst(self):
        cases = {'2026-03-06': '21:00', '2026-03-09': '20:00', '2026-03-08': '20:00',
                 '2026-11-01': '21:00', '2026-10-30': '20:00'}
        for day, hhmm in cases.items():
            self.assertEqual(self.observed(day), f'{day}T{hhmm}:00+00:00', day)

    def test_weekend_dates_are_accepted_as_given(self):
        self.assertEqual(self.one('SYNA,2026-10-31,1'), 'imported')

    def test_close_to_utc_function(self):
        self.assertEqual(price_import.close_to_utc(date(2026, 3, 9)), datetime(2026, 3, 9, 20, tzinfo=timezone.utc))

    def test_missing_timezone_data_is_a_clear_file_error(self):
        with mock.patch.object(price_import, 'ZoneInfo', side_effect=ZoneInfoNotFoundError('x')):
            self.assertEqual(self.error(HEAD + 'SYNA,2026-11-06,1\n'), 'timezone_unavailable')


class ImportBehaviourTests(Base):
    def counts(self):
        return {t: self.count(t) for t in ('market_observations', 'securities', 'holdings', 'valuation_snapshots')}

    def test_dry_run_writes_nothing(self):
        before = self.counts()
        result = self.run_import(HEAD + 'SYNA,2026-11-06,1.5\nNOPE,2026-11-06,2\n', dry_run=True)
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['counts'], {'imported': 1, 'unknown_symbol': 1})
        self.assertEqual(self.counts(), before)

    def test_import_writes_observation_with_provenance(self):
        result = self.run_import(HEAD + 'syna,2026-11-06,0.10\nSYNB,2026-11-05,123.456789012\n')
        self.assertEqual((result['source'], result['total_rows'], result['counts']),
                         ('synthetic-file', 2, {'imported': 2}))
        self.assertEqual(self.observations(), [
            ('s1', '0.1', 'USD', 'price', 'synthetic-file', '2026-11-06T21:00:00+00:00', NOW.isoformat()),
            ('s2', '123.456789012', 'USD', 'price', 'synthetic-file', '2026-11-05T21:00:00+00:00',
             NOW.isoformat())])

    def test_fetched_at_is_the_injected_clock_in_utc(self):
        self.run_import(HEAD + 'SYNA,2026-11-05,1\n', now=NOW.astimezone(timezone(timedelta(hours=5))))
        self.assertEqual(self.observations()[0][6], NOW.isoformat())

    def test_rerun_is_idempotent(self):
        text = HEAD + 'SYNA,2026-11-05,1\nSYNB,2026-11-05,2.50\n'
        self.run_import(text)
        result = self.run_import(text, now=LATER)
        self.assertEqual(result['counts'], {'unchanged': 2})
        self.assertEqual(self.count('market_observations'), 2)
        self.assertEqual(self.run_import(HEAD + 'SYNB,2026-11-05,2.5000\n', now=LATER)['counts'], {'unchanged': 1})

    def test_correction_keeps_old_row_and_valuation_respects_cutoff(self):
        self.run_import(HEAD + 'SYNA,2026-11-05,1\n')
        result = self.run_import(HEAD + 'SYNA,2026-11-05,2\n', now=LATER)
        self.assertEqual(result['counts'], {'corrected': 1})
        self.assertEqual([r[1] for r in self.observations()], ['1', '2'])
        before = valuation.select_observation(self.store.db, 's1', 'synthetic-file', 'price', LATER - timedelta(seconds=1))
        after = valuation.select_observation(self.store.db, 's1', 'synthetic-file', 'price', LATER)
        self.assertEqual((before[1], after[1]), ('1', '2'))
        self.assertEqual(self.run_import(HEAD + 'SYNA,2026-11-05,2\n', now=LATER + timedelta(days=1))['counts'],
                         {'unchanged': 1})
        back = self.run_import(HEAD + 'SYNA,2026-11-05,1\n', now=LATER + timedelta(days=2))
        self.assertEqual(back['counts'], {'corrected': 1})
        self.assertEqual(self.count('market_observations'), 3)

    def test_same_clock_conflict_is_reported_and_not_written(self):
        self.store.db.execute('INSERT INTO market_observations VALUES (?,?,?,?,?,?,?,?)',
                              ('pre', 's1', '9', 'USD', 'price', 'synthetic-file',
                               '2026-11-05T21:00:00+00:00', NOW.isoformat()))
        result = self.run_import(HEAD + 'SYNA,2026-11-05,1\nSYNB,2026-11-05,2\n')
        self.assertEqual([r['outcome'] for r in result['rows']], ['conflict', 'imported'])
        self.assertEqual(result['counts'], {'conflict': 1, 'imported': 1})
        self.assertEqual(self.count('market_observations'), 2)

    def test_other_sources_are_independent(self):
        self.run_import(HEAD + 'SYNA,2026-11-05,1\n', source='one')
        self.assertEqual(self.run_import(HEAD + 'SYNA,2026-11-05,2\n', source='two', now=LATER)['counts'],
                         {'imported': 1})

    def test_valid_rows_import_and_rejected_rows_are_reported(self):
        text = HEAD + 'SYNA,2026-11-05,1\nNOPE,2026-11-05,1\nSYNB,2026-11-05,abc\nSYNC,2026-11-05,3\n'
        result = self.run_import(text)
        self.assertEqual(result['counts'], {'imported': 2, 'unknown_symbol': 1, 'invalid_format': 1})
        self.assertEqual(self.count('market_observations'), 2)

    def test_import_is_atomic(self):
        text = HEAD + 'SYNA,2026-11-05,1\nSYNB,2026-11-05,2\nSYNC,2026-11-05,3\n'
        real, calls = valuation.ingest_observation, []

        def flaky(store, observation):
            calls.append(1)
            if len(calls) == 3:
                raise RuntimeError('boom')
            return real(store, observation)
        with mock.patch.object(valuation, 'ingest_observation', flaky):
            with self.assertRaises(RuntimeError):
                self.run_import(text)
        self.assertEqual(self.count('market_observations'), 0)

    def test_result_never_contains_values_or_file_text(self):
        text = HEAD + 'SYNA,2026-11-05,123.456\nNOPE,2026-11-05,SECRET-MARKER\nSYNB,SECRET-DATE,5\n'
        dump = json.dumps(self.run_import(text, dry_run=True))
        for needle in ('123.456', 'SECRET', 'MARKER'):
            self.assertNotIn(needle, dump)


if __name__ == '__main__':
    unittest.main()
class ImportRowsSeamTests(Base):
    """import_rows takes ParsedRow objects directly (used by the fetch pipeline); same semantics."""
    def rows(self, *items):
        return [price_import.ParsedRow(i + 2, s, d, c) for i, (s, d, c) in enumerate(items)]
    def test_same_result_shape_as_csv_import(self):
        csv_result = self.run_import(HEAD + 'SYNA,2026-11-05,1.5\nSYNB,2026-11-05,2\n', dry_run=True)
        rows_result = price_import.import_rows(
            self.store, self.rows(('SYNA', '2026-11-05', '1.5'), ('SYNB', '2026-11-05', '2')),
            'synthetic-file', dry_run=True, now=NOW)
        csv_result.pop('ignored_columns')
        rows_result.pop('ignored_columns')
        self.assertEqual(rows_result, csv_result)
    def test_writes_unchanged_corrected_and_rejects_bad_rows(self):
        rows = self.rows(('SYNA', '2026-11-05', '1.5'), ('NOPE', '2026-11-05', '2'),
                         ('SYNFX', '2026-11-05', '2'), ('SYNB', '2026-11-05', '0'))
        result = price_import.import_rows(self.store, rows, 'yahoo-chart', dry_run=False, now=NOW)
        self.assertEqual([r['outcome'] for r in result['rows']],
                         ['imported', 'unknown_symbol', 'unsupported_kind', 'invalid_value'])
        self.assertEqual(self.count('market_observations'), 1)
        again = price_import.import_rows(self.store, rows[:1], 'yahoo-chart', dry_run=False, now=LATER)
        self.assertEqual(again['counts'], {'unchanged': 1})
        fixed = self.rows(('SYNA', '2026-11-05', '1.6'))
        self.assertEqual(price_import.import_rows(self.store, fixed, 'yahoo-chart', dry_run=False,
                                                  now=LATER)['counts'], {'corrected': 1})
        self.assertEqual(self.count('market_observations'), 2)
    def test_invalid_source_and_empty_rows_are_file_errors(self):
        with self.assertRaises(FileError) as caught:
            price_import.import_rows(self.store, self.rows(('SYNA', '2026-11-05', '1')), 'bad source!',
                                     dry_run=True, now=NOW)
        self.assertEqual(caught.exception.code, 'invalid_source')
        with self.assertRaises(FileError) as caught:
            price_import.import_rows(self.store, [], 'yahoo-chart', dry_run=True, now=NOW)
        self.assertEqual(caught.exception.code, 'empty_file')
