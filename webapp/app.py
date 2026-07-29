import datetime
import functools
import os
import secrets
import sys

from urllib.parse import quote

import joblib
import pandas as pd
from authlib.integrations.flask_client import OAuth
from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import LoginManager, current_user, login_required, login_user, logout_user

import analytics
import auth
import feedback
import geoip
import sentiment
import live_predict
import dubai_predict
import metals_predict
from llms_txt_content import LLMS_TXT
from tv_symbols import CRYPTO_TV_SYMBOLS, STOCK_TV_SYMBOLS
from fotmob_lineup import get_live_matchday_info
from live_predict import predict_fixture_live
from mlb_fixtures import get_fixtures as get_mlb_fixtures
from mlb_predict import (
    blend_with_h2h as mlb_blend_with_h2h,
    blend_with_pitcher as mlb_blend_with_pitcher,
    blend_with_sentiment as mlb_blend_with_sentiment,
    blend_with_streak as mlb_blend_with_streak,
    generate_insights as mlb_generate_insights,
    get_h2h_record as mlb_get_h2h_record,
    load_mlb_elo,
    load_mlb_history,
    mlb_win_probability,
    predict_total_home_runs,
    predict_total_hits,
    predict_total_runs,
    predict_total_strikeouts,
    total_runs_over_under as mlb_total_runs_over_under,
    total_runs_range as mlb_total_runs_range,
)
from mlb_predict import TEAM_RUNS_STD_DEV as MLB_TEAM_RUNS_STD_DEV
from npb_fixtures import get_fixtures as get_npb_fixtures
from npb_predict import (
    blend_with_h2h as npb_blend_with_h2h,
    blend_with_sentiment as npb_blend_with_sentiment,
    blend_with_streak as npb_blend_with_streak,
    generate_insights as npb_generate_insights,
    get_h2h_record as npb_get_h2h_record,
    load_npb_elo,
    load_npb_history,
    npb_win_probability,
    predict_total_runs as npb_predict_total_runs,
    total_runs_over_under as npb_total_runs_over_under,
    total_runs_range as npb_total_runs_range,
)
from npb_predict import TEAM_RUNS_STD_DEV as NPB_TEAM_RUNS_STD_DEV
import cricket_predict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBAPP_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
from leagues import LEAGUES  # noqa: E402

from league_display import LEAGUE_DISPLAY

MLB_SCRIPTS_DIR = os.path.join(SCRIPTS_DIR, "mlb")
if MLB_SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, MLB_SCRIPTS_DIR)
from mlb_config import ESPN_TO_RETROSHEET  # noqa: E402

DEFAULT_LEAGUE = "premier_league"


class SimpleCache:
    """Minimal TTL-based cache without external dependencies."""
    def __init__(self, ttl_seconds=1800):
        self.cache = {}
        self.ttl = ttl_seconds

    def get(self, key):
        if key in self.cache:
            value, timestamp = self.cache[key]
            if datetime.datetime.now().timestamp() - timestamp < self.ttl:
                return value
            del self.cache[key]
        return None

    def set(self, key, value):
        self.cache[key] = (value, datetime.datetime.now().timestamp())


_dataframe_cache = SimpleCache(ttl_seconds=1800)


def cached_dataframe(cache_key):
    """Decorator to cache DataFrame results for 30 minutes."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            cached = _dataframe_cache.get(cache_key)
            if cached is not None:
                return cached
            result = func(*args, **kwargs)
            _dataframe_cache.set(cache_key, result)
            return result
        return wrapper
    return decorator


def _load_ml_ensemble(path):
    """Loads a trained win/loss ensemble saved by train_ml_models.py, if
    present. Returns None if the model file doesn't exist yet (e.g. it
    hasn't been trained locally and copied over) so callers can fall back
    to the Elo-only blend instead of erroring."""
    if not os.path.exists(path):
        return None
    return joblib.load(path)


MLB_ML_MODEL = _load_ml_ensemble(os.path.join(BASE_DIR, "data", "mlb", "ml_ensemble_model.joblib"))
NPB_ML_MODEL = _load_ml_ensemble(os.path.join(BASE_DIR, "data", "npb", "ml_ensemble_model.joblib"))


def _ml_win_probability(bundle, feature_row):
    """Runs the trained ensemble's own predict_proba on a single game's
    features and returns P(home win) -- None if unavailable, so the caller
    can skip the blend cleanly."""
    if bundle is None:
        return None
    X = pd.DataFrame([feature_row], columns=bundle["features"])
    if bundle["uses_scaled_features"]:
        X = pd.DataFrame(bundle["scaler"].transform(X), columns=bundle["features"])
    return float(bundle["model"].predict_proba(X)[0][1])


def blend_with_ml_model(prob_home, ml_prob, weight=0.5):
    """Averages the Elo/H2H/streak-blended probability with the trained
    ensemble's own probability -- the ensemble captures nonlinear feature
    interactions the additive Elo blend can't, so giving it equal weight
    (rather than treating it as a minor tiebreaker) is the point of having
    trained it at all. Falls back to the Elo-only prob_home when no trained
    model is available."""
    if ml_prob is None:
        return prob_home
    return (1 - weight) * prob_home + weight * ml_prob


def static_version(filename):
    """File mtime as a cache-busting query string. Without this, browsers
    that already cached an old static asset (e.g. under the old 7-day
    Cache-Control header) keep serving it until a hard refresh, since
    server-side header/content changes don't retroactively invalidate what's
    already sitting in the browser's cache -- appending a version tied to the
    actual file's last-modified time forces a fresh fetch the moment the file
    changes, regardless of what the browser has cached."""
    path = os.path.join(WEBAPP_DIR, "static", filename)
    try:
        return int(os.path.getmtime(path))
    except OSError:
        return 0


def estimated_end_iso(start_iso, hours):
    """A rough endDate for SportsEvent structured data -- Google lists
    endDate as a recommended (not required) Event property, and we don't
    track real game lengths, so this is an honest estimate (typical game
    duration) rather than a fabricated exact time. Returns "" if start_iso
    is empty/unparseable so the template can omit the field cleanly."""
    if not start_iso:
        return ""
    try:
        start = pd.to_datetime(start_iso)
        end = start + pd.Timedelta(hours=hours)
        return end.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return ""


def league_paths(league_key):
    cfg = LEAGUES[league_key]
    return {
        "pred": os.path.join(BASE_DIR, "output_tables", f"{cfg['output_name']}_ml_predictions.csv"),
        "backtest": os.path.join(BASE_DIR, "output_tables", f"{cfg['output_name']}_backtest.csv"),
        "metrics": os.path.join(BASE_DIR, cfg["model_dir"], "training_metrics.pkl"),
    }


def get_league_or_404(league_key):
    if league_key not in LEAGUES:
        abort(404)
    return LEAGUES[league_key]


app = Flask(__name__)
# Session-signing key -- also used for login sessions now (see auth.py /
# flask-login setup below), not just flash messages, so a real secret here
# matters in production (set FLASK_SECRET_KEY; the fallback is dev-only).
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-insecure-key")
app.jinja_env.globals["static_version"] = static_version

# Accounts (signup/login/Google sign-in) are being tested locally first --
# ACCOUNTS_ENABLED defaults on (for local dev) but is set to "0" in
# production.env until the feature is ready to go live there. When off, the
# routes 404 and the nav hides the Log In/Sign Up links entirely, rather
# than showing a broken/half-wired feature to real visitors.
ACCOUNTS_ENABLED = os.environ.get("ACCOUNTS_ENABLED", "1") == "1"
app.jinja_env.globals["accounts_enabled"] = ACCOUNTS_ENABLED

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id):
    return auth.get_user_by_id(user_id)


# "Sign in with Google" is only active once these are set (see deploy docs --
# they come from a Google Cloud Console OAuth 2.0 Client ID, not something
# this codebase can generate). Without them, the Google button is simply
# hidden and email/password signup still works standalone.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
oauth = OAuth(app)
if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

ADMIN_USER = os.environ.get("ADMIN_USER", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")


def admin_required(view):
    """Gates a route behind HTTP Basic Auth using ADMIN_USER/ADMIN_PASSWORD
    env vars. The site has no real accounts system (see app.secret_key
    comment above), so this is a deliberately lightweight admin gate rather
    than wiring up the unused users.db/auth.py accounts machinery just for
    one internal page. If the env vars aren't set on a given deploy, the
    route is refused outright (fails closed, not open)."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not ADMIN_USER or not ADMIN_PASSWORD:
            abort(404)
        auth = request.authorization
        valid = (
            auth and secrets.compare_digest(auth.username or "", ADMIN_USER)
            and secrets.compare_digest(auth.password or "", ADMIN_PASSWORD)
        )
        if not valid:
            return Response(
                "Authentication required.", 401,
                {"WWW-Authenticate": 'Basic realm="Degen Predictions Admin"'},
            )
        return view(*args, **kwargs)
    return wrapped


