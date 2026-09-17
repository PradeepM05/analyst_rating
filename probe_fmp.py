"""Probe what the FMP free tier actually allows: per-symbol universe + endpoints.

Costs ~25 calls. Run once; results guide whether a paid tier is worth it.
    python probe_fmp.py
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import config

# Spectrum from mega-cap to small-cap, plus the two we already know.
TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL",        # mega
    "JPM", "UNH", "CAT", "NOW", "UBER",             # large
    "SUNB", "URI", "WSM", "DKS", "TOL",             # large/mid
    "MCFT", "OESX", "ARRY", "FLNC", "DAVE",         # small
]

OTHER_ENDPOINTS = {
    "historical price (backtest!)": "/historical-price-eod/light?symbol=AAPL",
    "price target consensus": "/price-target-consensus?symbol=AAPL",
    "grades consensus": "/grades-consensus?symbol=AAPL",
    "earnings calendar": "/earnings-calendar",
    "company profile": "/profile?symbol=AAPL",
}


def call(path_with_query: str):
    sep = "&" if "?" in path_with_query else "?"
    url = f"{config.FMP_BASE}{path_with_query}{sep}apikey={config.FMP_API_KEY}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.loads(r.read().decode())
        n = len(data) if isinstance(data, list) else 1
        return True, f"OK ({n} records)"
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        msg = body[:110].replace("\n", " ").strip()
        return False, f"HTTP {e.code}: {msg}"
    except Exception as e:
        return False, f"ERR {e}"


def main():
    print("=== per-symbol /grades universe ===")
    allowed, denied = [], []
    for t in TICKERS:
        ok, msg = call(f"/grades?symbol={t}")
        print(f"  {t:6s} {'✓' if ok else '✗'}  {msg}")
        (allowed if ok else denied).append(t)
        time.sleep(0.3)
    print(f"\n  allowed: {len(allowed)}/{len(TICKERS)} -> {', '.join(allowed) or 'none'}")
    print(f"  denied : {', '.join(denied) or 'none'}")

    print("\n=== other endpoints ===")
    for label, path in OTHER_ENDPOINTS.items():
        ok, msg = call(path)
        print(f"  {label:32s} {'✓' if ok else '✗'}  {msg}")
        time.sleep(0.3)

    print("\nVerdict hints:")
    print("  - If most tickers pass: a watchlist sweep is viable on free tier.")
    print("  - If only mega-caps pass: 'US Coverage' on Starter is what you'd be buying.")
    print("  - If historical price passes: backtest.py is buildable now.")


if __name__ == "__main__":
    main()