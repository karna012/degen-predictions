"""Live/upcoming NPB fixtures via spaia.jp's public schedule API -- same
"unofficial but widely used, read-only, no-auth" category as ESPN's
endpoints used elsewhere in this codebase. Degrades to an empty fixture
list rather than crashing if the endpoint is ever blocked/changed.

Unlike the MLB/tennis feeds, this single endpoint already covers both
history and live/upcoming fixtures for the current season (see
scripts/npb/npb_config.py for the full explanation), so there's no separate
historical archive to reconcile here.

Team names: the schedule endpoint's HomeTeamNameE/VisitorTeamNameE fields
are short nicknames only ("Tigers") -- TEAM_FULL_NAMES (full official
English franchise names) is used instead.

Pitcher names: this data source has no English name field at all for
players -- only PlayerName (kanji) and PlayerNameK (katakana reading) from
the player-info endpoint. There's no free romanization/translation service
wired in here, so PlayerNameK is deterministically transliterated to
English letters (Hepburn-style romaji) locally -- not a translation, just a
1:1 phonetic script conversion, which is what "English" can honestly mean
for a Japanese name without an actual localization API.
"""
import datetime
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "npb"))
from npb_config import TEAM_FULL_NAMES  # noqa: E402

SCHEDULE_URL = "https://spaia.jp/baseball/npb/api/schedules"
# Season ``schedules`` lags hours on GameState/HScore. This per-date feed
# has live/final runs (gameStateId 4 = 試合終了).
GAMES_BY_DATE_URL = "https://spaia.jp/baseball/npb/api/games_info_by_date"
PLAYER_URL = "https://spaia.jp/baseball/npb/api/player_by_team"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}
REGULAR_SEASON_GAME_KINDS = (1, 2)
CACHE_TTL_SECONDS = 2 * 60
LIVE_CACHE_TTL_SECONDS = 45
PLAYER_CACHE_TTL_SECONDS = 24 * 60 * 60  # a player's name never changes -- cache generously
_cache = {}
_player_cache = {}

# NPB's own official site (npb.jp) -- a genuine second vendor, independent of
# spaia.jp, used only when spaia is down (source_settings admin can also flip
# it to primary). Regular-season + Japan Series run March-November; the
# off-season months 404/are empty, so they're skipped rather than fetched.
NPBJP_SCHEDULE_URL = "https://npb.jp/games/{year}/schedule_{month:02d}_detail.html"
NPBJP_MONTHS = range(3, 12)
# npb.jp's schedule table uses each team's short kanji/katakana name (no
# English at all) -- mapped to spaia's own numeric team IDs so downstream
# code (TEAM_FULL_NAMES lookups, pitcher enrichment keyed by these IDs)
# doesn't need a second code path.
NPBJP_TEAM_IDS = {
    "巨人": 1, "ヤクルト": 2, "DeNA": 3, "中日": 4, "阪神": 5, "広島": 6,
    "西武": 7, "日本ハム": 8, "ロッテ": 9, "オリックス": 11, "ソフトバンク": 12, "楽天": 376,
}
_NPBJP_ROW_RE = re.compile(r'<tr id="date(\d{4})"[^>]*>(.*?)</tr>', re.DOTALL)
_NPBJP_FIELD_RE = {
    "team1": re.compile(r'<div class="team1">([^<]*)</div>'),
    "team2": re.compile(r'<div class="team2">([^<]*)</div>'),
    "score1": re.compile(r'<div class="score1">([^<]*)</div>'),
    "score2": re.compile(r'<div class="score2">([^<]*)</div>'),
    "time": re.compile(r'<div class="time">([^<]*)</div>'),
}