analytics.init_db()
feedback.init_db()


def get_client_ip():
    """The real visitor IP, not nginx's -- nginx (see deploy/nginx config)
    sets X-Real-IP to $remote_addr before proxying to gunicorn, since
    request.remote_addr alone would otherwise just show 127.0.0.1 for every
    visitor. Falls back to remote_addr for local/dev runs without nginx in
    front."""
    return request.headers.get("X-Real-IP") or request.remote_addr or "unknown"


@app.after_request
def log_visit(response):
    if analytics.should_log(request.path):
        analytics.log_visit(
            ip=get_client_ip(), path=request.path, method=request.method,
            status=response.status_code, user_agent=request.headers.get("User-Agent"),
            referrer=request.headers.get("Referer"),
        )
    return response


def compute_unlocked_round(df):
    """A round unlocks once every fixture in the previous round has kicked
    off. Round 1 is always unlocked; round N+1 unlocks once round N's last
    fixture date has passed -- this is global (based on real match dates),
    not tied to any per-user action."""
    if df.empty:
        return 1
    now = pd.Timestamp.now("UTC").tz_localize(None)
    dates = pd.to_datetime(df["DateUtc"])
    if dates.dt.tz is not None:
        dates = dates.dt.tz_localize(None)
    unlocked = 1
    for r in sorted(df["RoundNumber"].unique().tolist()):
        if dates[df["RoundNumber"] == r].max() < now:
            unlocked = r + 1
        else:
            break
    return unlocked


@cached_dataframe("predictions_{league_key}")
def load_predictions(league_key):
    path = league_paths(league_key)["pred"]
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    df["MatchDate"] = pd.to_datetime(df["DateUtc"]).dt.strftime("%a %d %b %Y, %H:%M UTC")
    return df


@cached_dataframe("backtest_{league_key}")
def load_backtest(league_key):
    path = league_paths(league_key)["backtest"]
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    df["MatchDate"] = pd.to_datetime(df["Date"], dayfirst=True, format="mixed").dt.strftime("%d %b %Y")
    return df


INVESTMENT_RANKINGS_PATH = os.path.join(BASE_DIR, "data", "investment_scoring", "ranked_assets.csv")


@cached_dataframe("investment_rankings")
def load_investment_rankings():
    """Output of the separate investment_scoring project (see that repo's
    main.py) -- refreshed by manually re-running that pipeline locally and
    copying output/ranked_assets.csv here, same pattern as the MLB/NPB
    model retrain: never generated live on request, never run on EC2."""
    if not os.path.exists(INVESTMENT_RANKINGS_PATH):
        return pd.DataFrame()
    return pd.read_csv(INVESTMENT_RANKINGS_PATH)


@app.route("/")
def root_redirect():
    return render_template("category_landing.html")


@app.route("/sports")
def sports_landing():
    return render_template("sports_landing.html")


@app.route("/sports/soccer")
def soccer_leagues():
    leagues_info = []
    for key, cfg in LEAGUES.items():
        df = load_predictions(key)
        display = LEAGUE_DISPLAY.get(key, {})
        leagues_info.append({
            "key": key, "label": cfg["label"],
            "fixture_count": len(df),
            "available": not df.empty,
            **display,
        })
    return render_template("soccer_leagues.html", leagues_info=leagues_info)


@app.route("/sports/baseball")
def baseball_leagues():
    mlb_elo = load_mlb_elo()
    mlb_upcoming = sum(1 for m in get_mlb_fixtures() if not m["completed"])
    mlb_info = {
        "key": "mlb", "label": "MLB", "monogram": "MLB", "color": "#041e42", "country": "USA",
        "fixture_count": mlb_upcoming, "available": not mlb_elo.empty,
    }

    npb_elo = load_npb_elo()
    npb_upcoming = sum(1 for m in get_npb_fixtures() if not m["completed"])
    npb_info = {
        "key": "npb", "label": "NPB", "monogram": "NPB", "color": "#bc002d", "country": "Japan",
        "fixture_count": npb_upcoming, "available": not npb_elo.empty,
    }

    return render_template("baseball_leagues.html", mlb_info=mlb_info, npb_info=npb_info)


@app.route("/sports/cricket")
def cricket_leagues():
    cricket_elo = cricket_predict.load_elo()
    hundred_matches = cricket_predict.matches_for_competition("hnd_male", limit=10000)
    mlc_matches = cricket_predict.matches_for_competition("mlc_male", limit=10000)
    hundred_info = {
        "key": "hundred", "label": "The Hundred", "monogram": "HND", "color": "#7b2ff7", "country": "England",
        "fixture_count": len(hundred_matches), "available": len(hundred_matches) > 0,
    }
    mlc_info = {
        "key": "mlc", "label": "MLC", "monogram": "MLC", "color": "#d4a017", "country": "USA",
        "fixture_count": len(mlc_matches), "available": len(mlc_matches) > 0,
    }
    return render_template("cricket_leagues.html", hundred_info=hundred_info, mlc_info=mlc_info)


CRICKET_COMPETITION_BY_SLUG = {"hundred": "hnd_male", "mlc": "mlc_male"}


def _cricket_fixtures_page(league_slug):
    """Upcoming/live matches ONLY -- see cricket_predict.upcoming_fixtures()
    docstring for why this is honestly empty today (no real upcoming-
    fixture source exists for Cricsheet-covered competitions), rather than
    quietly showing completed historical matches mislabeled as fixtures.
    Completed matches with predicted-vs-actual live on the /results page."""
    competition = CRICKET_COMPETITION_BY_SLUG[league_slug]
    fixtures = cricket_predict.upcoming_fixtures(competition)
    label = cricket_predict.COMPETITION_LABELS.get(competition, competition)
    season = cricket_predict.season_window(competition)
    return render_template(
        "cricket_fixtures.html", fixtures=fixtures, competition_label=label,
        league_slug=league_slug, season_window=season,
    )


def _cricket_results_page(league_slug):
    competition = CRICKET_COMPETITION_BY_SLUG[league_slug]
    matches_df = cricket_predict.matches_for_competition(competition, limit=30)
    matches = [cricket_predict.build_match_view(row) for _, row in matches_df.iterrows()]
    label = cricket_predict.COMPETITION_LABELS.get(competition, competition)
    return render_template(
        "cricket_results.html", matches=matches, competition_label=label, league_slug=league_slug,
    )


def _cricket_match_detail(league_slug, match_id):
    competition = CRICKET_COMPETITION_BY_SLUG[league_slug]
    row = cricket_predict.get_match_row(match_id)
    if row is None or row["Competition"] != competition:
        abort(404)
    match = cricket_predict.build_match_view(row)
    pitch_report = cricket_predict.get_venue_pitch_report(match["venue"])
    weather = cricket_predict.get_venue_weather(match["venue"], match["date"]) if match["date"] else None
    return render_template(
        "cricket_match.html", match=match, league_slug=league_slug, pitch_report=pitch_report, weather=weather,
    )


@app.route("/hundred/")
def hundred_home():
    return redirect("/hundred/fixtures")


@app.route("/hundred/fixtures")
def hundred_fixtures_page():
    return _cricket_fixtures_page("hundred")


@app.route("/hundred/results")
def hundred_results_page():
    return _cricket_results_page("hundred")


@app.route("/hundred/match/<int:match_id>")
def hundred_match_detail(match_id):
    return _cricket_match_detail("hundred", match_id)


@app.route("/hundred/fixture/<slug>")
def hundred_fixture_detail(slug):
    """Detail page for a real upcoming/live fixture (Wikipedia-sourced, see
    hundred_fixtures.py) -- separate from hundred_match_detail() above,
    which is only for completed matches already in the Cricsheet historical
    archive and keyed by a numeric match_id, not a slug."""
    import hundred_fixtures
    fixture = hundred_fixtures.get_fixture_by_slug(slug)
    if fixture is None:
        abort(404)
    detail = cricket_predict.build_fixture_detail(fixture)
    return render_template("hundred_fixture_detail.html", match=detail)


