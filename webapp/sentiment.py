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
from concurrent.futures import ThreadPoolExecutor

import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

logger = logging.getLogger(__name__)

_analyzer = SentimentIntensityAnalyzer()
_cache = {}
SENTIMENT_CACHE_TTL_SECONDS = 6 * 3600  # 6 hours -- team news doesn't need to be fresher than this
MAX_HEADLINES = 10
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


def _fetch_and_score(team_name):
    try:
        url = GOOGLE_NEWS_RSS.format(query=team_name.replace(" ", "+"))
        feed = feedparser.parse(url)
        headlines = [e.title for e in feed.entries[:MAX_HEADLINES]]
        if not headlines:
            return None
        scores = [_analyzer.polarity_scores(h)["compound"] for h in headlines]
        return sum(scores) / len(scores)
    except Exception as exc:
        logger.warning("Sentiment fetch failed for %r: %s", team_name, exc)
        return None


def get_team_sentiment(team_name):
    """Returns a float in [-1, 1], or None if no headlines were found /
    the fetch failed -- callers must treat None as "no signal", not 0.0
    (which would look like real neutral sentiment)."""
    now = time.time()
    cached = _cache.get(team_name)
    if cached and now - cached["fetched_at"] < SENTIMENT_CACHE_TTL_SECONDS:
        return cached["score"]
    score = _fetch_and_score(team_name)
    _cache[team_name] = {"score": score, "fetched_at": now}
    return score


def prefetch_team_sentiments(team_names, max_workers=10):
    """Resolves every not-yet-cached team's sentiment in parallel before
    the caller starts its own loop -- pages like /results that call
    get_team_sentiment() once per team across dozens of past games were
    paying for each fetch sequentially on a cold cache (e.g. right after a
    deploy restarts the worker process), the exact same slow-page-load
    failure mode the NPB pitcher-name and geoip lookups hit earlier in this
    project -- same fix applies here."""
    now = time.time()
    uncached = [
        t for t in set(team_names)
        if not (t in _cache and now - _cache[t]["fetched_at"] < SENTIMENT_CACHE_TTL_SECONDS)
    ]
    if not uncached:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        list(pool.map(get_team_sentiment, uncached))
