"""Deterministic domain services. Trusted in-process caller supplies user identity.

This is NOT an authentication boundary; do not expose directly to a network.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import sqlite3
from typing import Protocol
from uuid import uuid4

RESEARCH_TARGET = 400
MONITORING_CAPACITY = 75
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
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS portfolios(
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS securities(
          id TEXT PRIMARY KEY, symbol TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
          country TEXT NOT NULL, source TEXT NOT NULL,
          validated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS holdings(
          portfolio_id TEXT NOT NULL REFERENCES portfolios(id),
          security_id TEXT NOT NULL REFERENCES securities(id), quantity TEXT NOT NULL,
          PRIMARY KEY(portfolio_id,security_id));
        CREATE TABLE IF NOT EXISTS research(
          security_id TEXT PRIMARY KEY REFERENCES securities(id));
        CREATE TABLE IF NOT EXISTS monitoring(
          security_id TEXT PRIMARY KEY REFERENCES securities(id));
        CREATE TRIGGER IF NOT EXISTS monitoring_cap BEFORE INSERT ON monitoring
        WHEN (SELECT COUNT(*) FROM monitoring)>=75
          AND NOT EXISTS(SELECT 1 FROM monitoring WHERE security_id=NEW.security_id)
        BEGIN SELECT RAISE(ABORT,'Enhanced monitoring capacity reached'); END;
        CREATE TABLE IF NOT EXISTS memories(
          id TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id),
          kind TEXT NOT NULL, body TEXT NOT NULL, source TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS paper_orders(
          id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL REFERENCES portfolios(id),
          security_id TEXT NOT NULL REFERENCES securities(id), quantity TEXT NOT NULL,
          reference_price TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'recorded',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        """)

    def close(self):
        self.db.close()

    def create_user(self, user_id: str):
        if not user_id.strip():
            raise ValueError("Empty user identity")
        with self.db:
            self.db.execute("INSERT INTO users VALUES (?)", (user_id,))

    def create_portfolio(self, user_id: str, name: str) -> str:
        portfolio_id = str(uuid4())
        with self.db:
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

    def import_holding(self, user_id: str, portfolio_id: str, symbol: str,
                       quantity: str, validator: SecurityValidator):
        self._authorize(user_id,portfolio_id)
        qty = positive(quantity)
        security = validator.resolve(symbol)
        with self.db:
            self.register(security)
            self.db.execute("INSERT INTO holdings VALUES (?,?,?) ON CONFLICT(portfolio_id,security_id) "
                            "DO UPDATE SET quantity=excluded.quantity",(portfolio_id,security.security_id,str(qty)))
        return security.security_id

    def holdings(self, user_id: str, portfolio_id: str):
        self._authorize(user_id,portfolio_id)
        return self.db.execute("SELECT security_id,quantity FROM holdings WHERE portfolio_id=? ORDER BY security_id",
                               (portfolio_id,)).fetchall()

    def set_research(self, security_id: str):
        # 400 is a planning target, never a hard master-list or holdings limit.
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO research VALUES (?)",(security_id,))

    def enable_monitoring(self, security_id: str):
        # Operator-only shared service: capacity counts distinct instruments globally.
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO monitoring VALUES (?)",(security_id,))

    def add_private_memory(self, user_id: str, kind: str, body: str, source: str):
        if kind not in {"episodic","semantic","procedural"} or not body or not source:
            raise ValueError("Memory kind, content and provenance required")
        with self.db:
            self.db.execute("INSERT INTO memories(id,user_id,kind,body,source) VALUES (?,?,?,?,?)",
                            (str(uuid4()),user_id,kind,body,source))

    def ingest_public_fact(self, body: str, source: str):
        """Trusted ingestion ONLY: must be independent public data, never user extraction."""
        if not body or not source:
            raise ValueError("Content and public provenance required")
        with self.db:
            self.db.execute("INSERT INTO memories(id,user_id,kind,body,source) VALUES (?,NULL,'semantic',?,?)",
                            (str(uuid4()),body,source))

    def retrieve_memory(self, user_id: str):
        if not self.db.execute("SELECT 1 FROM users WHERE id=?",(user_id,)).fetchone():
            raise PermissionError("Unknown user")
        return self.db.execute("SELECT kind,body,source FROM memories WHERE user_id=? OR user_id IS NULL ORDER BY rowid",
                               (user_id,)).fetchall()

    def record_paper_order(self, user_id: str, portfolio_id: str, security_id: str,
                           quantity: str, reference_price: str, mode: str = "read_only") -> str:
        self._authorize(user_id,portfolio_id)
        if mode != "paper":
            raise PermissionError("Only explicit paper mode permits simulated order records")
        qty, price = positive(quantity), positive(reference_price)
        order_id = str(uuid4())
        with self.db:
            self.db.execute("INSERT INTO paper_orders(id,portfolio_id,security_id,quantity,reference_price) "
                            "VALUES (?,?,?,?,?)",(order_id,portfolio_id,security_id,str(qty),str(price)))
        return order_id