@app.route("/mlc/")
def mlc_home():
    return redirect("/mlc/fixtures")


@app.route("/mlc/fixtures")
def mlc_fixtures_page():
    return _cricket_fixtures_page("mlc")


@app.route("/mlc/results")
def mlc_results_page():
    return _cricket_results_page("mlc")


@app.route("/mlc/match/<int:match_id>")
def mlc_match_detail(match_id):
    return _cricket_match_detail("mlc", match_id)


@app.route("/crypto")
def crypto_rankings():
    df = load_investment_rankings()
    crypto_df = df[df["asset_class"] == "crypto"].sort_values("rank_within_asset_class") \
        if not df.empty else df
    return render_template("investment_rankings.html", rows=crypto_df.to_dict(orient="records"),
                            category="crypto", category_label="Crypto", back_href="/",
                            tv_symbol_map=CRYPTO_TV_SYMBOLS)


@app.route("/crypto/asset/<ticker>")
def crypto_asset_detail(ticker):
    df = load_investment_rankings()
    row = df[(df["asset_class"] == "crypto") & (df["ticker"] == ticker)]
    if row.empty:
        return render_template("not_found.html", home=ticker, away=""), 404
    return render_template("investment_asset_detail.html", asset=row.iloc[0].to_dict(),
                            category="crypto", category_label="Crypto", back_href="/crypto")


@app.route("/stocks")
def stocks_landing():
    return render_template("stocks_landing.html")


@app.route("/stocks/<market>")
def stocks_market_rankings(market):
    if market not in ("us", "india"):
        abort(404)
    asset_class = f"{market}_stocks"
    df = load_investment_rankings()
    market_df = df[df["asset_class"] == asset_class].sort_values("rank_within_asset_class") \
        if not df.empty else df
    label = "US Stocks" if market == "us" else "India Stocks"
    return render_template("investment_rankings.html", rows=market_df.to_dict(orient="records"),
                            category=f"stocks/{market}", category_label=label, back_href="/stocks",
                            tv_symbol_map=STOCK_TV_SYMBOLS)


@app.route("/stocks/<market>/asset/<ticker>")
def stocks_asset_detail(market, ticker):
    if market not in ("us", "india"):
        abort(404)
    asset_class = f"{market}_stocks"
    df = load_investment_rankings()
    row = df[(df["asset_class"] == asset_class) & (df["ticker"] == ticker)]
    if row.empty:
        return render_template("not_found.html", home=ticker, away=""), 404
    label = "US Stocks" if market == "us" else "India Stocks"
    return render_template("investment_asset_detail.html", asset=row.iloc[0].to_dict(),
                            category=f"stocks/{market}", category_label=label, back_href=f"/stocks/{market}")


def _format_dubai_prices_updated_at():
    updated_at_raw = dubai_predict.get_live_prices_updated_at()
    if not updated_at_raw:
        return None
    try:
        return datetime.datetime.fromisoformat(updated_at_raw).strftime("%b %d, %Y %H:%M")
    except ValueError:
        return None


@app.route("/dubai")
def dubai_landing():
    return render_template("dubai_landing.html",
                          areas=dubai_predict.get_areas_list(),
                          property_types=dubai_predict.get_property_types_list(),
                          bedroom_configs=dubai_predict.get_bedroom_configs_list(),
                          areas_summary=dubai_predict.get_areas_summary(),
                          prices_updated_at=_format_dubai_prices_updated_at())


@app.route("/dubai/guide")
def dubai_guide():
    """SEO landing page for Dubai investors."""
    return render_template("dubai_seo.html",
                          area_summary=dubai_predict.get_area_investment_summary(),
                          prices_updated_at=_format_dubai_prices_updated_at())


@app.route("/metals")
def metals_landing():
    """Metals price prediction calculator."""
    categories = metals_predict.get_categories()
    all_metals = metals_predict.get_all_metals()
    default_metal = next(name for name, data in all_metals.items() if data["category"] == "precious")
    return render_template("metals_landing.html",
                          categories=categories,
                          all_metals=all_metals,
                          default_metal=default_metal)


@app.route("/metals/api/predict", methods=["POST"])
def metals_predict_api():
    """Predict metal price and returns."""
    data = request.get_json()
    result = metals_predict.predict_price(
        metal_name=data.get("metal_name"),
        holding_period_months=int(data.get("holding_period_months", 6)),
        investment_amount_usd=float(data.get("investment_amount_usd", 10000))
    )
    return jsonify(result)


@app.route("/metals/insights")
def metals_insights():
    """Market insights for metals and stones."""
    market_stats = metals_predict.get_market_stats()
    return render_template(
        "metals_insights.html",
        market_stats=market_stats,
        price_last_updated=metals_predict.PRICE_LAST_UPDATED,
    )


@app.route("/dubai/api/calculate", methods=["POST"])
def dubai_calculate_roi():
    data = request.get_json()
    result = dubai_predict.predict_roi(
        area=data.get("area"),
        property_type=data.get("property_type"),
        bedroom_config=data.get("bedroom_config"),
        investment_amount_aed=float(data.get("investment_amount", 1500000)),
        holding_period_months=int(data.get("holding_period_months", 6))
    )
    return jsonify(result)


@app.route("/<league_key>/")
def index(league_key):
    league = get_league_or_404(league_key)
    df = load_predictions(league_key)
    team_filter = request.args.get("team", "").strip()

    unlocked_round = compute_unlocked_round(df)
    all_rounds = sorted(df["RoundNumber"].unique().tolist()) if not df.empty else []
    # Rounds are unlocked sequentially: a round only appears once every
    # fixture in the previous round has kicked off (see compute_unlocked_round).
    available_rounds = [r for r in all_rounds if r <= unlocked_round]

    round_filter = request.args.get("round", "")
    if round_filter and int(round_filter) in available_rounds:
        selected_round = int(round_filter)
    else:
        selected_round = unlocked_round if unlocked_round in all_rounds else (all_rounds[-1] if all_rounds else None)

    if selected_round is not None:
        df = df[df["RoundNumber"] == selected_round]
    if team_filter:
        mask = df["HomeTeam"].str.contains(team_filter, case=False, na=False) | \
            df["AwayTeam"].str.contains(team_filter, case=False, na=False)
        df = df[mask]

    matches = df.to_dict(orient="records")

    return render_template(
        "index.html", matches=matches, rounds=available_rounds,
        selected_round=selected_round, team_filter=team_filter,
        total=len(matches),
        league_key=league_key, league_label=league["label"], leagues=LEAGUES,
        league_keywords=LEAGUE_DISPLAY.get(league_key, {}).get("keywords", ""),
    )


