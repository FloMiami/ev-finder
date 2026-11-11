"""Client for The Odds API (v4) with local caching.

Free tier: 500 credits/month, no card required. Get a key at
https://the-odds-api.com/ and set ODDS_API_KEY.

Credit cost (per the docs, and confirmed in the response headers):
- Bulk game lines (h2h / spreads / totals) for one sport:
  1 credit per region per market. With regions=us and 3 markets,
  one sport costs 3 credits per refresh. Three sports = 9 credits.
  A daily refresh is ~270 credits/month — inside the free 500.
- Player props use the per-event endpoint, one call per event, and
  cost more. The exact cost is in the response headers
  (x-requests-remaining / x-requests-used), so this module reads
  them after every call and stops fetching props when the balance
  gets low. Props are OFF by default for that reason.

Cache: bulk odds are cached to data/ for 15 minutes so a page
refresh does not burn credits.
"""

import calendar
import json
import os
import time

import requests

BASE = "https://api.the-odds-api.com/v4"

# sport label -> Odds API sport key
SPORTS = {
    "nba": "basketball_nba",
    "ncaab": "basketball_ncaab",
    "nfl": "americanfootball_nfl",
}

GAME_MARKETS = "h2h,spreads,totals"

# Player-prop markets per sport (availability varies by book).
PROP_MARKETS = {
    "basketball_nba": (
        "player_points,player_rebounds,player_assists,player_threes,"
        "player_points_rebounds_assists,player_blocks,player_steals"
    ),
    "basketball_ncaab": "player_points",
    "americanfootball_nfl": (
        "player_pass_yards,player_rush_yards,player_receptions,"
        "player_reception_yards,player_pass_tds,player_anytime_td"
    ),
}

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CACHE_TTL_SECONDS = 15 * 60  # 15 minutes
# Stop fetching props once the monthly balance drops below this.
PROP_CREDIT_FLOOR = 50


def _cache_path(name):
    # The file where one sport's cached odds live.
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{name}.json")


def load_cache(name, max_age=None):
    """Return (data, age_seconds) or (None, None) if missing/stale.

    max_age overrides the 15 minute default. The app passes the daily
    refresh window here so a cold start can still serve the last good
    scan instead of paying for a fresh fetch.
    """
    path = _cache_path(name)
    if not os.path.exists(path):
        return None, None
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return None, None
    age = time.time() - payload.get("saved_at", 0)
    ttl = CACHE_TTL_SECONDS if max_age is None else max_age
    if age > ttl:
        return None, age
    return payload.get("data"), age


def save_cache(name, data):
    path = _cache_path(name)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"saved_at": time.time(), "data": data}, f)
    except OSError:
        pass


def _credits_from_headers(headers):
    # The API answers with the monthly balance in these two headers,
    # so every call reports what is left without a separate check.
    def _int(name):
        try:
            return int(headers.get(name, -1))
        except (TypeError, ValueError):
            return -1

    return _int("x-requests-remaining"), _int("x-requests-used")


def fetch_bulk_odds(api_key, sport_key, markets=GAME_MARKETS, regions="us"):
    """GET /v4/sports/{sport}/odds — game lines for a whole sport.

    Returns (events, credits_remaining, credits_used).
    Raises requests.HTTPError on failure.
    """
    url = f"{BASE}/sports/{sport_key}/odds"
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": markets,
        "oddsFormat": "american",
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    remaining, used = _credits_from_headers(resp.headers)
    return resp.json(), remaining, used


def fetch_event_props(api_key, sport_key, event_id, markets):
    """GET /v4/sports/{sport}/events/{eventId}/odds — props for one event.

    Returns (event_dict, credits_remaining, credits_used).
    """
    url = f"{BASE}/sports/{sport_key}/events/{event_id}/odds"
    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": markets,
        "oddsFormat": "american",
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    remaining, used = _credits_from_headers(resp.headers)
    return resp.json(), remaining, used


def upcoming_event_ids(events, within_hours=24):
    """Event ids starting within the next `within_hours` hours."""
    # Only near-term games get props, so the scan spends credits on
    # games about to start instead of next week's slate.
    now = time.time()
    ids = []
    for event in events or []:
        try:
            # commence_time is ISO 8601 UTC, e.g. 2026-11-11T23:00:00Z
            ts = calendar.timegm(
                time.strptime(event["commence_time"][:19], "%Y-%m-%dT%H:%M:%S")
            )
        except (KeyError, ValueError):
            continue
        if 0 <= ts - now <= within_hours * 3600:
            ids.append(event["id"])
    return ids
