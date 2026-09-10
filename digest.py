"""Module 4 — digest: render the ranked daily report.

Reads events+scores+enrichments for a date, renders Markdown to file + stdout.
LLM (optional, --narrative) writes a short intro from the computed numbers only —
it references figures, never recomputes or invents them.

CLI:
  python digest.py                    # today's events (by published_at date)
  python digest.py --date 2026-09-08
  python digest.py --days 3           # trailing N days in one digest
  python digest.py --narrative        # add LLM-written intro (needs ANTHROPIC_API_KEY)
Output: digests/digest_YYYY-MM-DD.md
"""
import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import config
import db
import score as scorer

DIGEST_DIR = Path(__file__).parent / "digests"
from zoneinfo import ZoneInfo  # add to imports



def load_events(conn, start: str, end: str):
    return conn.execute(
        """SELECT e.*, s.total, s.direction, s.components,
                  en.llm_summary, en.llm_catalyst, en.llm_extracted_json
           FROM events e
           JOIN scores s ON s.event_id = e.id AND s.config_version = ?
           LEFT JOIN enrichments en ON en.event_id = e.id
           WHERE substr(e.published_at, 1, 10) BETWEEN ? AND ?
           ORDER BY s.total DESC, e.published_at DESC""",
        (config.CONFIG_VERSION, start, end),
    ).fetchall()


def fmt_event(r) -> str:
    arrow = {1: "🟢▲", -1: "🔴▼", 0: "⚪"}[r["direction"]]
    comp = json.loads(r["components"])
    pt = ""
    if r["new_pt"]:
        pt = f" · PT ${r['old_pt']:g}→${r['new_pt']:g}" if r["old_pt"] else f" · PT ${r['new_pt']:g}"
        if r["price_at_post"]:
            upside = (r["new_pt"] - r["price_at_post"]) / r["price_at_post"] * 100
            pt += f" ({upside:+.0f}% vs ${r['price_at_post']:g})"
    elif r["price_at_post"]:
        pt = f" · @ ${r['price_at_post']:g}"    
    cluster = f" · 🔗{comp['cluster_peers']} peer(s)" if comp.get("cluster_peers") else ""
    lines = [
        f"**{r['ticker']}** {arrow} `{r['total']:.1f}` — {r['firm']} "
        f"({r['action'].replace('_', ' ')}){pt}{cluster}",
        f"  {r['news_title']}" if r["news_title"] else "",
    ]
    if r["llm_summary"]:
        lines.append(f"  > {r['llm_summary']}")
    if r["llm_catalyst"]:
        lines.append(f"  _catalyst: {r['llm_catalyst']}_")
    return "\n".join(l for l in lines if l)


def render(rows, start: str, end: str, narrative: str = "") -> str:
    buckets = {"act": [], "notable": [], "log": []}
    discarded = 0
    for r in rows:
        b = scorer.bucket(r["total"])
        if b == "discard":
            discarded += 1
        else:
            buckets[b].append(r)

    period = start if start == end else f"{start} → {end}"
    out = [f"# Analyst Ratings Digest — {period}", ""]
    if narrative:
        out += [narrative, ""]
    out.append(f"_{len(rows)} events scored · config {config.CONFIG_VERSION} · "
               f"{discarded} below digest threshold_")
    out.append("")

    titles = {"act": "🎯 Act / Investigate (20+)",
              "notable": "📌 Notable (8–20)",
              "log": "📋 Log (3–8)"}
    for key in ("act", "notable", "log"):
        if not buckets[key]:
            continue
        out += [f"## {titles[key]}", ""]
        for r in buckets[key]:
            out += [fmt_event(r), ""]

    if not any(buckets.values()):
        out.append("_Quiet day — nothing above the log threshold._")
    out.append("\n---\n_Informational only. High score = high-information headline, "
               "not a buy signal. Not financial advice._")
    return "\n".join(out)


def build_narrative(rows) -> str:
    """LLM intro from computed numbers only. Failure-isolated: returns '' on any error."""
    top = [
        {"ticker": r["ticker"], "score": r["total"],
         "direction": r["direction"], "action": r["action"], "firm": r["firm"]}
        for r in rows[:8] if r["total"] >= config.BUCKET_NOTABLE
    ]
    if not top:
        return ""
    try:
        from enrich import call_claude
        reply = call_claude(
            "You write a 2-3 sentence intro for a daily analyst-ratings digest. "
            "Use ONLY the numbers and facts in the JSON provided. Never invent price "
            "targets, reasons, or data not present. Plain prose, no markdown headers.",
            f"Today's top scored events:\n{json.dumps(top)}",
            max_tokens=250,
        )
        return reply.strip()
    except Exception as e:
        print(f"  narrative skipped: {e}")
        return ""


def run(target_date: str = None, days: int = 1, narrative: bool = False) -> Path:
    end = target_date or datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    start = (date.fromisoformat(end) - timedelta(days=days - 1)).isoformat()

    with db.connect() as conn:
        rows = load_events(conn, start, end)
        intro = build_narrative(rows) if narrative else ""
        text = render(rows, start, end, intro)
        db.log_run(conn, end, "digest", "ok", f"{len(rows)} events")

    DIGEST_DIR.mkdir(exist_ok=True)
    path = DIGEST_DIR / f"digest_{end}.md"
    path.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n[saved to {path}]")
    return path


def main():
    ap = argparse.ArgumentParser(description="Render daily digest")
    ap.add_argument("--date", help="YYYY-MM-DD (default today)")
    ap.add_argument("--days", type=int, default=1, help="Trailing days to include")
    ap.add_argument("--narrative", action="store_true", help="LLM-written intro")
    args = ap.parse_args()
    db.init_db()
    run(target_date=args.date, days=args.days, narrative=args.narrative)


if __name__ == "__main__":
    main()