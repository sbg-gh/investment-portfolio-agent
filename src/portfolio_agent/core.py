"""Deterministic domain services. Trusted in-process caller supplies user identity.

Internal persistence API, NOT an authentication boundary. Expose application services only.

The M5a memory additions (richer record contract, owner-scoped run capture, scoped keyword
retrieval, deletion lineage/supersede) are SUBSTRATE ONLY and make NO learning/adaptation
claim. Extraction, candidate review, held-out evaluation and versioned-procedure promotion
or rollback are M5b. Retrieving a prior note or explanation is not behavioral learning.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import sqlite3
from typing import Protocol
from uuid import uuid4

RESEARCH_TARGET = 400
MONITORING_CAPACITY = 75
# M5a deterministic keyword-retrieval ranking weights (keyword-only this slice; no vectors).
KEYWORD_WEIGHT = 100
TYPE_WEIGHT = 10
PORTFOLIO_WEIGHT = 5


def _descending_text(value: str):
    """Sort key making ISO-8601 text sort DESC (newer first) under an ascending sort."""
    return tuple(-ord(ch) for ch in (value or ''))
# Max distinct securities held pending beyond the active tier before a proposal is rejected.
ADMISSION_QUEUE_CAPACITY = 25
SUPPORTED_KINDS = {"stock", "etf", "mutual_fund"}

@dataclass(frozen=True)
class ValidatedSecurity:
    security_id: str
    symbol: str
    kind: str
    country: str
    source: str

class SecurityValidator(Protocol):
    def resolve(self, symbol: str) -> ValidatedSecurity: ...

class FixtureValidator:
    """Offline fixtures only; never use this as real instrument validation."""
    def __init__(self, records: dict[str, ValidatedSecurity]):
        self.records = records

    def resolve(self, symbol: str) -> ValidatedSecurity:
        try:
            return self.records[symbol.strip().upper()]
        except KeyError as exc:
            raise ValueError("Unknown symbol; authoritative validation required") from exc

def positive(value: str) -> Decimal:
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Expected a positive finite decimal") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError("Expected a positive finite decimal")
    return number

class Store:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        try:
            from .migrations import migrate
            migrate(self.db)
        except BaseException:
            self.db.close()
            raise

    @contextmanager
    def transaction(self):
        nested = self.db.in_transaction
        savepoint = 'sp_' + uuid4().hex
        self.db.execute(f'SAVEPOINT {savepoint}' if nested else 'BEGIN IMMEDIATE')
        try:
            yield
            if nested:
                self.db.execute(f'RELEASE SAVEPOINT {savepoint}')
            else:
                self.db.commit()
        except BaseException:
            if nested:
                self.db.execute(f'ROLLBACK TO SAVEPOINT {savepoint}')
                self.db.execute(f'RELEASE SAVEPOINT {savepoint}')
            else:
                self.db.rollback()
            raise

    def close(self):
        self.db.close()

    def create_user(self, user_id: str):
        if not user_id.strip():
            raise ValueError("Empty user identity")
        with self.transaction():
            self.db.execute("INSERT INTO users VALUES (?)", (user_id,))

    def create_portfolio(self, user_id: str, name: str) -> str:
        portfolio_id = str(uuid4())
        with self.transaction():
            self.db.execute("INSERT INTO portfolios VALUES (?,?,?)", (portfolio_id,user_id,name))
        return portfolio_id

    def _authorize(self, user_id: str, portfolio_id: str):
        if not self.db.execute("SELECT 1 FROM portfolios WHERE id=? AND user_id=?",
                               (portfolio_id,user_id)).fetchone():
            raise PermissionError("Portfolio unavailable")

    def register(self, security: ValidatedSecurity):
        if security.country != "US" or security.kind not in SUPPORTED_KINDS:
            raise ValueError("Only US stocks, ETFs and mutual funds are supported")
        if not all((security.security_id.strip(), security.symbol.strip(), security.source.strip())):
            raise ValueError("Identity and validation provenance required")
        row = self.db.execute("SELECT symbol,kind,country,source FROM securities WHERE id=?",
                              (security.security_id,)).fetchone()
        values = (security.symbol,security.kind,security.country,security.source)
        if row is not None and row != values:
            raise ValueError("Conflicting security identity; reconciliation required")
        self.db.execute("INSERT OR IGNORE INTO securities(id,symbol,kind,country,source) VALUES (?,?,?,?,?)",
                        (security.security_id,*values))
        if not self.db.execute("SELECT 1 FROM securities WHERE id=?",(security.security_id,)).fetchone():
            raise ValueError("Symbol conflicts with an existing security")

    def _grant_baseline_coverage(self, security_id: str, since: str):
        # Deterministic: a held security retains coverage, independent of monitoring.
        self.db.execute("INSERT OR IGNORE INTO baseline_coverage(security_id,covered,since) VALUES (?,1,?)",
                        (security_id, since))

    def _sync_baseline_coverage(self, security_id: str):
        # Coverage-set == held-set: drop the row once no portfolio holds the security.
        held = self.db.execute("SELECT 1 FROM holdings WHERE security_id=? LIMIT 1",
                               (security_id,)).fetchone()
        if not held:
            self.db.execute("DELETE FROM baseline_coverage WHERE security_id=?", (security_id,))

    def import_holding(self, user_id: str, portfolio_id: str, symbol: str,
                       quantity: str, validator: SecurityValidator):
        from .valuation import utc
        self._authorize(user_id,portfolio_id)
        qty = positive(quantity)
        security = validator.resolve(symbol)
        now = utc(datetime.now(timezone.utc)).isoformat()
        with self.transaction():
            self.register(security)
            self.db.execute("INSERT INTO holdings VALUES (?,?,?) ON CONFLICT(portfolio_id,security_id) "
                            "DO UPDATE SET quantity=excluded.quantity",(portfolio_id,security.security_id,str(qty)))
            self._grant_baseline_coverage(security.security_id, now)
        return security.security_id

    def register_alias(self, symbol: str, security_id: str, source: str,
                       effective_from, effective_to=None):
        """Trusted internal: map a symbol to an ALREADY-registered security over a window.

        Never overrides or invents authoritative identity. Window is [effective_from, effective_to).
        """
        from .valuation import utc
        if not symbol.strip() or not source.strip():
            raise ValueError("Alias symbol and provenance required")
        if not self.db.execute("SELECT 1 FROM securities WHERE id=?", (security_id,)).fetchone():
            raise ValueError("Alias target security is not registered")
        start = utc(effective_from).isoformat()
        end = None if effective_to is None else utc(effective_to).isoformat()
        if end is not None and end <= start:
            raise ValueError("Alias effective_to must be after effective_from")
        with self.transaction():
            self.db.execute("INSERT INTO security_aliases(id,symbol,security_id,source,effective_from,effective_to) "
                            "VALUES (?,?,?,?,?,?)", (str(uuid4()), symbol, security_id, source, start, end))

    def resolve_alias(self, symbol: str, as_of) -> str | None:
        """Resolve a symbol to a security_id as of a date, or None. Never auto-registers."""
        from .valuation import utc
        at = utc(as_of).isoformat()
        row = self.db.execute(
            "SELECT security_id FROM security_aliases WHERE symbol=? AND effective_from<=? "
            "AND (effective_to IS NULL OR ?<effective_to) ORDER BY effective_from DESC, rowid DESC LIMIT 1",
            (symbol, at, at)).fetchone()
        return row[0] if row else None

    def _existing_receipt(self, portfolio_id: str, idempotency_key: str):
        return self.db.execute("SELECT security_id FROM import_receipts WHERE portfolio_id=? AND idempotency_key=?",
                               (portfolio_id, idempotency_key)).fetchone()

    def import_holding_with_provenance(self, user_id: str, portfolio_id: str, symbol: str,
                                       quantity: str, validator: SecurityValidator, *,
                                       source: str, market_as_of, idempotency_key: str):
        """Audited, idempotent, provenance-bearing import. Identity via alias first, else validator."""
        from .valuation import utc
        self._authorize(user_id, portfolio_id)
        if not source.strip() or not idempotency_key.strip():
            raise ValueError("Import source and idempotency key required")
        qty = positive(quantity)
        cutoff = utc(market_as_of).isoformat()
        existing = self._existing_receipt(portfolio_id, idempotency_key)
        if existing is not None:
            return existing[0]
        security_id = self.resolve_alias(symbol, market_as_of)
        with self.transaction():
            if security_id is None:
                security = validator.resolve(symbol)
                self.register(security)
                security_id = security.security_id
            self.db.execute("INSERT INTO holdings VALUES (?,?,?) ON CONFLICT(portfolio_id,security_id) "
                            "DO UPDATE SET quantity=excluded.quantity", (portfolio_id, security_id, str(qty)))
            self._grant_baseline_coverage(security_id, cutoff)
            receipt_id = str(uuid4())
            self.db.execute("INSERT INTO import_receipts(id,portfolio_id,security_id,idempotency_key,source,"
                            "market_as_of,quantity,action) VALUES (?,?,?,?,?,?,?,'upsert')",
                            (receipt_id, portfolio_id, security_id, idempotency_key, source, cutoff, str(qty)))
            self.db.execute("INSERT INTO holding_events(id,portfolio_id,security_id,event,quantity,receipt_id) "
                            "VALUES (?,?,?,'import',?,?)",
                            (str(uuid4()), portfolio_id, security_id, str(qty), receipt_id))
        return security_id

    def remove_holding(self, user_id: str, portfolio_id: str, security_id: str, *,
                       source: str, idempotency_key: str, market_as_of):
        """Explicit audited removal. No silent no-op overwrite; a missing holding is a reconciliation."""
        from .valuation import utc
        self._authorize(user_id, portfolio_id)
        if not source.strip() or not idempotency_key.strip():
            raise ValueError("Removal source and idempotency key required")
        cutoff = utc(market_as_of).isoformat()
        existing = self._existing_receipt(portfolio_id, idempotency_key)
        if existing is not None:
            return existing[0]
        if not self.db.execute("SELECT 1 FROM holdings WHERE portfolio_id=? AND security_id=?",
                               (portfolio_id, security_id)).fetchone():
            raise ValueError("Nothing to remove; reconciliation required")
        with self.transaction():
            self.db.execute("DELETE FROM holdings WHERE portfolio_id=? AND security_id=?",
                            (portfolio_id, security_id))
            self._sync_baseline_coverage(security_id)
            receipt_id = str(uuid4())
            self.db.execute("INSERT INTO import_receipts(id,portfolio_id,security_id,idempotency_key,source,"
                            "market_as_of,quantity,action) VALUES (?,?,?,?,?,?,NULL,'remove')",
                            (receipt_id, portfolio_id, security_id, idempotency_key, source, cutoff))
            self.db.execute("INSERT INTO holding_events(id,portfolio_id,security_id,event,quantity,receipt_id) "
                            "VALUES (?,?,?,'remove',NULL,?)",
                            (str(uuid4()), portfolio_id, security_id, receipt_id))
        return security_id

    def receipts(self, user_id: str, portfolio_id: str):
        self._authorize(user_id, portfolio_id)
        return self.db.execute(
            "SELECT id,security_id,source,market_as_of,quantity,action FROM import_receipts "
            "WHERE portfolio_id=? ORDER BY rowid", (portfolio_id,)).fetchall()

    def holdings(self, user_id: str, portfolio_id: str):
        self._authorize(user_id,portfolio_id)
        return self.db.execute("SELECT security_id,quantity FROM holdings WHERE portfolio_id=? ORDER BY security_id",
                               (portfolio_id,)).fetchall()

    def baseline_coverage(self, security_id: str):
        """Return the covered flag (0/1) for a security, or None when uncovered/unknown.

        Public market-coverage status; not owner-scoped and carries no private context.
        """
        row = self.db.execute("SELECT covered FROM baseline_coverage WHERE security_id=?",
                              (security_id,)).fetchone()
        return None if row is None else row[0]

    def reconcile_baseline_coverage(self):
        """Assert covered-set == distinct-held-securities-set; return discrepancies.

        Rows are (security_id, reason) where reason is 'held_without_coverage' (a held
        security missing covered=1) or 'coverage_without_holder' (a covered row for an
        unheld security). An empty list means coverage is consistent.
        """
        held = {row[0] for row in self.db.execute(
            "SELECT DISTINCT security_id FROM holdings").fetchall()}
        covered = {row[0] for row in self.db.execute(
            "SELECT security_id FROM baseline_coverage WHERE covered=1").fetchall()}
        rows = []
        for security_id in sorted(held - covered):
            rows.append((security_id, 'held_without_coverage'))
        for security_id in sorted(covered - held):
            rows.append((security_id, 'coverage_without_holder'))
        return rows

    def record_transaction(self, user_id: str, portfolio_id: str, security_id: str, side: str,
                           quantity: str, *, source: str, idempotency_key: str, occurred_at) -> str:
        """Append one signed buy/sell event. Idempotent on (portfolio_id, idempotency_key).

        Decimal quantities only; never auto-mutates holdings. Reconciliation is a separate read.
        """
        from .valuation import utc
        self._authorize(user_id, portfolio_id)
        if side not in {"buy", "sell"}:
            raise ValueError("Transaction side must be buy or sell")
        qty = positive(quantity)
        if not source.strip() or not idempotency_key.strip():
            raise ValueError("Transaction source and idempotency key required")
        when = utc(occurred_at).isoformat()
        existing = self.db.execute(
            "SELECT id FROM transactions WHERE portfolio_id=? AND idempotency_key=?",
            (portfolio_id, idempotency_key)).fetchone()
        if existing is not None:
            return existing[0]
        transaction_id = str(uuid4())
        with self.transaction():
            self.db.execute(
                "INSERT INTO transactions(id,portfolio_id,security_id,side,quantity,source,idempotency_key,occurred_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (transaction_id, portfolio_id, security_id, side, str(qty), source, idempotency_key, when))
        return transaction_id

    def transactions(self, user_id: str, portfolio_id: str):
        self._authorize(user_id, portfolio_id)
        return self.db.execute(
            "SELECT id,security_id,side,quantity,source,occurred_at FROM transactions "
            "WHERE portfolio_id=? ORDER BY rowid", (portfolio_id,)).fetchall()

    def reconcile_portfolio(self, user_id: str, portfolio_id: str):
        """Compare the signed ledger sum per security to the holdings snapshot quantity.

        Returns (security_id, ledger_sum_text, holding_qty_text, reconciled_bool) rows;
        flags divergence without ever coercing the snapshot.
        """
        self._authorize(user_id, portfolio_id)
        ledger: dict[str, Decimal] = {}
        for security_id, side, quantity in self.db.execute(
                "SELECT security_id,side,quantity FROM transactions WHERE portfolio_id=? ORDER BY rowid",
                (portfolio_id,)).fetchall():
            signed = Decimal(quantity) if side == "buy" else -Decimal(quantity)
            ledger[security_id] = ledger.get(security_id, Decimal(0)) + signed
        snapshot = {security_id: Decimal(quantity) for security_id, quantity in self.db.execute(
            "SELECT security_id,quantity FROM holdings WHERE portfolio_id=?", (portfolio_id,)).fetchall()}
        rows = []
        for security_id in sorted(set(ledger) | set(snapshot)):
            ledger_sum = ledger.get(security_id, Decimal(0))
            holding_qty = snapshot.get(security_id)
            reconciled = holding_qty is not None and ledger_sum == holding_qty
            rows.append((security_id, str(ledger_sum),
                         None if holding_qty is None else str(holding_qty), reconciled))
        return rows

    def set_research(self, security_id: str):
        # 400 is a planning target, never a hard master-list or holdings limit.
        with self.transaction():
            self.db.execute("INSERT OR IGNORE INTO research VALUES (?)",(security_id,))

    def enable_monitoring(self, security_id: str):
        # Operator-only shared service: capacity counts distinct instruments globally.
        with self.transaction():
            self.db.execute("INSERT OR IGNORE INTO monitoring VALUES (?)",(security_id,))

    def propose_enhancement(self, security_id: str, *, reason: str, operator: str, now,
                            queue_capacity: int = ADMISSION_QUEUE_CAPACITY) -> str:
        """Admit a distinct security to enhanced monitoring, queue it, or reject it.

        State machine (no auto-eviction): first MONITORING_CAPACITY distinct securities are
        'admitted' into the active monitoring set; the next are 'queued' FIFO up to
        queue_capacity; beyond that the proposal is 'rejected' and nothing is written.
        Re-proposing an already admitted/queued security is idempotent — it returns the
        current state and adds no duplicate row. Deterministic integer counts; no Decimal.
        """
        from .valuation import utc
        if not reason.strip() or not str(operator).strip():
            raise ValueError("Admission reason and operator required")
        existing = self.db.execute(
            "SELECT state FROM monitoring_admissions WHERE security_id=?", (security_id,)).fetchone()
        if existing is not None:
            return existing[0]
        proposed_at = utc(now).isoformat()
        with self.transaction():
            active = self.db.execute("SELECT COUNT(*) FROM monitoring").fetchone()[0]
            if active < MONITORING_CAPACITY:
                self.db.execute("INSERT OR IGNORE INTO monitoring VALUES (?)", (security_id,))
                self.db.execute(
                    "INSERT INTO monitoring_admissions(id,security_id,state,reason,operator,position,proposed_at) "
                    "VALUES (?,?, 'admitted',?,?,NULL,?)",
                    (str(uuid4()), security_id, reason, operator, proposed_at))
                return 'admitted'
            queued = self.db.execute(
                "SELECT COUNT(*) FROM monitoring_admissions WHERE state='queued'").fetchone()[0]
            if queued < queue_capacity:
                position = (self.db.execute(
                    "SELECT COALESCE(MAX(position),0) FROM monitoring_admissions WHERE state='queued'"
                ).fetchone()[0]) + 1
                self.db.execute(
                    "INSERT INTO monitoring_admissions(id,security_id,state,reason,operator,position,proposed_at) "
                    "VALUES (?,?, 'queued',?,?,?,?)",
                    (str(uuid4()), security_id, reason, operator, position, proposed_at))
                return 'queued'
        return 'rejected'

    def monitoring_admissions(self):
        """Return admission rows (security_id, state, reason, position, proposed_at).

        Deterministic ordering: admitted first, then queued FIFO by position, tie-break on
        the stable security_id. Public monitoring membership; carries no private context.
        """
        return self.db.execute(
            "SELECT security_id,state,reason,position,proposed_at FROM monitoring_admissions "
            "ORDER BY CASE state WHEN 'admitted' THEN 0 ELSE 1 END, "
            "position IS NULL DESC, position, security_id").fetchall()

    def add_private_memory(self, user_id: str, kind: str, body: str, source: str, *,
                           portfolio_id=None, payload=None, evidence_ids=None, expires_at=None,
                           confidence=None, applicable_conditions=None, source_run_id=None,
                           version=1, supersedes=None) -> str:
        """Write one owner-scoped private memory (M1 3-arg form plus the M5a richer contract).

        M5a SUBSTRATE ONLY: storing a richer record makes NO learning/adaptation claim.
        Identity is the already-authenticated owner; a supplied portfolio_id is re-authorized
        and never trusted beyond that owner. New rows default status='active', version=1,
        review_status='unreviewed'. Returns the new memory id.
        """
        if not isinstance(user_id, str) or not user_id.strip():
            raise PermissionError("Private memory requires a registered owner")
        if not self.db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise PermissionError("Private memory requires a registered owner")
        if kind not in {"episodic","semantic","procedural"} or not body or not source:
            raise ValueError("Memory kind, content and provenance required")
        if portfolio_id is not None:
            self._authorize(user_id, portfolio_id)
        memory_id = str(uuid4())
        with self.transaction():
            self.db.execute(
                "INSERT INTO memories(id,user_id,scope,kind,body,source,portfolio_id,payload,"
                "evidence_ids,expires_at,confidence,applicable_conditions,source_run_id,"
                "status,version,supersedes,review_status) "
                "VALUES (?,?,'private',?,?,?,?,?,?,?,?,?,?,'active',?,?,'unreviewed')",
                (memory_id, user_id, kind, body, source, portfolio_id, payload, evidence_ids,
                 expires_at, confidence, applicable_conditions, source_run_id, version, supersedes))
        return memory_id

    def ingest_public_fact(self, body: str, source: str):
        """Trusted ingestion ONLY: must be independent public data, never user extraction."""
        if not body or not source:
            raise ValueError("Content and public provenance required")
        with self.transaction():
            self.db.execute("INSERT INTO memories(id,user_id,scope,kind,body,source) VALUES (?,NULL,'public','semantic',?,?)",
                            (str(uuid4()),body,source))

    def retrieve_memory(self, user_id: str):
        if not self.db.execute("SELECT 1 FROM users WHERE id=?",(user_id,)).fetchone():
            raise PermissionError("Unknown user")
        return self.db.execute("SELECT kind,body,source FROM memories WHERE (scope='private' AND user_id=?) OR scope='public' ORDER BY rowid",
                               (user_id,)).fetchall()

    def record_run(self, user_id: str, *, request_id: str, task_type: str, policy_version: str,
                   tool_sequence, model_version=None, observation_timestamps=None,
                   outcome: str, feedback=None, cost=None, latency_ms=None) -> str:
        """Capture ONE owner-scoped run record, redacting secrets and minimizing personal
        content BEFORE the write.

        SUBSTRATE ONLY — recording a run makes NO learning/adaptation claim. Identity is the
        already-authenticated owner; no caller-supplied owner id is trusted. The tool sequence
        is reduced to tool NAMES only and free-text feedback is bounded; the private payload is
        not logged by default. cost is stored as Decimal text; timestamps are UTC ISO-8601.
        """
        from .redaction import redact_capture
        from .valuation import decimal_text
        if not isinstance(user_id, str) or not user_id.strip():
            raise PermissionError("Run capture requires a registered owner")
        if not self.db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise PermissionError("Run capture requires a registered owner")
        if not request_id or not task_type or not policy_version or not outcome:
            raise ValueError("Run record request/task/policy/outcome required")
        redacted = redact_capture({'tool_sequence': list(tool_sequence or []),
                                   'feedback': feedback if isinstance(feedback, str) else ''})
        tool_names = json.dumps(redacted['tool_sequence'])
        clean_feedback = redacted['feedback'] if isinstance(feedback, str) else None
        observations = None if observation_timestamps is None else json.dumps(list(observation_timestamps))
        cost_text = None if cost is None else decimal_text(Decimal(str(cost)))
        run_id = str(uuid4())
        with self.transaction():
            self.db.execute(
                "INSERT INTO run_records(id,user_id,request_id,task_type,policy_version,"
                "model_version,tool_sequence,observation_timestamps,outcome,feedback,cost,latency_ms) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, user_id, request_id, task_type, policy_version, model_version,
                 tool_names, observations, outcome, clean_feedback, cost_text, latency_ms))
        return run_id

    def run_records(self, user_id: str):
        """Return the authenticated owner's run records only. No cross-user rows ever."""
        if not self.db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise PermissionError("Unknown user")
        return self.db.execute(
            "SELECT id,request_id,task_type,policy_version,model_version,tool_sequence,"
            "outcome,feedback,cost,latency_ms,captured_at FROM run_records "
            "WHERE user_id=? ORDER BY rowid", (user_id,)).fetchall()

    @staticmethod
    def _tokenize(text) -> list:
        """Deterministic token normalization: lowercase, strip punctuation, split on space."""
        if not isinstance(text, str):
            return []
        cleaned = ''.join(ch.lower() if ch.isalnum() else ' ' for ch in text)
        return [tok for tok in cleaned.split() if tok]

    def retrieve_scoped_memories(self, user_id: str, query: str, *, query_time: str,
                                 kind=None, portfolio_id=None, limit=None):
        """Scoped, deterministic keyword retrieval — SUBSTRATE ONLY, no adaptation claim.

        Ownership and status/expiry filters run in SQL BEFORE any scoring, so an excluded
        row never reaches the ranker: only the owner's private rows plus permitted public
        facts, with status='active' and not expired at query_time. Ranking is deterministic
        keyword overlap (no embeddings this slice); ties break on newer created_at then the
        stable id. Returns dict rows carrying their score and matched_tokens.

        Keyword-only this slice. If vectors are ever added they inherit the SAME authorization
        and deletion semantics as these source rows (private stays private; deleted/expired
        stays excluded); they are never a bypass.
        """
        if not isinstance(user_id, str) or not user_id.strip():
            raise PermissionError("Scoped retrieval requires a registered owner")
        if not self.db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise PermissionError("Unknown user")
        params = [user_id, query_time]
        sql = ("SELECT id,user_id,scope,kind,body,source,portfolio_id,confidence,"
               "applicable_conditions,created_at FROM memories "
               "WHERE ((scope='private' AND user_id=?) OR scope='public') "
               "AND status='active' AND (expires_at IS NULL OR expires_at>?)")
        if portfolio_id is not None:
            # Owner-scoped portfolio filter: never trust a caller id beyond the owner.
            self._authorize(user_id, portfolio_id)
            sql += " AND portfolio_id=?"
            params.append(portfolio_id)
        if kind is not None:
            sql += " AND kind=?"
            params.append(kind)
        filtered = self.db.execute(sql, params).fetchall()
        query_tokens = self._tokenize(query)
        query_set = set(query_tokens)
        scored = []
        for (mem_id, _owner, _scope, row_kind, body, _source, row_portfolio,
             confidence, conditions, created_at) in filtered:
            row_tokens = set(self._tokenize(body)) | set(self._tokenize(conditions))
            matched = [tok for tok in query_tokens if tok in row_tokens]
            keyword_overlap = len(set(matched))
            score = keyword_overlap * KEYWORD_WEIGHT
            if kind is not None and row_kind == kind:
                score += TYPE_WEIGHT
            if portfolio_id is not None and row_portfolio == portfolio_id:
                score += PORTFOLIO_WEIGHT
            # Small bounded confidence term (0 when NULL), never outweighing a keyword match.
            try:
                score += min(max(float(confidence or 0), 0.0), 1.0)
            except (TypeError, ValueError):
                pass
            scored.append({'id': mem_id, 'kind': row_kind, 'body': body,
                           'score': score, 'matched_tokens': sorted(query_set & row_tokens),
                           'created_at': created_at})
        scored.sort(key=lambda r: (-r['score'], _descending_text(r['created_at']), r['id']))
        if limit is not None:
            scored = scored[:limit]
        return scored

    def _owned_memory(self, user_id: str, memory_id: str):
        """Authorize that user_id owns memory_id; return (scope, status) or raise."""
        row = self.db.execute(
            "SELECT scope,status,user_id FROM memories WHERE id=?", (memory_id,)).fetchone()
        if row is None or row[2] != user_id:
            raise PermissionError("Memory unavailable")
        return row[0], row[1]

    def delete_memory(self, user_id: str, memory_id: str, *, reason: str):
        """Soft-delete a memory, retaining deletion lineage — SUBSTRATE ONLY, no learning claim.

        Authorizes ownership first, sets status='deleted' and writes a `memory_deletions` row.
        The body is retained for lineage but excluded from retrieval. A deleted row is never
        resurrected.
        """
        if not reason or not reason.strip():
            raise ValueError("Deletion reason required")
        with self.transaction():
            scope, status = self._owned_memory(user_id, memory_id)
            self.db.execute("UPDATE memories SET status='deleted' WHERE id=?", (memory_id,))
            self.db.execute(
                "INSERT INTO memory_deletions(id,memory_id,previous_status,reason,scope) "
                "VALUES (?,?,?,?,?)", (str(uuid4()), memory_id, status, reason, scope))

    def supersede_memory(self, user_id: str, memory_id: str, *, reason: str, kind: str,
                         body: str, source: str, **new_fields) -> str:
        """Supersede a memory with a replacement, retaining lineage — SUBSTRATE ONLY.

        Authorizes ownership first, marks the old row status='superseded', writes lineage, and
        inserts the replacement with incremented version and supersedes set. Makes NO
        learning/adaptation claim; this is provenance-preserving record maintenance.
        """
        if not reason or not reason.strip():
            raise ValueError("Supersede reason required")
        with self.transaction():
            scope, status = self._owned_memory(user_id, memory_id)
            old_version = self.db.execute(
                "SELECT version FROM memories WHERE id=?", (memory_id,)).fetchone()[0]
            self.db.execute("UPDATE memories SET status='superseded' WHERE id=?", (memory_id,))
            self.db.execute(
                "INSERT INTO memory_deletions(id,memory_id,previous_status,reason,scope) "
                "VALUES (?,?,?,?,?)", (str(uuid4()), memory_id, status, reason, scope))
            return self.add_private_memory(user_id, kind, body, source,
                                           version=old_version + 1, supersedes=memory_id,
                                           **new_fields)

    def record_paper_order(self, user_id: str, portfolio_id: str, security_id: str,
                           quantity: str, reference_price: str, mode: str = "read_only") -> str:
        self._authorize(user_id,portfolio_id)
        if mode != "paper":
            raise PermissionError("Only explicit paper mode permits simulated order records")
        qty, price = positive(quantity), positive(reference_price)
        order_id = str(uuid4())
        with self.transaction():
            self.db.execute("INSERT INTO paper_orders(id,portfolio_id,security_id,quantity,reference_price) "
                            "VALUES (?,?,?,?,?)",(order_id,portfolio_id,security_id,str(qty),str(price)))
        return order_id

    def _require_user(self, user_id: str):
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("Registered user required")
        if not self.db.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            raise ValueError("Registered user required")

    def set_notification_preferences(self, user_id: str, *, quiet_start, quiet_end,
                                     materiality_threshold, cooldown_seconds, channel='local-sink'):
        """UPSERT a registered user's notification preferences from the trusted context.

        quiet_start/quiet_end are either both None (no quiet window) or ints in 0..23.
        materiality_threshold is a nonnegative finite Decimal (0 allowed), stored as decimal
        text. cooldown_seconds is a nonnegative int. Nothing is written on a validation error.
        """
        from .valuation import decimal_text
        self._require_user(user_id)
        if (quiet_start is None) != (quiet_end is None):
            raise ValueError("Quiet hours require both start and end or neither")
        if quiet_start is not None:
            for hour in (quiet_start, quiet_end):
                if not isinstance(hour, int) or isinstance(hour, bool) or not 0 <= hour <= 23:
                    raise ValueError("Quiet hours must be integers in 0..23")
        if not isinstance(materiality_threshold, Decimal) or not materiality_threshold.is_finite() \
                or materiality_threshold < 0:
            raise ValueError("Materiality threshold must be a nonnegative finite Decimal")
        if not isinstance(cooldown_seconds, int) or isinstance(cooldown_seconds, bool) \
                or cooldown_seconds < 0:
            raise ValueError("Cooldown seconds must be a nonnegative integer")
        if not isinstance(channel, str) or not channel.strip():
            raise ValueError("Notification channel required")
        threshold_text = decimal_text(materiality_threshold)
        with self.transaction():
            self.db.execute(
                "INSERT INTO notification_preferences"
                "(user_id,quiet_start,quiet_end,materiality_threshold,cooldown_seconds,channel) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "quiet_start=excluded.quiet_start,quiet_end=excluded.quiet_end,"
                "materiality_threshold=excluded.materiality_threshold,"
                "cooldown_seconds=excluded.cooldown_seconds,channel=excluded.channel",
                (user_id, quiet_start, quiet_end, threshold_text, cooldown_seconds, channel))

    def notification_preferences(self, user_id: str) -> dict:
        """Return a user's notification preferences, with deterministic defaults when unset.

        Defaults: no quiet window, threshold '0', cooldown 0, channel 'local-sink'.
        """
        self._require_user(user_id)
        row = self.db.execute(
            "SELECT quiet_start,quiet_end,materiality_threshold,cooldown_seconds,channel "
            "FROM notification_preferences WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            return {'quiet_start': None, 'quiet_end': None, 'materiality_threshold': '0',
                    'cooldown_seconds': 0, 'channel': 'local-sink'}
        return {'quiet_start': row[0], 'quiet_end': row[1], 'materiality_threshold': row[2],
                'cooldown_seconds': row[3], 'channel': row[4]}

    def _within_quiet_hours(self, hour: int, quiet_start, quiet_end) -> bool:
        # Quiet window is the half-open [quiet_start, quiet_end); wraps past midnight.
        if quiet_start is None or quiet_end is None:
            return False
        if quiet_start <= quiet_end:
            return quiet_start <= hour < quiet_end
        return hour >= quiet_start or hour < quiet_end

    def enqueue_notification(self, user_id: str, event: str, *, dedup_key: str, channel: str,
                             now, materiality=None) -> str:
        """Persist one outbox row BEFORE delivery (at-least-once); dedup on (user_id, dedup_key).

        A repeat (user_id, dedup_key) returns the prior id and writes nothing. Otherwise the
        delivery_status is 'suppressed' when the item falls in quiet hours, OR its materiality
        is below the user's threshold, OR it is within cooldown_seconds of the last 'sent' row
        for (user_id, event); else 'pending'. event is a fixed label, never private content.
        """
        from .valuation import utc
        self._require_user(user_id)
        if not isinstance(event, str) or not event.strip():
            raise ValueError("Notification event label required")
        if not isinstance(dedup_key, str) or not dedup_key.strip():
            raise ValueError("Dedup key required")
        if not isinstance(channel, str) or not channel.strip():
            raise ValueError("Notification channel required")
        existing = self.db.execute(
            "SELECT id FROM notification_outbox WHERE user_id=? AND dedup_key=?",
            (user_id, dedup_key)).fetchone()
        if existing is not None:
            return existing[0]
        when = utc(now)
        prefs = self.notification_preferences(user_id)
        status = 'pending'
        if self._within_quiet_hours(when.hour, prefs['quiet_start'], prefs['quiet_end']):
            status = 'suppressed'
        elif materiality is not None and Decimal(materiality) < Decimal(prefs['materiality_threshold']):
            status = 'suppressed'
        elif self._within_cooldown(user_id, event, when, prefs['cooldown_seconds']):
            status = 'suppressed'
        notification_id = str(uuid4())
        with self.transaction():
            # Store the caller-controlled UTC instant so cooldown is deterministic
            # (never the wall-clock insert time).
            self.db.execute(
                "INSERT INTO notification_outbox"
                "(id,user_id,event,dedup_key,channel,delivery_status,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (notification_id, user_id, event, dedup_key, channel, status, when.isoformat()))
        return notification_id

    def _within_cooldown(self, user_id: str, event: str, when, cooldown_seconds: int) -> bool:
        from datetime import datetime
        if cooldown_seconds <= 0:
            return False
        row = self.db.execute(
            "SELECT created_at FROM notification_outbox "
            "WHERE user_id=? AND event=? AND delivery_status='sent' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1", (user_id, event)).fetchone()
        if row is None:
            return False
        last = datetime.fromisoformat(row[0])
        return (when - last).total_seconds() < cooldown_seconds

    def read_monitoring_state(self, security_id: str, rule: str):
        """Return the stored (state_key, observed_at) for a (security, rule), or None.

        Carries only a deterministic state_key and an observation timestamp; no private
        portfolio context. Used to keep unchanged conditions quiet.
        """
        row = self.db.execute(
            "SELECT state_key,observed_at FROM monitoring_state WHERE security_id=? AND rule=?",
            (security_id, rule)).fetchone()
        return None if row is None else (row[0], row[1])

    def record_monitoring_state(self, security_id: str, rule: str, state_key: str, observed_at):
        """UPSERT the deterministic monitoring state for a (security, rule).

        state_key is a short vocabulary term (e.g. 'fresh'/'stale'/'missing' or a bucketed
        materiality marker); observed_at is an optional UTC ISO-8601 instant or None. No
        symbols, quantities, holdings or sources are ever stored here.
        """
        if not isinstance(security_id, str) or not security_id.strip():
            raise ValueError("Security id required")
        if not isinstance(rule, str) or not rule.strip():
            raise ValueError("Monitoring rule required")
        if not isinstance(state_key, str) or not state_key.strip():
            raise ValueError("Monitoring state key required")
        with self.transaction():
            self.db.execute(
                "INSERT INTO monitoring_state(security_id,rule,state_key,observed_at) "
                "VALUES (?,?,?,?) ON CONFLICT(security_id,rule) DO UPDATE SET "
                "state_key=excluded.state_key,observed_at=excluded.observed_at",
                (security_id, rule, state_key, observed_at))

    def deliver_pending(self, notifier, now) -> int:
        """Deliver every 'pending' outbox row (ordered by rowid) and mark it 'sent'.

        The notifier must be idempotent on the dedup key so a retried deliver_pending adds no
        duplicate delivery. Returns the number of rows advanced to 'sent'.
        """
        from .valuation import utc
        when = utc(now).isoformat()
        rows = self.db.execute(
            "SELECT id,user_id,event,dedup_key FROM notification_outbox "
            "WHERE delivery_status='pending' ORDER BY rowid").fetchall()
        delivered = 0
        for notification_id, user_id, event, dedup_key in rows:
            notifier.notify(user_id, event, deduplication_key=dedup_key)
            with self.transaction():
                self.db.execute(
                    "UPDATE notification_outbox SET delivery_status='sent' WHERE id=?",
                    (notification_id,))
            delivered += 1
        return delivered
