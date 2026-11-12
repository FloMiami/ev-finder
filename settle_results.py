"""Grade the plays in docs/plays-log.json against final results.

This is the second half of the results tracker. It runs each morning
before the scan (see .github/workflows/scan.yml): it reads the log,
finds every pending play whose game should be over, and marks it win,
loss or push, with the profit in units (flat 1 unit per play).

Results come from ESPN's free public scoreboard, not The Odds API:
scores cost Odds API credits, ESPN costs none, and ESPN also gives
the box scores player props need. Those endpoints are unofficial, so
every parse is defensive: anything that does not match cleanly stays
pending for the next run instead of being guessed at.

Rules, in plain language:
- A play is only graded once its game should be over (tip-off plus
  6 hours, so late West Coast games are done by the 6am CT run).
- Moneyline: your team wins -> win, loses -> loss, ties (NFL only)
  -> push, which is how books void a tied moneyline.
- Spread: your team's score plus the spread beats the other team ->
  win, loses -> loss, lands exactly on it -> push.
- Total: the combined score beats the line -> Over wins, falls short
  -> Under wins, lands exactly on the line -> push.
- Player props: the player's stat is read from the game's box score.
  Over the line -> win, under -> loss, exactly on it -> push. If the
  player is listed in the box score but did not play, the play is a
  push, because books void a prop on a player who never played. If
  the player is not in the box score at all, the play stays pending
  instead of being guessed at.
- A game that cannot be found, a postponed game, or a play the script
  cannot parse stays pending and gets a warning in the run log.

Run locally (test log, no key needed):
    PLAYS_LOG=/tmp/test-plays-log.json python settle_results.py

Run in the workflow:
    python settle_results.py
    -> updates docs/plays-log.json in place
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ev import MARKET_LABELS, american_to_decimal

# Where the log lives. The workflow uses the default; tests point it
# at a throwaway file with the PLAYS_LOG environment variable.
LOG_PATH = os.environ.get(
    "PLAYS_LOG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "plays-log.json"),
)
# The v5 model props live in their own log (same shape, separate
# file) so the forward-test record never mixes into the consensus
# record. The same settle run grades both.
MODEL_LOG_PATH = os.environ.get(
    "MODEL_PROPS_LOG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "docs", "model-props-log.json"),
)

# ESPN's public scoreboard and game summary. Free, no key. Unofficial,
# so the code below reads them defensively and never assumes a field.
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
LEAGUE = {
    "basketball_nba": "basketball/nba",
    "basketball_ncaab": "basketball/mens-college-basketball",
    "americanfootball_nfl": "football/nfl",
}
# The log stores the readable market label ("Total", "Player points");
# the script maps it back to the market key to know what to grade.
MARKET_BY_LABEL = {label: key for key, label in MARKET_LABELS.items()}

# Which box-score stat settles each prop market. The anytime-TD market
# is any touchdown the player scored himself (rushing or receiving);
# a touchdown he threw does not count, which is how books grade it.
PROP_STATS = {
    "player_points": ["points"],
    "player_rebounds": ["rebounds"],
    "player_assists": ["assists"],
    "player_threes": ["threePointFieldGoalsMade-threePointFieldGoalsAttempted"],
    "player_points_rebounds_assists": ["points", "rebounds", "assists"],
    "player_blocks": ["blocks"],
    "player_steals": ["steals"],
    "player_pass_yards": ["passingYards"],
    "player_rush_yards": ["rushingYards"],
    "player_receptions": ["receptions"],
    "player_reception_yards": ["receivingYards"],
    "player_pass_tds": ["passingTouchdowns"],
    "player_rush_tds": ["rushingTouchdowns"],
    "player_anytime_td": ["rushingTouchdowns", "receivingTouchdowns"],
}

# A game is only graded once it should be over. Tip-off plus this
# many hours covers even the longest overtime games.
SETTLE_DELAY = timedelta(hours=6)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EVFinder/1.0)"}


def norm(name):
    """Lowercase a name and strip everything but letters and digits,
    so 'Los Angeles Lakers' matches ESPN's 'Los Angeles Lakers'
    even if spacing or punctuation differs."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def fetch_json(url):
    """GET one JSON document from ESPN. Raises on a bad status."""
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json()


def espn_date(commence_time):
    """The date ESPN files a game under: the tip-off date in US
    Eastern time. A 7pm ET tip on Nov 10 is 2025-11-11T00:00Z in UTC,
    but ESPN's scoreboard lists it under 20251110."""
    dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
    return dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y%m%d")


