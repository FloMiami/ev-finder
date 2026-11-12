#!/usr/bin/env python3
"""Live move-watcher for the v6 >= 2.0 line-movement rule. Paper only.

What this is: one morning check of today's NBA games. It reads the
current spread line and the opening line for each game from Oddize,
flags the games where the line has moved at least 2.0 points, and
writes the flags to watch-log.json. Nothing here is a bet. The flags
are paper picks that grade.py settles the next day from ESPN's free
scoreboard, so the record builds one morning at a time.

What this is NOT: a verified winner. The >= 2.0 rule is a lead from
the v6 Oddize rerun (543 bets, 57.1% win rate, 95% CI 52.9-61.2%,
ROI +11.03%) — but it was one cell of a 16-cell grid, it does not
survive a multiple-comparisons correction, and picking it after
seeing it win is tuning on the test set. This watcher is the
fresh-data test of that lead. Its record starts at 0 and means
nothing until 50-100 flags have settled.

Two honest deviations from the backtest, both verified live on
2026-11-12 and both recorded here so nobody mistakes this for the
same test:
- Pinnacle is NOT in the live /odds/latest response (0 of 100
  events carry a Pinnacle entry). The backtest was Pinnacle-only.
  This watcher uses the MEDIAN move across the real sportsbooks
  present, and records the book that offered the flagged price.
  That tests "follow the move at a soft book's current line,"
  which is the realistic executable version — but it is a
  different test from the Pinnacle-only backtest.
- Polymarket (poly) and Smarkets (smkt) are EXCLUDED. They are
  prediction/exchange markets, not sportsbooks, and their
  open_line is not a comparable opener. Verified 2026-11-12:
  Polymarket showed a +13.0 "move" on DET@WAS and a -7.0 "move"
  on TOR@PHI. That is feed noise, not line movement.

Credit math: one check costs 1 Oddize credit
(GET /api/v1/odds/latest?sport=nba). The balance check is free.
The script refuses to spend a credit when fewer than 10 remain.
One check a day from Nov 12 to the Dec 1 reset costs ~19 credits
of the 59 left — ample spare. Do NOT run this on a schedule
without Alex's approval naming the schedule.

Usage (from this folder):
    python3 watch.py

The key normally comes from the vault helper on this machine. In
GitHub Actions the workflow passes the ODDIZE_API_KEY repository
secret as an environment variable instead, and the script sends it
directly as the X-API-Key header (same endpoint, same 1-credit
cost). Without either route, the script cannot fetch and exits
with an error — it never writes a flag from no data."""

import json
import os
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests  # only used by the direct (env-key) fetch route below

# The project's odds math lives in ~/workspace/ev-finder/ev.py.
sys.path.insert(0, os.path.expanduser("~/workspace/ev-finder"))

# The Oddize helper (vault-stored key, surrogate auth). Never put the
# key itself in this file, in an argument, or in a log.
HELPER = os.path.expanduser(
    "~/workspace/custom-connectors/custom.oddize/bin/oddize_fetch.py")
ODDIZE_BASE = "https://oddize.com/api/v1"

# Stop spending credits when the balance falls to this floor.
CREDIT_FLOOR = 10

# The rule, pre-registered. Do not change these numbers after flags
# exist — changing the bar mid-test would invalidate the record.
PRIMARY_THRESHOLD = 2.0   # flag when the median move is >= 2.0 points
DIAGNOSTIC_THRESHOLD = 1.0  # log smaller moves separately, never mixed in

# Prediction/exchange markets. Their open_line is not a sportsbook
# opener (verified Nov 12: Polymarket "moves" of +13.0 and -7.0).
EXCLUDED_BOOKS = {"poly", "smkt"}

# Files live beside this script.
HERE = os.path.dirname(os.path.abspath(__file__))
PRIMARY_LOG = os.path.join(HERE, "watch-log.json")
DIAGNOSTIC_LOG = os.path.join(HERE, "watch-diagnostic.json")
RAW_DIR = os.path.join(HERE, "raw")


def fetch_json_direct(url, api_key):
    """Call Oddize directly with a key from the environment.

    This is the GitHub Actions route: the workflow passes the
    ODDIZE_API_KEY repository secret as an environment variable, and
    this function sends it as the X-API-Key header Oddize reads.
    Same endpoint, same 1-credit cost, same response shape as the
    helper: {"data": <response body>, "credits_cost": ...,
    "credits_remaining": ...}. The key is never printed or logged.
    """
    resp = requests.get(
        url, headers={"X-API-Key": api_key}, timeout=30,
        allow_redirects=False)  # Never follow a redirect on an authenticated call.
    if resp.status_code != 200:
        raise RuntimeError("Oddize HTTP %d: %s"
                           % (resp.status_code, resp.text[:200]))

    def header_int(name):
        """A credit header as an int, or None when it is missing."""
        try:
            return int(resp.headers.get(name))
        except (TypeError, ValueError):
            return None

    return {
        "data": resp.json(),
        "credits_cost": header_int("X-Credits-Cost"),
        "credits_remaining": header_int("X-Credits-Remaining"),
    }


