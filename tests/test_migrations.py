"""M2 v4 migration acceptance: written against the unchanged v3 baseline first."""
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from portfolio_agent.core import Store
from portfolio_agent.migrations import MIGRATIONS, SCHEMA_VERSION, migrate


def _build_v3(path):
    """Build a v3 database with synthetic holdings/observations/snapshots."""
    db = sqlite3.connect(path)
    migrate(db, MIGRATIONS[:3])
    db.execute("INSERT INTO users VALUES ('alice')")
    db.execute("INSERT INTO portfolios VALUES ('p','alice','private')")
    db.execute("INSERT INTO securities(id,symbol,kind,country,source) VALUES ('s','S','stock','US','fixture')")
    db.execute("INSERT INTO holdings VALUES ('p','s','1.25')")
    db.execute("INSERT INTO market_observations VALUES "
               "('o','s','10','USD','price','fixture','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')")
    db.execute("INSERT INTO valuation_snapshots(id,user_id,portfolio_id,payload) VALUES ('snap','alice','p','{}')")
    db.commit()
    db.close()


class V4MigrationTests(unittest.TestCase):
    def test_fresh_database_ends_at_v4(self):
        s = Store()
        self.addCleanup(s.close)
        self.assertEqual(s.db.execute('PRAGMA user_version').fetchone()[0], 4)
        self.assertEqual(SCHEMA_VERSION, 4)
        for table in ('import_receipts', 'holding_events', 'security_aliases'):
            self.assertIsNotNone(
                s.db.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchone())

    def test_v3_upgrade_preserves_existing_data(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'old.sqlite3'
            _build_v3(path)
            upgraded = Store(str(path))
            try:
                self.assertEqual(upgraded.holdings('alice', 'p'), [('s', '1.25')])
                self.assertEqual(upgraded.db.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
                self.assertEqual(upgraded.db.execute('SELECT COUNT(*) FROM market_observations').fetchone()[0], 1)
                self.assertEqual(upgraded.db.execute('SELECT COUNT(*) FROM valuation_snapshots').fetchone()[0], 1)
                self.assertEqual(upgraded.db.execute('SELECT COUNT(*) FROM import_receipts').fetchone()[0], 0)
            finally:
                upgraded.close()

    def test_broken_v4_upgrade_rolls_back_to_v3(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'old.sqlite3'
            _build_v3(path)
            db = sqlite3.connect(path)
            self.addCleanup(db.close)
            broken = MIGRATIONS[:3] + ((4, 'CREATE TABLE partial(id TEXT);\nINSERT INTO nonexistent VALUES (1);\n'),)
            with self.assertRaises(sqlite3.OperationalError):
                migrate(db, broken)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertEqual(db.execute('SELECT * FROM holdings').fetchall(), [('p', 's', '1.25')])
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='partial'").fetchone())

    def test_future_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'future.sqlite3'
            db = sqlite3.connect(path)
            db.execute('PRAGMA user_version=999')
            db.close()
            with self.assertRaises(ValueError):
                Store(str(path))

    def test_v4_backup_restore_preserves_audit_tables_and_revokes_credentials(self):
        from portfolio_agent.auth import Authenticator
        from portfolio_agent.backup import backup, restore
        from portfolio_agent.core import FixtureValidator, ValidatedSecurity
        from portfolio_agent.services import PortfolioService
        s = Store()
        self.addCleanup(s.close)
        s.create_user('alice')
        auth = Authenticator(s)
        app = PortfolioService(s, auth,
                               FixtureValidator({'DEMO': ValidatedSecurity('s0', 'DEMO', 'etf', 'US', 'fixture')}))
        ctx = auth.authenticate(auth.provision('alice'))
        p = app.create_portfolio(ctx, 'private')
        s.import_holding_with_provenance('alice', p, 'DEMO', '2.5', app._validator, source='fixture',
                                         market_as_of=datetime(2026, 11, 6, 21, tzinfo=timezone.utc),
                                         idempotency_key='k1')
        s.register_alias('ALIAS', 's0', 'fixture', datetime(2026, 1, 1, tzinfo=timezone.utc))
        with tempfile.TemporaryDirectory() as td:
            snapshot = Path(td) / 'snap.sqlite3'
            recovered = Path(td) / 'restored.sqlite3'
            backup(s, snapshot)
            restore(snapshot, recovered)
            restored = Store(str(recovered))
            try:
                self.assertEqual(restored.db.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
                self.assertEqual(restored.db.execute('PRAGMA integrity_check').fetchone(), ('ok',))
                self.assertEqual(restored.db.execute('PRAGMA foreign_key_check').fetchall(), [])
                self.assertEqual(restored.db.execute('SELECT COUNT(*) FROM import_receipts').fetchone()[0], 1)
                self.assertEqual(restored.db.execute('SELECT COUNT(*) FROM holding_events').fetchone()[0], 1)
                self.assertEqual(restored.db.execute('SELECT COUNT(*) FROM security_aliases').fetchone()[0], 1)
                self.assertEqual(restored.db.execute('SELECT COUNT(*) FROM credentials WHERE revoked=0').fetchone()[0], 0)
            finally:
                restored.close()


if __name__ == '__main__':
    unittest.main()