# Katakana -> Hepburn romaji, longest sequences first so digraphs (kya, sha,
# etc.) match before their component kana would.
_KATAKANA_DIGRAPHS = {
    "キャ": "kya", "キュ": "kyu", "キョ": "kyo", "シャ": "sha", "シュ": "shu", "ショ": "sho",
    "チャ": "cha", "チュ": "chu", "チョ": "cho", "ニャ": "nya", "ニュ": "nyu", "ニョ": "nyo",
    "ヒャ": "hya", "ヒュ": "hyu", "ヒョ": "hyo", "ミャ": "mya", "ミュ": "myu", "ミョ": "myo",
    "リャ": "rya", "リュ": "ryu", "リョ": "ryo", "ギャ": "gya", "ギュ": "gyu", "ギョ": "gyo",
    "ジャ": "ja", "ジュ": "ju", "ジョ": "jo", "ビャ": "bya", "ビュ": "byu", "ビョ": "byo",
    "ピャ": "pya", "ピュ": "pyu", "ピョ": "pyo", "ファ": "fa", "フィ": "fi", "フェ": "fe", "フォ": "fo",
    "ウィ": "wi", "ウェ": "we", "ウォ": "wo", "ヴァ": "va", "ヴィ": "vi", "ヴェ": "ve", "ヴォ": "vo",
    "ティ": "ti", "ディ": "di", "デュ": "dyu", "ツァ": "tsa", "ツィ": "tsi", "ツェ": "tse", "ツォ": "tso",
}
_KATAKANA_SINGLES = {
    "ア": "a", "イ": "i", "ウ": "u", "エ": "e", "オ": "o",
    "カ": "ka", "キ": "ki", "ク": "ku", "ケ": "ke", "コ": "ko",
    "ガ": "ga", "ギ": "gi", "グ": "gu", "ゲ": "ge", "ゴ": "go",
    "サ": "sa", "シ": "shi", "ス": "su", "セ": "se", "ソ": "so",
    "ザ": "za", "ジ": "ji", "ズ": "zu", "ゼ": "ze", "ゾ": "zo",
    "タ": "ta", "チ": "chi", "ツ": "tsu", "テ": "te", "ト": "to",
    "ダ": "da", "ヂ": "ji", "ヅ": "zu", "デ": "de", "ド": "do",
    "ナ": "na", "ニ": "ni", "ヌ": "nu", "ネ": "ne", "ノ": "no",
    "ハ": "ha", "ヒ": "hi", "フ": "fu", "ヘ": "he", "ホ": "ho",
    "バ": "ba", "ビ": "bi", "ブ": "bu", "ベ": "be", "ボ": "bo",
    "パ": "pa", "ピ": "pi", "プ": "pu", "ペ": "pe", "ポ": "po",
    "マ": "ma", "ミ": "mi", "ム": "mu", "メ": "me", "モ": "mo",
    "ヤ": "ya", "ユ": "yu", "ヨ": "yo",
    "ラ": "ra", "リ": "ri", "ル": "ru", "レ": "re", "ロ": "ro",
    "ワ": "wa", "ヲ": "wo", "ン": "n",
    "ッ": "", "ー": "",  # handled contextually below
}


def _katakana_to_romaji(text):
    if not text:
        return None
    text = text.strip()
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in (" ", "　"):
            out.append(" ")
            i += 1
            continue
        pair = text[i:i + 2]
        if pair in _KATAKANA_DIGRAPHS:
            out.append(_KATAKANA_DIGRAPHS[pair])
            i += 2
            continue
        if ch == "ッ" and i + 1 < len(text):
            # small tsu doubles the next consonant (e.g. ッカ -> "kka")
            nxt = text[i + 1:i + 3]
            nxt_roma = _KATAKANA_DIGRAPHS.get(nxt) or _KATAKANA_SINGLES.get(text[i + 1], "")
            if nxt_roma:
                out.append(nxt_roma[0])
            i += 1
            continue
        if ch == "ー" and out and out[-1]:
            out.append(out[-1][-1])  # long vowel mark repeats the previous vowel sound
            i += 1
            continue
        if ch in _KATAKANA_SINGLES:
            out.append(_KATAKANA_SINGLES[ch])
        else:
            return None  # hit something we don't recognize (kanji, punctuation) -- bail out
        i += 1
    romaji = "".join(out)
    return " ".join(word.capitalize() for word in romaji.split(" ") if word)


def _lookup_pitcher_name(player_id, fetch=True):
    if not player_id:
        return None
    now = time.time()
    cached = _player_cache.get(player_id)
    if cached and now - cached["fetched_at"] < PLAYER_CACHE_TTL_SECONDS:
        return cached["name"]
    if not fetch:
        return None
    try:
        resp = requests.get(PLAYER_URL, params={"person_info_id": player_id}, headers=HEADERS, timeout=3)
        resp.raise_for_status()
        data = resp.json()
        name = _katakana_to_romaji(data.get("PlayerNameK"))
    except Exception:
        name = None
    _player_cache[player_id] = {"name": name, "fetched_at": now}
    return name


