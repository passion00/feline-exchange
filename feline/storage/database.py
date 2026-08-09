from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from threading import Lock

from feline.core.events import AIAnalysisResult, CandleUpdate, Event, NewsEvent, OrderUpdate, PortfolioSnapshot, PriceTick, RegimeEvent, RiskEvent, SignalEvent
from feline.portfolio.models import Position
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
        allowed = {"market_events", "news_events", "ai_analyses", "signals", "paper_orders", "paper_trades", "positions", "portfolio_snapshots", "risk_events", "system_events", "candles", "regime_events"}
        if table not in allowed:
            raise ValueError("Invalid table")
        return int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def latest_portfolio(self) -> tuple[float, dict[str, Position]] | None:
        row = self.connection.execute("SELECT cash FROM portfolio_snapshots ORDER BY id DESC LIMIT 1").fetchone()
        if row is None: return None
        positions = {r["instrument"]: Position(r["instrument"],r["quantity"],r["average_price"],r["realized_pnl"]) for r in self.connection.execute("SELECT * FROM positions")}
        return float(row["cash"]), positions

    def save_health(self, component: str, status: str, details: dict | None = None) -> None:
        with self.lock:
            self.connection.execute("INSERT INTO health_state VALUES (?,?,datetime('now'),?) ON CONFLICT(component) DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at,details=excluded.details", (component,status,json.dumps(details or {})))
            self.connection.commit()

    def health(self) -> list[dict]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM health_state ORDER BY component")]

    def close(self) -> None:
        self.connection.close()
