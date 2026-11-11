# EV Finder — daily +EV plays for NBA, NCAAB and NFL

A small Python program that scans live odds every day, finds the plays
where a book's price is better than the consensus fair price, and shows
them on a simple web page. No bet is ever placed automatically — it only
finds and displays.

## The honest part first: Covers and Optimal have no API

The two models from the other day — **Covers** and **Optimal** — do not
offer a public API, so no program can call them directly. Their numbers
only live on their web pages.

So this program uses the same *method* Optimal uses, built on live odds:

1. Pull every book's price for every game line and prop from
   **The Odds API** (free tier: 500 credits/month, no card).
2. Strip the vig out of each book's two-way price pair, so each side
   gets a fair probability.
3. Take the **median** fair probability across all books — that is the
   consensus. The median is used so one wild book can't drag it around.
4. Flag a play when a book's price implies a lower chance of winning
   than the consensus fair chance, by at least 3% (configurable).
   A 5%+ edge gets tagged SOLID; 3–5% gets tagged THIN.

That is exactly what "positive EV" means here: if the consensus is
right, the book is paying you more than the outcome is worth.

**The assumption every flagged play rests on:** the consensus is right.
If the consensus is wrong, the edge is wrong. A single prop can miss
by a wide margin even when the math is right on average. Never size a
thin edge with a big stake, and re-check the line before you bet it —
a moved line can erase a thin edge.

## Quick start (local)

```bash
cd ev-finder
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# Demo mode (no key): runs on built-in sample lines so you can see it work
venv/bin/python app.py
# -> http://127.0.0.1:5000

# Live mode: get a free key at https://the-odds-api.com/
# (sign up, copy the key — 500 credits/month, no card required)
export ODDS_API_KEY=your_key_here
venv/bin/python app.py
```

### Windows (Command Prompt or PyCharm)

```bat
cd ev-finder
python -m venv venv
venv\Scripts\pip install -r requirements.txt

:: Demo mode (no key): runs on built-in sample lines so you can see it work
venv\Scripts\python app.py
:: -> http://127.0.0.1:5000

:: Live mode: set the key first (your real key, no quotes needed)
set ODDS_API_KEY=your_key_here
venv\Scripts\python app.py
```

In PyCharm, set the key in the run configuration instead of the command
line: Run → Edit Configurations → app → Environment variables →
ODDS_API_KEY. PyCharm creates the venv from the interpreter settings,
so the `python -m venv` line above is only for the command line.

What you get on screen: sport, game, market, the play, the book, the
book's price, the fair price, the edge, the EV per $1, and a SOLID /
THIN tag. Filter by NBA / NCAAB / NFL, hit **Refresh lines** to pull
fresh odds, or read the same data as JSON at `/api/ev`.

## Credit budget (free tier, 500/month)

| What | Cost per refresh |
|---|---|
| Game lines (moneyline, spread, total) — 1 sport | 3 credits (1 per market, US region) |
| Game lines — all 3 sports | 9 credits |
| Daily refresh, all 3 sports | ~270 credits/month |
| Player props — 1 NBA event (7 markets) | ~6 credits (observed 2026-11-11) |
| Player props — 3 NBA events | ~18 credits per scan |
| Daily auto-refresh, lines + 3 NBA prop events | ~27/day, ~810/month — over the free tier |
| Every-other-day auto-refresh (AUTO_REFRESH_HOURS=48) | ~405/month — fits the free tier |

Game lines are cached 15 minutes so page refreshes don't burn credits.
Player props are on for NBA only, 3 upcoming events
(`PROP_EVENTS=3`, `PROP_SPORTS=basketball_nba`). They cost more credits
per event (about 6 credits for one NBA event with 7 prop markets,
observed 2026-11-11), so the scan stops fetching props if the monthly
balance drops below 50 credits. The page shows the remaining balance.
Set `PROP_EVENTS=0` to turn them off, or add a sport key to
`PROP_SPORTS` (e.g. `basketball_ncaab`) to include it.

## Daily display on the live site

Yes — once the key is set, the site looks after itself. The app scans
once at startup and re-scans whenever the last scan is older than
`AUTO_REFRESH_HOURS` (default 48 hours), so a visitor always sees
fresh plays without clicking anything. The Refresh button forces a
fresh scan on demand. The last scan time is saved on disk, so an app
restart serves the last good scan instead of paying for a fresh fetch.

