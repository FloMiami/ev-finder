"""EV Finder — web app.

Shows +EV plays for NBA, men's college basketball and NFL, found by
comparing each book's price to the consensus fair price across books
(the same method Optimal uses — see ev.py).

Modes:
- LIVE: ODDS_API_KEY is set. Fetches real odds from The Odds API,
  cached 15 minutes per sport so refreshes don't burn credits.
- DEMO: no key set. Runs on the built-in sample lines so the page
  and the math can be checked before a key is added.

Player props are on for NBA only (PROP_EVENTS=3, PROP_SPORTS=basketball_nba)
because the per-event endpoint costs more credits. Set PROP_EVENTS to 0
to turn them off, or add a sport to PROP_SPORTS to include it, and the
scan stops if the monthly balance drops low.

Daily display: the app scans once at startup and re-scans when the last
scan is older than AUTO_REFRESH_HOURS (default 48, set it to 24 for a
true daily refresh). The last scan time lives in data/, so a cold start
serves the last good scan instead of paying for a fresh fetch.

Run locally:
    python -m venv venv && venv/bin/pip install -r requirements.txt
    ODDS_API_KEY=your_key venv/bin/python app.py
    -> http://127.0.0.1:5000

Azure App Service (free F1 tier) startup command:
    gunicorn --bind=0.0.0.0 --timeout 600 app:app
"""

import os

from flask import Flask, jsonify, redirect, render_template, request, url_for

import demo_data
import odds_api
from ev import DEFAULT_MIN_EDGE, find_ev

app = Flask(__name__)

API_KEY = os.environ.get("ODDS_API_KEY", "").strip()
MIN_EDGE = float(os.environ.get("MIN_EDGE", str(DEFAULT_MIN_EDGE)))
# Player props on for NBA only: 3 upcoming events. Props cost more
# credits per event, so this stays small — the scan stops if the monthly
# balance drops below the floor (see odds_api.PROP_CREDIT_FLOOR).
PROP_EVENTS = int(os.environ.get("PROP_EVENTS", "3"))
PROP_SPORTS = [s.strip() for s in
               os.environ.get("PROP_SPORTS", "basketball_nba").split(",") if s.strip()]

# How often the live site re-scans on its own, in hours. The page
# re-scans when the last scan is older than this, so a daily visitor
# always sees fresh plays without clicking Refresh. Default 48 hours:
# a full scan (game lines + 3 NBA prop events) costs about 27 credits,
# so a daily scan is about 810/month and does NOT fit the free 500
# credits/month. Every other day is about 405/month and fits. Set it
# to 24 for a true daily refresh (needs the paid plan or accepts the
# cap), or 0 to only refresh on the Refresh button.
AUTO_REFRESH_HOURS = float(os.environ.get("AUTO_REFRESH_HOURS", "48"))
PROP_SPORTS = [s.strip() for s in
               os.environ.get("PROP_SPORTS", "basketball_nba").split(",") if s.strip()]

STATE = {
    # The latest scan result. The page reads from this, so a refresh
    # updates the page without restarting the app.
    "plays": [],
    "mode": "demo",
    "updated": None,
    "credits_remaining": None,
    "error": None,
}


def _fmt_price(price):
    # Jinja prints -110 fine but drops the plus on +150, so add it here.
    return f"+{price}" if price > 0 else str(price)


app.jinja_env.filters["fmt_price"] = _fmt_price


