#!/usr/bin/env python3
"""Grade the live-watch flags from ESPN's free scoreboard. 0 credits.

Run this the morning after a watch (or any later morning): for every
flag with no result yet whose game should be over (tip-off + 6 hours
covers even the longest overtime game), it fetches the final score
from ESPN's public scoreboard, grades the spread, and records W/L/P
and profit at a flat $100 stake at the flagged price. It then
recomputes the running record in each log file.

A flag stays pending — never guessed — when the game cannot be
found, is not finished yet, or the score cannot be read.

Usage (from this folder):
    python3 grade.py
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

# The project's odds math lives in ~/workspace/ev-finder/ev.py.
sys.path.insert(0, os.path.expanduser("~/workspace/ev-finder"))
from ev import american_to_decimal  # noqa: E402  (path set above)

# ESPN's public scoreboard. Free, no key. One call per date.
SCOREBOARD_URL = ("https://site.api.espn.com/apis/site/v2/sports/"
                  "basketball/nba/scoreboard")
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; EVFinder/1.0)"}

# A game is only graded once it should be over.
SETTLE_DELAY = timedelta(hours=6)

# Files live beside this script.
HERE = os.path.dirname(os.path.abspath(__file__))
PRIMARY_LOG = os.path.join(HERE, "watch-log.json")
DIAGNOSTIC_LOG = os.path.join(HERE, "watch-diagnostic.json")

# ESPN's team abbreviations -> the abbreviations Oddize uses. ESPN
# differs in nine cases; the rest (BOS, LAL, MIA, ...) already match.
# Verified against ESPN's Nov 11 scoreboard for GS, NY and UTAH;
# BKN, CHA and PHX are ESPN's standard codes for those teams.
ESPN_TEAM_MAP = {
    "GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP",
    "WSH": "WAS", "UTAH": "UTA",
    "BKN": "BRK", "CHA": "CHO", "PHX": "PHO",
}

# Oddize abbreviation -> ESPN display name. Fallback when the
# abbreviation match fails, so one code difference never strands a
# flag as ungradeable.
DISPLAY_NAMES = {
    "ATL": "Atlanta Hawks", "BOS": "Boston Celtics",
    "BRK": "Brooklyn Nets", "CHI": "Chicago Bulls",
    "CHO": "Charlotte Hornets", "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets",
    "DET": "Detroit Pistons", "GSW": "Golden State Warriors",
    "HOU": "Houston Rockets", "IND": "Indiana Pacers",
    "LAC": "LA Clippers", "LAL": "Los Angeles Lakers",
    "MEM": "Memphis Grizzlies", "MIA": "Miami Heat",
    "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans", "NYK": "New York Knicks",
    "OKC": "Oklahoma City Thunder", "ORL": "Orlando Magic",
    "PHI": "Philadelphia 76ers", "PHO": "Phoenix Suns",
    "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings",
    "SAS": "San Antonio Spurs", "TOR": "Toronto Raptors",
    "UTA": "Utah Jazz", "WAS": "Washington Wizards",
}


def norm(name):
    """Lowercase a name and strip everything but letters and digits,
    so 'Philadelphia 76ers' matches ESPN's 'Philadelphia 76ers'
    even if spacing or punctuation differs."""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def fetch_day_scores(date_str):
    """Final scores for one ET date (YYYYMMDD) from ESPN.

    Returns a list of {away, home, score_away, score_home} with
    teams in Oddize abbreviations, plus a display-name lookup for
    the fallback match. Only completed games are returned.
    """
    resp = requests.get(
        SCOREBOARD_URL, params={"dates": date_str},
        headers=HEADERS, timeout=20)
    resp.raise_for_status()
    rows = []
    for event in resp.json().get("events", []):
        comp = (event.get("competitions") or [{}])[0]
        status = comp.get("status", {})
        if not status.get("type", {}).get("completed"):
            continue  # not finished yet — grade it another morning
        by_side = {}
        for c in comp.get("competitors", []):
            team = c.get("team", {})
            abbr = ESPN_TEAM_MAP.get(team.get("abbreviation"),
                                     team.get("abbreviation"))
            try:
                score = int(c.get("score"))
            except (TypeError, ValueError):
                by_side = {}
                break
            by_side[c.get("homeAway")] = (abbr, score,
                                          team.get("displayName", ""))
        if "home" not in by_side or "away" not in by_side:
            continue
        rows.append({
            "away": by_side["away"][0],
            "home": by_side["home"][0],
            "score_away": by_side["away"][1],
            "score_home": by_side["home"][1],
            "away_name": by_side["away"][2],
            "home_name": by_side["home"][2],
        })
    return rows


def find_scores(flag, cache):
    """Find a flag's final scores in the cache, fetching the date
    from ESPN on first use. Matches on team abbreviations, falling
    back to display names. Returns (score_away, score_home) or None
    when the game cannot be found (stays pending)."""
    date_str = flag["tipoff"][:10].replace("-", "")
    if date_str not in cache:
        cache[date_str] = fetch_day_scores(date_str)
    for row in cache[date_str]:
        if row["away"] == flag["away"] and row["home"] == flag["home"]:
            return row["score_away"], row["score_home"]
        if (norm(row["away_name"]) == norm(DISPLAY_NAMES.get(flag["away"], ""))
                and norm(row["home_name"]) == norm(DISPLAY_NAMES.get(flag["home"], ""))):
            return row["score_away"], row["score_home"]
    return None


def grade_spread(flag, score_away, score_home):
    """Grade one flagged spread from the final score.

    The flagged side's score plus its line beats the opponent's
    score for a win, loses below it, and pushes on an exact tie
    (whole-number lines can push). Returns win/loss/push.
    """
    line = float(flag["side_line"])
    if flag["side"] == flag["away"]:
        mine, theirs = score_away, score_home
    else:
        mine, theirs = score_home, score_away
    if mine + line > theirs:
        return "win"
    if mine + line < theirs:
        return "loss"
    return "push"


def profit_for(price, result):
    """Profit in dollars on a flat $100 stake at the flagged price:
    a win pays the decimal odds minus 1, a loss is -$100, a push
    is $0."""
    if result == "win":
        return round((american_to_decimal(int(price)) - 1.0) * 100.0, 2)
    if result == "loss":
        return -100.0
    return 0.0


def recompute_record(log):
    """Rebuild the running record from the graded flags.

    Pushes count as bets for the ROI denominator (same as the v6
    backtest) but not in the win-rate numerator or denominator.
    ROI is None until at least one flag has settled.
    """
    graded = [f for f in log["flags"] if f["result"] is not None]
    wins = sum(1 for f in graded if f["result"] == "win")
    losses = sum(1 for f in graded if f["result"] == "loss")
    pushes = sum(1 for f in graded if f["result"] == "push")
    profit = round(sum(f["profit"] for f in graded), 2)
    log["record"] = {
        "flags": len(log["flags"]),
        "graded": len(graded),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "profit": profit,
        "roi": (round(profit / (len(graded) * 100.0), 4)
                if graded else None),
    }


def grade_log(path, cache, now):
    """Grade every ungraded, should-be-over flag in one log file.
    Returns (newly graded, still pending)."""
    if not os.path.exists(path):
        return 0, 0
    with open(path) as f:
        log = json.load(f)
    graded_now = 0
    pending = 0
    for flag in log["flags"]:
        if flag["result"] is not None:
            continue
        tipoff = datetime.fromisoformat(flag["tipoff"])
        if tipoff + SETTLE_DELAY > now:
            pending += 1
            continue  # game should not be over yet — try tomorrow
        scores = find_scores(flag, cache)
        if scores is None:
            pending += 1
            print("  %s @ %s (%s): game not found on ESPN — stays pending"
                  % (flag["away"], flag["home"], flag["date"]))
            continue
        score_away, score_home = scores
        result = grade_spread(flag, score_away, score_home)
        flag["result"] = result
        flag["score_away"] = score_away
        flag["score_home"] = score_home
        flag["profit"] = profit_for(flag["price"], result)
        flag["graded_at"] = now.isoformat()
        graded_now += 1
        print("  %s @ %s (%s): follow %s %s at %d — %s, final %d-%d, "
              "profit $%.2f"
              % (flag["away"], flag["home"], flag["date"], flag["side"],
                 flag["side_line"], flag["price"], result,
                 score_away, score_home, flag["profit"]))
    recompute_record(log)
    with open(path, "w") as f:
        json.dump(log, f, indent=2, sort_keys=True)
        f.write("\n")
    return graded_now, pending


def main():
    """Grade both logs and print the running records."""
    now = datetime.now(timezone.utc)
    cache = {}
    print("Grading primary log (%s)..." % PRIMARY_LOG)
    graded, pending = grade_log(PRIMARY_LOG, cache, now)
    print("  graded %d now, %d still pending" % (graded, pending))
    print("Grading diagnostic log (%s)..." % DIAGNOSTIC_LOG)
    graded, pending = grade_log(DIAGNOSTIC_LOG, cache, now)
    print("  graded %d now, %d still pending" % (graded, pending))
    for path in (PRIMARY_LOG, DIAGNOSTIC_LOG):
        if not os.path.exists(path):
            continue
        with open(path) as f:
            record = json.load(f)["record"]
        roi = ("%.2f%%" % (record["roi"] * 100)
               if record["roi"] is not None else "n/a")
        print("%s: %d flags, %d graded, %d-%d-%d, profit $%.2f, ROI %s"
              % (os.path.basename(path), record["flags"], record["graded"],
                 record["wins"], record["losses"], record["pushes"],
                 record["profit"], roi))


if __name__ == "__main__":
    main()
