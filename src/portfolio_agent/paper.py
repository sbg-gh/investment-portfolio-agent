"""Paper portfolio simulation: store-level, ledger-based and strictly separate from real holdings.

Writes only paper_accounts and paper_ledger. It never reads or writes holdings, transactions,
import_receipts, holding_events, paper_orders or baseline_coverage, so a paper fill cannot
change a real position. Fills reuse valuation.select_observation (the valuation selection
rule): the latest observation observed AND fetched at or before the cutoff, from the account's
source, with no slippage and no substitution. Rejected orders raise PaperRejected and write
nothing; there is no pending state (retry with a NEW idempotency key once data arrives).
"""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import hashlib
import json
from uuid import uuid4
from .core import SUPPORTED_KINDS
from .valuation import FreshnessPolicy, VALUATION_VERSION, decimal_text, numeric, select_observation, utc

FILL_MODEL = 'last_observation_at_or_before_cutoff'
FEE_KINDS = ('none', 'flat', 'per_share', 'percentage')
REJECTION_REASONS = ('insufficient_cash', 'insufficient_position', 'price_missing', 'price_stale',
                     'nav_missing', 'nav_stale')
_BASIS_SCALE = Decimal('1e-12')


class PaperRejected(ValueError):
    """A well-formed order that cannot fill; nothing is stored."""

    def __init__(self, reason):
        if reason not in REJECTION_REASONS:
            raise ValueError('Unknown rejection reason')
        super().__init__(reason)
        self.reason = reason


@contextmanager
def _ctx():
    # Up to 28 digits per factor and fixed 12-digit scales: 80 digits cover every product and
    # aggregate exactly, independent of any ambient context.
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.rounding = ROUND_HALF_EVEN
        yield ctx


def require_paper_mode(mode):
    if not (type(mode) is str and mode == 'paper'):
        raise PermissionError('Paper operations require paper mode')


def _amount(value):
    """Positive finite Decimal from a str or Decimal; floats and bools are rejected."""
    if isinstance(value, bool) or not isinstance(value, (str, Decimal)):
        raise ValueError('Amount must be a decimal string or Decimal')
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('Expected a positive finite decimal') from exc
    if not number.is_finite() or number <= 0:
        raise ValueError('Expected a positive finite decimal')
    return numeric(number)


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label} required')
    return value


@dataclass(frozen=True)
class FeeModel:
    kind: str
    amount: Decimal

    def __post_init__(self):
        if self.kind not in FEE_KINDS:
            raise ValueError('Unsupported fee model')
        if not isinstance(self.amount, Decimal) or isinstance(self.amount, bool):
            raise ValueError('Fee amount must be a Decimal')
        numeric(self.amount)
        if self.kind == 'none' and self.amount != 0:
            raise ValueError('A none fee model has no amount')
        if self.kind == 'percentage' and self.amount > 1:
            raise ValueError('Percentage fee is a fraction no greater than 1')

    def fee(self, quantity, gross):
        with _ctx():
            if self.kind == 'flat':
                return self.amount
            if self.kind == 'per_share':
                return self.amount * quantity
            if self.kind == 'percentage':
                return self.amount * gross
            return Decimal(0)

    def as_dict(self):
        return {'kind': self.kind, 'amount': decimal_text(self.amount)}


def _authorize_account(store, user, account_id):
    unavailable = PermissionError('Paper account unavailable')
    if not isinstance(account_id, str) or not account_id:
        raise unavailable
    row = store.db.execute('SELECT id,portfolio_id,price_source,fee_kind,fee_amount FROM paper_accounts '
                           'WHERE id=? AND user_id=?', (account_id, user)).fetchone()
    if row is None:
        raise unavailable
    try:
        store._authorize(user, row[1])
    except PermissionError:
        raise unavailable from None
    return {'id': row[0], 'portfolio_id': row[1], 'price_source': row[2],
            'fee_model': FeeModel(row[3], Decimal(row[4]))}


def _key(value):
    return _text(value, 'Idempotency key')


def create_account(store, user, portfolio_id, *, source, fee_model, mode=None):
    store._authorize(user, portfolio_id)
    require_paper_mode(mode)
    _text(source, 'Price source')
    if not isinstance(fee_model, FeeModel):
        raise ValueError('FeeModel required')
    account_id = str(uuid4())
    with store.transaction():
        store.db.execute('INSERT INTO paper_accounts(id,user_id,portfolio_id,currency,price_source,'
                         'fee_kind,fee_amount) VALUES (?,?,?,?,?,?,?)',
                         (account_id, user, portfolio_id, 'USD', source, fee_model.kind,
                          decimal_text(fee_model.amount)))
    return account_id


