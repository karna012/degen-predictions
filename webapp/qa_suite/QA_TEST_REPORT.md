# Degen Predictions — QA Test Report

**Product:** Degen Predictions (degenpredictions.com)  
**Application:** Flask webapp (`webapp/app.py`)  
**Report date:** 14 August 2026  
**Prepared for:** Karan, Researcher  
**Suite location:** `webapp/qa_suite/`  
**Execution:** Flask test client (in-process). Does not hit production, Telegram, or X.

---

## 1. Executive summary

A dedicated QA suite was added beside the existing `webapp/tests/` folder. It covers smoke, sanity, API, end-to-end journeys, integration, regression, performance guardrails, defensive security, API contracts, black-box HTTP checks, and A/B *consistency* (this site has no split-testing framework).

| Result | Count |
|---|---|
| Collected | 133 |
| Passed | 131 |
| Skipped | 2 |
| Failed | 0 |
| Runtime | ~1 min 57 s (local Windows, Flask test client) |

**Verdict:** Public surfaces, NFL wiring, unlock APIs, admin gating, and combo settlement contracts are healthy in this environment. Two soccer live-predict contract cases skipped when the model returned an unavailable/locked fixture — the JSON error path still hides internals.

This is **not** a production load test, browser-automation pentest, or live-channel send test. Channel auto-post remains off (`CHANNEL_AUTO_POST` unset).

---

## 2. Scope and method

| Type | What we ran | What we did **not** run |
|---|---|---|
| Smoke | Critical GETs must not 5xx | Multi-region CDN / nginx |
| Sanity | Recent UX rules (See fixtures, Unlock prediction, NFL in nav) | Visual pixel diffs |
| API | Predict JSON, unlock 204, GP hop allowlist | AuthN against live Basic Auth secrets |
| E2E | Multi-step journeys via test client | Selenium / Playwright against prod |
| Integration | today_picks ↔ analytics ↔ combo_results ↔ sitemap | ESPN live outage injection |
| Regression | Auto-post off, no Bet Now, combo cover cap = 1 | Historical git bisect |
| Performance | Per-request budgets (≤8 s pages, unlock &lt; 0.5 s) | k6/Locust concurrent users |
| Security | Admin 401, CSRF on send, honeypot, no traceback, robots Disallow | Offensive exploits or credential stuffing |
| Contract | Sitemap XML, llms.txt, NFL/soccer JSON keys and types | OpenAPI codegen |
| Black box | Opaque GET/POST status and branding | Ignoring HTML structure only — still HTTP |
| A/B | Deterministic copy; no experiment cookies | Optimizely-style variant assignment (N/A) |

Existing unit/functional tests in `webapp/tests/` remain the day-to-day regression pack (NFL, MLB, admin, SEO, etc.).

---

## 3. Results by suite

| File | Cases | Focus | Result |
|---|---|---|---|
| `test_01_smoke.py` | 39 | Home, sports hubs, NFL, picks, sitemap, retired `/dubai` `/metals` | Pass |
| `test_02_sanity.py` | 7 | NFL catalog, See fixtures, Unlock prediction, disclaimer | Pass |
| `test_03_api.py` | 10 | Unlock 204, predict 404/JSON, NFL contract, no traceback | Pass (1 skip) |
| `test_04_e2e.py` | 5 | Home→NFL unlock; Today's Picks; soccer; combos; FAQ | Pass |
| `test_05_integration.py` | 6 | NFL tips + favorite-cover; GP analytics; settle ML/spread/totals | Pass |
| `test_06_regression.py` | 8 | Auto-post default off; CTA copy; combo caps; hidden combo results | Pass |
| `test_07_performance.py` | 9 | Page budgets; repeated homepage; unlock latency | Pass |
| `test_08_security.py` | 16 | Admin lock, CSRF, GP allowlist, honeypot, robots, XSS escape | Pass |
| `test_09_contract.py` | 7 | XML/plain-text/JSON shapes | Pass (1 skip) |
| `test_10_blackbox.py` | 22 | Anonymous 200s, 404/308 unknown URL, no ISE banner | Pass |
| `test_11_ab_consistency.py` | 4 | No variant cookies; stable Sports/NFL copy | Pass |

