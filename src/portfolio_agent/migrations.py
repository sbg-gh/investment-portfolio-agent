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
MIGRATIONS = ((1, LEGACY_SCHEMA), (2, SCOPE_SCHEMA), (3, VALUATION_SCHEMA))
SCHEMA_VERSION = 3

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
