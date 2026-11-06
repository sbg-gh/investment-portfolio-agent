"""Deterministic monitoring rules, public coalescing and proactive job handlers.

Two pure evaluators read ONLY existing data (holdings, baseline_coverage,
market_observations):
  * evaluate_stale_coverage reuses valuation.FreshnessPolicy stale/missing semantics -
    it NEVER fabricates a value; a security with no observation is 'missing'.
  * evaluate_materiality is a deterministic Decimal comparison of the two most recent
    observations by (observed_at, fetched_at).

Public collection is coalesced by (security, source, time-bucket) so the public step runs
once per key regardless of how many users hold the security; the plan reveals no holder
identity. The private monitor_portfolio handler RE-DERIVES and RE-AUTHORIZES the current
owner of the job's owner_ref at execution time (never trusting a payload owner id) and only
enqueues an owner-scoped outbox item on a monitoring_state CHANGE, so unchanged inputs stay
quiet. A deterministic dedup_key keyed on (user, security, rule, state) means a retry cannot
double-notify. All timestamps are UTC ISO-8601; all comparisons use Decimal.
"""
from datetime import datetime
from decimal import Decimal, localcontext

from .valuation import FreshnessPolicy, decimal_text, utc

RULE_STALE = 'stale-coverage'
RULE_MATERIALITY = 'materiality'

# Fixed, payload-free outbox event labels (never a symbol/quantity/holding).
EVENT_STALE = 'coverage-staleness-change'
EVENT_MATERIALITY = 'materiality-crossing'

# owner_ref is a trusted-internal reference, NOT a payload identity. It encodes
# (user_id, portfolio_id) so the handler can re-authorize the CURRENT owner at run time.
# portfolio_id is a uuid4 (no delimiter); we split on the LAST delimiter so a user_id that
# itself contains the delimiter still round-trips.
_OWNER_REF_DELIM = '::'


def encode_owner_ref(user_id: str, portfolio_id: str) -> str:
    """Encode a trusted (user_id, portfolio_id) reference for a private job.

    This is a trusted-internal reference carried on the job row; the owner it names is
    re-authorized at execution time and is never trusted as an identity on its own.
    """
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("owner_ref requires a user id")
    if not isinstance(portfolio_id, str) or not portfolio_id.strip():
        raise ValueError("owner_ref requires a portfolio id")
    return f"{user_id}{_OWNER_REF_DELIM}{portfolio_id}"


def decode_owner_ref(owner_ref: str):
    """Return (user_id, portfolio_id) from an encoded owner_ref, or raise ValueError."""
    if not isinstance(owner_ref, str) or _OWNER_REF_DELIM not in owner_ref:
        raise ValueError("Malformed owner_ref")
    user_id, portfolio_id = owner_ref.rsplit(_OWNER_REF_DELIM, 1)
    if not user_id or not portfolio_id:
        raise ValueError("Malformed owner_ref")
    return user_id, portfolio_id


def _security_kind(store, security_id):
    row = store.db.execute("SELECT kind FROM securities WHERE id=?", (security_id,)).fetchone()
    return None if row is None else row[0]


def _recent_observations(store, security_id, limit):
    basis = 'nav' if _security_kind(store, security_id) == 'mutual_fund' else 'price'
    return store.db.execute(
        "SELECT value,observed_at,fetched_at FROM market_observations "
        "WHERE security_id=? AND basis=? ORDER BY observed_at DESC, fetched_at DESC, id DESC "
        "LIMIT ?", (security_id, basis, int(limit))).fetchall()


def evaluate_stale_coverage(store, security_id, now, policy=None):
    """Return 'fresh' | 'stale' | 'missing' for a security's latest observation.

    Mirrors value_portfolio's status logic (price window for stock/etf, nav window for
    mutual_fund) and NEVER fabricates a value: no observation -> 'missing'.
    """
    policy = FreshnessPolicy() if policy is None else policy
    if not isinstance(policy, FreshnessPolicy):
        raise ValueError("FreshnessPolicy required")
    cutoff = utc(now)
    kind = _security_kind(store, security_id)
    basis = 'nav' if kind == 'mutual_fund' else 'price'
    rows = _recent_observations(store, security_id, 1)
    if not rows:
        return 'missing'
    observed_at = rows[0][1]
    max_age = policy.nav_max_age if basis == 'nav' else policy.price_max_age
    return 'stale' if cutoff - datetime.fromisoformat(observed_at) > max_age else 'fresh'


def evaluate_materiality(store, security_id, threshold):
    """Compare the two most recent observations' Decimal change ratio against `threshold`.

    Returns ('crossed'|'within', change_ratio_decimal_text). With fewer than two
    observations (or a zero prior value) the ratio is '0' and the state is 'within'.
    """
    if not isinstance(threshold, Decimal) or not threshold.is_finite() or threshold < 0:
        raise ValueError("Materiality threshold must be a nonnegative finite Decimal")
    rows = _recent_observations(store, security_id, 2)
    if len(rows) < 2:
        return 'within', '0'
    latest = Decimal(rows[0][0])
    prior = Decimal(rows[1][0])
    if prior == 0:
        return 'within', '0'
    with localcontext() as context:
        context.prec = 80
        ratio = abs(latest - prior) / abs(prior)
    return ('crossed' if ratio >= threshold else 'within'), decimal_text(ratio)


