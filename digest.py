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
from zoneinfo import ZoneInfo
from pathlib import Path

import config
import db
import score as scorer

DIGEST_DIR = Path(__file__).parent / "digests"


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


def is_candidate(r) -> bool:
    """Your setup: PT raised, meaningful implied upside, not noise."""
    if not r["new_pt"] or not r["price_at_post"]:
        return False
    if r["total"] < getattr(config, "CANDIDATE_MIN_SCORE", 5.0):
        return False
    if getattr(config, "CANDIDATE_REQUIRE_PT_RAISE", True):
        if r["old_pt"] and r["new_pt"] <= r["old_pt"]:
            return False
    upside = (r["new_pt"] - r["price_at_post"]) / r["price_at_post"]
    return upside >= getattr(config, "CANDIDATE_MIN_UPSIDE", 0.20)


def _pt_str(r) -> str:
    """PT display: change %% when both PTs known, implied upside vs current price."""
    bits = []
    if r["new_pt"] and r["old_pt"]:
        chg = (r["new_pt"] - r["old_pt"]) / r["old_pt"] * 100
        bits.append(f"PT ${r['old_pt']:g}\u2192${r['new_pt']:g} ({chg:+.0f}%)")
    elif r["new_pt"]:
        bits.append(f"PT ${r['new_pt']:g}")
    if r["new_pt"] and r["price_at_post"]:
        upside = (r["new_pt"] - r["price_at_post"]) / r["price_at_post"] * 100
        bits.append(f"implied {upside:+.0f}% vs ${r['price_at_post']:g}")
    return " \u00b7 ".join(bits)


def _action_line(r) -> str:
    arrow = {1: "\u25b2", -1: "\u25bc", 0: "\u00b7"}[r["direction"]]
    pt = _pt_str(r)
    line = f"  {arrow} `{r['total']:.1f}` {r['firm']} \u2014 {r['action'].replace('_', ' ')}"
    if pt:
        line += f" \u00b7 {pt}"
    if r["llm_catalyst"]:
        line += f" \u00b7 _{r['llm_catalyst']}_"
    return line


def _earnings_note(conn, ticker: str, when: str) -> str:
    if conn is None:
        return ""
    try:
        import earnings as earn
        label, _ = earn.proximity(conn, ticker, when)
        return f" \u00b7 \U0001f4c5 {label}" if label else ""
    except Exception:
        return ""


def fmt_candidate(r, note: str = "") -> str:
    upside = (r["new_pt"] - r["price_at_post"]) / r["price_at_post"] * 100
    chg = ""
    if r["old_pt"]:
        chg = f" (raised from ${r['old_pt']:g}, {((r['new_pt']-r['old_pt'])/r['old_pt']*100):+.0f}%)"
    comp = json.loads(r["components"])
    peers = comp.get("cluster_peers", 0)
    corrob = (f"\u2713 {peers} independent peer(s) agree" if peers
              else "\u26a0 no independent corroboration")
    lines = [
        f"**{r['ticker']}** \u2014 **{upside:+.0f}% implied upside** \u00b7 `{r['total']:.1f}`",
        f"  PT ${r['new_pt']:g}{chg} vs ${r['price_at_post']:g} \u00b7 {r['firm']} "
        f"({r['firm_tier']}){note}",
        f"  {corrob}" + ("  \u00b7 \U0001f4f0 roundup-sourced" if r["is_roundup"] else ""),
    ]
    if r["news_title"]:
        lines.append(f"  \u2014 {r['news_title']}")
    if r["llm_summary"]:
        lines.append(f"  > {r['llm_summary']}")
    return "\n".join(lines)