def generate_betting_tips(m):
    """Turns raw model probabilities into plain-language leans + confidence.
    Thresholds are deliberately conservative -- football is high-variance,
    so a tip is only raised when the model shows real separation."""
    tips = []

    def confidence(p):
        return "High" if p >= 0.55 else ("Medium" if p >= 0.45 else "Low")

    result_probs = {m["HomeTeam"]: m["P_Home_Win"], "Draw": m["P_Draw"], m["AwayTeam"]: m["P_Away_Win"]}
    top_pick = max(result_probs, key=result_probs.get)
    top_p = result_probs[top_pick]
    second_p = sorted(result_probs.values(), reverse=True)[1]
    if top_p - second_p >= 0.08:
        tips.append({
            "market": "Match Result", "pick": top_pick, "probability": top_p,
            "confidence": confidence(top_p),
            "note": f"Clearest lean of the available outcomes ({top_p*100:.0f}% vs next-best {second_p*100:.0f}%).",
        })
    else:
        tips.append({
            "market": "Match Result", "pick": "No strong lean", "probability": top_p,
            "confidence": "Low",
            "note": "Outcomes are close together -- the model doesn't see a clear favourite.",
        })

    if m["P_BTTS_Yes"] >= 0.55 or m["P_BTTS_Yes"] <= 0.45:
        pick = "Yes" if m["P_BTTS_Yes"] >= 0.55 else "No"
        p = m["P_BTTS_Yes"] if pick == "Yes" else 1 - m["P_BTTS_Yes"]
        tips.append({"market": "Both Teams to Score", "pick": pick, "probability": p,
                      "confidence": confidence(p), "note": None})

    if m["P_Over_2.5_Goals"] >= 0.55 or m["P_Over_2.5_Goals"] <= 0.45:
        pick = "Over 2.5" if m["P_Over_2.5_Goals"] >= 0.55 else "Under 2.5"
        p = m["P_Over_2.5_Goals"] if pick == "Over 2.5" else 1 - m["P_Over_2.5_Goals"]
        tips.append({"market": "Total Goals", "pick": pick, "probability": p,
                      "confidence": confidence(p), "note": None})

    if m["P_Home_More_Corners"] >= 0.58 or m["P_Home_More_Corners"] <= 0.42:
        pick = f"{m['HomeTeam']} (more corners)" if m["P_Home_More_Corners"] >= 0.58 \
            else f"{m['AwayTeam']} (more corners)"
        p = m["P_Home_More_Corners"] if m["P_Home_More_Corners"] >= 0.58 else 1 - m["P_Home_More_Corners"]
        tips.append({"market": "Corners", "pick": pick, "probability": p,
                      "confidence": confidence(p), "note": None})

    p_fav = m.get("P_Favorite_Avoids_Defeat")
    if p_fav is None or pd.isna(p_fav):
        return tips
    tips.append({
        "market": "Favorite Avoids Defeat",
        "pick": f"{m['Favorite_Team']} win or draw" if p_fav >= 0.5 else f"{m['Favorite_Team']} loses",
        "probability": p_fav if p_fav >= 0.5 else 1 - p_fav,
        "confidence": confidence(p_fav if p_fav >= 0.5 else 1 - p_fav),
        "note": "Higher base-rate market: favourites in this league lose outright only ~25-30% of the time.",
    })

    return tips


@app.route("/<league_key>/match/<home>/<away>")
def match_detail(league_key, home, away):
    league = get_league_or_404(league_key)
    df = load_predictions(league_key)
    row = df[(df["HomeTeam"] == home) & (df["AwayTeam"] == away)]
    if row.empty:
        return render_template("not_found.html", home=home, away=away), 404
    match = row.iloc[0].to_dict()
    tips = generate_betting_tips(match)

    match_date = pd.to_datetime(match["DateUtc"]).strftime("%Y-%m-%d")
    lineup_info = get_live_matchday_info(home, away, match_date) if live_predict.USE_LIVE_LINEUP else None
    match_end_date = estimated_end_iso(match["DateUtc"], hours=2)
    # The source CSV stores DateUtc with a space separator ("2026-08-15
    # 07:30:00Z"), not proper ISO 8601 ("...T07:30:00Z") -- fine for the
    # human-readable date used elsewhere on this page, but Google's
    # structured-data validator rejects it as a malformed startDate. This
    # is the actual ISO-formatted value the SportsEvent JSON-LD uses.
    match_start_date_iso = estimated_end_iso(match["DateUtc"], hours=0)

    return render_template(
        "match.html", match=match, tips=tips, lineup_info=lineup_info,
        league_key=league_key, league_label=league["label"], league_country=league.get("country", ""),
        match_end_date=match_end_date, match_start_date_iso=match_start_date_iso,
    )


@app.route("/<league_key>/api/predict/<home>/<away>")
def api_predict(league_key, home, away):
    get_league_or_404(league_key)
    df = load_predictions(league_key)
    row = df[(df["HomeTeam"] == home) & (df["AwayTeam"] == away)]
    if row.empty:
        return jsonify({"error": "Fixture not found"}), 404

    row_dict = row.iloc[0].to_dict()
    round_number = int(row_dict["RoundNumber"])
    unlocked_round = compute_unlocked_round(df)
    if round_number > unlocked_round:
        return jsonify({
            "error": f"Round {round_number} is locked -- it unlocks once "
                     f"Round {round_number - 1} has kicked off.",
        }), 403

    match_date = pd.to_datetime(row_dict["DateUtc"]).strftime("%Y-%m-%d")
    try:
        result = predict_fixture_live(home, away, match_date, league_key=league_key)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    result["Unlocked_Round"] = unlocked_round
    return jsonify(result)


@app.route("/mlb/")
def mlb_home():
    return redirect("/mlb/fixtures")


@app.route("/mlb/fixtures")
def mlb_fixtures_page():
    fixtures = get_mlb_fixtures()
    elo_df = load_mlb_elo()
    elo_teams = set(elo_df["Team"]) if not elo_df.empty else set()

    for m in fixtures:
        m["home_code"] = ESPN_TO_RETROSHEET.get(m["home_abbr"])
        m["away_code"] = ESPN_TO_RETROSHEET.get(m["away_abbr"])
        m["has_rating"] = bool(m["home_code"] in elo_teams and m["away_code"] in elo_teams)

    # Only live/upcoming and postponed fixtures are shown on this page --
    # finished games are dropped entirely.
    #
    # ESPN marks postponed games as not-completed but leaves the original
    # (now-past) date/time on them since the makeup date isn't announced yet
    # -- comparing that date against today is what catches these, so a
    # postponed game from an earlier date doesn't sit at the top of
    # "upcoming" looking like it's about to happen.
    today_str = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    live_or_upcoming, postponed = [], []
    for m in fixtures:
        if m["completed"]:
            continue
        elif m["status"] != "in" and (m["date"] or "")[:10] < today_str:
            postponed.append(m)
        else:
            live_or_upcoming.append(m)

    grouped_by_date = {}
    for m in live_or_upcoming:
        day = (m["date"] or "")[:10]
        grouped_by_date.setdefault(day, []).append(m)

    return render_template(
        "mlb_fixtures.html", fixtures=fixtures,
        grouped_by_date=grouped_by_date, grouped_finished={},
        postponed=postponed,
    )


