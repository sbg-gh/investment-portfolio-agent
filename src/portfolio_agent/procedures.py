"""M5b deterministic experience extraction and the procedure promotion lifecycle.

Extraction is rule-based, offline and owner-scoped: it reads ONLY the authenticated owner's
M5a run records and produces UNREVIEWED procedural candidates. A candidate is un-shareable
(M5a DB CHECK) and inactive until it passes review, a held-out evaluation and approval.
Nothing here claims learning: a single run, user feedback, storage or retrieval is never
sufficient for promotion. Free-text feedback is never read, so it cannot reach a procedure.

Every function takes (store, user, ...) where `user` is the already-authenticated owner from
the service layer's AuthContext; no owner id is ever taken from a payload or argument.
"""
import json
import re
from decimal import Decimal
from uuid import uuid4

from .leakage import assert_no_held_out

TASK_PREFIX = 'valuation:'
SOURCE = 'm5b:rule-extraction'
SCHEMA = 'procedure-v1'
# Only identifier-shaped kinds/tool names may enter a procedure body or payload.
IDENTIFIER_RE = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
CONFIDENCE_QUANTUM = Decimal('0.0001')


def _body(kind, prefer_tool, over_tool):
    return f'For {kind} valuation questions prefer the {prefer_tool} tool over the {over_tool} tool.'


def _canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(',', ':'))


def _single_tool_runs(store, user):
    """Owner's run records reduced to (id, kind, tool, outcome) for single-tool valuation runs."""
    runs = []
    for run in store.run_records(user):
        run_id, _request, task_type, _policy, _model, tool_sequence, outcome = run[:7]
        if not task_type.startswith(TASK_PREFIX):
            continue
        kind = task_type[len(TASK_PREFIX):]
        try:
            tools = json.loads(tool_sequence)
        except (TypeError, ValueError):
            continue
        if not (isinstance(tools, list) and len(tools) == 1 and isinstance(tools[0], str)):
            continue
        if IDENTIFIER_RE.match(kind) and IDENTIFIER_RE.match(tools[0]):
            runs.append((run_id, kind, tools[0], outcome))
    return runs


def tally(store, user, kind, prefer_tool, over_tool):
    """Evidence for 'prefer_tool beats over_tool' from the owner's runs: supporting run ids,
    contradicting run ids and the baseline failures of the tool being replaced."""
    supporting, contradicting, failures = [], [], []
    for run_id, run_kind, tool, outcome in _single_tool_runs(store, user):
        if run_kind != kind:
            continue
        if tool == prefer_tool:
            (supporting if outcome == 'success' else contradicting).append(run_id)
        elif tool == over_tool:
            if outcome == 'success':
                contradicting.append(run_id)
            elif outcome == 'stale_data':
                failures.append(run_id)
    return supporting, contradicting, failures


def _insert_event(store, memory_id, owner, actor, event, from_state, to_state, trigger,
                  reason=None, evaluation=None):
    assert_no_held_out(reason, evaluation)
    store.db.execute(
        "INSERT INTO procedure_events(id,memory_id,owner_id,actor_id,event,from_state,to_state,"
        "trigger,reason,evaluation) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (str(uuid4()), memory_id, owner, actor, event, from_state, to_state, trigger, reason,
         None if evaluation is None else _canonical(evaluation)))


def current_state(store, memory_id):
    """Lifecycle state = to_state of the latest event; None (never injectable) with no events."""
    row = store.db.execute(
        "SELECT to_state FROM procedure_events WHERE memory_id=? ORDER BY rowid DESC LIMIT 1",
        (memory_id,)).fetchone()
    return None if row is None else row[0]


