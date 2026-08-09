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
    """
    CREATE TABLE IF NOT EXISTS candles (id TEXT PRIMARY KEY, instrument TEXT NOT NULL, timeframe TEXT NOT NULL, open_time TEXT NOT NULL, close_time TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(instrument,timeframe,open_time));
    CREATE TABLE IF NOT EXISTS regime_events (id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, instrument TEXT NOT NULL, previous TEXT NOT NULL, current TEXT NOT NULL, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS news_dedup (fingerprint TEXT PRIMARY KEY, first_seen TEXT NOT NULL, event_id TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS health_state (component TEXT PRIMARY KEY, status TEXT NOT NULL, updated_at TEXT NOT NULL, details TEXT NOT NULL);
    """,
    """
    CREATE TABLE IF NOT EXISTS fills (id TEXT PRIMARY KEY,order_id TEXT NOT NULL,timestamp TEXT NOT NULL,instrument TEXT NOT NULL,quantity REAL NOT NULL,fill_price REAL NOT NULL,commission REAL NOT NULL,spread_cost REAL NOT NULL,slippage_cost REAL NOT NULL,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS financing_charges (id TEXT PRIMARY KEY,timestamp TEXT NOT NULL,instrument TEXT NOT NULL,amount REAL NOT NULL,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS broker_state (singleton INTEGER PRIMARY KEY CHECK(singleton=1),cash REAL NOT NULL,payload TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS pending_orders (order_id TEXT PRIMARY KEY,state TEXT NOT NULL,remaining_quantity REAL NOT NULL,payload TEXT NOT NULL,updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS experiments (experiment_id TEXT PRIMARY KEY,status TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL,result TEXT,error TEXT);
    CREATE TABLE IF NOT EXISTS walk_forward_windows (id INTEGER PRIMARY KEY AUTOINCREMENT,experiment_id TEXT NOT NULL,train_start TEXT,train_end TEXT,test_start TEXT,test_end TEXT,result TEXT);
    CREATE TABLE IF NOT EXISTS trades (trade_id TEXT PRIMARY KEY,instrument TEXT NOT NULL,status TEXT NOT NULL,entry_time TEXT NOT NULL,exit_time TEXT,payload TEXT NOT NULL);
    """,
    """
    CREATE TABLE IF NOT EXISTS replay_sessions (replay_session_id TEXT PRIMARY KEY,dataset_path TEXT NOT NULL,dataset_checksum TEXT NOT NULL,started_at TEXT,ended_at TEXT,status TEXT NOT NULL,payload TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_replay_sessions_started ON replay_sessions(started_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS dataset_registry (checksum TEXT PRIMARY KEY,path TEXT NOT NULL,provider TEXT,instrument TEXT,timeframe TEXT,payload TEXT NOT NULL,registered_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS research_experiments (experiment_id TEXT PRIMARY KEY,status TEXT NOT NULL,created_at TEXT NOT NULL,manifest_checksum TEXT NOT NULL,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS research_episodes (episode_id TEXT NOT NULL,experiment_id TEXT NOT NULL,event_id TEXT NOT NULL,replay_session_id TEXT,status TEXT NOT NULL,split TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(experiment_id,episode_id));
    CREATE TABLE IF NOT EXISTS event_results (experiment_id TEXT NOT NULL,event_id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(experiment_id,event_id));
    CREATE TABLE IF NOT EXISTS aggregate_results (experiment_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS research_exclusions (experiment_id TEXT NOT NULL,event_id TEXT NOT NULL,reason TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(experiment_id,event_id));
    CREATE INDEX IF NOT EXISTS idx_research_episode_experiment ON research_episodes(experiment_id);
    """,
)
