# Analyst Ratings Daily Pipeline

Ingests analyst rating actions (upgrades / downgrades / price-target changes), scores
them for signal quality, enriches notable ones with LLM context, and emails a ranked
digest several times a day.

**Purpose: a screener.** The digest surfaces candidates matching a specific setup —
price target raised well above the current price, ideally corroborated by other firms
and backed by a recent earnings beat. It is not a recommendation engine: every
candidate is meant to be verified independently before any position is taken.
Educational/informational project; not financial advice.

## Pipeline

```
ingest -> score -> earnings -> enrich -> digest -> email
(FMP)    (pure DB) (calendar)  (LLM,     (Markdown) (Gmail)
                                >=8 only)
```

Scoring is cheap deterministic arithmetic and runs on everything; enrichment (network
+ LLM, fragile) runs last and only on notable events. Every stage is failure-isolated —
enrichment dying does not stop the digest.

## Files

| File | Role |
|---|---|
| `config.py` | All tunable constants (tiers, weights, upside band, candidate filter, roundup patterns), `.env` loading, `CONFIG_VERSION` |
| `db.py` | SQLite schema + helpers (`events`, `scores`, `enrichments`, `earnings`, `pipeline_runs`), WAL mode, auto-migration |
| `ingest.py` | Module 1 — FMP pull, normalization, regex PT extraction, US-only + roundup flagging, identity dedupe |
| `score.py` | Module 2 — deterministic scoring with component breakdown; versioned; `--rescore`, `--show` |
| `earnings.py` | Earnings calendar sync + proximity lookup ("reported 2d ago (beat)" / "reports in 3d") |
| `enrich.py` | Module 3 — Haiku PT extraction from ambiguous titles, 2-sentence "why", catalyst classification; USD-only write-back triggers rescore |
| `digest.py` | Module 4 — Candidates section + ticker-grouped buckets; `digests/digest_YYYY-MM-DD.md` |
| `notify.py` | Emails the digest via Gmail SMTP (App Password) |
| `run_daily.py` | Orchestrator with fail-fast config/secret guards; `--ingest-only`, `--email` |
| `probe_fmp.py` | Diagnostic: which tickers/endpoints the current FMP plan allows |
| `cleanup_db.py`, `migrate_v1_1.py`, `migrate_v1_2.py` | One-off repairs/migrations (idempotent) |
| `.github/workflows/pipeline.yml` | Actions schedule + DB persistence |

## Scoring (v1.2 — strategy-aligned)

```
score = base_action x firm_weight x (1 + magnitude_bonus + upside_bonus) + cluster_bonus
```

- **base_action**: upgrade/downgrade 10 · initiate_buy/sell 6 · **pt_change_only 6** · reiterate 0
  (raised from 3 in v1.1: a large target hike on a maintained rating is the target setup)
- **firm_weight**: Tier 1 x1.5 · Tier 2 x1.2 · other x1.0
- **magnitude_bonus**: |dPT|/old_PT, floor 5%, cap 30%
- **upside_bonus** (v1.2 curve): 0 below 5% · scales up 5–20% · **peak 0.60 across the
  20–40% band** · drops to 0.15 above 40% (stale target / broken stock, not a tradeable gap)
- **cluster_bonus**: same-direction Tier 1/2 peers, trailing 5 days, only *independent*
  ones (different `news_url`, non-roundup): 1 peer +4, 2+ peers +8

Digest sections: **Candidates** (PT raised, >=20% implied upside, score >= 5) ·
**Act 20+** · **Notable 8–20** · **Log 3–8** · below 3 stored but not shown.

Scores are keyed by `(event_id, config_version)` — retuning never destroys history.
**Regime notes:** v1.1 (regex PTs, roundup-aware clustering) and v1.2 (upside band,
candidates) both deployed Sep 2026; scores across versions are not comparable.

## Data source reality (measured, not assumed)

Run `python probe_fmp.py` to re-measure after any plan change. As of Sep 2026 on the
**free** plan:

- **Market-wide feed** (`/stable/grades-latest-news`): works, but `limit` caps at 10
  and only `page=0` is allowed. Coverage is therefore **sampled, not complete** —
  frequent pulls + identity dedupe stitch the day together, but busy mornings overflow
  a 10-record window.
