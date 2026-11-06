"""M1 acceptance tests: written against the unchanged M0 baseline first."""
from dataclasses import replace
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from portfolio_agent.core import Store, FixtureValidator, ValidatedSecurity

class MemoryRegressionTests(unittest.TestCase):
    def test_missing_private_owner_is_rejected(self):
        s=Store()
        self.addCleanup(s.close)
        s.create_user('bob')
        for owner in (None,'','missing'):
            with self.subTest(owner=owner):
                with self.assertRaises(PermissionError):
                    s.add_private_memory(owner,'episodic','private secret','fixture')
        self.assertEqual(s.retrieve_memory('bob'),[])

class M1Tests(unittest.TestCase):
    def setUp(self):
        from portfolio_agent.auth import Authenticator
        from portfolio_agent.services import PortfolioService, OperatorService
        self.s=Store()
        self.addCleanup(self.s.close)
        for user in ('alice','bob','admin'):self.s.create_user(user)
        self.now=[1000.0]
        self.auth=Authenticator(self.s,clock=lambda:self.now[0])
        self.tokens={u:self.auth.provision(u,role='operator' if u=='admin' else 'user') for u in ('alice','bob','admin')}
        self.contexts={u:self.auth.authenticate(t) for u,t in self.tokens.items()}
        self.v=FixtureValidator({'DEMO':ValidatedSecurity('s0','DEMO','etf','US','fixture')})
        self.service=PortfolioService(self.s,self.auth,self.v)
        self.operator=OperatorService(self.service)
        self.p=self.service.create_portfolio(self.contexts['alice'],'private name')

    def test_context_cannot_be_forged_or_relabelled(self):
        from portfolio_agent.auth import Authenticator
        with self.assertRaises(PermissionError):self.auth.authenticate('alice')
        with self.assertRaises(PermissionError):self.service.holdings('alice',self.p)
        forged=replace(self.contexts['bob'],user_id='alice')
        with self.assertRaises(PermissionError):self.service.holdings(forged,self.p)
        other=Authenticator(self.s,clock=lambda:self.now[0])
        with self.assertRaises(PermissionError):other.require(self.contexts['alice'])
        rows=self.s.db.execute('SELECT token_hash FROM credentials').fetchall()
        self.assertNotIn(self.tokens['alice'],str(rows))
        self.assertIn((hashlib.sha256(self.tokens['alice'].encode()).hexdigest(),),rows)

    def test_expiry_and_revocation_invalidate_existing_context(self):
        self.auth.revoke(self.contexts['alice'].credential_id)
        with self.assertRaises(PermissionError):self.service.holdings(self.contexts['alice'],self.p)
        with self.assertRaises(PermissionError):self.auth.authenticate(self.tokens['alice'])
        self.now[0]+=301
        with self.assertRaises(PermissionError):self.service.memories(self.contexts['bob'])
        fresh=self.auth.authenticate(self.tokens['bob'])
        self.assertEqual(self.service.memories(fresh),[])
        self.now[0]+=86400
        with self.assertRaises(PermissionError):self.auth.authenticate(self.tokens['bob'])

    def test_user_cannot_publish_or_change_global_membership(self):
        for action in (lambda:self.operator.ingest_public_fact(self.contexts['alice'],'secret','source'),
                       lambda:self.operator.set_research(self.contexts['alice'],'s0'),
                       lambda:self.operator.enable_monitoring(self.contexts['alice'],'s0')):
            with self.assertRaises(PermissionError):action()
        self.operator.ingest_public_fact(self.contexts['admin'],'public','fixture')
        self.assertEqual(self.service.memories(self.contexts['bob']),[('semantic','public','fixture')])

    def test_cross_user_operations_and_deleted_ownership(self):
        a,b=self.contexts['alice'],self.contexts['bob']
        self.service.import_holding(a,self.p,'DEMO','2.5')
        self.service.add_memory(a,'episodic','secret content','private-source')
        for action in (lambda:self.service.holdings(b,self.p),
                       lambda:self.service.import_holding(b,self.p,'DEMO','9'),
                       lambda:self.service.record_paper_order(b,self.p,'s0','1','5',mode='paper')):
            with self.assertRaises(PermissionError):action()
        self.assertEqual(self.service.memories(b),[])
        self.assertEqual(self.service.holdings(a,self.p),[('s0','2.5')])
        with self.s.db:self.s.db.execute('UPDATE portfolios SET user_id=? WHERE id=?',('bob',self.p))
        with self.assertRaises(PermissionError):self.service.holdings(a,self.p)
        self.assertEqual(self.service.holdings(b,self.p),[('s0','2.5')])

    def test_traces_are_private_and_have_no_payload(self):
        a,b=self.contexts['alice'],self.contexts['bob']
        self.service.add_memory(a,'episodic','sensitive body','sensitive source')
        with self.assertRaises(PermissionError):self.service.holdings(b,self.p)
        alice=self.service.traces(a)
        bob=self.service.traces(b)
        self.assertEqual([r[2] for r in bob],['holdings'])
        self.assertEqual(bob[0][3],'denied')
        self.assertNotIn('sensitive',str(alice))
        self.assertNotIn(self.p,str(bob))
        self.assertNotIn('private name',str(alice))
        self.assertTrue(all(r[4]=='m1-v1' and r[5]>=0 for r in alice+bob))

    def test_schema_rejects_accidental_public_private_mixture(self):
        for owner,scope,kind in ((None,'private','episodic'),('alice','public','semantic'),(None,'public','procedural')):
            with self.assertRaises(sqlite3.IntegrityError):
                with self.s.db:
                    self.s.db.execute('INSERT INTO memories(id,user_id,scope,kind,body,source) VALUES (?,?,?,?,?,?)',
                                      ('bad',owner,scope,kind,'secret','source'))

    def test_full_monitoring_tier_does_not_block_new_holding(self):
        for n in range(75):
            with self.s.db:self.s.register(ValidatedSecurity(f'm{n}',f'M{n}','stock','US','fixture'))
            self.operator.enable_monitoring(self.contexts['admin'],f'm{n}')
        self.service.import_holding(self.contexts['alice'],self.p,'DEMO','2')
        self.assertEqual(self.service.holdings(self.contexts['alice'],self.p),[('s0','2')])
        with self.assertRaises(sqlite3.IntegrityError):self.operator.enable_monitoring(self.contexts['admin'],'s0')

    def test_trace_failure_rolls_back_successful_action(self):
        # A caller must not get an error after a hidden mutation has committed.
        self.s.db.execute("CREATE TRIGGER fail_trace BEFORE INSERT ON execution_traces BEGIN SELECT RAISE(ABORT,'trace unavailable'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.import_holding(self.contexts['alice'],self.p,'DEMO','3')
        self.assertEqual(self.s.holdings('alice',self.p),[])
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM securities').fetchone()[0],0)

    def test_backup_restore_preserves_data_and_revokes_credentials(self):
        from portfolio_agent.backup import backup, restore
        a=self.contexts['alice']
        self.service.import_holding(a,self.p,'DEMO','2.5')
        self.service.add_memory(a,'episodic','alice secret','source')
        self.service.add_memory(self.contexts['bob'],'semantic','bob secret','source')
        with tempfile.TemporaryDirectory() as td:
            snapshot=Path(td)/'snapshot.sqlite3'; recovered=Path(td)/'restored.sqlite3'
            backup(self.s,snapshot)
            with self.assertRaises(FileExistsError):backup(self.s,snapshot)
            restore(snapshot,recovered)
            with self.assertRaises(FileExistsError):restore(snapshot,recovered)
            restored=Store(str(recovered))
            try:
                self.assertEqual(restored.holdings('alice',self.p),[('s0','2.5')])
                self.assertEqual(restored.retrieve_memory('alice'),[('episodic','alice secret','source')])
                self.assertEqual(restored.retrieve_memory('bob'),[('semantic','bob secret','source')])
                with self.assertRaises(PermissionError):restored.holdings('bob',self.p)
                self.assertEqual(restored.db.execute('SELECT COUNT(*) FROM credentials WHERE revoked=0').fetchone()[0],0)
                self.assertEqual(restored.db.execute('PRAGMA integrity_check').fetchone(),('ok',))
            finally:restored.close()

class MigrationTests(unittest.TestCase):
    def test_m0_upgrade_preserves_private_data_and_quarantines_null_owners(self):
        from portfolio_agent.migrations import LEGACY_SCHEMA, SCHEMA_VERSION
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'legacy.sqlite3'
            db=sqlite3.connect(path);db.executescript(LEGACY_SCHEMA)
            db.execute("INSERT INTO users VALUES ('alice')")
            db.execute("INSERT INTO memories(id,user_id,kind,body,source) VALUES ('a','alice','episodic','secret','source')")
            db.execute("INSERT INTO memories(id,user_id,kind,body,source) VALUES ('b',NULL,'semantic','ambiguous','source')")
            db.commit();db.close()
            s=Store(str(path))
            self.assertEqual(s.retrieve_memory('alice'),[('episodic','secret','source')])
            self.assertEqual(s.db.execute('SELECT body FROM memory_quarantine').fetchall(),[('ambiguous',)])
            self.assertEqual(s.db.execute('PRAGMA user_version').fetchone()[0],SCHEMA_VERSION)
            s.close()
            s=Store(str(path))
            self.assertEqual(len(s.retrieve_memory('alice')),1)
            s.close()

    def test_migration_failure_rolls_back_ddl_and_version(self):
        from portfolio_agent.migrations import migrate
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        db.execute('CREATE TABLE original(value TEXT)')
        db.execute("INSERT INTO original VALUES ('saved')");db.commit()
        with self.assertRaises(sqlite3.OperationalError):
            migrate(db,((1,'CREATE TABLE partial(id TEXT);\nINSERT INTO nonexistent VALUES (1);\n'),))
        self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],0)
        self.assertEqual(db.execute('SELECT * FROM original').fetchall(),[('saved',)])
        self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='partial'").fetchone())

    def test_future_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'future.sqlite3'
            db=sqlite3.connect(path);db.execute('PRAGMA user_version=999');db.close()
            with self.assertRaises(ValueError):Store(str(path))

if __name__=='__main__':unittest.main()
