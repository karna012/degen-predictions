# Data Sources Reference

Every external data feed the platform touches, what it's used for, and
whether it's real/live or a modeling assumption. Written 2026-07-29 during
the Dubai real estate real-price integration; keep this updated when a
data source changes.

## Dubai Real Estate (`/dubai`)

| Source | Used for | Status |
|---|---|---|
| RapidAPI "UAE Real Estate" (`uae-real-estate3.p.rapidapi.com`) | Current sale price/sqft, current rent/sqft, trailing 12-month appreciation -- all 7 areas | **Live, real.** Real DLD-registered transactions surfaced via Bayut, not asking prices or synthetic data. Refreshed daily. |
| Hand-set infrastructure model (`webapp/dubai_predict.py` `DUBAI_AREAS`) | Metro proximity, development phase, infrastructure factor -- forward-looking multiplier applied on top of the real appreciation rate | **Modeling assumption**, not live data. Represents catalysts (a metro extension opening, a master plan completing) that a trailing price trend can't capture yet. Documented as such in code. |
| `api.bayut.com` (in `scripts/collect_bayut_dubai_data.py`, `scripts/dubai_data_ingestion.py`, `scripts/collect_dubai_historical_data.py`) | Originally attempted direct Bayut scraping | **Dead/unused.** These calls don't work (no auth, likely blocked) and aren't wired into the live site at all. |
| `scripts/generate_bayut_synthetic_data.py` + `data/dubai/bayut_*.csv` | Was meant to fall back to synthetic training data when the above failed | **Fake data, flagged but not removed.** Never actually reached the live calculator -- `predict_roi()` never called the joblib models these files were meant to train. Left in the repo as of this writing; a cleanup candidate. |
| Dubai Land Department (`dubailand.gov.ae`) | Considered as an official transaction source | **Never implemented.** No public API without an emailed OAuth key; `collect_dubai_historical_data.py` only documents the manual-download process. |

**Pipeline:** `automation/data_fetchers.py`'s `RealEstateDataFetcher` -> `data/dubai/live_prices.json` (daily, via `deploy/refresh_dubai_prices.sh` + `degen-refresh-dubai-prices.timer`) -> read by `webapp/dubai_predict.py` with a fallback to static defaults if the cache is missing/the API key isn't set. Free tier: 750 req/month, budget ~630/month at current call volume -- don't add areas/pages without rechecking.

## Metals (`/metals`)

| Source | Used for | Status |
|---|---|---|
| TradingView scanner (`scanner.tradingview.com/global/scan`) | Gold, Silver, Platinum, Palladium, Copper, Steel current prices | **Live, real.** Same backend the page's embedded TradingView chart widget reads from, so the calculator's price matches the chart. |
| Yahoo Finance (`query1.finance.yahoo.com`) | Aluminum current price | **Live, real.** Fallback because TradingView's free scanner won't resolve Aluminum's CFD symbol without a paid session. |
| Hand-updated static benchmarks | Nickel, Lithium, Mercury | **Not live.** No free retail-quoted price exists for these (documented in `MetalsDataFetcher`'s docstring) -- periodically hand-updated. |

**Pipeline:** `automation/data_fetchers.py`'s `MetalsDataFetcher` -> `data/metals/current_prices.json`, overlaid onto `webapp/metals_predict.py`'s `METALS_DATA` at runtime.

## Crypto & Stock Rankings (`/crypto`, `/stocks/us`, `/stocks/india`)

| Source | Used for | Status |
|---|---|---|
| CoinGecko (`api.coingecko.com`) | Crypto prices, 24h change, market cap | **Live, real.** |
| TradingView scanner, via `webapp/tv_symbols.py`'s verified ticker map | Embedded live candlestick chart per asset | **Live, real** for the ~50/60 crypto assets and 100% of stocks that have a TradingView listing; assets without one show a "not tracked" notice instead of a fabricated chart. |
| Alpha Vantage (`ALPHA_VANTAGE_KEY` env var, in `StocksDataFetcher`) | Was meant to fetch US/India stock data | **Stub, unused.** `fetch_us_stocks`/`fetch_india_stocks` return empty DataFrames -- never implemented. Whatever currently powers the rankings' base numbers comes from elsewhere (`ranked_assets.csv` / investment scoring pipeline, not this fetcher). |

## Sports

| League | Source | Status |
|---|---|---|
| MLB | ESPN (`site.api.espn.com`) for current season; Retrosheet (`retrosheet.org/gamelogs`) for historical game logs | **Live, real.** Refreshed every 3h via `degen-refresh-data.timer`. |
| NPB | spaia.jp public schedule API | **Live, real.** Same 3h refresh cycle. |
| Cricket | Cricsheet.org (`cricsheet.org/downloads/*.zip`, ball-by-ball JSON + `people.csv`) | **Live, real.** |
| Cricket (The Hundred fixtures) | Wikipedia/Wikimedia (`en.wikipedia.org`, `meta.wikimedia.org`) | **Live, real** -- fixture list scraped from the tournament's Wikipedia page. |
| Cricket weather | Open-Meteo (`api.open-meteo.com`) | **Live, real** -- feeds `webapp/cricket_predict.py`. |
| Soccer (Premier League) | fixturedownload.com (fixtures), understat.com (advanced stats) | **Live, real.** |
| Soccer lineups | FotMob (`fotmob.com`, `webapp/fotmob_lineup.py`) | **Live, real.** |

## Supporting Infrastructure

| Source | Used for |
|---|---|
| Google News (`news.google.com`, `webapp/sentiment.py`) | Headline sentiment feed into predictions |
| ip-api.com (`webapp/geoip.py`) | Visitor geolocation |
| Google OAuth (`accounts.google.com`) | Account sign-in |

## Notes for future updates

- When adding a new external data source, add a row here in the same pass -- this file is the single place to check "is this number real or modeled" without reading every fetcher script.
- Anything under `automation/data_fetchers.py`'s `FETCHERS` dict should have a corresponding row above; if a fetcher is a no-op stub (like `SportsDataFetcher`, `StocksDataFetcher` as of this writing), say so explicitly rather than implying it's live.
- RapidAPI free-tier budgets (Dubai: 750/month) are a hard constraint -- note the budget math here if you change call volume, not just in the fetcher's docstring.