def compute_mlb_prediction(home_code, away_code, home_era="", away_era=""):
    """Shared by the JSON predict API and the full match-detail page --
    returns None if either team doesn't have enough rated games yet."""
    df = load_mlb_elo()
    row_home = df[df["Team"] == home_code]
    row_away = df[df["Team"] == away_code]
    if row_home.empty or row_away.empty:
        return None

    home_row, away_row = row_home.iloc[0], row_away.iloc[0]
    elo_home = float(home_row["Elo"])
    elo_away = float(away_row["Elo"])
    history = load_mlb_history()
    h2h = mlb_get_h2h_record(history, home_code, away_code)
    home_streak = int(home_row.get("CurrentStreak", 0))
    away_streak = int(away_row.get("CurrentStreak", 0))

    prob_home = mlb_win_probability(elo_home, elo_away)
    prob_home = mlb_blend_with_h2h(prob_home, h2h)
    prob_home = mlb_blend_with_pitcher(prob_home, home_era, away_era)
    prob_home = mlb_blend_with_streak(prob_home, home_streak, away_streak)

    home_sentiment = sentiment.get_team_sentiment(str(home_row["TeamName"]))
    away_sentiment = sentiment.get_team_sentiment(str(away_row["TeamName"]))
    prob_home = mlb_blend_with_sentiment(prob_home, home_sentiment, away_sentiment)

    h2h_meetings = h2h["meetings"]
    h2h_home_rate = (h2h["home_wins"] / h2h_meetings) if h2h_meetings else 0.5
    home_last10_rate = (home_row.get("Last10Wins", 0) / home_row["Last10Played"]) if home_row.get("Last10Played") else 0.5
    away_last10_rate = (away_row.get("Last10Wins", 0) / away_row["Last10Played"]) if away_row.get("Last10Played") else 0.5
    ml_prob_home = _ml_win_probability(MLB_ML_MODEL, {
        "EloDiff": elo_home - elo_away,
        "HomeLast10WinRate": home_last10_rate, "AwayLast10WinRate": away_last10_rate,
        "HomeStreak": home_streak, "AwayStreak": away_streak,
        "H2HMeetings": h2h_meetings, "HomeH2HWinRate": h2h_home_rate,
        "RunsForDiff": float(home_row["AvgRunsScored"]) - float(away_row["AvgRunsScored"]),
        "RunsAllowedDiff": float(away_row["AvgRunsAllowed"]) - float(home_row["AvgRunsAllowed"]),
        "HitsForDiff": float(home_row["AvgHitsFor"]) - float(away_row["AvgHitsFor"]),
        "HRForDiff": float(home_row["AvgHRFor"]) - float(away_row["AvgHRFor"]),
        "SOInducedDiff": float(home_row["AvgSOInduced"]) - float(away_row["AvgSOInduced"]),
    })
    prob_home = blend_with_ml_model(prob_home, ml_prob_home)

    home_runs, away_runs = predict_total_runs(home_row, away_row)
    home_hits, away_hits = predict_total_hits(home_row, away_row)
    home_hr, away_hr = predict_total_home_runs(home_row, away_row)
    home_so, away_so = predict_total_strikeouts(home_row, away_row)
    favourite = home_code if prob_home >= 0.5 else away_code
    total_runs = round(home_runs + away_runs, 1)
    runs_low, runs_high = mlb_total_runs_range(total_runs)
    runs_ou = mlb_total_runs_over_under(total_runs)
    home_runs_ou = mlb_total_runs_over_under(home_runs, std_dev=MLB_TEAM_RUNS_STD_DEV)
    away_runs_ou = mlb_total_runs_over_under(away_runs, std_dev=MLB_TEAM_RUNS_STD_DEV)
    insights = mlb_generate_insights(
        home_code, away_code, home_row, away_row, h2h, home_era, away_era,
        home_streak, away_streak, favourite, total_runs,
    )

    return {
        "home": home_code, "away": away_code,
        "home_prob": prob_home, "away_prob": 1 - prob_home,
        "elo_home": round(elo_home, 1), "elo_away": round(elo_away, 1),
        "h2h_home_wins": h2h["home_wins"], "h2h_away_wins": h2h["away_wins"], "h2h_meetings": h2h["meetings"],
        "favourite": favourite,
        "home_streak": home_streak, "away_streak": away_streak,
        "home_season_record": f"{int(home_row.get('SeasonWins', 0))}-{int(home_row.get('SeasonLosses', 0))}",
        "away_season_record": f"{int(away_row.get('SeasonWins', 0))}-{int(away_row.get('SeasonLosses', 0))}",
        "predicted_total_runs": total_runs,
        "predicted_total_runs_low": runs_low, "predicted_total_runs_high": runs_high,
        "total_runs_line": runs_ou["line"], "total_runs_pick": runs_ou["pick"],
        "total_runs_pick_probability": runs_ou["probability"],
        "home_predicted_runs": home_runs, "away_predicted_runs": away_runs,
        "home_runs_line": home_runs_ou["line"], "home_runs_pick_probability": home_runs_ou["probability"],
        "away_runs_line": away_runs_ou["line"], "away_runs_pick_probability": away_runs_ou["probability"],
        "predicted_total_hits": round(home_hits + away_hits, 1),
        "home_predicted_hits": home_hits, "away_predicted_hits": away_hits,
        "predicted_total_home_runs": round(home_hr + away_hr, 2),
        "home_predicted_home_runs": home_hr, "away_predicted_home_runs": away_hr,
        "predicted_total_strikeouts": round(home_so + away_so, 1),
        "home_predicted_strikeouts": home_so, "away_predicted_strikeouts": away_so,
        "insights": insights,
    }


@app.route("/mlb/api/predict")
def mlb_api_predict():
    home_code = request.args.get("home", "").strip()
    away_code = request.args.get("away", "").strip()
    home_era = request.args.get("home_era", "")
    away_era = request.args.get("away_era", "")

    result = compute_mlb_prediction(home_code, away_code, home_era, away_era)
    if result is None:
        return jsonify({"error": "One or both teams don't have enough rated games yet."}), 404
    return jsonify(result)


@app.route("/mlb/match/<home_code>/<away_code>")
def mlb_match_detail(home_code, away_code):
    home_era = request.args.get("home_era", "")
    away_era = request.args.get("away_era", "")
    home_name = request.args.get("home_name", home_code)
    away_name = request.args.get("away_name", away_code)
    match_date = request.args.get("date", "")

    result = compute_mlb_prediction(home_code, away_code, home_era, away_era)
    if result is None:
        return render_template("not_found.html", home=home_name, away=away_name), 404

    return render_template(
        "mlb_match.html", match=result, home_name=home_name, away_name=away_name,
        match_date=match_date, home_era=home_era, away_era=away_era,
        match_end_date=estimated_end_iso(match_date, hours=3),
    )


def compute_npb_prediction(home_id, away_id):
    """Same shape as compute_mlb_prediction, minus the pitcher-ERA blend and
    hits/HR/strikeouts markets (not available from this data source)."""
    df = load_npb_elo()
    row_home = df[df["TeamID"] == home_id]
    row_away = df[df["TeamID"] == away_id]
    if row_home.empty or row_away.empty:
        return None

    home_row, away_row = row_home.iloc[0], row_away.iloc[0]
    elo_home = float(home_row["Elo"])
    elo_away = float(away_row["Elo"])
    history = load_npb_history()
    h2h = npb_get_h2h_record(history, home_id, away_id)
    home_streak = int(home_row.get("CurrentStreak", 0))
    away_streak = int(away_row.get("CurrentStreak", 0))

    prob_home = npb_win_probability(elo_home, elo_away)
    prob_home = npb_blend_with_h2h(prob_home, h2h)
    prob_home = npb_blend_with_streak(prob_home, home_streak, away_streak)

    home_sentiment = sentiment.get_team_sentiment(str(home_row["TeamName"]))
    away_sentiment = sentiment.get_team_sentiment(str(away_row["TeamName"]))
    prob_home = npb_blend_with_sentiment(prob_home, home_sentiment, away_sentiment)

    h2h_meetings = h2h["meetings"]
    h2h_home_rate = (h2h["home_wins"] / h2h_meetings) if h2h_meetings else 0.5
    home_last10_rate = (home_row.get("Last10Wins", 0) / home_row["Last10Played"]) if home_row.get("Last10Played") else 0.5
    away_last10_rate = (away_row.get("Last10Wins", 0) / away_row["Last10Played"]) if away_row.get("Last10Played") else 0.5
    ml_prob_home = _ml_win_probability(NPB_ML_MODEL, {
        "EloDiff": elo_home - elo_away,
        "HomeLast10WinRate": home_last10_rate, "AwayLast10WinRate": away_last10_rate,
        "HomeStreak": home_streak, "AwayStreak": away_streak,
        "H2HMeetings": h2h_meetings, "HomeH2HWinRate": h2h_home_rate,
        "RunsForDiff": float(home_row["AvgRunsScored"]) - float(away_row["AvgRunsScored"]),
        "RunsAllowedDiff": float(away_row["AvgRunsAllowed"]) - float(home_row["AvgRunsAllowed"]),
    })
    prob_home = blend_with_ml_model(prob_home, ml_prob_home)

    home_runs, away_runs = npb_predict_total_runs(home_row, away_row)
    total_runs = round(home_runs + away_runs, 1)
    runs_low, runs_high = npb_total_runs_range(total_runs)
    runs_ou = npb_total_runs_over_under(total_runs)
    home_runs_ou = npb_total_runs_over_under(home_runs, std_dev=NPB_TEAM_RUNS_STD_DEV)
    away_runs_ou = npb_total_runs_over_under(away_runs, std_dev=NPB_TEAM_RUNS_STD_DEV)
    favourite_name = home_row["TeamName"] if prob_home >= 0.5 else away_row["TeamName"]
    insights = npb_generate_insights(
        home_row["TeamName"], away_row["TeamName"], home_row, away_row, h2h,
        home_streak, away_streak, favourite_name, total_runs,
    )

    return {
        "home": home_id, "away": away_id,
        "home_prob": prob_home, "away_prob": 1 - prob_home,
        "elo_home": round(elo_home, 1), "elo_away": round(elo_away, 1),
        "h2h_home_wins": h2h["home_wins"], "h2h_away_wins": h2h["away_wins"], "h2h_meetings": h2h["meetings"],
        "favourite_name": favourite_name,
        "home_streak": home_streak, "away_streak": away_streak,
        "home_season_record": f"{int(home_row.get('SeasonWins', 0))}-{int(home_row.get('SeasonLosses', 0))}",
        "away_season_record": f"{int(away_row.get('SeasonWins', 0))}-{int(away_row.get('SeasonLosses', 0))}",
        "predicted_total_runs_low": runs_low, "predicted_total_runs_high": runs_high,
        "total_runs_line": runs_ou["line"], "total_runs_pick": runs_ou["pick"],
        "total_runs_pick_probability": runs_ou["probability"],
        "predicted_total_runs": total_runs,
        "home_predicted_runs": home_runs, "away_predicted_runs": away_runs,
        "home_runs_line": home_runs_ou["line"], "home_runs_pick_probability": home_runs_ou["probability"],
        "away_runs_line": away_runs_ou["line"], "away_runs_pick_probability": away_runs_ou["probability"],
        "insights": insights,
    }


