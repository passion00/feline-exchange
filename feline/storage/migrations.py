MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS market_events (id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, event_type TEXT NOT NULL, instrument TEXT, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS news_events (id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, source TEXT, headline TEXT NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS ai_analyses (id TEXT PRIMARY KEY, job_id TEXT NOT NULL, timestamp TEXT NOT NULL, instrument TEXT, available INTEGER NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS signals (id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, instrument TEXT NOT NULL, strategy TEXT NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS paper_orders (id TEXT PRIMARY KEY, request_id TEXT, timestamp TEXT NOT NULL, instrument TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS paper_trades (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, timestamp TEXT NOT NULL, instrument TEXT NOT NULL, quantity REAL NOT NULL, price REAL NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS positions (instrument TEXT PRIMARY KEY, quantity REAL NOT NULL, average_price REAL NOT NULL, realized_pnl REAL NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS portfolio_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, equity REAL NOT NULL, cash REAL NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS risk_events (id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, approved INTEGER NOT NULL, rule TEXT NOT NULL, order_request_id TEXT, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS system_events (id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, event_type TEXT NOT NULL, severity TEXT NOT NULL, payload TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_market_instrument_time ON market_events(instrument, timestamp);
    CREATE INDEX IF NOT EXISTS idx_orders_request ON paper_orders(request_id);
    CREATE INDEX IF NOT EXISTS idx_risk_request ON risk_events(order_request_id);
    """,
)

