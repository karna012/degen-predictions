import datetime
import functools
import json
import logging
import os
import re
import secrets
import sys
import threading
import time

from urllib.parse import quote

import joblib
import pandas as pd
from authlib.integrations.flask_client import OAuth
from flask import Flask, Response, abort, flash, jsonify, make_response, redirect, render_template, request, send_file, session, url_for
from flask_login import LoginManager, current_user, login_required, login_user, logout_user

import analytics
import asset_logos
import auth
import combo_results
import feedback
import geoip
import investment_sources
import sentiment
import live_predict
import match_context
import today_picks
import daily_digest
import safer_result
import slip_ocr
import soccer_players
import source_settings
from llms_txt_content import build_llms_txt
from tv_symbols import CRYPTO_TV_SYMBOLS, STOCK_TV_SYMBOLS
from fotmob_lineup import get_live_matchday_info
from live_predict import predict_fixture_live
from mlb_fixtures import get_fixtures as get_mlb_fixtures
from nfl_fixtures import get_fixtures as get_nfl_fixtures
from nfl_predict import (
    compute_nfl_prediction,
    ensure_nfl_ratings,
    load_nfl_elo,
    nfl_track_record,
)
from mlb_predict import (
    blend_with_h2h as mlb_blend_with_h2h,
    blend_with_pitcher as mlb_blend_with_pitcher,
    blend_with_sentiment as mlb_blend_with_sentiment,
    blend_with_streak as mlb_blend_with_streak,
    build_mlb_ml_features,
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
    build_npb_ml_features,
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
import tennis_predict
import tennis_fixtures
import soccer_fixtures
import seo_pages

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEBAPP_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(BASE_DIR, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
from leagues import LEAGUES, correct_fixture_dateutc  # noqa: E402

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

    def clear(self):
        self.cache.clear()


_dataframe_cache = SimpleCache(ttl_seconds=1800)
# Short TTL for assembled pages; combo legs also use a longer on-disk TTL below.
_page_cache = SimpleCache(ttl_seconds=120)


def _refresh_after_events(*, min_interval=60):
    """Settle finished slips in the background. Do not wipe page/live caches
    on every public GET — that forced ESPN/CSV refetches and made the site lag."""
    try:
        stats = combo_results.refresh_for_finished_events(min_interval=min_interval)
        settled = 0
        if stats:
            settled = (
                int(stats.get("legs_settled") or 0)
                + int(stats.get("slips_settled") or 0)
                + int(stats.get("picks_settled") or 0)
            )
        if settled:
            _page_cache.clear()
    except Exception:
        logging.getLogger(__name__).exception("post-event refresh on request failed")

# Combo gather is expensive (~10–15s cold: soccer models + MLB/NPB). Keep a
# long disk cache and serve stale while one worker refreshes in the background.
COMBO_DISK_FRESH_SECONDS = 10 * 60
COMBO_DISK_STALE_SECONDS = 45 * 60
_combo_refresh_lock = threading.Lock()
_combo_refresh_inflight = set()

# today_picks.todays_live_fixtures() walks every sport/league sequentially
# and can take 20-30s cold (each gunicorn worker has its own in-memory
# _page_cache, and workers recycle every ~1000 requests -- without a disk
# cache this 20-30s hit recurred on every worker (re)start, not just once
# per deploy). Same disk-cache-plus-background-refresh shape as the combo
# bundle above, reusing its file-cache helpers with a different cache_key.
LIVE_FIXTURES_DISK_FRESH_SECONDS = 90
LIVE_FIXTURES_DISK_STALE_SECONDS = 20 * 60


def _combo_empty_bundle():
    return ([], {2: [], 3: [], "mixed_2": [], "mixed_3": []})


def _read_combo_disk(cache_key):
    """Return (bundle, age_seconds) or (None, None)."""
    import pickle

    file_path = os.path.join(WEBAPP_DIR, f"{cache_key}.pkl")
    try:
        age = time.time() - os.path.getmtime(file_path)
        with open(file_path, "rb") as f:
            cached = pickle.load(f)
        if cached is None:
            return None, None
        return cached, age
    except FileNotFoundError:
        return None, None
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "Ignoring corrupt combo cache %s: %s", file_path, exc,
        )
        try:
            os.remove(file_path)
        except OSError:
            pass
        return None, None


def _write_combo_disk(cache_key, bundle):
    import pickle
    import tempfile

    file_path = os.path.join(WEBAPP_DIR, f"{cache_key}.pkl")
    directory = os.path.dirname(file_path) or WEBAPP_DIR
    fd, tmp_path = tempfile.mkstemp(prefix=f".{cache_key}.", suffix=".pkl", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            pickle.dump(bundle, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp_path, file_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _combo_file_lock(cache_key):
    """Cross-process lock so 3 gunicorn workers do not rebuild at once."""
    lock_path = os.path.join(WEBAPP_DIR, f".{cache_key}.lock")
    lock_f = open(lock_path, "a+", encoding="utf-8")
    try:
        import fcntl
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
    except ImportError:
        pass
    return lock_f


def _release_combo_file_lock(lock_f):
    if lock_f is None:
        return
    try:
        import fcntl
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
    except ImportError:
        pass
    except Exception:
        pass
    try:
        lock_f.close()
    except Exception:
        pass


def _rebuild_combo_bundle(include_live=True):
    """Build legs + combos (no caching). Expensive — call under a lock."""
    from concurrent.futures import ThreadPoolExecutor

    jobs = []
    for sport_key, sport in today_picks.TODAY_SPORTS.items():
        for league_id, league in sport["leagues"].items():
            kind = league["kind"]
            if not include_live and kind in ("mlb", "npb", "nfl"):
                continue
            jobs.append((sport_key, sport, league_id, league))

    pool_n = today_picks.MAX_PICKS_FOR_COMBOS
    # Gather down to the lowest tier build_best_combos will ever use
    # (COMBO_NEAR_MISS_MIN) so its own floor ladder — 0.70, then 0.68, then
    # near-miss — has real near-threshold legs to fall back on when a thin
    # slate has nothing at 0.68+. build_best_combos still enforces the
    # correct floor per tier; this just widens what is available to choose
    # from, quality-gating happens downstream.
    gather_floor = today_picks.COMBO_NEAR_MISS_MIN

    def _legs_for(job):
        sport_key, sport, league_id, league = job
        kind = league["kind"]
        tz_name = league["tz"]
        try:
            if kind == "soccer":
                _, picks, _ = today_picks.build_soccer_today(
                    league_id, load_predictions(league_id), tz_name,
                    max_n=pool_n, min_conf=gather_floor,
                )
            elif kind == "mlb":
                _, picks, _ = today_picks.normalize_mlb_slate(
                    build_mlb_today_slate(),
                    max_n=pool_n, min_conf=gather_floor,
                )
            elif kind == "nfl":
                _, picks, _ = today_picks.normalize_nfl_slate(
                    build_nfl_today_slate(),
                    max_n=pool_n, min_conf=gather_floor,
                )
            elif kind == "npb":
                def _npb_fixtures_fast(*args, **kwargs):
                    kwargs.setdefault("resolve_pitchers", False)
                    return get_npb_fixtures(*args, **kwargs)
                _, picks, _ = today_picks.build_npb_today(
                    compute_npb_prediction, _npb_fixtures_fast, load_npb_elo, tz_name,
                    max_n=pool_n, min_conf=gather_floor,
                )
            elif kind == "cricket":
                _, picks, _ = today_picks.build_cricket_today(
                    league_id, tz_name,
                    max_n=pool_n, min_conf=gather_floor,
                )
            elif kind == "tennis":
                _, picks, _ = today_picks.build_tennis_today(
                    league_id, tz_name,
                    max_n=pool_n, min_conf=gather_floor,
                )
            else:
                return []
        except Exception:
            return []
        out = []
        for p in picks:
            out.append(today_picks.annotate_leg(
                p,
                sport_key=sport_key,
                sport_label=sport["label"],
                league_id=league_id,
                league_label=league["label"],
                league_color=league.get("color") or sport.get("color") or "",
            ))
        return out

    legs = []
    # Keep concurrency modest — this box is ~2GB RAM with 3 gunicorn workers.
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(jobs)))) as pool:
        for part in pool.map(_legs_for, jobs):
            legs.extend(part)

    combos = today_picks.build_best_combos(
        legs, sizes=(2, 3), top_k=today_picks.MAX_COMBOS_PER_SIZE,
    )
    # Snapshot the published public slate for admin Combo results grading
    # and the single-leg pick ledger used by the EC2 feedback loop.
    # Only on the full (live) rebuild so soft/fast caches do not double-count.
    if include_live:
        try:
            combo_results.snapshot_published_picks(legs)
        except Exception:
            logging.getLogger(__name__).exception("published picks snapshot failed")
        try:
            combo_results.snapshot_published_combos(combos)
        except Exception:
            logging.getLogger(__name__).exception("combo_results snapshot failed")
    return (legs, combos)


ADMIN_FULL_MARKETS_DISK_FRESH_SECONDS = 5 * 60
ADMIN_FULL_MARKETS_DISK_STALE_SECONDS = 30 * 60


def _spawn_admin_full_markets_refresh():
    """Refresh the admin full-markets cache in a daemon thread (single-flight).

    Admin-only, so real site traffic never keeps this warm the way the
    public combo bundle is -- without a disk cache every worker (re)start
    paid the full sequential every-league build (~20-30s) inline on the
    admin's next Telegram-tab click.
    """
    cache_key = "admin_full_markets"
    with _combo_refresh_lock:
        if cache_key in _combo_refresh_inflight:
            return
        _combo_refresh_inflight.add(cache_key)

    def _run():
        try:
            with app.app_context():
                legs = _build_admin_full_market_legs()
                _page_cache.set(cache_key, legs)
                try:
                    _write_combo_disk(cache_key, legs)
                except OSError:
                    pass
        except Exception:
            logging.getLogger(__name__).exception("Background admin full-markets refresh failed")
        finally:
            with _combo_refresh_lock:
                _combo_refresh_inflight.discard(cache_key)

    threading.Thread(
        target=_run, name="admin-full-markets-refresh", daemon=True,
    ).start()


def _gather_admin_full_market_legs():
    """All today's fixtures × all model markets for admin Telegram slip builder.

    Unlike Today's Picks / Best Combos, this does NOT apply high-confidence
    floors or 'one tip per fixture' — every priced market is selectable.
    """
    cache_key = "admin_full_markets"
    cached = _page_cache.get(cache_key)
    if cached is not None:
        return cached

    disk, age = _read_combo_disk(cache_key)
    if disk is not None and age is not None and age <= ADMIN_FULL_MARKETS_DISK_FRESH_SECONDS:
        _page_cache.set(cache_key, disk)
        return disk

    if disk is not None and age is not None and age <= ADMIN_FULL_MARKETS_DISK_STALE_SECONDS:
        _page_cache.set(cache_key, disk)
        _spawn_admin_full_markets_refresh()
        return disk

    legs = _build_admin_full_market_legs()
    _page_cache.set(cache_key, legs)
    try:
        _write_combo_disk(cache_key, legs)
    except OSError:
        pass
    return legs


def _admin_sendable_board_legs():
    """Admin Telegram catalog: drop finished / kicked-off fixtures.

    The full-market cache can still hold legs that finished after it was built
    (status is often left as ``upcoming``). Re-filter at read time with the
    same live-board rules as Today's Picks so Sport → Event never lists finals.
    """
    board_raw = _gather_admin_full_market_legs()
    try:
        fixtures = _cached_live_fixtures()
    except Exception:
        logging.getLogger(__name__).exception(
            "admin telegram live fixtures failed; kickoff filter only",
        )
        fixtures = []
    upcoming = today_picks.filter_upcoming_public_legs(
        board_raw, fixtures=fixtures,
    )
    return [daily_digest._leg_public(leg) for leg in upcoming]


def _build_admin_full_market_legs():
    legs = []

    def _push(pick, *, sport_key, sport, league_id, league):
        legs.append(today_picks.annotate_leg(
            pick,
            sport_key=sport_key,
            sport_label=sport["label"],
            league_id=league_id,
            league_label=league["label"],
            league_color=league.get("color") or sport.get("color") or "",
        ))

    for sport_key, sport in today_picks.TODAY_SPORTS.items():
        for league_id, league in sport["leagues"].items():
            kind = league["kind"]
            tz_name = league["tz"]
            try:
                if kind == "soccer":
                    df = load_predictions(league_id)
                    today = today_picks.today_in_tz(tz_name)
                    if df is None or df.empty:
                        continue
                    for _, row in df.iterrows():
                        kickoff = row.get("DateUtc")
                        if today_picks.local_date(kickoff, tz_name) != today:
                            continue
                        if today_picks.fixture_is_finished(
                            {"kickoff_iso": str(kickoff or ""), "sport_key": "soccer"},
                            sport_key="soccer",
                        ):
                            continue
                        if not today_picks._data_confidence_ok(row):
                            continue
                        home, away = row["HomeTeam"], row["AwayTeam"]
                        url = f"/{league_id}/match/{quote(str(home))}/{quote(str(away))}"
                        when = row.get("MatchDate") or str(kickoff or "")[:16]
                        for tip in today_picks.soccer_admin_market_tips(
                            row, league_key=league_id,
                        ):
                            _push(today_picks._pick(
                                away=away, home=home,
                                matchup=f"{home} vs {away}",
                                kickoff_iso=str(kickoff or ""),
                                when=when, status="upcoming",
                                status_label="Admin market",
                                pick=tip["pick"], market=tip["market"],
                                confidence=float(tip["probability"]),
                                url=url,
                            ), sport_key=sport_key, sport=sport,
                                league_id=league_id, league=league)
                elif kind == "mlb":
                    slate = build_mlb_today_slate()
                    for e in slate.get("board") or []:
                        m = e["fixture"]
                        if m.get("completed") or m.get("status") == "final":
                            continue
                        pred = e.get("prediction")
                        home_name = m.get("home_team", "")
                        away_name = m.get("away_team", "")
                        when = m.get("start_time_et") or m.get("when") or ""
                        kickoff = m.get("start_time_utc") or ""
                        url = "/mlb/today"
                        for tip in today_picks._baseball_market_tips(
                            pred, home_name=home_name, away_name=away_name,
                            include_moneyline=True, include_upset_lean=True,
                        ):
                            _push(today_picks._pick(
                                away=away_name, home=home_name,
                                matchup=f"{away_name} @ {home_name}",
                                kickoff_iso=str(kickoff or ""),
                                when=when, status="upcoming",
                                status_label="Admin market",
                                pick=tip["pick"], market=tip["market"],
                                confidence=float(tip["probability"]),
                                url=url,
                            ), sport_key=sport_key, sport=sport,
                                league_id=league_id, league=league)
                elif kind == "nfl":
                    slate = build_nfl_today_slate()
                    for e in slate.get("board") or []:
                        m = e["fixture"]
                        if m.get("completed") or m.get("status") == "final":
                            continue
                        pred = e.get("prediction")
                        home_name = m.get("home_team", "")
                        away_name = m.get("away_team", "")
                        when = m.get("start_time_et") or m.get("when") or ""
                        kickoff = m.get("start_time_utc") or m.get("date") or ""
                        url = "/nfl/fixtures"
                        for tip in today_picks._nfl_market_tips(
                            pred, home_name=home_name, away_name=away_name,
                            include_moneyline=True, include_upset_lean=True,
                            include_favorite_spread=True,
                        ):
                            _push(today_picks._pick(
                                away=away_name, home=home_name,
                                matchup=f"{away_name} @ {home_name}",
                                kickoff_iso=str(kickoff or ""),
                                when=when, status="upcoming",
                                status_label="Admin market",
                                pick=tip["pick"], market=tip["market"],
                                confidence=float(tip["probability"]),
                                url=url,
                            ), sport_key=sport_key, sport=sport,
                                league_id=league_id, league=league)
                elif kind == "npb":
                    _, picks, _ = today_picks.build_npb_today(
                        compute_npb_prediction, get_npb_fixtures, load_npb_elo,
                        tz_name, max_n=500, min_conf=0.0,
                    )
                    # build_npb still filters by bar; expand tips per fixture below
                    # Prefer raw tip expansion when slate available.
                    for p in picks:
                        _push(p, sport_key=sport_key, sport=sport,
                              league_id=league_id, league=league)
                elif kind == "cricket":
                    _, picks, _ = today_picks.build_cricket_today(
                        league_id, tz_name, max_n=500, min_conf=0.0,
                    )
                    for p in picks:
                        _push(p, sport_key=sport_key, sport=sport,
                              league_id=league_id, league=league)
                elif kind == "tennis":
                    _, picks, _ = today_picks.build_tennis_today(
                        league_id, tz_name, max_n=500, min_conf=0.0,
                    )
                    for p in picks:
                        _push(p, sport_key=sport_key, sport=sport,
                              league_id=league_id, league=league)
            except Exception:
                logging.getLogger(__name__).exception(
                    "admin full-market gather failed for %s/%s", sport_key, league_id,
                )
    return legs


def _spawn_live_fixtures_refresh():
    """Refresh the live-fixtures cache in a daemon thread (single-flight)."""
    cache_key = "live_fixtures"
    with _combo_refresh_lock:
        if cache_key in _combo_refresh_inflight:
            return
        _combo_refresh_inflight.add(cache_key)

    def _run():
        try:
            with app.app_context():
                rows = today_picks.todays_live_fixtures()
                _page_cache.set(cache_key, rows)
                try:
                    _write_combo_disk(cache_key, rows)
                except OSError:
                    pass
        except Exception:
            logging.getLogger(__name__).exception("Background live-fixtures refresh failed")
        finally:
            with _combo_refresh_lock:
                _combo_refresh_inflight.discard(cache_key)

    threading.Thread(
        target=_run, name="live-fixtures-refresh", daemon=True,
    ).start()


def _cached_live_fixtures():
    cache_key = "live_fixtures"
    cached = _page_cache.get(cache_key)
    if cached is not None:
        return cached

    disk, age = _read_combo_disk(cache_key)
    if disk is not None and age is not None and age <= LIVE_FIXTURES_DISK_FRESH_SECONDS:
        _page_cache.set(cache_key, disk)
        return disk

    if disk is not None and age is not None and age <= LIVE_FIXTURES_DISK_STALE_SECONDS:
        # Serve the last known board immediately; refresh out of band so
        # this request (and the next ~90s of requests) never blocks on the
        # full sequential sport/league walk.
        _page_cache.set(cache_key, disk)
        _spawn_live_fixtures_refresh()
        return disk

    # No usable disk cache at all (first run ever, or file wiped) -- only
    # this path pays today_picks.todays_live_fixtures()'s full cold cost
    # inline; every subsequent worker (re)start finds a warm disk file.
    rows = today_picks.todays_live_fixtures()
    _page_cache.set(cache_key, rows)
    try:
        _write_combo_disk(cache_key, rows)
    except OSError:
        pass
    return rows


def _public_upcoming_bundle(legs, combos):
    """Strip finished / kicked-off fixtures from a cached combo bundle."""
    try:
        fixtures = _cached_live_fixtures()
    except Exception:
        logging.getLogger(__name__).exception("live fixture catalog failed")
        fixtures = []
    legs = today_picks.filter_upcoming_public_legs(legs, fixtures=fixtures)
    combos = today_picks.filter_upcoming_public_combos(combos or {}, fixtures=fixtures)
    return legs, combos


def _gather_combo_picks_today(include_live=True, *, allow_blocking_rebuild=True):
    """Collect today's combo legs across sports, then score combos.

    Cached in-memory (~2 min) and on disk (fresh 10 min, stale-serve up to 45 min
    while one worker refreshes). Uses a file lock so workers do not stampede.
    """
    cache_key = "combo_full" if include_live else "combo_fast"
    cached = _page_cache.get(cache_key)
    if cached is not None:
        return _public_upcoming_bundle(*cached)

    disk, age = _read_combo_disk(cache_key)
    if disk is not None and age is not None and age <= COMBO_DISK_FRESH_SECONDS:
        _page_cache.set(cache_key, disk)
        return _public_upcoming_bundle(*disk)

    if disk is not None and age is not None and age <= COMBO_DISK_STALE_SECONDS:
        # Serve yesterday's warm board immediately; refresh out of band.
        _page_cache.set(cache_key, disk)
        _spawn_combo_refresh(include_live)
        return _public_upcoming_bundle(*disk)

    if not allow_blocking_rebuild:
        _spawn_combo_refresh(include_live)
        return _combo_empty_bundle()

    lock_f = None
    try:
        lock_f = _combo_file_lock(cache_key)
        # Another worker may have finished while we waited for the lock.
        cached = _page_cache.get(cache_key)
        if cached is not None:
            return _public_upcoming_bundle(*cached)
        disk, age = _read_combo_disk(cache_key)
        if disk is not None and age is not None and age <= COMBO_DISK_FRESH_SECONDS:
            _page_cache.set(cache_key, disk)
            return _public_upcoming_bundle(*disk)

        out = _rebuild_combo_bundle(include_live=include_live)
        _page_cache.set(cache_key, out)
        try:
            _write_combo_disk(cache_key, out)
        except OSError:
            pass
        return _public_upcoming_bundle(*out)
    finally:
        _release_combo_file_lock(lock_f)


def _spawn_combo_refresh(include_live=True):
    """Refresh combo cache in a daemon thread (single-flight per cache key)."""
    cache_key = "combo_full" if include_live else "combo_fast"
    with _combo_refresh_lock:
        if cache_key in _combo_refresh_inflight:
            return
        _combo_refresh_inflight.add(cache_key)

    def _run():
        lock_f = None
        try:
            with app.app_context():
                lock_f = _combo_file_lock(cache_key)
                disk, age = _read_combo_disk(cache_key)
                if disk is not None and age is not None and age <= COMBO_DISK_FRESH_SECONDS:
                    _page_cache.set(cache_key, disk)
                    return
                out = _rebuild_combo_bundle(include_live=include_live)
                _page_cache.set(cache_key, out)
                try:
                    _write_combo_disk(cache_key, out)
                except OSError:
                    pass
        except Exception:
            logging.getLogger(__name__).exception("Background combo refresh failed")
        finally:
            _release_combo_file_lock(lock_f)
            with _combo_refresh_lock:
                _combo_refresh_inflight.discard(cache_key)

    threading.Thread(
        target=_run, name=f"combo-refresh-{cache_key}", daemon=True,
    ).start()


def cached_dataframe(cache_key):
    """Decorator to cache DataFrame results for 30 minutes.

    cache_key is a base name, not a format string -- callers like
    load_predictions(league_key) previously passed "predictions_{league_key}"
    expecting per-argument caching, but a plain (non-f) string never
    interpolates {league_key}, so every call shared one literal cache slot:
    the first league loaded got cached, and every other league's call
    returned that same cached DataFrame instead of loading its own. Folding
    the actual call args into the key here fixes that generally, for every
    decorated function, without relying on cache_key matching a parameter
    name."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            full_key = (cache_key, args, tuple(sorted(kwargs.items())))
            cached = _dataframe_cache.get(full_key)
            if cached is not None:
                return cached
            result = func(*args, **kwargs)
            _dataframe_cache.set(full_key, result)
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


_static_mtime_cache: dict[str, tuple[int, float]] = {}


def static_version(filename):
    """File mtime as a cache-busting query string. Without this, browsers
    that already cached an old static asset (e.g. under the old 7-day
    Cache-Control header) keep serving it until a hard refresh, since
    server-side header/content changes don't retroactively invalidate what's
    already sitting in the browser's cache -- appending a version tied to the
    actual file's last-modified time forces a fresh fetch the moment the file
    changes, regardless of what the browser has cached."""
    now = time.time()
    hit = _static_mtime_cache.get(filename)
    if hit and now - hit[1] < 30:
        return hit[0]
    path = os.path.join(WEBAPP_DIR, "static", filename)
    try:
        ver = int(os.path.getmtime(path))
    except OSError:
        ver = 0
    _static_mtime_cache[filename] = (ver, now)
    return ver


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
# Session-signing key -- also used for login sessions and CSRF tokens for
# admin POSTs. A real secret is required when ADMIN_USER is set (prod-like).
_SECRET = (os.environ.get("FLASK_SECRET_KEY") or "").strip()
_ADMIN_USER_EARLY = (os.environ.get("ADMIN_USER") or "").strip()
if _ADMIN_USER_EARLY and (not _SECRET or _SECRET == "dev-only-insecure-key"):
    raise RuntimeError(
        "FLASK_SECRET_KEY must be a non-placeholder secret when ADMIN_USER is set"
    )
app.secret_key = _SECRET or "dev-only-insecure-key"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Secure cookies whenever we look like production (admin gate configured).
    SESSION_COOKIE_SECURE=bool(_ADMIN_USER_EARLY) or os.environ.get("SESSION_COOKIE_SECURE") == "1",
    # Admin login must re-prompt after the browser is closed.
    SESSION_PERMANENT=False,
    # /static/ URLs are already cache-busted via static_version()'s ?v=<mtime>
    # query string, so a changed file gets a new URL -- safe to tell browsers
    # to cache the response itself for a long time instead of revalidating
    # on every request.
    SEND_FILE_MAX_AGE_DEFAULT=31536000,
)
app.jinja_env.globals["static_version"] = static_version


@app.route("/healthz")
def healthz():
    """Liveness + feed integrity. Empty/error fetches never wipe last-good data."""
    try:
        import feed_store
        feeds = feed_store.snapshot()
    except Exception:
        feeds = {}
    hard = [name for name, row in feeds.items() if row.get("hard_fail")]
    degraded = [name for name, row in feeds.items() if row.get("degraded")]
    payload = {
        "ok": not hard,
        "degraded": bool(degraded),
        "hard_fail": hard,
        "feeds": feeds,
    }
    # Always 200 if the process can answer — a 503 here would take the
    # site out of rotation while boards still serve last-known-good data.
    return jsonify(payload), 200

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


def get_csrf_token() -> str:
    """Per-session CSRF token for admin POSTs (Basic Auth alone is CSRF-prone)."""
    tok = session.get("_csrf_token")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["_csrf_token"] = tok
    return tok


def validate_csrf_or_abort() -> None:
    expected = session.get("_csrf_token") or ""
    got = (
        (request.form.get("csrf_token") if request.form else None)
        or request.headers.get("X-CSRF-Token")
        or request.headers.get("X-Csrf-Token")
        or ""
    ).strip()
    if not expected or not got or not secrets.compare_digest(got, expected):
        abort(403)


def csrf_protect(view):
    """Require a valid CSRF token on state-changing requests."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            validate_csrf_or_abort()
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_csrf_token():
    # Only mint session CSRF on admin-gated surfaces (avoids cookies for every public hit).
    path = request.path or ""
    if (
        path.startswith("/admin")
        or path.startswith("/results")
        or path.startswith("/todays-picks/digest")
        or path.startswith("/backtest")
        or path.startswith("/player")
    ):
        return {"csrf_token": get_csrf_token()}
    return {"csrf_token": ""}


