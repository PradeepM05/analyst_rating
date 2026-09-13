# Analyst Ratings Daily Pipeline

A daily job that ingests analyst rating actions (upgrades / downgrades / price-target
changes), scores them for signal quality, enriches notable ones with LLM context, and
emails a ranked digest every weekday morning.

**High score = high-information headline, not a buy signal.** Initial weights are
reasoned priors, not fitted — the first months are data collection for tuning via
backtests. Educational/informational project; not financial advice.

## Pipeline

```
ingest  ->  score  ->  enrich  ->  digest  ->  email
(FMP)      (pure DB)   (LLM, >=8   (Markdown)  (Gmail)
                        only)
```

Order matters: scoring is cheap deterministic arithmetic on structured fields and runs
on everything; enrichment (network + LLM, fragile) runs last and only on notable
events. Every stage is failure-isolated — enrichment dying does not stop the digest.

## Files

| File | Role |
|---|---|
| `config.py` | All tunable constants (tiers, weights, thresholds, roundup patterns), `.env` loading, `CONFIG_VERSION` |
| `db.py` | SQLite schema + helpers (`events`, `scores`, `enrichments`, `pipeline_runs`), WAL mode, auto-migration |
| `ingest.py` | Module 1 — FMP pull, normalization, regex PT extraction, US-only + roundup flagging, hash dedupe |
| `score.py` | Module 2 — deterministic scoring with component breakdown; versioned; re-runnable (`--rescore`, `--show`) |
| `enrich.py` | Module 3 — Claude (Haiku) PT extraction from ambiguous titles, 2-sentence "why" summary, catalyst classification; USD-only write-back triggers rescore |
| `digest.py` | Module 4 — bucketed Markdown digest (`digests/digest_YYYY-MM-DD.md`); optional LLM narrative from computed numbers only |
| `notify.py` | Emails the digest via Gmail SMTP (App Password) |
| `run_daily.py` | Orchestrator with fail-fast config/secret guards; `--ingest-only` for light runs, `--email` for the full run |
| `cleanup_db.py` / `migrate_v1_1.py` | One-off repair/migration scripts (idempotent, safe to keep) |
| `.github/workflows/pipeline.yml` | GitHub Actions schedule + DB persistence |

## Scoring (v1.1)

```
score = base_action x firm_weight x (1 + magnitude_bonus + upside_bonus) + cluster_bonus
```

- **base_action**: upgrade/downgrade 10 · initiate_buy/sell 6 · pt_change_only 3 · reiterate 0
- **firm_weight**: Tier 1 x1.5 (GS, MS, JPM, BofA, Citi, WF, UBS, Barclays) · Tier 2 x1.2 · other x1.0
- **magnitude_bonus**: |dPT|/old_PT, floor 5%, cap 30%
- **upside_bonus**: |PT-price|/price, floor 5%, cap 35%, stale-analyst guard above 50%
- **cluster_bonus**: same-direction Tier 1/2 peers on the ticker, trailing 5 days — only *independent* peers count (different `news_url`, non-roundup): 1 peer +4, 2+ peers +8

Digest buckets: **20+** act/investigate · **8–20** notable · **3–8** log · **<3** stored but not shown.

Scores are keyed by `(event_id, config_version)` — retuning never destroys history.
Per-event component breakdowns are stored as JSON for future backtests.
**Regime note:** v1.1 (regex PTs at ingest, roundup-aware clustering) deployed
Sep 2026; scores before/after are not directly comparable.

## Data source notes (hard-won)

- FMP **stable** API only (`/stable/grades-latest-news`, `/stable/grades`);
  v3/v4 endpoints return 403 for post-Aug-2025 keys.
- Our tier: `limit` caps at 10 and only `page=0` is allowed — coverage comes from
  pulling every 30 min through the morning and letting hash-dedupe stitch the day.
- Numeric PTs live only in headline text; a regex catches the common
  "to $X from $Y" grammar at ingest, Haiku handles ambiguous cases at enrichment.
  Only **USD** targets are written back (feed carries EUR/DKK/GBp for ADRs).
- Foreign listings filtered at ingest (5-letter tickers ending Y/F, non-USD
  currency tokens in headline).
- Roundup articles ("top calls", shared `news_url` across 3+ same-day events) are
  flagged: still scored, but never counted as independent cluster confirmation.
- TheFly article links are JS-paywalled; the classifier returns `insufficient_data`
  rather than inventing reasoning. Real summaries need a source with article access
  (future: Benzinga via Polygon, paid).
- Dedupe key = hash(ticker + firm + day) — event *identity*, deliberately excluding
  derived fields like action so renormalization can't create duplicates.

## Setup

Python 3.10+, stdlib only (no pip installs).

```bash
cp .env.example .env
```

`.env` (never committed):
```
FMP_API_KEY=...
ANTHROPIC_API_KEY=sk-ant-...
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=...        # Google Account -> Security -> 2-Step Verification -> App passwords
```

## Usage

```bash
python run_daily.py                   # full pipeline
python run_daily.py --ingest-only    # light: ingest + score only
python run_daily.py --email          # full + email the digest
python ingest.py                     # single feed pull (latest 10)
python ingest.py --symbol NOW        # per-symbol history
python score.py --show               # ranked view of scored events
python score.py --rescore            # replay all scores under current CONFIG_VERSION
python enrich.py --dry-run           # list enrichment candidates, no API calls
python digest.py --date 2026-09-11   # render a specific day
python notify.py --date 2026-09-11   # email a specific day's digest
```

## Automation (GitHub Actions)

Private repo. `ratings.db` and `digests/` are **committed back** by the workflow —
that's the persistence layer (SQLite at ~100 events/day stays tiny for years, and
git history doubles as backup). Secrets live in repo Settings -> Secrets and
variables -> Actions: `FMP_API_KEY`, `ANTHROPIC_API_KEY`, `GMAIL_ADDRESS`,
`GMAIL_APP_PASSWORD`.

Schedule (UTC; set for EDT — shift one hour in November):
- Every 30 min, 10:00–15:00 UTC weekdays -> `--ingest-only` (US analyst notes drop ~6–9 AM ET)
- 15:30 UTC weekdays -> full run: enrich + digest + **email**

Cron runs are best-effort (can be 5–20 min late or occasionally dropped);
the 30-min cadence absorbs that. A `concurrency` group prevents two runs from
writing the DB at once. Manual runs via the Actions tab always take the full path.

## Roadmap

- [ ] Ticker-grouped digest ("Citi and UBS both downgraded PYPL" as one story)
- [ ] `backtest.py` — forward returns at +1/+5/+20 days by bucket/tier/action/cluster
      (needs a few hundred events first; the schema is ready — it's a join)
- [ ] Consensus context (`grades-consensus` endpoint, tier permitting):
      "Street: 48 Buy / 6 Hold — first Tier 1 downgrade"
- [ ] v2 filters: contrarian-vs-chase flag, earnings-adjacency penalty
- [ ] MCP server exposing the DB to Claude Desktop (`query_events`,
      `get_top_scored`, `get_analyst_history`)
- [ ] Analyst-level hit rates once names are available (Benzinga upgrade path)

## Caveats

Analyst targets are often lagging; if the stock already gapped, most edge is gone by
the time a daily job sees it. Watch survivorship/hindsight bias in backtests.
Paper-trade long before any automation touches real money.