The catch is the credit budget: one full scan (game lines + 3 NBA prop
events) costs about 27 credits. A true daily refresh is about
810/month, which does NOT fit the free 500 credits/month. The default
48 hours (about 405/month) fits. To get a true daily refresh, set
`AUTO_REFRESH_HOURS=24` and either upgrade to the paid plan (20,000
credits/month from $30/month) or accept that the free key runs out
mid-month.

## GitHub Pages (static site + scheduled scan)

GitHub Pages cannot run Python or keep a secret, so the Flask app
itself cannot live there. What can live there is a static page that a
scheduled GitHub Actions run refreshes once a day. That is what
`docs/index.html`, `docs/plays.json`, `scan_static.py` and
`.github/workflows/scan.yml` are for.

Setup (once):

1. Push this repo to GitHub as a **PUBLIC** repo (free GitHub Pages
   needs a public repo).
2. Repo Settings → Secrets and variables → Actions → New repository
   secret: `ODDS_API_KEY` = your key from the-odds-api.com. The key
   never appears in the code or the published page.
3. Repo Settings → Pages → Source: Deploy from a branch → Branch:
   main, Folder: /docs. The site is then live at
   `https://<your-username>.github.io/<repo-name>/`.

The workflow runs daily at 12:00 UTC (6:00 AM CST / 7:00 AM CDT) and
can also be started by hand from the repo's Actions tab
(workflow_dispatch). Each run fetches live odds, finds the plays, and
commits the updated `docs/plays.json`, which the page reads.

Trade-offs vs the Flask app: the page updates on the schedule, not on
demand — there is no live Refresh button. One run costs about 15
credits (9 for game lines + about 6 for 1 NBA prop event), so daily
runs are about 450/month and fit the free 500. To scan more prop
events, raise `PROP_EVENTS` in the workflow file — 3 events is about
810/month and does not fit the free tier.

Alternative that keeps the live app: Render.com's free web service
runs the Flask app as-is from the GitHub repo (build
`pip install -r requirements.txt`, start
`gunicorn --bind=0.0.0.0:8000 --timeout 600 app:app`), with the key as
an environment variable in the Render dashboard. It sleeps after 15
minutes idle and takes about a minute to wake, like Azure's free tier.

## Running scans from Muse (connector)

The key is also stored in Muse's Secure Vault as the
`custom.the-odds-api` connector, so Koda can run scans without the key
being pasted again. The connector helper is at
`~/workspace/custom-connectors/custom.the-odds-api/bin/odds_fetch.py`
and the scan driver is `scan_connector.py`:

```bash
python3 scan_connector.py --sports nba --prop-events 3
```

`--sports` takes a comma-separated list of `nba`, `ncaab`, `nfl`.
On Azure the app still uses the `ODDS_API_KEY` app setting — the
connector is only for scans run from Muse.

## How the math works (plain language)

- **Implied probability:** a -110 price means the book thinks that side
  wins 52.4% of the time (you'd need to win that often just to break
  even). A +130 price implies 43.5%.
- **Vig removal:** a book's two sides always add up to more than 100%
  (e.g. 52.4% + 52.4% = 104.8%). That extra 4.8% is the book's margin.
  Dividing each side by the total removes it, leaving fair probabilities
  that sum to 100%.
- **Consensus:** the median fair probability across books. If five books
  say a side wins 48–52% of the time, the consensus is ~50%.
- **Edge:** consensus fair probability minus the implied probability of
  the book's price. If the consensus says 50% and a book offers +130
  (43.5% implied), the edge is 6.5 points — the book is paying you as
  if the side wins less often than the market thinks it does.
- **EV per $1:** fair probability × decimal odds − 1. At 50% fair and
  +130 (2.30 decimal), EV = 0.50 × 2.30 − 1 = +15%.

## Deploy to a free Azure web app

Free tier: Azure App Service **F1 (Free)**. It sleeps after 20 minutes
idle, so the first load after a while is slow — fine for a personal tool.