_COLUMNS = ('id', 'entry_type', 'security_id', 'quantity', 'unit_price', 'fee', 'cash_delta', 'basis',
            'observation_id', 'price_as_of', 'nav_date', 'as_of', 'source', 'idempotency_key', 'created_at')


def _rows(db, account_id):
    return [dict(zip(_COLUMNS, row)) for row in db.execute(
        f'SELECT {",".join(_COLUMNS)} FROM paper_ledger WHERE account_id=? ORDER BY rowid', (account_id,))]


def _existing(db, account_id, key):
    row = db.execute(f'SELECT {",".join(_COLUMNS)} FROM paper_ledger WHERE account_id=? AND idempotency_key=?',
                     (account_id, key)).fetchone()
    return None if row is None else dict(zip(_COLUMNS, row))


def _entry(row, replayed):
    return {'entry_id': row['id'], 'entry_type': row['entry_type'], 'cash_delta': row['cash_delta'],
            'as_of': row['as_of'], 'source': row['source'], 'idempotency_key': row['idempotency_key'],
            'replayed': replayed}


def deposit(store, user, account_id, amount, *, source, idempotency_key, as_of, mode=None):
    account = _authorize_account(store, user, account_id)
    require_paper_mode(mode)
    value = _amount(amount)
    _text(source, 'Deposit source')
    key = _key(idempotency_key)
    when = utc(as_of)
    with store.transaction():
        prior = _existing(store.db, account['id'], key)
        if prior is not None:
            if prior['entry_type'] != 'deposit':
                raise ValueError('Idempotency key belongs to a different entry')
            return _entry(prior, True)
        entry_id = str(uuid4())
        store.db.execute('INSERT INTO paper_ledger(id,account_id,entry_type,cash_delta,as_of,source,'
                         'idempotency_key) VALUES (?,?,?,?,?,?,?)',
                         (entry_id, account['id'], 'deposit', decimal_text(value), when.isoformat(),
                          source, key))
        return _entry(_existing(store.db, account['id'], key), False)


def _gross(row):
    with _ctx():
        return Decimal(row['quantity']) * Decimal(row['unit_price'])


