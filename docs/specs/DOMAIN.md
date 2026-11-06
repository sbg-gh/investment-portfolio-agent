# Domain and security-universe specification

## Identity and ownership

User owns portfolios; portfolio owns positions and paper records. Every externally callable operation obtains the user from trusted authentication, then checks ownership. IDs alone never authorize access. Future repositories must scope SQL reads and writes, caches, events, prompts and notifications. Shared instrument metadata is public; portfolio membership is private.

M0 uses opaque UUID portfolio IDs and explicit trusted caller IDs. It implements a holdings snapshot, not a transaction ledger. The M0 `import_holding` path replaces a holding's quantity silently; M2 adds `import_holding_with_provenance`, which records source and market as-of provenance per import, writes an import receipt and a holding event, and is idempotent on a caller-supplied `(portfolio_id, idempotency_key)`. Removal is now explicit and audited through `remove_holding`: removing a holding that does not exist raises a reconciliation error rather than a silent no-op. The legacy `import_holding` remains for back-compat. Positive finite decimal strings preserve precision. Money and quantities must never use binary floats.

## Security master and membership

Stable internal security ID is the primary identity; symbols are display/resolution aliases, not permanent identities. M0's one unique symbol per security is a simplified fixture constraint. M2 now implements effective-dated symbol aliases: `resolve_alias(symbol, as_of)` maps a symbol to an already-registered security over a half-open window `[effective_from, effective_to)` (newest effective_from wins). Resolution runs before the authoritative validator and never overrides or invents authoritative identity; on a miss, import falls through to the validator's auto-registration. Exchange/provider identifiers, currency, status, eligibility evidence, validation timestamps and corporate-action reconciliation remain M2 work.

Three independent concepts:

1. Security master: all validated supported instruments; no 400/75 limit.
2. Research universe: curated membership aiming at 400 global instruments; explicit membership and reasons; can exceed target with review.
3. Enhanced monitoring: at most 75 distinct global instruments with higher-cost collection/analysis. Membership does not follow automatically from research or holdings.

Every validated holding auto-registers in the master, including instruments outside research or enhanced monitoring. Baseline coverage includes all held instruments. A full enhanced tier never rejects a valid holding. M0 enforces 75 with a SQLite trigger inside transactions and idempotent insert behavior. M2 adds membership audit, admission queue, expiry and replacement policy.

Suggested M2 admission ordering: explicit operator priority, time-bounded material event, then risk-relevant demand using private aggregated scoring. Tie-break deterministically by stable ID. Do not evict automatically in M0. Do not expose demand counts or which users caused prioritization. Define reassessment cadence and hysteresis before automatic replacement.

## Future portfolio entities (M2/M6)

Accounts, cash balances, transactions, lots, position snapshots, prices/NAV observations, corporate actions and valuation snapshots. Each private record has an owner path, provenance and audit timestamp. Transactions have external idempotency keys. Snapshots record as-of time and reconciliation status. Keep original imports for authorized audit under retention policy.

Market observations carry security ID, value, currency, observation time, fetch time, source and price basis. US mutual funds use dated NAV; never describe stale NAV as a live quote or assume intraday execution. Missing prices remain missing rather than zero. Performance, fees, dividends and splits need explicit accounting conventions and fixtures before reporting returns.

## M0 schema mapping

`users`, `portfolios`, `holdings`: private ownership path. `securities`, `research`, `monitoring`: shared metadata. `memories`: explicit scope and owner constraints; public facts require public scope and NULL owner. Legacy ambiguous ownerless memories move to operator-only `memory_quarantine`. `paper_orders`: private recorded buy intent via portfolio ownership. See core.py for executable schema. Direct SQL and operator registration are trusted internal operations; future network interfaces must not expose the Store object wholesale.

M1 adds `credentials` and owner-scoped `execution_traces`; see ../M1.md for migration and access contracts.

M2a schema v3 adds shared market_observations and private valuation_snapshots. Snapshots embed current held quantities, market cutoff, source and evidence IDs. They are not historical position ledgers.

M2 schema v4 adds private `import_receipts` (one per applied import/removal, with source, market as-of, quantity, action and a `UNIQUE(portfolio_id, idempotency_key)` constraint enforcing idempotency), `holding_events` (an audit event per import/removal referencing its receipt), and shared `security_aliases` (effective-dated symbol-to-security mappings). `holdings` stays the current-state snapshot; audit history lives in the new tables. Transactions carry external idempotency keys as the DOMAIN ledger note requires.