@app.route("/npb/")
def npb_home():
    return redirect("/npb/fixtures")


@app.route("/npb/fixtures")
def npb_fixtures_page():
    fixtures = get_npb_fixtures()
    elo_df = load_npb_elo()
    elo_teams = set(elo_df["TeamID"]) if not elo_df.empty else set()

    for m in fixtures:
        m["has_rating"] = bool(m["home_team_id"] in elo_teams and m["away_team_id"] in elo_teams)

    # Only live/upcoming and postponed fixtures are shown on this page --
    # finished games are dropped entirely.
    today_str = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    live_or_upcoming, postponed = [], []
    for m in fixtures:
        if m["completed"]:
            continue
        elif m["postponed"] and (m["date"] or "")[:10] < today_str:
            postponed.append(m)
        else:
            live_or_upcoming.append(m)

    grouped_by_date = {}
    for m in live_or_upcoming:
        day = (m["date"] or "")[:10]
        grouped_by_date.setdefault(day, []).append(m)

    return render_template(
        "npb_fixtures.html", fixtures=fixtures,
        grouped_by_date=grouped_by_date, grouped_finished={},
        postponed=postponed,
    )


@app.route("/npb/api/predict")
def npb_api_predict():
    try:
        home_id = int(request.args.get("home", ""))
        away_id = int(request.args.get("away", ""))
    except ValueError:
        return jsonify({"error": "Invalid team IDs."}), 400

    result = compute_npb_prediction(home_id, away_id)
    if result is None:
        return jsonify({"error": "One or both teams don't have enough rated games yet."}), 404
    return jsonify(result)


@app.route("/npb/match/<int:home_id>/<int:away_id>")
def npb_match_detail(home_id, away_id):
    home_name = request.args.get("home_name", str(home_id))
    away_name = request.args.get("away_name", str(away_id))
    match_date = request.args.get("date", "")

    result = compute_npb_prediction(home_id, away_id)
    if result is None:
        return render_template("not_found.html", home=home_name, away=away_name), 404

    return render_template(
        "npb_match.html", match=result, home_name=home_name, away_name=away_name, match_date=match_date,
        match_end_date=estimated_end_iso(match_date, hours=3),
    )


@app.route("/robots.txt")
def robots():
    """Robots.txt for search engine crawlers."""
    return Response("""User-agent: *
Allow: /
Disallow: /api/
Sitemap: https://degenpredictions.com/sitemap.xml

# Dubai-specific crawling
User-agent: Googlebot
Allow: /
Allow: /dubai/
Allow: /dubai/guide

User-agent: Bingbot
Allow: /
Allow: /dubai/
Allow: /dubai/guide
""", mimetype="text/plain")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/contact")
def contact():
    return render_template("contact.html")


@app.route("/faq")
def faq():
    return render_template("faq.html")


@app.route("/feedback", methods=["GET", "POST"])
def feedback_page():
    if request.method == "POST":
        if request.form.get("website"):  # honeypot -- real users never fill this in
            return redirect("/feedback")
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        message = request.form.get("message", "").strip()
        if not name or not email or not message:
            flash("Name, email, and feedback are all required.", "error")
            return redirect("/feedback")
        feedback.submit_feedback(
            name=name, email=email,
            message=message, page=request.form.get("page", ""), ip=get_client_ip(),
        )
        flash("Thanks for the feedback -- it's been sent.", "success")
        return redirect("/feedback")
    return render_template("feedback.html")


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if not ACCOUNTS_ENABLED:
        abort(404)
    if current_user.is_authenticated:
        return redirect("/")
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not email or not password:
            flash("Name, email, and password are all required.", "error")
            return redirect("/signup")
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return redirect("/signup")
        if auth.get_user_by_email(email):
            flash("An account with that email already exists -- try logging in instead.", "error")
            return redirect("/login")
        try:
            user = auth.create_user(email=email, name=name, password=password)
        except ValueError:
            flash("An account with that email already exists -- try logging in instead.", "error")
            return redirect("/login")
        login_user(user)
        flash(f"Welcome, {user.name}!", "success")
        return redirect("/")
    return render_template("signup.html", google_enabled=bool(GOOGLE_CLIENT_ID))


@app.route("/login", methods=["GET", "POST"])
def login():
    if not ACCOUNTS_ENABLED:
        abort(404)
    if current_user.is_authenticated:
        return redirect("/")
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = auth.get_user_by_email(email)
        if not user or not user.check_password(password):
            flash("Incorrect email or password.", "error")
            return redirect("/login")
        login_user(user)
        flash(f"Welcome back, {user.name}!", "success")
        return redirect("/")
    return render_template("login.html", google_enabled=bool(GOOGLE_CLIENT_ID))


@app.route("/logout")
@login_required
def logout():
    if not ACCOUNTS_ENABLED:
        abort(404)
    logout_user()
    flash("You've been logged out.", "info")
    return redirect("/")


