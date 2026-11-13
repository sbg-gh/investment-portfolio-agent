"""Authorized application boundary. Store and credential provisioning stay internal."""
from time import perf_counter
from uuid import uuid4

import re

POLICY_VERSION = 'm1-v1'
OPERATOR_ATTESTED = 'operator-attested'
_SECURITY_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,63}')
_SYMBOL = re.compile(r'[A-Z0-9][A-Z0-9.-]{0,14}')

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

    def portfolios(self, context):
        """The caller's own portfolios only (owner taken from the AuthContext)."""
        return self._run(context,'portfolios',lambda user:[
            {'id':row[0],'name':row[1]} for row in self._store.db.execute(
                'SELECT id,name FROM portfolios WHERE user_id=? ORDER BY rowid',(user,))])

    def securities(self, context, security_ids):
        """Public identity (symbol, kind) for the given ids; unknown ids are omitted."""
        if (not isinstance(security_ids,(list,tuple)) or len(security_ids)>500
                or not all(isinstance(item,str) for item in security_ids)):
            raise ValueError('A list of at most 500 security ids is required')
        def read(_):
            found = []
            for security_id in sorted(set(security_ids)):
                row = self._store.db.execute('SELECT id,symbol,kind FROM securities WHERE id=?',
                                             (security_id,)).fetchone()
                if row is not None:
                    found.append({'security_id':row[0],'symbol':row[1],'kind':row[2]})
            return found
        return self._run(context,'securities',read)

    def observation_sources(self, context):
        """Distinct observation source labels (public market metadata, no private data)."""
        return self._run(context,'observation_sources',lambda _:[
            row[0] for row in self._store.db.execute(
                'SELECT DISTINCT source FROM market_observations ORDER BY source')])

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

    def create_sample_portfolio(self, context, *, as_of):
        """The caller's own synthetic sample portfolio (owner from the AuthContext only).
        Never registers instruments; raises StarterDataMissing if any is unregistered."""
        from .starter_data import create_sample
        return self._run(context,'create_sample_portfolio',lambda user:create_sample(
            self._store,user,self._validator,as_of=as_of))
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

    # M5b procedure lifecycle. Identity is the AuthContext owner; there is no user-id parameter.
    # Procedure content stays out of execution_traces: only the operation name and outcome are logged.
    def extract_procedure_candidates(self, context):
        from . import procedures
        return self._run(context,'extract_procedure_candidates',
            lambda user:procedures.extract_candidates(self._store,user))

    def propose_procedure_version(self, context, memory_id, **overrides):
        from . import procedures
        return self._run(context,'propose_procedure_version',
            lambda user:procedures.propose_version(self._store,user,memory_id,**overrides))

    def review_procedure(self, context, memory_id):
        from . import procedures
        return self._run(context,'review_procedure',
            lambda user:procedures.review_candidate(self._store,user,memory_id))

    def record_procedure_evaluation(self, context, memory_id, report):
        from . import procedures
        return self._run(context,'record_procedure_evaluation',
            lambda user:procedures.record_evaluation(self._store,user,memory_id,report))

    def approve_procedure(self, context, memory_id):
        from . import procedures
        return self._run(context,'approve_procedure',
            lambda user:procedures.approve(self._store,user,memory_id))

    def activate_procedure(self, context, memory_id):
        from . import procedures
        return self._run(context,'activate_procedure',
            lambda user:procedures.activate(self._store,user,memory_id))

    def deprecate_procedure(self, context, memory_id, *, reason):
        from . import procedures
        return self._run(context,'deprecate_procedure',
            lambda user:procedures.deprecate(self._store,user,memory_id,reason=reason))

    def rollback_procedure(self, context, memory_id, *, reason):
        from . import procedures
        return self._run(context,'rollback_procedure',
            lambda user:procedures.rollback(self._store,user,memory_id,reason=reason))

    def check_procedure_regression(self, context, memory_id, report):
        from . import procedures
        return self._run(context,'check_procedure_regression',
            lambda user:procedures.check_regression(self._store,user,memory_id,report))

    def active_procedures(self, context):
        from . import procedures
        return self._run(context,'active_procedures',
            lambda user:procedures.active_procedures(self._store,user))

    def procedure_lineage(self, context, memory_id):
        from . import procedures
        return self._run(context,'procedure_lineage',
            lambda user:procedures.lineage(self._store,user,memory_id))

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

    # M6 paper simulation. Identity is the AuthContext owner; there is no user/owner parameter.
    # Paper state lives only in paper_accounts/paper_ledger and never touches real holdings.
    # Traces carry only the operation name and outcome (no ids, amounts or symbols).
    def create_paper_account(self, context, portfolio_id, *, source, fee_kind='none', fee_amount='0',
                             mode='read_only'):
        from . import paper
        return self._run(context,'create_paper_account',lambda user:paper.create_account(
            self._store,user,portfolio_id,source=source,
            fee_model=paper.fee_model_from(fee_kind,fee_amount),mode=mode))

    def paper_deposit(self, context, account_id, amount, *, source, idempotency_key, as_of,
                      mode='read_only'):
        from . import paper
        return self._run(context,'paper_deposit',lambda user:paper.deposit(
            self._store,user,account_id,amount,source=source,idempotency_key=idempotency_key,
            as_of=as_of,mode=mode))

    def submit_paper_order(self, context, account_id, security_id, side, quantity, *, as_of,
                           idempotency_key, mode='read_only', policy=None):
        from . import paper
        return self._run(context,'submit_paper_order',lambda user:paper.submit_order(
            self._store,user,account_id,security_id,side,quantity,as_of=as_of,
            idempotency_key=idempotency_key,mode=mode,policy=policy))

    def paper_ledger(self, context, account_id):
        from . import paper
        return self._run(context,'paper_ledger',lambda user:paper.ledger(self._store,user,account_id))

    def paper_positions(self, context, account_id):
        from . import paper
        return self._run(context,'paper_positions',lambda user:paper.positions(self._store,user,account_id))

    def value_paper_account(self, context, account_id, *, as_of, policy=None):
        from . import paper
        return self._run(context,'value_paper_account',lambda user:paper.value_account(
            self._store,user,account_id,as_of=as_of,policy=policy))

    def reconcile_paper_account(self, context, account_id):
        from . import paper
        return self._run(context,'reconcile_paper_account',
            lambda user:paper.reconcile_account(self._store,user,account_id))

