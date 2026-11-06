"""Operator-only consistent backups and restore to a new path; never overwrite."""
from pathlib import Path
import os
import sqlite3
from .migrations import SCHEMA_VERSION

def _validate(db):
    if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise ValueError('Database integrity check failed')
    if db.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Database foreign-key check failed')
    if db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
        raise ValueError('Backup schema is incompatible')

def _copy(source, destination):
    _validate(source)
    path = Path(destination)
    descriptor = os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(descriptor)
    try:
        target = sqlite3.connect(path)
        try:
            source.backup(target)
            _validate(target)
        finally:
            target.close()
    except BaseException:
        path.unlink(missing_ok=True)
        raise

def backup(store, destination):
    if store.db.in_transaction:
        raise ValueError('Finish the active transaction before backup')
    _copy(store.db,destination)

def restore(source_path, destination):
    source = sqlite3.connect(Path(source_path).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        _copy(source,destination)
        # Restored credentials must not resurrect a revoked or expired installation credential.
        target = sqlite3.connect(destination)
        try:
            with target:
                target.execute('UPDATE credentials SET revoked=1')
        finally:
            target.close()
    finally:
        source.close()
