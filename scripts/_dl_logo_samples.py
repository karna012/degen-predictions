import json
from pathlib import Path

import pandas as pd
import requests

s = requests.Session()
s.headers["User-Agent"] = "x"
out = Path(r"data/investment_scoring/_logo_samples")
out.mkdir(exist_ok=True)
df = pd.read_csv(r"data/investment_scoring/ranked_assets.csv")
u = df[(df.horizon_months == 6) & (df.asset_class != "crypto")][["ticker", "logo_url"]].drop_duplicates("ticker")
cmap = {str(r.ticker): r.logo_url for r in u.itertuples()}
cache = json.load(open("webapp/asset_logo_cache.json", encoding="utf-8"))
want = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "JPM", "XOM",
    "ETERNAL.NS", "RELIANCE.NS", "INFY.NS", "HDFCBANK.NS", "WIPRO.NS",
    "INDIGO.NS", "SBIN.NS", "ITC.NS", "TRENT.NS", "JIOFIN.NS",
    "bitcoin", "ethereum", "solana", "ripple", "dogecoin", "binancecoin",
]
for t in want:
    url = cmap.get(t) or cache.get(t) or cache.get(f"stock:{t}")
    if not url or (isinstance(url, float)):
        print("no url", t)
        continue
    r = s.get(url, timeout=15)
    safe = t.replace("&", "_").replace(".", "_")
    ext = ".jpg" if "jpeg" in (r.headers.get("content-type") or "") else ".png"
    (out / f"sample_{safe}{ext}").write_bytes(r.content)
    print("ok", t, r.status_code, len(r.content))
