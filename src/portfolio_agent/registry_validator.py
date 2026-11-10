"""Validator backed by the local instrument registry (operator-attested, not authoritative).

No authoritative market-data provider exists yet. Holdings import resolves symbols only
through instruments an operator has registered; anything else needs registration or
reconciliation. This never invents identifiers.
"""
from .core import ValidatedSecurity


class RegistryValidator:
    def __init__(self, store):
        self._store = store

    def resolve(self, symbol):
        if not isinstance(symbol,str):
            raise ValueError('Symbol required')
        row = self._store.db.execute(
            'SELECT id,symbol,kind,country,source FROM securities WHERE symbol=?',
            (symbol.strip().upper(),)).fetchone()
        if row is None:
            raise ValueError('Instrument not registered; operator registration or reconciliation required')
        return ValidatedSecurity(*row)