def _fill(db, account, row, replayed):
    """Self-describing fill built only from stored facts, so replays equal first fills."""
    kind = db.execute('SELECT kind FROM securities WHERE id=?', (row['security_id'],)).fetchone()[0]
    observed, fetched = db.execute('SELECT observed_at,fetched_at FROM market_observations WHERE id=?',
                                   (row['observation_id'],)).fetchone()
    fill = {'status': 'filled', 'side': row['entry_type'], 'security_id': row['security_id'], 'kind': kind,
            'quantity': row['quantity'], 'unit_price': row['unit_price'], 'gross': decimal_text(_gross(row)),
            'fee': row['fee'], 'cash_delta': row['cash_delta'], 'basis': row['basis'],
            'observation_id': row['observation_id'], 'observed_at': observed, 'fetched_at': fetched,
            'price_as_of': row['price_as_of'], 'nav_date': row['nav_date'], 'as_of': row['as_of'],
            'source': row['source'], 'fill_model': FILL_MODEL, 'fee_model': account['fee_model'].as_dict()}
    digest = hashlib.sha256(json.dumps(fill, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'entry_id': row['id'], **fill, 'fill_digest': digest, 'replayed': replayed}


def submit_order(store, user, account_id, security_id, side, quantity, *, as_of, idempotency_key,
                 mode=None, policy=None):
    account = _authorize_account(store, user, account_id)
    require_paper_mode(mode)
    if side not in ('buy', 'sell'):
        raise ValueError('Side must be buy or sell')
    qty = _amount(quantity)
    key = _key(idempotency_key)
    cutoff = utc(as_of)
    policy = FreshnessPolicy() if policy is None else policy
    if not isinstance(policy, FreshnessPolicy):
        raise ValueError('FreshnessPolicy required')
    db = store.db
    with store.transaction():
        prior = _existing(db, account['id'], key)
        if prior is not None:
            if prior['entry_type'] not in ('buy', 'sell'):
                raise ValueError('Idempotency key belongs to a different entry')
            return _fill(db, account, prior, True)
        security = db.execute('SELECT kind FROM securities WHERE id=?', (security_id,)).fetchone() \
            if isinstance(security_id, str) else None
        if security is None or security[0] not in SUPPORTED_KINDS:
            raise ValueError('Unknown or unsupported security')
        basis = 'nav' if security[0] == 'mutual_fund' else 'price'
        observation = select_observation(db, security_id, account['price_source'], basis, cutoff)
        if observation is None:
            raise PaperRejected(f'{basis}_missing')
        obs_id, value, observed_at, _fetched = observation
        max_age = policy.nav_max_age if basis == 'nav' else policy.price_max_age
        if cutoff - datetime.fromisoformat(observed_at) > max_age:
            raise PaperRejected(f'{basis}_stale')
        price = numeric(Decimal(value))
        state = _replay(_rows(db, account['id']))
        with _ctx():
            gross = qty * price
            fee = account['fee_model'].fee(qty, gross)
            if side == 'buy':
                cash_delta = -(gross + fee)
            else:
                held = state['quantity'].get(security_id, Decimal(0))
                if qty > held:
                    raise PaperRejected('insufficient_position')
                cash_delta = gross - fee
            if state['cash'] + cash_delta < 0:
                raise PaperRejected('insufficient_cash')
        nav_date = datetime.fromisoformat(observed_at).date().isoformat() if basis == 'nav' else None
        entry_id = str(uuid4())
        db.execute('INSERT INTO paper_ledger(id,account_id,entry_type,security_id,quantity,unit_price,fee,'
                   'cash_delta,basis,observation_id,price_as_of,nav_date,as_of,source,idempotency_key) '
                   'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (entry_id, account['id'], side, security_id, decimal_text(qty), decimal_text(price),
                    decimal_text(fee), decimal_text(cash_delta), basis, obs_id, observed_at, nav_date,
                    cutoff.isoformat(), account['price_source'], key))
        return _fill(db, account, _existing(db, account['id'], key), False)


def ledger(store, user, account_id):
    account = _authorize_account(store, user, account_id)
    return _rows(store.db, account['id'])


def _replay(rows):
    """Pure FIFO replay. Reports ledger issues instead of raising."""
    cash = Decimal(0)
    realized = Decimal(0)
    lots = {}
    issues = []
    with _ctx():
        for row in rows:
            cash += Decimal(row['cash_delta'])
            if cash < 0 and 'negative_cash' not in issues:
                issues.append('negative_cash')
            if row['entry_type'] == 'deposit':
                continue
            qty, price, fee = Decimal(row['quantity']), Decimal(row['unit_price']), Decimal(row['fee'])
            book = lots.setdefault(row['security_id'], [])
            if row['entry_type'] == 'buy':
                book.append({'quantity': qty, 'basis': qty * price + fee, 'acquired_entry_id': row['id']})
                continue
            remaining, consumed = qty, Decimal(0)
            while remaining > 0 and book:
                lot = book[0]
                if lot['quantity'] <= remaining:
                    take, used = lot['quantity'], lot['basis']
                    book.pop(0)
                else:
                    take = remaining
                    used = (lot['basis'] * take / lot['quantity']).quantize(_BASIS_SCALE, ROUND_HALF_EVEN)
                    lot['quantity'] -= take
                    lot['basis'] -= used
                remaining -= take
                consumed += used
            if remaining > 0 and 'oversold' not in issues:
                issues.append('oversold')
            realized += (qty * price - fee) - consumed
        lots = {sid: book for sid, book in lots.items() if book}
        quantity = {sid: sum((lot['quantity'] for lot in book), Decimal(0)) for sid, book in lots.items()}
        basis = {sid: sum((lot['basis'] for lot in book), Decimal(0)) for sid, book in lots.items()}
    return {'cash': cash, 'lots': lots, 'quantity': quantity, 'cost_basis': basis,
            'realized_pnl': realized, 'issues': issues}