@app.route("/login/google")
def login_google():
    if not ACCOUNTS_ENABLED or not GOOGLE_CLIENT_ID:
        abort(404)
    redirect_uri = url_for("login_google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@app.route("/login/google/callback")
def login_google_callback():
    if not ACCOUNTS_ENABLED or not GOOGLE_CLIENT_ID:
        abort(404)
    token = oauth.google.authorize_access_token()
    userinfo = token.get("userinfo")
    if not userinfo or not userinfo.get("email"):
        flash("Google sign-in failed -- please try again.", "error")
        return redirect("/login")

    google_id = userinfo["sub"]
    email = userinfo["email"].lower().strip()
    name = userinfo.get("name") or email.split("@")[0]

    user = auth.get_user_by_google_id(google_id)
    if not user:
        user = auth.get_user_by_email(email)
        if user:
            # Same email already signed up with a password -- link the
            # Google identity to that existing account rather than
            # creating a confusing second account with the same email.
            auth.link_google_id(email, google_id)
            user = auth.get_user_by_email(email)
        else:
            user = auth.create_user(email=email, name=name, google_id=google_id)

    login_user(user)
    flash(f"Welcome, {user.name}!", "success")
    return redirect("/")


SITE_URL = "https://degenpredictions.com"


@app.route("/ads.txt")
def ads_txt():
    body = "google.com, pub-9692020533590575, DIRECT, f08c47fec0942fa0\n"
    return Response(body, mimetype="text/plain")


@app.route("/llms.txt")
def llms_txt():
    """Plain-text site summary for AI crawlers/generative engines (GEO),
    following the llmstxt.org convention."""
    return Response(LLMS_TXT, mimetype="text/plain")


SITEMAP_CACHE_PATH = os.path.join(WEBAPP_DIR, "sitemap_cache.xml")
SITEMAP_CACHE_TTL_SECONDS = 1200  # 20 min


def _build_sitemap_xml():
    static_pages = [
        "", "terms", "privacy", "contact", "feedback", "mlb/fixtures", "npb/fixtures",
        "hundred/fixtures", "hundred/results", "mlc/fixtures", "mlc/results",
        "sports", "sports/soccer", "sports/baseball", "sports/cricket", "crypto", "stocks", "stocks/us", "stocks/india",
        "dubai", "dubai/guide", "metals",
    ]
    urls = [f"{SITE_URL}/{p}" for p in static_pages]
    for key in LEAGUES:
        urls.append(f"{SITE_URL}/{key}/")
        urls.append(f"{SITE_URL}/{key}/backtest")
        urls.append(f"{SITE_URL}/{key}/about")
        df = load_predictions(key)
        for _, row in df.iterrows():
            urls.append(f"{SITE_URL}/{key}/match/{quote(row['HomeTeam'])}/{quote(row['AwayTeam'])}")

    # Crypto/stocks asset detail pages -- same reasoning as the MLB/NPB
    # try/except below: a bad rankings CSV must never take the whole
    # sitemap down, just skip these dynamic URLs for this build.
    try:
        inv_df = load_investment_rankings()
        for _, row in inv_df.iterrows():
            asset_class = row["asset_class"]
            ticker = quote(str(row["ticker"]))
            if asset_class == "crypto":
                urls.append(f"{SITE_URL}/crypto/asset/{ticker}")
            elif asset_class == "us_stocks":
                urls.append(f"{SITE_URL}/stocks/us/asset/{ticker}")
            elif asset_class == "india_stocks":
                urls.append(f"{SITE_URL}/stocks/india/asset/{ticker}")
    except Exception:
        pass

    # MLB/NPB fixtures come from live upstream APIs (ESPN, spaia.jp) --
    # if either is slow or briefly down, that must never take the whole
    # sitemap down with it (a bot crawling this URL doesn't retry the way a
    # browser might), so failures here just mean those dynamic URLs are
    # skipped for this build, not a 500 or a hang.
    try:
        for m in get_mlb_fixtures():
            home_code = ESPN_TO_RETROSHEET.get(m["home_abbr"])
            away_code = ESPN_TO_RETROSHEET.get(m["away_abbr"])
            if home_code and away_code:
                urls.append(
                    f"{SITE_URL}/mlb/match/{home_code}/{away_code}"
                    f"?home_name={quote(m['home_team'])}&away_name={quote(m['away_team'])}"
                )
    except Exception:
        pass

    try:
        npb_elo = load_npb_elo()
        npb_team_ids = set(npb_elo["TeamID"]) if not npb_elo.empty else set()
        for m in get_npb_fixtures():
            if m["home_team_id"] in npb_team_ids and m["away_team_id"] in npb_team_ids:
                urls.append(
                    f"{SITE_URL}/npb/match/{m['home_team_id']}/{m['away_team_id']}"
                    f"?home_name={quote(m['home_team'])}&away_name={quote(m['away_team'])}"
                )
    except Exception:
        pass

    # Cricket match pages -- Cricsheet is a static historical archive, so
    # this can't fail the way a live upstream fetch can, but still wrapped
    # for the same defensive reason as the MLB/NPB blocks above.
    try:
        for slug, competition in CRICKET_COMPETITION_BY_SLUG.items():
            for _, row in cricket_predict.matches_for_competition(competition, limit=10000).iterrows():
                urls.append(f"{SITE_URL}/{slug}/match/{int(row['match_id'])}")
    except Exception:
        pass

    # URLs with query strings (the MLB/NPB match pages) contain literal "&"
    # separating params -- XML requires that escaped as &amp; inside <loc>,
    # otherwise the sitemap is malformed and search engines reject it.
    # lastmod is a freshness signal search engines use to prioritize
    # recrawling -- this site's fixture/prediction data changes daily, so
    # today's date is accurate for every URL (there's no per-page edit
    # timestamp tracked, so a single shared value is the honest choice here).
    today = datetime.date.today().isoformat()
    entries = "".join(
        f"<url><loc>{u.replace('&', '&amp;')}</loc><lastmod>{today}</lastmod></url>" for u in urls
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</urlset>"
    )


@app.route("/sitemap.xml")
def sitemap_xml():
    # Cached to a file (not an in-memory dict) since gunicorn runs multiple
    # worker processes -- an in-memory cache would be per-process, so each
    # worker would independently rebuild from live ESPN/NPB API calls on
    # its own first hit, meaning roughly 1 in N requests would still pay
    # the slow/flaky rebuild cost no matter how long the TTL. A shared file
    # means only the very first request across ALL workers after a restart
    # (or after the TTL expires) pays that cost -- previously every single
    # request rebuilt from those live APIs, so a slow/unavailable upstream
    # could make this route hang or fail even though the rest of the site
    # was fine, which is very plausibly why Search Console reported
    # "Couldn't fetch" on the sitemap despite it being reachable moments
    # later.
    try:
        age = datetime.datetime.now(datetime.timezone.utc).timestamp() - os.path.getmtime(SITEMAP_CACHE_PATH)
        if age <= SITEMAP_CACHE_TTL_SECONDS:
            with open(SITEMAP_CACHE_PATH, "r", encoding="utf-8") as f:
                return Response(f.read(), mimetype="application/xml")
    except OSError:
        pass

    xml = _build_sitemap_xml()
    try:
        with open(SITEMAP_CACHE_PATH, "w", encoding="utf-8") as f:
            f.write(xml)
    except OSError:
        pass
    return Response(xml, mimetype="application/xml")


@app.context_processor
def inject_current_year():
    return {"current_year": datetime.date.today().year}


@app.context_processor
def inject_fixture_picker():
    league_key = (request.view_args or {}).get("league_key", DEFAULT_LEAGUE)
    if league_key not in LEAGUES:
        league_key = DEFAULT_LEAGUE
    df = load_predictions(league_key)
    if df.empty:
        return {"picker_fixtures": [], "current_league_key": league_key, "all_leagues": LEAGUES}
    df = df.sort_values("DateUtc")
    fixtures = [
        {"home": r["HomeTeam"], "away": r["AwayTeam"],
         "label": f"R{r['RoundNumber']}: {r['HomeTeam']} vs {r['AwayTeam']}"}
        for _, r in df.iterrows()
    ]
    return {"picker_fixtures": fixtures, "current_league_key": league_key, "all_leagues": LEAGUES}


SEASON_LABELS = {
    "2122": "2021-22", "2223": "2022-23", "2324": "2023-24",
    "2425": "2024-25", "2526": "2025-26",
}


def summarize_backtest(df):
    if df.empty:
        return {}
    summary = {}
    for sample_type, g in df.groupby("Sample_Type"):
        summary[sample_type] = {
            "n": len(g),
            "result_acc": g["Result_Correct"].mean(),
            "btts_acc": g["BTTS_Correct"].mean(),
            "goals_acc": g["Goals_Correct"].mean(),
            "corners_acc": g["Corners_Winner_Correct"].mean(),
            "favorite_acc": g["FavoriteAvoidsDefeat_Correct"].mean()
                if "FavoriteAvoidsDefeat_Correct" in g.columns else None,
        }
    return summary


def mlb_track_record(n=20):
    """Recomputes the current model's prediction for each of the last `n`
    finished MLB games and compares it to what actually happened. The
    prediction uses TODAY's Elo/stats, not a historical snapshot from before
    that game was played, so this is "how would the current model have
    called it", not a true point-in-time backtest."""
    history = load_mlb_history()
    if history.empty:
        return [], {}

    recent = history.tail(n).iloc[::-1]

    elo_df = load_mlb_elo()
    team_names = set(recent["HomeTeam"]).union(recent["VisitorTeam"])
    full_names = elo_df[elo_df["Team"].isin(team_names)]["TeamName"].astype(str).tolist()
    sentiment.prefetch_team_sentiments(full_names)

    rows = []
    for _, g in recent.iterrows():
        home_code, away_code = g["HomeTeam"], g["VisitorTeam"]
        pred = compute_mlb_prediction(home_code, away_code)
        if pred is None:
            continue
        actual_home_runs, actual_away_runs = float(g["HomeScore"]), float(g["VisitorScore"])
        actual_total_runs = actual_home_runs + actual_away_runs
        actual_winner = home_code if actual_home_runs > actual_away_runs else away_code
        line = pred["total_runs_line"]
        runs_correct = actual_total_runs > line
        home_line = pred["home_runs_line"]
        away_line = pred["away_runs_line"]
        home_runs_correct = actual_home_runs > home_line
        away_runs_correct = actual_away_runs > away_line
        rows.append({
            "date": g["Date"], "home": home_code, "away": away_code,
            "predicted_winner": pred["favourite"], "actual_winner": actual_winner,
            "winner_correct": pred["favourite"] == actual_winner,
            "total_runs_line": line, "actual_total_runs": actual_total_runs, "runs_correct": runs_correct,
            "home_runs_line": home_line, "away_runs_line": away_line,
            "actual_home_runs": actual_home_runs, "actual_away_runs": actual_away_runs,
            "home_runs_correct": home_runs_correct, "away_runs_correct": away_runs_correct,
        })

    summary = {
        "n": len(rows),
        "winner_acc": (sum(r["winner_correct"] for r in rows) / len(rows)) if rows else None,
        "runs_acc": (sum(r["runs_correct"] for r in rows) / len(rows)) if rows else None,
        "team_runs_acc": (sum(r["home_runs_correct"] + r["away_runs_correct"] for r in rows) / (2 * len(rows)))
            if rows else None,
    }
    return rows, summary


def npb_track_record(n=20):
    """Same idea as mlb_track_record() -- see its docstring."""
    history = load_npb_history()
    if history.empty:
        return [], {}

    elo_df = load_npb_elo()
    team_names = dict(zip(elo_df["TeamID"], elo_df["TeamName"])) if not elo_df.empty else {}

    recent = history.tail(n).iloc[::-1]

    recent_team_ids = set(recent["HomeTeamID"]).union(recent["VisitorTeamID"])
    sentiment.prefetch_team_sentiments(
        [team_names[tid] for tid in recent_team_ids if tid in team_names],
    )

    rows = []
    for _, g in recent.iterrows():
        home_id, away_id = int(g["HomeTeamID"]), int(g["VisitorTeamID"])
        pred = compute_npb_prediction(home_id, away_id)
        if pred is None:
            continue
        actual_home_runs, actual_away_runs = float(g["HomeScore"]), float(g["VisitorScore"])
        actual_total_runs = actual_home_runs + actual_away_runs
        actual_winner_name = team_names.get(home_id, home_id) if actual_home_runs > actual_away_runs \
            else team_names.get(away_id, away_id)
        line = pred["total_runs_line"]
        runs_correct = actual_total_runs > line
        home_line = pred["home_runs_line"]
        away_line = pred["away_runs_line"]
        home_runs_correct = actual_home_runs > home_line
        away_runs_correct = actual_away_runs > away_line
        rows.append({
            "date": g["Date"],
            "home": team_names.get(home_id, home_id), "away": team_names.get(away_id, away_id),
            "predicted_winner": pred["favourite_name"], "actual_winner": actual_winner_name,
            "winner_correct": pred["favourite_name"] == actual_winner_name,
            "total_runs_line": line, "actual_total_runs": actual_total_runs, "runs_correct": runs_correct,
            "home_runs_line": home_line, "away_runs_line": away_line,
            "actual_home_runs": actual_home_runs, "actual_away_runs": actual_away_runs,
            "home_runs_correct": home_runs_correct, "away_runs_correct": away_runs_correct,
        })

    summary = {
        "n": len(rows),
        "winner_acc": (sum(r["winner_correct"] for r in rows) / len(rows)) if rows else None,
        "runs_acc": (sum(r["runs_correct"] for r in rows) / len(rows)) if rows else None,
        "team_runs_acc": (sum(r["home_runs_correct"] + r["away_runs_correct"] for r in rows) / (2 * len(rows)))
            if rows else None,
    }
    return rows, summary


IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _to_ist(iso_utc_str):
    """UTC ISO timestamp -> "YYYY-MM-DD HH:MM IST" string, for admins who
    think in IST rather than UTC. Returns "" on anything unparseable
    rather than raising -- a display helper should never break the page."""
    if not iso_utc_str:
        return ""
    try:
        dt = datetime.datetime.fromisoformat(iso_utc_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")
    except (ValueError, TypeError):
        return ""


@app.route("/admin")
@admin_required
def admin_dashboard():
    recent = analytics.recent_visits(limit=100)
    top_visitors = analytics.top_visitors(days=30)
    feedback_list = feedback.list_feedback(limit=200)

    all_ips = [r["ip"] for r in recent] + [v["ip"] for v in top_visitors] + [f["ip"] for f in feedback_list]
    geoip.prefetch_locations(all_ips)

    for r in recent:
        r["ts_ist"] = _to_ist(r["ts"])
        r["location"] = geoip.get_location(r["ip"])
    for v in top_visitors:
        v["first_seen_ist"] = _to_ist(v["first_seen"])
        v["last_seen_ist"] = _to_ist(v["last_seen"])
        v["location"] = geoip.get_location(v["ip"])
    for f in feedback_list:
        f["ts_ist"] = _to_ist(f["ts"])
        f["location"] = geoip.get_location(f["ip"])

    return render_template(
        "admin_dashboard.html",
        summary=analytics.summary(days=30),
        daily=analytics.visits_per_day(days=14),
        top_pages=analytics.top_pages(days=30),
        top_visitors=top_visitors,
        recent=recent,
        feedback_list=feedback_list,
        feedback_count=feedback.count_feedback(),
    )


RESULTS_CACHE_PATH = os.path.join(WEBAPP_DIR, "results_cache.html")
RESULTS_CACHE_TTL_SECONDS = 3 * 60 * 60  # matches degen-refresh-data.timer's 3h cadence --
                                          # no point caching longer than the underlying data changes


def _build_results_page():
    football_summaries = {}
    for key in LEAGUES:
        df = load_backtest(key)
        summary = summarize_backtest(df)
        if summary:
            out_of_sample_key = next((k for k in summary if "Out-of-Sample" in k), None)
            football_summaries[key] = {
                "label": LEAGUES[key]["label"],
                "summary": summary[out_of_sample_key] if out_of_sample_key else next(iter(summary.values())),
            }

    mlb_rows, mlb_summary = mlb_track_record()
    npb_rows, npb_summary = npb_track_record()

    return render_template(
        "results.html",
        football_summaries=football_summaries,
        mlb_rows=mlb_rows, mlb_summary=mlb_summary,
        npb_rows=npb_rows, npb_summary=npb_summary,
        results_generated_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )


@app.route("/results")
@admin_required
def results_page():
    # Cached to a file shared across gunicorn's worker processes (same
    # reasoning as sitemap.xml's cache -- an in-memory cache would be
    # per-process and each worker would independently pay the recompute
    # cost). This page recomputes MLB/NPB predictions for dozens of past
    # games, each of which now also does live news-sentiment lookups
    # (see sentiment.py) -- recomputing that on every single page view
    # was both slow and pointless for numbers that only need to reflect
    # "yesterday's results," not "this exact second."
    force_refresh = request.args.get("refresh") == "1"
    try:
        age = datetime.datetime.now(datetime.timezone.utc).timestamp() - os.path.getmtime(RESULTS_CACHE_PATH)
        if not force_refresh and age <= RESULTS_CACHE_TTL_SECONDS:
            with open(RESULTS_CACHE_PATH, "r", encoding="utf-8") as f:
                return f.read()
    except OSError:
        pass

    html = _build_results_page()
    try:
        with open(RESULTS_CACHE_PATH, "w", encoding="utf-8") as f:
            f.write(html)
    except OSError:
        pass
    return html


@app.route("/<league_key>/backtest")
def backtest(league_key):
    league = get_league_or_404(league_key)
    df = load_backtest(league_key)
    seasons = sorted(df["Season"].unique().tolist(), reverse=True) if not df.empty else []

    season_filter = request.args.get("season", "")
    team_filter = request.args.get("team", "").strip()
    sample_filter = request.args.get("sample", "")
    only_wrong = request.args.get("only_wrong", "")

    overall_summary = summarize_backtest(df)

    filtered = df
    if season_filter:
        filtered = filtered[filtered["Season"].astype(str) == season_filter]
    if team_filter:
        mask = filtered["HomeTeam"].str.contains(team_filter, case=False, na=False) | \
            filtered["AwayTeam"].str.contains(team_filter, case=False, na=False)
        filtered = filtered[mask]
    if sample_filter == "out":
        filtered = filtered[filtered["Sample_Type"].str.contains("Out-of-Sample")]
    elif sample_filter == "in":
        filtered = filtered[filtered["Sample_Type"].str.contains("In-Sample")]
    if only_wrong:
        filtered = filtered[~filtered["Result_Correct"]]

    filtered = filtered.sort_values("Date", ascending=False)
    matches = filtered.to_dict(orient="records")

    return render_template(
        "backtest.html", matches=matches, seasons=seasons, season_labels=SEASON_LABELS,
        selected_season=season_filter, team_filter=team_filter, sample_filter=sample_filter,
        only_wrong=only_wrong, overall_summary=overall_summary, total=len(matches),
        league_key=league_key, league_label=league["label"],
    )


@app.route("/<league_key>/about")
def about(league_key):
    league = get_league_or_404(league_key)
    return render_template(
        "about.html", league_key=league_key, league_label=league["label"],
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
