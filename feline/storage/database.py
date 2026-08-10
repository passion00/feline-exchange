from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Lock

from feline.core.events import AIAnalysisResult, CandleUpdate, Event,FeedHealthEvent,FillEvent,FinancingEvent, NewsEvent, OrderRequest,OrderType,OrderUpdate, PortfolioSnapshot, PriceTick, RegimeEvent, RiskEvent,Side, SignalEvent
from feline.portfolio.models import Position
from feline.execution.models import PendingOrder
from .migrations import MIGRATIONS


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = Lock()
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.migrate()

    def migrate(self) -> None:
        with self.lock:
            for version, sql in enumerate(MIGRATIONS, 1):
                exists = self.connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
                applied = exists and self.connection.execute("SELECT 1 FROM schema_migrations WHERE version=?", (version,)).fetchone()
                if not applied:
                    self.connection.executescript(sql)
                    self.connection.execute("INSERT INTO schema_migrations VALUES (?, datetime('now'))", (version,))
            self.connection.commit()

    def persist_event(self, event: Event) -> None:
        payload = json.dumps(event.payload(), ensure_ascii=False)
        ts = event.timestamp.isoformat()
        with self.lock:
            if isinstance(event, PriceTick):
                self.connection.execute("INSERT OR IGNORE INTO market_events VALUES (?,?,?,?,?)", (event.id, ts, type(event).__name__, event.instrument, payload))
            elif isinstance(event, NewsEvent):
                self.connection.execute("INSERT OR IGNORE INTO news_events VALUES (?,?,?,?,?)", (event.id, ts, event.source, event.headline, payload))
            elif isinstance(event, AIAnalysisResult):
                self.connection.execute("INSERT OR IGNORE INTO ai_analyses VALUES (?,?,?,?,?,?)", (event.id, event.job_id, ts, event.instrument, int(event.available), payload))
            elif isinstance(event, SignalEvent):
                self.connection.execute("INSERT OR IGNORE INTO signals VALUES (?,?,?,?,?)", (event.id, ts, event.instrument, event.strategy, payload))
            elif isinstance(event, RiskEvent):
                self.connection.execute("INSERT OR IGNORE INTO risk_events VALUES (?,?,?,?,?,?)", (event.id, ts, int(event.approved), event.rule, event.order_request_id, payload))
            elif isinstance(event, OrderUpdate):
                self.connection.execute("INSERT OR IGNORE INTO paper_orders VALUES (?,?,?,?,?,?)", (event.order_id, event.correlation_id, ts, event.instrument, event.status.value, payload))
                if event.fill_price is not None:
                    self.connection.execute("INSERT INTO paper_trades(order_id,timestamp,instrument,quantity,price,payload) VALUES (?,?,?,?,?,?)", (event.order_id, ts, event.instrument, event.quantity, event.fill_price, payload))
            elif isinstance(event,FillEvent):
                self.connection.execute("INSERT OR IGNORE INTO fills VALUES (?,?,?,?,?,?,?,?,?,?)",(event.id,event.order_id,ts,event.instrument,event.quantity,event.fill_price,event.commission,event.spread_cost,event.slippage_amount,payload))
            elif isinstance(event,FinancingEvent):
                self.connection.execute("INSERT OR IGNORE INTO financing_charges VALUES (?,?,?,?,?)",(event.id,ts,event.instrument,event.amount,payload))
            elif isinstance(event, CandleUpdate):
                self.connection.execute("INSERT OR IGNORE INTO candles VALUES (?,?,?,?,?,?)", (event.id,event.instrument,event.timeframe,event.open_time.isoformat(),event.close_time.isoformat(),payload))
            elif isinstance(event, RegimeEvent):
                self.connection.execute("INSERT OR IGNORE INTO regime_events VALUES (?,?,?,?,?,?)", (event.id,ts,event.instrument,event.previous.value,event.current.value,payload))
            elif isinstance(event, PortfolioSnapshot):
                self.connection.execute("INSERT INTO portfolio_snapshots(timestamp,equity,cash,payload) VALUES (?,?,?,?)", (ts,event.equity,event.cash,payload))
                for instrument, position in event.positions.items():
                    self.connection.execute("INSERT INTO positions VALUES (?,?,?,?,?) ON CONFLICT(instrument) DO UPDATE SET quantity=excluded.quantity,average_price=excluded.average_price,realized_pnl=excluded.realized_pnl,updated_at=excluded.updated_at", (instrument,position["quantity"],position["average_price"],position["realized_pnl"],ts))
            else:
                self.connection.execute("INSERT OR IGNORE INTO system_events VALUES (?,?,?,?,?)", (event.id, ts, type(event).__name__, "info", payload))
            self.connection.commit()

    def count(self, table: str) -> int:
        allowed = {"market_events", "news_events", "ai_analyses", "signals", "paper_orders", "paper_trades", "trades", "positions", "portfolio_snapshots", "risk_events", "system_events", "candles", "regime_events","fills","financing_charges","pending_orders","experiments","walk_forward_windows","replay_sessions","realtime_sessions","realtime_quotes","realtime_validation_summaries","broker_profiles","broker_sessions","broker_events","dataset_registry","research_experiments","research_episodes","event_results","aggregate_results","research_exclusions","research_post_shock_metrics"}
        if table not in allowed:
            raise ValueError("Invalid table")
        return int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def latest_portfolio(self) -> tuple[float, dict[str, Position]] | None:
        row = self.connection.execute("SELECT cash FROM portfolio_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if row is None: return None
        positions = {r["instrument"]: Position(r["instrument"],r["quantity"],r["average_price"],r["realized_pnl"]) for r in self.connection.execute("SELECT * FROM positions")}
        return float(row["cash"]), positions

    def persist_broker_state(self,broker) -> None:
        now=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
        state=broker.portfolio_state();state["protective"]=broker.protective;payload=json.dumps(state)
        with self.lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                self.connection.execute("INSERT INTO broker_state VALUES(1,?,?,?) ON CONFLICT(singleton) DO UPDATE SET cash=excluded.cash,payload=excluded.payload,updated_at=excluded.updated_at",(broker.cash,payload,now))
                self.connection.execute("DELETE FROM pending_orders")
                for oid,p in broker.pending.items():
                    self.connection.execute("INSERT INTO pending_orders VALUES(?,?,?,?,?)",(oid,p.state.value,p.remaining_quantity,json.dumps(p.request.payload()),now))
                for instrument,p in broker.positions.items():
                    self.connection.execute("INSERT INTO positions VALUES (?,?,?,?,?) ON CONFLICT(instrument) DO UPDATE SET quantity=excluded.quantity,average_price=excluded.average_price,realized_pnl=excluded.realized_pnl,updated_at=excluded.updated_at",(instrument,p.quantity,p.average_price,p.realized_pnl,now))
                self.connection.commit()
            except Exception:self.connection.rollback();raise

    def commit_execution(self,broker,fills=(),updates=(),fault_at:str|None=None)->None:
        """Atomically persist execution ledger, orders, cash, positions and pending/protection."""
        def fail(point):
            if fault_at==point:raise RuntimeError(f"injected:{point}")
        fail("before_transaction");now=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat();state=broker.portfolio_state();state["protective"]=broker.protective
        with self.lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                for update in updates:
                    self.connection.execute("INSERT OR REPLACE INTO paper_orders VALUES(?,?,?,?,?,?)",(update.order_id,update.correlation_id,update.timestamp.isoformat(),update.instrument,update.status.value,json.dumps(update.payload())))
                fail("after_order_state")
                for fill in fills:
                    self.connection.execute("INSERT OR IGNORE INTO fills VALUES(?,?,?,?,?,?,?,?,?,?)",(fill.id,fill.order_id,fill.timestamp.isoformat(),fill.instrument,fill.quantity,fill.fill_price,fill.commission,fill.spread_cost,fill.slippage_amount,json.dumps(fill.payload())))
                fail("after_fill_insertion");fail("after_cash_calculation")
                for instrument,p in broker.positions.items():self.connection.execute("INSERT INTO positions VALUES(?,?,?,?,?) ON CONFLICT(instrument) DO UPDATE SET quantity=excluded.quantity,average_price=excluded.average_price,realized_pnl=excluded.realized_pnl,updated_at=excluded.updated_at",(instrument,p.quantity,p.average_price,p.realized_pnl,now))
                fail("after_position_calculation")
                self.connection.execute("INSERT INTO broker_state VALUES(1,?,?,?) ON CONFLICT(singleton) DO UPDATE SET cash=excluded.cash,payload=excluded.payload,updated_at=excluded.updated_at",(broker.cash,json.dumps(state),now));self.connection.execute("DELETE FROM pending_orders")
                for oid,p in broker.pending.items():self.connection.execute("INSERT INTO pending_orders VALUES(?,?,?,?,?)",(oid,p.state.value,p.remaining_quantity,json.dumps(p.request.payload()),now))
                fail("before_commit");self.connection.commit();fail("after_commit")
            except Exception:
                if self.connection.in_transaction:self.connection.rollback()
                raise

    def integrity_report(self)->dict:
        checks={"sqlite":self.connection.execute("PRAGMA integrity_check").fetchone()[0]};issues=[]
        for row in self.connection.execute("SELECT order_id,remaining_quantity FROM pending_orders WHERE remaining_quantity<0"):issues.append(f"negative remaining:{row['order_id']}")
        duplicates=self.connection.execute("SELECT id,COUNT(*) c FROM fills GROUP BY id HAVING c>1").fetchall()
        if duplicates:issues.append("duplicate fill ids")
        checks["issues"]=issues;checks["ok"]=checks["sqlite"]=="ok" and not issues;return checks

    def recover_broker_state(self):
        row=self.connection.execute("SELECT cash,payload FROM broker_state WHERE singleton=1").fetchone()
        if not row:return None
        positions={r['instrument']:Position(r['instrument'],r['quantity'],r['average_price'],r['realized_pnl']) for r in self.connection.execute("SELECT * FROM positions")};pending={}
        from datetime import datetime
        for r in self.connection.execute("SELECT * FROM pending_orders"):
            d=json.loads(r['payload']);d['timestamp']=datetime.fromisoformat(d['timestamp']);d['side']=Side(d['side']);d['order_type']=OrderType(d['order_type'])
            if d.get('expires_at'):d['expires_at']=datetime.fromisoformat(d['expires_at'])
            req=OrderRequest(**d);pending[r['order_id']]=PendingOrder(r['order_id'],req,__import__('feline.core.events',fromlist=['OrderStatus']).OrderStatus(r['state']),r['remaining_quantity'])
        protective={k:tuple(v) for k,v in json.loads(row['payload']).get('protective',{}).items()}
        return float(row['cash']),positions,pending,protective

    def save_health(self, component: str, status: str, details: dict | None = None) -> None:
        with self.lock:
            self.connection.execute("INSERT INTO health_state VALUES (?,?,datetime('now'),?) ON CONFLICT(component) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at,details=excluded.details", (component,status,json.dumps(details or {})))
            self.connection.commit()

    def save_replay_session(self,session:dict,status:str)->None:
        with self.lock:
            self.connection.execute("INSERT INTO replay_sessions VALUES(?,?,?,?,?,?,?) ON CONFLICT(replay_session_id) DO UPDATE SET ended_at=excluded.ended_at,status=excluded.status,payload=excluded.payload",(session["replay_session_id"],session["dataset_path"],session["dataset_checksum"],session.get("replay_start_timestamp"),session.get("replay_end_timestamp"),status,json.dumps(session,default=str)))
            self.connection.commit()

    def save_realtime_session(self,session:dict,status:str)->None:
        with self.lock:
            self.connection.execute("INSERT INTO realtime_sessions VALUES(?,?,?,?,?,?,?) ON CONFLICT(realtime_session_id) DO UPDATE SET ended_at=excluded.ended_at,status=excluded.status,payload=excluded.payload",(session["realtime_session_id"],session["provider"],json.dumps(session["instruments"]),session["started_at"],session.get("ended_at"),status,json.dumps(session,default=str)))
            self.connection.commit()

    def save_realtime_quote(self,tick:PriceTick)->None:
        if not tick.realtime_session_id or not tick.ingestion_timestamp:return
        with self.lock:
            self.connection.execute("INSERT OR IGNORE INTO realtime_quotes VALUES(?,?,?,?,?,?,?,?,?)",(tick.id,tick.realtime_session_id,tick.timestamp.isoformat(),tick.ingestion_timestamp.isoformat(),tick.instrument,tick.bid,tick.ask,tick.provider_sequence,json.dumps(tick.payload())))
            self.connection.commit()

    def save_realtime_validation(self,summary:dict)->None:
        with self.lock:
            self.connection.execute("INSERT OR REPLACE INTO realtime_validation_summaries VALUES(?,?,?,?,?,?)",(summary["validation_session_id"],summary["realtime_session_id"],summary["validation_mode"],summary["created_at"],summary["validation_status"],json.dumps(summary,default=str,sort_keys=True)))
            self.connection.commit()

    def save_trade(self,trade,status:str)->None:
        payload=json.dumps(__import__('dataclasses').asdict(trade),default=str,sort_keys=True)
        with self.lock:
            self.connection.execute("INSERT OR REPLACE INTO trades VALUES(?,?,?,?,?,?)",(trade.trade_id,trade.instrument,status,trade.entry_time.isoformat(),trade.exit_time.isoformat() if trade.exit_time else None,payload))
            self.connection.commit()

    def save_broker_profile(self,profile)->None:
        public=profile.public_dict();now=__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
        with self.lock:self.connection.execute("INSERT OR REPLACE INTO broker_profiles VALUES(?,?,?,?,?,?,?)",(profile.profile_id,profile.adapter,profile.environment,profile.account_id,profile.name,json.dumps(public,sort_keys=True),now));self.connection.commit()
    def save_broker_session(self,session:dict)->None:
        with self.lock:self.connection.execute("INSERT OR REPLACE INTO broker_sessions VALUES(?,?,?,?,?,?,?,?,?)",(session["session_id"],session.get("profile_id"),session["adapter"],session["environment"],session.get("account_id"),session["started_at"],session.get("ended_at"),session["status"],json.dumps(session,sort_keys=True,default=str)));self.connection.commit()
    def save_broker_events(self,session_id:str,events)->None:
        with self.lock:
            for event in events:self.connection.execute("INSERT OR IGNORE INTO broker_events VALUES(?,?,?,?,?,?,?)",(event["event_id"],session_id,event["timestamp"],event["kind"],event.get("request_id"),event["status"],json.dumps(event.get("payload",{}),sort_keys=True,default=str)))
            self.connection.commit()

    def register_dataset(self,record)->None:
        payload=json.dumps(__import__('dataclasses').asdict(record),default=str)
        with self.lock:self.connection.execute("INSERT OR REPLACE INTO dataset_registry VALUES(?,?,?,?,?,?,?)",(record.checksum,record.path,record.provider,record.instrument,record.timeframe,payload,record.import_timestamp));self.connection.commit()

    def save_research_experiment(self,experiment_id,status,created_at,manifest_checksum,payload)->None:
        with self.lock:self.connection.execute("INSERT OR REPLACE INTO research_experiments VALUES(?,?,?,?,?)",(experiment_id,status,created_at,manifest_checksum,json.dumps(payload,default=str)));self.connection.commit()

    def save_research_episode(self,experiment_id,episode,result,split,status,replay_session_id=None)->None:
        payload=json.dumps(result,default=str)
        with self.lock:
            self.connection.execute("INSERT OR REPLACE INTO research_episodes VALUES(?,?,?,?,?,?,?)",(episode.episode_id,experiment_id,episode.entry.event.event_id,replay_session_id,status,split,payload))
            if status=="excluded":self.connection.execute("INSERT OR REPLACE INTO research_exclusions VALUES(?,?,?,?)",(experiment_id,episode.entry.event.event_id,result.get("reason") or episode.exclusion_reason or "excluded",payload))
            else:self.connection.execute("INSERT OR REPLACE INTO event_results VALUES(?,?,?)",(experiment_id,episode.entry.event.event_id,payload))
            self.connection.commit()

    def save_aggregate_result(self,experiment_id,result)->None:
        with self.lock:self.connection.execute("INSERT OR REPLACE INTO aggregate_results VALUES(?,?)",(experiment_id,json.dumps(result,default=str)));self.connection.commit()

    def save_post_shock_metrics(self,experiment_id,event_id,result)->None:
        fields=("one_minute_reference","incremental_horizons","initial_shock_reference_price","initial_shock_displacement","initial_shock_direction","stabilization_reference","post_stabilization_horizons","post_stabilization_outcome","retracement_fraction","impulse_retention_fraction","maximum_post_stabilization_extension","maximum_post_stabilization_reversal","time_to_post_event_extreme_seconds","time_to_post_event_high_seconds","time_to_post_event_low_seconds","decision_diagnostics")
        payload={key:result.get(key) for key in fields}
        with self.lock:self.connection.execute("INSERT OR REPLACE INTO research_post_shock_metrics VALUES(?,?,?)",(experiment_id,event_id,json.dumps(payload,default=str)));self.connection.commit()

    def health(self) -> list[dict]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM health_state ORDER BY component")]

    def save_experiment(self,experiment,status:str="created",result:dict|None=None,error:str|None=None)->None:
        payload=json.dumps(__import__('dataclasses').asdict(experiment),default=str)
        with self.lock:self.connection.execute("INSERT OR REPLACE INTO experiments VALUES(?,?,?,?,?,?)",(experiment.experiment_id,status,experiment.created_at,payload,json.dumps(result) if result else None,error));self.connection.commit()

    def save_walk_forward(self,experiment_id:str,window:tuple,result:dict)->None:
        with self.lock:self.connection.execute("INSERT INTO walk_forward_windows(experiment_id,train_start,train_end,test_start,test_end,result) VALUES(?,?,?,?,?,?)",(experiment_id,*map(str,window),json.dumps(result)));self.connection.commit()

    def close(self) -> None:
        self.connection.close()
