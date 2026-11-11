"""Sample odds in The Odds API v4 shape, for running the app with no key.

DEMO DATA — these lines are made up, not real odds. They exist so the
page renders and the math can be checked before a real API key is added.
The numbers were built so a few genuine +EV rows show up:

- Lakers/Celtics total 224.5: PointsBet Over +130 (SOLID),
  BetRivers Over +120 (THIN)
- Suns/Warriors moneyline: PointsBet Suns +160 (THIN)
- Duke/UNC total 148.5: BetRivers Over +125 (SOLID)
- LeBron James Over 24.5 pts: BetRivers +115 (THIN)
- Chiefs/Bills total 48.5: BetMGM Under +125 (SOLID)
"""

# Bookmaker titles as The Odds API returns them.
DK = "DraftKings"
FD = "FanDuel"
MGM = "BetMGM"
BR = "BetRivers"
PB = "PointsBet"


def _book(title, markets):
    # One bookmaker's odds for one event: its name plus its markets.
    return {"key": title.lower().replace(" ", ""), "title": title,
            "last_update": "2026-11-11T12:00:00Z", "markets": markets}


def _h2h(home, away, home_price, away_price):
    # Moneyline market: two outcomes, one price per team.
    return {"key": "h2h", "last_update": "2026-11-11T12:00:00Z", "outcomes": [
        {"name": home, "price": home_price},
        {"name": away, "price": away_price},
    ]}


def _spread(home, away, point, home_price, away_price):
    # Spread market. The point is the HOME team's spread, e.g. -5.5
    # means home -5.5, so the away team's spread is the mirror (+5.5).
    return {"key": "spreads", "last_update": "2026-11-11T12:00:00Z", "outcomes": [
        {"name": home, "price": home_price, "point": point},
        {"name": away, "price": away_price, "point": -point},
    ]}


def _total(point, over_price, under_price):
    # Totals market: Over and Under on the same points line.
    return {"key": "totals", "last_update": "2026-11-11T12:00:00Z", "outcomes": [
        {"name": "Over", "price": over_price, "point": point},
        {"name": "Under", "price": under_price, "point": point},
    ]}


def _prop(market_key, player, point, over_price, under_price):
    # Player-prop market: one player, one stat line, Over and Under.
    return {"key": market_key, "last_update": "2026-11-11T12:00:00Z", "outcomes": [
        {"name": player, "description": "Over", "price": over_price, "point": point},
        {"name": player, "description": "Under", "price": under_price, "point": point},
    ]}


def _event(event_id, sport_key, sport_title, home, away, commence, books):
    # One game in The Odds API's shape: id, sport, teams, start time, books.
    return {
        "id": event_id,
        "sport_key": sport_key,
        "sport_title": sport_title,
        "commence_time": commence,
        "home_team": home,
        "away_team": away,
        "bookmakers": books,
    }


