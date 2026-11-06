# Development handoff

Start by reading AGENTS.md and the decision register. Run the offline tests and demo. The package is a new repository root, not a patch to the ChatGPT project mirror.

Suggested next development request:

> Implement M2 import provenance and reconciliation from docs/MILESTONES.md using TDD. Write failing tests and evaluation cases for explicit holding removal, source/as-of import receipts, effective-dated aliases and idempotent retries. Preserve M1 boundaries and M2a valuation/evidence semantics. Keep real providers, live trading and model integration out of this slice.

For a fresh extraction only, initialize version control:

```sh
git init
git add .
git commit -m "Initialize local-first portfolio agent foundation"
```

Before public release select a license and review CI action versions under the organization's dependency policy. No remote repository, credentials or deployment has been created by this package.
