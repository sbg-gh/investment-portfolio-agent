"""fetch-prices CLI: trusted local administration, fixed codes, no ui extra, offline (fake transport
injected through price_fetch.HOOKS). Synthetic instruments only."""
import contextlib
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from portfolio_agent import price_fetch, yahoo_chart as yc
from portfolio_agent.core import Store, ValidatedSecurity
from portfolio_agent.web import server
from tests.test_yahoo_chart import Fake, Offline, by_symbol, ok
ROOT = Path(__file__).resolve().parents[1]
FRI_AFTER = datetime(2026, 11, 13, 22, 0, tzinfo=timezone.utc)
def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = server.main(list(argv), env={})
    return code, out.getvalue(), err.getvalue()
class FetchPricesCliTests(Offline):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / 'p.db')
        store = Store(self.db)
        with store.transaction():
            for sid, symbol, kind in (('s1', 'SYNA', 'stock'), ('s2', 'SYNB', 'etf'), ('f1', 'SYNFX', 'mutual_fund')):
                store.register(ValidatedSecurity(sid, symbol, kind, 'US', 'synthetic-test'))
        store.close()
    def hooks(self, transport):
        patcher = mock.patch.dict(price_fetch.HOOKS, {'transport': transport, 'clock': lambda: FRI_AFTER,
                                                      'sleeper': lambda s: None}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
    def count(self, table):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        finally:
            con.close()
    def cli(self, *extra):
        return run_cli('fetch-prices', '--db', self.db, *extra)
    def test_success_prints_fixed_json_and_exits_zero(self):
        self.hooks(Fake(default=by_symbol))
        code, out, err = self.cli()
        self.assertEqual((code, err), (0, ''))
        result = json.loads(out)
        self.assertEqual((result['dry_run'], result['source'], result['counts']), (False, 'yahoo-chart', {'imported': 2}))
        self.assertEqual([s['symbol'] for s in result['symbols']], ['SYNA', 'SYNB'])   # the fund is never requested
        self.assertEqual(self.count('market_observations'), 4)
    def test_rerun_is_idempotent_with_exit_zero(self):
        self.hooks(Fake(default=by_symbol))
        self.cli()
        code, out, _ = self.cli()
        self.assertEqual((code, json.loads(out)['counts']), (0, {'unchanged': 2}))
        self.assertEqual(self.count('market_observations'), 4)
    def test_dry_run_writes_nothing(self):
        fake = Fake(default=by_symbol)
        self.hooks(fake)
        code, out, _ = self.cli('--dry-run')
        self.assertEqual((code, json.loads(out)['dry_run'], len(fake.calls)), (0, True, 2))
        self.assertEqual(self.count('market_observations'), 0)
    def test_any_failed_symbol_exits_three_and_others_still_import(self):
        self.hooks(Fake(ok(b'{}', 400), by_symbol))
        code, out, _ = self.cli()
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out)['counts'], {'provider_error': 1, 'imported': 1})
        self.assertEqual(self.count('market_observations'), 2)
    def test_blocked_stops_the_press_and_exits_three(self):
        self.hooks(Fake(ok(b'no', 403, 'text/plain')))
        code, out, _ = self.cli()
        self.assertEqual((code, json.loads(out)['counts']), (3, {'blocked': 1, 'skipped_stopped': 1}))
    def test_output_has_no_provider_text_or_paths(self):
        self.hooks(Fake(ok(b'{"error":"SECRET-BODY"}', 400), by_symbol))
        code, out, err = self.cli()
        self.assertNotIn('SECRET', out + err)
        self.assertNotIn(self.db, out + err)
    def test_no_registered_instruments_is_a_configuration_error(self):
        empty = str(Path(self.tmp.name) / 'empty.db')
        Store(empty).close()
        code, out, err = run_cli('fetch-prices', '--db', empty)
        self.assertEqual((code, out, err), (1, '', 'error: no_instruments\n'))
    def test_unusable_database_is_exit_one(self):
        bad = Path(self.tmp.name) / 'not-a-db'
        bad.write_bytes(b'this is not sqlite' * 100)
        code, out, err = run_cli('fetch-prices', '--db', str(bad))
        self.assertEqual((code, out), (1, ''))
    def test_runs_without_importing_uvicorn(self):
        empty = str(Path(self.tmp.name) / 'empty.db')
        Store(empty).close()
        code = ("import sys; from portfolio_agent.web import server; "
                f"rc = server.main(['fetch-prices','--db',{empty!r}], env={{}}); "
                "assert 'uvicorn' not in sys.modules; sys.exit(rc)")
        done = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True,
                              env={'PYTHONPATH': str(ROOT / 'src'), 'PATH': ''})
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn('no_instruments', done.stderr)
if __name__ == '__main__':
    unittest.main()
