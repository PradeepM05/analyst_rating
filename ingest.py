"""Module 1 — ingest: pull FMP, normalize to one schema, dedupe, store EVERYTHING (US names).

No judgment. No LLM. Reiterations are kept (needed for clusters & backtests).
Standalone CLI:
    python ingest.py                  # pull latest 10 from market-wide feed (page 0)
    python ingest.py --symbol NOW     # per-symbol grades endpoint instead
    python ingest.py --from-file x.json   # ingest a saved JSON payload (testing/offline)

Notes for our FMP tier:
  - stable/grades-latest-news caps limit at 10 and only allows page=0 (402 otherwise),
    so daily coverage comes from running this repeatedly (cron) and letting dedupe stitch.
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

import config
import db

# Fallbacks in case config.py predates these settings.
US_ONLY = getattr(config, "US_ONLY", True)
FOREIGN_CURRENCY_TOKENS = getattr(config, "FOREIGN_CURRENCY_TOKENS", (
    " EUR ", " GBP ", " GBp ", " DKK ", " SEK ", " NOK ",
    " CHF ", " JPY ", " HKD ", " AUD ", " CAD ", " PLN ", " TRY ",
))

# ---------------------------------------------------------------- normalization

_BULLISH = ("buy", "outperform", "overweight", "positive", "strong")
_BEARISH = ("sell", "underperform", "underweight", "negative", "reduce")


def normalize_action(rec: dict) -> str:
    """Map FMP's action/grade fields to our enum.

    Initiations are checked FIRST: the stable feed's `action` field mislabels
    initiations as upgrade/downgrade (it appears to compare the new grade against
    consensus rather than a prior grade). An initiation has no previousGrade,
    and/or says "initiated" in the title — trust that over the action field.
    """
    action = (rec.get("action") or "").strip().lower()
    new_g = (rec.get("newGrade") or "").strip().lower()
    prev_g = (rec.get("previousGrade") or "").strip().lower()
    title = (rec.get("newsTitle") or "").lower()

    # --- initiations first (see docstring) ---
    if (not prev_g and new_g) or "initiat" in title:
        if any(w in new_g for w in _BULLISH):
            return "initiate_buy"
        if any(w in new_g for w in _BEARISH):
            return "initiate_sell"
        return "pt_change_only"   # neutral initiation ≈ weak signal

    if action in ("upgrade", "upgrades"):
        return "upgrade"
    if action in ("downgrade", "downgrades"):
        return "downgrade"

    if action in ("hold", "maintain", "maintains", "reiterate", "reiterates", "reiterated"):
        # Same grade maintained. PT may still have changed — on FMP that's only
        # visible in newsTitle, so call it pt_change_only when a target is
        # mentioned, else reiterate. Enrichment refines PTs later.
        if "price target" in title or " pt " in f" {title} " or "$" in title:
            return "pt_change_only"
        return "reiterate"

    # Grade-comparison fallback when the action field is missing/odd.
    if new_g and prev_g and new_g != prev_g:
        rank = {"sell": 0, "underperform": 0, "underweight": 0, "reduce": 0,
                "hold": 1, "neutral": 1, "market perform": 1, "equal-weight": 1, "equal weight": 1,
                "buy": 2, "outperform": 2, "overweight": 2, "accumulate": 2, "strong buy": 3}
        n, p = rank.get(new_g), rank.get(prev_g)
        if n is not None and p is not None:
            return "upgrade" if n > p else ("downgrade" if n < p else "reiterate")
    return "unknown"


def is_foreign(ev: dict) -> bool:
    """OTC ADR/foreign-ordinary heuristic + non-USD currency token in headline."""
    t = ev.get("ticker") or ""
    if len(t) == 5 and t[-1] in ("Y", "F"):
        return True
    title = f" {ev.get('news_title') or ''} "
    return any(tok in title for tok in FOREIGN_CURRENCY_TOKENS)


import re

# TheFly-style headline grammar. USD only — foreign currencies handled by the
# foreign filter / enrichment path; never write non-USD numbers into PT fields.
_PT_TO_FROM = re.compile(r"(?:price target|target)\s+(?:raised|lowered|cut|increased|reduced)?\s*to\s+\$([\d,]+(?:\.\d+)?)\s+from\s+\$([\d,]+(?:\.\d+)?)", re.I)
_PT_TO_ONLY = re.compile(r"(?:price target|target)\s+(?:of\s+)?(?:raised|lowered|cut|increased|reduced)?\s*to\s+\$([\d,]+(?:\.\d+)?)", re.I)


def extract_pt(title: str):
    """Regex PT extraction from headline. Returns (new_pt, old_pt) — either may be None."""
    if not title:
        return None, None
    m = _PT_TO_FROM.search(title)
    if m:
        return float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
    m = _PT_TO_ONLY.search(title)
    if m:
        return float(m.group(1).replace(",", "")), None
    return None, None


def is_roundup_title(title: str) -> bool:
    t = (title or "").lower()
    return any(p in t for p in getattr(config, "ROUNDUP_TITLE_PATTERNS", ()))


def normalize_fmp(rec: dict) -> dict:
    """FMP record -> unified event dict for db.insert_event."""
    ticker = (rec.get("symbol") or "").upper().strip()
    firm = rec.get("gradingCompany") or ""
    published = rec.get("publishedDate") or rec.get("date") or ""
    action = normalize_action(rec)
    new_pt, old_pt = extract_pt(rec.get("newsTitle") or "")
    return {
        "event_hash": db.event_hash(ticker, firm, published),
        "ticker": ticker,
        "published_at": published,
        "source": "fmp",
        "action": action,
        "firm": firm,
        "firm_tier": config.firm_tier(firm),
        "analyst": rec.get("analyst"),           # usually absent on free tier; stored anyway
        "new_grade": rec.get("newGrade"),
        "previous_grade": rec.get("previousGrade"),
        "new_pt": new_pt,                         # regex-extracted from headline (USD only);
        "old_pt": old_pt,                         # enrichment LLM refines ambiguous cases
        "price_at_post": rec.get("priceWhenPosted"),
        "news_title": rec.get("newsTitle"),
        "news_url": rec.get("newsURL"),
        "news_publisher": rec.get("newsPublisher"),
        "is_roundup": 1 if is_roundup_title(rec.get("newsTitle")) else 0,
        "raw_json": json.dumps(rec, separators=(",", ":")),
    }

# ---------------------------------------------------------------- fetching

def fetch_json(url: str) -> list:
    req = urllib.request.Request(url, headers={"User-Agent": "ratings-pipeline/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        raise RuntimeError(f"HTTP {e.code} from FMP: {body}") from e
    if isinstance(data, dict) and "Error Message" in data:
        raise RuntimeError(f"FMP error: {data['Error Message']}")
    return data if isinstance(data, list) else []


def fetch_rss_feed(page: int = 0, limit: int = 10) -> list:
    """Market-wide feed. Our tier: limit caps at 10, page must be 0."""
    q = urllib.parse.urlencode({"page": page, "limit": limit, "apikey": config.FMP_API_KEY})
    return fetch_json(f"{config.FMP_RSS_ENDPOINT}?{q}")


def fetch_symbol(symbol: str, limit: int = 100) -> list:
    q = urllib.parse.urlencode({"symbol": symbol, "limit": limit, "apikey": config.FMP_API_KEY})
    return fetch_json(f"{config.FMP_SYMBOL_ENDPOINT}?{q}")

# ---------------------------------------------------------------- main

def ingest_records(records: list, db_path=None) -> dict:
    stats = {"total": len(records), "inserted": 0, "duplicates": 0,
             "skipped": 0, "skipped_foreign": 0}
    with db.connect(db_path) as conn:
        for rec in records:
            try:
                ev = normalize_fmp(rec)
            except Exception:
                stats["skipped"] += 1
                continue
            if not ev["ticker"] or not ev["published_at"]:
                stats["skipped"] += 1
                continue
            if US_ONLY and is_foreign(ev):
                stats["skipped_foreign"] += 1
                continue
            if db.insert_event(conn, ev):
                stats["inserted"] += 1
            else:
                stats["duplicates"] += 1
        # shared-URL roundup pass: 3+ events citing one URL on one day = aggregator piece
        n_min = getattr(config, "ROUNDUP_SHARED_URL_MIN", 3)
        marked = conn.execute(
            """UPDATE events SET is_roundup = 1 WHERE is_roundup = 0 AND news_url IN (
                 SELECT news_url FROM events
                 WHERE news_url IS NOT NULL AND news_url != ''
                 GROUP BY news_url, substr(published_at, 1, 10)
                 HAVING COUNT(*) >= ?)""", (n_min,)).rowcount
        if marked:
            stats["marked_roundup"] = marked
        db.log_run(conn, date.today().isoformat(), "ingest", "ok", json.dumps(stats))
    return stats


def main():
    ap = argparse.ArgumentParser(description="Ingest analyst rating events")
    ap.add_argument("--symbol", help="Use per-symbol grades endpoint instead of market feed")
    ap.add_argument("--from-file", help="Ingest a saved JSON payload (offline/testing)")
    args = ap.parse_args()

    db.init_db()

    if args.from_file:
        records = json.loads(open(args.from_file).read())
    elif args.symbol:
        if not config.FMP_API_KEY:
            sys.exit("FMP_API_KEY not set (put it in .env)")
        records = fetch_symbol(args.symbol)
    else:
        if not config.FMP_API_KEY:
            sys.exit("FMP_API_KEY not set (put it in .env)")
        records = fetch_rss_feed(page=0)

    stats = ingest_records(records)
    print(f"ingest: {stats}")


if __name__ == "__main__":
    main()