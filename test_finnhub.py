import config, os, json, urllib.request, urllib.error

k = os.environ.get("FINNHUB_API_KEY", "")
print("key loaded:", bool(k), f"({len(k)} chars)")
url = f"https://finnhub.io/api/v1/stock/upgrade-downgrade?symbol=SUNB&token={k}"
try:
    data = json.loads(urllib.request.urlopen(url, timeout=30).read())
    print(json.dumps(data[:5], indent=2))
    print(len(data), "records")
except urllib.error.HTTPError as e:
    print("HTTP", e.code, "-", e.read().decode()[:300])