def coalesce_public_collection(securities, source, bucket):
    """Deterministic de-duplicated plan keyed by (security_id, source, time-bucket).

    The public step is computed once per key regardless of how many users hold the
    security. The plan carries NO holder identity.
    """
    plan = []
    seen = set()
    for security_id in securities:
        key = (security_id, source, bucket)
        if key not in seen:
            seen.add(key)
            plan.append(key)
    return plan


def _dedup_key(user_id, security_id, rule, state_key):
    # Deterministic per (user, security, rule, state): a retry over the same state reuses
    # the same key, so enqueue_notification dedups and never double-notifies.
    return f"{user_id}|{security_id}|{rule}|{state_key}"


def collect_handler(job_row, store, now):
    """Public, coalesced collect step. Writes NO private data and reveals no holder identity.

    The job is keyed by its public_scope (security|source|bucket); coalescing on
    (security, source, time-bucket) means the enqueuer only ever schedules one job per key.
    This step is intentionally holder-blind: it never reads holdings or enqueues an outbox
    item. Kept as an explicit seam for later public enrichment.
    """
    scope = job_row['public_scope']
    if not scope:
        raise ValueError("Public collect job requires a public_scope")
    # Deterministic, idempotent no-op over existing public data. The public market
    # observations are already ingested by the operator path; nothing user-scoped happens.
    return None


def monitor_portfolio_handler(job_row, store, now):
    """Private monitor step: reauthorize the CURRENT owner, then notify only on a change.

    (1) RE-DERIVE and RE-AUTHORIZE the current owner from the job's owner_ref via
        store._authorize - on PermissionError (ownership changed) no-op and write nothing.
        The owner id is NEVER trusted from the payload; authorization is at EXECUTION time.
    (2) Evaluate the deterministic rules for the portfolio's held + baseline-covered
        securities.
    (3) Compare each result to the recorded monitoring_state.
    (4) Only on a state CHANGE record the new monitoring_state and enqueue an owner-scoped
        outbox item with a deterministic dedup_key, so unchanged inputs stay quiet and a
        retry cannot double-notify.
    """
    owner_ref = job_row['owner_ref']
    if not owner_ref:
        raise ValueError("monitor_portfolio job requires an owner_ref")
    user_id, portfolio_id = decode_owner_ref(owner_ref)
    try:
        # Reauthorize-at-execution: the CURRENT owner of the portfolio, not the payload.
        store._authorize(user_id, portfolio_id)
    except PermissionError:
        # Ownership changed after enqueue: no-op, write nothing, complete cleanly.
        return None

    prefs = store.notification_preferences(user_id)
    channel = prefs['channel']
    threshold = Decimal(prefs['materiality_threshold'])

    # Held + baseline-covered securities for this portfolio only (owner-scoped read).
    securities = [row[0] for row in store.db.execute(
        "SELECT h.security_id FROM holdings h "
        "JOIN baseline_coverage b ON b.security_id=h.security_id AND b.covered=1 "
        "WHERE h.portfolio_id=? ORDER BY h.security_id", (portfolio_id,)).fetchall()]

    for security_id in securities:
        _evaluate_and_notify_stale(store, user_id, security_id, now, channel)
        _evaluate_and_notify_materiality(store, user_id, security_id, threshold, now, channel)
    return None


def _evaluate_and_notify_stale(store, user_id, security_id, now, channel):
    state_key = evaluate_stale_coverage(store, security_id, now)
    prior = store.read_monitoring_state(security_id, RULE_STALE)
    if prior is not None and prior[0] == state_key:
        return  # unchanged -> stay quiet
    observed_at = utc(now).isoformat()
    store.record_monitoring_state(security_id, RULE_STALE, state_key, observed_at)
    if state_key == 'fresh':
        return  # a return to fresh records state but needs no alert
    store.enqueue_notification(
        user_id, EVENT_STALE,
        dedup_key=_dedup_key(user_id, security_id, RULE_STALE, state_key),
        channel=channel, now=now)


def _evaluate_and_notify_materiality(store, user_id, security_id, threshold, now, channel):
    state_key, ratio = evaluate_materiality(store, security_id, threshold)
    prior = store.read_monitoring_state(security_id, RULE_MATERIALITY)
    if prior is not None and prior[0] == state_key:
        return  # unchanged -> stay quiet
    store.record_monitoring_state(security_id, RULE_MATERIALITY, state_key, utc(now).isoformat())
    if state_key != 'crossed':
        return
    store.enqueue_notification(
        user_id, EVENT_MATERIALITY,
        dedup_key=_dedup_key(user_id, security_id, RULE_MATERIALITY, state_key),
        channel=channel, now=now, materiality=Decimal(ratio))
