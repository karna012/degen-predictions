# Degen Predictions — Site Architecture Bible

**Live site:** [https://degenpredictions.com](https://degenpredictions.com)  
**Repo root:** `Degen_predictions/`  
**Audience for this doc:** operators and researchers who need the full pin-to-pin map after a short break.  
**Last verified against code:** 2026-08-11 (repo state at write time).

---

## Table of contents

1. [Executive overview](#1-executive-overview)
2. [High-level architecture](#2-high-level-architecture)
3. [Directory map](#3-directory-map)
4. [Sports & leagues](#4-sports--leagues)
5. [Today's Picks + Best Combos](#5-todays-picks--best-combos)
6. [Prediction pages / fixture detail / markets](#6-prediction-pages--fixture-detail--markets)
7. [Results, accuracy hub, OOS / calibration](#7-results-accuracy-hub-oos--calibration)
8. [Non-sports (stocks / crypto)](#8-non-sports-stocks--crypto)
9. [Daily distribution](#9-daily-distribution)
10. [Admin](#10-admin)
11. [Models](#11-models)
12. [Database / persistence](#12-database--persistence)
13. [Security measures](#13-security-measures)
14. [Automation / timers](#14-automation--timers)
15. [Deploy](#15-deploy)
16. [Feature inventory](#16-feature-inventory)
17. [Key file index](#17-key-file-index)
18. [Known gaps / caveats](#18-known-gaps--caveats)

---

## 1. Executive overview

**Degen Predictions** is a free, educational ML forecast site covering:

- **Sports** — soccer (6 leagues), MLB, NPB, cricket (IPL / BBL / Hundred / CPL / MLC), men’s ATP tennis
- **Crypto** — multi-horizon ranking boards (Beat BTC / ranges when absolute USD direction fails OOS)
- **Stocks** — US (S&P-style universe) and India (Nifty) ranking boards

**Product framing (honest, not marketing):**

- Educational / research project — **not** betting advice, **not** investment advice (`/terms`, `/privacy`, `/faq`, `/about`).
- Full-slate match-winner markets typically land in the mid-50s% OOS; public **Today’s Picks** / **Best Combos** are *selective* high-probability legs (stricter bars), not “every game tip.”
- Soccer **Safer Result** is a separate calibrated favourite-or-draw / exact-win rule, only labeled 70%+ where later-slice OOS evidence clears the bar.
- Crypto/stocks emphasize **relative** leans (peer lean, Beat BTC / beat index). Absolute equity-premium point forecasts are withheld when walk-forward direction fails guardrails.
- No account is required to use the public site. Optional login/signup exists in code but is **off in production** (`ACCOUNTS_ENABLED=0`).

**Primary public habit surfaces:**

| Surface | URL |
|---------|-----|
| Home / category landing | `/` |
| Today’s Picks | `/todays-picks` |
| Best Combos | `/todays-picks/combos` |
| Combo track record | admin only (`/admin?section=combo_results`) |
| Accuracy hub | `/accuracy` |
| Daily digest (admin-gated) | `/todays-picks/digest` |
| Telegram invite hop | `/go/telegram` |

---

## 2. High-level architecture

```
Browser / bots
    │
    ▼
Cloudflare (DNS + HTTPS + edge security)   ← documented in docs/build_document.py, SEARCH_CONSOLE.md
    │
    ▼
nginx (EC2)  — rate limits, static /static/, proxy to gunicorn
    │
    ▼
gunicorn → Flask app (webapp/app.py) on 127.0.0.1:8000
    │
    ├── Reads: data/*, model/*, output_tables/* CSVs, SQLite DBs under webapp/
    ├── Live fixture APIs (ESPN, spaia, Wikipedia, fixturedownload, …) on demand + caches
    └── Never trains models on request; never blocks forever on a bad upstream for sitemap
```

**Major moving parts:**

| Layer | Role |
|-------|------|
| `webapp/` | Flask routes, Jinja templates, sport predict modules, combo settle, daily digest |
| `data/` | Training archives, Elo CSVs, confidence thresholds, investment rankings, digests, feedback JSON |
| `model/` | Soccer stacked models + per-league `confidence_thresholds.json` (paths from `scripts/leagues.py`) |
| `scripts/` | Offline download / feature / train / predict / calibrate / promote / feedback loop |
| `automation/` | Price fetchers (legacy Dubai/metals), live-tracker scaffold, distribute wrapper |
| `deploy/` | systemd units, nginx, sync scripts, `production.env.example`, hardening |
| `docs/` | Ops notes (this bible, DATA_SOURCES, DAILY_DISTRIBUTION, SEO) |

**Production host (from deploy scripts):** EC2 Ubuntu, user `ubuntu`, app dir `~/Degen_predictions`, service `degen-predictions`. Sync scripts hardcode host `ubuntu@52.201.204.110` and key `deploy/degen-predictions-key.pem`.

**Runtime:** Flask served by **gunicorn** (3 workers), env from `deploy/production.env`. Local: `cd webapp && python app.py` (dev).

---

## 3. Directory map

```
Degen_predictions/
├── webapp/                 # Flask app (source of truth for routes + settle)
│   ├── app.py              # Almost all HTTP routes
│   ├── today_picks.py      # TODAY_SPORTS catalog, floors, builders, build_best_combos
│   ├── combo_results.py    # SQLite snapshot + settle + feedback report
│   ├── daily_digest.py     # Top-3 + drafts + Telegram/X/email posts
│   ├── post_event_refresh.py
│   ├── *_predict.py / *_fixtures.py / safer_result.py / tennis_score.py …
│   ├── analytics.py / feedback.py / auth.py / geoip.py
│   ├── templates/ / static/ / tests/
│   ├── combo_results.db    # runtime (not synced from laptop)
│   ├── combo_full.pkl / combo_fast.pkl   # combo page disk cache
│   └── analytics.db / feedback.db / users.db (local / optional)
├── data/
│   ├── pl_5yr|laliga_5yr|… # Soccer raw + features
│   ├── mlb/ npb/ cricket/ tennis/
│   ├── investment_scoring/ # ranked_assets.csv + horizon metrics
│   ├── daily_digest/       # YYYY-MM-DD.json, state.json, cards/
│   ├── feedback/           # live_calibration.json, post_event.json, history
│   ├── live/               # live-tracker output (if wired)
│   └── dubai/ metals/      # legacy price caches (pages 404)
├── model/{pl,laliga,…}/    # Soccer joblib/pkl stacks + confidence_thresholds.json
├── scripts/                # Offline ML + calibrate + EC2 feedback
├── automation/             # fetchers + live_match_tracker (scaffold)
├── deploy/                 # EC2, nginx, timers, sync
├── docs/                   # This file + DATA_SOURCES + distribution
├── output_tables/          # Soccer prediction CSVs, daily_drafts/
└── .cursor/rules/          # Agent wiring rules (Today’s Picks / post-event / all-sports)
```

**Ownership rule of thumb:** if it changes what users see on a page, it is almost always `webapp/`. If it changes model weights or OOS bars, it is `scripts/` + `data/` / `model/`. If it changes how the box stays alive, it is `deploy/`.

---

## 4. Sports & leagues

### 4.1 Catalog that drives Today’s Picks / Combos

Source of truth: `today_picks.TODAY_SPORTS`. **A league missing from this dict never appears in Today’s Picks or Best Combos.**

| Sport key | League id | Kind | TZ | Public board notes |
|-----------|-----------|------|----|--------------------|
| soccer | premier_league, la_liga, serie_a, bundesliga, ligue_1, mls | soccer | London/Madrid/Rome/Berlin/Paris/NY | Result / Safer Result / DNB / handicap / BTTS / goals / corners |
| baseball | mlb | mlb | America/New_York | Moneyline skipped on public picks (coin-flip); props / Upset lean |
| baseball | npb | npb | Asia/Tokyo | Same coin-flip moneyline skip |
| cricket | hundred, mlc, ipl, bbl, cpl | cricket | London / NY / Kolkata / Sydney / Port_of_Spain | Match winner skipped on public picks; totals / team runs / Upset lean |
| tennis | atp | tennis | America/New_York | Match winner + sets/games/handicap markets; surface = filter/feature, not a league |

### 4.2 Per-sport fixtures → predict → results → settle

| Sport | Fixtures | Predictions | Results pages | Combo settle (`combo_results.lookup_score_for_leg`) |
|-------|----------|-------------|---------------|------------------------------------------------------|
| **Soccer** | fixturedownload + prediction CSVs in `output_tables/` / league loaders | Stacked models under `model/<league>/`; live refresh via `live_predict` + FotMob lineups | League `/<key>/` + admin `/results`; public accuracy via hub | `_lookup_soccer` ← football-data **history** (`FTHG`/`FTAG`). **Lags** (often next day). Live scoreboard not wired for soccer settle yet. |
| **MLB** | ESPN scoreboard (`mlb_fixtures`) | Elo + `data/mlb/ml_ensemble_model.joblib` | `/mlb/results` (ESPN + Elo refresh every 3h) | `_lookup_mlb` — prefer completed same-date game in a series |
| **NPB** | spaia `schedules` | Elo + `data/npb/ml_ensemble_model.joblib` | `/npb/fixtures` live overlay | `_lookup_npb` — finals from spaia **`games_info_by_date`** (`gameStateId` 4). Season `schedules` **lags hours** on scores — do not settle from schedules alone |
| **Hundred / CPL** | Wikipedia season templates | Cricket Elo + joblibs under `data/cricket/` | `/hundred/results`, `/cpl/results` merge live finals onto Cricsheet | `_lookup_cricket` from **full** `get_hundred_fixtures()` / `get_cpl_fixtures()` (not upcoming-only). Name rebrands applied (e.g. MI London → Oval Invincibles) |
| **IPL / BBL** | fixturedownload.com | Same cricket models | `/ipl/results`, `/bbl/results` | Cricket lookup + fd completed flags; seasons Mar–May / Dec–Jan |
| **MLC** | No live schedule feed | Cricsheet archive | `/mlc/results` only | Cricsheet; do not invent upcoming fixtures |
| **Tennis ATP** | ESPN `site.web.api` (+ Wikipedia brackets). Skip qualifying on public board; men singles only | Surface Elo + `ml_ensemble_model.joblib` + `ml_markets_model.joblib` | `/tennis/results` | `_lookup_tennis` — prefer **completed** within ±1 day of kickoff; never grade today’s pairing with a week-old rematch. Score via `tennis_score.parse_tennis_score` |

### 4.3 Post-event rule (all sports)

After full time:

1. Prefer **live** score source — never wait for weekly retrain / Cricsheet / football-data dump alone.
2. Match **teams + kickoff date**; prefer a **completed** row over scheduled duplicates.
3. No score / still live → stay **Pending**. Do not invent a result.
4. Grade combo legs + published picks; roll up slips (any pending leg keeps slip pending; void/push ignored in win/loss).
5. EC2 `degen-post-event.timer` (15 min) → `webapp/post_event_refresh.py`.

Agent wiring checklists live in `.cursor/rules/todays-picks-combos-wiring.mdc`, `post-event-results.mdc`, `all-sports-rules.mdc`.

---

## 5. Today's Picks + Best Combos

### 5.1 Routes

| Route | Role |
|-------|------|
| `/todays-picks` | Sport picker |
| `/todays-picks/<sport>` | League picker |
| `/todays-picks/<sport>/<league>` | Up to 6 public tips for that league’s day |
| `/todays-picks/combos` | 2-leg / 3-leg + mixed boards |
| `/todays-picks/combos/results` | Hidden from public (404); admin Basic Auth still works |
| `/todays-picks/digest` | Admin-gated Top-3 digest UI |
| `/todays-picks/top3`, `/todays-picks/digest.json` | Admin JSON |

### 5.2 Probability floors (`today_picks.py`)

| Constant | Default | Role |
|----------|---------|------|
| `HIGH_CONF_MIN` | 0.55 | Soft default when no calibrated bar |
| `HIGH_PROB_MIN` | 0.68 | Hard public floor for Today's Picks |
| `WISE_PROB_MIN` | 0.70 | Stricter floor for soft/uncalibrated markets |
| `effective_high_prob_min()` | ≥ 0.68 | `max(HIGH_PROB_MIN, live_calibration)` — live feedback may **raise only** |
| `MAX_PICKS_PER_LEAGUE` | 6 | Public board cap |
| `MAX_PICKS_FOR_COMBOS` | 18 | Per-league gather into combo pool |
| `COMBO_FALLBACK_MIN` | 0.62 | Preferred combo usable floor |
| `COMBO_SOFT_FILL_MIN` | 0.55 | Thin-slate soft fill |
| `COMBO_PREFER_MIN` | 0.68 | Prefer legs ≥ this in scoring |
| `COMBO_POOL_CAP` | 48 | Diversified pool size |
| `COMBO_BEAM_WIDTH` | 256 | Beam width for multi-leg candidates |
| `MAX_COMBOS_PER_SIZE` / `MIN` | 3 / 2 | Publish quotas per board size |
| `MAX_MIXED_COMBOS_PER_SIZE` | 3 | Mixed board quota |

Calibrated bars: soccer `model/<league>/confidence_thresholds.json`; other sports `data/<sport>/confidence_thresholds.json`. Markets that fail `met_target` are **unavailable** as 70% tips (`market_min_conf` / `sport_market_min_conf` → `None`).

Live raise: `data/feedback/live_calibration.json` → `suggested_min_confidence` (written by `scripts/run_ec2_feedback_loop.py`).

### 5.3 Today's Picks flow

1. Resolve league from `TODAY_SPORTS` (`kind`, `tz`).
2. Load today’s fixtures in league TZ.
3. Score sport markets; emit tips only above the tip bar.
4. Rank by confidence; keep ≤ 6.
5. Label: calibrated bar cleared → “70%+ confidence tier”; else high-confidence / wise.

**Public vs combo gather:** same builders with `min_conf=None` (public/stricter) vs `min_conf=COMBO_SOFT_FILL_MIN` (wider pool).

**Sport-specific tip rules:**

- Soccer: Safer Result only where later-slice eval cleared 70%.
- Baseball / cricket: skip favorite moneyline / match winner on public board; optional **Upset lean** when close (`COINFLIP_FAV_MAX` / `UPSET_LEAN_MIN`).
- Tennis: favorite match winner when strong; optional Upset lean; surface filter only.

### 5.4 Best Combos flow

Implemented in `app._rebuild_combo_bundle` + `today_picks.build_best_combos`:

1. **Gather** — every `TODAY_SPORTS` league via ThreadPoolExecutor (max 4 workers); `annotate_leg` adds `sport_key` / `league_id`.
2. **Usable floors** — try 0.62 then soft-fill 0.55.
3. **Diversify** — `_diversify_leg_pool`: round-robin sport → league → market family. Sport order: soccer, baseball, cricket, tennis.
4. **Candidates** — beam search; legs on one slip must be **distinct fixtures**.
5. **Joint probability** — product of leg confidences (independence assumption; UI may note correlation).
6. **Score** (`_make_combo`) — primarily `log(joint)`, plus mix bonuses (cross-sport > cross-league), multi-market bonus, prefer_min bonus, correlation / same-side O/U penalties.
7. **Publish** — main boards `2` / `3` and mixed `mixed_2` / `mixed_3`. Soft-fill may share fixtures across slips (`is_soft_fill`).

On full live rebuild: `snapshot_published_picks` + `snapshot_published_combos`.

### 5.5 Combo cache

- In-memory page TTL cache + disk: `webapp/combo_full.pkl` / `combo_fast.pkl` (+ lock files).
- Stale cache can hide newly wired sports until rebuild/restart.
- Sync scripts **exclude** these pkls and `combo_results.db` so prod state is not overwritten by laptop empties.

### 5.6 Quick compare

| | Today's Picks | Best Combos |
|--|---|----|
| Unit | 1 tip | 2–3 tips |
| Scope | One league | All `TODAY_SPORTS` |
| Cap | 6 / league | 18 / league → 48 pool; ~3+3 main + mixed |
| Bar | ≥68% / calibrated / wise 70% | Prefer ≥62%; soft-fill ≥55% |
| Ranking | Confidence | Joint × mix × correlation score |

---

## 6. Prediction pages / fixture detail / markets

### 6.1 How a user gets a soccer prediction

1. Hub `/sports/soccer` → league home `/<league_key>/` (e.g. `/premier_league/`).
2. Round unlock: a round appears only after previous round fixtures have kicked off (`compute_unlocked_round`).
3. Click match → `/<league_key>/match/<home>/<away>`:
   - Loads prediction row CSV
   - `generate_betting_tips` (Safer Result, BTTS, O/U, corners, …)
   - Optional FotMob lineup via `live_predict` / `match_context`
4. Optional JSON: `/<league_key>/api/predict/<home>/<away>` — locked rounds return 403; failures return clean 500 JSON (no stack traces).

### 6.2 Baseball / cricket / tennis match pages

| Sport | Detail route pattern | Predict module |
|-------|----------------------|----------------|
| MLB | `/mlb/match/<home_code>/<away_code>` | `mlb_predict` + Elo blend |
| NPB | `/npb/match/<home_id>/<away_id>` | `npb_predict` |
| Cricket | `/<comp>/match/<id>` or `/<comp>/fixture/<slug>` | `cricket_predict` |
| Tennis | `/tennis/fixture/<slug>` | `tennis_predict` |

Hubs: `/sports/baseball`, `/sports/cricket`, `/sports/tennis` (plus per-comp homes like `/ipl/`, `/hundred/`).

### 6.3 Markets by sport (what models price)

| Sport | Markets on boards / picks |
|-------|---------------------------|
| Soccer | 1X2 / Safer Result / DNB / AH / BTTS / O2.5 / corners |
| MLB / NPB | Moneyline (combo pool; skipped public), totals, team runs, hits, Ks, handicap / Upset lean |
| Cricket | Match winner (combo pool; skipped public), totals, team runs, Upset lean |
| Tennis | Match winner, total sets (2.5/3.5), player to win a set, total games, handicap (±3.5 / ±5.5) |

Admin Telegram builder (`_gather_admin_full_market_legs`) can expose **all priced markets** without public confidence floors.

---

## 7. Results, accuracy hub, OOS / calibration

### 7.1 Public accuracy

- **`/accuracy`** — soccer Safer Result + BTTS / O2.5 / corners from `training_metrics.pkl`; other sports full-slate + 70% tier status from threshold JSON; Degen Team summary from `combo_results`.
- League results: `/mlb/results`, `/tennis/results`, cricket `/*/results`, combo `/todays-picks/combos/results`.
- `/llms.txt` / `/llms-full.txt` — AI-crawler summaries with live OOS chips when available.

### 7.2 Calibration scripts

| Script | Scope |
|--------|-------|
| `scripts/calibrate_confidence.py` | Soccer markets → `model/<league>/confidence_thresholds.json` |
| `scripts/calibrate_sports_confidence.py` | MLB / NPB / cricket / tennis → `data/*/confidence_thresholds.json` |
| `scripts/evaluate_safer_result.py` | Safer Result later-slice evidence → consumed by `safer_result.py` |
| `scripts/report_70_tier.py` | Reporting helper for 70% tier |
| `scripts/run_ec2_feedback_loop.py` | Settle + write `data/feedback/{latest,live_calibration,history}` |

### 7.3 Settlement pipeline

`combo_results.settle_pending()`:

1. Lookup score for each pending leg (`lookup_score_for_leg` → sport-specific).
2. `settle_leg_against_score` → won / lost / push / void / pending.
3. Roll up combo slips; update published picks.
4. Overdue = kickoff >6h still pending (date-only kickoffs get +1d 12h grace logic in helpers).

Admin can force-settle Degen Team slips via `/admin/degen-team/settle`.

### 7.4 Admin-only results tooling

- `/results` — admin Basic Auth; soccer results tooling
- `/<league_key>/backtest`, `/<league_key>/players` — also `@admin_required`

---

## 8. Non-sports (stocks / crypto)

### 8.1 Present and public

| Surface | Route | Behavior |
|---------|-------|----------|
| Crypto board | `/crypto` | Horizon filter; Beat BTC when absolute return withheld; stablecoins excluded |
| Crypto asset | `/crypto/asset/<ticker>` | Detail + TradingView when mapped; relative lean language |
| Stocks landing | `/stocks` | US / India chooser |
| Stocks board | `/stocks/us`, `/stocks/india` | Ranked by predicted horizon return + outperform / peer lean |
| Stock asset | `/stocks/<market>/asset/<ticker>` | Same honesty banners |

**Data:** `data/investment_scoring/ranked_assets.csv` (+ horizon metric JSON). Live prices/charts via CoinGecko / TradingView (`tv_symbols.py`). Alpha Vantage stock fetcher was **removed** (never implemented); rankings come from the offline investment scoring pipeline.

**Not combo legs:** stocks/crypto never enter Today’s Picks / Best Combos.

### 8.2 Removed / disabled product surfaces

| Path | Status |
|------|--------|
| `/dubai`, `/dubai/*` | **404** (`dubai_removed`) — not in nav |
| `/metals`, `/metals/*` | **404** (`metals_removed`) — not in nav |
| Dubai/metals fetchers + timers | May still exist under `automation/` / `deploy/` for caches; pages are gone |
| Reddit auto-post | **Not present** in `daily_digest.py` (no Reddit channel). Drafts are X / Quora / Shorts / Telegram only. Treat Reddit as **removed / unused**. |

> Note: `README.md` still mentions Dubai/metals as “reachable by URL”; **code overrides** — they abort 404. Prefer this bible + `app.py` over the README blurb.

---

## 9. Daily distribution

Full ops note: [`docs/DAILY_DISTRIBUTION.md`](DAILY_DISTRIBUTION.md).

### 9.1 Job

`deploy/daily_distribute.sh` → `webapp/daily_digest.py`  
Timer: `degen-daily-distribute.timer` at **03:30 UTC (~09:00 IST)**.

**Always writes:**

- `data/daily_digest/YYYY-MM-DD.json` (+ `latest.json`)
- `output_tables/daily_drafts/YYYY-MM-DD.md` (X / Quora / Shorts / Telegram copy)
- Slip card images under `data/daily_digest/cards/` when posting Telegram

**Idempotent** per Asia/Kolkata day via `data/daily_digest/state.json`.

**Posts when credentials exist:**

1. **Telegram first** — `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` (numeric `-100…`, not invite link)
2. Optional **X/Twitter** — `TWITTER_*` / `X_*` (morning slips and RESULT slips)
3. Optional **email** — `SMTP_*` + `DIGEST_EMAIL_TO`

Settled RESULT cards (`post_event_refresh`) post the same slip PNG to Telegram and X. Telegram and X are tracked independently so a failed X post retries without double-posting Telegram.

**Reddit:** not implemented (removed / never wired). Do not expect Reddit posts from this job.

### 9.2 Site CTAs

- `TELEGRAM_CHANNEL_URL` — public invite for humans (`/go/telegram`, homepage CTAs). **Not** the bot chat id.
- Default invite hardcoded in `daily_digest.DEFAULT_TELEGRAM_CHANNEL_URL` if env empty.

### 9.3 Content source

Same high-prob pool as the site (`_gather_combo_picks_today`) — **no parallel pick model**. Top-3 by confidence + featured combo slips for Telegram images.

---

## 10. Admin

### 10.1 Access

- HTTP Basic Auth: `ADMIN_USER` / `ADMIN_PASSWORD`
- If either unset → admin routes **404** (fails closed)
- When `ADMIN_USER` is set, `FLASK_SECRET_KEY` must be a real non-placeholder value (app refuses to start otherwise)
- CSRF: session token required on admin POSTs (`csrf_protect`); minted on admin GETs

### 10.2 `/admin` sections (`?section=`)

| Section | Purpose |
|---------|---------|
| `overview` | Dashboard summary + quick links |
| `feedback` | User feedback inbox |
| `traffic` / `pages` / `recurring` / `visitors` / `requests` | Analytics (GeoIP enriched) |
| `tools` | Ops helpers |
| `picks` | Filtered live pick legs |
| `combos` | Filtered combo boards |
| `combo_results` | Snapshot + settle + 30d slip list |
| `telegram` | Send slips / OCR paste builder (`/admin/telegram-send`, `/admin/telegram-parse-slip`) |
| `degen_team` | Manual settle for Telegram-posted “Degen Team” slips |

### 10.3 Degen Team

Admin Telegram sends persist as `source=degen_team` in SQLite (multis → `combo_slips`, singles → `published_picks`). Public surface: “Best Combos by Degen Team” on combos page + accuracy hub summary. Settle via `/admin/degen-team/settle` (CSRF + Basic Auth).

Optional OCR for pasted slip images: `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` (`slip_ocr.py`).

### 10.4 Accounts (optional, prod off)

Routes `/login`, `/signup`, `/logout`, Google OAuth exist but gated by `ACCOUNTS_ENABLED` (production.env.example sets `0`). Storage would be DynamoDB via `auth.py` when enabled.

---

## 11. Models

### 11.1 Where artifacts live

| Domain | Paths (as expected by code) | Train entrypoints |
|--------|-----------------------------|-------------------|
| Soccer | `model/{pl,laliga,seriea,bundesliga,ligue1,mls}/` — stacked `*_base_models.pkl`, `*_meta.pkl`, `feature_cols.pkl`, `training_metrics.pkl`, `confidence_thresholds.json` | `scripts/train_models.py`, `predict_pl.py`, `backtest_pl.py`, `calibrate_confidence.py` |
| MLB | `data/mlb/ml_ensemble_model.joblib` (+ Elo CSVs) | `scripts/mlb/train_ml_models.py`, `build_elo.py`, `download_*` |
| NPB | `data/npb/ml_ensemble_model.joblib` | `scripts/npb/train_ml_models.py`, … |
| Cricket | `data/cricket/{winner,team1_runs,team2_runs,total_runs}_model.joblib` | `scripts/cricket/train_ml_models.py`, … |
| Tennis | `data/tennis/ml_ensemble_model.joblib`, `ml_markets_model.joblib` | `scripts/tennis/train_ml_models.py`, … |
| Investments | `data/investment_scoring/ranked_assets.csv` | Local Windows retrain scripts under `deploy/retrain_investment_scoring.ps1` (heavy work stays off EC2) |

> Workspace note: large `.joblib` / soccer `.pkl` weights may be gitignored or only on the training machine / EC2. Thresholds and CSVs are more often present. Code paths above are authoritative.

### 11.2 What each sport predicts (summary)

- **Soccer:** result (1X2), BTTS, over 2.5, favorite-avoids-defeat / Safer Result inputs, corners when trained.
- **MLB/NPB:** win probability ensemble blended with Elo; props derived for picks.
- **Cricket:** winner + team/total runs regressors; weather/sentiment on live boards.
- **Tennis:** match winner (+ surface Elo) and markets model for sets/games/handicap.
- **Crypto/stocks:** walk-forward tabular/sequence models → ranked forward returns + outperform probabilities.

### 11.3 Promote / backup

`scripts/promote_gate.py` compares live vs candidate scores before promoting model files; backups under `data/feedback/model_backup/`.

Retrain on EC2: **avoid** — small instance; soccer/cricket/investment retrain tasks are Windows-oriented (`deploy/register_*_retrain_task.ps1`). EC2 timers refresh data + settle only.

---

## 12. Database / persistence

| Store | Location | Contents |
|-------|----------|----------|
| Combo / picks ledger | `webapp/combo_results.db` | `combo_slips`, `combo_legs`, `published_picks` (+ degen_team columns) |
| Analytics | `webapp/analytics.db` | Visits (human/bot filter) |
| Feedback form | `webapp/feedback.db` | `/feedback` submissions |
| Users (optional) | DynamoDB / local `users.db` | Only if accounts enabled |
| Combo page cache | `combo_full.pkl`, `combo_fast.pkl` | Legs + combos bundle |
| Sitemap cache | `webapp/sitemap_cache.xml` | Shared across gunicorn workers |
| Results HTML caches | `results_cache.html`, `mlb_results_cache.html` | Render caches |
| Daily digest | `data/daily_digest/*` | JSON + state + card images |
| Feedback metrics | `data/feedback/*.json` + `history.jsonl` | Live calibration, post_event report |
| Drafts | `output_tables/daily_drafts/` | Manual social copy |
| Prediction tables | `output_tables/*_ml_predictions.csv` | Soccer boards |

Sync excludes DBs and combo pkls so production ledgers survive deploys.

---

## 13. Security measures

| Control | Where / what |
|---------|----------------|
| Flask secret | `FLASK_SECRET_KEY`; placeholder forbidden when `ADMIN_USER` set |
| Admin auth | HTTP Basic; missing creds → 404 |
| CSRF | Session token on admin POSTs; `X-CSRF-Token` or form field |
| Secure cookies | `SESSION_COOKIE_SECURE` when admin user set |
| nginx body size | `client_max_body_size 8m` |
| Rate limits | `deploy/nginx-limits.conf` zones keyed by `CF-Connecting-IP` when present: general 20 r/s, admin 5, API 3, feedback 1 |
| Location bursts | `/admin`, `/todays-picks/digest`, `/feedback`, `/api/predict`, `/` |
| Security headers | X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy |
| fail2ban | `deploy/jail.local` — sshd, nginx-http-auth, botsearch, limit-req |
| robots.txt | Disallows `/api/`, `/admin`, `/results`, digest/top3 JSON; allows major AI crawlers; points sitemap |
| Cloudflare | Front door for DNS/HTTPS/WAF; nginx still sets headers for origin hits; rate-limit key prefers CF IP |
| Secrets | `production.env` never committed; example file only |
| Predict API | Rate-limited; unlocked-round gate; sanitized errors |

Apply hardening on server: `bash deploy/apply_bot_hardening.sh`.

---

## 14. Automation / timers

Install / refresh timers: `bash deploy/setup_automation.sh` (packs automation + systemd units, enables timers).

| Unit | Cadence | Script / effect |
|------|---------|-----------------|
| `degen-predictions.service` | always | gunicorn Flask app |
| `degen-post-event.timer` | every 15 min | `webapp/post_event_refresh.py` — bust live caches → settle → `data/feedback/post_event.json` |
| `degen-daily-distribute.timer` | 03:30 UTC | `deploy/daily_distribute.sh` → Top-3 + optional posts |
| `degen-refresh-data.timer` | every 3h | `deploy/refresh_daily_data.sh` — MLB/NPB pull + Elo + `run_ec2_feedback_loop.py` |
| `degen-live-tracker.timer` | every 5 min | `automation/live_match_tracker.py` — **scaffold** (returns zero live matches until wired) |
| `degen-reboot.timer` | 04:00 UTC | instance reboot |
| `degen-refresh-dubai-prices.timer` | daily (legacy) | RapidAPI Dubai prices — pages removed; timer may linger |
| `certbot.timer` | system | Let’s Encrypt renew |
| ~~`degen-automation.timer`~~ | **removed** | Stub retrain; `setup_automation.sh` disables/deletes if present |

Windows Task Scheduler counterparts exist for local retrain / distribute (`deploy/*.ps1`).

**Cron note:** older `DEPLOY.md` mentions a 06:00 `daily_predict.sh` crontab for soccer predictions; prefer verifying what is actually enabled on the box (`systemctl list-timers 'degen-*'`).

---

## 15. Deploy

### 15.1 Sync modes

| Script | Packs | Use when |
|--------|-------|----------|
| `deploy/sync_webapp_only.sh` | `webapp/` only | Fast UI/route fixes; avoids huge `scripts/` |
| `deploy/sync.sh [extra dirs…]` | `webapp/` + `scripts/` (+ optional `data`, `model`, …) | Code + pipeline changes |
| GitHub Actions `.github/workflows/deploy.yml` | `webapp` + `scripts` on green push to `main`/`master` | Automated CD after pytest |

All restart `degen-predictions` and exclude runtime DBs / combo caches / feedback from overwrite where configured.

### 15.2 First-time / infra

See [`deploy/DEPLOY.md`](../deploy/DEPLOY.md): IAM, Route53, EC2 Ubuntu, Elastic IP, `setup_ec2.sh` (nginx, venv, certbot, systemd), HTTPS.

### 15.3 Env

Copy `deploy/production.env.example` → `deploy/production.env` on EC2 (not in git). Loaded by systemd `EnvironmentFile=`.

### 15.4 nginx

- Site: `deploy/nginx.conf` → `/etc/nginx/sites-available/degen-predictions`
- Limits: `deploy/nginx-limits.conf` → `/etc/nginx/conf.d/degen-limits.conf`
- Static alias → `webapp/static/` with long cache

---

## 16. Feature inventory

### 16.1 Nav / footer (public)

Home · Today’s Picks (dropdown: Best Combos, Combo results, all leagues) · Sports · Crypto · Stocks · About · Accuracy · Terms · Privacy · Feedback · FAQ · Telegram · Instagram · email

Login/Signup only if `accounts_enabled`.

### 16.2 Public routes checklist

**Core**

- [x] `/` home
- [x] `/sports`, `/sports/soccer|baseball|cricket|tennis`
- [x] `/about`, `/accuracy`, `/faq`, `/terms`, `/privacy`, `/feedback`
- [x] `/contact` → 301 `/about`
- [x] `/go/telegram`
- [x] `/robots.txt`, `/sitemap.xml`, `/ads.txt`, `/llms.txt`, `/llms-full.txt`

**Today’s Picks**

- [x] `/todays-picks` (+ sport/league)
- [x] `/todays-picks/combos`, `/todays-picks/combos/results`
- [ ] Digest/top3 public — **admin-gated**

**Soccer leagues** (`premier_league`, `la_liga`, `serie_a`, `bundesliga`, `ligue_1`, `mls`)

- [x] `/<league>/`, `/<league>/match/...`, `/<league>/api/predict/...`, `/<league>/about`
- [ ] `/<league>/backtest`, `/<league>/players` — **admin**
- [ ] Global `/results` — **admin**

**Baseball**

- [x] `/mlb/fixtures`, `/mlb/results`, `/mlb/match/...`, `/mlb/api/predict`
- [x] `/mlb/`, `/mlb/today` → redirect to Today’s Picks MLB
- [x] `/npb/fixtures`, `/npb/match/...`, `/npb/api/predict`

**Cricket**

- [x] `/hundred|mlc|ipl|bbl|cpl/` + fixtures + results + match/fixture detail

**Tennis**

- [x] `/tennis/`, fixtures, results, fixture detail

**Investments**

- [x] `/crypto`, `/crypto/asset/<ticker>`
- [x] `/stocks`, `/stocks/us|india`, asset detail

**Removed**

- [x] `/dubai*`, `/metals*` → 404

### 16.3 Sitemap static seeds (from `_build_sitemap_xml`)

Includes Today’s Picks trees, sport hubs, fixture/results pages, accuracy/about/faq, crypto/stocks, plus dynamic match/asset URLs when data loads.

---

## 17. Key file index

| If you need… | Open… |
|--------------|--------|
| All HTTP routes | `webapp/app.py` |
| Today’s Picks floors / TODAY_SPORTS / combo scoring | `webapp/today_picks.py` |
| Settle / snapshot / feedback report | `webapp/combo_results.py` |
| Safer Result rule | `webapp/safer_result.py` |
| Daily Telegram / drafts | `webapp/daily_digest.py` |
| Post-event timer job | `webapp/post_event_refresh.py` |
| Soccer league config | `scripts/leagues.py` (+ `webapp` import of LEAGUES) |
| MLB fixtures/predict | `webapp/mlb_fixtures.py`, `mlb_predict.py` |
| NPB fixtures/predict | `webapp/npb_fixtures.py`, `npb_predict.py` |
| Cricket fixtures/predict | `hundred_fixtures.py`, `cpl_fixtures.py`, `cricket_fd_fixtures.py`, `cricket_predict.py` |
| Tennis fixtures/predict/score | `tennis_fixtures.py`, `tennis_predict.py`, `tennis_score.py` |
| Investment boards | `app.py` rankings helpers + `data/investment_scoring/` |
| Admin UI | `templates/admin_dashboard.html` + `partials/admin_*` |
| Nginx / limits / fail2ban | `deploy/nginx.conf`, `nginx-limits.conf`, `jail.local` |
| Env template | `deploy/production.env.example` |
| Sync / automation install | `deploy/sync*.sh`, `setup_automation.sh` |
| Data honesty catalog | `docs/DATA_SOURCES.md` |
| Agent wiring rules | `.cursor/rules/*.mdc` |
| CI/CD | `.github/workflows/deploy.yml` |
| AI crawler text | `webapp/llms_txt_content.py` |

---

## 18. Known gaps / caveats

Verified limitations (do not assume they are fixed):

1. **Soccer combo settle lags** football-data history; no live soccer scoreboard settle yet.
2. **`automation/live_match_tracker.py` is a scaffold** — timer runs but finds zero live matches until wired.
3. **Dubai / metals pages 404**; README and some timers/docs may still mention them.
4. **Large model binaries** may be absent from a sparse checkout; thresholds/CSVs can exist without `.joblib` weights.
5. **Accounts disabled in prod** — login UI may not show; DynamoDB path unused.
6. **Reddit** not in distribution code; drafts cover X / Quora / Shorts / Telegram only.
7. **MLC** has no live upcoming feed by design.
8. **Combo cache staleness** after wiring a new league — clear pkl / restart if sport missing from combos.
9. Exact Cloudflare dashboard settings (WAF rules, caching levels) are **ops console**, not fully encoded in repo — only CF IP header usage and docs references are in-tree.
10. Host IP / PEM paths in sync scripts are environment-specific; rotate if the box moves.

---

*End of architecture bible. For day-to-day distribution ops see `docs/DAILY_DISTRIBUTION.md`; for “is this number real?” see `docs/DATA_SOURCES.md`.*
