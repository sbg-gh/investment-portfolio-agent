# M1 verification report

Local validation on 2026-11-06 with Python 3.11.12:

- Original M0: nine tests and demo passed before changes; the missing-owner privacy defect was reproduced separately.
- Red phase: twelve M1 acceptance tests failed/errored against unchanged M0; two harness tests failed before evaluation implementation.
- Additional red phase: trace-write failure left an imported holding committed. Atomic action/trace persistence fixed this regression.
- Green phase: 24 tests passed, covering original invariants, missing owners, forged identities, revocation/expiry, operator gates, current ownership, schema constraints, migration rollback, populated recovery, trace privacy and trace-failure rollback.
- Eight deterministic safety evaluation cases passed. Expected-answer mutation makes all eight fail; malformed suites are rejected.
- Offline authenticated demo passed. The 0.2.0 wheel installed into a fresh virtual environment; all 24 tests, the demo and all eight evaluation cases passed against the installed package.

This is local verification, not a GitHub CI result or an evaluation of financial/model performance. Python 3.12/3.13 and the full OS matrix remain CI checks. There is no learning-improvement claim.

The original ZIP remains an M0 artifact. MANIFEST.json in this working tree hashes the current source/documentation files except itself; it is regenerated for this change. No credentials, real portfolios or database files are included.

## M2a valuation verification

- Red phase: 13 valuation acceptance/harness tests failed against unchanged M1 before implementation.
- Green phase: all 37 tests pass locally on Python 3.11.12.
- Evaluation gates: 8 M1 safety cases and 7 M2a valuation cases pass; both harnesses detect deliberately wrong expected answers.
- The M2a tests cover schema v2-to-v3 migration and populated snapshot recovery, including cross-user denial and revoked recovery credentials.
- No market provider, exchange-calendar model, historical holdings ledger, return calculation or learning system is claimed. The GitHub matrix remains unexecuted until pushed.

- Installed-package check: the 0.3.0 wheel installed in a fresh virtual environment; all 37 tests, both evaluation gates and the demo passed without source-path imports.