def extract_candidates(store, user):
    """Create unreviewed private procedural candidates from the owner's run records.

    Rule: within a `valuation:<kind>` family, when runs using tool A ended `stale_data` and runs
    using a different tool B ended `success`, propose 'prefer B over A'. Idempotent per owner
    and payload. Returns the ids of newly created candidates.
    """
    from .valuation import decimal_text
    runs = _single_tool_runs(store, user)
    created = []
    for kind in sorted({run[1] for run in runs}):
        stale_tools = sorted({t for _i, k, t, o in runs if k == kind and o == 'stale_data'})
        success_tools = sorted({t for _i, k, t, o in runs if k == kind and o == 'success'})
        for over_tool in stale_tools:
            for prefer_tool in success_tools:
                if prefer_tool == over_tool:
                    continue
                payload = {'schema': SCHEMA, 'when': {'security_kind': kind},
                           'prefer_tool': prefer_tool, 'over_tool': over_tool}
                text = _canonical(payload)
                if store.db.execute(
                        "SELECT 1 FROM memories WHERE user_id=? AND kind='procedural' AND payload=? "
                        "AND status!='deleted'", (user, text)).fetchone():
                    continue
                supporting, contradicting, _failures = tally(store, user, kind, prefer_tool, over_tool)
                if not supporting:
                    continue
                confidence = (Decimal(len(supporting)) /
                              Decimal(len(supporting) + len(contradicting))).quantize(CONFIDENCE_QUANTUM)
                with store.transaction():
                    memory_id = store.add_private_memory(
                        user, 'procedural', _body(kind, prefer_tool, over_tool), SOURCE,
                        payload=text, evidence_ids=json.dumps(supporting + contradicting),
                        confidence=decimal_text(confidence),
                        applicable_conditions=_canonical({'security_kind': kind,
                                                          'task_type': TASK_PREFIX + kind}))
                    _insert_event(store, memory_id, user, user, 'extracted', None, 'candidate',
                                  'manual', 'rule-based extraction from run records')
                created.append(memory_id)
    return created


# ---------------------------------------------------------------------------------------------
# Promotion lifecycle: candidate -> reviewed -> evaluated -> approved -> active ->
# deprecated | rolled_back, plus rejected. Every legal move is an event with an explicit
# (allowed from-states -> to-state) rule; anything else raises ValueError and writes nothing.
# `superseded` (v1 displaced by activating v2) and `restored` (v1 returned by rolling v2 back)
# are internal events driven only by activate/rollback.
# ---------------------------------------------------------------------------------------------
EVENT_TRANSITIONS = {
    'reviewed': ({'candidate'}, 'reviewed'),
    'rejected': ({'candidate'}, 'rejected'),
    'evaluated': ({'reviewed', 'evaluated'}, 'evaluated'),
    'approved': ({'evaluated'}, 'approved'),
    'activated': ({'approved'}, 'active'),
    'deprecated': ({'approved', 'active'}, 'deprecated'),
    'rolled_back': ({'active'}, 'rolled_back'),
    'superseded': ({'active'}, 'deprecated'),
    'restored': ({'deprecated'}, 'active'),
    'published': ({'active'}, 'active'),
}
TRANSITIONS = frozenset((origin, to) for origins, to in EVENT_TRANSITIONS.values() for origin in origins)
REVIEW_MIN_SUPPORT = 3
REVIEW_MAX_CONTRADICTIONS = 0
ROLLBACK_TRIGGERS = ('manual', 'regression')
MAX_REASON_CHARS = 280
SHA256_RE = re.compile(r'^[0-9a-f]{64}$')
# memories.status per lifecycle state (retrieval gate); candidates stay retrievable so the
# retrieval-only ablation variant can see them, but they are never injected as policy.
STATUS_FOR_STATE = {'rejected': 'deprecated', 'deprecated': 'deprecated',
                    'rolled_back': 'deprecated', 'active': 'active'}


def _owned(store, user, memory_id):
    """Authorize that `user` owns a private procedural memory before any read or write."""
    row = store.db.execute(
        "SELECT user_id,scope,kind,status,payload,supersedes,version,evidence_ids,confidence,"
        "applicable_conditions FROM memories WHERE id=?", (memory_id,)).fetchone()
    if row is None or not isinstance(user, str) or row[0] != user or row[1] != 'private' \
            or row[2] != 'procedural':
        raise PermissionError('Procedure unavailable')
    if row[3] == 'deleted':
        raise ValueError('Deleted procedures cannot change lifecycle state')
    return {'payload': row[4], 'supersedes': row[5], 'version': row[6], 'status': row[3],
            'evidence_ids': row[7], 'confidence': row[8], 'applicable_conditions': row[9]}


