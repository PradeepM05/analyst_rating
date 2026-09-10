"""Central config. All tunable constants live here (retuning = one-line change).

Secrets come from environment / .env — never hardcode keys in this file.
"""
import os
from pathlib import Path

# --- load .env if present (no dependency needed) ---
def _load_dotenv(path: Path = Path(__file__).parent / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))

_load_dotenv()

# --- secrets / endpoints ---
FMP_API_KEY = os.environ.get("FMP_API_KEY", "")
FMP_BASE = "https://financialmodelingprep.com/stable"
# Market-wide feed (preferred): pull everything daily, filter locally.
# Includes newsTitle/newsURL/priceWhenPosted like the old RSS feed did.
FMP_RSS_ENDPOINT = f"{FMP_BASE}/grades-latest-news"
# Per-symbol fallback: slimmer shape (no news fields on our plan tier),
# normalize_fmp already falls back to rec["date"] when publishedDate is absent.
FMP_SYMBOL_ENDPOINT = f"{FMP_BASE}/grades"

# --- storage ---
DB_PATH = Path(os.environ.get("RATINGS_DB", Path(__file__).parent / "ratings.db"))

# --- config versioning (bump when you retune; old scores keep their version) ---
CONFIG_VERSION = "v1.0"

# --- firm tiers ---
TIER1 = {
    "goldman sachs", "morgan stanley", "jpmorgan", "jp morgan",
    "bank of america", "bofa securities", "citigroup", "citi",
    "wells fargo", "ubs", "barclays",
}
TIER2 = {
    "jefferies", "bernstein", "evercore isi", "piper sandler",
    "raymond james", "wedbush", "keybanc", "stifel", "oppenheimer",
    "rbc capital", "rbc capital markets",
}

FIRM_WEIGHT = {"tier1": 1.5, "tier2": 1.2, "other": 1.0}

def firm_tier(firm: str) -> str:
    f = (firm or "").strip().lower()
    if f in TIER1 or any(f.startswith(t) for t in TIER1):
        return "tier1"
    if f in TIER2 or any(f.startswith(t) for t in TIER2):
        return "tier2"
    return "other"

# --- universe filter ---
US_ONLY = True
FOREIGN_CURRENCY_TOKENS = (" EUR ", " GBP ", " GBp ", " DKK ", " SEK ", " NOK ",
                           " CHF ", " JPY ", " HKD ", " AUD ", " CAD ", " PLN ", " TRY ")

# --- scoring constants (v1, reasoned priors — NOT fitted) ---
BASE_ACTION = {
    "upgrade": 10, "downgrade": 10,
    "initiate_buy": 6, "initiate_sell": 6,
    "pt_change_only": 3, "reiterate": 0,
}
MAGNITUDE_NOISE_FLOOR = 0.05
MAGNITUDE_CAP = 0.30
UPSIDE_NOISE_FLOOR = 0.05
UPSIDE_STALE_THRESHOLD = 0.50
UPSIDE_STALE_BONUS = 0.10
UPSIDE_CAP = 0.35
CLUSTER_WINDOW_DAYS = 5
CLUSTER_BONUS = {0: 0, 1: 4, 2: 8}  # 2 = "2 or more" peers

# --- digest buckets ---
BUCKET_ACT = 20.0
BUCKET_NOTABLE = 8.0
BUCKET_LOG = 3.0
ENRICH_THRESHOLD = 8.0
