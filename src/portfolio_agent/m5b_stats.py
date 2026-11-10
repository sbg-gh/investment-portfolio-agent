"""M5b pre-registered paired statistics (stdlib only, deterministic).

Constants come from m5b_prereg.json, committed before any result existed. The verdict is
always RECOMPUTED here from discordant counts; callers cannot supply one.
"""
import hashlib
import json
import math
import random
from pathlib import Path

PREREG_PATH = Path(__file__).with_name("m5b_prereg.json")


def prereg_sha256(data) -> str:
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_prereg(path=None):
    """Return (prereg dict, canonical-json sha256)."""
    with open(path or PREREG_PATH, encoding="utf-8") as handle:
        data = json.load(handle)
    return data, prereg_sha256(data)


def _tail_ge(x: int, n: int, p: float) -> float:
    """P(X >= x) for X ~ Binomial(n, p)."""
    return sum(math.comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(x, n + 1))


def _tail_le(x: int, n: int, p: float) -> float:
    """P(X <= x) for X ~ Binomial(n, p)."""
    return sum(math.comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(0, x + 1))


def clopper_pearson(x: int, n: int, alpha: float = 0.05):
    """Exact two-sided (1 - alpha) Clopper-Pearson interval by bisection on binomial tails."""
    if not (isinstance(x, int) and isinstance(n, int)) or n < 0 or x < 0 or x > n:
        raise ValueError("Require integers 0 <= x <= n")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be in (0, 1)")
    if n == 0:
        return 0.0, 1.0
    half = alpha / 2
    lower = 0.0
    if x > 0:
        lo, hi = 0.0, 1.0  # P(X >= x | p) increases with p
        for _ in range(200):
            mid = (lo + hi) / 2
            if _tail_ge(x, n, mid) < half:
                lo = mid
            else:
                hi = mid
        lower = (lo + hi) / 2
    upper = 1.0
    if x < n:
        lo, hi = 0.0, 1.0  # P(X <= x | p) decreases with p
        for _ in range(200):
            mid = (lo + hi) / 2
            if _tail_le(x, n, mid) > half:
                lo = mid
            else:
                hi = mid
        upper = (lo + hi) / 2
    return lower, upper


def paired_counts(a, b):
    """Return (b_only, c_only): cases only `a` solved and cases only `b` solved."""
    a, b = list(a), list(b)
    if len(a) != len(b):
        raise ValueError("Paired outcomes must have equal length")
    only_a = sum(1 for x, y in zip(a, b) if x and not y)
    only_b = sum(1 for x, y in zip(a, b) if y and not x)
    return only_a, only_b


def _pair(value):
    if isinstance(value, dict):
        if set(value) != {"b", "c"}:
            raise ValueError("Contrast counts take only b and c; verdicts are computed here")
        return value["b"], value["c"]
    b, c = value
    return b, c


def decide(contrast_counts, n_target: int, prereg) -> dict:
    """Apply the pre-registered decision rule to discordant counts per contrast."""
    names = [c["name"] for c in prereg["contrasts"]]
    if set(contrast_counts) != set(names):
        raise ValueError("Counts required for exactly the pre-registered contrasts")
    alpha = prereg["alpha"]
    contrasts = {}
    for name in names:
        b, c = _pair(contrast_counts[name])
        if not (isinstance(b, int) and isinstance(c, int)) or b < 0 or c < 0 or b + c > n_target:
            raise ValueError("Invalid discordant counts")
        lower, upper = clopper_pearson(b, b + c, alpha)
        contrasts[name] = {"b": b, "c": c, "n": n_target, "lower": lower, "upper": upper,
                           "risk_difference": (b - c) / n_target if n_target else 0.0}
    if n_target < prereg["n_min"]:
        verdict, reason = "inconclusive", "too_small"
    elif any(e["b"] + e["c"] == 0 for e in contrasts.values()):
        verdict, reason = "inconclusive", "no_discordant_pairs"
    elif any(e["upper"] < 0.5 for e in contrasts.values()):
        verdict, reason = "significant_regression", "interval_below_half"
    elif all(e["lower"] > 0.5 for e in contrasts.values()):
        verdict, reason = "significant_improvement", "both_intervals_above_half"
    else:
        verdict, reason = "inconclusive", "interval_contains_half"
    return {"verdict": verdict, "reason": reason, "contrasts": contrasts}


def paired_bootstrap(diffs, seed: int, b: int, alpha: float = 0.05) -> dict:
    """Seeded percentile bootstrap of the mean paired difference (DESCRIPTIVE only)."""
    diffs = list(diffs)
    if not diffs:
        raise ValueError("Bootstrap needs at least one paired difference")
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(sum(rng.choice(diffs) for _ in range(n)) / n for _ in range(b))
    lo_index = int(math.floor((alpha / 2) * (b - 1)))
    hi_index = int(math.ceil((1 - alpha / 2) * (b - 1)))
    return {"mean": sum(diffs) / n, "lower": means[lo_index], "upper": means[hi_index],
            "seed": seed, "b": b, "alpha": alpha}