def _require_state(store, memory_id):
    state = current_state(store, memory_id)
    if state is None:
        raise ValueError('Procedure has no lifecycle state')
    return state


def _clean_reason(reason, *, required=False):
    if reason is None or (isinstance(reason, str) and not reason.strip()):
        if required:
            raise ValueError('A reason is required')
        return None
    if not isinstance(reason, str):
        raise ValueError('Reason must be text')
    assert_no_held_out(reason)
    from .redaction import redact_capture
    return redact_capture({'feedback': reason})['feedback'][:MAX_REASON_CHARS]


def _apply(store, user, memory_id, event, trigger, *, reason=None, evaluation=None, actor=None,
           owner=None):
    """Check the explicit transition rule, then append the lineage event."""
    from_state = _require_state(store, memory_id)
    allowed, to_state = EVENT_TRANSITIONS[event]
    if from_state not in allowed:
        raise ValueError(f'Illegal procedure transition: {from_state} cannot {event}')
    _insert_event(store, memory_id, owner or user, actor or user, event, from_state, to_state,
                  trigger, reason, evaluation)
    return to_state


def _set_status(store, memory_id, *, status=None, review_status=None):
    if status is not None:
        store.db.execute("UPDATE memories SET status=? WHERE id=?", (status, memory_id))
    if review_status is not None:
        store.db.execute("UPDATE memories SET review_status=? WHERE id=?", (review_status, memory_id))


def _parse_payload(text):
    try:
        payload = json.loads(text)
        when = payload['when']
        kind, prefer, over = when['security_kind'], payload['prefer_tool'], payload['over_tool']
    except (TypeError, ValueError, KeyError):
        raise ValueError('Procedure payload is malformed')
    if payload.get('schema') != SCHEMA or not all(
            isinstance(v, str) and IDENTIFIER_RE.match(v) for v in (kind, prefer, over)) or prefer == over:
        raise ValueError('Procedure payload is malformed')
    return {'schema': SCHEMA, 'when': {'security_kind': kind}, 'prefer_tool': prefer, 'over_tool': over}


def review_candidate(store, user, memory_id):
    """Evidence/contradiction review, recomputed from the owner's run records only.

    Passes with >= REVIEW_MIN_SUPPORT distinct supporting runs where the preferred tool
    succeeded and no contradicting run. Feedback text is never evidence. A failed review moves
    the candidate to `rejected` (terminal); a pass moves it to `reviewed`.
    """
    with store.transaction():
        record = _owned(store, user, memory_id)
        _require_state(store, memory_id)
        if current_state(store, memory_id) != 'candidate':
            raise ValueError(f'Illegal procedure transition: {current_state(store, memory_id)} cannot be reviewed')
        payload = _parse_payload(record['payload'])
        supporting, contradicting, _failures = tally(
            store, user, payload['when']['security_kind'], payload['prefer_tool'], payload['over_tool'])
        passed = (len(set(supporting)) >= REVIEW_MIN_SUPPORT
                  and len(contradicting) <= REVIEW_MAX_CONTRADICTIONS)
        reason = f'review supporting={len(supporting)} contradicting={len(contradicting)}'
        if passed:
            state = _apply(store, user, memory_id, 'reviewed', 'review', reason=reason)
        else:
            state = _apply(store, user, memory_id, 'rejected', 'review', reason=reason)
            _set_status(store, memory_id, status='deprecated')
        return {'passed': passed, 'supporting': len(supporting), 'contradicting': len(contradicting),
                'state': state}


