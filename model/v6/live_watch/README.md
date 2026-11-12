# Live move-watcher — v6 ≥ 2.0 rule, paper only

## Read this first — this is NOT a verified winner

The ≥ 2.0 rule is a **lead**, not a winner. It comes from the v6
Oddize rerun (`../oddize_rerun/BACKTEST_V6_ODDIZE.md`): 543 bets,
57.1% win rate, 95% CI 52.9–61.2%, ROI +11.03%, consistent in both
seasons. But it was **one cell of a 16-cell grid**, it does not
survive a multiple-comparisons correction, and picking it after
seeing it win is tuning on the test set. The pre-registered primary
rule (≥ 1.0) returned only +3.36% ROI on 1,349 bets, with a
confidence interval that includes break-even.

This folder is the **fresh-data test** of the ≥ 2.0 lead. The record
starts at 0-0-0 and means nothing until 50–100 flags have settled.
Do not bet on these flags. Do not push anything on them.

## How it works

Every morning, `watch.py` reads today's NBA board from Oddize
(1 credit) and flags the games where the spread line has moved at
least 2.0 points from the open. The next morning, `grade.py`
settles yesterday's flags from ESPN's free scoreboard (0 credits)
and updates the running record. One flag per game per day.

The watcher flags the move **as it happens, mid-flight** — better
than betting the close (where the rerun proved most of the value
is already gone), worse than the open (where the sharp money's
+13.53% edge lives). That is the whole point of watching live.

## Run it

From this folder:

```text
python3 grade.py    # first, next morning — settles yesterday's flags, 0 credits
python3 watch.py    # then the morning check — 1 Oddize credit, flags today's games
python3 sync_docs.py  # copy the log into docs/ so the site can show it
```

No cron, no scheduled job. A daily run needs Alex's approval
naming the schedule — it has not been given.

## The site shows it too

The page (`docs/index.html`) has a "Line-move model — forward test"
section that reads `docs/watch-log.json`: today's ungraded flags
with the game, side, line, price, book, open line → current line,
median move, books at threshold, and the reason in plain language,
plus the record line (W-L-P, units, ROI) and a history table of
graded flags. It hides itself when the log is missing or empty.
`sync_docs.py` is the bridge: it copies this folder's
`watch-log.json` into `docs/watch-log.json` and checks both copies
still parse and hold the same number of flags.

## The daily run in GitHub Actions

The workflow (`.github/workflows/scan.yml`) runs the watcher steps
after the EV scan, all guarded so a watcher failure can never fail
the scan:

1. **Grade** — `grade.py` runs every day, free, no key needed.
2. **Flag** — `watch.py` runs only when the `ODDIZE_API_KEY`
   repository secret exists (1 credit). Without the secret the step
   skips itself and the site keeps showing the flags from the log
   committed by hand. The script sends the secret as the `X-API-Key`
   header directly; the vault helper on this machine is the other
   route, used for the manual morning runs.
3. **Sync** — `sync_docs.py` copies the log into `docs/`.
4. **Publish** — the workflow commits `docs/watch-log.json` and the
   live-watch logs alongside the plays files.

One setup step, only if the Actions flag step is wanted: in GitHub,
repo Settings → Secrets and variables → Actions → New repository
secret, name `ODDIZE_API_KEY`, value your key from the Oddize
dashboard. The key never appears in the code or the published page.
Without it, the manual path (the three commands above, then a
normal `git add` / commit / push of `docs/watch-log.json`) is the
flagging route.

## What it flagged today (Nov 12, 2026)

Primary flag (≥ 2.0):

- **TOR +8.5 at -108 (ProphetX)** at Toronto @ Philadelphia.
  Median move +2.0 points across 11 books, 6 of them at ≥ 2.0.
  The line opened with Philadelphia -10.5 and moved to -8.5 —
  money came in on Toronto.

Diagnostic entries (≥ 1.0, kept separate so the primary record
stays clean):

- **DET -10.5 at -107 (ProphetX)** at Detroit @ Washington,
  median move +1.0.
- **MIN -5.5 at -110 (Bet365)** at Dallas @ Minnesota,
  median move -1.0.

Charlotte @ Phoenix had no move worth recording (median 0.0).
Record after the first check: 1 flag, 0 graded, 0-0-0.

## Credit math

- 1 credit per morning check (`/api/v1/odds/latest?sport=nba`).
- The balance check (`/api/v1/account/usage`) is free.
- The script refuses to spend when fewer than 10 credits remain.
- Balance after the first day's work: **56 of 1,500** (3 spent
  Nov 12: 1 exploratory fetch plus 2 watcher runs, the second to
  verify the dedupe). One check a day until the Dec 1 reset costs
  ~19 more — ample spare.
- Credits reset Dec 1, 2026.

## Two honest deviations from the backtest

**Pinnacle is not in the live feed.** Verified Nov 12: 0 of 100
events in `/odds/latest` carry a Pinnacle entry. The backtest was
Pinnacle-only, so this watcher cannot match it. It uses the
**median move across the real sportsbooks present** and records
the book that offered the flagged price. That tests "follow the
move at a soft book's current line" — the realistic executable
version — but it is a different test from the Pinnacle-only
backtest, and the record must be read as its own test.

**Polymarket and Smarkets are excluded.** They are
prediction/exchange markets, not sportsbooks, and their open_line
is not a comparable opener. Verified Nov 12: Polymarket showed a
+13.0 "move" on Detroit @ Washington and a -7.0 "move" on
Toronto @ Philadelphia. That is feed noise, not line movement.

## Files

- `watch.py` — the morning check (plain-language comments).
- `grade.py` — settles flags from ESPN the next morning.
- `sync_docs.py` — copies `watch-log.json` into `docs/` for the
  site, and verifies the copy matches the source.
- `watch-log.json` — primary ≥ 2.0 flags + running record.
- `watch-diagnostic.json` — ≥ 1.0 moves + separate record.
- `raw/` — one timestamped snapshot of each board fetch, so
  every flag can be audited back to the raw odds.
