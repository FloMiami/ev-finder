#!/usr/bin/env python3
"""Add a hand-called spread pick to the manual-picks log. 0 credits.

Alex's friends call in NBA spread picks by hand ("Knicks -8.5,
+105, ProphetX, tonight"). This script records one of those picks
so it shows on the site in its OWN section with its OWN record —
never mixed into the v6 model forward test, or the model test
would be corrupted.

A manual pick is a flag-shaped entry in manual-picks-log.json,
the same shape the watcher writes to watch-log.json, so the
existing grade.py grades it the next morning from ESPN's free
scoreboard (0 credits, flat $100 stake) and sync_docs.py copies
it to docs/ for the site.

Rules enforced here, so a bad pick can never get in:
  - the side must be one of the two teams in the game;
  - line and price must be numbers;
  - the game's tipoff must still be in the future — a manual pick
    has to be a line that was actually available when it was
    called (use --force only to backfill a pick for testing);
  - no duplicate: same date + game + side can only be entered once.

Usage (from this folder):
    python3 add_manual_pick.py --away NYK --home IND --side NYK \
        --line -8.5 --price 105 --book phtx --by "Marcus"
    # --date 2026-11-13 and --tipoff 19:00 default to today at 7pm ET.
    # --note "Knicks own the paint" is optional.
"""

import argparse
import json
import os
import sys
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

# This script lives beside grade.py and sync_docs.py, so it reuses
# their record math and docs-copy instead of duplicating either.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from grade import recompute_record  # noqa: E402  (path set above)
import sync_docs  # noqa: E402  (path set above)

# The manual log lives beside the watch log; the site copy lives in
# the docs/ folder, next to watch-log.json.
MANUAL_LOG = os.path.join(HERE, "manual-picks-log.json")

# Tipoff times are in Eastern Time, like the rest of the project.
ET = ZoneInfo("America/New_York")

# An empty log, written when the first pick is added and shipped
# with the site so the page has a valid (empty) file to fetch.
EMPTY_LOG = {
    "flags": [],
    "record": {
        "flags": 0, "graded": 0, "wins": 0, "losses": 0,
        "pushes": 0, "profit": 0, "roi": None,
    },
}


def parse_args(argv):
    """Read the pick from the command line.

    argparse turns --line into a float and --price into an int, so
    a non-number is refused before anything is written."""
    p = argparse.ArgumentParser(
        description="Add a hand-called spread pick to the manual log.")
    p.add_argument("--away", required=True,
                   help="away team abbreviation, e.g. NYK")
    p.add_argument("--home", required=True,
                   help="home team abbreviation, e.g. IND")
    p.add_argument("--side", required=True,
                   help="the team the pick is on: the away or home team")
    p.add_argument("--line", required=True, type=float,
                   help="the spread line on that side, e.g. -8.5")
    p.add_argument("--price", required=True, type=int,
                   help="American price, e.g. 105 or -110")
    p.add_argument("--book", required=True,
                   help="book code, e.g. phtx (ProphetX)")
    p.add_argument("--by", required=True,
                   help="who called the pick in, e.g. Marcus")
    p.add_argument("--date", default=None,
                   help="game date YYYY-MM-DD, defaults to today (ET)")
    p.add_argument("--tipoff", default="19:00",
                   help="tipoff time HH:MM in ET, defaults to 19:00")
    p.add_argument("--note", default=None,
                   help="optional one-line reason for the pick")
    p.add_argument("--force", action="store_true",
                   help="allow a pick whose tipoff already passed. "
                        "Testing/backfill only — never use for a live call.")
    return p.parse_args(argv)


def parse_tipoff(date_str, tipoff_str):
    """Turn a date + HH:MM into an Eastern-Time tipoff datetime.

    Refuses a time that is not HH:MM on a 24-hour clock."""
    try:
        hour, minute = (int(part) for part in tipoff_str.split(":"))
        tipoff_time = time(hour, minute)
    except (ValueError, AttributeError):
        raise SystemExit(
            "Bad --tipoff %r: use 24-hour HH:MM, e.g. 19:00"
            % tipoff_str)
    try:
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit(
            "Bad --date %r: use YYYY-MM-DD, e.g. 2026-11-13" % date_str)
    return datetime.combine(day, tipoff_time, tzinfo=ET)


