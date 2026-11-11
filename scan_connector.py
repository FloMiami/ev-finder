"""Run an EV scan using the vault-stored Odds API key (custom connector).

Instead of ODDS_API_KEY, every call goes through
~/workspace/custom-connectors/custom.the-odds-api/bin/odds_fetch.py,
which attaches a surrogate the egress layer swaps for the real key.
The key never appears in this file, in arguments, or in output.

Usage:
    python scan_connector.py [--sports nba,ncaab,nfl] [--prop-events 5]

Defaults: NBA only, 3 upcoming events' props. Bulk game lines are
cached 15 minutes (see odds_api) so repeat runs don't burn credits.
Props stop early if the monthly balance drops to the floor (50).
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import odds_api
from ev import find_ev

HELPER = os.path.expanduser(
    "~/workspace/custom-connectors/custom.the-odds-api/bin/odds_fetch.py"
)
BASE = "https://api.the-odds-api.com/v4"


def fetch(url):
    """Call the connector helper; return (data, credits_remaining)."""
    proc = subprocess.run(
        [sys.executable, HELPER, "--url", url],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "odds_fetch failed")
    payload = json.loads(proc.stdout)
    return payload["data"], payload["credits_remaining"]


def main():
    """Run the scan: game lines first, then props, then print plays."""
    parser = argparse.ArgumentParser(description="EV scan via the Odds API connector.")
    parser.add_argument("--sports", default="nba",
                        help="Comma-separated: nba, ncaab, nfl (default: nba)")
    parser.add_argument("--prop-events", type=int, default=3,
                        help="Upcoming events per sport to fetch props for (default: 3)")
    args = parser.parse_args()

    labels = [s.strip() for s in args.sports.split(",") if s.strip()]
    events = []
    remaining = None
    errors = []

    # Game lines first. The cache is checked before each fetch, so a
    # second run inside 15 minutes reuses the lines instead of paying.
    for label in labels:
        sport_key = odds_api.SPORTS[label]
        cached, _age = odds_api.load_cache(f"bulk_{label}")
        if cached is not None:
            events.extend(cached)
            continue
        url = (f"{BASE}/sports/{sport_key}/odds"
               f"?regions=us&markets={odds_api.GAME_MARKETS}&oddsFormat=american")
        try:
            data, remaining = fetch(url)
        except Exception as exc:  # noqa: BLE001 - report and keep going
            errors.append(f"{label}: {exc}")
            continue
        odds_api.save_cache(f"bulk_{label}", data)
        events.extend(data)

    # Props last, only for the next few events, because each one costs
    # credits. Stop early if the balance hits the floor.
    if args.prop_events > 0 and (remaining is None or remaining > odds_api.PROP_CREDIT_FLOOR):
        for label in labels:
            sport_key = odds_api.SPORTS[label]
            markets = odds_api.PROP_MARKETS.get(sport_key)
            if not markets:
                continue
            ids = odds_api.upcoming_event_ids(
                [e for e in events if e.get("sport_key") == sport_key]
            )[:args.prop_events]
            for event_id in ids:
                url = (f"{BASE}/sports/{sport_key}/events/{event_id}/odds"
                       f"?regions=us&markets={markets}&oddsFormat=american")
                try:
                    prop_event, remaining = fetch(url)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"props {event_id}: {exc}")
                    continue
                events.append(prop_event)
                if remaining is not None and remaining <= odds_api.PROP_CREDIT_FLOOR:
                    break

    plays = find_ev(events, float(os.environ.get("MIN_EDGE", "0.03")))
    print(f"MODE: live (connector)")
    print(f"UPDATED: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"CREDITS REMAINING: {remaining}")
    print(f"ERROR: {'; '.join(errors) if errors else None}")
    print(f"PLAYS: {len(plays)}")
    for p in plays:
        print(f"{p['sport_title']:5} | {p['market']:24} | {p['event']:45} | "
              f"{p['outcome']:28} | {p['book']:12} | book {p['book_price']:+d} | "
              f"fair {p['fair_price']:+d} | edge +{p['edge_pp']}% | EV +{p['ev_pct']}% | {p['tag']}")


if __name__ == "__main__":
    main()
