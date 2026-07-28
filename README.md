# Degen Predictions

A multi-league football (soccer) match prediction web app. Machine-learning models trained on 5 seasons of historical data generate pre-match probabilities (result, BTTS, total goals, corners, etc.) for **Premier League, La Liga, Serie A, Bundesliga, and Ligue 1**. The site is fully open — no accounts, no login, no payment system of any kind.

> Educational/research project. Not betting advice. Does not promote or facilitate real-money gambling (see `/terms` and `/privacy` in the running app).

---

## 1. High-level architecture

```mermaid
flowchart LR
    subgraph Data Sources
        FD[football-data.co.uk<br/>historical match CSVs]
        US[understat.com<br/>player stats]
        FX[fixturedownload.com<br/>upcoming fixtures feed]
    end

    subgraph Offline pipeline -- scripts/
        DL[download_data.py] --> BF[build_features.py]
        BF --> TR[train_models.py]
        TR --> MD[(model/*.pkl<br/>RF + XGBoost + CatBoost)]
        MD --> PR[predict_pl.py]
        FX --> PR
        PR --> OUT[(output_tables/*.csv<br/>*_ml_predictions.csv)]
        MD --> BT[backtest_pl.py]
        BT --> OUT
    end

    FD --> DL
    US --> DL

    subgraph Web app -- webapp/
        OUT --> APP[Flask app.py]
        LP[live_predict.py] <--> APP
        APP --> JINJA[Jinja templates]
        JINJA --> BROWSER[Browser]
        SCHEDULER[Scheduled task/cron<br/>daily, 6 AM] --> DL
        SCHEDULER --> PR
    end

    BROWSER <-->|HTML + fetch/JSON| APP
```

**Offline pipeline** (run manually or via the scheduled task) downloads data, engineers features, trains models, and writes flat prediction/backtest CSVs to `output_tables/`. **The Flask app never trains anything live** — it just reads those CSVs and, when a user clicks "Get Predictions" on a specific fixture, re-runs inference for that one match through `live_predict.py` (using the same trained model files) so results reflect the latest team form.

---

## 2. Request / page (DOM) flow

Every page extends `webapp/templates/base.html`, which owns:
- `<head>`: fonts, `style.css`, favicon
- Header/nav (Home / Leagues — no auth, nothing gated)
- A `#react-bg-root` div — mounts a small React 18 component (`static/space-bg.js`, loaded via UMD scripts, no build step) that renders the animated space/canvas background (stars, planets, Milky Way, occasional lightning) behind everything via `position: fixed` canvas at `z-index: 0`
- A `#toast-stack` div for JS-driven toast notifications (`showToast()`)
- `{% block content %}` — page-specific content
- Footer with legal links (`/terms`, `/privacy`)

```mermaid
flowchart TD
    A[GET /] --> D[leagues.html<br/>league picker cards]
    D -->|click a league card| E["/{league_key}/"]
    E --> F[index.html<br/>fixture-grid of fixture-card elements]
    F -->|click fixture link| G["/{league_key}/match/{home}/{away}"]
    G --> H[match.html<br/>full breakdown + prediction-insight cards]
    F -->|click "Get Predictions"| I[fetch /{league_key}/api/predict/home/away]
    I --> J[live_predict.py runs inference<br/>for that one fixture]
    J --> K[JSON result fills in the<br/>fixture-card's hidden outcome rows]
```

Client-side JS (inline in each template, no bundler) handles:
- Instant fixture filter/sort (`applyFilterSort()` in `index.html`) — no page reload
- Custom rounded dropdowns (`initCustomSelect()`) replacing native `<select>` where the native dropdown panel couldn't be themed
- Live per-fixture kickoff countdowns
- Toast notifications

---

## 3. Tech stack

| Layer | Choice |
|---|---|
| ML models | scikit-learn RandomForest + XGBoost + CatBoost, stacked ensembles (LightGBM removed — blocked by a Windows Application Control policy on this machine) |
| Web framework | Flask (no auth, no accounts, no database) |
| Frontend | Server-rendered Jinja2 templates, vanilla JS, one small React 18 component (UMD, no build step) for the animated background |
| Styling | Hand-written CSS (`webapp/static/style.css`), single dark theme, CSS custom properties |
| Testing | pytest (`webapp/tests/`) — unit, functional/route, regression, and performance tests |
| Scheduling | Windows Task Scheduler locally (`DegenPredictions Daily Refresh`, daily 6 AM); cron on the AWS deployment (see `deploy/DEPLOY.md`) |

---

## 4. Directory structure

