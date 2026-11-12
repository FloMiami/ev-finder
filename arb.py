"""True arbitrage detection for the EV Finder.

An arbitrage (arb) is the one bet that cannot lose. It happens when the
best price on EACH side of a two-way market, taken from different books,
implies a combined probability under 100%. Bet both sides in the right
proportions and the payout is identical no matter which side wins.

The math (no model, no prediction, just arithmetic):
  dA, dB = best decimal odds on side A and side B, across all books
  pA = 1/dA, pB = 1/dB (implied probabilities)
  it is an arb when pA + pB < 1
  guaranteed ROI on total stake = (1 - (pA + pB)) / (pA + pB)
  for a $100 total stake: stake A = 100 * pA/(pA+pB),
                          stake B = 100 * pB/(pA+pB)

Two honest caveats. First, arbs are rare on soft US books (DraftKings,
FanDuel, BetMGM and the rest) because they copy each other's lines, so
most scans find none. Second, when one does appear it is usually small
(under 1%), and books limit or ban accounts that bet arbs repeatedly.
The math is guaranteed; getting both bets down at the quoted prices
before either line moves is the hard part.
"""

from ev import MARKET_LABELS, _outcome_label, american_to_decimal

# Total stake the reported split is calculated for.
STAKE_TOTAL = 100.0


def _line_key(outcomes):
    """The line that defines one market, independent of outcome order.

    Totals, props and moneylines have the same point on both sides, so
    the key is just that point. Spreads have opposite points (-5.5 and
    +5.5), and books do not all list them in the same order, so the key
    is the sorted pair of points — the same market whichever way a book
    lists its two sides.
    """
    points = sorted(
        (o.get("point") for o in outcomes),
        key=lambda p: (p is None, p),
    )
    return tuple(points)


def _side_key(outcome):
    """What identifies one side of a market: who/what, Over or Under,
    and the line. Moneylines have no description or point; totals use
    Over/Under as the name; props use the player name plus Over/Under.
    """
    return (
        outcome.get("name", ""),
        outcome.get("description", ""),
        outcome.get("point"),
    )


def _group_markets(events):
    """Group every two-way market the same way ev.find_ev does: one
    group per event + market + line, with each book's two outcomes.
    Only two-way markets are used, and a group needs at least two
    books before it can produce anything (one book alone can never
    create its own edge — its two sides always sum past 100%).
    """
    groups = {}
    for event in events or []:
        for book in event.get("bookmakers", []):
            for market in book.get("markets", []):
                outcomes = market.get("outcomes", [])
                if len(outcomes) != 2:
                    continue  # only two-way markets (skip outrights etc.)
                key = (event.get("id"), market.get("key"), _line_key(outcomes))
                group = groups.setdefault(key, {
                    "event": event,
                    "market_key": market.get("key"),
                    "books": {},
                })
                group["books"][book.get("title", book.get("key", "Book"))] = outcomes
    return groups


def _best_per_side(group):
    """Best decimal odds on each side of one market, across all books.

    Returns {side_key: (decimal, book_title, price, outcome)} with the
    highest decimal price seen for that side. A side only counts if at
    least one book priced it.
    """
    best = {}
    for book_title, outcomes in group["books"].items():
        for outcome in outcomes:
            try:
                decimal = american_to_decimal(outcome["price"])
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                continue
            key = _side_key(outcome)
            if key not in best or decimal > best[key][0]:
                best[key] = (decimal, book_title, int(outcome["price"]), outcome)
    return best


def _combined_implied(group):
    """Combined implied probability of the best price on each side.

    Returns None when the group cannot be priced (fewer than two books
    or a side missing). Otherwise returns a dict with both sides, both
    books, both prices and the combined implied probability.
    """
    if len(group["books"]) < 2:
        return None
    best = _best_per_side(group)
    if len(best) < 2:
        return None
    # Exactly two sides in a two-way market; take them in a fixed order
    # so the same market always reports the same side A and side B.
    side_keys = sorted(best.keys())
    dec_a, book_a, price_a, out_a = best[side_keys[0]]
    dec_b, book_b, price_b, out_b = best[side_keys[1]]
    if book_a == book_b:
        # Both best prices at one book: that book's own two sides always
        # sum past 100% (its margin), so this can never be an arb.
        return None
    p_a = 1.0 / dec_a
    p_b = 1.0 / dec_b
    return {
        "event": group["event"],
        "market_key": group["market_key"],
        "side_a": (out_a, book_a, price_a, dec_a, p_a),
        "side_b": (out_b, book_b, price_b, dec_b, p_b),
        "combined": p_a + p_b,
    }