def _evaluation_digest(report):
    """Validate a held-out report and RECOMPUTE the verdict from its discordant counts.

    Only counts, n_target, gate_ok and an optional report hash are read; any caller-supplied
    verdict or free text is ignored, so the stored digest is numeric and payload-free.
    """
    from .m5b_stats import decide, load_prereg
    if not isinstance(report, dict):
        raise ValueError('Evaluation report must be a mapping')
    prereg, prereg_hash = load_prereg()
    n_target, gate_ok, report_hash = report.get('n_target'), report.get('gate_ok'), report.get('report_sha256')
    if isinstance(n_target, bool) or not isinstance(n_target, int) or n_target < 0:
        raise ValueError('n_target must be a non-negative integer')
    if not isinstance(gate_ok, bool):
        raise ValueError('gate_ok must be a boolean')
    if report_hash is not None and not (isinstance(report_hash, str) and SHA256_RE.match(report_hash)):
        raise ValueError('report_sha256 must be a sha256 hex digest')
    contrasts = report.get('contrasts')
    if not isinstance(contrasts, dict):
        raise ValueError('contrasts must be a mapping of (b, c) counts')
    pairs = {}
    for name, value in contrasts.items():
        if not (isinstance(value, (list, tuple)) and len(value) == 2
                and all(isinstance(v, int) and not isinstance(v, bool) for v in value)):
            raise ValueError('Each contrast needs integer (b, c) counts')
        pairs[name] = tuple(value)
    decision = decide(pairs, n_target, prereg)
    return {'verdict': decision['verdict'], 'reason': decision['reason'],
            'contrasts': decision['contrasts'], 'gate_ok': gate_ok, 'n_target': n_target,
            'prereg_sha256': prereg_hash, 'report_sha256': report_hash}


def record_evaluation(store, user, memory_id, report):
    """Record a held-out evaluation event; the verdict is recomputed by m5b_stats.decide."""
    with store.transaction():
        _owned(store, user, memory_id)
        _require_state(store, memory_id)
        digest = _evaluation_digest(report)
        _apply(store, user, memory_id, 'evaluated', 'evaluation',
               reason=f"verdict={digest['verdict']}", evaluation=digest)
        return digest


def _latest_evaluation(store, memory_id):
    row = store.db.execute(
        "SELECT evaluation FROM procedure_events WHERE memory_id=? AND event='evaluated' "
        "ORDER BY rowid DESC LIMIT 1", (memory_id,)).fetchone()
    return None if row is None or row[0] is None else json.loads(row[0])


def approve(store, user, memory_id):
    """Approve only after a significant_improvement verdict with every gate check ok."""
    with store.transaction():
        _owned(store, user, memory_id)
        if _require_state(store, memory_id) != 'evaluated':
            raise ValueError(f'Illegal procedure transition: {current_state(store, memory_id)} cannot be approved')
        digest = _latest_evaluation(store, memory_id)
        if digest is None or digest['verdict'] != 'significant_improvement' or digest['gate_ok'] is not True:
            raise ValueError('Approval needs a significant_improvement verdict and a passing gate')
        _apply(store, user, memory_id, 'approved', 'evaluation',
               reason='approved on held-out evaluation', evaluation=digest)
        _set_status(store, memory_id, review_status='approved')


def activate(store, user, memory_id):
    """Activate an approved procedure; an active predecessor version is superseded."""
    with store.transaction():
        record = _owned(store, user, memory_id)
        _apply(store, user, memory_id, 'activated', 'manual', reason='activated')
        _set_status(store, memory_id, status='active')
        predecessor = record['supersedes']
        if predecessor and current_state(store, predecessor) == 'active':
            _owned(store, user, predecessor)
            _apply(store, user, predecessor, 'superseded', 'manual', reason=f'superseded by version {record["version"]}')
            _set_status(store, predecessor, status='superseded')


def deprecate(store, user, memory_id, *, reason):
    with store.transaction():
        _owned(store, user, memory_id)
        text = _clean_reason(reason, required=True)
        _apply(store, user, memory_id, 'deprecated', 'manual', reason=text)
        _set_status(store, memory_id, status='deprecated')


def rollback(store, user, memory_id, *, reason, trigger='manual', evaluation=None):
    """Roll back an active procedure and restore the version it superseded, if any."""
    if trigger not in ROLLBACK_TRIGGERS:
        raise ValueError('Rollback trigger must be manual or regression')
    with store.transaction():
        record = _owned(store, user, memory_id)
        text = _clean_reason(reason, required=True)
        _apply(store, user, memory_id, 'rolled_back', trigger, reason=text, evaluation=evaluation)
        _set_status(store, memory_id, status='deprecated')
        predecessor = record['supersedes']
        if predecessor:
            last = store.db.execute(
                "SELECT event FROM procedure_events WHERE memory_id=? ORDER BY rowid DESC LIMIT 1",
                (predecessor,)).fetchone()
            if last is not None and last[0] == 'superseded':
                _owned(store, user, predecessor)
                _apply(store, user, predecessor, 'restored', trigger,
                       reason=f'restored after rollback of version {record["version"]}')
                _set_status(store, predecessor, status='active')


