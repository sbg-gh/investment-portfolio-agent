"""M5b held-out leakage guard.

Held-out evaluation cases carry a canary ('HELDOUT-<12 hex>') and a 'heldout' task_type
prefix. Nothing carrying either may enter memory, run records or public facts.
"""
import hashlib
import re

CANARY_RE = re.compile(r"HELDOUT-[0-9a-f]{12}")
TASK_PREFIX_RE = re.compile(r"^heldout[:_\-]", re.IGNORECASE)


class HeldOutLeakageError(ValueError):
    """A held-out case, canary or task type reached a memory/run write path."""


def make_canary(case_id: str) -> str:
    return "HELDOUT-" + hashlib.sha256(str(case_id).encode("utf-8")).hexdigest()[:12]


def _leaks(text) -> bool:
    if not isinstance(text, str):
        return False
    return bool(CANARY_RE.search(text)) or bool(TASK_PREFIX_RE.match(text))


def assert_no_held_out(*texts) -> None:
    """Raise HeldOutLeakageError if any text carries a canary or heldout task_type prefix."""
    for text in texts:
        if _leaks(text):
            raise HeldOutLeakageError("Held-out evaluation content may not enter memory")


def scan_store(store) -> list:
    """Return violations (table, row id, column) across stored memory and run content."""
    db = store.db
    checks = (
        ("memories", "id", ("body", "source", "payload", "applicable_conditions")),
        ("run_records", "id", ("task_type", "tool_sequence", "feedback", "model_version")),
        ("memory_deletions", "id", ("reason",)),
    )
    violations = []
    for table, key, columns in checks:
        for row in db.execute(f"SELECT {key},{','.join(columns)} FROM {table}").fetchall():
            for column, value in zip(columns, row[1:]):
                if _leaks(value):
                    violations.append(f"{table}:{row[0]}:{column}")
    return violations
