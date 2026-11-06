# Test-driven development and evaluation gates

For behavior changes: write an acceptance or regression test, run it against unchanged code and observe a meaningful failure, implement the smallest change that passes, then refactor with the suite green. Missing-feature imports are acceptable initial failures, but privacy and regression tests must assert behavior, not merely importability. Do not change expected outcomes to accommodate a defect. Record red/green evidence in the change description.

For this M1 change, an early implementation draft was set aside when TDD was requested. Twelve M1 acceptance tests were run against the unchanged M0 source: missing-owner isolation and future-version rejection failed, and missing M1 modules produced errors. Evaluation contract tests also failed before the harness existed. A subsequent trace-write-failure test failed against the draft because a holding had already committed; nested transactions fixed it. The final suite includes the original nine tests, thirteen M1 tests and two evaluation-harness tests.

## Run gates

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m portfolio_agent.cli demo
PYTHONPATH=src python -m portfolio_agent.cli eval
PYTHONPATH=src python -m portfolio_agent.cli eval-valuation
```

The `eval` command now runs both the M1 safety baseline and the M2 import-provenance baseline; it exits nonzero if either suite has any failed case.

CI installs the package, then runs all three gates on Python 3.11/3.12/3.13 and macOS/Linux. Verify installed-package evaluation too: its JSON fixtures must be bundled in the wheel. CI failures block readiness; no green GitHub run is claimed until it actually runs.

## Evaluation baseline

The versioned dataset lives in `src/portfolio_agent/eval_cases.json`. Each case starts from an isolated synthetic database and exercises the actual services. The report includes suite, policy version, canonical dataset hash, case-level expected/observed results and counts. All eight cases must pass. The harness tests deliberately replace expected answers and require every case to fail, demonstrating that results are not hard-coded passes. Empty datasets, duplicate IDs and unknown scenarios are rejected.

Cases cover cross-user access, forged identity, private retrieval, missing private owners, unauthorized public promotion, live-order denial, outside-universe holdings with a full enhanced tier, and authorized public sharing. `evals/baselines/m1-safety-v1.json` records the initial deterministic result.

These are public development/regression fixtures, NOT held-out learning evaluations. Passing does not establish financial correctness, model quality, or adaptation. In M2 add deterministic valuation/NAV/staleness datasets. In M4 add fixed tool-selection/evidence cases. In M5 maintain separately frozen held-out cases; compare no-memory, retrieval-only and approved-procedure variants without allowing evaluation outcomes into candidate memory. Follow MEMORY.md for predeclared metrics, uncertainty and promotion gates.

## M2a TDD evidence and valuation gate

Thirteen new acceptance tests were run against the M1 source before implementation; all failed with missing valuation APIs, observations/schema or evaluation module. After implementation, the full suite has 37 passing tests. The original 24 tests remain intact. Coverage includes exact decimal calculations under reduced ambient precision, price/NAV validation, freshness boundaries, late-fetched/future observations, source isolation, repeated/conflicting observations, cross-user access, snapshot replay after holdings changes, restore, v2 upgrade and trace-failure rollback.

Run `PYTHONPATH=src python -m portfolio_agent.cli eval-valuation` in addition to the M1 gates. Seven dataset-driven cases live in `src/portfolio_agent/valuation_cases.json`; the expected answers are separate from the calculation. The harness reports a dataset hash and case-level observed answers; mutation of expected answers makes all cases fail. `evals/baselines/m2-valuation-v1.json` records the initial baseline. CI executes the installed-package valuation command so missing packaged fixtures fail the build.

## M2 import-provenance TDD evidence and eval gate

Each new behavior was driven red-first against the unchanged v3 source before implementation. The v4 migration tests failed on `user_version` (3 != 4) and missing audit tables; the alias and import tests failed on missing `register_alias`/`resolve_alias`/`import_holding_with_provenance`/`remove_holding`; the service tests failed on the missing `PortfolioService` methods; and the import-eval harness tests failed on the missing `import_evaluation` module. After implementation the full suite has 68 passing tests (the previous 37 remain intact, plus 31 new across `tests/test_migrations.py`, `tests/test_aliases.py`, `tests/test_import_provenance.py`, `tests/test_import_provenance_service.py` and `tests/test_import_evals.py`). Coverage includes v3→v4 upgrade preservation, broken-upgrade rollback to v3, v4 backup/restore with credential revocation, alias window/precedence and no-auto-register guarantees, receipt/event provenance, idempotent retries, audited removal and reconciliation errors, cross-user denial, forged-context denial and failed-action trace rollback.

Four dataset-driven cases live in `src/portfolio_agent/import_cases.json` (scenarios `import_receipt`, `explicit_removal`, `effective_dated_alias`, `idempotent_retry`); expected answers are separate from the logic, and mutating any expected answer makes `eval` exit nonzero. These are deterministic regression/safety fixtures, kept distinct from held-out learning evaluation (no `learning_improvement` key); passing establishes no adaptation claim.
