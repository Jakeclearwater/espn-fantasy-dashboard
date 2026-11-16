# ESPN fantasy basketball: injury and waiver dashboard

A small tool that exports an ESPN fantasy basketball league to JSON and builds a single HTML page for keeping an eye on injuries, the week ahead and waivers. It checks ESPN's injury flags against the NBA's official injury report, because ESPN's flags can lag, projects your matchup day by day, suggests free agents worth adding, and looks for trades that help you without costing the other team much.

One command does everything:

```
python export.py
```

It writes these files to `out/`:

| File | Contents |
| --- | --- |
| `rosters.json` | Every team's roster, one object per player: team, slot, player, NBA team, positions, status, injury, latest note, as-of time and source |
| `free_agents.json` | The top free agents by last-15-day average, with season, last-7 and last-15 averages and percent rostered |
| `transactions.json` | Adds, drops, trades and waiver claims across the league for the last 7 days |
| `waivers.json` | Your waiver priority, the full waiver order, FAAB remaining and the next waiver run |
| `outlook.json` | The week ahead: matchup days, projected starters and points by day for you and your opponent, lineup checks and waiver suggestions |
| `trades.json` | Trade ideas with each other team: who you'd give and get, the projected effect on both teams for the rest of the season, and why |
| `news.json` | Injured regulars and who benefits, plus the tagged notes for every player |
| `players.json` | Detail for your players, your opponent's and the listed free agents: averages, per-game stats, games this week, recent games, injury details, ownership and recent notes |
| `dashboard.html` | A standalone page: the week outlook, your team and your opponent side by side, waiver suggestions, free agents and league transactions |

It also prints a short summary: your players who are out or questionable, the week outlook, the best waiver suggestions and the 10 best free agents.

## What's on the dashboard

The bar at the top picks which team to view and which to compare it with (any two teams in the league; it starts on yours and your opponent this week). Every section and sub-section has a Hide/Show button, with Expand all and Collapse all beside the pickers; the page remembers what you've closed.