def positions(store, user, account_id):
    account = _authorize_account(store, user, account_id)
    rows = _rows(store.db, account['id'])
    state = _replay(rows)
    return {'cash': decimal_text(state['cash']),
            'positions': [{'security_id': sid, 'quantity': decimal_text(state['quantity'][sid]),
                           'cost_basis': decimal_text(state['cost_basis'][sid]),
                           'lots': [{'quantity': decimal_text(l['quantity']), 'basis': decimal_text(l['basis']),
                                     'acquired_entry_id': l['acquired_entry_id']} for l in state['lots'][sid]]}
                          for sid in sorted(state['lots'])],
            'realized_pnl': decimal_text(state['realized_pnl']), 'issues': state['issues'],
            'ledger_entries': len(rows)}


def value_account(store, user, account_id, *, as_of, policy=None):
    account = _authorize_account(store, user, account_id)
    cutoff = utc(as_of)
    policy = FreshnessPolicy() if policy is None else policy
    if not isinstance(policy, FreshnessPolicy):
        raise ValueError('FreshnessPolicy required')
    db = store.db
    rows = _rows(db, account['id'])
    state = _replay(rows)
    lines = []
    with _ctx():
        subtotal = Decimal(0)
        for sid in sorted(state['lots']):
            kind = db.execute('SELECT kind FROM securities WHERE id=?', (sid,)).fetchone()[0]
            basis = 'nav' if kind == 'mutual_fund' else 'price'
            line = {'security_id': sid, 'kind': kind, 'quantity': decimal_text(state['quantity'][sid]),
                    'basis': basis, 'status': 'missing', 'market_value': None, 'unit_value': None,
                    'observation_id': None, 'observed_at': None, 'fetched_at': None,
                    'cost_basis': decimal_text(state['cost_basis'][sid])}
            row = select_observation(db, sid, account['price_source'], basis, cutoff)
            if row:
                identity, price, observed, fetched = row
                amount = state['quantity'][sid] * numeric(Decimal(price))
                max_age = policy.nav_max_age if basis == 'nav' else policy.price_max_age
                line.update(status='stale' if cutoff - datetime.fromisoformat(observed) > max_age else 'fresh',
                            market_value=decimal_text(amount), unit_value=price, observation_id=identity,
                            observed_at=observed, fetched_at=fetched)
                subtotal += amount
            lines.append(line)
        status = ('incomplete' if any(x['status'] == 'missing' for x in lines) else
                  'stale' if any(x['status'] == 'stale' for x in lines) else 'complete')
        total = decimal_text(state['cash'] + subtotal) if status == 'complete' else None
        priced = decimal_text(subtotal)
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'valuation_version': VALUATION_VERSION, 'holdings_basis': 'paper_ledger', 'currency': 'USD',
            'market_as_of': cutoff.isoformat(), 'source': account['price_source'], 'status': status,
            'cash': decimal_text(state['cash']), 'priced_subtotal': priced, 'total': total,
            'policy': policy.as_dict(), 'issues': state['issues'], 'lines': lines, 'ledger_sha256': digest}


def reconcile_account(store, user, account_id):
    """Flag divergence between stored cash/quantities and independently recomputed values."""
    account = _authorize_account(store, user, account_id)
    rows = _rows(store.db, account['id'])
    state = _replay(rows)
    with _ctx():
        ledger_cash = sum((Decimal(r['cash_delta']) for r in rows), Decimal(0))
        recomputed = Decimal(0)
        signed = {}
        for r in rows:
            if r['entry_type'] == 'deposit':
                recomputed += Decimal(r['cash_delta'])
                continue
            gross, fee, qty = _gross(r), Decimal(r['fee']), Decimal(r['quantity'])
            if r['entry_type'] == 'buy':
                recomputed -= gross + fee
            else:
                recomputed += gross - fee
                qty = -qty
            signed[r['security_id']] = signed.get(r['security_id'], Decimal(0)) + qty
    result = [{'check': 'cash', 'ledger_cash_sum': decimal_text(ledger_cash),
               'recomputed_cash_from_components': decimal_text(recomputed), 'ok': ledger_cash == recomputed}]
    for sid in sorted(set(signed) | set(state['quantity'])):
        left, right = signed.get(sid, Decimal(0)), state['quantity'].get(sid, Decimal(0))
        result.append({'check': 'position', 'security_id': sid, 'signed_quantity_sum': decimal_text(left),
                       'open_lot_quantity_sum': decimal_text(right), 'ok': left == right})
    result.extend({'check': 'issue', 'issue': issue, 'ok': False} for issue in state['issues'])
    return result
