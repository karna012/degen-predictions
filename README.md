# Degen Predictions

A free ML/AI prediction platform focused on **Sports**, **Crypto**, and **Stocks** (US & India). No paywall — every prediction tool is free, with account/login optional (Google or email/password) and not required to use anything.

> Educational/research project. Not betting or financial advice (see `/terms`, `/privacy`, `/faq` in the running app).

Live at **[degenpredictions.com](https://degenpredictions.com)**.

---

## 1. What's in each category

| Category | Route | What it does |
|---|---|---|
| Sports | `/sports`, `/sports/soccer`, `/sports/baseball`, `/sports/cricket`, plus per-league/per-match routes | Soccer (Premier League, La Liga, Serie A, Bundesliga, Ligue 1), MLB, NPB, and cricket (The Hundred, MLC) match predictions — result, BTTS, total goals, corners, using ELO ratings blended with trained ML ensembles |
| Crypto | `/crypto`, `/crypto/asset/<ticker>` | Cryptocurrencies ranked by AI-predicted 6-month forward return, live CoinGecko prices, embedded TradingView chart per asset |
| Stocks | `/stocks`, `/stocks/<us\|india>`, `/stocks/<market>/asset/<ticker>` | US and India stock rankings by predicted 6-month return |

Legacy calculators for Dubai real estate (`/dubai`) and metals (`/metals`) remain reachable by URL but are no longer part of the primary product surface (nav / homepage / marketing).

Every external data feed the platform depends on — which numbers are real/live vs. which are hand-set modeling assumptions — is catalogued in **[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md)**. Read that before assuming any given number is "real."

Traffic / SEO ops: [`docs/SEARCH_CONSOLE.md`](docs/SEARCH_CONSOLE.md), [`docs/TRAFFIC_30_DAY.md`](docs/TRAFFIC_30_DAY.md).

---

## 2. High-level architecture

```mermaid
flowchart LR
    subgraph External data
        APIs[RapidAPI, CoinGecko, TradingView,<br/>Yahoo Finance, ESPN, Retrosheet,<br/>spaia.jp, Cricsheet, fixturedownload,<br/>understat, FotMob -- see docs/DATA_SOURCES.md]
    end

    subgraph automation/
        FETCH[data_fetchers.py<br/>Dubai + metals caches] --> CACHE[(data/*/*.json<br/>cached snapshots)]
        LIVE[live_match_tracker.py]
    end

    subgraph scripts/ -- offline ML pipeline
        TRAIN[train_*.py per category] --> MODEL[(model/*/*.joblib)]
    end

    APIs --> FETCH
    CACHE --> APP
    MODEL --> APP

    subgraph webapp/
        APP[Flask app.py] --> JINJA[Jinja templates]
        JINJA --> BROWSER[Browser]
    end

    BROWSER <-->|HTML + fetch/JSON| APP
```

Fetchers and training scripts run offline/on a schedule (systemd timers in production, Windows Task Scheduler locally); the Flask app itself never trains anything or blocks a request on a live upstream call — it reads whatever the last fetch/train cycle wrote, with static fallbacks if a fetch ever fails, so a bad API day degrades gracefully instead of crashing a page.

---

## 3. Tech stack

| Layer | Choice |
|---|---|
| ML models | Slim stack: calibrated XGBoost (+ logistic/ridge) with Elo for sports; walk-forward XGBoost/Ridge for crypto/stocks (LSTM only if it beats tabular IC) |
| Web framework | Flask, no required accounts — optional login via email/password (`flask-login`) or Google OAuth (`authlib`), gated by `ACCOUNTS_ENABLED` |
| Accounts storage | DynamoDB (`boto3`), only used when accounts are enabled |
| Frontend | Server-rendered Jinja2 templates, vanilla JS, no build step, no framework (the animated space background used to be a React component — removed in favor of a plain canvas/DOM setup, since it was never re-rendered) |
| Styling | Hand-written CSS (`webapp/static/style.css`), single dark theme, CSS custom properties |
| Testing | pytest (`webapp/tests/`) — unit, functional/route, regression, and performance tests; CI runs the suite on every push/PR (`.github/workflows/deploy.yml`) |
| Scheduling | systemd timers on the EC2 deployment (`deploy/*.timer`, `deploy/systemd/*.timer`); Windows Task Scheduler locally |
| CI/CD | GitHub Actions: test suite on every push/PR, auto-deploy to EC2 on a green push to `master` (see `.github/workflows/deploy.yml`) |

---

## 4. Directory structure

```
Degen_predictions/
├── automation/
│   ├── data_fetchers.py          # Live Dubai + metals price caches
│   └── live_match_tracker.py      # Single-pass live match score poller
├── scripts/
│   ├── leagues.py                 # Soccer league config (fd_code, paths, name maps, fixture slug)
│   ├── mlb/, npb/, cricket/       # Per-sport data download + Elo build scripts
│   └── train_*.py, predict_*.py, backtest_*.py  # Offline ML pipeline, per category
├── data/<category>/               # Raw + engineered data, per category (mostly git-tracked)
├── model/<category>/              # Trained model files, per category
├── webapp/
│   ├── app.py                     # All Flask routes
│   ├── dubai_predict.py, metals_predict.py, cricket_predict.py, ...   # Per-category prediction logic
│   ├── auth.py                    # Optional login/signup (DynamoDB + Google OAuth)
│   ├── templates/                 # Jinja2 templates
│   ├── tests/                     # pytest suite
│   └── static/                    # CSS, JS, images
├── docs/
│   ├── DATA_SOURCES.md            # Every external data feed: what it is, real vs. modeled
│   └── DEPLOY.md, generate_diagrams*.py, build_document.py
├── deploy/                        # Systemd units, sync.sh, EC2 setup, production.env.example
└── requirements.txt
```

---

## 5. Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `FLASK_SECRET_KEY` | `dev-only-insecure-key` | Signs session cookies. Set a real random secret in production. |
| `ACCOUNTS_ENABLED` | `1` | Gates login/signup routes. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | unset | Google OAuth login. |
| `ADMIN_USER` / `ADMIN_PASSWORD` | unset | HTTP Basic Auth for `/admin`, `/results`. Routes 404 if either is unset (fails closed). |
| `AWS_REGION` / `USERS_TABLE_NAME` | `us-east-1` / `degen-predictions-users` | DynamoDB accounts table. |
| `RAPIDAPI_KEY` | unset | Powers the Dubai real estate live-price refresh (`automation.data_fetchers.RealEstateDataFetcher`). Without it, that daily refresh is skipped and the calculator falls back to static estimates. |

See `deploy/production.env.example` for the full production template.

---

## 6. Running it locally

```sh
git clone <this-repo>
cd Degen_predictions
python -m venv venv
venv/Scripts/activate        # Windows
# source venv/bin/activate   # Linux/macOS
pip install -r requirements.txt

cd webapp
python app.py    # http://127.0.0.1:5000, debug mode on
```

Each category's data comes from whatever's already checked into `data/<category>/` and `model/<category>/`. To refresh live data yourself:

```sh
python -c "from automation.data_fetchers import fetch_category; fetch_category('real_estate')"   # Dubai, needs RAPIDAPI_KEY
python -c "from automation.data_fetchers import fetch_category; fetch_category('metals')"        # Metals, no key needed
python -c "from automation.data_fetchers import fetch_category; fetch_category('crypto')"        # Crypto, no key needed
```

Retraining ML models is a per-category offline step under `scripts/` (e.g. `scripts/mlb/`, `scripts/cricket/`, `scripts/train_models.py` for soccer) — not something the running app ever does itself.

---

## 7. Automated refresh (production)

Systemd timers on the EC2 instance:

| Timer | Cadence | Does |
|---|---|---|
| `degen-refresh-data.timer` | Every 3h | MLB/NPB current-season data + Elo rebuild |
| `degen-refresh-dubai-prices.timer` | Daily, 05:00 UTC | Real Dubai transaction prices via RapidAPI (rate-limited to stay under the free-tier monthly cap — see the fetcher's docstring before adding areas/calls) |
| `degen-live-tracker.timer` | Frequent | Live match score polling |
| `degen-reboot.timer` | Daily, 04:00 UTC | Instance reboot |

Install/enable these the same way as the main app service (`sudo cp ... /etc/systemd/system/ && sudo systemctl enable --now ...`) — they're not currently wired into `setup_ec2.sh`, so a from-scratch server rebuild needs them added manually.

---

## 8. Deploying

Two ways code reaches production, both hitting the same EC2 instance:

- **Manual**: `deploy/sync.sh` — tars `webapp/` + `scripts/` (pass extra dirs as args, e.g. `deploy/sync.sh automation deploy`), scp's to the server, extracts, restarts the `degen-predictions` systemd service.
- **Automatic**: `.github/workflows/deploy.yml` — on every push to `master`, runs the pytest suite; if it passes, packs and deploys the same way sync.sh does, then runs a post-deploy smoke test against the live domain. A red test suite never reaches production.

Full first-time EC2 setup (instance, nginx, HTTPS, systemd service) is in **[`deploy/DEPLOY.md`](deploy/DEPLOY.md)**.

---

## 9. Credits / data sources

Full breakdown in **[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md)**. Summary:

- **Dubai real estate**: RapidAPI "UAE Real Estate" — real DLD-registered transactions
- **Metals**: TradingView scanner (live) + Yahoo Finance (Aluminum fallback)
- **Crypto**: CoinGecko
- **MLB**: ESPN (current season), Retrosheet (historical)
- **NPB**: spaia.jp
- **Cricket**: Cricsheet.org, Wikipedia (The Hundred fixtures)
- **Soccer**: fixturedownload.com (fixtures), understat.com (stats), FotMob (lineups)
- Background art credits: `webapp/static/space-bg.js`

This project originated as a single-league (Premier League) prediction script and was rebuilt into the current multi-category platform.