def _credentials_match(got: str, expected: str) -> bool:
    """Constant-time compare that tolerates unequal lengths."""
    if not isinstance(got, str) or not isinstance(expected, str):
        return False
    if len(got) != len(expected):
        # Still touch compare_digest with equal-length dummies so timing is flatter.
        secrets.compare_digest(expected, expected)
        return False
    return secrets.compare_digest(got, expected)


def _basic_auth_ok() -> bool:
    if not ADMIN_USER or not ADMIN_PASSWORD:
        return False
    auth_header = request.authorization
    if not auth_header:
        return False
    if not _credentials_match(auth_header.username or "", ADMIN_USER):
        return False
    raw_pw = auth_header.password or ""
    try:
        import admin_2fa
        if admin_2fa.is_enabled():
            password, otp = admin_2fa.split_basic_password(raw_pw)
            if not otp:
                otp = (request.headers.get("X-Admin-OTP") or "").strip()
            if not _credentials_match(password, ADMIN_PASSWORD):
                return False
            return admin_2fa.verify_and_consume(otp)
    except Exception:
        logging.getLogger(__name__).exception("admin 2FA basic-auth check failed")
        return False
    return _credentials_match(raw_pw, ADMIN_PASSWORD)


def _admin_session_ok() -> bool:
    if not session.get("admin_authenticated"):
        return False
    try:
        import admin_security_cfg
        cfg = admin_security_cfg.load()
        epoch = int(cfg.get("session_epoch") or 1)
        if "admin_epoch" not in session:
            session["admin_epoch"] = epoch
        elif int(session.get("admin_epoch") or 0) != epoch:
            _clear_admin_session()
            return False
        idle = int(cfg.get("idle_timeout_minutes") or 0)
        if idle > 0:
            last = float(session.get("admin_last_seen") or 0)
            import time as _time
            if last and (_time.time() - last) > idle * 60:
                _clear_admin_session()
                return False
            session["admin_last_seen"] = _time.time()
        else:
            import time as _time
            session["admin_last_seen"] = _time.time()
        sid = session.get("admin_session_id")
        if sid:
            try:
                admin_security_cfg.touch_session(sid, ip=get_client_ip())
            except Exception:
                pass
    except Exception:
        logging.getLogger(__name__).exception("admin session policy check failed")
    return True


def _admin_ip_allowed() -> bool:
    try:
        import admin_security_cfg
        return admin_security_cfg.ip_allowed(get_client_ip())
    except Exception:
        logging.getLogger(__name__).exception("admin IP allowlist check failed")
        return True


def _mark_admin_session(*, actor: str = "admin", role: str = "full") -> None:
    import time as _time
    import admin_roles
    import admin_security_cfg
    try:
        session["admin_epoch"] = int(admin_security_cfg.load().get("session_epoch") or 1)
    except Exception:
        session["admin_epoch"] = 1
    session["admin_last_seen"] = _time.time()
    session["admin_authenticated"] = True
    session["admin_actor"] = (actor or "admin")[:80]
    session["admin_role"] = admin_roles.normalize_role(role)
    sid = session.get("admin_session_id") or admin_roles.new_session_id()
    session["admin_session_id"] = sid
    session.permanent = False
    try:
        admin_security_cfg.register_session(
            session_id=sid,
            actor=session["admin_actor"],
            role=session["admin_role"],
            ip=get_client_ip(),
            ua=(request.headers.get("User-Agent") or "")[:160],
        )
    except Exception:
        logging.getLogger(__name__).exception("register admin session failed")


def _clear_admin_session() -> None:
    sid = session.get("admin_session_id")
    if sid:
        try:
            import admin_security_cfg
            admin_security_cfg.revoke_session(sid)
        except Exception:
            pass
    session.pop("admin_authenticated", None)
    session.pop("admin_2fa_pending", None)
    session.pop("admin_2fa_next", None)
    session.pop("admin_2fa_fails", None)
    session.pop("admin_epoch", None)
    session.pop("admin_last_seen", None)
    session.pop("admin_actor", None)
    session.pop("admin_role", None)
    session.pop("admin_session_id", None)


def _current_admin_role() -> str:
    if _basic_auth_ok():
        return "full"
    try:
        import admin_roles
        return admin_roles.normalize_role(session.get("admin_role") or "full")
    except Exception:
        return "full"


def _admin_has_perm(perm: str) -> bool:
    try:
        import admin_roles
        return admin_roles.has_perm(_current_admin_role(), perm)
    except Exception:
        return True


