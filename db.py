"""SQLite schema + helpers.

Tables per the brief:
  events       raw normalized events (module 1)  — store EVERYTHING incl. reiterations
  scores       total + component breakdown JSON + config_version (module 2)
  enrichments  article text, consensus data, LLM outputs (module 3)
  pipeline_runs  per-stage status so a failed stage can be rerun alone

backtest.py later = a join of events+scores against price data. No schema change needed.
"""
import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY,
    event_hash    TEXT NOT NULL UNIQUE,          -- dedupe: hash(ticker+firm+date+action)
    ticker        TEXT NOT NULL,
    published_at  TEXT NOT NULL,                 -- ISO8601 from source
    ingested_at   TEXT NOT NULL DEFAULT (datetime('now')),
    source        TEXT NOT NULL,                 -- 'fmp' | 'finnhub' | ...
    action        TEXT NOT NULL,                 -- normalized: upgrade|downgrade|initiate_buy|initiate_sell|pt_change_only|reiterate|unknown
    firm          TEXT,
    firm_tier     TEXT,                          -- tier1|tier2|other (denormalized for easy queries)
    analyst       TEXT,                          -- store from day one (usually NULL on FMP free tier)
    new_grade     TEXT,
    previous_grade TEXT,
    new_pt        REAL,                          -- often NULL until enrichment parses newsTitle
    old_pt        REAL,
    price_at_post REAL,
    news_title    TEXT,
    news_url      TEXT,
    news_publisher TEXT,
    raw_json      TEXT NOT NULL                  -- full original payload, always
);
CREATE INDEX IF NOT EXISTS idx_events_ticker_date ON events(ticker, published_at);

CREATE TABLE IF NOT EXISTS scores (
    event_id       INTEGER NOT NULL REFERENCES events(id),
    config_version TEXT NOT NULL,
    scored_at      TEXT NOT NULL DEFAULT (datetime('now')),
    total          REAL NOT NULL,                -- strength (unsigned)
    direction      INTEGER NOT NULL,             -- +1 bullish / -1 bearish / 0 neutral
    components     TEXT NOT NULL,                -- JSON breakdown: base, firm_weight, magnitude_bonus, upside_bonus, cluster_bonus
    PRIMARY KEY (event_id, config_version)       -- versioned: old scores survive retuning
);

CREATE TABLE IF NOT EXISTS enrichments (
    event_id      INTEGER PRIMARY KEY REFERENCES events(id),
    enriched_at   TEXT NOT NULL DEFAULT (datetime('now')),
    article_text  TEXT,
    consensus_json TEXT,
    llm_extracted_json TEXT,                     -- {old_pt, new_pt, rating, firm}
    llm_summary   TEXT,                          -- 2-sentence "why"
    llm_catalyst  TEXT,                          -- earnings_reaction|thesis_change|valuation_markup|macro_sector_call
    llm_log       TEXT                           -- prompts + responses for auditing
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id         INTEGER PRIMARY KEY,
    run_date   TEXT NOT NULL,                    -- YYYY-MM-DD
    stage      TEXT NOT NULL,                    -- ingest|score|enrich|digest
    status     TEXT NOT NULL,                    -- ok|error
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    detail     TEXT
);
"""

@contextmanager
def connect(db_path: Path = None):
    conn = sqlite3.connect(db_path or config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db(db_path: Path = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)

def event_hash(ticker: str, firm: str, published_date: str) -> str:
    """Dedupe key = event identity. Derived fields (action) deliberately excluded:
    renormalizing must not create duplicates."""
    day = (published_date or "")[:10]
    key = "|".join(x.strip().lower() for x in (ticker or "", firm or "", day))
    return hashlib.sha256(key.encode()).hexdigest()[:16]

def insert_event(conn, ev: dict) -> bool:
    """Insert one normalized event. Returns True if inserted, False if duplicate."""
    try:
        conn.execute(
            """INSERT INTO events (event_hash, ticker, published_at, source, action,
                   firm, firm_tier, analyst, new_grade, previous_grade,
                   new_pt, old_pt, price_at_post, news_title, news_url,
                   news_publisher, raw_json)
               VALUES (:event_hash, :ticker, :published_at, :source, :action,
                   :firm, :firm_tier, :analyst, :new_grade, :previous_grade,
                   :new_pt, :old_pt, :price_at_post, :news_title, :news_url,
                   :news_publisher, :raw_json)""",
            ev,
        )
        return True
    except sqlite3.IntegrityError:
        return False

def log_run(conn, run_date: str, stage: str, status: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO pipeline_runs (run_date, stage, status, detail) VALUES (?,?,?,?)",
        (run_date, stage, status, detail),
    )
