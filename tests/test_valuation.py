"""Fixture-backed valuation acceptance contract, written before implementation."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import sqlite3
import tempfile
from pathlib import Path
import unittest
from portfolio_agent.auth import Authenticator
from portfolio_agent.core import Store, FixtureValidator, ValidatedSecurity
from portfolio_agent.services import PortfolioService, OperatorService
from portfolio_agent.ports import Observation

NOW=datetime(2026,11,6,21,tzinfo=timezone.utc)

class ValuationTests(unittest.TestCase):
    def setUp(self):
        self.s=Store();self.addCleanup(self.s.close)
        for user in ('alice','bob','admin'):self.s.create_user(user)
        self.auth=Authenticator(self.s)
        self.ctx={u:self.auth.authenticate(self.auth.provision(u,role='operator' if u=='admin' else 'user'))
                  for u in ('alice','bob','admin')}
        records={sym:ValidatedSecurity(sym,sym,kind,'US','synthetic') for sym,kind in
                 (('STOCK','stock'),('ETF','etf'),('FUND','mutual_fund'))}
        self.app=PortfolioService(self.s,self.auth,FixtureValidator(records))
        self.ops=OperatorService(self.app)
        self.p=self.app.create_portfolio(self.ctx['alice'],'private portfolio')
        for sym,qty in (('STOCK','1.25'),('ETF','2.5'),('FUND','3')):
            self.app.import_holding(self.ctx['alice'],self.p,sym,qty)

    def observation(self, sym, value='10', *, age=timedelta(0), fetched_at=None, basis=None):
        return Observation(sym,Decimal(value),NOW-age,'fixture',basis or ('nav' if sym=='FUND' else 'price'),
                           fetched_at=fetched_at or NOW)

    def ingest(self,sym,**kwargs):
        return self.ops.ingest_observation(self.ctx['admin'],self.observation(sym,**kwargs))

    def value(self, **kwargs):
        return self.app.value_portfolio(self.ctx['alice'],self.p,as_of=NOW,source='fixture',**kwargs)

    def test_mixed_assets_exact_decimal_and_nav_provenance(self):
        self.ingest('STOCK',value='10.01');self.ingest('ETF',value='20.02')
        nav=self.ingest('FUND',value='5.555',age=timedelta(days=1))
        with localcontext() as ctx:
            ctx.prec=6
            result=self.value()
        self.assertEqual(result['status'],'complete')
        self.assertEqual(Decimal(result['total']),Decimal('79.2275'))
        line=next(x for x in result['lines'] if x['security_id']=='FUND')
        self.assertEqual(line['basis'],'nav');self.assertEqual(line['observation_id'],nav)
        self.assertEqual(line['observed_at'],(NOW-timedelta(days=1)).isoformat())
        self.assertEqual(line['source'],'fixture');self.assertEqual(line['fetched_at'],NOW.isoformat())
        self.assertEqual(result['holdings_basis'],'current_snapshot')
        self.assertEqual(len(result['holdings_sha256']),64)

    def test_missing_and_stale_never_become_complete_total(self):
        self.ingest('STOCK',age=timedelta(days=2));self.ingest('ETF')
        result=self.value()
        self.assertEqual(result['status'],'incomplete');self.assertIsNone(result['total'])
        lines={x['security_id']:x for x in result['lines']}
        self.assertEqual(lines['STOCK']['status'],'stale')
        self.assertEqual(lines['FUND']['status'],'missing')
        self.assertIsNone(lines['FUND']['market_value'])
        self.assertEqual(Decimal(result['priced_subtotal']),Decimal('37.5'))
        self.ingest('FUND')
        self.assertEqual(self.value()['status'],'stale')

    def test_future_observed_or_late_fetched_data_is_excluded(self):
        self.ingest('STOCK',value='999',age=timedelta(days=-1),fetched_at=NOW+timedelta(days=1))
        self.ingest('ETF',value='999',age=timedelta(days=1),fetched_at=NOW+timedelta(seconds=1))
        result=self.value()
        self.assertTrue(all(x['status']=='missing' for x in result['lines']))
        self.assertIsNone(result['total'])

    def test_freshness_boundary_and_explicit_policy(self):
        from portfolio_agent.valuation import FreshnessPolicy
        self.ingest('STOCK',age=timedelta(hours=24));self.ingest('ETF');self.ingest('FUND',age=timedelta(days=3))
        self.assertEqual(self.value()['status'],'complete')
        result=self.value(policy=FreshnessPolicy(price_max_age=timedelta(hours=23),nav_max_age=timedelta(days=2)))
        self.assertEqual(result['status'],'stale')
        self.assertEqual(result['policy']['nav_max_age_seconds'],172800)
        with self.assertRaises(ValueError):FreshnessPolicy(price_max_age=timedelta(seconds=-1))

    def test_invalid_observations_rejected_without_side_effects(self):
        base=self.observation('STOCK')
        invalid=[replace(base,value=0.1),replace(base,value=Decimal('NaN')),replace(base,value=Decimal('-1')),
                 replace(base,value=Decimal('1e100')),replace(base,currency='EUR'),replace(base,source=''),
                 replace(base,observed_at=NOW.replace(tzinfo=None)),replace(base,fetched_at=None),
                 replace(base,observed_at=NOW+timedelta(seconds=1)),replace(base,basis='nav'),
                 replace(base,security_id='unknown'),replace(self.observation('FUND'),basis='price')]
        for obs in invalid:
            with self.subTest(obs=obs):
                with self.assertRaises(ValueError):self.ops.ingest_observation(self.ctx['admin'],obs)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM market_observations').fetchone()[0],0)

    def test_idempotent_ingestion_and_conflicting_revision(self):
        first=self.ingest('STOCK');self.assertEqual(first,self.ingest('STOCK'))
        with self.assertRaises(ValueError):self.ingest('STOCK',value='11')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM market_observations').fetchone()[0],1)

    def test_explicit_source_and_latest_known_observation(self):
        self.ingest('STOCK',value='5',age=timedelta(hours=2))
        self.ingest('STOCK',value='7',age=timedelta(hours=1))
        self.ops.ingest_observation(self.ctx['admin'],replace(self.observation('STOCK','999'),source='other'))
        row=next(x for x in self.value()['lines'] if x['security_id']=='STOCK')
        self.assertEqual(Decimal(row['market_value']),Decimal('8.75'))
        self.assertEqual(row['source'],'fixture')

    def test_user_cannot_ingest_or_value_another_portfolio(self):
        with self.assertRaises(PermissionError):self.ops.ingest_observation(self.ctx['alice'],self.observation('STOCK'))
        with self.assertRaises(PermissionError):self.app.value_portfolio(self.ctx['bob'],self.p,as_of=NOW,source='fixture')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM valuation_snapshots').fetchone()[0],0)

    def test_immutable_scoped_snapshot_survives_holdings_change_and_restore(self):
        from portfolio_agent.backup import backup,restore
        for sym in ('STOCK','ETF','FUND'):self.ingest(sym)
        before=self.value();snapshot_id=before['snapshot_id']
        self.app.import_holding(self.ctx['alice'],self.p,'STOCK','9')
        self.assertEqual(self.app.valuation_snapshot(self.ctx['alice'],snapshot_id),before)
        self.assertNotEqual(self.value()['holdings_sha256'],before['holdings_sha256'])
        with self.assertRaises(PermissionError):self.app.valuation_snapshot(self.ctx['bob'],snapshot_id)
        with tempfile.TemporaryDirectory() as td:
            snapshot=Path(td)/'backup.sqlite3';target=Path(td)/'restore.sqlite3'
            backup(self.s,snapshot);restore(snapshot,target)
            recovered=Store(str(target))
            try:
                auth=Authenticator(recovered)
                app=PortfolioService(recovered,auth,FixtureValidator({}))
                ctx=auth.authenticate(auth.provision('alice'))
                self.assertEqual(app.valuation_snapshot(ctx,snapshot_id),before)
            finally:recovered.close()
        with self.s.db:self.s.db.execute("UPDATE portfolios SET user_id='bob' WHERE id=?",(self.p,))
        for user in ('alice','bob'):
            with self.assertRaises(PermissionError):self.app.valuation_snapshot(self.ctx[user],snapshot_id)

    def test_empty_portfolio_and_zero_prices_are_distinct_from_missing(self):
        p=self.app.create_portfolio(self.ctx['alice'],'empty')
        result=self.app.value_portfolio(self.ctx['alice'],p,as_of=NOW,source='fixture')
        self.assertEqual(result['status'],'complete');self.assertEqual(Decimal(result['total']),Decimal('0'))
        for sym in ('STOCK','ETF','FUND'):self.ingest(sym,value='0')
        result=self.value()
        self.assertEqual(result['status'],'complete');self.assertEqual(Decimal(result['total']),Decimal('0'))

    def test_snapshot_and_trace_are_atomic(self):
        self.s.db.execute("CREATE TRIGGER fail_trace BEFORE INSERT ON execution_traces BEGIN SELECT RAISE(ABORT,'failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.value()
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM valuation_snapshots').fetchone()[0],0)

    def test_v2_upgrade_preserves_existing_holdings(self):
        from portfolio_agent.migrations import MIGRATIONS,migrate,SCHEMA_VERSION
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'old.sqlite3';db=sqlite3.connect(path)
            migrate(db,MIGRATIONS[:2])
            db.execute("INSERT INTO users VALUES ('alice')")
            db.execute("INSERT INTO portfolios VALUES ('p','alice','private')")
            db.execute("INSERT INTO securities(id,symbol,kind,country,source) VALUES ('s','S','stock','US','fixture')")
            db.execute("INSERT INTO holdings VALUES ('p','s','1.25')");db.commit();db.close()
            upgraded=Store(str(path))
            try:
                self.assertEqual(upgraded.holdings('alice','p'),[('s','1.25')])
                self.assertEqual(upgraded.db.execute('PRAGMA user_version').fetchone()[0],SCHEMA_VERSION)
                self.assertEqual(upgraded.db.execute('SELECT COUNT(*) FROM market_observations').fetchone()[0],0)
            finally:upgraded.close()
