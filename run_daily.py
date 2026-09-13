"""Orchestrator: ingest -> score -> enrich -> digest.

Each stage is failure-isolated: a crash logs the error and the pipeline continues
where that makes sense (enrich dying must not stop the digest — it ships unenriched).
Exit code is non-zero if any stage failed, so cron/Actions can alert.

Our FMP tier only allows page=0 (latest 10 events), so daily coverage comes from
scheduling this to run repeatedly through the morning (e.g. every 30 min, 6-11 AM ET)
and letting hash-dedupe stitch the day together.

  python run_daily.py
  python run_daily.py --skip-enrich       # cheap mode, no LLM calls
  python run_daily.py --narrative         # LLM-written digest intro
"""
import argparse
import sys
import traceback


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-enrich", action="store_true")
    ap.add_argument("--narrative", action="store_true")
    ap.add_argument("--ingest-only", action="store_true",
                    help="Light run: ingest + score only (for frequent morning pulls)")
    ap.add_argument("--email", action="store_true",
                    help="Email the digest after rendering (needs GMAIL_* in env)")
    args = ap.parse_args()

    import db
    import config  
    db.init_db()
    failures = []

    for attr in ("FMP_API_KEY", "FMP_RSS_ENDPOINT", "CONFIG_VERSION"):
        if not hasattr(config, attr):
            print(f"FATAL: config.py missing {attr} — file is damaged")
            sys.exit(1)
    import os
    missing = [k for k in ("FMP_API_KEY",) if not os.environ.get(k)]
    if args.email:
        missing += [k for k in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD") if not os.environ.get(k)]
    if missing:
        print(f"FATAL: missing env/secrets: {missing}")
        sys.exit(1)

    # 1. ingest — a failure here still lets us score/digest what's already stored
    try:
        import ingest
        records = ingest.fetch_rss_feed(page=0)
        print(f"[1/4] ingest: {ingest.ingest_records(records)}")
    except Exception:
        failures.append("ingest")
        traceback.print_exc()

    # 2. score — pure local, should never fail; if it does, stop (digest needs scores)
    try:
        import score
        print(f"[2/4] score: {score.run()}")
    except Exception:
        failures.append("score")
        traceback.print_exc()
        sys.exit(1)

    if args.ingest_only:
        print("[3/4] enrich + [4/4] digest: skipped (ingest-only run)")
        sys.exit(1 if failures else 0)

    # 3. enrich — optional garnish; digest ships without it
    if not args.skip_enrich:
        try:
            import enrich
            print(f"[3/4] enrich: {enrich.run()}")
        except Exception:
            failures.append("enrich")
            traceback.print_exc()
    else:
        print("[3/4] enrich: skipped")

    # 4. digest
    try:
        import digest
        digest.run(narrative=args.narrative)
        print("[4/4] digest: done")
        if args.email:
            import notify
            notify.send_digest()
    except Exception:
        failures.append("digest")
        traceback.print_exc()

    if failures:
        print(f"\nFAILED stages: {failures}")
        sys.exit(1)


if __name__ == "__main__":
    main()