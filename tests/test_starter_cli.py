"""seed-starter-data CLI: trusted local administration, counts-only output, no ui extra needed."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from portfolio_agent.web import server
ROOT = Path(__file__).resolve().parents[1]
def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = server.main(list(argv), env={})
    return code, out.getvalue(), err.getvalue()
class SeedCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / 'p.db')
    def count(self, table):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        finally:
            con.close()
    def test_registers_instruments_only_without_user_and_is_idempotent(self):
        code, out, err = run_cli('seed-starter-data', '--db', self.db)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {'instruments_registered': 20, 'instruments_already_present': 0,
                                           'sample_portfolio': 'skipped'})
        code, out, _ = run_cli('seed-starter-data', '--db', self.db)
        self.assertEqual(json.loads(out)['instruments_registered'], 0)
        self.assertEqual(self.count('securities'), 20)
        self.assertEqual(self.count('portfolios'), 0)
        self.assertEqual(self.count('market_observations'), 0)
    def test_user_gets_one_sample_portfolio_and_rerun_reports_existing(self):
        run_cli('create-user', 'alice', '--db', self.db)
        code, out, err = run_cli('seed-starter-data', '--db', self.db, '--user', 'alice')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)['sample_portfolio'], 'created')
        code, out, _ = run_cli('seed-starter-data', '--db', self.db, '--user', 'alice')
        self.assertEqual(json.loads(out)['sample_portfolio'], 'existing')
        self.assertEqual((self.count('portfolios'), self.count('import_receipts')), (1, 20))
    def test_output_is_counts_only(self):
        run_cli('create-user', 'alice', '--db', self.db)
        code, out, err = run_cli('seed-starter-data', '--db', self.db, '--user', 'alice')
        for needle in ('AMD', 'op:', 'alice', 'Sample'):
            self.assertNotIn(needle, out + err)
    def test_unknown_user_fails_generically_and_creates_nothing(self):
        code, out, err = run_cli('seed-starter-data', '--db', self.db, '--user', 'ghost')
        self.assertEqual(code, 1)
        self.assertEqual(err.strip(), 'error: unknown user or credential')
        self.assertNotIn('ghost', out + err)
        self.assertEqual((self.count('securities'), self.count('portfolios')), (0, 0))
    def test_runs_without_importing_uvicorn(self):
        code = ("import sys; from portfolio_agent.web import server; "
                f"rc = server.main(['seed-starter-data','--db',{self.db!r}], env={{}}); "
                "assert 'uvicorn' not in sys.modules; sys.exit(rc)")
        done = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True,
                              env={'PYTHONPATH': str(ROOT / 'src'), 'PATH': ''})
        self.assertEqual(done.returncode, 0, done.stderr)
if __name__ == '__main__':
    unittest.main()