def find_game(sport, home_name, away_name, commence_time):
    """Find one finished game on ESPN's scoreboard by both team names.
    Tries the tip-off date, then the next day in case the date math
    is off by one. Returns the event dict, or None when it cannot be
    found (the play stays pending)."""
    league = LEAGUE.get(sport)
    if not league:
        return None
    base = espn_date(commence_time)
    dates = [base]
    # The next day's slate, as a fallback for a date-boundary miss.
    next_day = (datetime.strptime(base, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    dates.append(next_day)
    for date in dates:
        try:
            data = fetch_json(f"{ESPN_BASE}/{league}/scoreboard?dates={date}")
        except Exception:  # noqa: BLE001 - one bad fetch, try the next date
            continue
        for event in data.get("events", []):
            comp = (event.get("competitions") or [{}])[0]
            teams = {}
            for c in comp.get("competitors", []):
                team = c.get("team", {})
                teams[c.get("homeAway")] = team.get("displayName", "")
            if norm(teams.get("home")) == norm(home_name) and norm(teams.get("away")) == norm(away_name):
                return event
    return None


def game_scores(event):
    """The final home and away scores, or None when the game is not
    finished yet. The status lives on the event itself."""
    status = event.get("status", {})
    if not status.get("type", {}).get("completed"):
        return None
    comp = (event.get("competitions") or [{}])[0]
    scores = {}
    for c in comp.get("competitors", []):
        try:
            scores[c.get("homeAway")] = float(c.get("score"))
        except (TypeError, ValueError):
            return None
    if "home" not in scores or "away" not in scores:
        return None
    return scores["home"], scores["away"]


def profit_for(book_price, status):
    """Profit in units on a flat 1-unit stake: a win pays the decimal
    odds minus 1, a loss is -1, a push is 0."""
    if status == "win":
        return round(american_to_decimal(book_price) - 1.0, 4)
    if status == "loss":
        return -1.0
    return 0.0


def settle_game_line(entry, home_score, away_score, home_name, away_name):
    """Grade a moneyline, spread or total from the final score.
    Returns win/loss/push, or None when the play cannot be parsed."""
    market = MARKET_BY_LABEL.get(entry["market"])
    outcome = entry["outcome"]
    if market == "h2h":
        # Moneyline: the outcome is just the team name.
        if norm(outcome) == norm(home_name):
            mine, theirs = home_score, away_score
        elif norm(outcome) == norm(away_name):
            mine, theirs = away_score, home_score
        else:
            return None
        if mine > theirs:
            return "win"
        if mine < theirs:
            return "loss"
        return "push"  # an NFL tie voids the moneyline
    if market == "spreads":
        # Spread: "Los Angeles Lakers -3.5" or "Charlotte Hornets +3.5".
        m = re.match(r"^(.*) ([+-]\d+(?:\.\d+)?)$", outcome)
        if not m:
            return None
        team, point = m.group(1), float(m.group(2))
        if norm(team) == norm(home_name):
            mine, theirs = home_score, away_score
        elif norm(team) == norm(away_name):
            mine, theirs = away_score, home_score
        else:
            return None
        if mine + point > theirs:
            return "win"
        if mine + point < theirs:
            return "loss"
        return "push"
    if market == "totals":
        # Total: "Over 224.5" or "Under 224.5".
        m = re.match(r"^(Over|Under) (\d+(?:\.\d+)?)$", outcome)
        if not m:
            return None
        total = home_score + away_score
        point = float(m.group(2))
        over = m.group(1) == "Over"
        if total == point:
            return "push"
        return "win" if (total > point) == over else "loss"
    return None


def fetch_boxscore(sport, event_id, cache):
    """One game's box score from ESPN's summary endpoint, cached per
    event id so several props on the same game cost one call."""
    key = (sport, event_id)
    if key in cache:
        return cache[key]
    league = LEAGUE.get(sport)
    if not league:
        return None
    try:
        data = fetch_json(f"{ESPN_BASE}/{league}/summary?event={event_id}")
    except Exception:  # noqa: BLE001 - a failed fetch leaves the play pending
        return None
    box = data.get("boxscore") or {}
    cache[key] = box
    return box


def player_stat(boxscore, player_name, stat_names):
    """One player's stat total from a box score, or None when the
    player did not play (or the stat is not tracked for that league).

    The box score nests the data two levels deep: each team's entry
    has a "statistics" list of stat groups, and each group has a
    "keys" list (the stat names) and an "athletes" list, with each
    athlete's "stats" values lining up with those keys. A player with
    didNotPlay set has an empty stats list, which is how a DNP is
    told apart from a quiet night (a player who played and got 0 of
    something still has a "0"). A value like "5-15" is a
    made-attempted pair, so the made part (the number before the
    dash) is what counts.

    Returns the stat total, the string "DNP" when the player is
    listed but did not play, or None when the player is not in the
    box score at all (or the stat is not tracked for that league) —
    in that case the play stays pending instead of being guessed at."""
    want = norm(player_name)
    found = False
    stat_seen = False
    total = 0.0
    for side in boxscore.get("players", []):
        groups = side.get("statistics") or []
        if isinstance(groups, dict):
            groups = [groups]
        for block in groups:
            keys = block.get("keys", [])
            for row in block.get("athletes", []):
                info = row.get("athlete", {})
                if norm(info.get("displayName")) != want and norm(info.get("fullName")) != want:
                    continue
                if row.get("didNotPlay") or not row.get("stats"):
                    return "DNP"  # listed but did not play -> push
                found = True
                values = row.get("stats", [])
                for stat in stat_names:
                    if stat not in keys:
                        continue
                    stat_seen = True
                    raw = values[keys.index(stat)]
                    if raw in (None, "", "-"):
                        continue
                    if isinstance(raw, str) and re.match(r"^-?\d+-\d+$", raw.strip()):
                        raw = raw.strip().split("-")[0]  # "5-15" -> 5 made
                    try:
                        total += float(raw)
                    except (TypeError, ValueError):
                        continue
    if not found or not stat_seen:
        return None
    return total


def settle_prop(entry, boxscore):
    """Grade a player prop from the box score. Returns win/loss/push,
    or None when it cannot be graded (play stays pending). A player
    missing from the box score is a push, not a loss."""
    market = MARKET_BY_LABEL.get(entry["market"])
    stat_names = PROP_STATS.get(market)
    if not stat_names or boxscore is None:
        return None
    outcome = entry["outcome"]
    if market == "player_anytime_td":
        # Anytime TD: "Saquon Barkley Yes" or "Saquon Barkley No".
        m = re.match(r"^(.*) (Yes|No)$", outcome)
        if not m:
            return None
        value = player_stat(boxscore, m.group(1), stat_names)
        if value == "DNP":
            return "push"
        if value is None:
            return None
        scored = value >= 1
        return "win" if scored == (m.group(2) == "Yes") else "loss"
    m = re.match(r"^(.*) (Over|Under) (\d+(?:\.\d+)?)$", outcome)
    if not m:
        return None
    value = player_stat(boxscore, m.group(1), stat_names)
    if value == "DNP":
        return "push"
    if value is None:
        return None
    point = float(m.group(3))
    over = m.group(2) == "Over"
    if value == point:
        return "push"
    return "win" if (value > point) == over else "loss"


def split_event(event_name):
    """The Odds API writes games as 'Away @ Home'."""
    parts = event_name.split(" @ ", 1)
    if len(parts) == 2:
        return parts[1], parts[0]  # home, away
    return None, None


def settle_entry(entry, box_cache):
    """Grade one pending log entry. Returns win/loss/push, or None
    when the play cannot be graded yet (it stays pending)."""
    home_name, away_name = split_event(entry["event"])
    if not home_name or not away_name:
        return None
    event = find_game(entry["sport"], home_name, away_name, entry.get("commence_time") or "")
    if event is None:
        return None
    scores = game_scores(event)
    if scores is None:
        return None
    home_score, away_score = scores
    market = MARKET_BY_LABEL.get(entry["market"])
    if market in ("h2h", "spreads", "totals"):
        return settle_game_line(entry, home_score, away_score, home_name, away_name)
    if market in PROP_STATS:
        box = fetch_boxscore(entry["sport"], event.get("id"), box_cache)
        return settle_prop(entry, box)
    return None


def settle_log(log_path):
    """Grade every pending play in one log file whose game should be
    over, and write the log back. Prints what settled and what is
    still waiting, so the workflow run log shows it. A missing log
    file is not an error — it just means no plays of that kind were
    ever logged."""
    if not os.path.exists(log_path):
        print(f"no log file at {log_path}, nothing to settle")
        return
    with open(log_path, encoding="utf-8") as f:
        log = json.load(f)
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    box_cache = {}
    settled = {"win": 0, "loss": 0, "push": 0}
    skipped = 0
    changed = False
    for entry in log:
        if entry.get("status") != "pending":
            continue
        # Too recent to be over yet: leave it for tomorrow's run.
        try:
            tip = datetime.fromisoformat(
                (entry.get("commence_time") or "").replace("Z", "+00:00")
            )
        except ValueError:
            tip = None
        if tip is None or now - tip < SETTLE_DELAY:
            skipped += 1
            continue
        try:
            status = settle_entry(entry, box_cache)
        except Exception as exc:  # noqa: BLE001 - one bad play must not kill the run
            print(f"could not grade {entry['event']} / {entry['outcome']}: {exc}")
            skipped += 1
            continue
        if status is None:
            print(f"still pending: {entry['event']} / {entry['outcome']}")
            skipped += 1
            continue
        entry["status"] = status
        entry["profit"] = profit_for(entry["book_price"], status)
        entry["settled_date"] = today
        settled[status] += 1
        changed = True
        print(f"settled {status}: {entry['event']} / {entry['outcome']} "
              f"({entry['book']} {entry['book_price']:+d}) -> {entry['profit']:+.2f}u")
    if changed:
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=2)
    print(f"{log_path}: settled {settled['win']} win, {settled['loss']} loss, "
          f"{settled['push']} push; {skipped} still pending")


def main():
    """Grade the consensus log and the v5 model-props log."""
    settle_log(LOG_PATH)
    settle_log(MODEL_LOG_PATH)


if __name__ == "__main__":
    main()
