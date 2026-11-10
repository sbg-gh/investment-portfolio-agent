import unittest

from portfolio_agent import leakage
from portfolio_agent.core import Store


class LeakageGuardTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.addCleanup(self.store.close)
        self.store.create_user("alice")
        self.user = "alice"
        self.canary = leakage.make_canary("case-1")

    def _count(self, table):
        return self.store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_canary_format(self):
        self.assertRegex(self.canary, r"^HELDOUT-[0-9a-f]{12}$")
        self.assertEqual(self.canary, leakage.make_canary("case-1"))
        self.assertNotEqual(self.canary, leakage.make_canary("case-2"))

    def test_assert_no_held_out_detects_canary_and_task_type(self):
        leakage.assert_no_held_out("clean text", None, 5)
        with self.assertRaises(leakage.HeldOutLeakageError):
            leakage.assert_no_held_out("fine", f"x {self.canary} y")
        with self.assertRaises(leakage.HeldOutLeakageError):
            leakage.assert_no_held_out("heldout:fund_value_question")
        self.assertTrue(issubclass(leakage.HeldOutLeakageError, ValueError))

    def test_memory_body_refused_and_nothing_written(self):
        before = self._count("memories")
        with self.assertRaises(leakage.HeldOutLeakageError):
            self.store.add_private_memory(self.user, "semantic", f"note {self.canary}", "test")
        self.assertEqual(self._count("memories"), before)

    def test_memory_other_fields_refused(self):
        for kw in ({"payload": self.canary}, {"applicable_conditions": self.canary}):
            with self.assertRaises(leakage.HeldOutLeakageError):
                self.store.add_private_memory(self.user, "semantic", "clean", "test", **kw)
        with self.assertRaises(leakage.HeldOutLeakageError):
            self.store.add_private_memory(self.user, "semantic", "clean", self.canary)
        self.assertEqual(self._count("memories"), 0)

    def test_run_feedback_and_task_type_refused(self):
        base = dict(request_id="r1", task_type="valuation:x", policy_version="p",
                    tool_sequence=["a"], outcome="success")
        for override in ({"feedback": f"fb {self.canary}"},
                         {"task_type": "heldout:fund_value_question"},
                         {"tool_sequence": [self.canary]},
                         {"model_version": self.canary}):
            with self.assertRaises(leakage.HeldOutLeakageError):
                self.store.record_run(self.user, **{**base, **override})
        self.assertEqual(self._count("run_records"), 0)

    def test_public_fact_refused(self):
        with self.assertRaises(leakage.HeldOutLeakageError):
            self.store.ingest_public_fact(f"fact {self.canary}", "public")
        self.assertEqual(self._count("memories"), 0)

    def test_clean_writes_still_succeed(self):
        self.store.add_private_memory(self.user, "semantic", "clean", "test")
        self.store.record_run(self.user, request_id="r2", task_type="valuation:etf",
                              policy_version="p", tool_sequence=["a"], outcome="success",
                              feedback="fine")
        self.store.ingest_public_fact("public fact", "public-source")
        self.assertEqual(leakage.scan_store(self.store), [])

    def test_scan_store_finds_bypass_writes(self):
        db = self.store.db
        db.execute("INSERT INTO memories(id,user_id,scope,kind,body,source) "
                   "VALUES ('m-bypass',?,'private','semantic',?,'raw')", (self.user, self.canary))
        db.execute("INSERT INTO run_records(id,user_id,request_id,task_type,policy_version,"
                   "tool_sequence,outcome,feedback) VALUES ('r-bypass',?,'rq','heldout:x','p','[]',"
                   "'success',?)", (self.user, self.canary))
        violations = leakage.scan_store(self.store)
        self.assertTrue(any("m-bypass" in str(v) for v in violations))
        self.assertTrue(any("r-bypass" in str(v) for v in violations))


if __name__ == "__main__":
    unittest.main()