def fetch_json(url):
    """Fetch one Oddize URL and return its parsed JSON.

    Two routes, same result shape. When the ODDIZE_API_KEY
    environment variable is set (GitHub Actions, from the repo
    secret), the key is sent directly as the X-API-Key header.
    Otherwise the vault helper on this machine supplies the key,
    which is the normal morning-run route. Retries once: the first
    call to this host timed out once in the egress proxy tunnel,
    and the retry went through in under a second. Raises if both
    attempts fail, so a bad fetch never silently produces an empty
    log. The key itself is never printed or logged.
    """
    api_key = os.environ.get("ODDIZE_API_KEY", "").strip()
    last_error = None
    for _attempt in range(2):
        try:
            if api_key:
                return fetch_json_direct(url, api_key)
            proc = subprocess.run(
                [sys.executable, HELPER, "--url", url],
                capture_output=True, text=True, timeout=60)
            if proc.returncode == 0:
                return json.loads(proc.stdout)
            last_error = proc.stderr.strip() or "helper failed"
        except Exception as exc:  # retry once, then raise below
            last_error = str(exc)
    raise RuntimeError("Oddize fetch failed twice: %s" % last_error)


def check_balance():
    """Read the free usage endpoint. Returns credits remaining."""
    result = fetch_json(ODDIZE_BASE + "/account/usage")
    return int(result["data"]["credits_remaining"])


def fetch_latest():
    """Fetch the current NBA board (1 credit). Returns the events."""
    result = fetch_json(ODDIZE_BASE + "/odds/latest?sport=nba")
    return result["data"].get("events", []), result.get("credits_remaining")


def best_price(entries):
    """Best price for the bettor among (line, price) entries.

    Highest decimal odds wins: a positive price always beats a
    negative one, and among negatives the one closest to zero
    (-108 beats -115). Returns (line, price, book).
    """
    from ev import american_to_decimal  # project's odds math, one source

    def decimal(price):
        return american_to_decimal(int(price))

    best = None
    for line, price, book in entries:
        key = decimal(price)
        if best is None or key > best[0]:
            best = (key, line, int(price), book)
    return best[1], best[2], best[3]


def analyze_game(event):
    """Work out the median spread move for one game.

    The move is read off the HOME team's spread line: if the home
    line rose (home getting more points), the money came in on the
    away team, and vice versa. Returns None when the game has no
    usable spread entries, otherwise a dict with the median move,
    the per-book moves, and the best price on each side.
    """
    away = event.get("team1")
    home = event.get("team2")
    spreads = [o for o in event.get("odds", [])
               if o.get("market") == "spread"
               and o.get("book") not in EXCLUDED_BOOKS
               and o.get("line") is not None
               and o.get("open_line") is not None]
    # One entry per book per side; keep both sides so the pick is
    # fully specified (line and price for the side we flag).
    by_book = {}
    for o in spreads:
        by_book.setdefault(o["book"], {})[o["team"]] = o
    moves = {}
    for book, sides in by_book.items():
        if home not in sides or away not in sides:
            continue  # need both sides to trust this book's line
        # The move, in points, read off the home line.
        moves[book] = float(sides[home]["line"]) - float(sides[home]["open_line"])
    if not moves:
        return None
    median_move = statistics.median(moves.values())
    # Best price on each side, at the most common current line for
    # that side (so the line and the price belong together).
    best = {}
    for side in (away, home):
        lines = [float(by_book[b][side]["line"]) for b in by_book
                 if side in by_book[b]]
        # The line most books are showing right now.
        modal_line = statistics.mode(lines)
        entries = [(float(by_book[b][side]["line"]),
                    by_book[b][side]["american_odds"], b)
                   for b in by_book
                   if side in by_book[b]
                   and float(by_book[b][side]["line"]) == modal_line]
        best[side] = best_price(entries)
    return {
        "median_move": median_move,
        "moves": moves,
        "best": best,
    }


def load_log(path, rule):
    """Read a log file, or start an empty one with the given rule."""
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {
        "rule": rule,
        "status": "forward test, NOT a verified winner",
        "record": {"flags": 0, "graded": 0, "wins": 0, "losses": 0,
                   "pushes": 0, "profit": 0.0, "roi": None},
        "flags": [],
    }