def _has_live_event(games, today_str):
    # This endpoint doesn't expose a distinct "in progress" state (only
    # scheduled=1 / completed=2 / postponed-ish=3-5) in practice, so "today's
    # regular-season games without a final score yet" is used as the proxy
    # for "might be live right now" to decide the cache TTL.
    return any(
        str(g.get("DateJPN")) == today_str and g.get("GameState") == 1 and g.get("GameKindID") in REGULAR_SEASON_GAME_KINDS
        for g in games
    )


def _fetch_spaia_season(year, ttl):
    import feed_store
    data = feed_store.get_json(
        f"npb:season:{year}",
        SCHEDULE_URL,
        params={"Year": year},
        headers=HEADERS,
        timeout=15,
        retries=3,
        ttl=ttl,
        count_items=lambda d: len(d) if isinstance(d, list) else (len(d.get("data") or []) if isinstance(d, dict) else 0),
    )
    return data if data not in (None, [], {}) else []


def _parse_npbjp_month(html, year, month):
    rows = []
    for day_str, block in _NPBJP_ROW_RE.findall(html):
        day = int(day_str[2:])
        fields = {}
        for key, pattern in _NPBJP_FIELD_RE.items():
            m = pattern.search(block)
            fields[key] = (m.group(1) or "").strip() if m else ""
        home_id = NPBJP_TEAM_IDS.get(fields["team1"])
        away_id = NPBJP_TEAM_IDS.get(fields["team2"])
        if not home_id or not away_id:
            continue
        time_raw = fields["time"].replace("：", ":")
        hh, _, mm = time_raw.partition(":")
        time_jpn = f"{int(hh):02d}{int(mm):02d}" if hh.isdigit() and mm.isdigit() else "0000"

        def _score(raw):
            raw = raw.replace("\xa0", "").strip()
            return int(raw) if raw.isdigit() else None

        hscore, vscore = _score(fields["score1"]), _score(fields["score2"])
        completed = hscore is not None and vscore is not None
        rows.append({
            "GameKindID": 1,
            "DateJPN": int(f"{year}{month:02d}{day:02d}"),
            "TimeJPN": int(time_jpn),
            "HTeamID": home_id, "VTeamID": away_id,
            "HomeTeamNameE": fields["team1"], "VisitorTeamNameE": fields["team2"],
            "HScore": hscore, "VScore": vscore,
            "GameState": 2 if completed else 1,
            "Home_StarterPitcher_ID": None, "Visitor_StarterPitcher_ID": None,
        })
    return rows


def _fetch_npbjp_month(year, month):
    try:
        resp = requests.get(
            NPBJP_SCHEDULE_URL.format(year=year, month=month), headers=HEADERS, timeout=10,
        )
        resp.raise_for_status()
        # npb.jp sends no charset in Content-Type, so requests falls back to
        # ISO-8859-1 (mojibake on the kanji team names) instead of the page's
        # actual UTF-8 -- force it explicitly rather than trusting .encoding.
        resp.encoding = "utf-8"
        return _parse_npbjp_month(resp.text, year, month)
    except Exception:
        return []


def _fetch_npbjp_season(year, ttl=None):
    with ThreadPoolExecutor(max_workers=len(NPBJP_MONTHS)) as pool:
        chunks = list(pool.map(lambda m: _fetch_npbjp_month(year, m), NPBJP_MONTHS))
    return [row for chunk in chunks for row in chunk]


def _npb_fixtures_prefer_npbjp():
    try:
        import source_settings
        order = source_settings.provider_order("npb", kind="fixtures")
        return bool(order and order[0] == "npbjp")
    except Exception:
        return False


def _fetch_season(year):
    now = time.time()
    cached = _cache.get(year)
    today_str = datetime.datetime.now(datetime.timezone.utc).date().strftime("%Y%m%d")
    ttl = CACHE_TTL_SECONDS
    if cached:
        ttl = LIVE_CACHE_TTL_SECONDS if _has_live_event(cached["data"], today_str) else CACHE_TTL_SECONDS
        from live_cache_bust import is_fresh
        if is_fresh(cached["fetched_at"], ttl):
            return cached["data"]

    fetchers = (
        [_fetch_npbjp_season, _fetch_spaia_season] if _npb_fixtures_prefer_npbjp()
        else [_fetch_spaia_season, _fetch_npbjp_season]
    )
    for fetch in fetchers:
        try:
            data = fetch(year, ttl)
        except Exception:
            data = []
        if data:
            _cache[year] = {"data": data, "fetched_at": now}
            return data
    return cached["data"] if cached else []


