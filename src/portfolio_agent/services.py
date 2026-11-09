"""Authorized application boundary. Store and credential provisioning stay internal."""
from time import perf_counter
from uuid import uuid4

POLICY_VERSION = 'm1-v1'

class PortfolioService:
    def __init__(self, store, authenticator, validator):
        self._store = store
        self._auth = authenticator
        self._validator = validator

    def _run(self, context, operation, action, *, operator=False):
        error = None
        result = None
        with self._store.transaction():
            # Verify under the same transaction as access and mutation.
            user = self._auth.require(context)
            start = perf_counter()
            outcome = 'success'
            try:
                with self._store.transaction():
                    if operator:
                        self._auth.require(context,operator=True)
                    result = action(user)
            except Exception as exc:
                error = exc
                outcome = ('denied' if isinstance(exc,PermissionError) else
                           'invalid' if isinstance(exc,ValueError) else 'error')
            # Successful mutation and trace commit together. Failed actions roll back
            # to the inner savepoint; their metadata-only outcome still gets recorded.
            self._store.db.execute(
                'INSERT INTO execution_traces(id,user_id,request_id,operation,outcome,policy_version,duration_ms) '
                'VALUES (?,?,?,?,?,?,?)',
                (str(uuid4()),user,context.request_id,operation,outcome,POLICY_VERSION,
                 (perf_counter()-start)*1000))
        if error is not None:
            raise error
        return result

    def create_portfolio(self, context, name):
        return self._run(context,'create_portfolio',lambda user:self._store.create_portfolio(user,name))

    def holdings(self, context, portfolio_id):
        return self._run(context,'holdings',lambda user:self._store.holdings(user,portfolio_id))

    def import_holding(self, context, portfolio_id, symbol, quantity):
        return self._run(context,'import_holding',lambda user:self._store.import_holding(
            user,portfolio_id,symbol,quantity,self._validator))

    def import_holding_with_provenance(self, context, portfolio_id, symbol, quantity, *,
                                       source, market_as_of, idempotency_key):
        return self._run(context,'import_holding_with_provenance',
            lambda user:self._store.import_holding_with_provenance(
                user,portfolio_id,symbol,quantity,self._validator,
                source=source,market_as_of=market_as_of,idempotency_key=idempotency_key))

    def remove_holding(self, context, portfolio_id, security_id, *, source, idempotency_key, market_as_of):
        return self._run(context,'remove_holding',lambda user:self._store.remove_holding(
            user,portfolio_id,security_id,source=source,idempotency_key=idempotency_key,market_as_of=market_as_of))

    def receipts(self, context, portfolio_id):
        return self._run(context,'receipts',lambda user:self._store.receipts(user,portfolio_id))

    def record_transaction(self, context, portfolio_id, security_id, side, quantity, *,
                           source, idempotency_key, occurred_at):
        return self._run(context,'record_transaction',lambda user:self._store.record_transaction(
            user,portfolio_id,security_id,side,quantity,
            source=source,idempotency_key=idempotency_key,occurred_at=occurred_at))

    def transactions(self, context, portfolio_id):
        return self._run(context,'transactions',lambda user:self._store.transactions(user,portfolio_id))

    def reconcile(self, context, portfolio_id):
        return self._run(context,'reconcile',lambda user:self._store.reconcile_portfolio(user,portfolio_id))

    def value_portfolio(self, context, portfolio_id, *, as_of, source, policy=None):
        from .valuation import value_portfolio
        return self._run(context,'value_portfolio',lambda user:value_portfolio(
            self._store,user,portfolio_id,as_of=as_of,source=source,policy=policy))

    def valuation_snapshot(self, context, snapshot_id):
        from .valuation import valuation_snapshot
        return self._run(context,'valuation_snapshot',lambda user:valuation_snapshot(self._store,user,snapshot_id))

    def add_memory(self, context, kind, body, source):
        return self._run(context,'add_memory',lambda user:self._store.add_private_memory(user,kind,body,source))

    def memories(self, context):
        return self._run(context,'memories',lambda user:self._store.retrieve_memory(user))

    def capture_run(self, context, *, task_type, policy_version, tool_sequence,
                    model_version=None, observation_timestamps=None, outcome,
                    feedback=None, cost=None, latency_ms=None):
        """Capture one owner-scoped M5a run record (SUBSTRATE ONLY, no adaptation claim).

        Identity is derived from the AuthContext; there is no owner/user_id parameter. The
        run record reuses the payload-free trace `request_id` from the context as its
        correlation token (never a caller-supplied id). The metadata-only `capture_run`
        execution trace is written exactly as today; the redacted run record is an ADDITIONAL
        owner-scoped row sharing that request_id.
        """
        return self._run(context,'capture_run',lambda user:self._store.record_run(
            user,request_id=context.request_id,task_type=task_type,policy_version=policy_version,
            tool_sequence=tool_sequence,model_version=model_version,
            observation_timestamps=observation_timestamps,outcome=outcome,
            feedback=feedback,cost=cost,latency_ms=latency_ms))

    def run_records(self, context):
        return self._run(context,'run_records',lambda user:self._store.run_records(user))

    def retrieve_memories(self, context, query, *, query_time, kind=None,
                          portfolio_id=None, limit=None):
        """Scoped deterministic keyword retrieval (SUBSTRATE ONLY, no adaptation claim).

        Identity is the AuthContext owner; ownership and status/expiry filter BEFORE ranking,
        so only the owner's rows plus permitted public facts are ever scored. Keyword-only this
        slice; embeddings are deferred and would inherit the same authz + deletion semantics.
        """
        return self._run(context,'retrieve_memories',lambda user:self._store.retrieve_scoped_memories(
            user,query,query_time=query_time,kind=kind,portfolio_id=portfolio_id,limit=limit))

    def delete_memory(self, context, memory_id, *, reason):
        return self._run(context,'delete_memory',lambda user:self._store.delete_memory(
            user,memory_id,reason=reason))

    def supersede_memory(self, context, memory_id, *, reason, kind, body, source, **new_fields):
        return self._run(context,'supersede_memory',lambda user:self._store.supersede_memory(
            user,memory_id,reason=reason,kind=kind,body=body,source=source,**new_fields))

    def set_preferences(self, context, *, quiet_start, quiet_end,
                        materiality_threshold, cooldown_seconds, channel='local-sink'):
        return self._run(context,'set_preferences',lambda user:self._store.set_notification_preferences(
            user,quiet_start=quiet_start,quiet_end=quiet_end,
            materiality_threshold=materiality_threshold,cooldown_seconds=cooldown_seconds,channel=channel))

    def preferences(self, context):
        return self._run(context,'preferences',lambda user:self._store.notification_preferences(user))

    def traces(self, context):
        user = self._auth.require(context)
        return self._store.db.execute(
            'SELECT id,request_id,operation,outcome,policy_version,duration_ms FROM execution_traces '
            'WHERE user_id=? ORDER BY rowid',(user,)).fetchall()

    def record_paper_order(self, context, portfolio_id, security_id, quantity, reference_price, *, mode='read_only'):
        return self._run(context,'paper_order',lambda user:self._store.record_paper_order(
            user,portfolio_id,security_id,quantity,reference_price,mode))

class OperatorService:
    def __init__(self, service):
        self._service = service

    def ingest_observation(self, context, observation):
        from .valuation import ingest_observation
        return self._service._run(context,'ingest_observation',lambda _:ingest_observation(
            self._service._store,observation),operator=True)

    def ingest_public_fact(self, context, body, source):
        return self._service._run(context,'public_fact',lambda _:self._service._store.ingest_public_fact(
            body,source),operator=True)

    def enable_monitoring(self, context, security_id):
        return self._service._run(context,'enable_monitoring',lambda _:self._service._store.enable_monitoring(
            security_id),operator=True)

    def set_research(self, context, security_id):
        return self._service._run(context,'set_research',lambda _:self._service._store.set_research(
            security_id),operator=True)

    def propose_enhancement(self, context, security_id, *, reason):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        return self._service._run(context,'propose_enhancement',
            lambda user:self._service._store.propose_enhancement(
                security_id,reason=reason,operator=user,now=now),operator=True)

    def list_monitoring(self, context):
        return self._service._run(context,'list_monitoring',
            lambda _:self._service._store.monitoring_admissions(),operator=True)