- **This week.** The matchup still being played, or the next one once the current one has no games left, with the score so far, a projected finish, and a day-by-day grid of how many of each team's players have games. Days with empty lineup slots are highlighted, and a short list of lineup checks points out things like an out player in an active slot.
- **Roster sheets.** Your team and your opponent's, with each player's status, injury, latest note, games left this matchup and a source line. Toggles switch between all players and injury concerns, and between lineup order and most urgent first.
- **Player details.** Every player row, free agent and suggestion has a Details button. It opens fantasy points per game for the season, last 30, 15 and 7 days and ESPN's projection; a per-game stat line; games left this matchup (with projected start or bench for rostered players); the last five games; injury details with ESPN's expected return date; how widely the player is rostered; and the latest notes.
- **News and opportunities.** Regulars who are out this week, the minutes and shots they leave behind, and which teammates (yours, other teams' or free agents) are best placed to take them, with the projected boost. Below that, what the latest Rotowire notes say about roles, returns and teammates being out, with the sentence that shows it.
- **Who to target.** Suggested adds and drops in three lists: the best adds for the rest of the season, streamers for this matchup, and injured free agents worth watching. Each one says why and how the player can be added.
- **Free agents and transactions.** The top free agents by last-15-day average and the league's recent moves.
- **Trade ideas.** Up to five trades, one per team, that raise your projected points for the rest of the season while leaving the other team better off or close to even. Each shows both sides, the projected effect on each team, and the reasons, with a Details button for every player involved.

The dashboard file is a snapshot and doesn't update itself. For a page with a Refresh button, run:

```
python export.py --serve
```

That exports as usual, then serves the dashboard at http://127.0.0.1:8765/ and opens it in your browser. Refresh pulls fresh data from ESPN and the NBA (about 20 seconds) and reloads the page. Leave the command running while you use the page; Ctrl+C stops it. The server only accepts connections from your own computer, and the Refresh button only works from the page it serves.

The easiest way to do all of that is to double-click **Open Dashboard.command** in this folder. It opens a Terminal window, loads fresh data, starts the server and opens the dashboard in your browser. If the server is already running, it just opens the page. Keep the Terminal window open while you use the dashboard, and close it when you're done. To launch it from the Dock, drag the file to the right-hand side of the Dock.

## Online, for you and a friend

The repository includes a GitHub workflow (`.github/workflows/publish.yml`) that runs the export about every two hours and publishes the dashboard with GitHub Pages, so it's available at a link you can share, with your computer off. Anyone with the link can view it.

The quickest way to set it up is to double-click **Set Up GitHub.command**. It signs you in to GitHub (in your browser, the first time), creates the repository, pushes the code, stores your ESPN cookies as encrypted secrets straight from `fantasy_dash/local_secrets.py`, turns on Pages and publishes. Run it again whenever you want to push changes or your cookies expire. It needs GitHub's command-line tool (`brew install gh`).

To do the same by hand:

1. **Add your cookies as secrets.** In the repository, go to Settings > Secrets and variables > Actions > New repository secret. Add `ESPN_S2` and `SWID` with the same values as in `fantasy_dash/local_secrets.py`. GitHub keeps them encrypted, and they never appear in the code or the page.
2. **Turn on Pages.** Go to Settings > Pages and set Source to "GitHub Actions".
3. **Run it once.** Go to Actions > Publish dashboard > Run workflow. When it finishes (about two minutes), the page is at `https://<your-username>.github.io/<repository>/`.

To refresh it between scheduled runs, use Run workflow again; the published page links to it. When the ESPN cookies expire, update the two secrets. GitHub pauses scheduled workflows in a public repository after 60 days without any commits; if that happens, re-enable it on the Actions tab.

## Setup

You need Python 3.10 or newer, because espn-api 1.0.1 requires it. On a Mac, `brew install python@3.12` installs it.

```
cd espn-fantasy-dashboard
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Then put your two ESPN cookies in `fantasy_dash/local_secrets.py`, as described below (that file is git-ignored, so it never leaves your computer). The league ID (294680752), season (2027, which espn-api uses for the 2026-27 season) and default team are set in `fantasy_dash/config.py`.

## Finding espn_s2 and SWID

The league is private, so ESPN only shares its data with a signed-in member. The tool authenticates with two cookies from your browser.

1. Sign in at [espn.com](https://www.espn.com) and open your league on fantasy.espn.com.
2. Open the browser's developer tools:
   - **Chrome or Edge:** press Cmd+Option+I (Mac) or F12 (Windows), go to the **Application** tab, then expand **Cookies** under Storage in the left panel.
   - **Firefox:** press Cmd+Option+I or F12, go to the **Storage** tab, then expand **Cookies**.
   - **Safari:** first turn on Settings > Advanced > "Show features for web developers". Then use Develop > Show Web Inspector, go to the **Storage** tab, then expand **Cookies**.
3. Select `https://fantasy.espn.com` (or `https://www.espn.com`) and find the cookies named `espn_s2` and `SWID`.
4. Paste each value between the quotes in `fantasy_dash/local_secrets.py` (create it if it's missing):

   ```python
   ESPN_S2 = "AEB...very long value...%3D"
   SWID = "{1A2B3C4D-....}"
   ```

   Paste `espn_s2` exactly as shown, including any `%` characters. `SWID` includes curly braces; if you leave them off, the tool adds them back.

The tool never prints the cookie values. The cookies stop working when you sign out of ESPN or after a few months. If you see "ESPN refused access", copy fresh values.

## Options

| Flag | Default | What it does |
| --- | --- | --- |
| `--team "Fish 'n Chips"` | Fish 'n Chips | The team treated as yours. Your opponent is worked out from the current matchup. |
| `--top 100` | 50 | How many free agents to export |
| `--days 14` | 7 | How many days of league transactions to include |
| `--no-html` | off | Write the JSON files only |
| `--serve` | off | After exporting, serve the dashboard locally with a Refresh button |
| `--port 8765` | 8765 | Port for `--serve` |

## How injury status is decided

ESPN's injury flag is the starting point. Each player is then checked against the NBA's official injury report, which teams must file before every game. The league publishes it as a PDF every 15 minutes. The tool reads the latest report plus the final report from each of the last 3 days, and uses each team's next game if it's on a report, otherwise the most recent one.

| Status | Meaning |
| --- | --- |
| Out | Listed out on the latest report found |
| Questionable | Listed questionable or doubtful |
| Day-to-day | Listed probable: likely to play, but still on the report |
| Clear | Not on the latest report found. Players listed "available" also count as clear. |
| Unconfirmed | No recent report was found either way. ESPN flags the player, but no NBA report from the last 3 days covers their team, so the flag can't be checked. |

A player ESPN doesn't flag, whose team isn't on any recent report, is shown as Clear with ESPN as the source.

Each row records where its status came from (`source`) and when (`as_of`). Where ESPN carries a recent Rotowire note for a flagged player, that note becomes the "latest" line; otherwise the line describes the report listing.

In the dashboard, a red bar beside the slot marks a player who is out but in an active lineup spot (anything other than Bench or IR).

## How the week projection works

Each player gets a value per game that blends the last 15 days (half), the season (30%) and ESPN's projection (20%). An average built from only a few games counts for less, so one bad night in the last 15 days doesn't sink a player's value. Every remaining day of the matchup, the players with a game are put into the best lineup your league's slots allow, and only starters count. Players listed out are left out until ESPN's expected return date and then counted at half value; questionable players count at half value. IR players are left out.

This assumes the lineup is set well every day, which ESPN's per-game lineup locks allow. It's a way to compare options, not a forecast to rely on.

## How waiver suggestions work

Drops are judged on **long-term value**: mostly the season average and ESPN's projection, with a little recent form, and ignoring current injuries. That way an injured star, or a player easing back from injury on low minutes, isn't mistaken for a bench player. A team's eight best players by long-term value, and anyone rostered in 60% or more of ESPN leagues, are never suggested as drops; the dashboard lists who was kept and why. IR players are never suggested either.

The three weakest players left are the drop candidates. Each healthy free agent among the top 40 is tested in the week projection in place of each of them:

- **Best adds for the rest of the season**: free agents at least 1.5 points a game better long term than the weakest candidate.
- **Streamers for this matchup**: the most projected points added this matchup, but only by replacing someone who is no better long term. This picks up game days, open lineup slots and injured teammates, not just averages.

Suggestions in a list are alternatives for the same roster spot. They're worked out for every team, so you can see what other teams might go after. Each suggestion notes whether the player is a free agent (add straight away) or on waivers (claim, with your waiver priority). The league's add limit is shown alongside.

## How the news is read

**Injuries that open up minutes.** When a regular (20 or more fantasy points and minutes a game) is out for the week, half of their fantasy production is shared among healthy teammates in the rotation, in proportion to each one's minutes and how closely their positions match. A player's boosts can't add more than 30% to their value in all. If the injured player has already missed most of the last 15 days, recent form already reflects it, so the boost is halved. Boosts last until ESPN's expected return date and feed into the week projection, waiver suggestions and trade ideas.

**What the notes say.** Each Rotowire note from the last 10 days is scanned for phrases about role (will start, moved into the starting lineup, more minutes, coming off the bench), returns and injuries, and for teammates named as missing. A role sentence that names a teammate but not the player is skipped, since notes often describe other players. The matching sentence is shown so you can judge it. This is pattern matching rather than reading comprehension, so the odd tag will be off.

## How trade ideas work

Every roster in the league is projected day by day to the end of the fantasy season, with the same player values and lineup rules as the week projection. Injured players count from ESPN's expected return date (at half value for their first week back); a player out with no return date is assumed out for at least two weeks.

For each other team, one-for-one, two-for-one and one-for-two swaps are tested. After a trade, a team with an open roster spot adds the best healthy free agent, and a team with one player too many drops its weakest (or moves an injured player to a free IR slot). A trade is suggested only if:

- it raises your projection by at least 100 points,
- the other team ends up better off or within half a percent of where they were, and
- in a two-for-one, the best player on the side giving two is worth at least 65% of the one coming back, because few managers trade a star for two lesser players however the numbers add up.

The projections can't see team needs, keeper plans or how a manager rates their own players, so treat these as starting points for an offer rather than sure things.

## Free agent averages

In a points league, the averages are fantasy points per game on your league's own scoring. In a category league, ESPN doesn't compute a single average, so the tool ranks players on ESPN's default points scoring as a guide. The dashboard says which applies. "Rostered" is the share of all ESPN leagues that roster the player, not just yours.

## When something goes wrong

espn-api is unofficial, and ESPN can change its private API at any time. The tool tries to say clearly what failed:

- **"ESPN refused access"**: the cookies are missing, expired or from another account. Copy them again.
- **"League ... doesn't exist"**: check `LEAGUE_ID` and `YEAR` in `fantasy_dash/config.py`.
- **"ESPN's data didn't match what espn-api expects"**: ESPN has probably changed something. Try `pip install -U espn-api`, or check the [espn-api issues](https://github.com/cwendt94/espn-api/issues).
- **"The HTTPS certificate check failed"**: some networks, especially at work, inspect HTTPS traffic with their own certificate. Your Mac trusts it, but Python's bundled certificates don't. The tool uses the `truststore` package to check certificates against your computer's trust store, as your browser does, so make sure `pip install -r requirements.txt` has been run. Certificate checking is never switched off.
- **NBA report warnings**: if a report can't be downloaded or read, the export still finishes. The dashboard shows the warning, and players that can't be checked are marked Unconfirmed.

If free agents, transactions or the waiver order fail to load, the rest of the export still runs, and the problem is listed under Warnings.

## Tests

The tests use saved sample data (a trimmed ESPN league and two pages of a real NBA report), so they never call ESPN or the NBA.

```
pip install -r requirements-dev.txt
pytest
```

## Layout

```
export.py                  command-line entry point
fantasy_dash/
  config.py                league and team settings; reads the ESPN cookies
  local_secrets.py         your ESPN cookies (git-ignored, stays on your computer)
  espn_source.py           everything that talks to espn-api
  nba_report.py            finds, downloads and parses NBA injury report PDFs
  injuries.py              reconciles ESPN flags with the NBA report
  schedule.py              matchup days and each NBA team's games
  projection.py            player values and the best lineup each day
  recommend.py             waiver suggestions and lineup checks
  details.py               the expandable player detail
  news.py                  injury knock-on effects and note tagging
  trades.py                trade ideas
  server.py                the local server behind --serve
  dashboard.py             builds the standalone HTML
  summary.py               console summary
  templates/dashboard.html page template (data is embedded at build time)
tests/                     tests and sample data
```