def admin_perm(perm: str):
    """Require a role permission on top of admin_required."""
    def decorator(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if not _admin_has_perm(perm):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def _is_admin_request() -> bool:
    """True when this request has valid admin Basic Auth or a login session.

    Does not send WWW-Authenticate — use this to hide a page from the public
    without popping a browser login dialog. IP allowlist is checked separately.
    """
    if not ADMIN_USER or not ADMIN_PASSWORD:
        return False
    return _basic_auth_ok() or _admin_session_ok()


def _admin_next_url(raw: str | None = None) -> str:
    """Allow only same-site relative admin destinations (open-redirect safe)."""
    candidate = (raw or request.args.get("next") or request.form.get("next") or "").strip()
    if not candidate.startswith("/") or candidate.startswith("//") or "\\" in candidate:
        return "/admin"
    if candidate.startswith("/admin/login"):
        return "/admin"
    allowed_prefixes = (
        "/admin",
        "/results",
        "/todays-picks/digest",
        "/todays-picks/top3",
        "/todays-picks/combos/results",
        "/backtest",
        "/player",
    )
    # League admin tools: /{league}/backtest and /{league}/players
    if any(candidate.startswith(p) for p in allowed_prefixes):
        return candidate
    if candidate.count("/") >= 2 and (
        candidate.endswith("/backtest") or candidate.endswith("/players")
        or "/backtest?" in candidate or "/players?" in candidate
    ):
        return candidate
    return "/admin"


def _admin_unauthorized_response():
    """Browser GETs go to the login page; API / Basic-Auth clients get 401."""
    if request.authorization is not None:
        return Response(
            "Authentication required.",
            401,
            {"WWW-Authenticate": 'Basic realm="Admin"'},
        )
    if request.method in ("GET", "HEAD"):
        nxt = request.full_path if request.query_string else request.path
        if nxt.endswith("?"):
            nxt = nxt[:-1]
        return redirect(f"/admin/login?next={quote(nxt, safe='/?&=')}")
    return Response(
        "Authentication required.",
        401,
        {"WWW-Authenticate": 'Basic realm="Admin"'},
    )


def admin_required(view):
    """Gates a route behind ADMIN_USER/ADMIN_PASSWORD via login session or
    HTTP Basic Auth (scripts/tests). Browsers without a session are sent to
    /admin/login. If the env vars aren't set, the route fails closed (404)."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not ADMIN_USER or not ADMIN_PASSWORD:
            abort(404)
        if not _admin_ip_allowed():
            abort(403)
        if not _is_admin_request():
            return _admin_unauthorized_response()
        get_csrf_token()
        return view(*args, **kwargs)
    return wrapped


try:
    analytics.init_db()
except Exception:
    logging.getLogger(__name__).exception("analytics.init_db failed (continuing)")
try:
    feedback.init_db()
except Exception:
    logging.getLogger(__name__).exception("feedback.init_db failed (continuing)")


def get_client_ip():
    """The real visitor IP, not nginx's -- nginx (see deploy/nginx config)
    sets X-Real-IP to $remote_addr before proxying to gunicorn, since
    request.remote_addr alone would otherwise just show 127.0.0.1 for every
    visitor. Falls back to remote_addr for local/dev runs without nginx in
    front."""
    return request.headers.get("X-Real-IP") or request.remote_addr or "unknown"


@app.after_request
def log_visit(response):
    """Traffic logging must never turn a successful page into a 500."""
    try:
        path = request.path
        if analytics.is_get_prediction_api_path(path):
            qs = request.query_string.decode("utf-8", "replace")[:300]
            if qs:
                path = f"{path}?{qs}"
        if analytics.should_log(request.path):
            analytics.log_visit(
                ip=get_client_ip(), path=path, method=request.method,
                status=response.status_code, user_agent=request.headers.get("User-Agent"),
                referrer=request.headers.get("Referer"),
            )
    except Exception:
        logging.getLogger(__name__).exception("analytics log_visit failed (ignored)")
    return response


def _fixture_dates_naive(df):
    """Parse DateUtc to timezone-naive UTC timestamps for comparisons."""
    dates = pd.to_datetime(df["DateUtc"])
    if getattr(dates.dt, "tz", None) is not None:
        dates = dates.dt.tz_localize(None)
    return dates


def compute_unlocked_round(df):
    """A round unlocks once every fixture in the previous round has kicked
    off. Starts at the earliest round still present in the predictions file
    (not hardcoded 1) so mid-season feeds -- MLS especially, where finished
    rounds are omitted -- default to the next upcoming slate instead of
    falling through to the last round of the season."""
    if df.empty:
        return 1
    now = pd.Timestamp.now("UTC").tz_localize(None)
    dates = _fixture_dates_naive(df)
    rounds = sorted(df["RoundNumber"].unique().tolist())
    unlocked = rounds[0]
    for r in rounds:
        if dates[df["RoundNumber"] == r].max() < now:
            unlocked = r + 1
        else:
            break
    # Prediction CSVs often skip completed rounds, leaving gaps (e.g. 3 then
    # 20). If unlocked landed in a gap, snap forward to the next present round.
    later = [r for r in rounds if r >= unlocked]
    return later[0] if later else rounds[-1]


@cached_dataframe("predictions")
def load_predictions(league_key):
    path = league_paths(league_key)["pred"]
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    if league_key == "la_liga" and not df.empty:
        df["DateUtc"] = [
            correct_fixture_dateutc(league_key, d, h, a)
            for d, h, a in zip(df["DateUtc"], df["HomeTeam"], df["AwayTeam"])
        ]
    df["MatchDate"] = [
        today_picks.format_kickoff_ist(d) for d in df["DateUtc"].astype(str)
    ]
    return df


@cached_dataframe("backtest")
def load_backtest(league_key):
    path = league_paths(league_key)["backtest"]
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    df["MatchDate"] = pd.to_datetime(df["Date"], dayfirst=True, format="mixed").dt.strftime("%d %b %Y")
    return df


INVESTMENT_RANKINGS_PATH = os.path.join(BASE_DIR, "data", "investment_scoring", "ranked_assets.csv")
STOCK_HORIZONS_MONTHS = [6, 12, 24, 36, 60, 120, 180, 240, 300]
STOCK_HORIZON_LABELS = {
    6: "6 months", 12: "1 year", 24: "2 years", 36: "3 years", 60: "5 years",
    120: "10 years", 180: "15 years", 240: "20 years", 300: "25 years",
}


@cached_dataframe("investment_rankings")
def load_investment_rankings():
    """Output of the separate investment_scoring project (see that repo's
    main.py / train_multi_horizon_stocks.py) -- refreshed by manually
    re-running that pipeline locally and copying output/ranked_assets.csv
    here, same pattern as the MLB/NPB model retrain: never generated live
    on request, never run on EC2."""
    if not os.path.exists(INVESTMENT_RANKINGS_PATH):
        return pd.DataFrame()
    df = pd.read_csv(INVESTMENT_RANKINGS_PATH)
    # Legacy single-horizon files have no horizon_months column -- treat
    # every row as the original 6-month window so the UI still works.
    if "horizon_months" not in df.columns:
        df["horizon_months"] = 6
        df["horizon_label"] = "6 months"
    # Crypto CSV logos are often empty (NaN -> Jinja src="nan"); stock logos
    # still point at dead Clearbit hosts. Resolve both here so cards show
    # CoinGecko / FMP images with monogram fallback only when missing.
    return asset_logos.attach_logo_urls(df)


def available_stock_horizons(df, asset_class):
    if df is None or df.empty:
        return [6]
    sub = df[df["asset_class"] == asset_class] if "asset_class" in df.columns else df
    if sub.empty or "horizon_months" not in sub.columns:
        return [6]
    vals = sorted({int(h) for h in sub["horizon_months"].dropna().unique()})
    # Crypto multi-year boards are mostly historical-drift extrapolations
    # (walk-forward R² junk, near-identical 1000%+ clones). Cap the public
    # chips so the site doesn't advertise fake-looking 5y–25y forecasts.
    if asset_class == "crypto":
        vals = [h for h in vals if h <= CRYPTO_MAX_PUBLIC_HORIZON_MONTHS]
    return vals or [6]


def filter_rankings_by_horizon(df, asset_class, horizon_months):
    if df is None or df.empty:
        return df
    sub = df[df["asset_class"] == asset_class].copy() if "asset_class" in df.columns else df.copy()
    if "horizon_months" in sub.columns:
        matched = sub[sub["horizon_months"].astype(int) == int(horizon_months)]
        if not matched.empty:
            sub = matched
        else:
            # Fall back to nearest available shorter-or-equal horizon.
            available = sorted({int(h) for h in sub["horizon_months"].dropna().unique()})
            pick = max([h for h in available if h <= int(horizon_months)] or available[:1] or [6])
            sub = sub[sub["horizon_months"].astype(int) == pick]
    return sub.sort_values("rank_within_asset_class") if "rank_within_asset_class" in sub.columns else sub


# Stablecoins / cash-like / non-trading junk: absolute return models often
# rank these near the top on long horizons (flat price history + drift
# calibration), which makes the public board look bogus. Hide from UI.
CRYPTO_EXCLUDED_TICKERS = {
    # Fiat stables / cash-like
    "tether", "usd-coin", "usdc", "dai", "paypal-usd", "ripple-usd",
    "first-digital-usd", "ethena-usde", "usds", "global-dollar", "usdy",
    "usyc", "hashnote-usyc", "blackrock-usd-institutional-digital-liquidity-fund",
    "ondo-us-dollar-yield", "usual-usd", "binance-bridged-usdt-bnb-smart-chain",
    "usd1-wlfi", "usdd", "bfusd", "united-stables", "usdgo", "falcon-finance",
    # Tokenized funds / RWA cash products / HELOC wrappers
    "figure-heloc", "spiko-amundi-overnight-swap-fund-eur",
    # Gold pegs (dominate boards without being "crypto bets")
    "pax-gold", "tether-gold",
    # Exchange / custody utility tokens that aren't a liquid L1/L2 bet
    "leo-token",
}
# Long crypto horizons lack independent training windows and collapse to
# near-identical multi-thousand-% forecasts. Public board stops at 3 years.
CRYPTO_MAX_PUBLIC_HORIZON_MONTHS = 36
_CRYPTO_CONF_RANK = {"high": 0, "medium": 1, "low": 2, "unknown": 3}


def _is_crypto_display_excluded(ticker):
    """Stablecoins, RWA cash products, gold pegs, and similar non-bets."""
    t = str(ticker or "").strip().lower()
    if not t or t in CRYPTO_EXCLUDED_TICKERS:
        return True
    if t.endswith("-usd") or t.endswith("-usdc") or t.endswith("-usdt"):
        return True
    if "stable" in t or "heloc" in t or "overnight" in t:
        return True
    if "liquidity-fund" in t or "swap-fund" in t or t.endswith("-gold"):
        return True
    return False


def filter_crypto_display_universe(df):
    """Tighten the public crypto board: drop junk assets and identical
    forecast clones (missing-feature fillna(0) collapses thin coins onto
    one prediction), then dense-rank uniquely so ties don't all show #2.
    """
    if df is None or df.empty or "ticker" not in df.columns:
        return df if df is not None else pd.DataFrame()

    out = df[~df["ticker"].astype(str).map(_is_crypto_display_excluded)].copy()
    if out.empty:
        return out

    # Drop duplicate forecast clones: same calibrated return + outperform
    # prob means the model saw effectively identical feature vectors.
    if "predicted_forward_return" in out.columns:
        ret_key = pd.to_numeric(out["predicted_forward_return"], errors="coerce")
        # Range-only crypto rows intentionally withhold an unsupported point
        # estimate. Use the persisted rank signal only for clone detection.
        if ret_key.notna().sum() == 0 and "ranking_signal" in out.columns:
            ret_key = pd.to_numeric(out["ranking_signal"], errors="coerce")
        ret_key = ret_key.round(6)
        if "predicted_outperform_probability" in out.columns:
            op_key = pd.to_numeric(
                out["predicted_outperform_probability"], errors="coerce"
            ).round(6)
        else:
            op_key = pd.Series(0.0, index=out.index)
        out = out.assign(_clone_key=list(zip(ret_key.fillna(0.0), op_key.fillna(0.0))))
        conf = out["data_confidence"].astype(str).str.lower() if "data_confidence" in out.columns \
            else pd.Series("unknown", index=out.index)
        out = out.assign(
            _conf_rank=conf.map(_CRYPTO_CONF_RANK).fillna(3).astype(int),
            _ticker_l=out["ticker"].astype(str).str.lower(),
        )
        # Prefer higher confidence, then known TradingView-listed majors,
        # then stable alphabetical ticker for determinism.
        try:
            from tv_symbols import CRYPTO_TV_SYMBOLS as _tv
        except ImportError:
            _tv = {}
        out = out.assign(_has_tv=out["_ticker_l"].isin(set(_tv.keys())).astype(int))
        out = out.sort_values(
            ["_clone_key", "_conf_rank", "_has_tv", "_ticker_l"],
            ascending=[True, True, False, True],
        )
        out = out.drop_duplicates(subset=["_clone_key"], keep="first")
        out = out.drop(columns=["_clone_key", "_conf_rank", "_ticker_l", "_has_tv"])

    # Unique dense ranks after filtering (method=first breaks residual ties).
    if "final_score" in out.columns:
        out = out.sort_values(
            ["final_score", "ticker"], ascending=[False, True], kind="mergesort"
        )
        out["rank_within_asset_class"] = range(1, len(out) + 1)
    elif "rank_within_asset_class" in out.columns:
        out = out.sort_values("rank_within_asset_class", kind="mergesort")
        out["rank_within_asset_class"] = range(1, len(out) + 1)
    return out.reset_index(drop=True)


def enrich_rankings_for_display(df):
    """Add annualized + relative-board diagnostics.

    Absolute equity forecasts are usually positive (historical drift). The
    signed ranking the model actually supports is vs the board / benchmark.
    Never mutate ``predicted_forward_return`` — only derive extras.
    """
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame()

    out = df.copy()
    # Relative lean: percentile of ranking_signal within this board (what IC
    # actually validates for crypto). Used when absolute returns are withheld.
    if "ranking_signal" in out.columns and out["ranking_signal"].notna().any():
        sig = pd.to_numeric(out["ranking_signal"], errors="coerce")
        out["relative_lean_pct"] = (sig.rank(pct=True, method="average") * 100.0).round(0)
    else:
        out["relative_lean_pct"] = float("nan")

    if "predicted_forward_return" not in out.columns:
        return out

    ret = pd.to_numeric(out["predicted_forward_return"], errors="coerce")
    if "peer_median_return" not in out.columns or pd.to_numeric(
        out["peer_median_return"], errors="coerce"
    ).isna().all():
        med = float(ret.median()) if ret.notna().any() else float("nan")
        out["peer_median_return"] = med
    if "predicted_excess_return" not in out.columns or pd.to_numeric(
        out["predicted_excess_return"], errors="coerce"
    ).isna().all():
        peer = pd.to_numeric(out["peer_median_return"], errors="coerce")
        out["predicted_excess_return"] = ret - peer

    if "horizon_months" in out.columns and out["horizon_months"].notna().any():
        months = float(pd.to_numeric(out["horizon_months"], errors="coerce").dropna().iloc[0])
    else:
        months = 6.0
    months = max(months, 1.0)
    years = months / 12.0

    def _ann(series):
        # Preserve missing point estimates in range-only mode.
        s = pd.to_numeric(series, errors="coerce").clip(lower=-0.95)
        return (1.0 + s) ** (1.0 / years) - 1.0

    out["annualized_return"] = _ann(ret)
    excess = pd.to_numeric(out["predicted_excess_return"], errors="coerce")
    # Signed headline = vs-board gap when we have a real cross-section.
    if excess.notna().sum() >= 3 and excess.min() != excess.max():
        out["display_edge"] = excess
    else:
        out["display_edge"] = ret
    out["display_edge_annualized"] = out["annualized_return"]
    return out


def stock_board_analysis(df):
    """Live cross-section snapshot so the stock board is not an all-green tape."""
    if df is None or df.empty:
        return None
    ret = pd.to_numeric(df.get("predicted_forward_return"), errors="coerce") if "predicted_forward_return" in df.columns else pd.Series(dtype=float)
    excess = pd.to_numeric(df.get("predicted_excess_return"), errors="coerce") if "predicted_excess_return" in df.columns else pd.Series(dtype=float)
    op = pd.to_numeric(df.get("predicted_outperform_probability"), errors="coerce") if "predicted_outperform_probability" in df.columns else pd.Series(dtype=float)
    n = int(len(df))
    n_pos_abs = int((ret > 0).sum()) if ret.notna().any() else 0
    n_neg_abs = int((ret < 0).sum()) if ret.notna().any() else 0
    n_above = int((excess > 0).sum()) if excess.notna().any() else 0
    n_below = int((excess < 0).sum()) if excess.notna().any() else 0
    n_beat = int((op >= 0.5).sum()) if op.notna().any() else 0
    n_lag = int((op < 0.5).sum()) if op.notna().any() else 0
    peer = None
    if "peer_median_return" in df.columns:
        peer_s = pd.to_numeric(df["peer_median_return"], errors="coerce").dropna()
        if not peer_s.empty:
            peer = float(peer_s.iloc[0])
    return {
        "n": n,
        "n_pos_abs": n_pos_abs,
        "n_neg_abs": n_neg_abs,
        "n_above": n_above,
        "n_below": n_below,
        "n_beat": n_beat,
        "n_lag": n_lag,
        "peer_median": peer,
        "all_absolute_positive": bool(n > 0 and n_pos_abs == n and n_neg_abs == 0 and ret.notna().all()),
    }


def crypto_live_board_stats(df):
    """Counts from the filtered crypto board (complements train-time OOS JSON)."""
    if df is None or df.empty:
        return None
    op = pd.to_numeric(df.get("predicted_outperform_probability"), errors="coerce") if "predicted_outperform_probability" in df.columns else pd.Series(dtype=float)
    down = pd.to_numeric(df.get("predicted_downside_probability"), errors="coerce") if "predicted_downside_probability" in df.columns else pd.Series(dtype=float)
    n = int(len(df))
    return {
        "n": n,
        "n_beat_btc": int((op >= 0.5).sum()) if op.notna().any() else 0,
        "n_lag_btc": int((op < 0.5).sum()) if op.notna().any() else 0,
        "median_downside": float(down.median()) if down.notna().any() else None,
        "median_beat": float(op.median()) if op.notna().any() else None,
    }


def crypto_board_analysis(horizon_months):
    """Honest OOS snapshot for the crypto board banner (from train metrics)."""
    path = os.path.join(BASE_DIR, "data", "investment_scoring", "crypto_horizon_model_metrics.json")
    try:
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
    except Exception:
        return None
    if not isinstance(rows, list):
        return None
    row = next((r for r in rows if int(r.get("horizon_months") or 0) == int(horizon_months)), None)
    if not row:
        return None
    oof = row.get("selected_oof_metrics") or {}
    hist = row.get("historical_target_distribution") or {}
    unc = row.get("selected_oof_uncertainty_metrics") or {}
    ic = oof.get("cross_sectional_ic")
    r2 = oof.get("r2_log")
    sign = oof.get("sign_accuracy")
    hist_neg = hist.get("negative_fraction")
    return {
        "horizon_months": int(horizon_months),
        "model": row.get("selected_model"),
        "forecast_mode": row.get("forecast_mode"),
        "absolute_ok": bool(row.get("absolute_direction_supported")),
        "ic": float(ic) if isinstance(ic, (int, float)) else None,
        "r2_log": float(r2) if isinstance(r2, (int, float)) else None,
        "sign_accuracy": float(sign) if isinstance(sign, (int, float)) else None,
        "hist_negative_frac": float(hist_neg) if isinstance(hist_neg, (int, float)) else None,
        "interval_coverage": unc.get("interval_coverage"),
        "n_oof": oof.get("n"),
    }


def build_forecast_endpoint_chart(asset):
    """Return endpoint-only interval geometry; never imply a monthly path."""
    if not asset:
        return None

    def _number(name):
        value = asset.get(name)
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if value == value else None

    low = _number("prediction_interval_low")
    high = _number("prediction_interval_high")
    scenario = _number("median_scenario_return")
    if low is None or high is None:
        return None
    if low > high:
        low, high = high, low

    axis_low = min(low, 0.0, scenario if scenario is not None else 0.0)
    axis_high = max(high, 0.0, scenario if scenario is not None else 0.0)
    span = axis_high - axis_low
    if span <= 1e-12:
        axis_low -= 0.01
        axis_high += 0.01
        span = axis_high - axis_low
    padding = span * 0.08
    axis_low = max(-1.0, axis_low - padding)
    axis_high += padding
    span = axis_high - axis_low

    def _position(value):
        return max(0.0, min(100.0, (value - axis_low) / span * 100.0))

    low_position = _position(low)
    high_position = _position(high)
    return {
        "low": low,
        "high": high,
        "scenario": scenario,
        "axis_low": axis_low,
        "axis_high": axis_high,
        "low_position": low_position,
        "interval_width": max(high_position - low_position, 0.5),
        "zero_position": _position(0.0),
        "scenario_position": _position(scenario) if scenario is not None else None,
    }


def rankings_records(df):
    """to_dict with NaN -> None so Jinja comparisons work cleanly.

    pandas float columns coerce None back to NaN inside where()/to_dict, and
    Jinja treats float nan as truthy -- which produced <img src=\"nan\"> on
    crypto cards. Sanitize cell-by-cell and normalize logo_url.
    """
    if df is None or df.empty:
        return []
    records = []
    for row in df.to_dict(orient="records"):
        clean = {}
        for key, value in row.items():
            if value is None:
                clean[key] = None
            elif isinstance(value, float) and pd.isna(value):
                clean[key] = None
            else:
                try:
                    if pd.isna(value):
                        clean[key] = None
                        continue
                except (TypeError, ValueError):
                    pass
                clean[key] = value
        clean["logo_url"] = asset_logos.normalize_logo_url(clean.get("logo_url"))
        if clean["logo_url"] is None and clean.get("ticker"):
            ac = str(clean.get("asset_class") or "")
            if ac == "crypto":
                pass  # filled in attach_logo_urls; leave None -> monogram
            elif ac.endswith("stocks"):
                clean["logo_url"] = asset_logos.stock_logo_url(str(clean["ticker"]))
        records.append(clean)
    return records


def _public_accuracy_highlights(limit=6):
    """Compact full-slate OOS chips from real threshold files (never invent)."""
    cached = _page_cache.get("accuracy_highlights")
    if cached is not None:
        return cached[:limit]
    specs = (
        ("atp", "Tennis (ATP)", "/todays-picks/tennis/atp", "/tennis/results", "/sports/tennis"),
        ("cpl", "CPL", "/todays-picks/cricket/cpl", "/cpl/results", "/cpl/fixtures"),
        ("bbl", "BBL", "/todays-picks/cricket/bbl", "/bbl/results", "/bbl/fixtures"),
        ("hundred", "The Hundred", "/todays-picks/cricket/hundred", "/hundred/results", "/hundred/fixtures"),
        ("mlb", "MLB", "/todays-picks/baseball/mlb", "/mlb/results", "/mlb/fixtures"),
        ("ipl", "IPL", "/todays-picks/cricket/ipl", "/ipl/results", "/ipl/fixtures"),
        ("npb", "NPB", "/todays-picks/baseball/npb", "/npb/results", "/sports/baseball"),
        ("nfl", "NFL", "/todays-picks/american_football/nfl", "/nfl/results", "/sports/american-football"),
        ("mlc", "MLC", "/todays-picks/cricket/mlc", "/mlc/results", "/mlc/fixtures"),
    )
    items = []
    for league_id, label, picks_url, track_url, hub_url in specs:
        thr = today_picks.load_sport_thresholds(league_id)
        full = thr.get("full_slate_acc")
        if not isinstance(full, (int, float)):
            continue
        pct_f = float(full)
        items.append({
            "id": league_id,
            "label": label,
            "pct": f"{100 * pct_f:.0f}%",
            "pct_f": pct_f,
            "picks_url": picks_url,
            "track_url": track_url,
            "hub_url": hub_url,
        })
    items.sort(key=lambda row: -row["pct_f"])
    _page_cache.set("accuracy_highlights", items)
    return items[:limit]


def _home_kickoff_iso(raw) -> str:
    text = str(raw or "").strip().replace(" ", "T")
    if not text:
        return ""
    if text.endswith("Z") or "+" in text[10:]:
        return text
    if "T" in text:
        return text + "Z"
    return text


def _home_event_from_parts(*, league, league_id, matchup, kickoff_iso, href, live=False):
    iso = _home_kickoff_iso(kickoff_iso)
    return {
        "league": league or "",
        "league_id": league_id or "",
        "matchup": matchup or "",
        "kickoff_iso": iso,
        "href": href or "/todays-picks",
        "live": bool(live),
    }


def _home_combo_bundle_cached():
    """Read combo cache only — never block the homepage on a rebuild."""
    bundle = _page_cache.get("combo_full")
    if bundle is not None:
        return bundle
    disk, age = _read_combo_disk("combo_full")
    if disk is not None:
        _page_cache.set("combo_full", disk)
        if age is not None and age > COMBO_DISK_FRESH_SECONDS:
            _spawn_combo_refresh(include_live=True)
        return disk
    bundle = _page_cache.get("combo_fast")
    if bundle is not None:
        return bundle
    disk, _age = _read_combo_disk("combo_fast")
    if disk is not None:
        _page_cache.set("combo_fast", disk)
        return disk
    _spawn_combo_refresh(include_live=True)
    return None


def _home_events_from_combo(bundle) -> list[dict]:
    if not bundle:
        return []
    legs = bundle[0] if isinstance(bundle, (list, tuple)) else []
    out = []
    seen = set()
    for leg in legs or []:
        if not isinstance(leg, dict):
            continue
        matchup = (leg.get("matchup") or "").strip()
        if not matchup:
            home = (leg.get("home") or "").strip()
            away = (leg.get("away") or "").strip()
            if home and away:
                matchup = f"{home} vs {away}"
        if not matchup:
            continue
        iso = _home_kickoff_iso(leg.get("kickoff_iso") or leg.get("date") or "")
        key = matchup.lower()
        if key in seen:
            continue
        seen.add(key)
        live = bool(leg.get("is_live") or (leg.get("status") or "").lower() in ("in", "live"))
        league_id = (leg.get("league_id") or "").strip()
        href = (leg.get("url") or "").strip() or _todays_picks_schedule_url(
            (leg.get("sport_key") or "").strip(), league_id,
        )
        out.append(_home_event_from_parts(
            league=leg.get("league_label") or league_id,
            league_id=league_id,
            matchup=matchup,
            kickoff_iso=iso,
            href=href,
            live=live,
        ))
    return out


def _home_events_from_soccer_csv(now_utc) -> list[dict]:
    """Local prediction CSVs only — no live-feed round trip on the homepage."""
    out = []
    for key in ("la_liga", "premier_league", "mls", "serie_a"):
        cfg = LEAGUES.get(key) or {}
        try:
            df = load_predictions(key)
        except Exception:
            continue
        if df is None or getattr(df, "empty", True):
            continue
        rows = df.to_dict("records")
        upcoming, _postponed, _finished = today_picks.split_fixture_board(
            rows, sport_key="soccer", now=now_utc,
        )
        upcoming.sort(key=lambda r: str(r.get("DateUtc") or r.get("date") or ""))
        for row in upcoming[:2]:
            home = (row.get("HomeTeam") or row.get("home") or "").strip()
            away = (row.get("AwayTeam") or row.get("away") or "").strip()
            if not home or not away:
                continue
            iso = _home_kickoff_iso(row.get("DateUtc") or row.get("date") or "")
            live = False
            dt = today_picks.parse_kickoff_utc(iso)
            if dt is not None and dt <= now_utc:
                live = True
            out.append(_home_event_from_parts(
                league=cfg.get("label") or key,
                league_id=key,
                matchup=f"{home} vs {away}",
                kickoff_iso=iso,
                href=f"/{key}/match/{home}/{away}",
                live=live,
            ))
    return out


def _build_home_daily() -> dict:
    """Habit loop for the homepage: date + next kickoff — never picks."""
    cached = _page_cache.get("home_daily")
    if cached is not None:
        return cached
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    now_ist = now_utc.astimezone(ist)
    events = []
    seen = set()
    try:
        bundle = _home_combo_bundle_cached()
        for ev in _home_events_from_combo(bundle):
            key = ev["matchup"].lower()
            if key in seen:
                continue
            seen.add(key)
            events.append(ev)
    except Exception:
        logging.getLogger(__name__).exception("home daily combo events failed")
    if len(events) < 4:
        try:
            for ev in _home_events_from_soccer_csv(now_utc):
                key = ev["matchup"].lower()
                if key in seen:
                    continue
                seen.add(key)
                events.append(ev)
        except Exception:
            logging.getLogger(__name__).exception("home daily soccer events failed")

    def sort_key(ev):
        dt = today_picks.parse_kickoff_utc(ev.get("kickoff_iso") or "")
        live_rank = 0 if ev.get("live") else 1
        ts = dt.timestamp() if dt is not None else float("inf")
        return (live_rank, ts)

    events.sort(key=sort_key)
    live_count = sum(1 for ev in events if ev.get("live"))
    upcoming = events[:4]
    out = {
        "weekday": now_ist.strftime("%A"),
        "date_label": now_ist.strftime("%d %B %Y"),
        "ist_clock": now_ist.strftime("%H:%M IST"),
        "live_count": live_count,
        "next": upcoming[0] if upcoming else None,
        "upcoming": upcoming[1:],
    }
    _page_cache.set("home_daily", out)
    return out


def _feed_stale_for(*prefixes: str):
    try:
        import site_polish
        return site_polish.feed_stale_banner(*prefixes)
    except Exception:
        return None


@app.route("/")
def root_redirect():
    """Homepage must stay fast: never block on a cold combo rebuild."""
    home_now = {}
    try:
        import site_polish
        cached_now = _page_cache.get("home_now")
        if cached_now is None:
            cached_now = site_polish.build_home_now()
            _page_cache.set("home_now", cached_now)
        home_now = cached_now or {}
    except Exception:
        logging.getLogger(__name__).exception("home_now failed")
    resp = make_response(render_template(
        "category_landing.html",
        home_daily=_build_home_daily(),
        home_now=home_now,
        accuracy_highlights=_public_accuracy_highlights(),
    ))
    resp.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=120"
    return resp


@app.route("/sports")
def sports_landing():
    return render_template(
        "sports_landing.html",
        accuracy_highlights=_public_accuracy_highlights(),
    )


def _todays_picks_schedule_url(sport_key, league_id):
    if sport_key == "soccer":
        return f"/{league_id}/"
    if league_id == "mlb":
        return "/mlb/fixtures"
    if league_id == "npb":
        return "/npb/fixtures"
    if league_id in ("hundred", "mlc", "ipl", "bbl", "cpl"):
        return f"/{league_id}/fixtures"
    if league_id == "atp":
        return "/tennis/fixtures"
    if league_id == "nfl":
        return "/nfl/fixtures"
    return "/sports"


@app.route("/about")
def site_about():
    return render_template(
        "about.html",
        league_key="premier_league",
        league_label="all sports on this site",
        site_wide=True,
    )


@app.route("/accuracy")
def accuracy_hub():
    cached = _page_cache.get("accuracy_hub")
    if cached is None:
        cached = _build_accuracy_hub()
        _page_cache.set("accuracy_hub", cached)
    soccer_rows, soccer_aggregate, other_rows = cached
    published_track = None
    try:
        import combo_results
        team = combo_results.degen_team_summary(days=30)
        board = combo_results.summary(days=30)
        if (team.get("decided") or 0) >= 1 or (board.get("decided") or 0) >= 1:
            published_track = {"team": team, "board": board}
    except Exception:
        logging.getLogger(__name__).exception("accuracy published track failed")
    resp = make_response(render_template(
        "accuracy.html",
        soccer_rows=soccer_rows,
        soccer_aggregate=soccer_aggregate,
        other_rows=other_rows,
        accuracy_highlights=_public_accuracy_highlights(limit=8),
        published_track=published_track,
    ))
    resp.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=120"
    return resp


@app.route("/og/share.png")
def og_share_png():
    """Dynamic Open Graph card for picks / combos / markets shares."""
    import site_polish
    title = (request.args.get("title") or "Degen Predictions").strip()[:140]
    subtitle = (request.args.get("sub") or "").strip()[:200]
    kind = (request.args.get("kind") or "site").strip().lower()
    accents = {
        "pick": (45, 212, 191),
        "combo": (251, 191, 36),
        "crypto": (247, 147, 26),
        "stock": (96, 165, 250),
        "site": (45, 212, 191),
    }
    key = site_polish.og_cache_key(title=title, sub=subtitle, kind=kind)
    cache_path = os.path.join(WEBAPP_DIR, "static", "og-cache", f"{key}.png")
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        if not os.path.isfile(cache_path):
            png = site_polish.render_og_card(
                title=title,
                subtitle=subtitle,
                accent=accents.get(kind, accents["site"]),
            )
            with open(cache_path, "wb") as fh:
                fh.write(png)
    except Exception:
        logging.getLogger(__name__).exception("og card render failed")
        return redirect(url_for("static", filename="logo.png"))
    resp = send_file(cache_path, mimetype="image/png", max_age=86400)
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.route("/watchlist")
def watchlist_page():
    import site_polish
    items = site_polish.parse_watch_cookie(request.cookies.get(site_polish.WATCH_COOKIE))
    return render_template("watchlist.html", watch_items=items)


@app.route("/api/watchlist", methods=["GET", "POST"])
def api_watchlist():
    import site_polish
    items = site_polish.parse_watch_cookie(request.cookies.get(site_polish.WATCH_COOKIE))
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        action = (payload.get("action") or "toggle").strip()
        kind = str(payload.get("kind") or "").strip()
        key = str(payload.get("key") or "").strip()
        label = str(payload.get("label") or key).strip()
        href = str(payload.get("href") or "").strip()
        if not (kind and key and href.startswith("/")):
            return jsonify({"ok": False, "error": "bad_item"}), 400
        existing = [x for x in items if not (x["kind"] == kind and x["key"] == key)]
        if action == "remove" or (action == "toggle" and len(existing) < len(items)):
            items = existing
        else:
            items = existing + [{"kind": kind, "key": key, "label": label, "href": href}]
            items = items[-site_polish.WATCH_MAX:]
        body = jsonify({"ok": True, "items": items, "count": len(items)})
        resp = make_response(body)
        resp.set_cookie(
            site_polish.WATCH_COOKIE,
            site_polish.dump_watch_cookie(items),
            max_age=90 * 24 * 3600,
            samesite="Lax",
            path="/",
        )
        return resp
    return jsonify({"ok": True, "items": items, "count": len(items)})


def _fmt_acc(metrics, key):
    d = (metrics or {}).get(key) or {}
    if d.get("skipped"):
        return "n/a"
    val = d.get("best_acc")
    if val is None:
        val = d.get("stacked_acc")
    if val is None:
        return "—"
    try:
        return f"{100 * float(val):.1f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_safer_result(evidence):
    accuracy = (evidence or {}).get("accuracy")
    if accuracy is None:
        return "—"
    n = int(evidence.get("n") or 0)
    coverage = float(evidence.get("coverage") or 0)
    suffix = "" if evidence.get("met_target") else " (below 70%)"
    return f"{100 * float(accuracy):.1f}% · n={n} · {100 * coverage:.1f}% coverage{suffix}"


def _build_accuracy_hub():
    soccer_rows = []
    for key, cfg in LEAGUES.items():
        metrics = {}
        path = os.path.join(BASE_DIR, cfg["model_dir"], "training_metrics.pkl")
        try:
            import pickle
            with open(path, "rb") as f:
                metrics = pickle.load(f)
        except Exception:
            metrics = {}
        soccer_rows.append({
            "key": key,
            "label": cfg["label"],
            "safer": _fmt_safer_result(safer_result.league_evidence(key)),
            "btts": _fmt_acc(metrics, "btts"),
            "over25": _fmt_acc(metrics, "over25"),
            "corners": _fmt_acc(metrics, "home_more_corners"),
        })

    soccer_aggregate = _fmt_safer_result(
        safer_result.load_oos_report().get("aggregate") or {}
    )
    other_rows = []
    for league_id, label, track in (
        ("mlb", "MLB", "/mlb/results"),
        ("npb", "NPB", "/npb/results"),
        ("hundred", "The Hundred", "/hundred/results"),
        ("mlc", "MLC", "/mlc/results"),
        ("ipl", "IPL", "/ipl/results"),
        ("bbl", "BBL", "/bbl/results"),
        ("cpl", "CPL", "/cpl/results"),
        ("atp", "ATP (Men)", "/tennis/results"),
        ("nfl", "NFL", "/nfl/results"),
    ):
        thr = today_picks.load_sport_thresholds(league_id)
        full = thr.get("full_slate_acc")
        full_s = f"{100 * float(full):.1f}%" if isinstance(full, (int, float)) else "—"
        key = "moneyline" if league_id in ("mlb", "npb", "nfl") else "match_winner"
        avail = thr.get("available", {}).get(key)
        bar = thr.get("markets", {}).get(key)
        if thr.get("calibrated") and avail and bar is not None:
            tier_s = f"Yes @ ≥{100 * float(bar):.0f}%"
        elif thr.get("calibrated"):
            tier_s = "No (can't hit 70% OOS honestly)"
        else:
            tier_s = "Not calibrated"
        other_rows.append({
            "label": label,
            "full_slate": full_s,
            "tier": tier_s,
            "track_url": track,
        })
    return soccer_rows, soccer_aggregate, other_rows


@app.route("/todays-picks")
def todays_picks_sports():
    return render_template("today_sports.html", sports=today_picks.TODAY_SPORTS)


def _live_digest_and_legs():
    """Live digest plus the upcoming combo legs used to filter finished games."""
    picks = []
    legs = []
    combos = {}
    try:
        legs, combos = _gather_combo_picks_today(
            include_live=True, allow_blocking_rebuild=False,
        )
        picks = daily_digest.select_top3(legs, n=3)
    except Exception:
        logging.getLogger(__name__).exception("live digest gather failed")
    digest = daily_digest.build_digest_payload(picks, legs=legs, combos=combos)
    digest = daily_digest.filter_digest_upcoming(digest)
    upcoming_legs = [
        leg for leg in (legs or [])
        if isinstance(leg, dict) and not daily_digest._leg_is_finished(leg)
    ]
    if digest.get("picks") or digest.get("featured_combo_count"):
        return digest, upcoming_legs
    stored = daily_digest.load_latest_digest()
    if stored:
        stored = daily_digest.filter_digest_upcoming(stored)
        if stored.get("picks") or stored.get("featured_combo_count"):
            return stored, upcoming_legs
    return digest, upcoming_legs


def _live_digest_payload():
    """Top-3 + share payload from live Today's Picks legs (same board as combos).

    Thin share layer — does not invent picks. Falls back to persisted digest
    JSON when the live gather is empty or unavailable.
    """
    digest, _legs = _live_digest_and_legs()
    return digest


@app.route("/todays-picks/digest")
@admin_required
def todays_picks_digest():
    """Daily Top-3 package — Telegram / admin only (not a public web surface)."""
    if (request.args.get("format") or "").lower() in ("json", "1", "true"):
        return todays_picks_digest_json()
    digest = _live_digest_payload()
    tg = (
        (os.environ.get("TELEGRAM_CHANNEL_URL") or "").strip()
        or digest.get("telegram_channel_url")
    )
    resp = make_response(render_template(
        "digest.html",
        digest=digest,
        picks=digest.get("picks") or [],
        share_text=digest.get("share_text") or "",
        telegram_channel_url=tg,
    ))
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@app.route("/admin/telegram-parse-slip", methods=["POST"])
@admin_required
@csrf_protect
def admin_telegram_parse_slip():
    """Parse a pasted/uploaded bet-slip image in memory only.

    Images are read into RAM, OCR'd, and discarded — never written to disk,
    never stored in the DB, never served back as a file. Only structured
    fields (sport/league/event/market) are returned so the admin can edit
    and Send to Telegram.
    """
    ocr_text = (request.form.get("ocr_text") or request.form.get("text") or "").strip()
    upload = request.files.get("image") or request.files.get("file")
    image_bytes = b""
    filename = ""
    content_type = None
    if upload is not None:
        # Memory only — do not call save() / open a path.
        raw = upload.read() or b""
        max_bytes = 8 * 1024 * 1024
        if len(raw) > max_bytes:
            return jsonify({
                "ok": False,
                "error": "too_large",
                "message": "Image too large (max 8 MB). Paste again or crop the slip.",
                "legs": [],
            }), 400
        image_bytes = raw
        filename = (upload.filename or "").strip()
        content_type = (upload.mimetype or "").strip() or None

    if not image_bytes and not ocr_text:
        return jsonify({
            "ok": False,
            "error": "no_input",
            "message": "Paste a slip image (Ctrl+V) or add bet-details text, then parse.",
            "legs": [],
        }), 400

    try:
        result = slip_ocr.parse_slip_image(
            image_bytes,
            filename=filename,
            content_type=content_type,
            ocr_text=ocr_text or None,
        )
    except Exception:
        logging.getLogger(__name__).exception("telegram-parse-slip failed")
        return jsonify({
            "ok": False,
            "error": "parse_failed",
            "message": "Could not parse the slip. Edit legs manually or try another image.",
            "legs": [],
        }), 500

    # Never echo OCR dump / image back to clients or logs as a stored asset.
    if isinstance(result, dict):
        result.pop("raw_text", None)
        result["stored"] = False
        result["ephemeral"] = True
    # Drop local reference promptly (GC); nothing persisted.
    del image_bytes
    return jsonify(result)


def _admin_channel_targets(form) -> tuple[bool, bool]:
    """Telegram / X checkboxes. Default both when the old form omits them."""
    want_tg = (form.get("channel_telegram") or "").strip().lower() in (
        "1", "on", "true", "yes",
    )
    want_x = (form.get("channel_x") or "").strip().lower() in (
        "1", "on", "true", "yes",
    )
    if not want_tg and not want_x:
        return True, True
    return want_tg, want_x


@app.route("/admin/telegram-send", methods=["POST"])
@app.route("/todays-picks/digest/telegram-send", methods=["POST"])  # legacy alias
@admin_required
@csrf_protect
def admin_telegram_send():
    """Admin send — Today's Picks / combos / custom slips to Telegram and/or X."""
    pick_idxs = request.form.getlist("pick_idx")
    combo_idxs = request.form.getlist("combo_idx")
    action = (request.form.get("action") or "send_custom").strip()
    # Pasted/OCR bet-slip sends only post custom_* legs. Rebuilding every
    # league's full-market board here used to burn the gunicorn 30s timeout
    # so the header text landed in Telegram and sendPhoto never finished.
    needs_board = bool(pick_idxs)
    needs_digest = bool(combo_idxs) or action in (
        "send_combos", "send_both", "send_picks", "send_digest",
    )

    digest = {"date": daily_digest.today_key()}
    if needs_digest or needs_board:
        try:
            live = _live_digest_payload()
            if isinstance(live, dict) and live:
                digest = live
        except Exception:
            logging.getLogger(__name__).exception("telegram-send digest failed")

    board_legs = []
    if needs_board:
        try:
            # Same filtered catalog as the admin UI so pick_idx stays aligned
            # and finished events cannot be posted from a stale form.
            board_legs = _admin_sendable_board_legs()
        except Exception:
            logging.getLogger(__name__).exception("telegram-send full-market gather failed")
            board_legs = []

    selected_picks = []
    for raw in pick_idxs:
        try:
            i = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(board_legs):
            leg = board_legs[i]
            if today_picks.fixture_off_public_board(
                leg, sport_key=leg.get("sport_key") or "",
            ):
                continue
            selected_picks.append(leg)

    # Free-text custom legs (market + selection not on the priced board).
    custom_matchups = request.form.getlist("custom_matchup")
    custom_markets = request.form.getlist("custom_market")
    custom_picks = request.form.getlist("custom_pick")
    custom_sports = request.form.getlist("custom_sport")
    custom_leagues = request.form.getlist("custom_league")
    for i, matchup in enumerate(custom_matchups):
        matchup = (matchup or "").strip()
        pick = (custom_picks[i] if i < len(custom_picks) else "").strip()
        market = (custom_markets[i] if i < len(custom_markets) else "").strip()
        if not matchup or not pick:
            continue
        sport = (custom_sports[i] if i < len(custom_sports) else "").strip()
        league = (custom_leagues[i] if i < len(custom_leagues) else "").strip()
        line = (market or "Custom").strip()
        selected_picks.append({
            "matchup": matchup,
            "pick": pick,
            "market": line,
            "sport_label": sport or "Custom",
            "league_label": league or "",
            "sport_key": "",
            "league_id": "",
            "confidence": None,
            "confidence_pct": None,
            "selection_line": f"{pick} · {line}" if line else pick,
            "when": "",
            "status": "upcoming",
            "url": "",
            "away": "",
            "home": "",
        })

    featured = list(daily_digest.iter_featured_combo_slips(digest))
    selected_combos = []
    for raw in combo_idxs:
        try:
            i = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(featured):
            selected_combos.append(featured[i])

    custom_title = (request.form.get("slip_title") or "").strip()
    slip_mode = (request.form.get("slip_mode") or "multi").strip().lower()
    try:
        slip_n = int(request.form.get("slip_n") or "0")
    except (TypeError, ValueError):
        slip_n = 0

    digest_picks = [
        p for p in (digest.get("picks") or []) if isinstance(p, dict)
    ]
    digest_queue = action in ("send_picks", "send_both", "send_digest") and not pick_idxs
    if digest_queue and not selected_picks:
        selected_picks = digest_picks
    if action == "send_digest":
        action = "send_both"

    if action == "send_combos":
        slips = daily_digest.build_manual_slip_images(combos=selected_combos)
    elif action == "send_both":
        title = custom_title or "Today's Picks"
        slips = daily_digest.build_manual_slip_images(
            picks=selected_picks or None,
            combos=selected_combos or None,
            picks_title=title,
        )
    elif action == "send_picks":
        if not selected_picks:
            return redirect(url_for("admin_dashboard", section="telegram", tg="err_no_picks"))
        if not digest_queue:
            if slip_mode == "single" and len(selected_picks) != 1:
                return redirect(url_for("admin_dashboard", section="telegram", tg="err_slip_count"))
            if slip_mode == "multi" and slip_n >= 2 and len(selected_picks) != slip_n:
                return redirect(url_for("admin_dashboard", section="telegram", tg="err_slip_count"))
        slips = daily_digest.build_manual_slip_images(
            picks=selected_picks,
            picks_title=custom_title or "Today's Picks",
        )
    else:
        # send_custom — one N-leg slip from selected markets (+ optional custom legs)
        if not selected_picks:
            return redirect(url_for("admin_dashboard", section="telegram", tg="err_no_picks"))
        n = len(selected_picks)
        if slip_mode == "single" and n != 1:
            return redirect(url_for("admin_dashboard", section="telegram", tg="err_slip_count"))
        if slip_mode == "multi" and slip_n >= 2 and n != slip_n:
            return redirect(url_for("admin_dashboard", section="telegram", tg="err_slip_count"))
        title = custom_title or (f"{n}-leg slip" if n != 1 else "Single")
        slips = daily_digest.build_manual_slip_images(
            picks=selected_picks,
            picks_title=title,
        )

    day = digest.get("date") if isinstance(digest, dict) else None
    day = day or daily_digest.today_key()

    if not slips:
        logging.getLogger(__name__).warning(
            "admin telegram send: slip PNG render returned empty"
        )
        return redirect(url_for(
            "admin_dashboard", section="telegram", tg="err_render",
        ))

    want_tg, want_x = _admin_channel_targets(request.form)
    notes = []

    # Always snapshot for the X queue, regardless of the checkbox above --
    # this only writes a local JSON file the admin "X / Twitter posts" page
    # reads from, nothing auto-posts from it. Gating this on want_x meant a
    # slip sent with "X" left unticked (easy to do by accident) silently
    # never showed up there, with no error and no way to recover it short of
    # a manual DB-backed rebuild -- happened in production (2026-08-18,
    # "3 Leg Multi , Odds 1.70").
    try:
        daily_digest.save_last_telegram_send(slips, day=day)
        notes.append(f"{len(slips)} slip(s) queued for X")
    except Exception:
        logging.getLogger(__name__).exception("save last telegram send failed")
        notes.append("X queue save failed")

    ok = True
    msg = ""
    if want_tg:
        ok, msg = daily_digest.post_telegram(
            digest, slip_images=slips, rebuild_images=False,
        )
        if msg:
            notes.append(msg)
        if not ok:
            logging.getLogger(__name__).warning("manual telegram send failed: %s", msg)
            return redirect(url_for(
                "admin_dashboard",
                section="telegram",
                tg="err_send",
                tgm=" · ".join(n for n in notes if n)[:240],
            ))
        try:
            combo_results.record_degen_team_from_telegram_slips(
                slips, slate_date=day,
            )
        except Exception:
            logging.getLogger(__name__).exception(
                "record telegram channel slips failed"
            )
            notes.append("ledger save failed")

    section = "twitter" if want_x and not want_tg else "telegram"
    return redirect(url_for(
        "admin_dashboard",
        section=section,
        tg=f"ok_{max(len(slips), 1)}",
        tgm=" · ".join(n for n in notes if n)[:240],
    ))


@app.route("/admin/degen-team/settle", methods=["POST"])
@admin_required
@csrf_protect
def admin_degen_team_settle():
    """Manually grade a Telegram channel slip, then post RESULT to Telegram."""
    kind = (request.form.get("kind") or "").strip().lower()
    status = (request.form.get("status") or "").strip()
    note = (request.form.get("note") or "").strip()
    next_section = (request.form.get("next_section") or "degen_team").strip()
    if next_section not in ("degen_team", "lifecycle"):
        next_section = "degen_team"
    try:
        row_id = int(request.form.get("id") or "0")
    except (TypeError, ValueError):
        row_id = 0
    if not row_id:
        return redirect(url_for("admin_dashboard", section=next_section, dt="err_id"))
    if kind == "combo":
        result = combo_results.manual_settle_combo_slip(row_id, status, note=note)
    elif kind in ("single", "pick"):
        result = combo_results.manual_settle_pick(row_id, status, note=note)
    else:
        return redirect(url_for("admin_dashboard", section=next_section, dt="err_kind"))
    if not result.get("ok"):
        return redirect(url_for(
            "admin_dashboard",
            section=next_section,
            dt=f"err_{result.get('error') or 'settle'}",
        ))
    return redirect(url_for(
        "admin_dashboard",
        section=next_section,
        dt=f"ok_{result.get('status') or status}",
        dtm="graded — send RESULT when ready",
    ))


@app.route("/admin/lifecycle/block", methods=["POST"])
@admin_required
@csrf_protect
def admin_lifecycle_block():
    """Block/unblock a channel slip from RESULT posting."""
    kind = (request.form.get("kind") or "").strip().lower()
    blocked = (request.form.get("blocked") or "1").strip() != "0"
    try:
        row_id = int(request.form.get("id") or "0")
    except (TypeError, ValueError):
        row_id = 0
    result = combo_results.set_result_blocked(kind, row_id, blocked=blocked)
    if not result.get("ok"):
        return redirect(url_for(
            "admin_dashboard",
            section="lifecycle",
            dt=f"err_{result.get('error') or 'block'}",
        ))
    return redirect(url_for(
        "admin_dashboard",
        section="lifecycle",
        dt="ok_blocked" if blocked else "ok_unblocked",
    ))


@app.route("/admin/sources/save", methods=["POST"])
@admin_required
@admin_perm("sources")
@csrf_protect
def admin_sources_save():
    """Persist fixtures/results providers, Telegram, and API keys."""
    try:
        import source_settings
        saved = source_settings.apply_form(request.form)
        when = (saved.get("updated_at") or "").strip()
        return redirect(url_for(
            "admin_dashboard",
            section="sources",
            src="ok",
            srcm=f"saved {when}" if when else "saved",
        ))
    except Exception as exc:
        logging.getLogger(__name__).exception("admin sources save failed")
        return redirect(url_for(
            "admin_dashboard",
            section="sources",
            src="err",
            srcm=str(exc)[:180],
        ))


@app.route("/admin/sources/secrets", methods=["POST"])
@admin_required
@admin_perm("secrets")
@csrf_protect
def admin_sources_secrets():
    """Save full secrets vault (plaintext) + sync production.env."""
    try:
        import source_settings
        saved = source_settings.apply_secrets_form(
            request.form,
            sync_production_env=True,
        )
        try:
            import admin_audit
            admin_audit.log_event("secrets_save", ok=True, ip=get_client_ip(), detail="vault updated")
        except Exception:
            pass
        bits = ["secrets saved"]
        if saved.get("_production_env_written"):
            bits.append("production.env updated")
            bits.append("restart service for Flask/ADMIN_* to fully reload")
        return redirect(url_for(
            "admin_dashboard",
            section="sources",
            src="ok_secrets",
            srcm=" · ".join(bits),
        ))
    except Exception as exc:
        logging.getLogger(__name__).exception("admin secrets save failed")
        return redirect(url_for(
            "admin_dashboard",
            section="sources",
            src="err",
            srcm=str(exc)[:180],
        ))


@app.route("/admin/sources/probe", methods=["POST"])
@admin_required
@csrf_protect
def admin_sources_probe():
    """Force fresh upstream probes for every alternate source."""
    try:
        import source_health
        board = source_health.status_board(force=True)
        summary = board.get("summary") or {}
        try:
            import admin_ops
            admin_ops.maybe_alert_down_sources(force=False)
        except Exception:
            pass
        msg = (
            f"working {summary.get('working', 0)} · "
            f"down {summary.get('down', 0)} · "
            f"needs_key {summary.get('needs_key', 0)}"
        )
        return redirect(url_for(
            "admin_dashboard", section="sources", src="ok_probe", srcm=msg,
        ))
    except Exception as exc:
        logging.getLogger(__name__).exception("admin sources probe failed")
        return redirect(url_for(
            "admin_dashboard", section="sources", src="err", srcm=str(exc)[:180],
        ))


@app.route("/admin/sources/switch", methods=["POST"])
@admin_required
@admin_perm("sources")
@csrf_protect
def admin_sources_switch():
    """One-click set fixtures/results primary or fallback for a sport."""
    try:
        import source_health
        result = source_health.switch_provider(
            request.form.get("sport") or "",
            request.form.get("role") or "",
            request.form.get("provider") or "",
        )
        if not result.get("ok"):
            return redirect(url_for(
                "admin_dashboard",
                section="sources",
                src="err",
                srcm=result.get("error") or "switch failed",
            ))
        msg = (
            f"{result.get('sport')} {result.get('role')} → {result.get('provider')}"
        )
        try:
            import admin_audit
            admin_audit.log_event("source_switch", ok=True, ip=get_client_ip(), detail=msg)
        except Exception:
            pass
        return redirect(url_for(
            "admin_dashboard", section="sources", src="ok_switch", srcm=msg,
        ))
    except Exception as exc:
        logging.getLogger(__name__).exception("admin sources switch failed")
        return redirect(url_for(
            "admin_dashboard", section="sources", src="err", srcm=str(exc)[:180],
        ))


@app.route("/admin/sources/toggle", methods=["POST"])
@admin_required
@admin_perm("sources")
@csrf_protect
def admin_sources_toggle():
    """Enable or disable a sport / markets board from Sources."""
    try:
        import source_settings
        sport = (request.form.get("sport") or "").strip()
        enabled = (request.form.get("enabled") or "") in ("1", "on", "true", "yes")
        result = source_settings.set_feed_enabled(sport, enabled)
        if not result.get("ok"):
            return redirect(url_for(
                "admin_dashboard",
                section="sources",
                src="err",
                srcm=result.get("error") or "toggle failed",
            ))
        msg = f"{sport} {'enabled' if enabled else 'disabled'}"
        try:
            import admin_audit
            admin_audit.log_event("source_toggle", ok=True, ip=get_client_ip(), detail=msg)
        except Exception:
            pass
        return redirect(url_for(
            "admin_dashboard", section="sources", src="ok_toggle", srcm=msg,
        ))
    except Exception as exc:
        logging.getLogger(__name__).exception("admin sources toggle failed")
        return redirect(url_for(
            "admin_dashboard", section="sources", src="err", srcm=str(exc)[:180],
        ))


@app.route("/admin/sources/heal", methods=["POST"])
@admin_required
@admin_perm("sources")
@csrf_protect
def admin_sources_heal():
    """Auto-switch every broken primary feed to a working alternate."""
    try:
        import source_health
        result = source_health.heal_broken_sources(force_probe=True)
        n = int(result.get("count") or 0)
        try:
            import admin_audit
            admin_audit.log_event("source_heal", ok=True, ip=get_client_ip(), detail=f"fixed {n}")
        except Exception:
            pass
        if n:
            bits = [
                f"{row['sport']}: {row['from']} → {row['to']}"
                for row in (result.get("switched") or [])[:4]
            ]
            msg = f"Fixed {n} · " + " · ".join(bits)
        else:
            msg = "Nothing to fix — primaries look healthy (or no working alternate)"
        return redirect(url_for(
            "admin_dashboard", section="sources", src="ok_heal", srcm=msg[:240],
        ))
    except Exception as exc:
        logging.getLogger(__name__).exception("admin sources heal failed")
        return redirect(url_for(
            "admin_dashboard", section="sources", src="err", srcm=str(exc)[:180],
        ))


@app.route("/admin/telegram-results-post", methods=["POST"])
@admin_required
@csrf_protect
def admin_telegram_results_post():
    """Admin-send settled RESULT slips to Telegram and/or X."""
    want_tg, want_x = _admin_channel_targets(request.form)
    only_keys = None
    if (request.form.get("select_mode") or "").strip() == "1":
        only_keys = []
        for raw in request.form.getlist("result_key"):
            text = (raw or "").strip().lower()
            if ":" not in text:
                continue
            kind, _, id_s = text.partition(":")
            if kind not in ("combo", "single"):
                continue
            try:
                only_keys.append((kind, int(id_s)))
            except (TypeError, ValueError):
                continue
    try:
        combo_results.settle_pending()
    except Exception:
        logging.getLogger(__name__).exception("settle before telegram results failed")
    try:
        tg = daily_digest.post_due_telegram_results(
            do_telegram=want_tg, do_twitter=want_x,
            only_keys=only_keys,
        )
        msg = (
            f"checked {tg.get('checked', 0)}, "
            f"telegram {tg.get('posted', 0)}, "
            f"failed {tg.get('failed', 0)}, "
            f"x posted {tg.get('twitter_posted', 0)}, "
            f"x queued {tg.get('twitter_queued', 0)}, "
            f"x failed {tg.get('twitter_failed', 0)}"
        )
        flash = "ok_post"
        section = "twitter" if want_x and not want_tg else "degen_team"
    except Exception as exc:
        logging.getLogger(__name__).exception("telegram results post failed")
        msg = str(exc)[:240]
        flash = "err_post"
        section = "degen_team"
    next_section = (request.form.get("next_section") or "").strip()
    if next_section in ("lifecycle", "degen_team", "twitter"):
        section = next_section
    return redirect(url_for(
        "admin_dashboard", section=section, dt=flash, dtm=msg, tg=flash,
        tgm=msg,
    ))


@app.route("/admin/x-queue.json")
@admin_required
def admin_x_queue_json():
    """JSON for Chrome extension — same slips as the X admin queue."""
    prefer = request.args.get("source") != "digest"
    try:
        digest, board_legs = _live_digest_and_legs()
        queue = daily_digest.twitter_queue(
            digest=digest, prefer_telegram=prefer, board_legs=board_legs,
        )
    except Exception:
        logging.getLogger(__name__).exception("admin x-queue json failed")
        queue = {"date": "", "source": "digest", "posts": [], "count": 0}
    resp = make_response(jsonify(queue))
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@app.route("/admin/twitter-image/<int:index>.png")
@admin_required
def admin_twitter_image(index):
    """Serve the slip PNG for the X queue (admin only)."""
    day = (request.args.get("day") or "").strip() or None
    path = daily_digest.twitter_image_path(index, day=day)
    if path is None:
        abort(404)
    return send_file(
        path,
        mimetype="image/png",
        as_attachment=False,
        download_name=f"degen-x-{index:02d}.png",
        max_age=60,
    )


@app.route("/todays-picks/top3")
@app.route("/todays-picks/digest.json")
@admin_required
def todays_picks_digest_json():
    """JSON twin of Daily Top-3 — admin / internal only."""
    digest = _live_digest_payload()
    resp = make_response(jsonify(digest))
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@app.route("/todays-picks/combos")
def todays_picks_combos():
    """Best 2-leg and 3-leg mixes across all sports for today."""
    legs, combos = _gather_combo_picks_today(include_live=True)
    n2 = len(combos.get(2) or [])
    n3 = len(combos.get(3) or [])
    og_image = (
        "https://degenpredictions.com/og/share.png"
        f"?kind=combo&title={quote('Best Combos')}&sub={quote(f'{n2} two-leg · {n3} three-leg slips')}"
    )
    resp = make_response(render_template(
        "today_combos.html",
        legs=legs,
        combos_2=combos.get(2) or [],
        combos_3=combos.get(3) or [],
        mixed_2=combos.get("mixed_2") or [],
        mixed_3=combos.get("mixed_3") or [],
        max_combos=today_picks.MAX_COMBOS_PER_SIZE,
        min_combos=today_picks.MIN_COMBOS_PER_SIZE,
        max_mixed=today_picks.MAX_MIXED_COMBOS_PER_SIZE,
        page_og_image=og_image,
    ))
    resp.headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=60"
    return resp


@app.route("/todays-picks/combos/results")
def todays_picks_combo_results():
    """Combo results ledger — hidden from the public (admin Basic Auth only)."""
    if not _is_admin_request():
        abort(404)
    status_filter = request.args.get("status") or None
    if status_filter in ("", "all"):
        status_filter = None
    if status_filter not in (None, "pending", "won", "lost", "void"):
        status_filter = None
    try:
        _refresh_after_events(min_interval=60)
        summary = combo_results.summary(days=30)
        slips = combo_results.list_slips(days=30, status=status_filter, limit=80)
    except Exception:
        logging.getLogger(__name__).exception("public combo results failed")
        summary = {
            "days": 30, "won": 0, "lost": 0, "pending": 0, "void": 0,
            "decided": 0, "hit_rate": None, "total": 0,
        }
        slips = []
    resp = make_response(render_template(
        "combo_results_public.html",
        summary=summary,
        slips=slips,
        status_filter=status_filter or "",
    ))
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@app.route("/todays-picks/<sport_key>")
def todays_picks_leagues(sport_key):
    sport = today_picks.get_sport(sport_key)
    if sport is None:
        abort(404)
    return render_template(
        "today_leagues.html",
        sport_key=sport_key,
        sport=sport,
        schedule_urls={
            league_id: _todays_picks_schedule_url(sport_key, league_id)
            for league_id in sport["leagues"]
        },
    )


@app.route("/todays-picks/<sport_key>/<league_id>")
def todays_picks_league(sport_key, league_id):
    sport, league = today_picks.get_league(sport_key, league_id)
    if sport is None or league is None:
        abort(404)

    kind = league["kind"]
    tz_name = league["tz"]
    if kind == "soccer":
        today_local, picks, fixtures_today = today_picks.build_soccer_today(
            league_id, load_predictions(league_id), tz_name,
        )
    elif kind == "mlb":
        today_local, picks, fixtures_today = today_picks.normalize_mlb_slate(
            build_mlb_today_slate(),
        )
    elif kind == "nfl":
        today_local, picks, fixtures_today = today_picks.normalize_nfl_slate(
            build_nfl_today_slate(),
        )
    elif kind == "npb":
        today_local, picks, fixtures_today = today_picks.build_npb_today(
            compute_npb_prediction, get_npb_fixtures, load_npb_elo, tz_name,
        )
    elif kind == "cricket":
        today_local, picks, fixtures_today = today_picks.build_cricket_today(
            league_id, tz_name,
        )
    elif kind == "tennis":
        today_local, picks, fixtures_today = today_picks.build_tennis_today(
            league_id, tz_name,
        )
    else:
        abort(404)

    try:
        picks = today_picks.filter_upcoming_public_legs(
            picks, fixtures=_cached_live_fixtures(),
        )
    except Exception:
        logging.getLogger(__name__).exception("today picks live filter failed")
        picks = today_picks.filter_upcoming_public_legs(picks, fixtures=[])

    slate_correlation = today_picks.analyze_slate_correlation(picks)

    n_picks = len(picks or [])
    thr = today_picks.load_sport_thresholds(league_id)
    full = thr.get("full_slate_acc")
    acc_bit = (
        f" · {100 * float(full):.0f}% full-slate OOS"
        if isinstance(full, (int, float)) else ""
    )
    if n_picks:
        share_headline = (
            f"Degen Predictions — Today's Picks ({league['label']}): "
            f"{n_picks} high-probability pick{'s' if n_picks != 1 else ''}"
            f"{acc_bit} · {today_local}"
        )
    elif fixtures_today:
        share_headline = (
            f"Degen Predictions — Today's Picks ({league['label']}): "
            f"no high-probability picks cleared the bar today"
            f"{acc_bit}"
        )
    else:
        share_headline = (
            f"Degen Predictions — Today's Picks ({league['label']}): "
            f"no match today · check back on a matchday{acc_bit}"
        )

    og_title = f"{league['label']} Today's Picks"
    og_sub = share_headline[:180]
    og_image = (
        f"https://degenpredictions.com/og/share.png"
        f"?kind=pick&title={quote(og_title)}&sub={quote(og_sub)}"
    )
    return render_template(
        "today_picks.html",
        sport_key=sport_key,
        sport=sport,
        league_id=league_id,
        league=league,
        today_local=today_local,
        picks=picks,
        slate_correlation=slate_correlation,
        fixtures_today=fixtures_today,
        max_picks=today_picks.MAX_PICKS_PER_LEAGUE,
        has_live=any(p.get("status") == "live" for p in picks),
        schedule_url=_todays_picks_schedule_url(sport_key, league_id),
        share_headline=share_headline,
        og_accuracy_bit=acc_bit,
        pick_count=n_picks,
        page_og_image=og_image,
    )


@app.route("/sports/soccer")
def soccer_leagues():
    leagues_info = []
    for key, cfg in LEAGUES.items():
        df = load_predictions(key)
        display = LEAGUE_DISPLAY.get(key, {})
        rows = df.to_dict(orient="records") if df is not None and not df.empty else []
        upcoming, _postponed, _finished = today_picks.split_fixture_board(
            rows, sport_key="soccer",
        )
        leagues_info.append({
            "key": key, "label": cfg["label"],
            "fixture_count": len(upcoming),
            "available": bool(upcoming) or not df.empty,
            **display,
        })
    return render_template("soccer_leagues.html", leagues_info=leagues_info)


@app.route("/sports/baseball")
def baseball_leagues():
    cached = _page_cache.get("baseball_leagues_page")
    if cached is not None:
        return render_template("baseball_leagues.html", **cached)

    mlb_elo = load_mlb_elo()
    mlb_upcoming = sum(1 for m in get_mlb_fixtures() if not m["completed"])
    mlb_info = {
        "key": "mlb", "label": "MLB", "monogram": "MLB", "color": "#041e42", "country": "USA",
        "fixture_count": mlb_upcoming, "available": not mlb_elo.empty,
    }

    npb_elo = load_npb_elo()
    # Counts only — skip cold pitcher-name HTTP lookups that made this page multi-second.
    npb_upcoming = sum(
        1 for m in get_npb_fixtures(resolve_pitchers=False) if not m["completed"]
    )
    npb_info = {
        "key": "npb", "label": "NPB", "monogram": "NPB", "color": "#bc002d", "country": "Japan",
        "fixture_count": npb_upcoming, "available": not npb_elo.empty,
    }
    ctx = {"mlb_info": mlb_info, "npb_info": npb_info}
    _page_cache.set("baseball_leagues_page", ctx)
    resp = make_response(render_template("baseball_leagues.html", **ctx))
    resp.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=120"
    return resp


@app.route("/sports/american-football")
def american_football_leagues():
    cached = _page_cache.get("american_football_leagues_page")
    if cached is not None:
        return render_template("american_football_leagues.html", **cached)

    ensure_nfl_ratings()
    nfl_elo = load_nfl_elo()
    nfl_upcoming = sum(1 for m in get_nfl_fixtures() if not m["completed"])
    nfl_info = {
        "key": "nfl", "label": "NFL", "monogram": "NFL", "color": "#013369", "country": "USA",
        "fixture_count": nfl_upcoming, "available": True,
    }
    ctx = {"nfl_info": nfl_info, "nfl_has_ratings": not nfl_elo.empty}
    _page_cache.set("american_football_leagues_page", ctx)
    resp = make_response(render_template("american_football_leagues.html", **ctx))
    resp.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=120"
    return resp


@app.route("/sports/cricket")
def cricket_leagues():
    cricket_elo = cricket_predict.load_elo()
    del cricket_elo  # loaded to warm cache; counts come from dataset
    def _info(key, label, monogram, color, country, competition):
        matches = cricket_predict.matches_for_competition(competition, limit=10000)
        thr = today_picks.load_sport_thresholds(key)
        full = thr.get("full_slate_acc")
        full_s = f"{100 * float(full):.0f}%" if isinstance(full, (int, float)) else None
        avail = thr.get("available", {}).get("match_winner")
        bar = thr.get("markets", {}).get("match_winner")
        if thr.get("calibrated") and avail and bar is not None:
            tier_s = f"70% tier @ ≥{100 * float(bar):.0f}%"
            tier_ok = True
        elif thr.get("calibrated"):
            tier_s = "No 70% winner tier"
            tier_ok = False
        else:
            tier_s = None
            tier_ok = None
        return {
            "key": key, "label": label, "monogram": monogram, "color": color, "country": country,
            "fixture_count": len(matches), "available": len(matches) > 0,
            "full_slate_acc": full_s,
            "tier_label": tier_s,
            "tier_ok": tier_ok,
        }
    leagues = [
        _info("ipl", "IPL", "IPL", "#0033a0", "India", "ipl_male"),
        _info("bbl", "BBL", "BBL", "#00a3e0", "Australia", "bbl_male"),
        _info("cpl", "CPL", "CPL", "#00a651", "Caribbean", "cpl_male"),
        _info("hundred", "The Hundred", "HND", "#7b2ff7", "England", "hnd_male"),
        _info("mlc", "MLC", "MLC", "#d4a017", "USA", "mlc_male"),
    ]
    return render_template("cricket_leagues.html", cricket_leagues=leagues)


CRICKET_COMPETITION_BY_SLUG = {
    "hundred": "hnd_male",
    "mlc": "mlc_male",
    "ipl": "ipl_male",
    "bbl": "bbl_male",
    "cpl": "cpl_male",
}


def _cricket_fixtures_page(league_slug):
    """Upcoming/live fixtures when a schedule source exists; otherwise an
    honest empty state. Completed matches live on /results."""
    competition = CRICKET_COMPETITION_BY_SLUG[league_slug]
    fixtures = cricket_predict.upcoming_fixtures(competition)
    fixtures = [
        f for f in fixtures
        if not f.get("completed")
        and today_picks.fixture_is_upcoming_or_live(f, sport_key="cricket")
    ]
    label = cricket_predict.COMPETITION_LABELS.get(competition, competition)
    season = cricket_predict.season_window(competition)
    source_note = {
        "hundred": "Schedule from Wikipedia’s season article.",
        "mlc": "No live schedule feed yet — check results for completed matches.",
        "ipl": "Schedule from fixturedownload.com (IPL season).",
        "bbl": "Schedule from fixturedownload.com (BBL season).",
        "cpl": "Schedule from Wikipedia’s season article.",
    }.get(league_slug, "")
    return render_template(
        "cricket_fixtures.html", fixtures=fixtures, competition_label=label,
        league_slug=league_slug, season_window=season, source_note=source_note,
        full_slate_acc=_full_slate_acc_label(league_slug),
    )


def _cricket_results_page(league_slug):
    _refresh_after_events(min_interval=60)
    competition = CRICKET_COMPETITION_BY_SLUG[league_slug]
    matches_df = cricket_predict.matches_for_competition(competition, limit=30)
    # List view: skip rich analysis panels (detail page loads those).
    archive = [cricket_predict.build_match_view(row, rich=False) for _, row in matches_df.iterrows()]
    live = cricket_predict.live_completed_result_views(competition, limit=40)
    matches = cricket_predict.merge_competition_result_views(archive, live, limit=40)
    label = cricket_predict.COMPETITION_LABELS.get(competition, competition)
    return render_template(
        "cricket_results.html", matches=matches, competition_label=label, league_slug=league_slug,
        full_slate_acc=_full_slate_acc_label(league_slug),
    )


def _cricket_match_detail(league_slug, match_id):
    competition = CRICKET_COMPETITION_BY_SLUG[league_slug]
    row = cricket_predict.get_match_row(match_id)
    if row is None or row["Competition"] != competition:
        abort(404)
    match = cricket_predict.build_match_view(row, rich=True)
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
    detail = cricket_predict.build_fixture_detail(fixture, competition="hnd_male")
    return render_template("cricket_fixture_detail.html", match=detail, league_slug="hundred")


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


@app.route("/ipl/")
def ipl_home():
    return redirect("/ipl/fixtures")


@app.route("/ipl/fixtures")
def ipl_fixtures_page():
    return _cricket_fixtures_page("ipl")


@app.route("/ipl/results")
def ipl_results_page():
    return _cricket_results_page("ipl")


@app.route("/ipl/match/<int:match_id>")
def ipl_match_detail(match_id):
    return _cricket_match_detail("ipl", match_id)


@app.route("/ipl/fixture/<slug>")
def ipl_fixture_detail(slug):
    import cricket_fd_fixtures
    fixture = cricket_fd_fixtures.get_fixture_by_slug("ipl", slug)
    if fixture is None:
        abort(404)
    fixture = {
        **fixture,
        "team1_elo_name": cricket_predict._canonical(fixture["team1"]),
        "team2_elo_name": cricket_predict._canonical(fixture["team2"]),
    }
    detail = cricket_predict.build_fixture_detail(fixture, competition="ipl_male")
    return render_template("cricket_fixture_detail.html", match=detail, league_slug="ipl")


@app.route("/bbl/")
def bbl_home():
    return redirect("/bbl/fixtures")


@app.route("/bbl/fixtures")
def bbl_fixtures_page():
    return _cricket_fixtures_page("bbl")


@app.route("/bbl/results")
def bbl_results_page():
    return _cricket_results_page("bbl")


@app.route("/bbl/match/<int:match_id>")
def bbl_match_detail(match_id):
    return _cricket_match_detail("bbl", match_id)


@app.route("/bbl/fixture/<slug>")
def bbl_fixture_detail(slug):
    import cricket_fd_fixtures
    fixture = cricket_fd_fixtures.get_fixture_by_slug("bbl", slug)
    if fixture is None:
        abort(404)
    fixture = {
        **fixture,
        "team1_elo_name": cricket_predict._canonical(fixture["team1"]),
        "team2_elo_name": cricket_predict._canonical(fixture["team2"]),
    }
    detail = cricket_predict.build_fixture_detail(fixture, competition="bbl_male")
    return render_template("cricket_fixture_detail.html", match=detail, league_slug="bbl")


@app.route("/cpl/")
def cpl_home():
    return redirect("/cpl/fixtures")


@app.route("/cpl/fixtures")
def cpl_fixtures_page():
    return _cricket_fixtures_page("cpl")


@app.route("/cpl/results")
def cpl_results_page():
    return _cricket_results_page("cpl")


@app.route("/cpl/match/<int:match_id>")
def cpl_match_detail(match_id):
    return _cricket_match_detail("cpl", match_id)


@app.route("/cpl/fixture/<slug>")
def cpl_fixture_detail(slug):
    import cpl_fixtures
    fixture = cpl_fixtures.get_fixture_by_slug(slug)
    if fixture is None:
        abort(404)
    fixture = {
        **fixture,
        "team1_elo_name": cricket_predict._canonical(fixture.get("team1_elo_name") or fixture["team1"]),
        "team2_elo_name": cricket_predict._canonical(fixture.get("team2_elo_name") or fixture["team2"]),
    }
    detail = cricket_predict.build_fixture_detail(fixture, competition="cpl_male")
    return render_template("cricket_fixture_detail.html", match=detail, league_slug="cpl")


@app.route("/sports/tennis")
def tennis_hub():
    elo = tennis_predict.load_elo()
    fixtures = tennis_predict.upcoming_with_predictions()
    thr = today_picks.load_sport_thresholds("atp")
    full = thr.get("full_slate_acc")
    full_s = f"{100 * float(full):.0f}%" if isinstance(full, (int, float)) else None
    return render_template(
        "tennis_hub.html",
        fixture_count=len(fixtures),
        player_count=0 if elo.empty else len(elo),
        full_slate_acc=full_s,
    )


@app.route("/tennis/")
def tennis_home():
    return redirect("/tennis/fixtures")


@app.route("/tennis/fixtures")
def tennis_fixtures_page():
    fixtures = tennis_predict.upcoming_with_predictions()
    fixtures = [
        f for f in fixtures
        if not f.get("completed")
        and today_picks.fixture_is_upcoming_or_live(f, sport_key="tennis")
    ]
    surface = (request.args.get("surface") or "").strip().title()
    if surface in ("Hard", "Clay", "Grass"):
        fixtures = [
            f for f in fixtures
            if (f.get("surface") or (f.get("prediction") or {}).get("surface")) == surface
        ]
    surfaces = sorted({
        (f.get("surface") or (f.get("prediction") or {}).get("surface"))
        for f in tennis_predict.upcoming_with_predictions()
        if (f.get("surface") or (f.get("prediction") or {}).get("surface"))
    })
    return render_template(
        "tennis_fixtures.html", fixtures=fixtures, surfaces=surfaces,
        full_slate_acc=_full_slate_acc_label("atp"),
    )


@app.route("/tennis/results")
def tennis_results_page():
    _refresh_after_events(min_interval=60)
    results = tennis_predict.recent_results(limit=40)
    return render_template(
        "tennis_results.html", results=results,
        full_slate_acc=_full_slate_acc_label("atp"),
    )


@app.route("/tennis/fixture/<slug>")
def tennis_fixture_detail(slug):
    fixture = tennis_fixtures.get_fixture_by_slug(slug)
    if fixture is None:
        abort(404)
    detail = tennis_predict.build_match_detail(fixture)
    return render_template("tennis_fixture_detail.html", match=detail)


@app.route("/crypto")
def crypto_rankings():
    import investment_sources
    if not investment_sources.markets_enabled("crypto"):
        return Response(
            "Crypto board is disabled in Admin → Sources.",
            503,
            {"Content-Type": "text/plain; charset=utf-8"},
        )
    df = load_investment_rankings()
    available = available_stock_horizons(df, "crypto")
    raw_horizon = request.args.get("horizon", str(available[0]))
    try:
        horizon = int(raw_horizon)
    except (TypeError, ValueError):
        horizon = available[0]
    if horizon not in available:
        horizon = available[0]

    crypto_df = filter_rankings_by_horizon(df, "crypto", horizon) if not df.empty else df
    crypto_df = filter_crypto_display_universe(crypto_df)

    horizon_label = STOCK_HORIZON_LABELS.get(horizon, f"{horizon} months")
    model_name = model_ic = model_r2 = None
    if not crypto_df.empty:
        row0 = crypto_df.iloc[0]
        if "horizon_label" in crypto_df.columns and pd.notna(row0.get("horizon_label")):
            horizon_label = row0.get("horizon_label")
        if "horizon_months" in crypto_df.columns and pd.notna(row0.get("horizon_months")):
            horizon = int(row0["horizon_months"])
        raw_model = row0.get("selected_model")
        if raw_model is not None and not (isinstance(raw_model, float) and pd.isna(raw_model)):
            model_name = str(raw_model)
        model_ic = row0.get("model_ic")
        model_r2 = row0.get("model_r2")
        if isinstance(model_ic, float) and pd.isna(model_ic):
            model_ic = None
        if isinstance(model_r2, float) and pd.isna(model_r2):
            model_r2 = None

    crypto_df = enrich_rankings_for_display(crypto_df)
    peer_s = pd.to_numeric(crypto_df["peer_median_return"], errors="coerce") if not crypto_df.empty and "peer_median_return" in crypto_df.columns else pd.Series(dtype=float)
    peer_median = float(peer_s.dropna().iloc[0]) if not peer_s.dropna().empty else None
    board_as_of = board_hold_until = None
    if not crypto_df.empty and "as_of_date" in crypto_df.columns:
        # Prefer the freshest coin date for the board banner -- rank #1 can
        # be a thinly traded name whose Yahoo series ended early.
        dated = crypto_df.dropna(subset=["as_of_date"]).copy()
        if not dated.empty:
            dated["_as_of"] = pd.to_datetime(dated["as_of_date"], errors="coerce")
            fresh = dated.loc[dated["_as_of"].idxmax()]
            board_as_of = fresh.get("as_of_date")
            board_hold_until = fresh.get("hold_until_date")

    # Only embed TV symbols for tickers actually shown (avoids leaking
    # excluded stables/RWA ids into the page JSON).
    shown = set(crypto_df["ticker"].astype(str)) if not crypto_df.empty else set()
    tv_map = {k: v for k, v in CRYPTO_TV_SYMBOLS.items() if k in shown}
    board_analysis = crypto_board_analysis(horizon)

    return render_template(
        "stocks_rankings.html",
        rows=rankings_records(crypto_df),
        category="crypto",
        category_label="Crypto",
        market="crypto",
        back_href="/",
        tv_symbol_map=tv_map,
        horizons=available,
        horizon_labels=STOCK_HORIZON_LABELS,
        selected_horizon=horizon,
        horizon_label=horizon_label,
        model_name=model_name,
        model_ic=model_ic,
        model_r2=model_r2,
        peer_median=peer_median,
        board_as_of=board_as_of,
        board_hold_until=board_hold_until,
        board_analysis=board_analysis,
        live_board_stats=crypto_live_board_stats(crypto_df),
        stock_analysis=None,
    )


@app.route("/crypto/asset/<ticker>")
def crypto_asset_detail(ticker):
    import investment_sources
    if not investment_sources.markets_enabled("crypto"):
        return Response(
            "Crypto board is disabled in Admin → Sources.",
            503,
            {"Content-Type": "text/plain; charset=utf-8"},
        )
    df = load_investment_rankings()
    available = available_stock_horizons(df, "crypto")
    raw_horizon = request.args.get("horizon", str(available[0]))
    try:
        horizon = int(raw_horizon)
    except (TypeError, ValueError):
        horizon = available[0]
    class_df = filter_rankings_by_horizon(df, "crypto", horizon) if not df.empty else df
    class_df = filter_crypto_display_universe(class_df)
    class_df = enrich_rankings_for_display(class_df)
    row = class_df[class_df["ticker"] == ticker]
    if row.empty:
        return render_template("not_found.html", home=ticker, away=""), 404
    asset = rankings_records(row)[0]
    horizon = int(asset.get("horizon_months") or horizon)
    tv_symbol = CRYPTO_TV_SYMBOLS.get(ticker)
    endpoint_chart = build_forecast_endpoint_chart(asset)
    return render_template(
        "investment_asset_detail.html",
        asset=asset,
        category="crypto",
        category_label="Crypto",
        market="crypto",
        back_href=f"/crypto?horizon={horizon}",
        total_in_class=len(class_df),
        horizon_months=horizon,
        horizon_label=asset.get("horizon_label") or STOCK_HORIZON_LABELS.get(horizon, f"{horizon} months"),
        tv_symbol=tv_symbol,
        endpoint_chart=endpoint_chart,
    )


@app.route("/stocks")
def stocks_landing():
    import investment_sources
    if not investment_sources.markets_enabled("stocks"):
        return Response(
            "Stocks boards are disabled in Admin → Sources.",
            503,
            {"Content-Type": "text/plain; charset=utf-8"},
        )
    return render_template(
        "stocks_landing.html",
        horizons=STOCK_HORIZONS_MONTHS,
        horizon_labels=STOCK_HORIZON_LABELS,
    )


@app.route("/stocks/<market>")
def stocks_market_rankings(market):
    if market not in ("us", "india"):
        abort(404)
    import investment_sources
    if not investment_sources.markets_enabled("stocks"):
        return Response(
            "Stocks boards are disabled in Admin → Sources.",
            503,
            {"Content-Type": "text/plain; charset=utf-8"},
        )
    asset_class = f"{market}_stocks"
    df = load_investment_rankings()
    available = available_stock_horizons(df, asset_class)
    raw_horizon = request.args.get("horizon", str(available[0]))
    try:
        horizon = int(raw_horizon)
    except (TypeError, ValueError):
        horizon = available[0]
    if horizon not in available:
        horizon = available[0]

    market_df = filter_rankings_by_horizon(df, asset_class, horizon)
    label = "US Stocks" if market == "us" else "India Stocks"
    model_name = None
    model_ic = None
    model_r2 = None
    horizon_label = STOCK_HORIZON_LABELS.get(horizon, f"{horizon} months")
    if not market_df.empty:
        row0 = market_df.iloc[0]
        model_name = row0.get("selected_model")
        model_ic = row0.get("model_ic")
        model_r2 = row0.get("model_r2")
        if "horizon_label" in market_df.columns and pd.notna(row0.get("horizon_label")):
            horizon_label = row0.get("horizon_label")
        # Actual horizon after fallback (in case requested wasn't available)
        if "horizon_months" in market_df.columns:
            horizon = int(row0["horizon_months"])

    market_df = enrich_rankings_for_display(market_df)
    peer_s = pd.to_numeric(market_df["peer_median_return"], errors="coerce") if not market_df.empty and "peer_median_return" in market_df.columns else pd.Series(dtype=float)
    peer_median = float(peer_s.dropna().iloc[0]) if not peer_s.dropna().empty else None
    stock_analysis = stock_board_analysis(market_df)
    board_as_of = board_hold_until = None
    if not market_df.empty and "as_of_date" in market_df.columns:
        dated = market_df.dropna(subset=["as_of_date"]).copy()
        if not dated.empty:
            dated["_as_of"] = pd.to_datetime(dated["as_of_date"], errors="coerce")
            fresh = dated.loc[dated["_as_of"].idxmax()]
            board_as_of = fresh.get("as_of_date")
            board_hold_until = fresh.get("hold_until_date")

    return render_template(
        "stocks_rankings.html",
        rows=rankings_records(market_df),
        category=f"stocks/{market}",
        category_label=label,
        market=market,
        back_href="/stocks",
        tv_symbol_map=STOCK_TV_SYMBOLS,
        horizons=available,
        horizon_labels=STOCK_HORIZON_LABELS,
        selected_horizon=horizon,
        horizon_label=horizon_label,
        model_name=model_name,
        model_ic=model_ic,
        model_r2=model_r2,
        peer_median=peer_median,
        board_as_of=board_as_of,
        board_hold_until=board_hold_until,
        stock_analysis=stock_analysis,
        board_analysis=None,
        live_board_stats=None,
    )


@app.route("/stocks/<market>/asset/<ticker>")
def stocks_asset_detail(market, ticker):
    if market not in ("us", "india"):
        abort(404)
    import investment_sources
    if not investment_sources.markets_enabled("stocks"):
        return Response(
            "Stocks boards are disabled in Admin → Sources.",
            503,
            {"Content-Type": "text/plain; charset=utf-8"},
        )
    asset_class = f"{market}_stocks"
    df = load_investment_rankings()
    available = available_stock_horizons(df, asset_class)
    raw_horizon = request.args.get("horizon", str(available[0]))
    try:
        horizon = int(raw_horizon)
    except (TypeError, ValueError):
        horizon = available[0]
    class_df = enrich_rankings_for_display(filter_rankings_by_horizon(df, asset_class, horizon))
    row = class_df[class_df["ticker"] == ticker]
    if row.empty:
        return render_template("not_found.html", home=ticker, away=""), 404
    label = "US Stocks" if market == "us" else "India Stocks"
    asset = rankings_records(row)[0]
    horizon = int(asset.get("horizon_months") or horizon)
    tv_symbol = STOCK_TV_SYMBOLS.get(ticker)
    endpoint_chart = build_forecast_endpoint_chart(asset)
    return render_template(
        "investment_asset_detail.html",
        asset=asset,
        category=f"stocks/{market}",
        category_label=label,
        back_href=f"/stocks/{market}?horizon={horizon}",
        total_in_class=len(class_df),
        horizon_months=horizon,
        horizon_label=asset.get("horizon_label") or STOCK_HORIZON_LABELS.get(horizon, f"{horizon} months"),
        market=market,
        tv_symbol=tv_symbol,
        endpoint_chart=endpoint_chart,
    )





# Parked products wiped from the public site — keep explicit 404s so
# /dubai and /metals never fall through to the soccer league catch-all.
@app.route("/dubai", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@app.route("/dubai/", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@app.route("/dubai/<path:rest>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def dubai_removed(rest=None):
    abort(404)


@app.route("/metals", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@app.route("/metals/", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@app.route("/metals/<path:rest>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def metals_removed(rest=None):
    abort(404)


@app.route("/<league_key>/")
def index(league_key):
    league = get_league_or_404(league_key)
    df = load_predictions(league_key)
    team_filter = request.args.get("team", "").strip()

    rows = df.to_dict(orient="records") if df is not None and not df.empty else []
    upcoming, _finished = soccer_fixtures.split_soccer_rows(league_key, rows)
    df = pd.DataFrame(upcoming)

    unlocked_round = compute_unlocked_round(df) if not df.empty else 1
    all_rounds = sorted(df["RoundNumber"].unique().tolist()) if not df.empty else []
    available_rounds = all_rounds

    round_filter = request.args.get("round", "")
    if round_filter and int(round_filter) in available_rounds:
        selected_round = int(round_filter)
    elif available_rounds:
        selected_round = available_rounds[0]
    else:
        selected_round = None

    if selected_round is not None and not df.empty:
        df = df[df["RoundNumber"] == selected_round]
    if team_filter and not df.empty:
        mask = df["HomeTeam"].str.contains(team_filter, case=False, na=False) | \
            df["AwayTeam"].str.contains(team_filter, case=False, na=False)
        df = df[mask]

    matches = df.to_dict(orient="records") if not df.empty else []

    return render_template(
        "index.html", matches=matches, rounds=available_rounds,
        selected_round=selected_round, team_filter=team_filter,
        total=len(matches),
        unlocked_round=unlocked_round,
        league_key=league_key, league_label=league["label"], leagues=LEAGUES,
        league_keywords=LEAGUE_DISPLAY.get(league_key, {}).get("keywords", ""),
        feed_stale=_feed_stale_for("soccer:"),
        breadcrumbs=seo_pages.breadcrumbs(
            ("Sports", "/sports"),
            ("Soccer", "/sports/soccer"),
            (league["label"], f"/{league_key}/"),
        ),
        breadcrumb_jsonld=seo_pages.breadcrumb_jsonld(seo_pages.breadcrumbs(
            ("Sports", "/sports"),
            ("Soccer", "/sports/soccer"),
            (league["label"], f"/{league_key}/"),
        )),
    )


@app.route("/<league_key>/fixtures")
def soccer_fixtures_alias(league_key):
    get_league_or_404(league_key)
    return redirect(f"/{league_key}/")


@app.route("/<league_key>/results")
def soccer_results_page(league_key):
    league = get_league_or_404(league_key)
    _refresh_after_events(min_interval=60)
    df = load_predictions(league_key)
    rows = df.to_dict(orient="records") if df is not None and not df.empty else []
    _upcoming, finished = soccer_fixtures.split_soccer_rows(league_key, rows)
    grouped = {}
    for m in finished:
        day = str(m.get("DateUtc") or "")[:10]
        grouped.setdefault(day, []).append(m)
    grouped = dict(sorted(grouped.items(), reverse=True))
    return render_template(
        "soccer_results.html",
        grouped_finished=grouped,
        result_count=len(finished),
        league_key=league_key,
        league_label=league["label"],
    )


def generate_betting_tips(m, league_key=None):
    """Turns raw model probabilities into plain-language leans + confidence.
    "High" uses calibrated bars (target ~70% OOS hit rate) when available.

    Safer Result is one selective market with explicit exact-win or
    favourite-or-draw semantics. It never forces a result pick.
    """
    tips = []
    t_safer = (
        today_picks.market_min_conf(league_key, safer_result.MARKET_NAME)
        if league_key
        else safer_result.DOUBLE_CHANCE_THRESHOLD
    )
    t_btts = today_picks.market_min_conf(league_key, "Both Teams to Score") or today_picks.HIGH_CONF_MIN
    t_goals = today_picks.market_min_conf(league_key, "Total Goals") or today_picks.HIGH_CONF_MIN
    t_corners = today_picks.market_min_conf(league_key, "Corners") or today_picks.HIGH_CONF_MIN

    def confidence(p, bar):
        if p >= bar:
            return "High"
        if p >= 0.45:
            return "Medium"
        return "Low"

    safer_tip = today_picks._safer_result_tip(m, league_key=league_key)
    if safer_tip:
        is_exact = safer_tip["selection_type"] == "exact"
        tips.append({
            "market": safer_result.MARKET_NAME,
            "pick": safer_tip["pick"],
            "probability": safer_tip["probability"],
            "confidence": confidence(
                safer_tip["probability"],
                safer_tip["threshold"],
            ),
            "note": (
                "Exact favourite win cleared both the 75% calibrated and raw 1X2 bars."
                if is_exact
                else "Favourite-or-draw cleared the 70% calibrated double-chance bar."
            ),
        })
    else:
        tips.append({
            "market": safer_result.MARKET_NAME,
            "pick": "No pick",
            "probability": None,
            "confidence": "Low",
            "note": (
                "Neither the calibrated 70% double-chance bar nor the stricter "
                "75% exact-win bar was cleared."
                if t_safer is not None
                else "This league does not have enough qualifying OOS evidence."
            ),
        })

    conf_btts = max(m["P_BTTS_Yes"], 1 - m["P_BTTS_Yes"])
    if conf_btts >= 0.55:
        pick = "Yes" if m["P_BTTS_Yes"] >= 0.5 else "No"
        p = conf_btts
        tips.append({"market": "Both Teams to Score", "pick": pick, "probability": p,
                      "confidence": confidence(p, t_btts), "note": None})

    conf_over = max(m["P_Over_2.5_Goals"], 1 - m["P_Over_2.5_Goals"])
    if conf_over >= 0.55:
        pick = "Over 2.5" if m["P_Over_2.5_Goals"] >= 0.5 else "Under 2.5"
        p = conf_over
        tips.append({"market": "Total Goals", "pick": pick, "probability": p,
                      "confidence": confidence(p, t_goals), "note": None})

    p_corners = m.get("P_Home_More_Corners")
    if (
        p_corners is not None
        and str(p_corners).strip() != ""
        and not pd.isna(p_corners)
        and max(float(p_corners), 1 - float(p_corners)) >= 0.55
    ):
        p_corners = float(p_corners)
        pick = f"{m['HomeTeam']} (more corners)" if p_corners >= 0.5 \
            else f"{m['AwayTeam']} (more corners)"
        p = max(p_corners, 1 - p_corners)
        tips.append({"market": "Corners", "pick": pick, "probability": p,
                      "confidence": confidence(p, t_corners), "note": None})

    return tips


@app.route("/<league_key>/predictions-today")
def soccer_predictions_today(league_key):
    """Evergreen SEO hub: today's upcoming fixtures + model leans for a soccer league."""
    league = get_league_or_404(league_key)
    cache_key = f"pred_today:{league_key}"
    board = _page_cache.get(cache_key)
    if board is None:
        board = seo_pages.build_predictions_today_board(
            league_key, load_predictions, soccer_fixtures.split_soccer_rows,
        )
        # Attach tip teasers + intros for cards
        enriched = []
        for f in board.get("fixtures") or []:
            row = f.get("row") or {}
            tips = generate_betting_tips(row, league_key=league_key)
            top = None
            for tip in tips:
                if tip.get("probability") is not None and tip.get("pick") not in (None, "No pick"):
                    if tip.get("confidence") == "High" or top is None:
                        top = tip
                    if tip.get("confidence") == "High":
                        break
            ctx = None
            try:
                match_date = str(row.get("DateUtc") or "")[:10]
                ctx = match_context.build_match_context(
                    league_key, league["label"], f["home"], f["away"], match_date, match=row,
                )
            except Exception:
                ctx = None
            intro = seo_pages.match_seo_intro(row, tips, league["label"], ctx)
            f = dict(f)
            f["top_tip"] = top
            f["seo_intro"] = intro
            f["indexable"] = seo_pages.is_match_indexable(row, ctx)
            f.pop("row", None)
            enriched.append(f)
        board = dict(board)
        board["fixtures"] = enriched
        _page_cache.set(cache_key, board)

    crumbs = seo_pages.breadcrumbs(
        ("Sports", "/sports"),
        ("Soccer", "/sports/soccer"),
        (league["label"], f"/{league_key}/"),
        ("Predictions today", f"/{league_key}/predictions-today"),
    )
    return render_template(
        "predictions_today.html",
        board=board,
        league_key=league_key,
        league_label=league["label"],
        league_keywords=LEAGUE_DISPLAY.get(league_key, {}).get("keywords", ""),
        breadcrumbs=crumbs,
        breadcrumb_jsonld=seo_pages.breadcrumb_jsonld(crumbs),
    )


@app.route("/<league_key>/match/<home>/<away>")
def match_detail(league_key, home, away):
    league = get_league_or_404(league_key)
    df = load_predictions(league_key)
    row = df[(df["HomeTeam"] == home) & (df["AwayTeam"] == away)]
    if row.empty:
        return render_template("not_found.html", home=home, away=away), 404
    match = row.iloc[0].to_dict()
    tips = generate_betting_tips(match, league_key=league_key)

    match_date = pd.to_datetime(match["DateUtc"]).strftime("%Y-%m-%d")
    lineup_info = get_live_matchday_info(home, away, match_date) if live_predict.USE_LIVE_LINEUP else None
    match_end_date = estimated_end_iso(match["DateUtc"], hours=2)
    # The source CSV stores DateUtc with a space separator ("2026-08-15
    # 07:30:00Z"), not proper ISO 8601 ("...T07:30:00Z") -- fine for the
    # human-readable date used elsewhere on this page, but Google's
    # structured-data validator rejects it as a malformed startDate. This
    # is the actual ISO-formatted value the SportsEvent JSON-LD uses.
    match_start_date_iso = estimated_end_iso(match["DateUtc"], hours=0)
    context = match_context.build_match_context(
        league_key, league["label"], home, away, match_date,
        match=match, lineup_info=lineup_info,
    )
    indexable = seo_pages.is_match_indexable(match, context)
    seo_intro = seo_pages.match_seo_intro(match, tips, league["label"], context)
    seo_description = seo_pages.match_seo_description(match, league["label"], seo_intro)
    crumbs = seo_pages.breadcrumbs(
        ("Sports", "/sports"),
        (league["label"], f"/{league_key}/"),
        ("Predictions today", f"/{league_key}/predictions-today"),
        (f"{home} vs {away}", f"/{league_key}/match/{quote(home)}/{quote(away)}"),
    )
    match_canonical = seo_pages.soccer_match_url(league_key, home, away)

    return render_template(
        "match.html", match=match, tips=tips, lineup_info=lineup_info,
        league_key=league_key, league_label=league["label"], league_country=league.get("country", ""),
        match_end_date=match_end_date, match_start_date_iso=match_start_date_iso,
        context=context,
        indexable=indexable,
        seo_intro=seo_intro,
        seo_description=seo_description,
        breadcrumbs=crumbs,
        breadcrumb_jsonld=seo_pages.breadcrumb_jsonld(crumbs),
        match_canonical=match_canonical,
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
        # Never surface raw Python exceptions (e.g. unpack TypeError) on the
        # public fixture card -- log server-side and return a clean message.
        app.logger.exception(
            "Live predict failed for %s: %s vs %s (%s)",
            league_key, home, away, exc,
        )
        return jsonify({
            "error": "Prediction unavailable for this fixture right now. Please try again later.",
        }), 500

    safer_tip = today_picks._safer_result_tip(result, league_key=league_key)
    result["Safer_Result_Pick"] = safer_tip["pick"] if safer_tip else "No pick"
    result["P_Safer_Result"] = safer_tip["probability"] if safer_tip else None
    result["Safer_Result_Code"] = safer_tip["code"] if safer_tip else None
    result["Unlocked_Round"] = unlocked_round
    return jsonify(result)


def _mlb_et_date(iso_utc_str):
    """Kickoff timestamp -> YYYY-MM-DD in America/New_York (MLB slate day)."""
    if not iso_utc_str:
        return ""
    try:
        from zoneinfo import ZoneInfo
        et = ZoneInfo("America/New_York")
    except Exception:
        et = datetime.timezone(datetime.timedelta(hours=-4))
    try:
        dt = datetime.datetime.fromisoformat(iso_utc_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(et).date().isoformat()
    except (ValueError, TypeError):
        return (iso_utc_str or "")[:10]


def _mlb_today_et():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:
        return datetime.datetime.now(
            datetime.timezone(datetime.timedelta(hours=-4))
        ).date().isoformat()


def build_mlb_today_slate():
    """Today's MLB board (ET): live + upcoming + finished, with model picks
    already filled in so the page is a habit board -- not click-to-predict."""
    cached = _page_cache.get("mlb_slate")
    if cached is not None:
        return cached

    fixtures = get_mlb_fixtures()
    elo_df = load_mlb_elo()
    elo_teams = set(elo_df["Team"]) if not elo_df.empty else set()
    today_et = _mlb_today_et()

    candidates = []
    for m in fixtures:
        m = dict(m)
        m["home_code"] = ESPN_TO_RETROSHEET.get(m["home_abbr"])
        m["away_code"] = ESPN_TO_RETROSHEET.get(m["away_abbr"])
        m["has_rating"] = bool(m["home_code"] in elo_teams and m["away_code"] in elo_teams)
        m["et_date"] = _mlb_et_date(m.get("date") or "")
        is_live = m.get("status") == "in"
        if m["et_date"] == today_et or is_live:
            candidates.append(m)

    names = []
    for m in candidates:
        if m["has_rating"]:
            for code in (m["home_code"], m["away_code"]):
                row = elo_df[elo_df["Team"] == code]
                if not row.empty:
                    names.append(str(row.iloc[0]["TeamName"]))
    if names:
        # Non-blocking: fills cache in background; prediction uses cached score or None.
        sentiment.prefetch_team_sentiments(names)

    board, top_picks = [], []
    for m in candidates:
        entry = {
            "fixture": m,
            "prediction": None,
            "confidence": None,
            "winner_name": None,
            "winner_correct": None,
            "match_url": None,
        }
        if m["has_rating"]:
            home_era = (m.get("home_pitcher") or {}).get("era") or ""
            away_era = (m.get("away_pitcher") or {}).get("era") or ""
            pred = compute_mlb_prediction(
                m["home_code"], m["away_code"], home_era, away_era,
                as_of_date=(m.get("date") or "")[:10] or None,
            )
            if pred is not None:
                entry["prediction"] = pred
                conf = max(pred["home_prob"], pred["away_prob"])
                entry["confidence"] = conf
                entry["winner_name"] = (
                    m["home_team"] if pred["favourite"] == m["home_code"] else m["away_team"]
                )
                entry["match_url"] = (
                    f"/mlb/match/{m['home_code']}/{m['away_code']}"
                    f"?home_name={quote(m['home_team'])}&away_name={quote(m['away_team'])}"
                    f"&date={quote(m.get('date') or '')}"
                    f"&home_era={quote(str(home_era))}&away_era={quote(str(away_era))}"
                )
                if m.get("completed"):
                    try:
                        actual_home = float(m.get("home_score"))
                        actual_away = float(m.get("away_score"))
                        actual_winner = m["home_code"] if actual_home > actual_away else m["away_code"]
                        entry["winner_correct"] = pred["favourite"] == actual_winner
                    except (TypeError, ValueError):
                        entry["winner_correct"] = None
                if not m.get("completed"):
                    top_picks.append(entry)
        board.append(entry)

    top_picks.sort(key=lambda e: e["confidence"] or 0, reverse=True)
    board.sort(key=lambda e: (
        0 if e["fixture"].get("status") == "in" else 1 if not e["fixture"].get("completed") else 2,
        e["fixture"].get("date") or "",
    ))
    out = {
        "today_et": today_et,
        "board": board,
        "top_picks": top_picks[:5],
        "live_count": sum(1 for e in board if e["fixture"].get("status") == "in"),
        "upcoming_count": sum(
            1 for e in board
            if not e["fixture"].get("completed") and e["fixture"].get("status") != "in"
        ),
        "finished_count": sum(1 for e in board if e["fixture"].get("completed")),
    }
    _page_cache.set("mlb_slate", out)
    return out


@app.route("/mlb/")
def mlb_home():
    return redirect("/todays-picks/baseball/mlb")


@app.route("/mlb/today")
def mlb_today_page():
    return redirect("/todays-picks/baseball/mlb")


@app.route("/mlb/fixtures")
def mlb_fixtures_page():
    fixtures = get_mlb_fixtures()
    elo_df = load_mlb_elo()
    elo_teams = set(elo_df["Team"]) if not elo_df.empty else set()

    for m in fixtures:
        m["home_code"] = ESPN_TO_RETROSHEET.get(m["home_abbr"])
        m["away_code"] = ESPN_TO_RETROSHEET.get(m["away_abbr"])
        m["has_rating"] = bool(m["home_code"] in elo_teams and m["away_code"] in elo_teams)

    _refresh_after_events(min_interval=60)
    live_or_upcoming, postponed, finished = today_picks.split_fixture_board(
        fixtures, sport_key="baseball",
    )
    del finished

    grouped_by_date = {}
    for m in live_or_upcoming:
        day = (m["date"] or "")[:10]
        grouped_by_date.setdefault(day, []).append(m)

    return render_template(
        "mlb_fixtures.html", fixtures=live_or_upcoming,
        grouped_by_date=grouped_by_date, grouped_finished={},
        postponed=postponed,
        feed_stale=_feed_stale_for("mlb:", "espn"),
    )


MLB_RESULTS_CACHE_PATH = os.path.join(WEBAPP_DIR, "mlb_results_cache.html")
MLB_RESULTS_CACHE_TTL_SECONDS = 3 * 60 * 60


def _build_mlb_results_page():
    fixtures = get_mlb_fixtures()
    _upcoming, _postponed, latest_scores = today_picks.split_fixture_board(
        fixtures, sport_key="baseball",
    )
    latest_scores = sorted(
        latest_scores, key=lambda m: str(m.get("date") or ""), reverse=True,
    )[:40]
    mlb_rows, mlb_summary = mlb_track_record(n=40)
    return render_template(
        "mlb_results.html",
        latest_scores=latest_scores,
        mlb_rows=mlb_rows,
        mlb_summary=mlb_summary,
        results_generated_at=today_picks.format_kickoff_ist(
            datetime.datetime.now(datetime.timezone.utc).isoformat()
        ),
    )


@app.route("/mlb/results")
def mlb_results_page():
    """Public MLB accuracy ledger -- habit/trust page, not admin-gated."""
    _refresh_after_events(min_interval=60)
    force_refresh = request.args.get("refresh") == "1"
    try:
        age = datetime.datetime.now(datetime.timezone.utc).timestamp() - os.path.getmtime(MLB_RESULTS_CACHE_PATH)
        if not force_refresh and age <= MLB_RESULTS_CACHE_TTL_SECONDS:
            with open(MLB_RESULTS_CACHE_PATH, "r", encoding="utf-8") as f:
                return f.read()
    except OSError:
        pass

    html = _build_mlb_results_page()
    try:
        with open(MLB_RESULTS_CACHE_PATH, "w", encoding="utf-8") as f:
            f.write(html)
    except OSError:
        pass
    return html


def compute_mlb_prediction(home_code, away_code, home_era="", away_era="",
                           home_pitcher=None, away_pitcher=None, as_of_date=None):
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

    ml_features = build_mlb_ml_features(
        home_row, away_row, h2h,
        home_era=home_era, away_era=away_era,
        home_pitcher=home_pitcher, away_pitcher=away_pitcher,
        as_of_date=as_of_date,
    )
    ml_prob_home = _ml_win_probability(MLB_ML_MODEL, ml_features)
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
        home_prob=prob_home, away_prob=1 - prob_home,
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
        "home_runs_line": home_runs_ou["line"], "home_runs_pick": home_runs_ou["pick"],
        "home_runs_pick_probability": home_runs_ou["probability"],
        "away_runs_line": away_runs_ou["line"], "away_runs_pick": away_runs_ou["pick"],
        "away_runs_pick_probability": away_runs_ou["probability"],
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
    match_date = request.args.get("date", "")

    result = compute_mlb_prediction(
        home_code, away_code, home_era, away_era, as_of_date=match_date or None,
    )
    if result is None:
        return jsonify({"error": "One or both teams don't have enough rated games yet."}), 404
    return jsonify(result)


@app.route("/mlb/match/<home_code>/<away_code>")
def mlb_match_detail(home_code, away_code):
    home_era = request.args.get("home_era", "")
    away_era = request.args.get("away_era", "")
    home_name = request.args.get("home_name", "")
    away_name = request.args.get("away_name", "")
    match_date = request.args.get("date", "")

    # Prefer clean path URLs in search; resolve names from today's fixtures when omitted.
    if not home_name or not away_name or home_name == home_code or away_name == away_code:
        try:
            for m in get_mlb_fixtures() or []:
                hc = ESPN_TO_RETROSHEET.get(m.get("home_abbr"))
                ac = ESPN_TO_RETROSHEET.get(m.get("away_abbr"))
                if hc == home_code and ac == away_code:
                    home_name = home_name or m.get("home_team") or home_code
                    away_name = away_name or m.get("away_team") or away_code
                    match_date = match_date or (m.get("date") or "")
                    break
        except Exception:
            pass
    home_name = home_name or home_code
    away_name = away_name or away_code

    result = compute_mlb_prediction(
        home_code, away_code, home_era, away_era, as_of_date=match_date or None,
    )
    if result is None:
        return render_template("not_found.html", home=home_name, away=away_name), 404

    crumbs = seo_pages.breadcrumbs(
        ("Sports", "/sports"),
        ("MLB", "/mlb/fixtures"),
        (f"{away_name} vs {home_name}", f"/mlb/match/{home_code}/{away_code}"),
    )
    return render_template(
        "mlb_match.html", match=result, home_name=home_name, away_name=away_name,
        match_date=match_date, home_era=home_era, away_era=away_era,
        match_end_date=estimated_end_iso(match_date, hours=3),
        breadcrumbs=crumbs,
        breadcrumb_jsonld=seo_pages.breadcrumb_jsonld(crumbs),
        page_canonical=f"https://degenpredictions.com/mlb/match/{home_code}/{away_code}",
    )


def build_nfl_today_slate():
    """Today's NFL board (ET): live + upcoming + finished, with model picks."""
    cached = _page_cache.get("nfl_slate")
    if cached is not None:
        return cached

    ensure_nfl_ratings()
    fixtures = get_nfl_fixtures()
    elo_df = load_nfl_elo()
    elo_teams = set(elo_df["Team"]) if not elo_df.empty else set()
    today_et = _mlb_today_et()

    candidates = []
    for m in fixtures:
        m = dict(m)
        m["home_code"] = (m.get("home_abbr") or "").strip().upper()
        m["away_code"] = (m.get("away_abbr") or "").strip().upper()
        m["has_rating"] = bool(m["home_code"] in elo_teams and m["away_code"] in elo_teams)
        m["et_date"] = _mlb_et_date(m.get("date") or "")
        is_live = m.get("status") == "in"
        if m["et_date"] == today_et or is_live:
            candidates.append(m)

    names = []
    for m in candidates:
        if m.get("home_team"):
            names.append(m["home_team"])
        if m.get("away_team"):
            names.append(m["away_team"])
    if names:
        sentiment.prefetch_team_sentiments(names)

    board, top_picks = [], []
    for m in candidates:
        entry = {
            "fixture": m,
            "prediction": None,
            "confidence": None,
            "winner_name": None,
            "winner_correct": None,
            "match_url": None,
        }
        if m["has_rating"]:
            pred = compute_nfl_prediction(m["home_code"], m["away_code"])
            if pred is not None:
                entry["prediction"] = pred
                conf = max(pred["home_prob"], pred["away_prob"])
                entry["confidence"] = conf
                entry["winner_name"] = (
                    m["home_team"] if pred["favourite"] == m["home_code"] else m["away_team"]
                )
                entry["match_url"] = (
                    f"/nfl/match/{m['home_code']}/{m['away_code']}"
                    f"?home_name={quote(m['home_team'])}&away_name={quote(m['away_team'])}"
                    f"&date={quote(m.get('date') or '')}"
                )
                if m.get("completed"):
                    try:
                        actual_home = float(m.get("home_score"))
                        actual_away = float(m.get("away_score"))
                        actual_winner = m["home_code"] if actual_home > actual_away else m["away_code"]
                        entry["winner_correct"] = pred["favourite"] == actual_winner
                    except (TypeError, ValueError):
                        entry["winner_correct"] = None
                if not m.get("completed"):
                    top_picks.append(entry)
        board.append(entry)

    top_picks.sort(key=lambda e: e["confidence"] or 0, reverse=True)
    board.sort(key=lambda e: (
        0 if e["fixture"].get("status") == "in" else 1 if not e["fixture"].get("completed") else 2,
        e["fixture"].get("date") or "",
    ))
    out = {
        "today_et": today_et,
        "board": board,
        "top_picks": top_picks[:5],
        "live_count": sum(1 for e in board if e["fixture"].get("status") == "in"),
        "upcoming_count": sum(
            1 for e in board
            if not e["fixture"].get("completed") and e["fixture"].get("status") != "in"
        ),
        "finished_count": sum(1 for e in board if e["fixture"].get("completed")),
    }
    _page_cache.set("nfl_slate", out)
    return out


@app.route("/nfl/")
def nfl_home():
    return redirect("/todays-picks/american_football/nfl")


@app.route("/nfl/fixtures")
def nfl_fixtures_page():
    ensure_nfl_ratings()
    fixtures = get_nfl_fixtures()
    elo_df = load_nfl_elo()
    elo_teams = set(elo_df["Team"]) if not elo_df.empty else set()

    for m in fixtures:
        m["home_code"] = (m.get("home_abbr") or "").strip().upper()
        m["away_code"] = (m.get("away_abbr") or "").strip().upper()
        m["has_rating"] = bool(m["home_code"] in elo_teams and m["away_code"] in elo_teams)

    _refresh_after_events(min_interval=60)
    live_or_upcoming, postponed, finished = today_picks.split_fixture_board(
        fixtures, sport_key="american_football",
    )
    del finished

    grouped_by_date = {}
    for m in live_or_upcoming:
        day = (m["date"] or "")[:10]
        grouped_by_date.setdefault(day, []).append(m)

    return render_template(
        "nfl_fixtures.html", fixtures=live_or_upcoming,
        grouped_by_date=grouped_by_date, grouped_finished={},
        postponed=postponed,
    )


NFL_RESULTS_CACHE_PATH = os.path.join(WEBAPP_DIR, "nfl_results_cache.html")
NFL_RESULTS_CACHE_TTL_SECONDS = 3 * 60 * 60


def _build_nfl_results_page():
    fixtures = get_nfl_fixtures()
    _upcoming, _postponed, latest_scores = today_picks.split_fixture_board(
        fixtures, sport_key="american_football",
    )
    latest_scores = sorted(
        latest_scores, key=lambda m: str(m.get("date") or ""), reverse=True,
    )[:40]
    nfl_rows, nfl_summary = nfl_track_record(n=40)
    return render_template(
        "nfl_results.html",
        latest_scores=latest_scores,
        nfl_rows=nfl_rows,
        nfl_summary=nfl_summary,
        results_generated_at=today_picks.format_kickoff_ist(
            datetime.datetime.now(datetime.timezone.utc).isoformat()
        ),
    )


@app.route("/nfl/results")
def nfl_results_page():
    """Public NFL accuracy ledger -- habit/trust page, not admin-gated."""
    _refresh_after_events(min_interval=60)
    force_refresh = request.args.get("refresh") == "1"
    try:
        age = datetime.datetime.now(datetime.timezone.utc).timestamp() - os.path.getmtime(NFL_RESULTS_CACHE_PATH)
        if not force_refresh and age <= NFL_RESULTS_CACHE_TTL_SECONDS:
            with open(NFL_RESULTS_CACHE_PATH, "r", encoding="utf-8") as f:
                return f.read()
    except OSError:
        pass

    html = _build_nfl_results_page()
    try:
        with open(NFL_RESULTS_CACHE_PATH, "w", encoding="utf-8") as f:
            f.write(html)
    except OSError:
        pass
    return html


@app.route("/nfl/api/predict")
def nfl_api_predict():
    home_code = request.args.get("home", "").strip()
    away_code = request.args.get("away", "").strip()
    result = compute_nfl_prediction(home_code, away_code)
    if result is None:
        return jsonify({"error": "One or both teams don't have enough rated games yet."}), 404
    return jsonify(result)


@app.route("/nfl/match/<home_code>/<away_code>")
def nfl_match_detail(home_code, away_code):
    home_name = request.args.get("home_name", home_code)
    away_name = request.args.get("away_name", away_code)
    match_date = request.args.get("date", "")

    result = compute_nfl_prediction(home_code, away_code)
    if result is None:
        return render_template("not_found.html", home=home_name, away=away_name), 404

    return render_template(
        "nfl_match.html", match=result, home_name=home_name, away_name=away_name,
        match_date=match_date,
        match_end_date=estimated_end_iso(match_date, hours=3.5),
    )


def compute_npb_prediction(home_id, away_id, as_of_date=None):
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

    ml_features = build_npb_ml_features(home_row, away_row, h2h, as_of_date=as_of_date)
    # Older shipped models may not include rest features yet -- only pass
    # columns the loaded bundle expects so a non-improved retrain still works.
    if NPB_ML_MODEL is not None:
        wanted = NPB_ML_MODEL.get("features") or list(ml_features.keys())
        ml_features = {k: ml_features[k] for k in wanted if k in ml_features}
    ml_prob_home = _ml_win_probability(NPB_ML_MODEL, ml_features)
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
        home_prob=prob_home, away_prob=1 - prob_home,
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
        "home_runs_line": home_runs_ou["line"], "home_runs_pick": home_runs_ou["pick"],
        "home_runs_pick_probability": home_runs_ou["probability"],
        "away_runs_line": away_runs_ou["line"], "away_runs_pick": away_runs_ou["pick"],
        "away_runs_pick_probability": away_runs_ou["probability"],
        "insights": insights,
    }


@app.route("/npb/")
def npb_home():
    return redirect("/npb/fixtures")


@app.route("/npb/fixtures")
def npb_fixtures_page():
    _refresh_after_events(min_interval=60)
    fixtures = get_npb_fixtures()
    elo_df = load_npb_elo()
    elo_teams = set(elo_df["TeamID"]) if not elo_df.empty else set()

    for m in fixtures:
        m["has_rating"] = bool(m["home_team_id"] in elo_teams and m["away_team_id"] in elo_teams)

    today_jst = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=9))
    ).date().isoformat()
    live_or_upcoming, postponed, finished = today_picks.split_fixture_board(
        fixtures, sport_key="baseball", today_str=today_jst,
    )
    del finished

    grouped_by_date = {}
    for m in live_or_upcoming:
        day = (m["date"] or "")[:10]
        grouped_by_date.setdefault(day, []).append(m)

    return render_template(
        "npb_fixtures.html", fixtures=live_or_upcoming,
        grouped_by_date=grouped_by_date, grouped_finished={},
        postponed=postponed,
    )


@app.route("/npb/results")
def npb_results_page():
    """Live spaia finals — not the lagged training archive."""
    _refresh_after_events(min_interval=60)
    fixtures = get_npb_fixtures(days_back=14, resolve_pitchers=False)
    finished = [m for m in fixtures if m.get("completed") and m.get("home_score") is not None]
    grouped = {}
    for m in finished:
        day = (m.get("date") or "")[:10]
        grouped.setdefault(day, []).append(m)
    grouped = dict(sorted(grouped.items(), reverse=True))
    return render_template(
        "npb_results.html",
        grouped_finished=grouped,
        result_count=len(finished),
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
    """Robots.txt for search engines and AI/answer crawlers (GEO)."""
    return Response("""User-agent: *
Allow: /
Disallow: /api/
Disallow: /*/api/
Disallow: /*/api/predict
Disallow: /*/api/predict/
Disallow: /admin
Disallow: /admin/
Disallow: /go/gp
Disallow: /go/gp/
Disallow: /results
Disallow: /backtest
Disallow: /todays-picks/digest
Disallow: /todays-picks/top3
Disallow: /todays-picks/digest.json
Disallow: /todays-picks/combos/results

User-agent: GPTBot
Allow: /

User-agent: ChatGPT-User
Allow: /

User-agent: Google-Extended
Allow: /

User-agent: GoogleOther
Allow: /

User-agent: PerplexityBot
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: anthropic-ai
Allow: /

User-agent: Applebot-Extended
Allow: /

# https://degenpredictions.com/llms.txt
# https://degenpredictions.com/llms-full.txt
Sitemap: https://degenpredictions.com/sitemap.xml
""", mimetype="text/plain")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/contact")
def contact():
    # Keep the old URL for crawlers; About is the public contact destination.
    return redirect("/about", code=301)


@app.route("/go/telegram")
def go_telegram():
    """Tracked hop to the public channel — logs as /go/telegram in analytics."""
    url = (os.environ.get("TELEGRAM_CHANNEL_URL") or "").strip()
    if not url:
        url = getattr(daily_digest, "DEFAULT_TELEGRAM_CHANNEL_URL", "") or ""
    if not url:
        return redirect("/todays-picks")
    return redirect(url, code=302)


_GP_DEST_RE = re.compile(
    r"^/(?:ipl|cpl|hundred|mlc|bbl|tennis)/fixture/[A-Za-z0-9][A-Za-z0-9._~-]*$"
)


@app.route("/api/unlock/<kind>", methods=["GET", "POST"])
def api_unlock(kind):
    """Record a human Unlock prediction / Unlock combo click. 204 only."""
    if kind not in ("combo", "pick"):
        abort(404)
    return ("", 204)


@app.route("/go/gp/<path:rest>")
def go_get_prediction(rest):
    """Tracked Get Prediction hop for cricket/tennis fixture buttons."""
    dest = "/" + (rest or "").lstrip("/")
    if not _GP_DEST_RE.match(dest):
        abort(404)
    return redirect(dest, code=302)


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



def _full_slate_acc_label(league_id: str):
    """Honest full-slate OOS accuracy label from confidence_thresholds, or None."""
    thr = today_picks.load_sport_thresholds(league_id)
    full = thr.get("full_slate_acc")
    if isinstance(full, (int, float)):
        return f"{100 * float(full):.0f}%"
    return None


SITE_URL = "https://degenpredictions.com"


@app.route("/ads.txt")
def ads_txt():
    body = "google.com, pub-9692020533590575, DIRECT, f08c47fec0942fa0\n"
    return Response(body, mimetype="text/plain")


@app.route("/favicon.ico", strict_slashes=False)
def favicon_ico():
    """Root favicon for Google/Bing SERP (avoid /favicon.ico → 404 slash redirect)."""
    from flask import send_from_directory

    resp = send_from_directory(
        os.path.join(WEBAPP_DIR, "static"),
        "favicon.ico",
        mimetype="image/x-icon",
        max_age=60 * 60 * 24 * 7,
    )
    # Help CDNs drop a stale globe-era icon after brand swaps.
    resp.headers["Cache-Control"] = "public, max-age=604800, must-revalidate"
    return resp


@app.route("/BingSiteAuth.xml")
def bing_site_auth():
    """Bing Webmaster Tools ownership verification (XML file method)."""
    body = (
        '<?xml version="1.0"?>\n'
        "<users>\n"
        "<user>803CFF66F82250CBA5A34A0E523D2440</user>\n"
        "</users>\n"
    )
    return Response(body, mimetype="application/xml")


@app.route("/84e8fa3329404e81b2aba483549d03f4.txt")
def indexnow_key_file():
    """IndexNow ownership key file (Bing Webmaster)."""
    return Response(
        "84e8fa3329404e81b2aba483549d03f4\n",
        mimetype="text/plain; charset=utf-8",
    )

@app.route("/llms.txt")
def llms_txt():
    """Plain-text site summary for AI crawlers/generative engines (GEO)."""
    body = build_llms_txt(
        highlights=_public_accuracy_highlights(limit=8),
        updated=board_updated_ist(),
        full=False,
    )
    return Response(body, mimetype="text/plain")


@app.route("/llms-full.txt")
def llms_full_txt():
    """Longer citation-oriented summary (llmstxt.org optional full file)."""
    body = build_llms_txt(
        highlights=_public_accuracy_highlights(limit=8),
        updated=board_updated_ist(),
        full=True,
    )
    return Response(body, mimetype="text/plain")


SITEMAP_CACHE_DIR = os.path.join(WEBAPP_DIR, "sitemap_cache")
SITEMAP_CACHE_TTL_SECONDS = 1200  # 20 min
# Legacy single-file cache (cleared once shards land).
SITEMAP_CACHE_PATH = os.path.join(WEBAPP_DIR, "sitemap_cache.xml")


def _sitemap_urlset(entries: list[tuple[str, str]]) -> str:
    """entries: list of (loc, lastmod ISO date)."""
    body = "".join(
        f"<url><loc>{loc.replace('&', '&amp;')}</loc><lastmod>{lastmod}</lastmod></url>"
        for loc, lastmod in entries
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{body}</urlset>"
    )


def _sitemap_index(shards: list[tuple[str, str]]) -> str:
    """shards: list of (sitemap path under SITE_URL, lastmod)."""
    body = "".join(
        f"<sitemap><loc>{SITE_URL}{path}</loc><lastmod>{lastmod}</lastmod></sitemap>"
        for path, lastmod in shards
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{body}</sitemapindex>"
    )


def _build_sitemap_static() -> str:
    static_pages = [
        "", "terms", "privacy", "feedback",
        "todays-picks", "todays-picks/combos",
        "todays-picks/soccer", "todays-picks/baseball", "todays-picks/cricket",
        "todays-picks/soccer/premier_league", "todays-picks/soccer/la_liga",
        "todays-picks/soccer/serie_a", "todays-picks/soccer/bundesliga", "todays-picks/soccer/ligue_1",
        "todays-picks/soccer/mls", "todays-picks/soccer/primeira_liga", "todays-picks/soccer/liga_mx",
        "todays-picks/baseball/mlb", "todays-picks/baseball/npb",
        "todays-picks/cricket/hundred", "todays-picks/cricket/mlc",
        "todays-picks/cricket/ipl", "todays-picks/cricket/bbl",
        "todays-picks/cricket/cpl",
        "todays-picks/tennis", "todays-picks/tennis/atp",
        "todays-picks/american_football", "todays-picks/american_football/nfl",
        "mlb/today", "mlb/fixtures", "mlb/results", "npb/fixtures", "npb/results",
        "nfl/fixtures", "nfl/results",
        "hundred/fixtures", "hundred/results", "mlc/fixtures", "mlc/results",
        "ipl/fixtures", "ipl/results", "bbl/fixtures", "bbl/results",
        "cpl/fixtures", "cpl/results",
        "tennis/fixtures", "tennis/results",
        "sports", "sports/soccer", "sports/baseball", "sports/cricket", "sports/tennis",
        "sports/american-football",
        "accuracy", "about", "faq",
        "crypto", "stocks", "stocks/us", "stocks/india",
    ]
    today = datetime.date.today().isoformat()
    return _sitemap_urlset([(f"{SITE_URL}/{p}" if p else f"{SITE_URL}/", today) for p in static_pages])


def _build_sitemap_leagues() -> str:
    entries = []
    for key in LEAGUES:
        pred_path = league_paths(key).get("pred")
        lastmod = seo_pages.file_lastmod(pred_path)
        entries.append((f"{SITE_URL}/{key}/", lastmod))
        entries.append((f"{SITE_URL}/{key}/about", lastmod))
        entries.append((f"{SITE_URL}/{key}/results", lastmod))
        entries.append((seo_pages.predictions_today_url(key), lastmod))
    return _sitemap_urlset(entries)


def _build_sitemap_matches() -> str:
    entries = []
    for key in LEAGUES:
        pred_path = league_paths(key).get("pred")
        lastmod = seo_pages.file_lastmod(pred_path)
        try:
            df = load_predictions(key)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        for _, row in df.iterrows():
            m = row.to_dict()
            if not seo_pages.match_has_forecast(m):
                continue
            entries.append((
                seo_pages.soccer_match_url(key, m["HomeTeam"], m["AwayTeam"]),
                lastmod,
            ))

    # Clean MLB / NFL / NPB paths (no query strings).
    try:
        for m in get_mlb_fixtures():
            home_code = ESPN_TO_RETROSHEET.get(m["home_abbr"])
            away_code = ESPN_TO_RETROSHEET.get(m["away_abbr"])
            if home_code and away_code:
                entries.append((seo_pages.clean_mlb_match_url(home_code, away_code), datetime.date.today().isoformat()))
    except Exception:
        pass

    try:
        for m in get_nfl_fixtures():
            home_code = (m.get("home_abbr") or "").strip().upper()
            away_code = (m.get("away_abbr") or "").strip().upper()
            if home_code and away_code:
                entries.append((seo_pages.clean_nfl_match_url(home_code, away_code), datetime.date.today().isoformat()))
    except Exception:
        pass

    try:
        npb_elo = load_npb_elo()
        npb_team_ids = set(npb_elo["TeamID"]) if not npb_elo.empty else set()
        for m in get_npb_fixtures():
            if m["home_team_id"] in npb_team_ids and m["away_team_id"] in npb_team_ids:
                entries.append((
                    seo_pages.clean_npb_match_url(m["home_team_id"], m["away_team_id"]),
                    datetime.date.today().isoformat(),
                ))
    except Exception:
        pass

    try:
        for f in tennis_predict.upcoming_with_predictions():
            slug = f.get("slug")
            if slug:
                entries.append((f"{SITE_URL}/tennis/fixture/{quote(str(slug))}", datetime.date.today().isoformat()))
    except Exception:
        pass

    try:
        for slug, competition in CRICKET_COMPETITION_BY_SLUG.items():
            for _, row in cricket_predict.matches_for_competition(competition, limit=10000).iterrows():
                entries.append((f"{SITE_URL}/{slug}/match/{int(row['match_id'])}", datetime.date.today().isoformat()))
    except Exception:
        pass

    return _sitemap_urlset(entries)


def _build_sitemap_markets() -> str:
    entries = []
    today = datetime.date.today().isoformat()
    try:
        inv_df = load_investment_rankings()
        lastmod = today
        try:
            import investment_sources
            lastmod = seo_pages.file_lastmod(investment_sources.CSV_PATH)
        except Exception:
            pass
        for _, row in inv_df.iterrows():
            asset_class = row["asset_class"]
            ticker = quote(str(row["ticker"]))
            if asset_class == "crypto":
                entries.append((f"{SITE_URL}/crypto/asset/{ticker}", lastmod))
            elif asset_class == "us_stocks":
                entries.append((f"{SITE_URL}/stocks/us/asset/{ticker}", lastmod))
            elif asset_class == "india_stocks":
                entries.append((f"{SITE_URL}/stocks/india/asset/{ticker}", lastmod))
    except Exception:
        pass
    if not entries:
        entries = [
            (f"{SITE_URL}/crypto", today),
            (f"{SITE_URL}/stocks/us", today),
            (f"{SITE_URL}/stocks/india", today),
        ]
    return _sitemap_urlset(entries)


def _build_sitemap_xml():
    """Legacy monolithic builder kept for tests that call it directly."""
    # Prefer concatenating shards into one urlset for callers that still expect it.
    shards = [
        _build_sitemap_static(),
        _build_sitemap_leagues(),
        _build_sitemap_matches(),
        _build_sitemap_markets(),
    ]
    locs = []
    for xml in shards:
        # Pull <url>...</url> blocks
        start = 0
        while True:
            i = xml.find("<url>", start)
            if i < 0:
                break
            j = xml.find("</url>", i)
            if j < 0:
                break
            locs.append(xml[i:j + 6])
            start = j + 6
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{''.join(locs)}</urlset>"
    )


def _cached_sitemap_body(name: str, builder) -> str:
    os.makedirs(SITEMAP_CACHE_DIR, exist_ok=True)
    path = os.path.join(SITEMAP_CACHE_DIR, f"{name}.xml")
    try:
        age = datetime.datetime.now(datetime.timezone.utc).timestamp() - os.path.getmtime(path)
        if age <= SITEMAP_CACHE_TTL_SECONDS:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
    except OSError:
        pass
    xml = builder()
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(xml)
    except OSError:
        pass
    return xml


@app.route("/sitemap.xml")
def sitemap_xml():
    """Sitemap index pointing at static / leagues / matches / markets shards."""
    today = datetime.date.today().isoformat()
    xml = _sitemap_index([
        ("/sitemap-static.xml", today),
        ("/sitemap-leagues.xml", today),
        ("/sitemap-matches.xml", today),
        ("/sitemap-markets.xml", today),
    ])
    return Response(xml, mimetype="application/xml")


@app.route("/sitemap-static.xml")
def sitemap_static_xml():
    return Response(_cached_sitemap_body("static", _build_sitemap_static), mimetype="application/xml")


@app.route("/sitemap-leagues.xml")
def sitemap_leagues_xml():
    return Response(_cached_sitemap_body("leagues", _build_sitemap_leagues), mimetype="application/xml")


@app.route("/sitemap-matches.xml")
def sitemap_matches_xml():
    return Response(_cached_sitemap_body("matches", _build_sitemap_matches), mimetype="application/xml")


@app.route("/sitemap-markets.xml")
def sitemap_markets_xml():
    return Response(_cached_sitemap_body("markets", _build_sitemap_markets), mimetype="application/xml")


def board_updated_ist():
    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    return datetime.datetime.now(ist).strftime("%d %b %Y, %H:%M IST")


@app.context_processor
def inject_current_year():
    return {
        "current_year": datetime.date.today().year,
        "board_updated_ist": board_updated_ist(),
    }


@app.context_processor
def inject_today_picks_nav():
    return {"today_picks_sports": today_picks.TODAY_SPORTS}


DEFAULT_INSTAGRAM_URL = "https://instagram.com/degen_predictions467"
DEFAULT_X_URL = "https://x.com/degenprediction"
DEFAULT_CONTACT_EMAIL = "info@degenpredictions.com"


@app.context_processor
def inject_telegram_channel():
    """Public invite URL for CTAs. Env overrides; default is the live channel invite."""
    url = (os.environ.get("TELEGRAM_CHANNEL_URL") or "").strip()
    if not url:
        url = getattr(daily_digest, "DEFAULT_TELEGRAM_CHANNEL_URL", "") or ""
    ig = (os.environ.get("INSTAGRAM_URL") or "").strip() or DEFAULT_INSTAGRAM_URL
    x_url = (os.environ.get("X_URL") or os.environ.get("TWITTER_URL") or "").strip() or DEFAULT_X_URL
    email = (os.environ.get("CONTACT_EMAIL") or "").strip() or DEFAULT_CONTACT_EMAIL
    return {
        "telegram_channel_url": url or None,
        "instagram_url": ig,
        "x_url": x_url,
        "contact_email": email,
    }


@app.context_processor
def inject_fixture_picker():
    # Previously loaded full prediction CSVs on every HTML render for a picker
    # that is no longer in templates -- that alone added hundreds of ms/request.
    league_key = (request.view_args or {}).get("league_key", DEFAULT_LEAGUE)
    if league_key not in LEAGUES:
        league_key = DEFAULT_LEAGUE
    return {
        "picker_fixtures": [],
        "current_league_key": league_key,
        "all_leagues": LEAGUES,
    }


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
        line, pick = pred["total_runs_line"], pred["total_runs_pick"]
        runs_correct = (actual_total_runs > line) if pick == "Over" else (actual_total_runs < line)
        home_line, home_pick = pred["home_runs_line"], pred["home_runs_pick"]
        away_line, away_pick = pred["away_runs_line"], pred["away_runs_pick"]
        home_runs_correct = (actual_home_runs > home_line) if home_pick == "Over" else (actual_home_runs < home_line)
        away_runs_correct = (actual_away_runs > away_line) if away_pick == "Over" else (actual_away_runs < away_line)
        rows.append({
            "date": g["Date"], "home": home_code, "away": away_code,
            "predicted_winner": pred["favourite"], "actual_winner": actual_winner,
            "winner_correct": pred["favourite"] == actual_winner,
            "total_runs_line": line, "total_runs_pick": pick,
            "actual_total_runs": actual_total_runs, "runs_correct": runs_correct,
            "home_runs_line": home_line, "home_runs_pick": home_pick,
            "away_runs_line": away_line, "away_runs_pick": away_pick,
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
        line, pick = pred["total_runs_line"], pred["total_runs_pick"]
        runs_correct = (actual_total_runs > line) if pick == "Over" else (actual_total_runs < line)
        home_line, home_pick = pred["home_runs_line"], pred["home_runs_pick"]
        away_line, away_pick = pred["away_runs_line"], pred["away_runs_pick"]
        home_runs_correct = (actual_home_runs > home_line) if home_pick == "Over" else (actual_home_runs < home_line)
        away_runs_correct = (actual_away_runs > away_line) if away_pick == "Over" else (actual_away_runs < away_line)
        rows.append({
            "date": g["Date"],
            "home": team_names.get(home_id, home_id), "away": team_names.get(away_id, away_id),
            "predicted_winner": pred["favourite_name"], "actual_winner": actual_winner_name,
            "winner_correct": pred["favourite_name"] == actual_winner_name,
            "total_runs_line": line, "total_runs_pick": pick,
            "actual_total_runs": actual_total_runs, "runs_correct": runs_correct,
            "home_runs_line": home_line, "home_runs_pick": home_pick,
            "away_runs_line": away_line, "away_runs_pick": away_pick,
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


def _admin_page_ctx(admin_section="tools"):
    """Shared chrome vars for admin_base.html pages outside /admin."""
    return {
        "admin_section": admin_section,
        "visitor_filter": "all",
        "feedback_count": feedback.count_feedback(),
    }


def _admin_picks_filters_from_request():
    """Parse Today's Picks / Best Combos admin board filters."""
    sport = (request.args.get("sport") or "").strip().lower()
    if sport not in ("", "soccer", "baseball", "cricket"):
        sport = ""
    league = (request.args.get("league") or "").strip()
    market = (request.args.get("market") or "").strip()
    side = (request.args.get("side") or "").strip().lower()
    if side not in ("", "all", "over", "under", "other"):
        side = ""
    status = (request.args.get("status") or "").strip().lower()
    if status not in ("", "all", "upcoming", "live", "final"):
        status = ""
    min_conf_raw = (request.args.get("min_conf") or "").strip()
    min_conf = None
    if min_conf_raw:
        try:
            min_conf = float(min_conf_raw)
        except ValueError:
            min_conf = None
    return {
        "sport": sport or None,
        "league": league or None,
        "market": market or None,
        "side": side or None,
        "status": status or None,
        "min_conf": min_conf,
    }


def _empty_combo_summary(*, days=30):
    return {
        "days": days, "won": 0, "lost": 0, "pending": 0, "void": 0,
        "decided": 0, "hit_rate": None, "total": 0,
    }


def _stamp_admin_geo(rows, *, first_last=False):
    if not rows:
        return
    geoip.prefetch_locations([r.get("ip") for r in rows], max_workers=6, max_lookups=12)
    for row in rows:
        if first_last:
            row["first_seen_ist"] = _to_ist(row.get("first_seen"))
            row["last_seen_ist"] = _to_ist(row.get("last_seen"))
        else:
            row["ts_ist"] = _to_ist(row.get("ts"))
        row["location"] = geoip.cached_location(row.get("ip"))


def _load_board_monitor() -> dict:
    path = os.path.join(
        os.path.dirname(WEBAPP_DIR), "data", "feedback", "post_event.json",
    )
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    """Branded admin sign-in (session). Basic Auth still works for scripts."""
    if not ADMIN_USER or not ADMIN_PASSWORD:
        abort(404)
    if not _admin_ip_allowed():
        abort(403)
    next_url = _admin_next_url()
    if _is_admin_request():
        return redirect(next_url)
    # Mid-2FA: bounce to the OTP form instead of showing password again.
    if session.get("admin_2fa_pending"):
        return redirect(f"/admin/2fa?next={quote(next_url, safe='/?&=')}")

    error = None
    if request.method == "POST":
        validate_csrf_or_abort()
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        actor = None
        role = None
        if _credentials_match(username, ADMIN_USER) and _credentials_match(password, ADMIN_PASSWORD):
            actor, role = ADMIN_USER or "admin", "full"
        else:
            try:
                import admin_roles
                import admin_security_cfg
                op = admin_roles.find_operator(admin_security_cfg.load(), username)
                if op and admin_roles.verify_password(op.get("password_hash") or "", password):
                    actor, role = op["username"], op["role"]
            except Exception:
                logging.getLogger(__name__).exception("operator login failed")
        ok = bool(actor and role)
        try:
            import admin_audit
            admin_audit.log_event(
                "login_password",
                ok=ok,
                ip=get_client_ip(),
                actor=actor or username or "unknown",
                detail="ok" if ok else "bad credentials",
            )
        except Exception:
            pass
        if ok:
            import admin_2fa
            dest = _admin_next_url(request.form.get("next"))
            if admin_2fa.is_enabled():
                session["admin_2fa_pending"] = True
                session["admin_2fa_next"] = dest
                session["admin_2fa_fails"] = 0
                session["admin_2fa_actor"] = actor
                session["admin_2fa_role"] = role
                session.pop("admin_authenticated", None)
                session.permanent = False
                get_csrf_token()
                return redirect(f"/admin/2fa?next={quote(dest, safe='/?&=')}")
            _mark_admin_session(actor=actor, role=role)
            get_csrf_token()
            return redirect(dest)
        error = "Invalid username or password."

    get_csrf_token()
    resp = make_response(
        render_template(
            "admin_login.html",
            error=error,
            next_url=next_url,
        )
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/admin/2fa", methods=["GET", "POST"])
def admin_2fa_verify():
    """Second factor after password — TOTP or backup code."""
    if not ADMIN_USER or not ADMIN_PASSWORD:
        abort(404)
    import admin_2fa
    if not admin_2fa.is_enabled():
        return redirect("/admin/login")
    if _is_admin_request():
        return redirect(_admin_next_url())
    if not session.get("admin_2fa_pending"):
        return redirect("/admin/login")

    next_url = _admin_next_url(session.get("admin_2fa_next") or request.args.get("next"))
    error = None
    fails = int(session.get("admin_2fa_fails") or 0)
    if fails >= 8:
        _clear_admin_session()
        return redirect("/admin/login")

    if request.method == "POST":
        validate_csrf_or_abort()
        code = (request.form.get("code") or "").strip()
        if admin_2fa.verify_and_consume(code):
            actor = session.pop("admin_2fa_actor", None) or (ADMIN_USER or "admin")
            role = session.pop("admin_2fa_role", None) or "full"
            _mark_admin_session(actor=actor, role=role)
            session.pop("admin_2fa_pending", None)
            session.pop("admin_2fa_fails", None)
            dest = _admin_next_url(request.form.get("next") or session.pop("admin_2fa_next", None))
            get_csrf_token()
            try:
                import admin_audit
                admin_audit.log_event("login_2fa", ok=True, ip=get_client_ip(), actor=actor, detail="verified")
            except Exception:
                pass
            return redirect(dest)
        fails += 1
        session["admin_2fa_fails"] = fails
        try:
            import admin_audit
            admin_audit.log_event("login_2fa", ok=False, ip=get_client_ip(), detail=f"fail #{fails}")
        except Exception:
            pass
        left = max(0, 8 - fails)
        error = (
            "Invalid code."
            if left > 3
            else f"Invalid code — {left} attempt{'s' if left != 1 else ''} left before re-login."
        )
        if fails >= 8:
            _clear_admin_session()
            return redirect("/admin/login")

    get_csrf_token()
    resp = make_response(
        render_template(
            "admin_2fa.html",
            error=error,
            next_url=next_url,
            attempts_left=max(0, 8 - fails),
        )
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/admin/logout", methods=["GET", "POST"])
def admin_logout():
    if request.method == "POST":
        validate_csrf_or_abort()
    _clear_admin_session()
    return redirect("/admin/login")


@app.route("/admin/2fa/setup", methods=["POST"])
@admin_required
@admin_perm("security")
@csrf_protect
def admin_2fa_setup_start():
    import admin_2fa
    account = (ADMIN_USER or "admin").strip() or "admin"
    admin_2fa.start_setup(account_name=account)
    return redirect(url_for("admin_dashboard", section="security", tfa="setup"))


@app.route("/admin/2fa/confirm", methods=["POST"])
@admin_required
@admin_perm("security")
@csrf_protect
def admin_2fa_setup_confirm():
    import admin_2fa
    result = admin_2fa.confirm_setup(request.form.get("code") or "")
    if not result.get("ok"):
        return redirect(url_for(
            "admin_dashboard",
            section="security",
            tfa="err",
            tfam=(result.get("error") or "confirm failed")[:180],
        ))
    codes = result.get("backup_codes") or []
    session["admin_2fa_backup_codes"] = codes
    return redirect(url_for("admin_dashboard", section="security", tfa="enabled"))


@app.route("/admin/2fa/cancel-setup", methods=["POST"])
@admin_required
@admin_perm("security")
@csrf_protect
def admin_2fa_setup_cancel():
    import admin_2fa
    admin_2fa.cancel_setup()
    return redirect(url_for("admin_dashboard", section="security", tfa="cancelled"))


@app.route("/admin/2fa/disable", methods=["POST"])
@admin_required
@admin_perm("security")
@csrf_protect
def admin_2fa_disable():
    import admin_2fa
    result = admin_2fa.disable(request.form.get("code") or "")
    if not result.get("ok"):
        return redirect(url_for(
            "admin_dashboard",
            section="security",
            tfa="err",
            tfam=(result.get("error") or "disable failed")[:180],
        ))
    session.pop("admin_2fa_backup_codes", None)
    return redirect(url_for("admin_dashboard", section="security", tfa="disabled"))


@app.route("/admin/2fa/regenerate-backups", methods=["POST"])
@admin_required
@admin_perm("security")
@csrf_protect
def admin_2fa_regen_backups():
    import admin_2fa
    result = admin_2fa.regenerate_backups(request.form.get("code") or "")
    if not result.get("ok"):
        return redirect(url_for(
            "admin_dashboard",
            section="security",
            tfa="err",
            tfam=(result.get("error") or "regen failed")[:180],
        ))
    session["admin_2fa_backup_codes"] = result.get("backup_codes") or []
    return redirect(url_for("admin_dashboard", section="security", tfa="backups"))


@app.route("/admin/security/save", methods=["POST"])
@admin_required
@admin_perm("security")
@csrf_protect
def admin_security_save():
    import admin_security_cfg
    import admin_audit
    saved = admin_security_cfg.apply_form(request.form)
    admin_audit.log_event(
        "security_save",
        ok=True,
        ip=get_client_ip(),
        detail=f"idle={saved.get('idle_timeout_minutes')} ips={len(saved.get('ip_allowlist') or [])}",
    )
    return redirect(url_for("admin_dashboard", section="security", tfa="policy"))


@app.route("/admin/security/logout-all", methods=["POST"])
@admin_required
@admin_perm("security")
@csrf_protect
def admin_security_logout_all():
    import admin_security_cfg
    import admin_audit
    epoch = admin_security_cfg.bump_session_epoch()
    admin_audit.log_event("logout_all", ok=True, ip=get_client_ip(), detail=f"epoch={epoch}")
    _clear_admin_session()
    return redirect("/admin/login")


@app.route("/admin/ops/job", methods=["POST"])
@admin_required
@csrf_protect
def admin_ops_job():
    import admin_ops
    import admin_audit
    job = (request.form.get("job") or "").strip()
    # Dangerous / publish jobs need stronger perms.
    need = "ops"
    if job in ("probe_sources", "heal_sources", "clear_mlb_cache", "clear_nfl_cache", "clear_soccer_cache",
               "alert_down_now", "clear_rankings_cache", "refresh_asset_logos", "alert_stale_rankings"):
        need = "ops_safe"
    elif job in ("build_digest", "settle_due_results", "refresh_accuracy"):
        need = "ops"
    if not _admin_has_perm(need):
        abort(403)
    result = admin_ops.run_job(job)
    admin_audit.log_event(
        "ops_job",
        ok=bool(result.get("ok")),
        ip=get_client_ip(),
        actor=session.get("admin_actor") or "admin",
        detail=f"{job}: {result.get('detail') or result.get('error') or ''}"[:240],
        meta={"job": job},
    )
    msg = result.get("detail") or result.get("error") or job
    return redirect(url_for(
        "admin_dashboard",
        section="tools",
        job="ok" if result.get("ok") else "err",
        jobm=str(msg)[:200],
    ))


@app.route("/admin/ops/upload-rankings", methods=["POST"])
@admin_required
@csrf_protect
def admin_ops_upload_rankings():
    """Replace ranked_assets.csv from Admin → Tools (stocks/crypto board)."""
    if not _admin_has_perm("ops"):
        abort(403)
    import admin_audit
    import investment_sources
    upload = request.files.get("csv_file")
    if upload is None or not (upload.filename or "").strip():
        return redirect(url_for(
            "admin_dashboard", section="tools", job="err", jobm="no CSV file selected",
        ))
    raw = upload.read()
    result = investment_sources.replace_ranked_csv(raw, filename=upload.filename or "ranked_assets.csv")
    admin_audit.log_event(
        "upload_rankings",
        ok=bool(result.get("ok")),
        ip=get_client_ip(),
        actor=session.get("admin_actor") or "admin",
        detail=(result.get("detail") or result.get("error") or "")[:240],
    )
    if result.get("ok"):
        try:
            import indexnow
            indexnow.submit_urls([
                f"{SITE_URL}/crypto",
                f"{SITE_URL}/stocks",
                f"{SITE_URL}/stocks/us",
                f"{SITE_URL}/stocks/india",
            ])
        except Exception:
            logging.getLogger(__name__).exception("indexnow after rankings upload failed")
    return redirect(url_for(
        "admin_dashboard",
        section="tools",
        job="ok" if result.get("ok") else "err",
        jobm=str(result.get("detail") or result.get("error") or "upload")[:200],
    ))


@app.route("/admin/security/operator", methods=["POST"])
@admin_required
@admin_perm("security")
@csrf_protect
def admin_security_operator():
    import admin_roles
    import admin_security_cfg
    import admin_audit
    action = (request.form.get("action") or "upsert").strip()
    cfg = admin_security_cfg.load()
    if action == "delete":
        result = admin_roles.delete_operator(cfg, request.form.get("username") or "")
    else:
        result = admin_roles.upsert_operator(
            cfg,
            username=request.form.get("username") or "",
            password=request.form.get("password") or "",
            role=request.form.get("role") or "viewer",
        )
    admin_audit.log_event(
        "operator_" + action,
        ok=bool(result.get("ok")),
        ip=get_client_ip(),
        detail=result.get("error") or result.get("username") or "",
    )
    return redirect(url_for(
        "admin_dashboard",
        section="security",
        tfa="ok" if result.get("ok") else "err",
        tfam=(result.get("error") or "operator updated")[:180],
    ))


@app.route("/admin/security/revoke-session", methods=["POST"])
@admin_required
@admin_perm("security")
@csrf_protect
def admin_security_revoke_session():
    import admin_security_cfg
    import admin_audit
    sid = (request.form.get("session_id") or "").strip()
    mine = session.get("admin_session_id")
    ok = admin_security_cfg.revoke_session(sid)
    admin_audit.log_event("session_revoke", ok=ok, ip=get_client_ip(), detail=sid[:24])
    if sid and sid == mine:
        _clear_admin_session()
        return redirect("/admin/login")
    return redirect(url_for("admin_dashboard", section="security", tfa="sessions"))


@app.route("/admin/security/change-password", methods=["POST"])
@admin_required
@admin_perm("security")
@csrf_protect
def admin_security_change_password():
    """Change primary ADMIN_PASSWORD (production.env + process env)."""
    global ADMIN_PASSWORD
    import admin_audit
    import source_settings
    current = request.form.get("current_password") or ""
    new = request.form.get("new_password") or ""
    confirm = request.form.get("confirm_password") or ""
    if not _credentials_match(current, ADMIN_PASSWORD):
        return redirect(url_for("admin_dashboard", section="security", tfa="err", tfam="Current password is wrong"))
    if len(new) < 10:
        return redirect(url_for("admin_dashboard", section="security", tfa="err", tfam="New password must be at least 10 chars"))
    if new != confirm:
        return redirect(url_for("admin_dashboard", section="security", tfa="err", tfam="New passwords do not match"))
    ADMIN_PASSWORD = new
    os.environ["ADMIN_PASSWORD"] = new
    source_settings.write_production_env_keys({"ADMIN_PASSWORD": new})
    try:
        source_settings.apply_secrets_form(
            {"secret_ADMIN_PASSWORD": new},
            sync_production_env=True,
        )
    except Exception:
        pass
    admin_audit.log_event("password_change", ok=True, ip=get_client_ip(), detail="primary admin")
    # Other gunicorn workers (--workers 3) keep the old ADMIN_PASSWORD in
    # their own process memory until they restart or recycle
    # (--max-requests) -- same class of issue rotate-flask-secret already
    # warns about below. Login attempts landing on a stale worker in that
    # window will wrongly report "Current password is wrong".
    return redirect(url_for(
        "admin_dashboard",
        section="security",
        tfa="password",
        tfam="Password changed — restart degen-predictions.service so every worker picks it up immediately (otherwise it applies gradually as workers recycle).",
    ))


@app.route("/admin/security/rotate-flask-secret", methods=["POST"])
@admin_required
@admin_perm("security")
@csrf_protect
def admin_security_rotate_flask():
    import admin_audit
    import source_settings
    new = secrets.token_urlsafe(48)
    os.environ["FLASK_SECRET_KEY"] = new
    source_settings.write_production_env_keys({"FLASK_SECRET_KEY": new})
    try:
        source_settings.apply_secrets_form(
            {f"secret_FLASK_SECRET_KEY": new},
            sync_production_env=True,
        )
    except Exception:
        pass
    # App secret_key is set at import — note restart required.
    admin_audit.log_event("flask_secret_rotate", ok=True, ip=get_client_ip(), detail="restart required")
    return redirect(url_for(
        "admin_dashboard",
        section="security",
        tfa="flask",
        tfam="New Flask secret written — restart degen-predictions.service to apply",
    ))


@app.route("/admin")
@admin_required
def admin_dashboard():
    visitor_filter = request.args.get("filter", "all")
    if visitor_filter not in ("all", "human", "bot"):
        visitor_filter = "all"
    admin_section = request.args.get("section", "overview")
    if admin_section not in (
        "overview", "feedback", "traffic", "pages", "gp",
        "recurring", "visitors", "requests", "tools",
        "picks", "combos", "combo_results", "telegram", "degen_team", "twitter",
        "lifecycle", "sources", "security",
    ):
        admin_section = "overview"

    recent = []
    top_visitors = []
    recurring = []
    feedback_list = []
    summary = {
        "total_visits": 0, "unique_visitors": 0,
        "human_visits": 0, "bot_visits": 0,
        "today_visits": 0, "today_unique_visitors": 0,
        "today_human_visits": 0, "today_bot_visits": 0,
        "window_days": 30,
    }
    daily = []
    top_pages = []
    gp_stats = analytics._empty_gp_stats()
    try:
        feedback_count = feedback.count_feedback()
    except Exception:
        feedback_count = 0

    if admin_section in ("overview", "traffic"):
        try:
            summary = analytics.summary(days=30)
            daily = analytics.visits_per_day(days=14)
        except Exception:
            logging.getLogger(__name__).exception("admin traffic stats failed")
    if admin_section in ("overview", "gp"):
        try:
            gp_stats = analytics.get_prediction_stats(days=30, limit=50)
        except Exception:
            logging.getLogger(__name__).exception("admin get-prediction stats failed")
    if admin_section == "pages":
        try:
            top_pages = analytics.top_pages(days=30)
        except Exception:
            logging.getLogger(__name__).exception("admin top pages failed")
    if admin_section == "visitors":
        try:
            top_visitors = analytics.top_visitors(days=30, filter=visitor_filter)
            _stamp_admin_geo(top_visitors, first_last=True)
        except Exception:
            logging.getLogger(__name__).exception("admin top visitors failed")
    if admin_section == "recurring":
        try:
            recurring = analytics.recurring_visitors(days=30, filter=visitor_filter)
            _stamp_admin_geo(recurring, first_last=True)
        except Exception:
            logging.getLogger(__name__).exception("admin recurring visitors failed")
    if admin_section == "requests":
        try:
            recent = analytics.recent_visits(limit=100, filter=visitor_filter)
            _stamp_admin_geo(recent)
        except Exception:
            logging.getLogger(__name__).exception("admin recent visits failed")
    if admin_section == "feedback":
        try:
            feedback_list = feedback.list_feedback(limit=200)
            _stamp_admin_geo(feedback_list)
        except Exception:
            logging.getLogger(__name__).exception("admin feedback list failed")

    picks_filters = _admin_picks_filters_from_request()
    admin_pick_legs = []
    admin_combos_2 = []
    admin_combos_3 = []
    admin_mixed_2 = []
    admin_mixed_3 = []
    admin_pick_markets = []
    admin_pick_leagues = []
    if admin_section in ("picks", "combos", "lifecycle"):
        try:
            legs, combos = _gather_combo_picks_today(
                include_live=True, allow_blocking_rebuild=False,
            )
            admin_pick_markets = sorted({
                (leg.get("market") or "").strip()
                for leg in legs if (leg.get("market") or "").strip()
            })
            admin_pick_leagues = sorted({
                (leg.get("league_id"), leg.get("league_label") or leg.get("league_id"))
                for leg in legs if leg.get("league_id")
            }, key=lambda x: (x[1] or "").lower())
            admin_pick_legs = today_picks.filter_pick_legs(legs, **picks_filters)
            today_picks.analyze_slate_correlation(admin_pick_legs)
            for leg in admin_pick_legs:
                leg["ou_side"] = today_picks._leg_ou_side(leg)
            combo_kwargs = {
                "sport": picks_filters.get("sport"),
                "league": picks_filters.get("league"),
                "market": picks_filters.get("market"),
                "side": picks_filters.get("side"),
                "mix": request.args.get("mix") or None,
                "min_joint": request.args.get("min_joint") or None,
            }
            admin_combos_2 = today_picks.filter_combos(combos.get(2) or [], **combo_kwargs)
            admin_combos_3 = today_picks.filter_combos(combos.get(3) or [], **combo_kwargs)
            admin_mixed_2 = today_picks.filter_combos(combos.get("mixed_2") or [], **combo_kwargs)
            admin_mixed_3 = today_picks.filter_combos(combos.get("mixed_3") or [], **combo_kwargs)
        except Exception:
            logging.getLogger(__name__).exception("Admin picks/combos board failed")

    combo_results_summary = _empty_combo_summary()
    combo_results_slips = []
    combo_settle_stats = None
    if admin_section == "combo_results":
        try:
            if request.args.get("settle") == "1":
                combo_settle_stats = combo_results.settle_pending()
            combo_results_summary = combo_results.summary(days=30)
            status_filter = request.args.get("combo_status") or None
            if status_filter == "all":
                status_filter = None
            combo_results_slips = combo_results.list_slips(
                days=30, status=status_filter, limit=80,
            )
        except Exception:
            logging.getLogger(__name__).exception("Admin combo results failed")

    tg_board_legs = []
    tg_events = []
    tg_featured_combos = []
    tg_digest_picks = []
    tg_flash = request.args.get("tg") or ""
    tg_flash_msg = (request.args.get("tgm") or "").strip()[:240]
    if admin_section == "telegram":
        try:
            digest = _live_digest_payload()
            tg_board_legs = _admin_sendable_board_legs()
            tg_events = today_picks.group_admin_events_by_fixture(tg_board_legs)
            tg_digest_picks = [
                p for p in (digest.get("picks") or [])
                if isinstance(p, dict) and not daily_digest._leg_is_finished(p)
            ]
            for title, combo in daily_digest.iter_featured_combo_slips(digest):
                legs = [
                    lg for lg in (combo.get("legs") or []) if isinstance(lg, dict)
                ]
                if not legs or any(daily_digest._leg_is_finished(lg) for lg in legs):
                    continue
                tg_featured_combos.append({"title": title, "combo": combo})
        except Exception:
            logging.getLogger(__name__).exception("Admin telegram board failed")

    twitter_queue = {
        "date": "", "source": "digest", "sent_at": None, "posts": [], "count": 0,
    }
    if admin_section == "twitter":
        try:
            digest, board_legs = _live_digest_and_legs()
            prefer = request.args.get("source") != "digest"
            twitter_queue = daily_digest.twitter_queue(
                digest=digest, prefer_telegram=prefer, board_legs=board_legs,
            )
        except Exception:
            logging.getLogger(__name__).exception("Admin twitter queue failed")

    degen_team_items = {"combos": [], "singles": []}
    degen_team_summary = _empty_combo_summary()
    dt_flash = request.args.get("dt") or ""
    dt_flash_msg = (request.args.get("dtm") or "").strip()[:240]
    if admin_section == "degen_team":
        try:
            if request.args.get("settle") == "1":
                combo_results.settle_pending()
            degen_team_items = combo_results.list_degen_team_items(days=60, limit=80)
            degen_team_summary = combo_results.degen_team_summary(days=30)
        except Exception:
            logging.getLogger(__name__).exception("Admin degen team section failed")

    lifecycle_board = {
        "rows": [], "kpis": {}, "sports": [], "leagues": [], "waiting_due_count": 0,
    }
    lifecycle_filters = {
        "sport": (request.args.get("lc_sport") or "").strip().lower(),
        "league": (request.args.get("lc_league") or "").strip().lower(),
        "phase": (request.args.get("lc_phase") or "").strip().lower(),
        "settle": (request.args.get("lc_settle") or "").strip().lower(),
        "overdue": request.args.get("lc_overdue") == "1",
        "waiting": request.args.get("lc_waiting") == "1",
        "channel": request.args.get("lc_channel") == "1",
        "board": request.args.get("lc_board") == "1",
    }
    if admin_section == "lifecycle":
        try:
            if request.args.get("settle") == "1":
                combo_results.settle_pending()
            # Prefer unfiltered live legs so sport filters are applied in the board.
            board_legs = []
            try:
                raw_legs, _ = _gather_combo_picks_today(
                    include_live=True, allow_blocking_rebuild=False,
                )
                board_legs = list(raw_legs or [])
            except Exception:
                board_legs = list(admin_pick_legs or [])
            lifecycle_board = combo_results.build_lifecycle_board(
                board_legs=board_legs,
                sport=lifecycle_filters["sport"] or None,
                league=lifecycle_filters["league"] or None,
                phase=lifecycle_filters["phase"] or None,
                settle_status=lifecycle_filters["settle"] or None,
                overdue_only=lifecycle_filters["overdue"],
                waiting_result=lifecycle_filters["waiting"],
                channel_only=lifecycle_filters["channel"],
                board_only=lifecycle_filters["board"],
                days=14,
                limit=150,
            )
        except Exception:
            logging.getLogger(__name__).exception("Admin lifecycle board failed")

    source_settings_view = {}
    sources_flash = request.args.get("src") or ""
    sources_flash_msg = (request.args.get("srcm") or "").strip()[:240]
    if admin_section == "sources":
        try:
            import source_settings
            source_settings_view = source_settings.admin_view_model()
        except Exception:
            logging.getLogger(__name__).exception("Admin sources panel failed")

    admin_2fa_view = None
    admin_2fa_setup = None
    admin_2fa_flash = (request.args.get("tfa") or "").strip()
    admin_2fa_flash_msg = (request.args.get("tfam") or "").strip()[:180]
    admin_2fa_backup_codes = session.pop("admin_2fa_backup_codes", None) if admin_section == "security" else None
    admin_security_view = None
    admin_audit_rows = []
    admin_ops_health = None
    admin_operators = []
    admin_sessions = []
    admin_job_flash = (request.args.get("job") or "").strip()
    admin_job_flash_msg = (request.args.get("jobm") or "").strip()[:200]
    if admin_section == "security":
        try:
            import admin_2fa
            import admin_security_cfg
            import admin_audit
            import admin_roles
            admin_2fa_view = admin_2fa.status_view()
            admin_security_view = admin_security_cfg.load()
            admin_audit_rows = admin_audit.recent(60)
            admin_operators = admin_roles.public_operators(admin_security_view)
            admin_sessions = admin_security_cfg.list_sessions()
            if admin_2fa_view.get("setup_pending") or admin_2fa_flash == "setup":
                st = admin_2fa.load_state()
                pending = (st.get("pending_secret") or "").strip()
                if pending:
                    account = (ADMIN_USER or "admin").strip() or "admin"
                    admin_2fa_setup = {
                        "secret": pending,
                        "uri": admin_2fa.provisioning_uri(pending, account_name=account),
                        "qr_data_uri": admin_2fa.qr_data_uri(pending, account_name=account),
                        "account": account,
                        "issuer": admin_2fa.ISSUER,
                    }
        except Exception:
            logging.getLogger(__name__).exception("Admin 2FA panel failed")
    if admin_section == "tools":
        try:
            import admin_ops
            admin_ops_health = admin_ops.health_board()
        except Exception:
            logging.getLogger(__name__).exception("Admin ops health failed")

    return render_template(
        "admin_dashboard.html",
        summary=summary,
        daily=daily,
        gp_stats=gp_stats,
        top_pages=top_pages,
        top_visitors=top_visitors,
        recurring=recurring,
        recent=recent,
        feedback_list=feedback_list,
        feedback_count=feedback_count,
        visitor_filter=visitor_filter,
        admin_section=admin_section,
        leagues=LEAGUES,
        picks_filters=picks_filters,
        picks_mix=request.args.get("mix") or "",
        picks_min_joint=request.args.get("min_joint") or "",
        admin_pick_legs=admin_pick_legs,
        admin_combos_2=admin_combos_2,
        admin_combos_3=admin_combos_3,
        admin_mixed_2=admin_mixed_2,
        admin_mixed_3=admin_mixed_3,
        admin_pick_markets=admin_pick_markets,
        admin_pick_leagues=admin_pick_leagues,
        today_sports=today_picks.TODAY_SPORTS,
        max_combos=today_picks.MAX_COMBOS_PER_SIZE,
        max_mixed=today_picks.MAX_MIXED_COMBOS_PER_SIZE,
        combo_results_summary=combo_results_summary,
        combo_results_slips=combo_results_slips,
        combo_settle_stats=combo_settle_stats,
        combo_status_filter=request.args.get("combo_status") or "",
        tg_board_legs=tg_board_legs,
        tg_events=tg_events,
        tg_featured_combos=tg_featured_combos,
        tg_digest_picks=tg_digest_picks,
        tg_flash=tg_flash,
        tg_flash_msg=tg_flash_msg,
        twitter_queue=twitter_queue,
        degen_team_items=degen_team_items,
        degen_team_summary=degen_team_summary,
        dt_flash=dt_flash,
        dt_flash_msg=dt_flash_msg,
        board_monitor=_load_board_monitor(),
        lifecycle_board=lifecycle_board,
        lifecycle_filters=lifecycle_filters,
        source_settings=source_settings_view,
        sources_flash=sources_flash,
        sources_flash_msg=sources_flash_msg,
        admin_2fa=admin_2fa_view,
        admin_2fa_setup=admin_2fa_setup,
        admin_2fa_flash=admin_2fa_flash,
        admin_2fa_flash_msg=admin_2fa_flash_msg,
        admin_2fa_backup_codes=admin_2fa_backup_codes,
        admin_security_cfg=admin_security_view,
        admin_audit_rows=admin_audit_rows,
        admin_ops_health=admin_ops_health,
        admin_job_flash=admin_job_flash,
        admin_job_flash_msg=admin_job_flash_msg,
        admin_client_ip=get_client_ip(),
        admin_operators=admin_operators,
        admin_sessions=admin_sessions,
        admin_role=_current_admin_role(),
        admin_actor=session.get("admin_actor") or ADMIN_USER or "admin",
        admin_session_id=session.get("admin_session_id"),
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
        results_generated_at=today_picks.format_kickoff_ist(
            datetime.datetime.now(datetime.timezone.utc).isoformat()
        ),
        **_admin_page_ctx("results"),
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
    _refresh_after_events(min_interval=60)
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
@admin_required
def backtest(league_key):
    league = get_league_or_404(league_key)
    df = load_backtest(league_key)
    seasons = sorted(df["Season"].unique().tolist(), reverse=True) if not df.empty else []

    season_filter = request.args.get("season", "")
    team_filter = request.args.get("team", "").strip()
    # Default to out-of-sample — the honest holdout — unless the user picks All/In.
    if "sample" not in request.args:
        sample_filter = "out"
    else:
        sample_filter = request.args.get("sample", "")
    only_wrong = request.args.get("only_wrong", "")

    overall_summary = summarize_backtest(df)
    # OOS first in the template cards (honest holdout before in-sample).
    overall_summary_ordered = {}
    if overall_summary:
        for k, v in overall_summary.items():
            if "Out-of-Sample" in str(k):
                overall_summary_ordered[k] = v
        for k, v in overall_summary.items():
            if k not in overall_summary_ordered:
                overall_summary_ordered[k] = v

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
        only_wrong=only_wrong, overall_summary=overall_summary_ordered, total=len(matches),
        league_key=league_key, league_label=league["label"],
        **_admin_page_ctx("backtest"),
    )


@app.route("/<league_key>/about")
def about(league_key):
    league = get_league_or_404(league_key)
    return render_template(
        "about.html", league_key=league_key, league_label=league["label"],
    )


@app.route("/<league_key>/players")
@admin_required
def league_players(league_key):
    league = get_league_or_404(league_key)
    try:
        min_minutes = int(request.args.get("min_minutes", "200"))
    except ValueError:
        min_minutes = 200
    table = soccer_players.players_table(
        league_key,
        season=request.args.get("season") or None,
        team=request.args.get("team", "").strip(),
        position=request.args.get("position", "").strip(),
        q=request.args.get("q", "").strip(),
        sort=request.args.get("sort", "goals_added"),
        min_minutes=max(0, min_minutes),
    )
    return render_template(
        "soccer_players.html",
        league_key=league_key,
        league_label=league["label"],
        **table,
        **_admin_page_ctx("players"),
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)


def _warmup_background():
    """Prime combo caches once after boot without blocking request workers."""
    def _run():
        try:
            with app.app_context():
                # Single-flight refresh; other workers skip if lock/inflight is set.
                _spawn_combo_refresh(include_live=True)
                _spawn_combo_refresh(include_live=False)
        except Exception:
            pass

    threading.Thread(target=_run, name="warmup-combos", daemon=True).start()


_warmup_background()
