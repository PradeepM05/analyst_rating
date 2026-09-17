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
#FMP_BASE = "https://financialmodelingprep.com/api/v4"
FMP_BASE = "https://financialmodelingprep.com/stable"
# Market-wide feed (preferred): pull everything daily, filter locally.
FMP_RSS_ENDPOINT = f"{FMP_BASE}/grades-latest-news"
# Per-symbol fallback:
FMP_SYMBOL_ENDPOINT = f"{FMP_BASE}/grades"

# --- storage ---
DB_PATH = Path(os.environ.get("RATINGS_DB", Path(__file__).parent / "ratings.db"))

# --- config versioning (bump when you retune; old scores keep their version) ---
CONFIG_VERSION = "v1.2"

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

# --- roundup detection (v1.1) ---
ROUNDUP_TITLE_PATTERNS = (
    "top calls", "top analyst", "what you missed", "fly by", "buy/sell:",
    "morning movers", "wall street's top", "analyst roundup", "street calls",
)
ROUNDUP_SHARED_URL_MIN = 3   # 3+ events sharing one news_url same day = roundup

# --- v1.2: strategy-aligned scoring -------------------------------------
# Target setup: PT raised well above current price, corroborated, short hold
# for a 5-10% move. The 20-40% implied-upside band is the sweet spot; below
# that there is little room to run, far above it usually means a broken stock
# or a stale target rather than a tradeable gap.
UPSIDE_SWEET_LOW = 0.20      # start of the band you care about
UPSIDE_SWEET_HIGH = 0.40     # end of the band
UPSIDE_SWEET_BONUS = 0.60    # bonus inside the band (was capped at 0.35)
UPSIDE_FAR_BONUS = 0.15      # above the band: probably beaten-down/stale
UPSIDE_NEAR_SCALE = 1.0      # 5-20%: linear, unchanged in spirit

# PT-only moves matter more under this strategy than a bare rating word.
BASE_ACTION_V12 = {"upgrade": 10, "downgrade": 10, "initiate_buy": 6,
                   "initiate_sell": 6, "pt_change_only": 6, "reiterate": 0}

# --- candidate filter (digest "Candidates" section) ---
CANDIDATE_MIN_UPSIDE = 0.20        # implied upside vs price at post
CANDIDATE_REQUIRE_PT_RAISE = True  # new_pt must exceed old_pt when both known
CANDIDATE_MIN_SCORE = 5.0          # ignore noise
CANDIDATE_ALLOW_UNCORROBORATED = True   # show, but mark corroboration level

# --- earnings proximity (v1.2) ---
FMP_EARNINGS_ENDPOINT = f"{FMP_BASE}/earnings-calendar"
EARNINGS_RECENT_DAYS = 5     # "reported N days ago" window
EARNINGS_UPCOMING_DAYS = 10  # "reports in N days" window