def check_regression(store, user, memory_id, report):
    """Revert an ACTIVE procedure automatically on a significant regression or a failed gate.

    The verdict is recomputed from counts. Inconclusive or improved results change nothing.
    """
    with store.transaction():
        _owned(store, user, memory_id)
        digest = _evaluation_digest(report)
        outcome = {'rolled_back': False, 'verdict': digest['verdict'], 'reason': digest['reason'],
                   'gate_ok': digest['gate_ok']}
        if _require_state(store, memory_id) != 'active':
            return outcome
        if digest['verdict'] == 'significant_regression' or not digest['gate_ok']:
            rollback(store, user, memory_id, trigger='regression', evaluation=digest,
                     reason=f"automatic revert: verdict={digest['verdict']} gate_ok={digest['gate_ok']}")
            outcome['rolled_back'] = True
        return outcome


def propose_version(store, user, memory_id, *, prefer_tool=None, over_tool=None):
    """Create the next unreviewed version (supersedes + version+1) of an ACTIVE procedure.

    The predecessor is untouched until the new version is itself approved and activated.
    """
    with store.transaction():
        record = _owned(store, user, memory_id)
        if _require_state(store, memory_id) != 'active':
            raise ValueError('Only an active procedure can receive a new version')
        payload = _parse_payload(record['payload'])
        for name, value in (('prefer_tool', prefer_tool), ('over_tool', over_tool)):
            if value is not None:
                payload[name] = value
        payload = _parse_payload(_canonical(payload))
        kind = payload['when']['security_kind']
        new_id = store.add_private_memory(
            user, 'procedural', _body(kind, payload['prefer_tool'], payload['over_tool']), SOURCE,
            payload=_canonical(payload), evidence_ids=record['evidence_ids'],
            confidence=record['confidence'], applicable_conditions=record['applicable_conditions'],
            version=record['version'] + 1, supersedes=memory_id)
        _insert_event(store, new_id, user, user, 'extracted', None, 'candidate', 'manual',
                      f'new version {record["version"] + 1} proposed')
        return new_id


def active_procedures(store, user):
    """The owner's approved, active procedures as structured policy (never unreviewed ones)."""
    if not store.db.execute("SELECT 1 FROM users WHERE id=?", (user,)).fetchone():
        raise PermissionError('Unknown user')
    rows = store.db.execute(
        "SELECT id,version,payload,body FROM memories WHERE user_id=? AND scope='private' "
        "AND kind='procedural' AND status='active' AND review_status='approved' ORDER BY rowid",
        (user,)).fetchall()
    result = []
    for memory_id, version, payload, body in rows:
        if current_state(store, memory_id) != 'active':
            continue
        try:
            parsed = _parse_payload(payload)
        except ValueError:
            continue
        result.append({'id': memory_id, 'version': version, 'payload': parsed, 'body': body})
    return result


def lineage(store, user, memory_id):
    """Append-only event history for one of the owner's procedures."""
    _owned(store, user, memory_id)
    rows = store.db.execute(
        "SELECT id,event,from_state,to_state,trigger,actor_id,reason,evaluation,created_at "
        "FROM procedure_events WHERE memory_id=? ORDER BY rowid", (memory_id,)).fetchall()
    return [{'id': r[0], 'event': r[1], 'from_state': r[2], 'to_state': r[3], 'trigger': r[4],
             'actor_id': r[5], 'reason': r[6], 'evaluation': None if r[7] is None else json.loads(r[7]),
             'created_at': r[8]} for r in rows]