# games_info_by_date.gameStateId (different enum from schedules.GameState)
_GAMES_INFO_FINAL = 4
_GAMES_INFO_STOPPED = 9  # postponed / called off


def _fetch_games_by_date(yyyymmdd: str) -> list:
    now = time.time()
    key = f"bydate:{yyyymmdd}"
    cached = _cache.get(key)
    from live_cache_bust import is_fresh
    if cached and is_fresh(cached["fetched_at"], LIVE_CACHE_TTL_SECONDS):
        return cached["data"]
    try:
        resp = requests.get(
            GAMES_BY_DATE_URL,
            params={"gameDate": yyyymmdd},
            headers=HEADERS,
            timeout=12,
        )
        resp.raise_for_status()
        data = resp.json() or []
        if not isinstance(data, list):
            data = []
        _cache[key] = {"data": data, "fetched_at": now}
        return data
    except Exception:
        if cached:
            return cached["data"]
        return []


def _live_score_map(start, today) -> dict:
    """(YYYYMMDD, home_id, away_id) -> games_info_by_date row."""
    out = {}
    days = []
    cur = start
    while cur <= today:
        days.append(cur.strftime("%Y%m%d"))
        cur += datetime.timedelta(days=1)
    if not days:
        return out
    with ThreadPoolExecutor(max_workers=min(8, len(days))) as pool:
        rows = list(pool.map(_fetch_games_by_date, days))
    for chunk in rows:
        for row in chunk or []:
            hid, aid = row.get("homeTeamId"), row.get("visitorTeamId")
            raw_day = row.get("gameDate")
            if hid is None or aid is None or not raw_day:
                continue
            out[(str(int(raw_day)), int(hid), int(aid))] = row
    return out


def _apply_games_info(fixture: dict, info: dict) -> None:
    state = info.get("gameStateId")
    hs, aws = info.get("homeTeamTotalRuns"), info.get("visitorTeamTotalRuns")
    has_score = hs is not None and aws is not None and str(hs) != "" and str(aws) != ""
    if state == _GAMES_INFO_FINAL and has_score:
        fixture["completed"] = True
        fixture["postponed"] = False
        fixture["is_live"] = False
        fixture["home_score"] = hs
        fixture["away_score"] = aws
        return
    if state == _GAMES_INFO_STOPPED:
        fixture["completed"] = False
        fixture["postponed"] = True
        fixture["is_live"] = False
        return
    if has_score:
        fixture["home_score"] = hs
        fixture["away_score"] = aws
        fixture["is_live"] = state != _GAMES_INFO_FINAL
        fixture["completed"] = False


def _jst_today():
    return datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=9))
    ).date()


