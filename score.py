"""Module 2 — score: read unscored events, compute score + component breakdown, write to DB.

Pure DB->DB. Zero network. No LLM. Re-runnable over history when retuning:
    python score.py                      # score events not yet scored under CONFIG_VERSION
    python score.py --rescore            # rescore ALL events under current CONFIG_VERSION
    python score.py --event-id 42        # score one event (debugging)
Old scores are never overwritten across versions: (event_id, config_version) is the PK,
so bumping CONFIG_VERSION in config.py preserves history for backtest comparisons.
"""
import argparse
import json
from datetime import date, datetime, timedelta

import config
import db

# ---------------------------------------------------------------- components

def base_action_score(action: str) -> float:
    return float(config.BASE_ACTION.get(action, 0))

def magnitude_bonus(new_pt, old_pt) -> float:
    """abs(new-old)/old, noise-floored and capped. 0 when PTs unknown (pre-enrichment)."""
    if not new_pt or not old_pt:
        return 0.0
    pct = abs(new_pt - old_pt) / abs(old_pt)
    if pct < config.MAGNITUDE_NOISE_FLOOR:
        return 0.0
    return min(pct, config.MAGNITUDE_CAP)

def upside_bonus(new_pt, price) -> float:
    """(target-price)/price with stale-analyst guard above 50%."""
    if not new_pt or not price:
        return 0.0
    u = abs((new_pt - price) / price)
    if u < config.UPSIDE_NOISE_FLOOR:
        return 0.0
    if u > config.UPSIDE_STALE_THRESHOLD:
        return config.UPSIDE_STALE_BONUS
    return min(u, config.UPSIDE_CAP)

def direction_of(action: str, new_grade, prev_grade, new_pt, old_pt) -> int:
    """+1 bullish / -1 bearish / 0 neutral-unknown."""
    if action in ("upgrade", "initiate_buy"):
        return 1
    if action in ("downgrade", "initiate_sell"):
        return -1
    if action == "pt_change_only" and new_pt and old_pt:
        return 1 if new_pt > old_pt else (-1 if new_pt < old_pt else 0)
    if action == "pt_change_only":
        # Direction unknown until enrichment parses PTs; default from grade bullishness.
        g = (new_grade or "").lower()
        if any(w in g for w in ("buy", "outperform", "overweight", "positive")):
            return 1
        if any(w in g for w in ("sell", "underperform", "underweight", "negative")):
            return -1
    return 0

def cluster_count(conn, event) -> int:
    """Same-direction Tier1/2 events on same ticker in trailing CLUSTER_WINDOW_DAYS,
    excluding this event. Direction proxy: same normalized action bucket sign.
    """
    ev_dir = direction_of(event["action"], event["new_grade"],
                          event["previous_grade"], event["new_pt"], event["old_pt"])
    if ev_dir == 0:
        return 0
    try:
        ev_date = datetime.fromisoformat(event["published_at"].replace("Z", "+00:00"))
    except ValueError:
        return 0
    window_start = (ev_date - timedelta(days=config.CLUSTER_WINDOW_DAYS)).isoformat()

    rows = conn.execute(
        """SELECT id, action, new_grade, previous_grade, new_pt, old_pt, firm
           FROM events
           WHERE ticker = ? AND id != ? AND firm_tier IN ('tier1','tier2')
             AND published_at >= ? AND published_at <= ?
             AND firm != ?""",
        (event["ticker"], event["id"], window_start, event["published_at"],
         event["firm"] or ""),
    ).fetchall()

    n = 0
    seen_firms = set()
    for r in rows:
        d = direction_of(r["action"], r["new_grade"], r["previous_grade"],
                         r["new_pt"], r["old_pt"])
        if d == ev_dir and r["firm"] not in seen_firms:
            seen_firms.add(r["firm"])
            n += 1
    return n

def cluster_bonus(n: int) -> float:
    return float(config.CLUSTER_BONUS[min(n, max(config.CLUSTER_BONUS))])

# ---------------------------------------------------------------- scoring

def score_event(conn, event) -> dict:
    """Compute score + full component breakdown for one event row."""
    base = base_action_score(event["action"])
    fw = config.FIRM_WEIGHT.get(event["firm_tier"], 1.0)
    mag = magnitude_bonus(event["new_pt"], event["old_pt"])
    ups = upside_bonus(event["new_pt"], event["price_at_post"])
    n_peers = cluster_count(conn, event)
    clus = cluster_bonus(n_peers)
    direction = direction_of(event["action"], event["new_grade"],
                             event["previous_grade"], event["new_pt"], event["old_pt"])

    total = base * fw * (1 + mag + ups) + clus
    return {
        "total": round(total, 3),
        "direction": direction,
        "components": {
            "base_action": base,
            "firm_weight": fw,
            "magnitude_bonus": round(mag, 4),
            "upside_bonus": round(ups, 4),
            "cluster_peers": n_peers,
            "cluster_bonus": clus,
            "action": event["action"],
            "firm_tier": event["firm_tier"],
        },
    }


def bucket(total: float) -> str:
    if total >= config.BUCKET_ACT:
        return "act"
    if total >= config.BUCKET_NOTABLE:
        return "notable"
    if total >= config.BUCKET_LOG:
        return "log"
    return "discard"

# ---------------------------------------------------------------- runner

def run(rescore: bool = False, event_id: int = None, db_path=None) -> dict:
    stats = {"scored": 0, "skipped_existing": 0}
    with db.connect(db_path) as conn:
        if event_id:
            rows = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchall()
        elif rescore:
            rows = conn.execute("SELECT * FROM events ORDER BY id").fetchall()
        else:
            rows = conn.execute(
                """SELECT e.* FROM events e
                   LEFT JOIN scores s ON s.event_id = e.id AND s.config_version = ?
                   WHERE s.event_id IS NULL ORDER BY e.id""",
                (config.CONFIG_VERSION,),
            ).fetchall()

        for ev in rows:
            result = score_event(conn, ev)
            conn.execute(
                """INSERT OR REPLACE INTO scores
                   (event_id, config_version, total, direction, components)
                   VALUES (?,?,?,?,?)""",
                (ev["id"], config.CONFIG_VERSION, result["total"],
                 result["direction"], json.dumps(result["components"])),
            )
            stats["scored"] += 1

        db.log_run(conn, date.today().isoformat(), "score", "ok", json.dumps(stats))
    return stats


def main():
    ap = argparse.ArgumentParser(description="Score ingested events")
    ap.add_argument("--rescore", action="store_true",
                    help="Rescore all events under current CONFIG_VERSION")
    ap.add_argument("--event-id", type=int, help="Score a single event by id")
    ap.add_argument("--show", action="store_true",
                    help="Print scored events for today, ranked")
    args = ap.parse_args()

    db.init_db()
    stats = run(rescore=args.rescore, event_id=args.event_id)
    print(f"score: {stats} (config {config.CONFIG_VERSION})")

    if args.show:
        with db.connect() as conn:
            rows = conn.execute(
                """SELECT e.ticker, e.action, e.firm, s.total, s.direction, s.components
                   FROM scores s JOIN events e ON e.id = s.event_id
                   WHERE s.config_version = ?
                   ORDER BY s.total DESC LIMIT 20""",
                (config.CONFIG_VERSION,),
            ).fetchall()
        for r in rows:
            arrow = {1: "▲", -1: "▼", 0: "·"}[r["direction"]]
            print(f"{r['total']:6.1f} {arrow} [{bucket(r['total']):8s}] "
                  f"{r['ticker']:6s} {r['action']:15s} {r['firm']}")


if __name__ == "__main__":
    main()