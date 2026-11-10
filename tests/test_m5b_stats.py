import copy
import hashlib
import json
import unittest
from pathlib import Path

from portfolio_agent import m5b_stats as stats


class ClopperPearsonTests(unittest.TestCase):
    def test_all_successes_lower_bound_matches_closed_form(self):
        for n, expected in ((6, 0.5407), (5, 0.4782)):
            lo, hi = stats.clopper_pearson(n, n, 0.05)
            self.assertAlmostEqual(lo, expected, delta=1e-4)
            self.assertAlmostEqual(lo, 0.025 ** (1 / n), delta=1e-9)
            self.assertEqual(hi, 1.0)

    def test_zero_successes_upper_bound_matches_closed_form(self):
        for n in (5, 6, 10):
            lo, hi = stats.clopper_pearson(0, n, 0.05)
            self.assertEqual(lo, 0.0)
            self.assertAlmostEqual(hi, 1 - 0.025 ** (1 / n), delta=1e-9)

    def test_five_of_ten_hand_value(self):
        lo, hi = stats.clopper_pearson(5, 10, 0.05)
        self.assertAlmostEqual(lo, 0.1871, delta=1e-4)
        self.assertAlmostEqual(hi, 0.8129, delta=1e-4)

    def test_symmetry(self):
        lo, hi = stats.clopper_pearson(3, 11, 0.05)
        lo2, hi2 = stats.clopper_pearson(8, 11, 0.05)
        self.assertAlmostEqual(lo, 1 - hi2, delta=1e-9)
        self.assertAlmostEqual(hi, 1 - lo2, delta=1e-9)

    def test_empty_sample_is_fully_uncertain(self):
        self.assertEqual(stats.clopper_pearson(0, 0, 0.05), (0.0, 1.0))

    def test_invalid_inputs_rejected(self):
        for args in ((-1, 5, 0.05), (6, 5, 0.05), (1, 5, 0.0), (1, 5, 1.0)):
            with self.assertRaises(ValueError):
                stats.clopper_pearson(*args)


class PairedCountsTests(unittest.TestCase):
    def test_discordant_counts(self):
        a = [1, 1, 0, 0, 1, 0]
        b = [1, 0, 0, 1, 0, 0]
        self.assertEqual(stats.paired_counts(a, b), (2, 1))

    def test_length_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            stats.paired_counts([1], [1, 0])


class DecideTests(unittest.TestCase):
    def setUp(self):
        self.prereg, _ = stats.load_prereg()

    def _decide(self, b1, c1, b2=None, c2=None, n=32):
        b2 = b1 if b2 is None else b2
        c2 = c1 if c2 is None else c2
        return stats.decide({"approved_vs_retrieval_only": (b1, c1),
                             "approved_vs_no_memory": (b2, c2)}, n, self.prereg)

    def test_significant_improvement(self):
        result = self._decide(24, 0)
        self.assertEqual(result["verdict"], "significant_improvement")
        for entry in result["contrasts"].values():
            self.assertGreater(entry["lower"], 0.5)
            self.assertEqual((entry["b"], entry["c"], entry["n"]), (24, 0, 32))
            self.assertAlmostEqual(entry["risk_difference"], 24 / 32)

    def test_small_effect_is_inconclusive(self):
        self.assertEqual(self._decide(5, 0)["verdict"], "inconclusive")

    def test_no_effect_is_inconclusive(self):
        self.assertEqual(self._decide(3, 3)["verdict"], "inconclusive")

    def test_no_discordant_pairs_is_inconclusive(self):
        self.assertEqual(self._decide(0, 0)["verdict"], "inconclusive")

    def test_significant_regression(self):
        self.assertEqual(self._decide(0, 10)["verdict"], "significant_regression")

    def test_too_small_is_inconclusive_even_with_large_b(self):
        result = self._decide(18, 0, n=19)
        self.assertEqual(result["verdict"], "inconclusive")
        self.assertEqual(result["reason"], "too_small")

    def test_one_contrast_failing_is_inconclusive(self):
        self.assertEqual(self._decide(24, 0, 5, 0)["verdict"], "inconclusive")

    def test_regression_on_one_contrast_wins_over_improvement_on_other(self):
        self.assertEqual(self._decide(24, 0, 0, 10)["verdict"], "significant_regression")

    def test_caller_cannot_supply_a_verdict(self):
        with self.assertRaises(ValueError):
            stats.decide({"approved_vs_retrieval_only": {"b": 24, "c": 0, "verdict": "x"},
                          "approved_vs_no_memory": (24, 0)}, 32, self.prereg)

    def test_missing_contrast_rejected(self):
        with self.assertRaises(ValueError):
            stats.decide({"approved_vs_retrieval_only": (24, 0)}, 32, self.prereg)

    def test_counts_exceeding_n_rejected(self):
        with self.assertRaises(ValueError):
            self._decide(30, 5)


class BootstrapTests(unittest.TestCase):
    def test_constant_differences_give_degenerate_interval(self):
        result = stats.paired_bootstrap([2.0] * 8, seed=1, b=200, alpha=0.05)
        self.assertEqual((result["lower"], result["upper"], result["mean"]), (2.0, 2.0, 2.0))

    def test_same_seed_is_identical(self):
        diffs = [1.0, -2.0, 3.0, 0.5, 4.0, -1.0]
        self.assertEqual(stats.paired_bootstrap(diffs, 7, 300, 0.05),
                         stats.paired_bootstrap(diffs, 7, 300, 0.05))

    def test_interval_is_ordered_and_contains_mean_region(self):
        diffs = [1.0, -2.0, 3.0, 0.5, 4.0, -1.0]
        r = stats.paired_bootstrap(diffs, 7, 300, 0.05)
        self.assertLessEqual(r["lower"], r["upper"])

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            stats.paired_bootstrap([], 1, 10, 0.05)


class PreregLoaderTests(unittest.TestCase):
    def test_hash_is_sha256_of_canonical_json(self):
        data, digest = stats.load_prereg()
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        self.assertEqual(digest, hashlib.sha256(canonical.encode("utf-8")).hexdigest())

    def test_altering_any_value_changes_hash(self):
        data, digest = stats.load_prereg()
        for key, value in (("seed", 1), ("alpha", 0.1), ("n_target", 33)):
            changed = copy.deepcopy(data)
            changed[key] = value
            self.assertNotEqual(stats.prereg_sha256(changed), digest)

    def test_registered_constants(self):
        data, _ = stats.load_prereg()
        self.assertEqual((data["seed"], data["repeats"], data["alpha"], data["bootstrap_b"],
                          data["n_min"], data["n_target"]), (20260601, 3, 0.05, 2000, 20, 32))
        self.assertEqual(sum(data["family_counts"][f] for f in data["target_families"]), 32)

    def test_file_is_packaged(self):
        pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
        self.assertIn("m5b_prereg.json", pyproject)


if __name__ == "__main__":
    unittest.main()
