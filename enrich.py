"""Module 3 — enrich: only events with score >= ENRICH_THRESHOLD.

Per event:
  (a) LLM extraction from news_title: {old_pt, new_pt, currency, rating, firm}
      -> written back onto events.new_pt/old_pt ONLY when currency is USD,
         then the event is rescored so magnitude/upside bonuses kick in.
  (b) fetch news_url article text (best-effort; failure-isolated)
  (c) LLM 2-sentence "why" summary + catalyst classification
      earnings_reaction | thesis_change | valuation_markup | macro_sector_call

Every prompt+response is logged to enrichments.llm_log for auditing.

CLI:
  python enrich.py                 # enrich all unenriched events >= threshold
  python enrich.py --dry-run       # show what would be enriched, no API calls
  python enrich.py --extract-only  # skip article fetch + summary (cheap mode)

Requires ANTHROPIC_API_KEY in .env or environment.
"""
import argparse
import json
import urllib.request
from datetime import date

import config
import db
import score as scorer

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"   # cheap, plenty for extraction/summary

EXTRACT_SYSTEM = """You extract structured data from analyst-rating headlines.
Respond with ONLY a JSON object, no markdown fences, no prose:
{"old_pt": <number or null>, "new_pt": <number or null>,
 "currency": "<ISO code like USD, EUR, DKK; use GBp for UK pence; null if none>",
 "rating": "<the rating mentioned, e.g. Buy, Overweight; null if none>",
 "firm": "<the firm named; null if none>"}
Numbers only for price targets (16100 not "DKK 16,100"). If the headline mentions
no price target, both pt fields are null."""

CLASSIFY_SYSTEM = """You analyze analyst-rating news articles. Respond with ONLY a JSON object:
{"summary": "<exactly 2 sentences on WHY the analyst made this call, or null>",
 "catalyst": "<one of: earnings_reaction | thesis_change | valuation_markup | macro_sector_call | insufficient_data>"}
earnings_reaction = reacting to just-reported results; thesis_change = new view on the
business; valuation_markup = mostly price/multiple housekeeping; macro_sector_call =
sector-wide or macro-driven adjustment.
Use insufficient_data (with summary null) when the article text is missing, paywalled,
navigation chrome, or a general market roundup that doesn't discuss THIS specific call.
Never infer reasoning that isn't actually stated in the article."""


# ---------------------------------------------------------------- anthropic call

def call_claude(system: str, user: str, max_tokens: int = 500) -> str:
    key = __import__("os").environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set (put it in .env)")
    body = json.dumps({
        "model": MODEL,
        "max_tokens": max_tokens,
        "temperature": 0,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode()
    req = urllib.request.Request(
        ANTHROPIC_URL, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode())
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


def parse_json_reply(text: str) -> dict:
    clean = text.replace("```json", "").replace("```", "").strip()
    return json.loads(clean)


# ---------------------------------------------------------------- article fetch

def fetch_article(url: str, max_chars: int = 8000) -> str:
    """Best-effort plain-text-ish fetch. Failure returns ''. Deliberately dumb:
    the LLM tolerates HTML noise better than a scraper tolerates layout changes."""
    if not url:
        return ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read(200_000).decode(errors="replace")
        # crude tag strip
        import re
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text[:max_chars]
    except Exception:
        return ""


# ---------------------------------------------------------------- enrichment

def extract_from_title(title: str, log: list) -> dict:
    prompt = f"Headline: {title}"
    reply = call_claude(EXTRACT_SYSTEM, prompt, max_tokens=200)
    log.append({"task": "extract", "prompt": prompt, "reply": reply})
    try:
        return parse_json_reply(reply)
    except Exception:
        return {}


def summarize_and_classify(title: str, article: str, log: list) -> dict:
    prompt = f"Headline: {title}\n\nArticle text (may be noisy):\n{article or '(no article available)'}"
    reply = call_claude(CLASSIFY_SYSTEM, prompt, max_tokens=300)
    log.append({"task": "classify", "prompt_chars": len(prompt), "reply": reply})
    try:
        return parse_json_reply(reply)
    except Exception:
        return {}


def enrich_event(conn, ev, extract_only: bool = False) -> dict:
    log = []
    extracted = extract_from_title(ev["news_title"] or "", log)

    # Write PTs back onto the event ONLY when currency matches the quoted price (USD).
    wrote_pts = False
    currency = (extracted.get("currency") or "").upper()
    if extracted.get("new_pt") and currency == "USD":
        conn.execute(
            "UPDATE events SET new_pt = ?, old_pt = ? WHERE id = ?",
            (extracted.get("new_pt"), extracted.get("old_pt"), ev["id"]),
        )
        wrote_pts = True

    summary, catalyst, article = None, None, ""
    if not extract_only:
        article = fetch_article(ev["news_url"])
        sc = summarize_and_classify(ev["news_title"] or "", article, log)
        catalyst = sc.get("catalyst")
        if catalyst == "insufficient_data":
            catalyst, summary = None, None
        else:
            summary = sc.get("summary")

    conn.execute(
        """INSERT OR REPLACE INTO enrichments
           (event_id, article_text, consensus_json, llm_extracted_json,
            llm_summary, llm_catalyst, llm_log)
           VALUES (?,?,?,?,?,?,?)""",
        (ev["id"], article or None, None, json.dumps(extracted),
         summary, catalyst, json.dumps(log)),
    )
    return {"event_id": ev["id"], "wrote_pts": wrote_pts,
            "currency": currency or None, "catalyst": catalyst}


def get_candidates(conn):
    return conn.execute(
        """SELECT e.* FROM events e
           JOIN scores s ON s.event_id = e.id AND s.config_version = ?
           LEFT JOIN enrichments en ON en.event_id = e.id
           WHERE s.total >= ? AND en.event_id IS NULL
           ORDER BY s.total DESC""",
        (config.CONFIG_VERSION, config.ENRICH_THRESHOLD),
    ).fetchall()


def run(dry_run: bool = False, extract_only: bool = False, db_path=None) -> dict:
    stats = {"candidates": 0, "enriched": 0, "pts_written": 0, "rescored": 0, "errors": 0}
    rescore_ids = []
    with db.connect(db_path) as conn:
        candidates = get_candidates(conn)
        stats["candidates"] = len(candidates)
        if dry_run:
            for ev in candidates:
                print(f"  would enrich: [{ev['id']}] {ev['ticker']} — {ev['news_title']}")
            return stats
        for ev in candidates:
            try:
                r = enrich_event(conn, ev, extract_only=extract_only)
                stats["enriched"] += 1
                if r["wrote_pts"]:
                    stats["pts_written"] += 1
                    rescore_ids.append(ev["id"])
            except Exception as e:
                stats["errors"] += 1
                print(f"  enrich failed for event {ev['id']}: {e}")
        db.log_run(conn, date.today().isoformat(), "enrich",
                   "ok" if stats["errors"] == 0 else "error", json.dumps(stats))
    # Rescore events whose PTs we just filled in, so magnitude/upside bonuses apply.
    for eid in rescore_ids:
        scorer.run(event_id=eid, db_path=db_path)
        stats["rescored"] += 1
    return stats


def main():
    ap = argparse.ArgumentParser(description="Enrich notable events with LLM context")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--extract-only", action="store_true",
                    help="PT extraction only; skip article fetch + summary")
    args = ap.parse_args()
    db.init_db()
    stats = run(dry_run=args.dry_run, extract_only=args.extract_only)
    print(f"enrich: {stats}")


if __name__ == "__main__":
    main()