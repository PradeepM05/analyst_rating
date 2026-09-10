    # Analyst Ratings Daily Pipeline

Pipeline: **ingest → score → enrich → digest** (score before enrich; enrichment only touches notable events).

## Setup

No dependencies — stdlib only so far. Python 3.10+.

```bash
cp .env.example .env   # then put your FMP key in .env (already done in this copy)
```

`.env` and `ratings.db` are gitignored. Never commit the key; rotate it on the FMP dashboard if it ever leaks.

## Usage

```bash
python ingest.py                    # market-wide upgrades/downgrades feed, page 0
python ingest.py --pages 3          # more pages
python ingest.py --symbol NOW       # per-symbol endpoint
python ingest.py --from-file x.json # offline/testing from a saved payload
```

Re-runs are safe: dedupe via hash(ticker+firm+date+action).

## Files

- `config.py` — all tunable constants (tiers, scoring weights, thresholds), `.env` loading, `CONFIG_VERSION`
- `db.py` — SQLite schema (`events`, `scores`, `enrichments`, `pipeline_runs`) + helpers
- `ingest.py` — module 1 (done)
- `score.py` — module 2 (next)
- `enrich.py`, `digest.py`, `run_daily.py` — later

## Notes

- Numeric price targets are usually only in `newsTitle`; `new_pt`/`old_pt` stay NULL until enrichment extracts them (LLM, module 3).
- A "maintains + raises PT" record is normalized to `pt_change_only`, not `reiterate`, when the title mentions a target.
- Deploy path (free): GitHub Actions cron running `run_daily.py`, committing `ratings.db` back to the repo or storing it as an artifact; key goes in repo Secrets, not in code.