# ---------------------------------------------------------------------------------------------
# Publication of a GENERIC procedure (operator-only at the service boundary).
# ---------------------------------------------------------------------------------------------
def generic_text(store, memory_id):
    """Generic body built ONLY from whitelisted, identifier-validated payload fields."""
    row = store.db.execute("SELECT payload FROM memories WHERE id=?", (memory_id,)).fetchone()
    if row is None:
        raise ValueError('Procedure unavailable')
    payload = _parse_payload(row[0])
    return _body(payload['when']['security_kind'], payload['prefer_tool'], payload['over_tool'])


def _tokens(text):
    return re.findall(r'[a-z0-9]+', str(text).lower())


def _contains_sequence(haystack, needle):
    size = len(needle)
    return size > 0 and any(haystack[i:i + size] == needle for i in range(len(haystack) - size + 1))


def privacy_review(store, owner, text):
    """Return the CLASSES of the owner's private data that overlap `text` (empty means clean).

    Markers: user id, portfolio ids/names, held security ids/symbols, run feedback and the
    owner's non-procedural private memories (preferences/notes). Matching is on contiguous
    alphanumeric token sequences, so planted identifiers cannot hide behind punctuation or case.
    Values are never echoed, only the class names.
    """
    db = store.db
    markers = [('user id', owner)]
    for portfolio_id, name in db.execute("SELECT id,name FROM portfolios WHERE user_id=?", (owner,)):
        markers += [('portfolio id', portfolio_id), ('portfolio name', name)]
    for security_id, symbol in db.execute(
            "SELECT DISTINCT s.id,s.symbol FROM holdings h JOIN portfolios p ON p.id=h.portfolio_id "
            "JOIN securities s ON s.id=h.security_id WHERE p.user_id=?", (owner,)):
        markers += [('security id', security_id), ('symbol', symbol)]
    for (feedback,) in db.execute(
            "SELECT feedback FROM run_records WHERE user_id=? AND feedback IS NOT NULL", (owner,)):
        markers.append(('feedback phrase', feedback))
    for (body,) in db.execute(
            "SELECT body FROM memories WHERE user_id=? AND scope='private' AND kind!='procedural'", (owner,)):
        markers.append(('preference text', body))
    haystack = _tokens(text)
    return sorted({label for label, value in markers if _contains_sequence(haystack, _tokens(value))})


def publish(store, operator, memory_id):
    """Publish an ACTIVE procedure's generic form as a reviewed public procedural memory.

    Authorization of the operator role happens in OperatorService; this function additionally
    requires the procedure to be active, rebuilds the content from whitelisted fields and runs
    the privacy review against the owner's private corpus before inserting the public row.
    """
    with store.transaction():
        row = store.db.execute(
            "SELECT user_id,scope,kind,status,review_status,payload FROM memories WHERE id=?",
            (memory_id,)).fetchone()
        if row is None or row[1] != 'private' or row[2] != 'procedural':
            raise ValueError('Procedure unavailable')
        owner = row[0]
        if row[3] != 'active' or row[4] != 'approved' or current_state(store, memory_id) != 'active':
            raise ValueError('Only an approved, active procedure can be published')
        payload = _parse_payload(row[5])
        body = generic_text(store, memory_id)
        violations = privacy_review(store, owner, body)
        if violations:
            raise ValueError('Privacy review failed: generic content overlaps private ' + ', '.join(violations))
        text = _canonical(payload)
        existing = store.db.execute(
            "SELECT id FROM memories WHERE scope='public' AND kind='procedural' AND payload=? "
            "AND status='active'", (text,)).fetchone()
        if existing:
            return existing[0]
        conditions = _canonical({'security_kind': payload['when']['security_kind']})
        assert_no_held_out(body, text, conditions)
        public_id = str(uuid4())
        store.db.execute(
            "INSERT INTO memories(id,user_id,scope,kind,body,source,payload,applicable_conditions,"
            "status,version,review_status) VALUES (?,NULL,'public','procedural',?,'m5b:publish',?,?,"
            "'active',1,'approved')", (public_id, body, text, conditions))
        _insert_event(store, memory_id, owner, operator, 'published', 'active', 'active', 'operator',
                      'generic procedure published after privacy review')
        return public_id