```
Degen_predictions/
├── scripts/                     # Offline ML pipeline (run from repo root)
│   ├── leagues.py                # Central config: one entry per league (fd_code, paths, name maps, fixture slug)
│   ├── download_data.py          # Pulls football-data.co.uk CSVs + understat player stats
│   ├── build_features.py         # Engineers features (form, Elo, H2H, squad quality, referee stats...)
│   ├── train_models.py           # Trains + stacks RF/XGBoost/CatBoost per league, saves to model/<league>/
│   ├── predict_pl.py             # Predicts all upcoming fixtures per league -> output_tables/*_ml_predictions.csv
│   ├── backtest_pl.py            # Backtests trained models against held-out historical matches
│   ├── pl_market_predictions.py  # Extra market-odds-style predictions (Premier League only)
│   └── daily_predict.bat         # Scheduled-task entry point; re-runs predict_pl.py for all leagues
├── data/<league>_5yr/            # Downloaded raw CSVs + engineered feature tables, per league
├── model/<league>/                # Trained model pickles + feature/label metadata, per league
├── output_tables/                 # *_ml_predictions.csv and *_backtest.csv the webapp reads directly
├── webapp/
│   ├── app.py                    # All Flask routes (see table below)
│   ├── live_predict.py           # Per-fixture live inference, caches model state per league
│   ├── league_display.py         # UI-only metadata per league (monogram, accent color, country) -- kept separate from scripts/leagues.py's training config
│   ├── fotmob_lineup.py          # Optional live-lineup lookup (disabled by default, fixtures are usually too far out)
│   ├── templates/                # Jinja2 templates (see section 2)
│   ├── tests/                    # pytest suite -- unit/functional/regression/performance
│   └── static/
│       ├── style.css              # All styling
│       ├── space-bg.js            # React background component
│       └── logo.png               # Site logo (chroma-keyed at runtime, see base.html)
├── deploy/                        # AWS deployment runbook + configs (see section 11)
└── requirements.txt
```

---

## 5. Web app routes

| Route | Methods | Purpose |
|---|---|---|
| `/` | GET | League picker (landing page) |
| `/<league_key>/` | GET | Fixture list for a league (round/team filter, client-side sort) |
| `/<league_key>/match/<home>/<away>` | GET | Full match breakdown + prediction insights |
| `/<league_key>/api/predict/<home>/<away>` | GET | Runs live inference for one fixture, returns JSON |
| `/<league_key>/backtest` | GET | Historical backtest results table |
| `/<league_key>/about` | GET | Methodology page |
| `/terms`, `/privacy` | GET | Legal pages |

No route requires authentication — the entire site is open.

---

## 6. Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `FLASK_SECRET_KEY` | `dev-only-insecure-key` | Used to sign flash-message session cookies. Set to a real random secret in production (see `deploy/production.env.example`). |

That's the only environment variable the app reads — there's no database, no accounts, and no other configurable secrets.

---

## 7. Running it locally

```sh
# 1. Clone and enter the repo
git clone <this-repo>
cd Degen_predictions

# 2. Create + activate a virtualenv
python -m venv football_preds
football_preds/Scripts/activate        # Windows
# source football_preds/bin/activate   # Linux/macOS

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the offline pipeline once per league you want data for
python scripts/download_data.py premier_league la_liga serie_a bundesliga ligue_1
python scripts/build_features.py
python scripts/train_models.py
python scripts/predict_pl.py
python scripts/backtest_pl.py

# 5. Start the web app
cd webapp
python app.py    # http://127.0.0.1:5000, debug mode on
```

Re-running just `python scripts/predict_pl.py [league_key ...]` (no args = all leagues) refreshes the upcoming-fixture predictions the webapp reads on every request — no restart needed.

---

## 8. Keeping data fresh (automated)

A Windows Scheduled Task, **"DegenPredictions Daily Refresh"**, runs `scripts/daily_predict.bat` every day at 6:00 AM. It re-runs `predict_pl.py` for all 5 leagues and appends output to `daily_predict.log`. Check/edit it with:

```powershell
schtasks /Query /TN "DegenPredictions Daily Refresh" /V /FO LIST
schtasks /Change /TN "DegenPredictions Daily Refresh" /ST 07:00   # example: change run time
```

Team-name mapping between `fixturedownload.com`'s upcoming-fixture feed and `football-data.co.uk`'s historical short names lives in each league's `fixture_name_map` in `scripts/leagues.py` — if a newly promoted team shows "Low confidence" indefinitely, check whether its fixture-feed name has an entry there.

---

## 9. Before a real deployment (checklist)

This app currently runs via Flask's built-in dev server (`app.run(debug=True)`) — fine for local use, **not** for anything internet-facing. Before exposing it beyond your own machine:

- [ ] Set a real `FLASK_SECRET_KEY` (long random string, kept out of source control)
- [ ] Turn off `debug=True` and serve via a production WSGI server (gunicorn on Linux) behind a reverse proxy (nginx) with HTTPS
- [ ] Review `/terms` and `/privacy` content for your actual jurisdiction/use case before treating this as more than a private demo
- [ ] Confirm league logos/branding you use elsewhere don't infringe trademarks (this app deliberately avoids hotlinking official league logos — see `webapp/league_display.py`)

The site has no accounts, credits, or payment system of any kind — it's fully open, no login required, and there is no per-user database to back up.

## 10. Deploying to AWS

A full step-by-step runbook (EC2 + nginx + gunicorn + Route 53 domain + Let's Encrypt HTTPS + a systemd service + a cron job for the daily prediction refresh) lives in **[`deploy/DEPLOY.md`](deploy/DEPLOY.md)**, along with the ready-to-use config files it references (`deploy/nginx.conf`, `deploy/degen-predictions.service`, `deploy/setup_ec2.sh`).

---

## 11. Credits / data sources

- Match history: [football-data.co.uk](https://www.football-data.co.uk)
- Player stats: [understat.com](https://understat.com)
- Upcoming fixtures: [fixturedownload.com](https://fixturedownload.com)
- Background art (sun/moon/planets): NASA public-domain photos via Wikimedia Commons (see `webapp/static/space-bg.js` for individual file credits)

This project originated from a single-league (Premier League) prediction script and was generalized to a 5-league Flask web app with live per-fixture inference.
