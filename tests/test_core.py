import sqlite3
import tempfile
import unittest
from pathlib import Path
from portfolio_agent.core import FixtureValidator, Store, ValidatedSecurity

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.s = Store()
        self.addCleanup(self.s.close)
        for user in ("alice","bob"):
            self.s.create_user(user)
        self.p = self.s.create_portfolio("alice","Private")
        self.v = FixtureValidator({"DEMO":self.security(0)})

    def security(self, n):
        return ValidatedSecurity(f"s{n}",f"S{n}","stock","US","fixture")

    def test_import_registers_without_membership(self):
        self.s.import_holding("alice",self.p,"DEMO","1.5",self.v)
        self.assertEqual(self.s.holdings("alice",self.p),[("s0","1.5")])
        for table in ("research","monitoring"):
            self.assertEqual(self.s.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],0)

    def test_private_portfolio_reads_and_writes(self):
        for action in (lambda:self.s.holdings("bob",self.p),
                       lambda:self.s.import_holding("bob",self.p,"DEMO","1",self.v),
                       lambda:self.s.record_paper_order("bob",self.p,"s0","1","2","paper")):
            with self.assertRaises(PermissionError): action()

    def test_invalid_holding_has_no_side_effects(self):
        for quantity in ("0","-1","NaN","Infinity","garbage"):
            with self.assertRaises(ValueError):
                self.s.import_holding("alice",self.p,"DEMO",quantity,self.v)
        with self.assertRaises(ValueError):
            self.s.import_holding("alice",self.p,"UNKNOWN","1",self.v)
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM securities").fetchone()[0],0)

    def test_universe_target_is_not_a_cap(self):
        for n in range(401):
            with self.s.db: self.s.register(self.security(n))
            self.s.set_research(f"s{n}")
        self.assertEqual(self.s.db.execute("SELECT COUNT(*) FROM research").fetchone()[0],401)

    def test_monitoring_capacity_and_idempotency(self):
        for n in range(76):
            with self.s.db: self.s.register(self.security(n))
        for n in range(75): self.s.enable_monitoring(f"s{n}")
        self.s.enable_monitoring("s0")
        with self.assertRaises(sqlite3.IntegrityError): self.s.enable_monitoring("s75")
        self.s.import_holding("alice",self.p,"DEMO","2",self.v)
        self.assertEqual(len(self.s.holdings("alice",self.p)),1)

    def test_memory_scope(self):
        self.s.add_private_memory("alice","episodic","alice secret","run-a")
        self.s.add_private_memory("bob","procedural","bob secret","run-b")
        self.s.ingest_public_fact("public fact","fixture-public-source")
        results = self.s.retrieve_memory("alice")
        self.assertEqual([row[1] for row in results],["alice secret","public fact"])
        with self.assertRaises(PermissionError): self.s.retrieve_memory("unknown")

    def test_execution_gate(self):
        self.s.import_holding("alice",self.p,"DEMO","1",self.v)
        for mode in ("read_only","live","unexpected"):
            with self.assertRaises(PermissionError):
                self.s.record_paper_order("alice",self.p,"s0","1","10",mode)
        self.s.record_paper_order("alice",self.p,"s0","1","10","paper")
        self.assertEqual(self.s.holdings("alice",self.p),[("s0","1")])

    def test_scope_and_identity_validation(self):
        for kind,country in (("crypto","US"),("stock","GB")):
            with self.assertRaises(ValueError):
                with self.s.db: self.s.register(ValidatedSecurity("bad","BAD",kind,country,"fixture"))
        with self.s.db: self.s.register(self.security(0))
        with self.assertRaises(ValueError):
            with self.s.db: self.s.register(ValidatedSecurity("s0","NEW","stock","US","fixture"))
        with self.assertRaises(ValueError):
            with self.s.db: self.s.register(ValidatedSecurity("other","S0","stock","US","fixture"))

    def test_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/"test.sqlite3")
            first = Store(path)
            first.create_user("persistent")
            p = first.create_portfolio("persistent","Saved")
            first.close()
            second = Store(path)
            try: self.assertEqual(second.holdings("persistent",p),[])
            finally: second.close()

if __name__ == "__main__": unittest.main()
