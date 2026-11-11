"""Positive-EV math for the EV Finder.

The method is the one Optimal uses, and the one Covers' projections are
checked against: compare each book's price to the *consensus fair price*.

1. Convert every book's American price to an implied probability.
2. For each two-way market (moneyline, spread, total, prop over/under),
   strip the vig from each book's pair so the two sides sum to 100%.
   That gives each book's fair probability for each side.
3. The consensus fair probability for a side is the MEDIAN of the books'
   fair probabilities. The median is used (not the mean) so one wild
   outlier book cannot drag the consensus around.
4. Edge = consensus fair probability - implied probability of the book's
   price. If edge >= the threshold, the play is flagged +EV.
5. EV per $1 staked = fair_prob * decimal_odds - 1.

The assumption every flagged play rests on: the consensus is right.
If the consensus is wrong, the edge is wrong. A single prop can miss
by a wide margin even when the model is right on average.
"""

import statistics

# How far above the consensus a price must be to count as +EV.
DEFAULT_MIN_EDGE = 0.03
# Edge at or above this gets the SOLID tag; below it is THIN.
SOLID_EDGE = 0.05

# Readable labels for market keys returned by The Odds API.
MARKET_LABELS = {
    "h2h": "Moneyline",
    "spreads": "Spread",
    "totals": "Total",
    "player_points": "Player points",
    "player_rebounds": "Player rebounds",
    "player_assists": "Player assists",
    "player_threes": "Player threes",
    "player_points_rebounds_assists": "Player pts+reb+ast",
    "player_blocks": "Player blocks",
    "player_steals": "Player steals",
    "player_pass_yards": "Player pass yards",
    "player_rush_yards": "Player rush yards",
    "player_receptions": "Player receptions",
    "player_reception_yards": "Player rec yards",
    "player_pass_tds": "Player pass TDs",
    "player_rush_tds": "Player rush TDs",
    "player_anytime_td": "Player anytime TD",
}


def american_to_decimal(american):
    """+150 -> 2.5, -110 -> 1.909."""
    american = int(american)
    if american > 0:
        return 1.0 + american / 100.0
    return 1.0 + 100.0 / abs(american)


def decimal_to_american(decimal):
    """2.5 -> +150, 1.909 -> -110."""
    if decimal >= 2.0:
        return int(round((decimal - 1.0) * 100))
    return -int(round(100.0 / (decimal - 1.0)))


def american_to_implied(american):
    """Implied probability of an American price: -110 -> 0.5238."""
    return 1.0 / american_to_decimal(american)


def fair_price_from_prob(prob):
    """The American price that would be fair for this probability."""
    return decimal_to_american(1.0 / prob)


def remove_vig(p_a, p_b):
    """Strip the book's margin so two sides sum to 100%."""
    total = p_a + p_b
    return p_a / total, p_b / total


def ev_per_dollar(fair_prob, decimal_odds):
    """Expected profit per $1 staked: +0.10 means +10% EV."""
    return fair_prob * decimal_odds - 1.0


def _outcome_label(market_key, outcome):
    """Turn one side of a market into words the table can show."""
    # Moneylines are just team names. Spreads and totals need their
    # number. Props need the player name plus Over or Under.
    point = outcome.get("point")
    name = outcome.get("name", "")
    desc = outcome.get("description", "")
    if market_key == "h2h":
        return name
    if market_key == "spreads":
        return f"{name} {point:+}" if point is not None else name
    if market_key == "totals":
        return f"{name} {point}" if point is not None else name
    # Player props: name is the player, description is Over/Under.
    if desc:
        return f"{name} {desc} {point}" if point is not None else f"{name} {desc}"
    return f"{name} {point}" if point is not None else name


def find_ev(events, min_edge=DEFAULT_MIN_EDGE):
    """Scan Odds API event payloads and return flagged +EV plays.

    events: list of event dicts in The Odds API v4 shape
        (bulk /odds response, or a single per-event response wrapped
        in a list). Only two-way markets are used.
    min_edge: minimum edge in probability points (0.03 = 3%).

    Returns a list of play dicts, sorted by edge descending.
    """
    # Group the two sides of each market: key -> group record.
    # The key is (event, market, line) so a -5.5 spread and a -6.5
    # spread are never mixed into one consensus.
    groups = {}
    for event in events or []:
        for book in event.get("bookmakers", []):
            for market in book.get("markets", []):
                outcomes = market.get("outcomes", [])
                if len(outcomes) != 2:
                    continue  # only two-way markets (skip outrights etc.)
                key = (event.get("id"), market.get("key"), outcomes[0].get("point"))
                group = groups.setdefault(key, {
                    "event": event,
                    "market_key": market.get("key"),
                    "point": outcomes[0].get("point"),
                    "books": {},
                })
                group["books"][book.get("title", book.get("key", "Book"))] = outcomes

    plays = []
    for (event_id, market_key, point), group in groups.items():
        event = group["event"]
        # Step 1-2: each book's no-vig fair probability per side.
        fair_by_side = [{}, {}]  # side index -> {book: fair prob}
        implied_by_side = [{}, {}]
        for book_title, outcomes in group["books"].items():
            try:
                p0 = american_to_implied(outcomes[0]["price"])
                p1 = american_to_implied(outcomes[1]["price"])
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                continue
            f0, f1 = remove_vig(p0, p1)
            fair_by_side[0][book_title] = f0
            fair_by_side[1][book_title] = f1
            implied_by_side[0][book_title] = p0
            implied_by_side[1][book_title] = p1

        # Step 3-5: consensus per side, then flag books beating it.
        # A book only counts if at least two books priced the market,
        # so one book alone can never create its own edge.
        for side in (0, 1):
            fairs = fair_by_side[side]
            if len(fairs) < 2:
                continue  # need at least two books for a consensus
            consensus = statistics.median(fairs.values())
            for book_title, implied in implied_by_side[side].items():
                edge = consensus - implied
                if edge < min_edge:
                    continue
                outcomes = group["books"][book_title]
                outcome = outcomes[side]
                decimal = american_to_decimal(outcome["price"])
                plays.append({
                    "sport": event.get("sport_key", ""),
                    "sport_title": event.get("sport_title", ""),
                    "event": f"{event.get('away_team', '')} @ {event.get('home_team', '')}",
                    "commence_time": event.get("commence_time", ""),
                    "market": MARKET_LABELS.get(market_key, market_key),
                    "outcome": _outcome_label(market_key, outcome),
                    "book": book_title,
                    "book_price": int(outcome["price"]),
                    "fair_price": fair_price_from_prob(consensus),
                    "consensus_prob": round(consensus, 4),
                    "implied_prob": round(implied, 4),
                    "edge_pp": round(edge * 100, 2),
                    "ev_pct": round(ev_per_dollar(consensus, decimal) * 100, 1),
                    "tag": "SOLID" if edge >= SOLID_EDGE else "THIN",
                })

    plays.sort(key=lambda p: p["edge_pp"], reverse=True)
    return plays
