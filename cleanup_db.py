"""One-off repair: renormalize actions from raw_json, recompute identity hashes,
delete duplicate rows (keeping the OLDEST id per hash), clean orphans, rescore all.

Run once after updating db.event_hash and ingest.normalize_action:
    python cleanup_db.py
Safe to re-run (idempotent).
"""
import json
import sqlite3

import config
import db
import ingest
import score


def main():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row

    # 0. neutralize hashes so recompute can't transiently collide with UNIQUE index
    conn.execute("UPDATE events SET event_hash = 'tmp_' || id")

    # 1. renormalize actions + recompute identity hashes from raw payloads
    renorm = 0
    rows = conn.execute(
        "SELECT id, ticker, firm, published_at, action, raw_json FROM events"
    ).fetchall()
    for r in rows:
        rec = json.loads(r["raw_json"])
        new_action = ingest.normalize_action(rec)
        if new_action != r["action"]:
            renorm += 1
        conn.execute("UPDATE events SET action = ? WHERE id = ?", (new_action, r["id"]))
    print(f"renormalized {renorm} actions")

    # 2. dedupe on identity (ticker+firm+day): keep lowest id per identity
    seen, drop = {}, []
    for r in rows:
        ident = db.event_hash(r["ticker"], r["firm"], r["published_at"])
        if ident in seen:
            drop.append(r["id"] if r["id"] > seen[ident] else seen[ident])
            seen[ident] = min(seen[ident], r["id"])
        else:
            seen[ident] = r["id"]
    for i in drop:
        conn.execute("DELETE FROM scores WHERE event_id = ?", (i,))
        conn.execute("DELETE FROM enrichments WHERE event_id = ?", (i,))
        conn.execute("DELETE FROM events WHERE id = ?", (i,))
    print(f"deleted {len(drop)} duplicate events (+ their scores/enrichments)")

    # 3. write final identity hashes (now guaranteed unique)
    for ident, keep_id in seen.items():
        conn.execute("UPDATE events SET event_hash = ? WHERE id = ?", (ident, keep_id))

    conn.commit()
    conn.close()

    # 4. rescore everything under current config
    print("rescore:", score.run(rescore=True))


if __name__ == "__main__":
    main()