def scan(force=False):
    """Refresh the scan. Live when a key exists, demo otherwise.

    A fresh fetch only happens when the last scan is older than
    AUTO_REFRESH_HOURS, or when forced (the Refresh button). The last
    scan time lives in data/, so it survives an app restart and a cold
    start does not burn credits on every visit.
    """
    if not API_KEY:
        STATE["plays"] = find_ev(demo_data.demo_events(), MIN_EDGE)
        STATE["mode"] = "demo"
        STATE["updated"] = "demo data (no API key set)"
        STATE["credits_remaining"] = None
        STATE["error"] = None
        return

    from datetime import datetime, timedelta, timezone

    # How old is the last scan? (data/last_scan.json, written after
    # every fresh fetch.) None means no scan has ever run.
    last, last_age = odds_api.load_cache("last_scan")
    due = force or last_age is None or last_age > AUTO_REFRESH_HOURS * 3600
    window = AUTO_REFRESH_HOURS * 3600

    events = []
    remaining = None
    errors = []
    fetched_at = None

    if not due:
        # Serve the last scan from disk. No credits spent.
        for label in odds_api.SPORTS:
            cached, _age = odds_api.load_cache(f"bulk_{label}", max_age=window)
            if cached:
                events.extend(cached)
        props, _age = odds_api.load_cache("props_all", max_age=window)
        if props:
            events.extend(props)
        if isinstance(last, dict):
            remaining = last.get("credits_remaining")
        if last_age is not None:
            fetched_at = datetime.now(timezone.utc) - timedelta(seconds=last_age)
    else:
        # Fresh fetch: game lines first, then props.
        fetched_any = False
        for label, sport_key in odds_api.SPORTS.items():
            cached, _age = odds_api.load_cache(f"bulk_{label}")
            if cached is not None and not force:
                events.extend(cached)
                continue
            try:
                data, remaining, _used = odds_api.fetch_bulk_odds(API_KEY, sport_key)
            except Exception as exc:  # noqa: BLE001 - surface it, don't crash
                errors.append(f"{label}: {exc}")
                # Fall back to the last good lines for this sport, even
                # if they are older than the 15 minute cache.
                stale, _age = odds_api.load_cache(f"bulk_{label}", max_age=window)
                if stale:
                    events.extend(stale)
                continue
            odds_api.save_cache(f"bulk_{label}", data)
            events.extend(data)
            fetched_any = True

        # Player props, only if enabled and the balance allows it.
        prop_events = []
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
                    prop_events.append(prop_event)
                    fetched_any = True
                    if remaining is not None and remaining <= odds_api.PROP_CREDIT_FLOOR:
                        break
        odds_api.save_cache("props_all", prop_events)
        # Only mark the scan done if at least one fresh fetch worked.
        # A failed scan does not count, so the next visit retries.
        if fetched_any:
            odds_api.save_cache("last_scan", {"ok": True, "credits_remaining": remaining})
        fetched_at = datetime.now(timezone.utc)

    STATE["plays"] = find_ev(events, MIN_EDGE)
    STATE["mode"] = "live"
    STATE["updated"] = (fetched_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    STATE["credits_remaining"] = remaining
    STATE["error"] = "; ".join(errors) if errors else None


@app.route("/")
def index():
    # Home page: the plays table, filtered by sport (?sport=nba etc.).
    # Re-scan on every visit, but scan() only spends credits when the
    # last scan is older than AUTO_REFRESH_HOURS.
    scan()
    sport = request.args.get("sport", "all")
    plays = [p for p in STATE["plays"] if sport == "all" or p["sport"] == sport]
    return render_template(
        "index.html", plays=plays, state=STATE, sport=sport, min_edge=MIN_EDGE
    )


@app.route("/refresh", methods=["POST"])
def refresh():
    # Re-run the scan and send the browser back to the table.
    # Bust every cache on a manual refresh so it pulls fresh lines.
    for name in [f"bulk_{label}" for label in odds_api.SPORTS] + ["props_all", "last_scan"]:
        path = os.path.join(odds_api.CACHE_DIR, f"{name}.json")
        try:
            os.remove(path)
        except OSError:
            pass
    scan(force=True)
    return redirect(url_for("index", sport=request.form.get("sport", "all")))


@app.route("/api/ev")
def api_ev():
    # The same data as JSON, for anything that wants to read it as data.
    return jsonify({
        "mode": STATE["mode"],
        "updated": STATE["updated"],
        "credits_remaining": STATE["credits_remaining"],
        "min_edge": MIN_EDGE,
        "plays": STATE["plays"],
    })


# Scan once when the app starts. This matters on Azure: gunicorn imports
# this file but never runs the __main__ block below, so without this the
# page would load empty until someone hit Refresh.
scan()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