class OperatorService:
    def __init__(self, service):
        self._service = service

    def ingest_observation(self, context, observation):
        from .valuation import ingest_observation
        return self._service._run(context,'ingest_observation',lambda _:ingest_observation(
            self._service._store,observation),operator=True)

    def import_prices(self, context, *, source, csv_text, dry_run, now=None):
        """Operator-only CSV price import: one transaction, one metadata-only trace.

        Dry run classifies rows and writes no observations. Registers nothing."""
        from . import price_import
        return self._service._run(context,'import_prices',lambda _:price_import.import_prices(
            self._service._store,csv_text,source,dry_run=dry_run,now=now),operator=True)

    def price_fetch_targets(self, context):
        """Operator-only: registered stock/ETF symbols for a Yahoo press (funds excluded)."""
        from . import price_fetch
        return self._service._run(context,'price_fetch_targets',lambda _:price_fetch.read_targets(
            self._service._store),operator=True)

    def import_rows(self, context, *, source, rows, dry_run, now=None):
        """Operator-only ingest of already-parsed price rows (the Yahoo fetch's single short write).

        Same validation and observation semantics as import_prices; identity and operator role are
        re-checked here under the transaction. Registers nothing."""
        from . import price_import
        return self._service._run(context,'import_rows',lambda _:price_import.import_rows(
            self._service._store,rows,source,dry_run=dry_run,now=now),operator=True)

    def register_instrument(self, context, *, security_id, symbol, kind):
        """Operator-ATTESTED registration of a US instrument (no authoritative provider exists).

        Provenance is recorded as 'operator-attested'; this does not validate the instrument
        against a market-data authority and must not be presented as such.
        """
        from .core import ValidatedSecurity
        if (not isinstance(security_id,str) or not _SECURITY_ID.fullmatch(security_id)
                or not isinstance(symbol,str) or not _SYMBOL.fullmatch(symbol.strip().upper())
                or not isinstance(kind,str)):
            raise ValueError('Invalid instrument identity')
        security = ValidatedSecurity(security_id,symbol.strip().upper(),kind,'US',OPERATOR_ATTESTED)
        def register(_):
            self._service._store.register(security)
            return security.security_id
        return self._service._run(context,'register_instrument',register,operator=True)

    def register_starter_instruments(self, context):
        """Operator-ATTESTED registration of the ai-conviction watchlist symbols (no prices)."""
        from .starter_data import register_instruments
        return self._service._run(context,'register_starter_instruments',lambda _:register_instruments(
            self._service._store),operator=True)
    def ingest_public_fact(self, context, body, source):
        return self._service._run(context,'public_fact',lambda _:self._service._store.ingest_public_fact(
            body,source),operator=True)

    def publish_generic_procedure(self, context, memory_id):
        """Operator-only: publish an active procedure's privacy-reviewed generic form."""
        from . import procedures
        return self._service._run(context,'publish_generic_procedure',
            lambda operator:procedures.publish(self._service._store,operator,memory_id),operator=True)

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
