"""import-prices CLI: trusted local administration, JSON without values or file text, no ui extra.
Synthetic fixtures only."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from portfolio_agent.core import Store, ValidatedSecurity
from portfolio_agent.web import server

ROOT = Path(__file__).resolve().parents[1]
HEAD = 'symbol,date,close\n'


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = server.main(list(argv), env={})
    return code, out.getvalue(), err.getvalue()


class ImportPricesCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / 'p.db')
        self.csv = Path(self.tmp.name) / 'prices.csv'
        store = Store(self.db)
        with store.transaction():
            for sid, symbol in (('s1', 'SYNA'), ('s2', 'SYNB')):
                store.register(ValidatedSecurity(sid, symbol, 'stock', 'US', 'synthetic-test'))
        store.close()

    def write(self, text):
        self.csv.write_bytes(text if isinstance(text, bytes) else text.encode())

    def count(self, table):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        finally:
            con.close()

    def cli(self, *extra):
        return run_cli('import-prices', '--db', self.db, '--csv', str(self.csv), '--source', 'cli-src', *extra)

    def test_dry_run_prints_json_and_changes_nothing(self):
        self.write(HEAD + 'SYNA,2020-01-02,1.5\nNOPE,2020-01-02,2\n')
        code, out, err = self.cli('--dry-run')
        self.assertEqual(code, 3, err)
        result = json.loads(out)
        self.assertEqual((result['dry_run'], result['counts']), (True, {'imported': 1, 'unknown_symbol': 1}))
        self.assertEqual(result['rows'][0], {'line': 2, 'symbol': 'SYNA', 'outcome': 'imported'})
        self.assertEqual(self.count('market_observations'), 0)

    def test_import_then_rerun_is_unchanged_with_exit_zero(self):
        self.write(HEAD + 'SYNA,2020-01-02,1.5\nSYNB,2020-01-02,2\n')
        code, out, err = self.cli()
        self.assertEqual((code, json.loads(out)['counts']), (0, {'imported': 2}))
        self.assertEqual(self.count('market_observations'), 2)
        code, out, _ = self.cli()
        self.assertEqual((code, json.loads(out)['counts']), (0, {'unchanged': 2}))
        self.assertEqual(self.count('market_observations'), 2)

    def test_correction_is_reported(self):
        self.write(HEAD + 'SYNA,2020-01-02,1.5\n')
        self.cli()
        self.write(HEAD + 'SYNA,2020-01-02,2.5\n')
        code, out, _ = self.cli()
        self.assertEqual((code, json.loads(out)['counts']), (0, {'corrected': 1}))

    def test_rejected_row_exits_3_but_valid_rows_are_imported(self):
        self.write(HEAD + 'SYNA,2020-01-02,1.5\nSYNB,2020-01-02,abc\n')
        code, out, _ = self.cli()
        self.assertEqual((code, json.loads(out)['counts']), (3, {'imported': 1, 'invalid_format': 1}))
        self.assertEqual(self.count('market_observations'), 1)

    def test_file_level_errors_exit_1_with_only_a_code(self):
        for content, expected in ((HEAD, 'empty_file'), ('a,b\n1,2\n', 'invalid_header'),
                                  (HEAD.encode() + b'\xff\xfe\n', 'invalid_encoding')):
            self.write(content)
            code, out, err = self.cli()
            self.assertEqual((code, out, err.strip()), (1, '', f'error: {expected}'))
        self.assertEqual(self.count('market_observations'), 0)

    def test_bad_source_and_missing_file_exit_1(self):
        self.write(HEAD + 'SYNA,2020-01-02,1\n')
        code, out, err = run_cli('import-prices', '--db', self.db, '--csv', str(self.csv), '--source', 'bad source')
        self.assertEqual((code, out, err.strip()), (1, '', 'error: invalid_source'))
        missing = str(Path(self.tmp.name) / 'nope-secret-name.csv')
        code, out, err = run_cli('import-prices', '--db', self.db, '--csv', missing, '--source', 'cli-src')
        self.assertEqual((code, out, err.strip()), (1, '', 'error: invalid_file'))
        self.assertNotIn('nope-secret-name', err)

    def test_output_has_no_values_cells_or_paths(self):
        self.write(HEAD.replace('close', 'close,extra') + 'SYNA,2020-01-02,123.456,SECRET-CELL\n'
                   'SECRET symbol,2020-01-02,9.99,x\n')
        code, out, err = self.cli()
        for needle in ('123.456', '9.99', 'SECRET', str(self.csv), self.tmp.name):
            self.assertNotIn(needle, out + err)

    def test_missing_required_arguments_fail(self):
        for argv in (['import-prices'], ['import-prices', '--db', self.db],
                     ['import-prices', '--db', self.db, '--csv', str(self.csv)]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                server.parse_args(argv)
            self.assertNotEqual(caught.exception.code, 0)

    def test_unregistered_symbol_is_never_registered(self):
        self.write(HEAD + 'NOPE,2020-01-02,1\n')
        self.cli()
        self.assertEqual(self.count('securities'), 2)

    def test_runs_without_importing_uvicorn(self):
        self.write(HEAD + 'SYNA,2020-01-02,1.5\n')
        code = ("import sys; from portfolio_agent.web import server; "
                f"rc = server.main(['import-prices','--db',{self.db!r},'--csv',{str(self.csv)!r},"
                "'--source','cli-src','--dry-run'], env={}); "
                "assert 'uvicorn' not in sys.modules; sys.exit(rc)")
        done = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True,
                              env={'PYTHONPATH': str(ROOT / 'src'), 'PATH': ''})
        self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == '__main__':
    unittest.main()
