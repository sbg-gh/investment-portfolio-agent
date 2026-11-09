"""M2 v5 migration acceptance: written against the unchanged v4 baseline first."""
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


def _build_v4(path):
    """Build a v4 database with synthetic holdings/receipts/aliases."""
    db = sqlite3.connect(path)
    migrate(db, MIGRATIONS[:4])
    db.execute("INSERT INTO users VALUES ('alice')")
    db.execute("INSERT INTO portfolios VALUES ('p','alice','private')")
    db.execute("INSERT INTO securities(id,symbol,kind,country,source) VALUES ('s','S','stock','US','fixture')")
    db.execute("INSERT INTO holdings VALUES ('p','s','1.25')")
    db.execute("INSERT INTO import_receipts(id,portfolio_id,security_id,idempotency_key,source,market_as_of,"
               "quantity,action) VALUES ('r','p','s','k1','fixture','2026-01-01T00:00:00+00:00','1.25','upsert')")
    db.execute("INSERT INTO holding_events(id,portfolio_id,security_id,event,quantity,receipt_id) "
               "VALUES ('e','p','s','import','1.25','r')")
    db.execute("INSERT INTO security_aliases(id,symbol,security_id,source,effective_from) "
               "VALUES ('a','ALIAS','s','fixture','2026-01-01T00:00:00+00:00')")
    db.commit()
    db.close()


def _build_v5(path):
    """Build a v5 database with synthetic holdings/transactions/admissions/coverage."""
    db = sqlite3.connect(path)
    migrate(db, MIGRATIONS[:5])
    db.execute("INSERT INTO users VALUES ('alice')")
    db.execute("INSERT INTO portfolios VALUES ('p','alice','private')")
    db.execute("INSERT INTO securities(id,symbol,kind,country,source) VALUES ('s','S','stock','US','fixture')")
    db.execute("INSERT INTO holdings VALUES ('p','s','1.25')")
    db.execute("INSERT INTO transactions(id,portfolio_id,security_id,side,quantity,source,idempotency_key,occurred_at) "
               "VALUES ('t','p','s','buy','1.25','fixture','k1','2026-01-01T00:00:00+00:00')")
    db.execute("INSERT INTO monitoring_admissions(id,security_id,state,reason,operator,position,proposed_at) "
               "VALUES ('m','s','admitted','fixture','admin',NULL,'2026-01-01T00:00:00+00:00')")
    db.execute("INSERT INTO baseline_coverage(security_id,covered,since) VALUES ('s',1,'2026-01-01T00:00:00+00:00')")
    db.commit()
    db.close()


def _build_v6(path):
    """Build a v6 database with a synthetic user and one private memory (legacy columns)."""
    db = sqlite3.connect(path)
    migrate(db, MIGRATIONS[:6])
    db.execute("INSERT INTO users VALUES ('alice')")
    db.execute("INSERT INTO portfolios VALUES ('p','alice','private')")
    db.execute("INSERT INTO securities(id,symbol,kind,country,source) VALUES ('s','S','stock','US','fixture')")
    db.execute("INSERT INTO holdings VALUES ('p','s','1.25')")
    db.execute("INSERT INTO memories(id,user_id,scope,kind,body,source) "
               "VALUES ('mem','alice','private','episodic','alice note','fixture')")
    db.commit()
    db.close()


