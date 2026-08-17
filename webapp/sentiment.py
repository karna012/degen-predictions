"""Team news-sentiment: free Google News RSS + local VADER scoring, same
approach as investment_scoring/src/sentiment.py (see that module's
docstring for the full rationale).

Cached in-memory with a long TTL (SENTIMENT_CACHE_TTL_SECONDS) -- team news
sentiment doesn't change meaningfully minute to minute, and the NPB
pitcher-name lookup incident earlier in this project (uncached per-request
external calls turning a fixtures page into a 15s load) is exactly the
failure mode this cache exists to avoid. A fixture/match page must never
block on a live news fetch.
"""
import logging
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

logger = logging.getLogger(__name__)

_analyzer = SentimentIntensityAnalyzer()
_cache = {}
_inflight = set()
_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="sentiment")
SENTIMENT_CACHE_TTL_SECONDS = 6 * 3600  # 6 hours -- team news doesn't need to be fresher than this
MAX_HEADLINES = 10
FETCH_TIMEOUT_SECONDS = 2.5
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


def _fetch_and_score(team_name):
    try:
        url = GOOGLE_NEWS_RSS.format(query=team_name.replace(" ", "+"))
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "DegenPredictions/1.0 (+https://degenpredictions.com)"},
        )
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as resp:
            raw = resp.read()
        feed = feedparser.parse(raw)
        headlines = [e.title for e in feed.entries[:MAX_HEADLINES]]
        if not headlines:
            return None
        scores = [_analyzer.polarity_scores(h)["compound"] for h in headlines]
        return sum(scores) / len(scores)
    except Exception as exc:
        logger.warning("Sentiment fetch failed for %r: %s", team_name, exc)
        return None


def _store(team_name, score):
    _cache[team_name] = {"score": score, "fetched_at": time.time()}
    _inflight.discard(team_name)


def _schedule_fetch(team_name):
    """Background fill -- never awaited from a request handler."""
    if team_name in _inflight:
        return
    _inflight.add(team_name)

    def _job():
        try:
            score = _fetch_and_score(team_name)
            _store(team_name, score)
        except Exception:
            _inflight.discard(team_name)

    _executor.submit(_job)


def get_team_sentiment(team_name):
    """Returns a float in [-1, 1], or None if no headlines were found /
    the fetch failed / cache is cold -- callers must treat None as "no signal",
    not 0.0 (which would look like real neutral sentiment).

    Cold misses schedule a background fetch and return immediately so page
    TTFB never waits on Google News RSS.
    """
    now = time.time()
    cached = _cache.get(team_name)
    if cached and now - cached["fetched_at"] < SENTIMENT_CACHE_TTL_SECONDS:
        return cached["score"]
    _schedule_fetch(team_name)
    return cached["score"] if cached else None


def prefetch_team_sentiments(team_names, max_workers=10):
    """Kick off background fetches for not-yet-cached teams.

    Does not block the caller -- previously this waited on a thread pool and
    could stall MLB/NPB boards for many seconds on a cold worker.
    """
    del max_workers  # kept for call-site compatibility
    now = time.time()
    for t in set(team_names):
        if not (t in _cache and now - _cache[t]["fetched_at"] < SENTIMENT_CACHE_TTL_SECONDS):
            _schedule_fetch(t)