def _arb_dict(info, stake_total=STAKE_TOTAL):
    """Turn one priced market into the arb record the page renders.

    The stake split is sized so the payout is identical whichever side
    wins: stake A * dA = stake B * dB = stake_total / (pA + pB).
    """
    event = info["event"]
    market_key = info["market_key"]
    out_a, book_a, price_a, _dec_a, p_a = info["side_a"]
    out_b, book_b, price_b, _dec_b, p_b = info["side_b"]
    combined = info["combined"]
    roi = (1.0 - combined) / combined
    stake_a = stake_total * p_a / combined
    stake_b = stake_total * p_b / combined
    return {
        "sport": event.get("sport_key", ""),
        "sport_title": event.get("sport_title", ""),
        "event": f"{event.get('away_team', '')} @ {event.get('home_team', '')}",
        "commence_time": event.get("commence_time", ""),
        "market": MARKET_LABELS.get(market_key, market_key),
        # Side A also fills the plain outcome/book fields so an arb
        # looks like a play to anything that only reads those.
        "outcome": _outcome_label(market_key, out_a),
        "book": book_a,
        "prices": [price_a, price_b],
        "outcome_a": _outcome_label(market_key, out_a),
        "book_a": book_a,
        "price_a": price_a,
        "outcome_b": _outcome_label(market_key, out_b),
        "book_b": book_b,
        "price_b": price_b,
        "combined_implied_pct": round(combined * 100, 2),
        "roi_pct": round(roi * 100, 2),
        "stake_a": round(stake_a, 2),
        "stake_b": round(stake_b, 2),
        "guaranteed_profit": round(stake_total * roi, 2),
    }


def find_arbs(events, stake_total=STAKE_TOTAL):
    """Scan Odds API event payloads and return true arbitrages.

    events: list of event dicts in The Odds API v4 shape (bulk /odds
        response, or a single per-event response wrapped in a list).
    stake_total: total stake the reported split is sized for ($100).

    Returns a list of arb dicts, sorted by guaranteed ROI descending.
    Most scans return an empty list — that is the normal result on
    soft US books, not a bug.
    """
    arbs = []
    for group in _group_markets(events).values():
        info = _combined_implied(group)
        if info is None or info["combined"] >= 1.0:
            continue
        arbs.append(_arb_dict(info, stake_total))
    arbs.sort(key=lambda a: a["roi_pct"], reverse=True)
    return arbs


def find_near_arbs(events, limit=10):
    """Closest near-arbs: every market's combined implied probability,
    smallest first. A true arb is under 100%; the entries just above
    100% show how far the board was from one. Used for diagnostics —
    the page only ever shows real arbs.
    """
    rows = []
    for group in _group_markets(events).values():
        info = _combined_implied(group)
        if info is None:
            continue
        event = info["event"]
        out_a, book_a, price_a, _d_a, _p_a = info["side_a"]
        out_b, book_b, price_b, _d_b, _p_b = info["side_b"]
        rows.append({
            "sport": event.get("sport_key", ""),
            "sport_title": event.get("sport_title", ""),
            "event": f"{event.get('away_team', '')} @ {event.get('home_team', '')}",
            "market": MARKET_LABELS.get(info["market_key"], info["market_key"]),
            "outcome_a": _outcome_label(info["market_key"], out_a),
            "book_a": book_a,
            "price_a": price_a,
            "outcome_b": _outcome_label(info["market_key"], out_b),
            "book_b": book_b,
            "price_b": price_b,
            "combined_implied_pct": round(info["combined"] * 100, 2),
        })
    rows.sort(key=lambda r: r["combined_implied_pct"])
    return rows[:limit]