def demo_events():
    """Return a list of demo events across NBA, NCAAB and NFL."""
    # Lakers/Celtics: two +EV totals (PointsBet solid, BetRivers thin)
    # plus a LeBron points prop (thin). The other markets here are tight.
    lakers_celtics = _event(
        "demo-nba-1", "basketball_nba", "NBA",
        "Los Angeles Lakers", "Boston Celtics", "2026-11-11T23:30:00Z", [
            _book(DK, [
                _h2h("Los Angeles Lakers", "Boston Celtics", -150, 130),
                _spread("Los Angeles Lakers", "Boston Celtics", -5.5, -110, -110),
                _total(224.5, -110, -110),
            ]),
            _book(FD, [
                _h2h("Los Angeles Lakers", "Boston Celtics", -145, 125),
                _spread("Los Angeles Lakers", "Boston Celtics", -5.5, -112, -108),
                _total(224.5, -108, -112),
            ]),
            _book(MGM, [
                _h2h("Los Angeles Lakers", "Boston Celtics", -155, 135),
                _spread("Los Angeles Lakers", "Boston Celtics", -5.5, -115, -105),
                _total(224.5, -105, -115),
            ]),
            _book(BR, [
                _h2h("Los Angeles Lakers", "Boston Celtics", -160, 140),
                _spread("Los Angeles Lakers", "Boston Celtics", -5.5, -105, -115),
                _total(224.5, 120, -125),
            ]),
            _book(PB, [
                _total(224.5, 130, -150),
            ]),
        ])

    # LeBron prop, kept as its own event entry because the API returns
    # props from a different endpoint than game lines.
    lebron_prop = _event(
        "demo-nba-1", "basketball_nba", "NBA",
        "Los Angeles Lakers", "Boston Celtics", "2026-11-11T23:30:00Z", [
            _book(DK, [_prop("player_points", "LeBron James", 24.5, -115, -105)]),
            _book(FD, [_prop("player_points", "LeBron James", 24.5, -110, -110)]),
            _book(MGM, [_prop("player_points", "LeBron James", 24.5, -120, 100)]),
            _book(BR, [_prop("player_points", "LeBron James", 24.5, 115, -135)]),
        ])

    # Knicks/76ers: every market tight on purpose, so the table is not
    # all flags. Shows what a no-edge game looks like.
    knicks_sixers = _event(
        "demo-nba-2", "basketball_nba", "NBA",
        "New York Knicks", "Philadelphia 76ers", "2026-11-12T00:00:00Z", [
            _book(DK, [
                _h2h("New York Knicks", "Philadelphia 76ers", -130, 110),
                _spread("New York Knicks", "Philadelphia 76ers", -4.5, -110, -110),
                _total(221.5, -110, -110),
            ]),
            _book(FD, [
                _h2h("New York Knicks", "Philadelphia 76ers", -125, 105),
                _spread("New York Knicks", "Philadelphia 76ers", -4.5, -112, -108),
                _total(221.5, -110, -110),
            ]),
            _book(MGM, [
                _h2h("New York Knicks", "Philadelphia 76ers", -135, 115),
                _spread("New York Knicks", "Philadelphia 76ers", -4.5, -105, -115),
                _total(221.5, -108, -112),
            ]),
        ])

    # Warriors/Suns: one thin moneyline edge (PointsBet Suns +160).
    warriors_suns = _event(
        "demo-nba-3", "basketball_nba", "NBA",
        "Golden State Warriors", "Phoenix Suns", "2026-11-12T02:00:00Z", [
            _book(DK, [
                _h2h("Golden State Warriors", "Phoenix Suns", -150, 130),
                _spread("Golden State Warriors", "Phoenix Suns", -3.5, -110, -110),
                _total(228.5, -110, -110),
            ]),
            _book(FD, [
                _h2h("Golden State Warriors", "Phoenix Suns", -145, 125),
                _spread("Golden State Warriors", "Phoenix Suns", -3.5, -108, -112),
                _total(228.5, -110, -110),
            ]),
            _book(MGM, [
                _h2h("Golden State Warriors", "Phoenix Suns", -155, 135),
                _spread("Golden State Warriors", "Phoenix Suns", -3.5, -112, -108),
            ]),
            _book(PB, [
                _h2h("Golden State Warriors", "Phoenix Suns", -170, 160),
            ]),
        ])

    # Duke/UNC: one solid total edge (BetRivers Over 148.5 +125).
    duke_unc = _event(
        "demo-ncaab-1", "basketball_ncaab", "NCAAB",
        "Duke Blue Devils", "North Carolina Tar Heels", "2026-11-12T00:30:00Z", [
            _book(DK, [
                _h2h("Duke Blue Devils", "North Carolina Tar Heels", -200, 170),
                _total(148.5, -110, -110),
            ]),
            _book(FD, [
                _h2h("Duke Blue Devils", "North Carolina Tar Heels", -190, 160),
                _total(148.5, -112, -108),
            ]),
            _book(MGM, [
                _h2h("Duke Blue Devils", "North Carolina Tar Heels", -210, 175),
                _total(148.5, -115, -105),
            ]),
            _book(BR, [
                _total(148.5, 125, -145),
            ]),
        ])

    # Bears/Packers: no edge anywhere, another tight game for contrast.
    bears_packers = _event(
        "demo-nfl-1", "americanfootball_nfl", "NFL",
        "Green Bay Packers", "Chicago Bears", "2026-11-15T18:00:00Z", [
            _book(DK, [
                _h2h("Green Bay Packers", "Chicago Bears", -180, 155),
                _spread("Green Bay Packers", "Chicago Bears", -3.5, -110, -110),
                _total(47.5, -110, -110),
            ]),
            _book(FD, [
                _h2h("Green Bay Packers", "Chicago Bears", -175, 150),
                _spread("Green Bay Packers", "Chicago Bears", -3.5, -112, -108),
                _total(47.5, -112, -108),
            ]),
            _book(MGM, [
                _h2h("Green Bay Packers", "Chicago Bears", -185, 160),
                _spread("Green Bay Packers", "Chicago Bears", -3.5, -115, -105),
            ]),
        ])

    # Chiefs/Bills: one solid total edge (BetMGM Under 48.5 +125).
    chiefs_bills = _event(
        "demo-nfl-2", "americanfootball_nfl", "NFL",
        "Kansas City Chiefs", "Buffalo Bills", "2026-11-15T21:25:00Z", [
            _book(DK, [
                _h2h("Kansas City Chiefs", "Buffalo Bills", -120, 100),
                _total(48.5, -110, -110),
            ]),
            _book(FD, [
                _h2h("Kansas City Chiefs", "Buffalo Bills", -115, -105),
                _total(48.5, -110, -110),
            ]),
            _book(MGM, [
                _h2h("Kansas City Chiefs", "Buffalo Bills", -125, 105),
                _total(48.5, -145, 125),
            ]),
        ])

    return [lakers_celtics, lebron_prop, knicks_sixers, warriors_suns,
            duke_unc, bears_packers, chiefs_bills]
