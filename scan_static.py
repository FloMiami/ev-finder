"""One-shot scan that writes the results for the GitHub Pages site.

This is the script the GitHub Actions workflow runs on a schedule. It
fetches live odds with the ODDS_API_KEY secret, finds the +EV plays,
and writes docs/plays.json. The static page (docs/index.html) reads
that file and renders the table. No server, no Flask, no key in the
page: the key only ever lives in the Actions secret.

Run locally (demo mode, no key needed):
    python scan_static.py
    -> writes docs/plays.json from the built-in sample lines

Run live:
    ODDS_API_KEY=your_key python scan_static.py
    -> writes docs/plays.json from real odds

Settings come from the environment, same names as the Flask app:
    MIN_EDGE (default 0.03), PROP_EVENTS (default 3),
    PROP_SPORTS (default basketball_nba).
"""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import demo_data
import odds_api
from ev import DEFAULT_MIN_EDGE, find_ev

API_KEY = os.environ.get("ODDS_API_KEY", "").strip()
MIN_EDGE = float(os.environ.get("MIN_EDGE", str(DEFAULT_MIN_EDGE)))
PROP_EVENTS = int(os.environ.get("PROP_EVENTS", "3"))
PROP_SPORTS = [s.strip() for s in
               os.environ.get("PROP_SPORTS", "basketball_nba").split(",") if s.strip()]
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "plays.json")


def main():
    """Fetch odds, find the plays, write docs/plays.json."""
    errors = []
    remaining = None

    if not API_KEY:
        # No key: write the sample plays so the page is never empty.
        # The page labels these DEMO, so nobody mistakes them for real odds.
        plays = find_ev(demo_data.demo_events(), MIN_EDGE)
        result = {
            "mode": "demo",
            "updated": "demo data (no API key set)",
            "credits_remaining": None,
            "min_edge": MIN_EDGE,
            "error": None,
            "plays": plays,
        }
        write(result)
        print(f"demo mode: wrote {len(plays)} sample plays to {OUT_PATH}")
        return

    events = []
    for label, sport_key in odds_api.SPORTS.items():
        try:
            data, remaining, _used = odds_api.fetch_bulk_odds(API_KEY, sport_key)
        except Exception as exc:  # noqa: BLE001 - report it, keep going
            errors.append(f"{label}: {exc}")
            continue
        events.extend(data)

    # Player props, only if enabled and the balance allows it.
    if PROP_EVENTS > 0 and (remaining is None or remaining > odds_api.PROP_CREDIT_FLOOR):
        for label, sport_key in odds_api.SPORTS.items():
            if sport_key not in PROP_SPORTS:
                continue
            markets = odds_api.PROP_MARKETS.get(sport_key)
            if not markets:
                continue
            ids = odds_api.upcoming_event_ids(
                [e for e in events if e.get("sport_key") == sport_key]
            )[:PROP_EVENTS]
            for event_id in ids:
                try:
                    prop_event, remaining, _used = odds_api.fetch_event_props(
                        API_KEY, sport_key, event_id, markets
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"props {event_id}: {exc}")
                    continue
                events.append(prop_event)
                if remaining is not None and remaining <= odds_api.PROP_CREDIT_FLOOR:
                    break

    plays = find_ev(events, MIN_EDGE)
    result = {
        "mode": "live",
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "credits_remaining": remaining,
        "min_edge": MIN_EDGE,
        "error": "; ".join(errors) if errors else None,
        "plays": plays,
    }
    write(result)
    print(f"live mode: wrote {len(plays)} plays to {OUT_PATH}")
    print(f"credits remaining: {remaining}")
    if errors:
        print(f"errors: {'; '.join(errors)}")


def write(result):
    """Write the result file, creating docs/ if it is missing."""
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