```bash
# One-time: install the Azure CLI and log in
az login

# Create a resource group and a free-tier web app (Python 3.12)
az group create --name ev-finder-rg --location eastus
az appservice plan create --name ev-finder-plan --resource-group ev-finder-rg --sku F1 --is-linux
az webapp create --name ev-finder-alex --resource-group ev-finder-rg --plan ev-finder-plan --runtime "PYTHON:3.12"

# Tell Azure your API key (never put it in the code), plus the refresh
# interval: 48 hours by default so a full scan stays inside the free
# 500 credits/month. Set AUTO_REFRESH_HOURS=24 for a true daily refresh.
az webapp config appsettings set --name ev-finder-alex --resource-group ev-finder-rg \
  --settings ODDS_API_KEY=your_key_here MIN_EDGE=0.03 AUTO_REFRESH_HOURS=48

# Deploy the code
az webapp up --name ev-finder-alex --resource-group ev-finder-rg --runtime "PYTHON:3.12"

# Tell Azure how to start it (or set this in the portal: Configuration → General settings → Startup Command)
az webapp config set --name ev-finder-alex --resource-group ev-finder-rg \
  --startup-file "gunicorn --bind=0.0.0.0 --timeout 600 app:app"
```

Your app will live at `https://ev-finder-alex.azurewebsites.net`
(use your own app name — it must be unique across all of Azure).

### Portal (no CLI)

If the command line sign-in is blocked, do it in the portal instead:

1. **Resource group:** left menu → Resource groups → + Create →
   name `ev-finder-rg`, region East US → Review + create → Create.
2. **Web app:** left menu → App Services → + Create → Web App →
   resource group `ev-finder-rg`, name `ev-finder-alex`, Publish Code,
   Runtime stack Python 3.12, OS Linux, region East US. For the plan,
   click Create new → name `ev-finder-plan` → change the pricing tier
   to F1 Free (Explore pricing plans → Dev / Test tab → F1) →
   Review + create → Create.
3. **Startup command:** open the web app → Configuration → General
   settings → Startup Command →
   `gunicorn --bind=0.0.0.0:8000 --timeout 600 app:app` → Save.
4. **Settings:** same Configuration page → Application settings → add
   `ODDS_API_KEY` (your key), `MIN_EDGE` = `0.03`,
   `AUTO_REFRESH_HOURS` = `48` → Save.
5. **Code:** open `https://ev-finder-alex.scm.azurewebsites.net/ZipDeployUI`
   and drag the deploy zip (files at the root, no folder prefix) onto
   the page. Wait for the build to finish — it installs requirements
   automatically. Then open `https://ev-finder-alex.azurewebsites.net`.

## Point it at your own domain (sotonetworks.com)

You manage sotonetworks.com DNS in Cloudflare, so:

1. **Cloudflare → your sotonetworks.com zone → DNS → Add record:**
   - Type: `CNAME`, Name: `ev`, Target: `ev-finder-alex.azurewebsites.net`
   - Set the proxy to **DNS only** (grey cloud) for now — Azure needs to
     see the raw record to verify you own the domain.
   - TTL: Auto.
2. **Azure Portal → your web app → Custom domains → Add custom domain:**
   - Enter `ev.sotonetworks.com` and validate. Azure checks the CNAME.
   - Once it shows as verified, the app answers at
     `https://ev.sotonetworks.com`.
3. **Back in Cloudflare**, you can turn the proxy back on (orange cloud)
   if you want Cloudflare's caching/protection in front of it.
4. Azure issues a free TLS certificate for the custom domain
   automatically once it is verified (Portal → Custom domains → the
   domain shows HTTPS).

Pick any subdomain you like instead of `ev` — `bets`, `plays`,
`edge` — the steps are the same.

## Files

| File | What it does |
|---|---|
| `app.py` | Flask web app: page, refresh, JSON API |
| `ev.py` | The math: odds conversion, vig removal, consensus, edge |
| `odds_api.py` | The Odds API client + 15-minute cache + credit tracking |
| `demo_data.py` | Sample lines so it runs with no key (clearly labeled DEMO) |
| `templates/index.html` | The page: table, filters, refresh button |
| `requirements.txt` | flask, requests, gunicorn |
| `.env.example` | Settings template — copy to `.env`, never commit a real key |

## What this does not do

- It does **not** call Covers or Optimal — they have no public API.
- It does **not** place bets. Every placement stays behind your
  explicit yes on the exact legs, price, and amount.
- It does **not** predict games. It finds prices that disagree with
  the market consensus. The consensus can be wrong.
- It does **not** use a "backtested system" or a tout's picks. There
  is no verified backtested system behind this — it is consensus math
  on live odds.