class V7MigrationTests(unittest.TestCase):
    def test_fresh_database_ends_at_v7(self):
        s = Store()
        self.addCleanup(s.close)
        self.assertEqual(s.db.execute('PRAGMA user_version').fetchone()[0], 7)
        self.assertEqual(SCHEMA_VERSION, 7)
        for table in ('run_records', 'memory_deletions'):
            self.assertIsNotNone(
                s.db.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchone())
        columns = {row[1] for row in s.db.execute('PRAGMA table_info(memories)').fetchall()}
        for column in ('portfolio_id', 'payload', 'source_run_id', 'evidence_ids', 'expires_at',
                       'confidence', 'status', 'version', 'supersedes', 'applicable_conditions',
                       'review_status'):
            self.assertIn(column, columns)

    def test_v6_upgrade_preserves_existing_data(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'old.sqlite3'
            _build_v6(path)
            upgraded = Store(str(path))
            try:
                self.assertEqual(upgraded.holdings('alice', 'p'), [('s', '1.25')])
                self.assertEqual(upgraded.db.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
                self.assertEqual(upgraded.retrieve_memory('alice'),
                                 [('episodic', 'alice note', 'fixture')])
                self.assertEqual(upgraded.db.execute('SELECT COUNT(*) FROM memories').fetchone()[0], 1)
                self.assertEqual(upgraded.db.execute('SELECT COUNT(*) FROM run_records').fetchone()[0], 0)
                self.assertEqual(upgraded.db.execute('SELECT COUNT(*) FROM memory_deletions').fetchone()[0], 0)
                # New rows carry the substrate defaults.
                self.assertEqual(upgraded.db.execute(
                    "SELECT status,version,review_status FROM memories WHERE id='mem'").fetchone(),
                    ('active', 1, 'unreviewed'))
            finally:
                upgraded.close()

    def test_broken_v7_upgrade_rolls_back_to_v6(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'old.sqlite3'
            _build_v6(path)
            db = sqlite3.connect(path)
            self.addCleanup(db.close)
            broken = MIGRATIONS[:6] + ((7, 'CREATE TABLE partial(id TEXT);\nINSERT INTO nonexistent VALUES (1);\n'),)
            with self.assertRaises(sqlite3.OperationalError):
                migrate(db, broken)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 6)
            self.assertEqual(db.execute('SELECT * FROM holdings').fetchall(), [('p', 's', '1.25')])
            self.assertEqual(db.execute("SELECT body FROM memories WHERE id='mem'").fetchall(),
                             [('alice note',)])
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='partial'").fetchone())

    def test_future_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'future.sqlite3'
            db = sqlite3.connect(path)
            db.execute('PRAGMA user_version=999')
            db.close()
            with self.assertRaises(ValueError):
                Store(str(path))

    def test_v7_backup_restore_preserves_new_tables_and_revokes_credentials(self):
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
        s.add_private_memory('alice', 'semantic', 'alice fact', 'fixture')
        s.record_run('alice', request_id='req-1', task_type='demo', policy_version='m1-v1',
                     tool_sequence=['list_holdings'], outcome='success')
        s.db.execute("INSERT INTO memory_deletions(id,memory_id,previous_status,reason,scope) "
                     "SELECT 'd','x','active','test','private' WHERE 0")
        s.db.commit()
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
                self.assertEqual(restored.db.execute('SELECT COUNT(*) FROM run_records').fetchone()[0], 1)
                self.assertIsNotNone(
                    restored.db.execute("SELECT name FROM sqlite_master WHERE name='memory_deletions'").fetchone())
                self.assertEqual(restored.db.execute('SELECT COUNT(*) FROM memories').fetchone()[0], 1)
                self.assertEqual(restored.db.execute('SELECT COUNT(*) FROM credentials WHERE revoked=0').fetchone()[0], 0)
            finally:
                restored.close()


class V6MigrationTests(unittest.TestCase):
    def test_fresh_database_ends_at_v6(self):
        s = Store()
        self.addCleanup(s.close)
        self.assertEqual(s.db.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
        for table in ('jobs', 'notification_outbox', 'notification_preferences', 'monitoring_state'):
            self.assertIsNotNone(
                s.db.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchone())

    def test_v5_upgrade_preserves_existing_data(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'old.sqlite3'
            _build_v5(path)
            upgraded = Store(str(path))
            try:
                self.assertEqual(upgraded.holdings('alice', 'p'), [('s', '1.25')])
                self.assertEqual(upgraded.db.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
                self.assertEqual(upgraded.db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0], 1)
                self.assertEqual(upgraded.db.execute('SELECT COUNT(*) FROM monitoring_admissions').fetchone()[0], 1)
                self.assertEqual(upgraded.db.execute('SELECT COUNT(*) FROM baseline_coverage').fetchone()[0], 1)
                self.assertEqual(upgraded.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)
                self.assertEqual(upgraded.db.execute('SELECT COUNT(*) FROM notification_outbox').fetchone()[0], 0)
            finally:
                upgraded.close()

    def test_broken_v6_upgrade_rolls_back_to_v5(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'old.sqlite3'
            _build_v5(path)
            db = sqlite3.connect(path)
            self.addCleanup(db.close)
            broken = MIGRATIONS[:5] + ((6, 'CREATE TABLE partial(id TEXT);\nINSERT INTO nonexistent VALUES (1);\n'),)
            with self.assertRaises(sqlite3.OperationalError):
                migrate(db, broken)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 5)
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

    def test_v6_backup_restore_preserves_new_tables_and_revokes_credentials(self):
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
        s.db.execute("INSERT INTO jobs(id,type,public_scope,idempotency_key,due_at,correlation_id) "
                     "VALUES ('j','monitor','global','ikey','2026-11-06T21:00:00+00:00','corr')")
        s.db.execute("INSERT INTO notification_outbox(id,user_id,event,dedup_key,channel) "
                     "VALUES ('o','alice','stale','d1','local-sink')")
        s.db.execute("INSERT INTO notification_preferences(user_id) VALUES ('alice')")
        s.db.execute("INSERT INTO monitoring_state(security_id,rule,state_key,observed_at) "
                     "VALUES ('s0','stale-coverage','sk','2026-11-06T21:00:00+00:00')")
        s.db.commit()
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
                self.assertEqual(restored.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 1)
                self.assertEqual(restored.db.execute('SELECT COUNT(*) FROM notification_outbox').fetchone()[0], 1)
                self.assertEqual(restored.db.execute('SELECT COUNT(*) FROM notification_preferences').fetchone()[0], 1)
                self.assertEqual(restored.db.execute('SELECT COUNT(*) FROM monitoring_state').fetchone()[0], 1)
                self.assertEqual(restored.db.execute('SELECT COUNT(*) FROM credentials WHERE revoked=0').fetchone()[0], 0)
            finally:
                restored.close()


class V5MigrationTests(unittest.TestCase):
    def test_fresh_database_has_v5_tables(self):
        s = Store()
        self.addCleanup(s.close)
        self.assertEqual(s.db.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
        for table in ('transactions', 'monitoring_admissions', 'baseline_coverage'):
            self.assertIsNotNone(
                s.db.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchone())

    def test_v4_upgrade_preserves_existing_data(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'old.sqlite3'
            _build_v4(path)
            upgraded = Store(str(path))
            try:
                self.assertEqual(upgraded.holdings('alice', 'p'), [('s', '1.25')])
                self.assertEqual(upgraded.db.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
                self.assertEqual(upgraded.db.execute('SELECT COUNT(*) FROM import_receipts').fetchone()[0], 1)
                self.assertEqual(upgraded.db.execute('SELECT COUNT(*) FROM holding_events').fetchone()[0], 1)
                self.assertEqual(upgraded.db.execute('SELECT COUNT(*) FROM security_aliases').fetchone()[0], 1)
                self.assertEqual(upgraded.db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0], 0)
            finally:
                upgraded.close()

    def test_broken_v5_upgrade_rolls_back_to_v4(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'old.sqlite3'
            _build_v4(path)
            db = sqlite3.connect(path)
            self.addCleanup(db.close)
            broken = MIGRATIONS[:4] + ((5, 'CREATE TABLE partial(id TEXT);\nINSERT INTO nonexistent VALUES (1);\n'),)
            with self.assertRaises(sqlite3.OperationalError):
                migrate(db, broken)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 4)
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

    def test_v5_backup_restore_preserves_new_tables_and_revokes_credentials(self):
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
        s.record_transaction('alice', p, 's0', 'buy', '2.5', source='fixture',
                             idempotency_key='t1',
                             occurred_at=datetime(2026, 11, 6, 21, tzinfo=timezone.utc))
        s.db.execute("INSERT INTO monitoring_admissions(id,security_id,state,reason,operator,position,proposed_at) "
                     "VALUES ('m','s0','admitted','fixture','admin',NULL,'2026-11-06T21:00:00+00:00')")
        # baseline_coverage for s0 is populated atomically by the import above (FEAT-002).
        s.db.commit()
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
                self.assertEqual(restored.db.execute('SELECT COUNT(*) FROM transactions').fetchone()[0], 1)
                self.assertEqual(restored.db.execute('SELECT COUNT(*) FROM monitoring_admissions').fetchone()[0], 1)
                self.assertEqual(restored.db.execute('SELECT COUNT(*) FROM baseline_coverage').fetchone()[0], 1)
                self.assertEqual(restored.db.execute('SELECT COUNT(*) FROM credentials WHERE revoked=0').fetchone()[0], 0)
            finally:
                restored.close()


if __name__ == '__main__':
    unittest.main()