def save_log(path, data):
    """Write a log file, sorted keys, human-readable."""
    with open(path, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def already_flagged(log, event_id, date):
    """True when this game was already flagged on this date."""
    return any(f["event_id"] == event_id and f["date"] == date
               for f in log["flags"])


def main():
    """One morning check: balance, board, flags, log. Prints a summary."""
    # The balance check is free. Never spend a credit below the floor.
    remaining = check_balance()
    print("Oddize credits remaining: %d (floor: %d)" % (remaining, CREDIT_FLOOR))
    if remaining < CREDIT_FLOOR:
        print("Balance below the floor — stopping without spending.")
        return

    events, remaining_after = fetch_latest()
    print("Fetched %d events, 1 credit spent, %s remaining"
          % (len(events), remaining_after))

    # Today's games only, in Eastern time (the start_date string
    # carries a -05:00 offset, so its date part IS the ET date).
    # Skip games that already tipped: a flag must be flaggable.
    now = datetime.now(timezone.utc)
    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    os.makedirs(RAW_DIR, exist_ok=True)
    # One snapshot per run (timestamped, so a second check the same
    # day never overwrites the first one's evidence).
    raw_path = os.path.join(
        RAW_DIR, "latest_%s.json" % now.strftime("%Y-%m-%d_%H%M"))
    with open(raw_path, "w") as f:
        json.dump({"fetched_at": now.isoformat(), "events": events}, f)

    primary = load_log(
        PRIMARY_LOG,
        "NBA spreads, follow the line move >= %.1f points (median move "
        "across real sportsbooks), flat $100, paper only" % PRIMARY_THRESHOLD)
    diagnostic = load_log(
        DIAGNOSTIC_LOG,
        "NBA spreads, line move >= %.1f and < %.1f points (median move "
        "across real sportsbooks), flat $100, paper only — diagnostic, "
        "kept separate from the primary record"
        % (DIAGNOSTIC_THRESHOLD, PRIMARY_THRESHOLD))

    new_primary = 0
    new_diagnostic = 0
    for event in events:
        start = event.get("start_date", "")
        if start[:10] != today:
            continue  # the board runs ~2 weeks out; today only
        try:
            tipoff = datetime.fromisoformat(start)
        except ValueError:
            continue
        if tipoff <= now:
            continue  # already tipped — too late to flag
        info = analyze_game(event)
        if info is None:
            continue
        move = info["median_move"]
        magnitude = abs(move)
        if magnitude < DIAGNOSTIC_THRESHOLD:
            continue  # no move worth recording
        # The side the money is on: a rising home line means the
        # away team is getting stronger (and vice versa).
        side = event["team1"] if move > 0 else event["team2"]
        line, price, book = info["best"][side]
        flag = {
            "event_id": event.get("event_id"),
            "date": today,
            "away": event.get("team1"),
            "home": event.get("team2"),
            "tipoff": start,
            "side": side,
            "side_line": line,
            "price": price,
            "book": book,
            "median_move": move,
            "n_books": len(info["moves"]),
            "books_at_threshold": sum(
                1 for m in info["moves"].values() if abs(m) >= PRIMARY_THRESHOLD),
            "per_book_move": info["moves"],
            "flagged_at": now.isoformat(),
            "credits_remaining_after": remaining_after,
            "result": None,
            "score_away": None,
            "score_home": None,
            "profit": None,
            "graded_at": None,
        }
        if magnitude >= PRIMARY_THRESHOLD:
            if already_flagged(primary, flag["event_id"], today):
                continue
            primary["flags"].append(flag)
            primary["record"]["flags"] += 1
            new_primary += 1
            print("FLAG >= %.1f: %s @ %s — follow %s %s at %d (%s), "
                  "median move %+.1f across %d books"
                  % (PRIMARY_THRESHOLD, flag["away"], flag["home"],
                     side, line, price, book, move, len(info["moves"])))
        else:
            if already_flagged(diagnostic, flag["event_id"], today):
                continue
            diagnostic["flags"].append(flag)
            diagnostic["record"]["flags"] += 1
            new_diagnostic += 1
            print("diagnostic >= %.1f: %s @ %s — %s %s at %d (%s), "
                  "median move %+.1f"
                  % (DIAGNOSTIC_THRESHOLD, flag["away"], flag["home"],
                     side, line, price, book, move))

    save_log(PRIMARY_LOG, primary)
    save_log(DIAGNOSTIC_LOG, diagnostic)
    print("New primary flags: %d (record: %d flags, %d graded, "
          "%d-%d-%d, profit $%.2f)"
          % (new_primary, primary["record"]["flags"],
             primary["record"]["graded"], primary["record"]["wins"],
             primary["record"]["losses"], primary["record"]["pushes"],
             primary["record"]["profit"]))
    print("New diagnostic entries: %d (record: %d flags)"
          % (new_diagnostic, diagnostic["record"]["flags"]))


if __name__ == "__main__":
    main()
