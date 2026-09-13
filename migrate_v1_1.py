"""One-off migration to v1.1: back-apply regex PT extraction and roundup flags
to existing events, then rescore everything under the new CONFIG_VERSION.
Old v1.0 scores are preserved (versioned PK). Safe to re-run.

    python migrate_v1_1.py
"""
import sqlite3

import config
import db
import ingest
import score


def main():
    db.init_db()   # adds is_roundup column if missing
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row

    pts, roundups = 0, 0
    for r in conn.execute("SELECT id, news_title, new_pt FROM events").fetchall():
        new_pt, old_pt = ingest.extract_pt(r["news_title"] or "")
        if new_pt and not r["new_pt"]:     # never overwrite enrichment-written PTs
            conn.execute("UPDATE events SET new_pt = ?, old_pt = ? WHERE id = ?",
                         (new_pt, old_pt, r["id"]))
            pts += 1
        if ingest.is_roundup_title(r["news_title"]):
            conn.execute("UPDATE events SET is_roundup = 1 WHERE id = ?", (r["id"],))
            roundups += 1

    shared = conn.execute(
        """UPDATE events SET is_roundup = 1 WHERE is_roundup = 0 AND news_url IN (
             SELECT news_url FROM events
             WHERE news_url IS NOT NULL AND news_url != ''
             GROUP BY news_url, substr(published_at, 1, 10)
             HAVING COUNT(*) >= ?)""",
        (config.ROUNDUP_SHARED_URL_MIN,)).rowcount

    conn.commit()
    conn.close()
    print(f"backfilled PTs on {pts} events; roundup flags: {roundups} by title, {shared} by shared URL")
    print("rescore under", config.CONFIG_VERSION, ":", score.run(rescore=True))


if __name__ == "__main__":
    main()