def load_log():
    """Read the manual log, or start from the empty shape when
    the file does not exist yet."""
    if not os.path.exists(MANUAL_LOG):
        return json.loads(json.dumps(EMPTY_LOG))
    with open(MANUAL_LOG, encoding="utf-8") as f:
        return json.load(f)


def save_log(log):
    """Write the log back, in the same sorted, indented shape
    grade.py writes, so every log file looks the same."""
    with open(MANUAL_LOG, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, sort_keys=True)
        f.write("\n")


def main(argv=None):
    """Validate the pick, append it, recompute the record, and
    sync the docs copy so the site can show it."""
    args = parse_args(argv)

    # Team codes are uppercase abbreviations; the book code is
    # lowercase, matching the feed codes the page already knows.
    away = args.away.strip().upper()
    home = args.home.strip().upper()
    side = args.side.strip().upper()
    book = args.book.strip().lower()
    by = args.by.strip()
    if not away.isalpha() or not home.isalpha() or not side.isalpha():
        print("Team codes must be letters only, e.g. NYK, IND.")
        return 1
    if away == home:
        print("Away and home cannot be the same team.")
        return 1
    if side not in (away, home):
        print("Side %s is not in this game — pick %s or %s."
              % (side, away, home))
        return 1
    if not by:
        print("--by cannot be blank: who called this pick in?")
        return 1

    # The game date defaults to today in Eastern Time.
    date_str = args.date or datetime.now(ET).strftime("%Y-%m-%d")
    tipoff = parse_tipoff(date_str, args.tipoff)
    now = datetime.now(ET)

    # The line has to be one that was actually available when the
    # pick was called — no adding a pick after the game tipped off.
    if tipoff <= now and not args.force:
        print("Refused: %s @ %s tipped off at %s ET, which is already "
              "past. A manual pick has to be called before tipoff, "
              "or the line was never available to bet."
              % (away, home, tipoff.strftime("%H:%M")))
        return 1

    log = load_log()

    # One pick per game per side: the same call twice is a mistake,
    # not a second pick. The opposite side of the same game is a
    # different pick and is allowed.
    for flag in log["flags"]:
        if (flag["date"] == date_str and flag["away"] == away
                and flag["home"] == home and flag["side"] == side):
            print("Refused: %s %s on %s is already in the manual log."
                  % (side, args.line, date_str))
            return 1

    # The entry mirrors a watcher flag exactly, so grade.py grades
    # it with no changes. Fields the model fills in are None here:
    # there is no line movement to measure on a hand-called pick.
    # The event id carries the side too, so opposite-side picks on
    # the same game stay distinct.
    flag = {
        "away": away,
        "home": home,
        "side": side,
        "side_line": args.line,
        "price": args.price,
        "book": book,
        "by": by,
        "note": args.note.strip() if args.note else None,
        "source": "manual",
        "median_move": None,
        "n_books": None,
        "books_at_threshold": None,
        "per_book_move": None,
        "event_id": "manual-%s-%s-%s-%s"
                    % (date_str.replace("-", ""), away, home, side),
        "date": date_str,
        "tipoff": tipoff.isoformat(),
        "flagged_at": datetime.now(timezone.utc).isoformat(),
        "graded_at": None,
        "result": None,
        "score_away": None,
        "score_home": None,
        "profit": None,
        "credits_remaining_after": None,
    }
    log["flags"].append(flag)

    # Same record math as the model logs: wins, losses, pushes,
    # profit, ROI from the graded flags.
    recompute_record(log)
    save_log(log)

    print("Added manual pick: %s %s at %d (%s), %s @ %s, %s, "
          "called by %s."
          % (side, args.line, args.price, book, away, home,
             date_str, by))
    record = log["record"]
    print("Manual record: %d flags, %d graded, %d-%d-%d, "
          "profit $%.2f"
          % (record["flags"], record["graded"], record["wins"],
             record["losses"], record["pushes"], record["profit"]))

    # Copy the log to docs/ so the page shows the new pick.
    sync_docs.sync_file(sync_docs.MANUAL_SOURCE, sync_docs.MANUAL_DEST,
                        "manual picks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
