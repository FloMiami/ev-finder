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
from arb import find_arbs
from ev import DEFAULT_MIN_EDGE, find_ev
# The v5 model props are a separate forward test: they ride on the
# same fetched events (0 extra credits) but never touch the
# consensus +EV logic. If the model files are missing, the scan
# still runs — it just skips the model section.
try:
    from model_props import find_model_props
except Exception:  # noqa: BLE001 - model is optional for the scan
    find_model_props = None

API_KEY = os.environ.get("ODDS_API_KEY", "").strip()
MIN_EDGE = float(os.environ.get("MIN_EDGE", str(DEFAULT_MIN_EDGE)))
PROP_EVENTS = int(os.environ.get("PROP_EVENTS", "3"))
PROP_SPORTS = [s.strip() for s in
               os.environ.get("PROP_SPORTS", "basketball_nba").split(",") if s.strip()]
OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "plays.json")
# The permanent record: every live play ever flagged, one entry each.
# The settle script marks entries win/loss/push here; the page reads
# this file to show the record and the history table.
LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "plays-log.json")
# The model props get their own log, so the v5 forward-test record
# never mixes into the consensus +EV record. Same shape, same
# settler, separate file.
MODEL_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "docs", "model-props-log.json")


def main():
    """Fetch odds, find the plays, write docs/plays.json."""
    errors = []
    remaining = None

    if not API_KEY:
        # No key: write the sample plays so the page is never empty.
        # The page labels these DEMO, so nobody mistakes them for real odds.
        plays = find_ev(demo_data.demo_events(), MIN_EDGE)
        arbs = find_arbs(demo_data.demo_events())
        result = {
            "mode": "demo",
            "updated": "demo data (no API key set)",
            "credits_remaining": None,
            "min_edge": MIN_EDGE,
            "error": None,
            "plays": plays,
            "arbs": arbs,
            "model_props": [],
            "model_error": None,
        }
        write(result)
        print(f"demo mode: wrote {len(plays)} sample plays and {len(arbs)} arbs to {OUT_PATH}")
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
    # True arbitrage: best price on each side across different books.
    # Same events list as the +EV scan, so prop events are included.
    arbs = find_arbs(events)
    # v5 model props: the projection model flags NBA props where its
    # projection implies 5%+ EV. Same events, 0 extra credits. A
    # model failure must never break the scan, so it is guarded.
    model_props = []
    model_error = None
    if find_model_props is not None:
        try:
            model_props, model_stats = find_model_props(events)
            print(f"model props: {model_stats['flagged']} flagged "
                  f"({model_stats['checked']} checked, "
                  f"{model_stats['matched']} matched, "
                  f"{model_stats['no_projection']} no projection, "
                  f"{model_stats['skipped']} skipped)")
        except Exception as exc:  # noqa: BLE001 - log it, keep going
            model_error = f"model props failed: {exc}"
            print(model_error)
    scan_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result = {
        "mode": "live",
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "credits_remaining": remaining,
        "min_edge": MIN_EDGE,
        "error": "; ".join(errors) if errors else None,
        "plays": plays,
        "arbs": arbs,
        "model_props": model_props,
        "model_error": model_error,
    }
    write(result)
    added = log_plays(plays, scan_date)
    print(f"live mode: wrote {len(plays)} plays, {len(arbs)} arbs and "
          f"{len(model_props)} model props to {OUT_PATH}")
    print(f"logged {added} new plays to {LOG_PATH}")
    model_added = log_plays(model_props, scan_date, MODEL_LOG_PATH)
    print(f"logged {model_added} new model props to {MODEL_LOG_PATH}")
    print(f"credits remaining: {remaining}")
    if errors:
        print(f"errors: {'; '.join(errors)}")


def log_plays(plays, scan_date, log_path=None):
    """Append today's live plays to the permanent record.

    Only live plays are logged, never the demo samples. A play is
    skipped when the same date, game, market, play and book is already
    in the log, so re-running the scan on the same day cannot count
    one play twice. Returns how many new entries were added.
    log_path defaults to the consensus log; the model props pass
    their own path so the two records stay separate.
    """
    if log_path is None:
        log_path = LOG_PATH
    log = []
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as f:
            log = json.load(f)
    seen = {(e["date"], e["event"], e["market"], e["outcome"], e["book"])
            for e in log}
    added = 0
    for p in plays:
        key = (scan_date, p["event"], p["market"], p["outcome"], p["book"])
        if key in seen:
            continue
        seen.add(key)
        log.append({
            "date": scan_date,
            "sport": p["sport"],
            "sport_title": p["sport_title"],
            "event": p["event"],
            "commence_time": p.get("commence_time"),
            "market": p["market"],
            "outcome": p["outcome"],
            "book": p["book"],
            "book_price": p["book_price"],
            "fair_price": p["fair_price"],
            "edge_pp": p["edge_pp"],
            "ev_pct": p["ev_pct"],
            "tag": p["tag"],
            "source": p.get("source", "consensus"),
            "projection": p.get("projection"),
            "p_over": p.get("p_over"),
            "status": "pending",
            "profit": None,
            "settled_date": None,
        })
        added += 1
    if added:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(log, f, indent=2)
    return added


def write(result):
    """Write the result file, creating docs/ if it is missing."""
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
