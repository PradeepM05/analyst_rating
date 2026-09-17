"""Migrate to v1.2: rescore all events under the strategy-aligned weights
(20-40% implied-upside band favored, pt_change_only raised to 6) and create the
earnings table. v1.0/v1.1 scores are preserved — versioned PK.

    python migrate_v1_2.py
"""
import config
import db
import score

try:
    import earnings
except ImportError:
    earnings = None


def main():
    db.init_db()
    if earnings:
        with db.connect() as conn:
            earnings.ensure_table(conn)
        print("earnings table ready")
    print("rescore under", config.CONFIG_VERSION, ":", score.run(rescore=True))
    print("\nNext: python earnings.py   (sync the calendar, 1 API call)")
    print("      python digest.py      (see the new Candidates section)")


if __name__ == "__main__":
    main()