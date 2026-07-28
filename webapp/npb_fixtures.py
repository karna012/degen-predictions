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
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "npb"))
from npb_config import TEAM_FULL_NAMES  # noqa: E402

SCHEDULE_URL = "https://spaia.jp/baseball/npb/api/schedules"
PLAYER_URL = "https://spaia.jp/baseball/npb/api/player_by_team"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"}
REGULAR_SEASON_GAME_KINDS = (1, 2)
CACHE_TTL_SECONDS = 15 * 60
LIVE_CACHE_TTL_SECONDS = 60
PLAYER_CACHE_TTL_SECONDS = 24 * 60 * 60  # a player's name never changes -- cache generously
_cache = {}
_player_cache = {}

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


def _lookup_pitcher_name(player_id):
    if not player_id:
        return None
    now = time.time()
    cached = _player_cache.get(player_id)
    if cached and now - cached["fetched_at"] < PLAYER_CACHE_TTL_SECONDS:
        return cached["name"]
    try:
        resp = requests.get(PLAYER_URL, params={"person_info_id": player_id}, headers=HEADERS, timeout=8)
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


def _fetch_season(year):
    now = time.time()
    cached = _cache.get(year)
    today_str = datetime.datetime.now(datetime.timezone.utc).date().strftime("%Y%m%d")
    if cached:
        ttl = LIVE_CACHE_TTL_SECONDS if _has_live_event(cached["data"], today_str) else CACHE_TTL_SECONDS
        if now - cached["fetched_at"] < ttl:
            return cached["data"]
    try:
        resp = requests.get(SCHEDULE_URL, params={"Year": year}, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        _cache[year] = {"data": data, "fetched_at": now}
        return data
    except Exception:
        if cached:
            return cached["data"]
        return []


def get_fixtures(days_back=3, days_ahead=10):
    """Every NPB regular-season game in the given window, sorted chronologically."""
    today = datetime.datetime.now(datetime.timezone.utc).date()
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
    if uncached_ids:
        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(_lookup_pitcher_name, uncached_ids))

    fixtures = []
    for g in windowed_games:
        game_date = datetime.datetime.strptime(str(int(g["DateJPN"])), "%Y%m%d").date()
        state = g.get("GameState")
        completed = state == 2
        time_raw = g.get("TimeJPN")
        time_str = f"{int(time_raw):04d}" if time_raw else "0000"
        iso_date = f"{game_date.isoformat()}T{time_str[:2]}:{time_str[2:]}:00"

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

        fixtures.append({
            "date": iso_date,
            "home_team_id": home_id, "home_team": TEAM_FULL_NAMES.get(home_id, g.get("HomeTeamNameE")),
            "away_team_id": away_id, "away_team": TEAM_FULL_NAMES.get(away_id, g.get("VisitorTeamNameE")),
            "home_pitcher": _lookup_pitcher_name(g.get("Home_StarterPitcher_ID")),
            "away_pitcher": _lookup_pitcher_name(g.get("Visitor_StarterPitcher_ID")),
            "completed": completed,
            "postponed": state in (3, 4, 5),
            "is_live": is_live,
            "home_score": g.get("HScore") if completed else None,
            "away_score": g.get("VScore") if completed else None,
        })

    fixtures.sort(key=lambda m: m["date"] or "")
    return fixtures
