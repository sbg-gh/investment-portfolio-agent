"""Deterministic valuation of CURRENT holdings with point-in-time market evidence.

This is neither a historical holdings ledger nor a performance calculator.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
import json
import sqlite3
from uuid import uuid4
from .ports import Observation

VALUATION_VERSION = 'valuation-v1'

@dataclass(frozen=True)
class FreshnessPolicy:
    price_max_age: timedelta = timedelta(days=1)
    nav_max_age: timedelta = timedelta(days=3)

    def __post_init__(self):
        if any(not isinstance(v,timedelta) or v<timedelta(0)
               for v in (self.price_max_age,self.nav_max_age)):
            raise ValueError('Freshness windows must be nonnegative timedeltas')

    def as_dict(self):
        return {'price_max_age_seconds':self.price_max_age.total_seconds(),
                'nav_max_age_seconds':self.nav_max_age.total_seconds()}

def utc(value):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Timezone-aware timestamp required')
    return value.astimezone(timezone.utc)

def numeric(value):
    # Explicit bounds keep precision deterministic and reject pathological inputs.
    if not isinstance(value,Decimal) or not value.is_finite() or value<0:
        raise ValueError('Nonnegative finite Decimal required')
    if len(value.as_tuple().digits)>28 or value.adjusted()>18 or value.as_tuple().exponent < -12:
        raise ValueError('Value exceeds supported precision or magnitude')
    return value

def decimal_text(value):
    result = format(value,'f')
    if '.' in result:
        result = result.rstrip('0').rstrip('.')
    return result if result not in ('-0','') else '0'

def ingest_observation(store, observation):
    if not isinstance(observation,Observation):
        raise ValueError('Observation required')
    row = store.db.execute('SELECT kind FROM securities WHERE id=?',(observation.security_id,)).fetchone()
    if row is None:
        raise ValueError('Observation instrument is not registered')
    expected_basis = 'nav' if row[0]=='mutual_fund' else 'price'
    if observation.basis != expected_basis or observation.currency != 'USD':
        raise ValueError('Unsupported observation basis or currency')
    if not isinstance(observation.source,str) or not observation.source.strip():
        raise ValueError('Observation provenance required')
    observed, fetched = utc(observation.observed_at), utc(observation.fetched_at)
    if fetched < observed:
        raise ValueError('Fetch time precedes observation time')
    value = decimal_text(numeric(observation.value))
    fields = (observation.security_id,value,observation.currency,observation.basis,
              observation.source,observed.isoformat(),fetched.isoformat())
    identity = hashlib.sha256(json.dumps(fields,separators=(',',':')).encode()).hexdigest()
    with store.transaction():
        try:
            store.db.execute('INSERT INTO market_observations VALUES (?,?,?,?,?,?,?,?) '
                             'ON CONFLICT(id) DO NOTHING',(identity,*fields))
        except sqlite3.IntegrityError as exc:
            raise ValueError('Conflicting observation; provide a new fetch timestamp for a revision') from exc
    return identity

def select_observation(db, security_id, source, basis, cutoff):
    """Latest observation known at the cutoff: observed AND fetched at or before it."""
    return db.execute('SELECT id,value,observed_at,fetched_at FROM market_observations '
        'WHERE security_id=? AND source=? AND basis=? AND observed_at<=? AND fetched_at<=? '
        'ORDER BY observed_at DESC,fetched_at DESC,id DESC LIMIT 1',
        (security_id,source,basis,cutoff.isoformat(),cutoff.isoformat())).fetchone()
def value_portfolio(store, user, portfolio_id, *, as_of, source, policy=None):
    store._authorize(user,portfolio_id)
    cutoff = utc(as_of)
    if not isinstance(source,str) or not source.strip():
        raise ValueError('An explicit observation source is required')
    policy = FreshnessPolicy() if policy is None else policy
    if not isinstance(policy,FreshnessPolicy):
        raise ValueError('FreshnessPolicy required')
    positions = store.db.execute('SELECT h.security_id,h.quantity,s.kind FROM holdings h '
        'JOIN securities s ON s.id=h.security_id WHERE h.portfolio_id=? ORDER BY h.security_id',
        (portfolio_id,)).fetchall()
    lines = []
    # Up to 28 digits per factor, a supported 19-digit integer part, and fixed 12-digit
    # scales; 80 digits safely cover products and aggregate values without ambient rounding.
    with localcontext() as context:
        context.prec = 80
        subtotal = Decimal(0)
        for security_id,quantity,kind in positions:
            qty = numeric(Decimal(quantity))
            basis = 'nav' if kind=='mutual_fund' else 'price'
            row = select_observation(store.db,security_id,source,basis,cutoff)
            line = {'security_id':security_id,'kind':kind,'quantity':decimal_text(qty),'basis':basis,
                    'status':'missing','market_value':None,'unit_value':None,'observation_id':None,
                    'source':None,'observed_at':None,'fetched_at':None}
            if row:
                identity,price,observed,fetched = row
                amount = qty * numeric(Decimal(price))
                max_age = policy.nav_max_age if basis=='nav' else policy.price_max_age
                line.update(status='stale' if cutoff-datetime.fromisoformat(observed)>max_age else 'fresh',
                    market_value=decimal_text(amount),unit_value=price,observation_id=identity,
                    source=source,observed_at=observed,fetched_at=fetched)
                subtotal += amount
            lines.append(line)
        status = ('incomplete' if any(x['status']=='missing' for x in lines) else
                  'stale' if any(x['status']=='stale' for x in lines) else 'complete')
        report = {'snapshot_id':str(uuid4()),'valuation_version':VALUATION_VERSION,'currency':'USD',
            'holdings_basis':'current_snapshot','market_as_of':cutoff.isoformat(),
            'holdings_sha256':hashlib.sha256(json.dumps(positions,separators=(',',':')).encode()).hexdigest(),
            'source':source,'policy':policy.as_dict(),'status':status,
            'total':decimal_text(subtotal) if status=='complete' else None,
            'priced_subtotal':decimal_text(subtotal),'lines':lines}
    store.db.execute('INSERT INTO valuation_snapshots(id,user_id,portfolio_id,payload) VALUES (?,?,?,?)',
                     (report['snapshot_id'],user,portfolio_id,json.dumps(report,sort_keys=True)))
    return report

def valuation_snapshot(store,user,snapshot_id):
    row = store.db.execute('SELECT portfolio_id,payload FROM valuation_snapshots WHERE id=? AND user_id=?',
                          (snapshot_id,user)).fetchone()
    if row is None:
        raise PermissionError('Valuation unavailable')
    store._authorize(user,row[0])
    return json.loads(row[1])