def get_fixtures(days_back=7, days_ahead=10, resolve_pitchers=True):
    """Every NPB regular-season game in the given window, sorted chronologically.

    resolve_pitchers=False skips cold HTTP name lookups (uses cache hits only) so
    combo/home aggregators don't wait on dozens of pitcher requests.
    Window is JST-based so Japan night games are not clipped by UTC date.
    """
    try:
        import source_settings
        pref = source_settings.feed_preference("npb")
        if not pref.get("enabled", True):
            return []
        results_order = source_settings.provider_order("npb", kind="results")
    except Exception:
        results_order = ["spaia_games_info", "spaia_schedules"]

    today = _jst_today()
    start = today - datetime.timedelta(days=days_back)
    end = today + datetime.timedelta(days=days_ahead)

    years = {start.year, end.year}
    games = []
    for year in years:
        games.extend(_fetch_season(year))

    windowed_games = []
    for g in games:
        if g.get("GameKindID") not in REGULAR_SEASON_GAME_KINDS:
            continue
        date_raw = g.get("DateJPN")
        if not date_raw:
            continue
        try:
            game_date = datetime.datetime.strptime(str(int(date_raw)), "%Y%m%d").date()
        except ValueError:
            continue
        if not (start <= game_date <= end):
            continue
        windowed_games.append(g)

    # Pitcher name lookups are separate HTTP calls (this data source has no
    # bulk endpoint for it) -- fetch every not-yet-cached one in parallel
    # first, since sequentially awaiting ~100 individual requests made the
    # fixtures page take 15+ seconds to load.
    pitcher_ids = {
        pid for g in windowed_games
        for pid in (g.get("Home_StarterPitcher_ID"), g.get("Visitor_StarterPitcher_ID"))
        if pid
    }
    uncached_ids = [
        pid for pid in pitcher_ids
        if pid not in _player_cache or time.time() - _player_cache[pid]["fetched_at"] >= PLAYER_CACHE_TTL_SECONDS
    ]
    if resolve_pitchers and uncached_ids:
        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(_lookup_pitcher_name, uncached_ids))

    fixtures = []
    for g in windowed_games:
        game_date = datetime.datetime.strptime(str(int(g["DateJPN"])), "%Y%m%d").date()
        state = g.get("GameState")
        completed = state == 2
        time_raw = g.get("TimeJPN")
        time_str = f"{int(time_raw):04d}" if time_raw else "0000"
        iso_date = f"{game_date.isoformat()}T{time_str[:2]}:{time_str[2:]}:00+09:00"

        home_id, away_id = g.get("HTeamID"), g.get("VTeamID")

        # This data source has no distinct "in progress" per-game field
        # (only scheduled=1/completed=2/postponed-ish=3-5) -- "still
        # state=1 but its scheduled first-pitch time has already passed"
        # is the honest proxy for live, same idea as _has_live_event() used
        # for cache TTL, but computed per-fixture here so the fixtures page
        # can actually show a Live badge.
        #
        # The state field itself isn't always prompt: spotted a real case
        # where two games that started at the same time both should have
        # been long over, but one had already flipped to state=2
        # (completed) while the other was still sitting at state=1 over 4
        # hours after first pitch. Since an average NPB game finishes in
        # ~3h10m, this window is capped at 3.5 hours -- narrow enough that
        # a genuinely-over game whose state update lagged won't sit there
        # showing "Live" for hours, at the small cost of occasionally
        # dropping the badge a little early for a real extra-innings game.
        LIVE_WINDOW_HOURS = 3.5
        game_start_jst = datetime.datetime(
            game_date.year, game_date.month, game_date.day,
            int(time_str[:2]), int(time_str[2:]), tzinfo=datetime.timezone(datetime.timedelta(hours=9)),
        )
        now_jst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
        is_live = (
            state == 1 and game_start_jst <= now_jst
            and now_jst - game_start_jst <= datetime.timedelta(hours=LIVE_WINDOW_HOURS)
        )
        hs, aws = g.get("HScore"), g.get("VScore")
        has_score = hs is not None and aws is not None and str(hs) != "" and str(aws) != ""
        # State=2 can lag hours after the final; if scores exist past the
        # live window, treat as completed so combo settlement can grade.
        if (
            not completed
            and has_score
            and now_jst >= game_start_jst + datetime.timedelta(hours=LIVE_WINDOW_HOURS)
        ):
            completed = True

        fixtures.append({
            "date": iso_date,
            "home_team_id": home_id, "home_team": TEAM_FULL_NAMES.get(home_id, g.get("HomeTeamNameE")),
            "away_team_id": away_id, "away_team": TEAM_FULL_NAMES.get(away_id, g.get("VisitorTeamNameE")),
            "home_pitcher": _lookup_pitcher_name(g.get("Home_StarterPitcher_ID"), fetch=resolve_pitchers),
            "away_pitcher": _lookup_pitcher_name(g.get("Visitor_StarterPitcher_ID"), fetch=resolve_pitchers),
            "completed": completed,
            "postponed": state in (3, 4, 5),
            "is_live": is_live and not completed,
            "home_score": hs if (completed or has_score) else None,
            "away_score": aws if (completed or has_score) else None,
        })

    live_map = {}
    # Prefer live games_info overlay unless admin picks schedules-only results.
    use_games_info = True
    if results_order:
        use_games_info = results_order[0] != "spaia_schedules"
    if use_games_info:
        live_map = _live_score_map(start, today)
    for fx in fixtures:
        try:
            hid, aid = int(fx["home_team_id"]), int(fx["away_team_id"])
            day = (fx.get("date") or "")[:10].replace("-", "")
        except (TypeError, ValueError, KeyError):
            continue
        info = live_map.get((day, hid, aid))
        if info:
            _apply_games_info(fx, info)

    fixtures.sort(key=lambda m: m["date"] or "")
    return fixtures
