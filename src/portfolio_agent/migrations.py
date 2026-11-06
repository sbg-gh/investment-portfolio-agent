"""Versioned, atomic SQLite migrations; legacy ownerless memory is quarantined."""
import sqlite3

LEGACY_SCHEMA = """
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
        """

SCOPE_SCHEMA = """
CREATE TABLE memory_quarantine(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, body TEXT NOT NULL, source TEXT NOT NULL,
 created_at TEXT NOT NULL, reason TEXT NOT NULL);
INSERT INTO memory_quarantine SELECT id,kind,body,source,created_at,
 'Legacy NULL owner has ambiguous provenance' FROM memories WHERE user_id IS NULL;
ALTER TABLE memories RENAME TO memories_legacy;
CREATE TABLE memories(
 id TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id),
 scope TEXT NOT NULL CHECK(scope IN ('private','public')),
 kind TEXT NOT NULL CHECK(kind IN ('episodic','semantic','procedural')),
 body TEXT NOT NULL, source TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK((scope='private' AND user_id IS NOT NULL) OR
       (scope='public' AND user_id IS NULL AND kind='semantic')));
INSERT INTO memories(id,user_id,scope,kind,body,source,created_at)
 SELECT id,user_id,'private',kind,body,source,created_at FROM memories_legacy WHERE user_id IS NOT NULL;
DROP TABLE memories_legacy;
CREATE INDEX memory_owner ON memories(user_id,scope);
CREATE INDEX portfolio_owner ON portfolios(user_id);
CREATE TABLE credentials(
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 token_hash TEXT NOT NULL UNIQUE, role TEXT NOT NULL CHECK(role IN ('user','operator')),
 expires_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0 CHECK(revoked IN (0,1)));
CREATE TABLE execution_traces(
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 request_id TEXT NOT NULL, operation TEXT NOT NULL,
 outcome TEXT NOT NULL CHECK(outcome IN ('success','denied','invalid','error')),
 policy_version TEXT NOT NULL, duration_ms REAL NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX trace_owner ON execution_traces(user_id,created_at);
"""
VALUATION_SCHEMA = """
CREATE TABLE market_observations(
 id TEXT PRIMARY KEY, security_id TEXT NOT NULL REFERENCES securities(id),
 value TEXT NOT NULL, currency TEXT NOT NULL CHECK(currency='USD'),
 basis TEXT NOT NULL CHECK(basis IN ('price','nav')), source TEXT NOT NULL,
 observed_at TEXT NOT NULL, fetched_at TEXT NOT NULL,
 UNIQUE(security_id,source,observed_at,fetched_at,basis));
CREATE INDEX observation_lookup ON market_observations(security_id,source,observed_at,fetched_at);
CREATE TABLE valuation_snapshots(
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 portfolio_id TEXT NOT NULL REFERENCES portfolios(id),
 payload TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX valuation_owner ON valuation_snapshots(user_id,portfolio_id);
"""
M2_IMPORT_SCHEMA = """
CREATE TABLE import_receipts(
 id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL REFERENCES portfolios(id),
 security_id TEXT NOT NULL REFERENCES securities(id), idempotency_key TEXT NOT NULL,
 source TEXT NOT NULL, market_as_of TEXT NOT NULL, quantity TEXT,
 action TEXT NOT NULL CHECK(action IN ('upsert','remove')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(portfolio_id,idempotency_key));
CREATE TABLE holding_events(
 id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL REFERENCES portfolios(id),
 security_id TEXT NOT NULL REFERENCES securities(id),
 event TEXT NOT NULL CHECK(event IN ('import','remove')), quantity TEXT,
 receipt_id TEXT NOT NULL REFERENCES import_receipts(id),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE security_aliases(
 id TEXT PRIMARY KEY, symbol TEXT NOT NULL, security_id TEXT NOT NULL REFERENCES securities(id),
 source TEXT NOT NULL, effective_from TEXT NOT NULL, effective_to TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX alias_lookup ON security_aliases(symbol,effective_from);
CREATE INDEX receipt_owner ON import_receipts(portfolio_id);
"""
M2_COMPLETION_SCHEMA = """
CREATE TABLE transactions(
 id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL REFERENCES portfolios(id),
 security_id TEXT NOT NULL REFERENCES securities(id),
 side TEXT NOT NULL CHECK(side IN ('buy','sell')), quantity TEXT NOT NULL,
 source TEXT NOT NULL, idempotency_key TEXT NOT NULL, occurred_at TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(portfolio_id,idempotency_key));
CREATE TABLE monitoring_admissions(
 id TEXT PRIMARY KEY, security_id TEXT NOT NULL REFERENCES securities(id),
 state TEXT NOT NULL CHECK(state IN ('admitted','queued')), reason TEXT NOT NULL,
 operator TEXT NOT NULL, position INTEGER, proposed_at TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(security_id));
CREATE TABLE baseline_coverage(
 security_id TEXT PRIMARY KEY REFERENCES securities(id),
 covered INTEGER NOT NULL DEFAULT 1 CHECK(covered IN (0,1)), since TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX transaction_lookup ON transactions(portfolio_id,security_id);
CREATE INDEX admission_queue ON monitoring_admissions(state,position);
"""
M3_PROACTIVE_SCHEMA = """
CREATE TABLE jobs(
 id TEXT PRIMARY KEY, type TEXT NOT NULL, owner_ref TEXT, public_scope TEXT,
 idempotency_key TEXT NOT NULL, due_at TEXT NOT NULL, lease_expires_at TEXT,
 attempts INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','leased','done','dead')),
 correlation_id TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK((owner_ref IS NOT NULL)<>(public_scope IS NOT NULL)),
 UNIQUE(type, idempotency_key));
CREATE TABLE notification_outbox(
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
 event TEXT NOT NULL, dedup_key TEXT NOT NULL, channel TEXT NOT NULL,
 delivery_status TEXT NOT NULL DEFAULT 'pending'
   CHECK(delivery_status IN ('pending','sent','suppressed')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(user_id, dedup_key));
CREATE TABLE notification_preferences(
 user_id TEXT PRIMARY KEY REFERENCES users(id),
 quiet_start INTEGER, quiet_end INTEGER,
 materiality_threshold TEXT NOT NULL DEFAULT '0',
 cooldown_seconds INTEGER NOT NULL DEFAULT 0,
 channel TEXT NOT NULL DEFAULT 'local-sink',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE monitoring_state(
 security_id TEXT NOT NULL REFERENCES securities(id),
 rule TEXT NOT NULL, state_key TEXT NOT NULL, observed_at TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(security_id, rule));
CREATE INDEX job_due ON jobs(status, due_at);
CREATE INDEX outbox_owner ON notification_outbox(user_id, created_at);
"""
MIGRATIONS = ((1, LEGACY_SCHEMA), (2, SCOPE_SCHEMA), (3, VALUATION_SCHEMA),
              (4, M2_IMPORT_SCHEMA), (5, M2_COMPLETION_SCHEMA),
              (6, M3_PROACTIVE_SCHEMA))
SCHEMA_VERSION = 6

def statements(script):
    pending = ''
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            yield pending
            pending = ''
    if pending.strip():
        raise ValueError('Incomplete migration SQL')

def migrate(db, migrations=MIGRATIONS):
    """Serialize migration checks and roll back the entire upgrade on failure."""
    db.execute('BEGIN IMMEDIATE')
    try:
        current = db.execute('PRAGMA user_version').fetchone()[0]
        latest = migrations[-1][0]
        if current > latest:
            raise ValueError('Database schema is newer than this application')
        for version, script in migrations:
            if version > current:
                for statement in statements(script):
                    db.execute(statement)
                db.execute(f'PRAGMA user_version={int(version)}')
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Foreign-key validation failed during migration')
        db.commit()
    except BaseException:
        db.rollback()
        raise
