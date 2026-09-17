"""Earnings proximity (v1.2) — sync FMP's earnings calendar into the DB so the
digest can flag "reported 2d ago" / "reports in 3d" next to each candidate.

One FMP call per run (the calendar endpoint is date-ranged, not per-symbol), so
this is cheap: run it once per digest cycle.

    python earnings.py            # sync a window around today
    python earnings.py --days 30  # wider window
"""
import argparse
import json
import urllib.parse
from datetime import date, datetime, timedelta

import config
import db
import ingest  # reuse fetch_json (handles FMP error bodies)

SCHEMA = """
CREATE TABLE IF NOT EXISTS earnings (
    ticker     TEXT NOT NULL,
    date       TEXT NOT NULL,          -- YYYY-MM-DD
    eps_actual REAL,
    eps_est    REAL,
    rev_actual REAL,
    rev_est    REAL,
    synced_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_earnings_date ON earnings(date);
"""


def ensure_table(conn):
    conn.executescript(SCHEMA)


def fetch_calendar(from_date: str, to_date: str) -> list:
    q = urllib.parse.urlencode({"from": from_date, "to": to_date,
                                "apikey": config.FMP_API_KEY})
    return ingest.fetch_json(f"{config.FMP_EARNINGS_ENDPOINT}?{q}")


def sync(days_back: int = 10, days_fwd: int = 15, db_path=None) -> dict:
    today = date.today()
    frm = (today - timedelta(days=days_back)).isoformat()
    to = (today + timedelta(days=days_fwd)).isoformat()
    records = fetch_calendar(frm, to)

    stats = {"fetched": len(records), "stored": 0}
    with db.connect(db_path) as conn:
        ensure_table(conn)
        for r in records:
            sym = (r.get("symbol") or "").upper().strip()
            d = (r.get("date") or r.get("reportedDate") or "")[:10]
            if not sym or not d:
                continue
            conn.execute(
                """INSERT OR REPLACE INTO earnings
                   (ticker, date, eps_actual, eps_est, rev_actual, rev_est)
                   VALUES (?,?,?,?,?,?)""",
                (sym, d,
                 r.get("epsActual") if r.get("epsActual") is not None else r.get("eps"),
                 r.get("epsEstimated") if r.get("epsEstimated") is not None else r.get("epsEstimate"),
                 r.get("revenueActual") if r.get("revenueActual") is not None else r.get("revenue"),
                 r.get("revenueEstimated") if r.get("revenueEstimated") is not None else r.get("revenueEstimate")),
            )
            stats["stored"] += 1
        db.log_run(conn, today.isoformat(), "earnings", "ok", json.dumps(stats))
    return stats


def proximity(conn, ticker: str, event_date: str):
    """Return (label, beat_flag) for the earnings nearest this event, or (None, None).

    label e.g. "reported 2d ago (beat)" or "reports in 3d".
    """
    try:
        ensure_table(conn)
        ev_day = datetime.fromisoformat(event_date.replace("Z", "+00:00")).date()
    except Exception:
        return None, None
    recent_days = getattr(config, "EARNINGS_RECENT_DAYS", 5)
    up_days = getattr(config, "EARNINGS_UPCOMING_DAYS", 10)
    rows = conn.execute(
        "SELECT date, eps_actual, eps_est FROM earnings WHERE ticker = ?", (ticker,)
    ).fetchall()
    best = None
    for r in rows:
        try:
            d = date.fromisoformat(r["date"])
        except ValueError:
            continue
        delta = (d - ev_day).days
        if -recent_days <= delta <= up_days:
            if best is None or abs(delta) < abs(best[0]):
                best = (delta, r)
    if not best:
        return None, None
    delta, r = best
    beat = None
    if r["eps_actual"] is not None and r["eps_est"] not in (None, 0):
        beat = r["eps_actual"] > r["eps_est"]
    if delta <= 0:
        label = f"reported {abs(delta)}d ago"
    else:
        label = f"reports in {delta}d"
    if beat is True:
        label += " (beat)"
    elif beat is False:
        label += " (miss)"
    return label, beat


def main():
    ap = argparse.ArgumentParser(description="Sync FMP earnings calendar")
    ap.add_argument("--days", type=int, default=15, help="days forward to fetch")
    ap.add_argument("--days-back", type=int, default=10)
    args = ap.parse_args()
    db.init_db()
    print("earnings:", sync(days_back=args.days_back, days_fwd=args.days))


if __name__ == "__main__":
    main()