- **Per-symbol** (`/stable/grades`): restricted to a ~dozen-ticker allowlist
  (AAPL, MSFT, NVDA, AMZN, GOOGL, JPM, UNH, UBER passed; CAT, NOW, SUNB, URI, WSM,
  DKS, TOL and all small caps returned 402). Returns full multi-year history for
  allowed names. **A watchlist sweep is not viable on the free plan** — this is what
  "US Coverage" on the $29 Starter plan buys.
- **Working free**: historical prices, price-target consensus, grades consensus,
  earnings calendar, company profile.
- **Finnhub** free tier no longer serves `upgrade-downgrade` (403, premium).
- v3/v4 FMP endpoints are dead for post-Aug-2025 keys — `/stable/` only.

Other hard-won notes:

- Numeric PTs live only in headline text; a regex catches "to $X from $Y" at ingest,
  Haiku handles ambiguous cases at enrichment. **USD only** — the feed carries
  EUR/DKK/GBp for foreign listings.
- Foreign listings filtered at ingest (5-letter tickers ending Y/F, non-USD tokens).
- Roundup articles ("top calls", shared `news_url` across 3+ same-day events) are
  flagged: still scored, never counted as independent corroboration. FMP sometimes
  mis-attributes a whole multi-stock roundup to one ticker — hence the visible
  "roundup-sourced (details unverified)" warning.
- TheFly links are JS-paywalled; the classifier returns `insufficient_data` rather
  than inventing reasoning.
- Dedupe key = hash(ticker + firm + day): event *identity*, excluding derived fields
  so renormalization can't create duplicates.

## Setup

Python 3.10+, stdlib only.

`.env` (never committed — plain `KEY=value` lines, no quotes, no fences):
```
FMP_API_KEY=...
ANTHROPIC_API_KEY=sk-ant-...
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=...        # Google Account -> Security -> 2-Step Verification -> App passwords
```

## Usage

```bash
python run_daily.py                   # full pipeline
python run_daily.py --ingest-only     # light: ingest + score only
python run_daily.py --email           # full + email
python ingest.py                      # single feed pull (latest 10)
python score.py --show                # ranked view
python score.py --rescore             # replay all scores under current CONFIG_VERSION
python earnings.py                    # sync earnings calendar (1 call)
python enrich.py --dry-run            # list enrichment candidates, no API calls
python digest.py --date 2026-09-16    # render a specific day
python notify.py --date 2026-09-16    # email a specific day
python probe_fmp.py                   # what does my plan allow? (~25 calls)
```

## Automation (GitHub Actions)

Private repo. `ratings.db` and `digests/` are **committed back** by the workflow —
that's the persistence layer. Secrets in Settings -> Secrets and variables -> Actions:
`FMP_API_KEY`, `ANTHROPIC_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`.

Schedule (UTC; set for EDT — shift +1 hour in November):
- Every 15 min, 10:00–14:45 UTC (6:00–10:45 ET) -> `--ingest-only`
- Every 30 min, 15:00–23:30 UTC (11:00–19:30 ET) -> `--ingest-only`
- Digest + email at 15:00, 21:00, 03:00, 09:00 UTC (11:00, 17:00, 23:00, 05:00 ET)

~48 FMP calls/day against the 250 free cap (~19%). Analyst actions run all day, not
just pre-market, so the window is deliberately wide.

**Careful:** the four digest cron strings must stay character-identical to the ones in
the "Decide run mode" step, or every run silently becomes ingest-only and no email is
sent. Cron is best-effort (5–20 min late, occasionally dropped); the cadence absorbs it.

## Roadmap

- [ ] Consensus context in the digest ("Street: 12 Buy / 4 Hold" — endpoint confirmed free)
- [ ] Per-digest "new since last report" mode (currently each digest covers the full day)
- [ ] Contrarian-vs-chase flag (target moved against recent price action)
- [ ] `backtest.py` — hit rate of +5%/+10% within 5/10/20 days by bucket and setup
      (historical price endpoint confirmed free; schema ready — it's a join)
- [ ] MCP server exposing the DB to Claude Desktop (`query_events`, `get_top_scored`)
- [ ] Decide on FMP Starter ($29/mo) once miss rate is observed — buys the full ticker
      universe and paging, i.e. complete rather than sampled coverage

## Caveats

Sampled coverage means real setups are missed — mid-caps during busy mornings most of
all. Analyst targets lag; if the stock already gapped on the news, the move may be gone
before the digest arrives. A "reports in Nd" flag is a warning, not a detail: a target
raise days before earnings is a very different risk from one after a beat. Take-profit
rules without a downside exit turn small frequent gains into occasional large losses.
Paper-trade before committing real money.