def fmt_ticker_group(ticker: str, rows: list) -> str:
    """One entry per ticker: header with combined signal, sub-lines per firm action."""
    best = rows[0]
    ups = sum(1 for r in rows if r["direction"] == 1)
    downs = sum(1 for r in rows if r["direction"] == -1)
    tier1 = sum(1 for r in rows if json.loads(r["components"]).get("firm_tier") == "tier1")
    all_roundup = all(r["is_roundup"] for r in rows)
    any_roundup = any(r["is_roundup"] for r in rows)

    if ups and downs:
        signal = f"\u26a1 mixed {ups}\u25b2/{downs}\u25bc"
    elif ups:
        signal = f"\U0001f7e2 {ups}\u25b2 bullish" if ups > 1 else "\U0001f7e2 \u25b2"
    elif downs:
        signal = f"\U0001f534 {downs}\u25bc bearish" if downs > 1 else "\U0001f534 \u25bc"
    else:
        signal = "\u26aa"

    head = f"**{ticker}** {signal} \u00b7 top `{best['total']:.1f}`"
    if best["price_at_post"]:
        head += f" \u00b7 @ ${best['price_at_post']:g}"
    if len(rows) > 1:
        story = f"{len(rows)} firms acted"
        if tier1:
            story += f" ({tier1} Tier 1)"
        head += f" \u00b7 {story}"
    if any_roundup:
        head += " \u00b7 \U0001f4f0 roundup-sourced" + (" (details unverified)" if all_roundup else "")

    lines = [head]
    lines += [_action_line(r) for r in rows]
    # one title per unique headline, not per event
    seen_titles = []
    for r in rows:
        t = r["news_title"]
        if t and t not in seen_titles:
            seen_titles.append(t)
    for t in seen_titles[:2]:
        lines.append(f"  \u2014 {t}")
    for r in rows:
        if r["llm_summary"]:
            lines.append(f"  > {r['llm_summary']}")
            break
    return "\n".join(lines)


def render(rows, start: str, end: str, narrative: str = "", conn=None) -> str:
    # group by ticker; a ticker's bucket = its best event's bucket
    by_ticker = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)
    for t in by_ticker:
        by_ticker[t].sort(key=lambda r: -r["total"])

    buckets = {"act": [], "notable": [], "log": []}
    discarded = 0
    shown_events = 0
    for t, trs in sorted(by_ticker.items(), key=lambda kv: -kv[1][0]["total"]):
        visible = [r for r in trs if scorer.bucket(r["total"]) != "discard"]
        discarded += len(trs) - len(visible)
        if not visible:
            continue
        b = scorer.bucket(visible[0]["total"])
        buckets[b].append((t, visible))
        shown_events += len(visible)

    period = start if start == end else f"{start} \u2192 {end}"
    out = [f"# Analyst Ratings Digest \u2014 {period}", ""]
    if narrative:
        out += [narrative, ""]
    out.append(f"_{len(rows)} events \u00b7 {len(by_ticker)} tickers \u00b7 config "
               f"{config.CONFIG_VERSION} \u00b7 {discarded} below digest threshold_")
    out.append("")

    # --- Candidates: the setup you actually trade ---
    # rank by score: it already encodes firm tier, the 20-40% upside band,
    # and independent corroboration — raw upside alone flatters stale targets.
    cands = sorted([r for r in rows if is_candidate(r)], key=lambda r: -r["total"])
    if cands:
        out += ["## \U0001f3af Candidates \u2014 PT raised, "
                f"\u2265{int(getattr(config, 'CANDIDATE_MIN_UPSIDE', 0.2)*100)}% implied upside", ""]
        for r in cands:
            out += [fmt_candidate(r, _earnings_note(conn, r["ticker"], r["published_at"])), ""]
        out += ["_Screening output \u2014 verify independently before acting._", ""]

    titles = {"act": "\U0001f3af Act / Investigate (20+)",
              "notable": "\U0001f4cc Notable (8\u201320)",
              "log": "\U0001f4cb Log (3\u20138)"}
    for key in ("act", "notable", "log"):
        if not buckets[key]:
            continue
        out += [f"## {titles[key]}", ""]
        for t, trs in buckets[key]:
            out += [fmt_ticker_group(t, trs), ""]

    if not any(buckets.values()):
        out.append("_Quiet day \u2014 nothing above the log threshold._")
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
        text = render(rows, start, end, intro, conn=conn)
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