**Skips (expected):** soccer live `/premier_league/api/predict/...` when the first CSV fixture is locked or the live model returns “Prediction unavailable”. Error JSON is still asserted not to leak a traceback in the dedicated API test.

---

## 4. Findings and observations

### 4.1 Pass — product rules

- American football / NFL is on Sports, Today's Picks, sitemap, FAQ, and nav.
- League hub cards use **See fixtures**; fixture cards use **Unlock prediction**; combos use **Unlock combo**.
- `/api/unlock/pick` and `/api/unlock/combo` return **204** with an empty body.
- `/nfl/api/predict` JSON includes `home_prob`, `away_prob`, `spread_pick`, `predicted_total_points`, `insights`.
- Combo settlement grades NFL moneyline, spread, and total points from a numeric box score.
- `CHANNEL_AUTO_POST` defaults **off**.
- Admin, digest, combo results, and backtests are not public (401 or 404).
- Feedback honeypot field `website` does not call `submit_feedback`.
- `/go/gp/...` only allows cricket/tennis fixture paths (no open redirect to arbitrary hosts).
- Retired `/dubai` and `/metals` are not live apps.

### 4.2 Notes — not failures in this run

1. **Soccer live predict can return HTTP 500** with `{"error": "Prediction unavailable..."}` for some CSV rows. Internals are not leaked. Worth treating “unavailable” as 503 in a later cleanup so monitors do not count it as an application crash.
2. **No browser security headers** are set in `@app.after_request` (analytics logging only). Consider `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, and a conservative CSP on HTML responses.
3. **XGBoost pickle version warnings** appear when loading soccer models — operational debt, not a functional fail in this suite.
4. **Performance tests are single-threaded** on the Flask client. They catch “this page got 10× slower,” not saturation of gunicorn workers or ESPN.
5. **A/B testing is not implemented.** Consistency tests lock the current single experience so a future experiment flag cannot silently fork CTAs.

### 4.3 Out of scope (by design)

- Sending Telegram or X posts.
- Scanning third-party ESPN/IndexNow endpoints.
- Credentialed production admin.
- WCAG audit, visual regression, or mobile device lab.

---

## 5. How to run

From `webapp/`:

```bash
# QA suite only
python -m pytest qa_suite -q

# One class of tests
python -m pytest qa_suite -m smoke -q
python -m pytest qa_suite -m security -q
python -m pytest qa_suite -m api -q

# Existing product tests + QA suite
python -m pytest -q
```

Markers: `smoke`, `sanity`, `api`, `e2e`, `integration`, `regression`, `performance`, `security`, `contract`, `blackbox`, `ab`.

Analytics/feedback SQLite in this suite is redirected to temp files so runs do not pollute production DBs.

---

## 6. Recommendations

| Priority | Item |
|---|---|
| Medium | Map soccer live-predict “unavailable” to 503 (keep the same JSON `error` string). |
| Medium | Add standard HTTP security headers on HTML responses. |
| Low | Re-export soccer XGBoost models to silence pickle warnings. |
| Low | Optional Playwright smoke against `127.0.0.1` after deploy (true browser E2E). |
| Low | Optional Locust/k6 against staging for concurrent `/nfl/fixtures` + predict. |

---

## 7. Traceability

| Requirement | Tests |
|---|---|
| Site stays up | `test_01_smoke`, `test_10_blackbox` |
| NFL behaves like other sports | `test_02_sanity`, `test_04_e2e`, `test_05_integration` |
| Unlock APIs | `test_03_api`, `test_09_contract` |
| Admin-only channel / no auto-post | `test_06_regression`, `test_08_security` |
| Combo anti-wipeout constants | `test_06_regression`, `test_05_integration` |
| JSON contracts | `test_09_contract`, `test_03_api` |
| Deterministic UX (no A/B) | `test_11_ab_consistency` |

---

*End of report. Re-run `python -m pytest qa_suite -q` after material deploys and paste a new results row into section 1.*
