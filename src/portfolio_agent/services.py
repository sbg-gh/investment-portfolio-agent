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
