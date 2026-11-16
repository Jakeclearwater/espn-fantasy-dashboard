#!/usr/bin/env python3
"""Export your ESPN fantasy basketball league to JSON and build an injury and waiver dashboard.

    python export.py [--team NAME] [--top N] [--days N] [--no-html] [--serve [--port N]]

Writes out/rosters.json, out/free_agents.json, out/transactions.json,
out/waivers.json, out/outlook.json, out/matchups.json, out/news.json,
out/trades.json, out/players.json and out/dashboard.html,
then prints a short summary. With --serve it then keeps running and serves
the dashboard on this computer, where a Refresh button reruns the export.
"""

import argparse
import json
import os
import sys
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fantasy_dash import (config, dashboard, details, espn_source, injuries, matchups, nba_report, news,
                          projection, recommend, schedule, summary, trades)

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "out"
# How far back to look for an NBA report covering a player's team.
REPORT_LOOKBACK_DAYS = 3
# ESPN's standard basketball lineup, used if the league's own can't be read.
DEFAULT_SLOT_COUNTS = {"0": 1, "1": 1, "2": 1, "3": 1, "4": 1, "5": 1, "6": 1, "11": 3, "12": 3, "13": 1}


def positive_int(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Export an ESPN fantasy basketball league to JSON and an HTML injury and waiver dashboard."
    )
    parser.add_argument("--team", default=config.DEFAULT_TEAM,
                        help=f'your team name as shown on ESPN (default: "{config.DEFAULT_TEAM}")')
    parser.add_argument("--top", type=positive_int, default=50,
                        help="how many free agents to export (default: 50)")
    parser.add_argument("--days", type=positive_int, default=7,
                        help="days of league transactions to include (default: 7)")
    parser.add_argument("--no-html", action="store_true", help="write the JSON files only")
    parser.add_argument("--serve", action="store_true",
                        help="after exporting, serve the dashboard locally with a Refresh button")
    parser.add_argument("--port", type=positive_int, default=8765,
                        help="port for --serve (default: 8765)")
    return parser.parse_args(argv)


def season_label(year):
    return f"{year - 1}-{str(year)[2:]}"


def average_label(league):
    if espn_source.is_points_league(league):
        return "Averages are fantasy points per game on your league's scoring"
    return ("Averages are fantasy points per game on ESPN's default points scoring, "
            "used as a guide because this is a category league")


def team_summary(team):
    if team is None:
        return None
    return {"id": team.team_id, "name": espn_source.team_label(team), "owner": espn_source.owner_name(team)}


def build_export(league, team_name, top, days, now, fetch_reports=None,
                 log=lambda message: None, local_tz=None):
    """Gather everything from ESPN and the NBA. Returns (json_files, dashboard_data)."""
    fetch_reports = fetch_reports or nba_report.fetch_recent_reports
    warnings = []
    my_team = espn_source.find_team(league, team_name)
    points_league = espn_source.is_points_league(league)

    try:
        settings, raw_teams = espn_source.league_extras(league)
    except espn_source.EspnError as exc:
        settings, raw_teams = {}, {}
        warnings.append(f"League settings were skipped, so the week outlook may be rough. {exc}")
    slot_counts = (settings.get("rosterSettings") or {}).get("lineupSlotCounts") or DEFAULT_SLOT_COUNTS
    slots = projection.starting_slots(slot_counts)

    calendar = schedule.Calendar(
        league.pro_schedule, league.firstScoringPeriod, league.finalScoringPeriod,
        league.matchup_ids, (settings.get("scheduleSettings") or {}).get("matchupPeriods"))
    matchup, matchup_days = schedule.outlook_matchup(calendar, league.currentMatchupPeriod, now)
    rows = espn_source.roster_rows(league)

    log("Loading free agents...")
    pool, free_agents = [], []
    try:
        pool = espn_source.free_agent_pool(league, top)
        free_agents = espn_source.free_agent_rows(league, pool, top)
    except espn_source.EspnError as exc:
        warnings.append(f"Free agents were skipped. {exc}")

    log("Loading league transactions...")
    try:
        transactions = espn_source.recent_transactions(league, days, now)
    except espn_source.EspnError as exc:
        transactions = []
        warnings.append(f"Transactions were skipped. {exc}")

    waivers = espn_source.waiver_info(league, my_team, pool, now, raw_teams)

    log("Checking the NBA's official injury reports...")
    reports, report_warnings = fetch_reports(league.year, now, REPORT_LOOKBACK_DAYS)
    warnings.extend(report_warnings)
    index = injuries.ReportIndex(reports, now.astimezone(nba_report.ET).date())
    everyone = rows + free_agents
    for row in everyone:
        injuries.reconcile(row, index, now, REPORT_LOOKBACK_DAYS, local_tz)

    everyone_ids = {r["player_id"] for r in everyone}
    # Less-owned free agents too: their game logs help rate each team's
    # defence, and their injury status shows which defenders are missing.
    defence_rows = []
    if pool:
        try:
            defence_rows = [r for r in espn_source.free_agent_rows(league, pool, len(pool))
                            if r["player_id"] not in everyone_ids]
        except espn_source.EspnError:
            defence_rows = []
    for row in defence_rows:
        injuries.reconcile(row, index, now, REPORT_LOOKBACK_DAYS, local_tz)
    log("Loading player cards and notes for the whole league...")
    try:
        cards = espn_source.player_cards(league, everyone_ids | {r["player_id"] for r in defence_rows})
    except espn_source.EspnError as exc:
        cards = {}
        warnings.append(f"Player details were skipped. {exc}")
    for row in everyone + defence_rows:  # the full card has the most reliable return date and ownership
        if row["player_id"] in cards:
            entry = cards[row["player_id"]][1]
            back, season = details.card_injury(entry)
            row["expected_return"] = back or row.get("expected_return")
            row["out_for_season"] = season
            if row.get("percent_rostered") is None:
                row["percent_rostered"] = details.card_rostered(entry)
    notes = espn_source.player_notes(league, everyone_ids)
    for row in everyone:
        injuries.attach_note(row, notes.get(row["player_id"]), now, local_tz)

    log("Reading the news...")
    for row in free_agents:
        row.setdefault("team", "Free agent")
        row.setdefault("team_id", None)
    teammates = {}
    for row in everyone:
        teammates.setdefault(row["nba_team"], []).append(row["player"])
    for row in everyone:
        others = [name for name in teammates.get(row["nba_team"], []) if name != row["player"]]
        row["signals"] = news.note_signals(notes.get(row["player_id"]), others, now, row["player"])
    opportunities = news.find_opportunities(everyone, now)

    log("Rating matchups...")
    rater = matchups.Matchups(
        matchups.Defence(matchups.game_logs(cards)), matchups.key_defenders(cards, league.year),
        {r["player_id"]: r for r in everyone + defence_rows}, calendar, now)
    rater.attach(everyone, matchup_days)

    log("Projecting the week and testing waiver moves for every team...")
    pairs = espn_source.matchup_pairs(league, matchup)
    opponent_id = pairs.get(my_team.team_id)
    opponent = next((t for t in league.teams if t.team_id == opponent_id), None)
    team_rows = {t.team_id: [r for r in rows if r["team_id"] == t.team_id] for t in league.teams}
    ranks = {team_id: raw.get("waiverRank") for team_id, raw in raw_teams.items()}
    projections, checks, suggestions = {}, {}, {}
    for team in league.teams:
        mine_now = team.team_id == my_team.team_id
        roster = team_rows[team.team_id]
        projections[team.team_id] = projection.project_team(roster, calendar, matchup_days, now, slots)
        name = espn_source.team_label(team)
        checks[team.team_id] = recommend.lineup_checks(
            roster, projections[team.team_id], slot_counts, local_tz,
            possessive="your" if mine_now else name + ("'" if name.endswith("s") else "'s"))
        suggestions[team.team_id] = recommend.suggest_moves(
            roster, free_agents, calendar, matchup_days, now, slots, ranks.get(team.team_id), len(league.teams),
            subject="you're" if mine_now else f"{name} is", local_tz=local_tz)

    log("Looking for trades...")
    deadline_ms = getattr(league.settings, "trade_deadline", 0)
    deadline = datetime.fromtimestamp(deadline_ms / 1000, tz=timezone.utc) if deadline_ms else None
    trade_ideas = trades.suggest_trades_for_all(
        rows, my_team.team_id, {t.team_id: team_summary(t) for t in league.teams}, free_agents,
        calendar, now, slots, slot_counts, league.finalScoringPeriod, deadline)

    starters_by_day = {}
    for team_projection in projections.values():
        for day in team_projection["days"]:
            for player_id in team_projection["player_games"]:
                starters_by_day.setdefault(player_id, {})[day["period"]] = player_id in day["starters"]
    player_details = details.build_details(
        {pid: card for pid, card in cards.items() if pid in everyone_ids}, notes, calendar, matchup_days, now, starters_by_day, league.year, points_league,
        signals={r["player_id"]: r["signals"] for r in everyone},
        boosts={r["player_id"]: r.get("boosts", []) for r in everyone},
        matchups={r["player_id"]: r.get("matchups", {}) for r in everyone})

    scores = {}
    try:
        scores = espn_source.live_scores(league)
    except espn_source.EspnError as exc:
        warnings.append(f"Matchup scores were skipped. {exc}")
    is_current = matchup == league.currentMatchupPeriod
    outlook = {
        "matchup_period": matchup,
        "is_current": is_current,
        "first_day": calendar.date_of(matchup_days[0]).isoformat() if matchup_days else None,
        "last_day": calendar.date_of(matchup_days[-1]).isoformat() if matchup_days else None,
        "slots": len(slots),
        "opponents": pairs,
        "projections": projections,
        "scores": {tid: scores.get(tid) for tid in projections} if is_current else {},
        "projected_finish": {
            tid: round((scores.get(tid) or 0 if is_current else 0) + p["points"], 1) for tid, p in projections.items()
        },
        "checks": checks,
        "previous": {},
        "next_lineup": {tid: recommend.next_lineup(team_rows[tid], projections[tid], local_tz) for tid in projections},
        "add_limits": {t.team_id: espn_source.add_limit(settings, raw_teams.get(t.team_id), matchup)
                       for t in league.teams},
        "waiver_ranks": ranks,
    }
    if not is_current and scores:
        # The matchup just finished, for every team: score and who it was against.
        last_pairs = espn_source.matchup_pairs(league, league.currentMatchupPeriod)
        teams_by_id = {t.team_id: t for t in league.teams}
        for team_id, score in scores.items():
            other = last_pairs.get(team_id)
            outlook["previous"][team_id] = {
                "matchup_period": league.currentMatchupPeriod,
                "opponent": team_summary(teams_by_id.get(other)) if other is not None else None,
                "mine": score, "theirs": scores.get(other) if other is not None else None,
            }

    generated_at = now.isoformat(timespec="seconds")
    matchup_summary = rater.summary(matchup_days)
    team_names = {r["player_id"]: r["team"] for r in rows}
    files = {
        "rosters.json": {
            "generated_at": generated_at,
            "league_id": league.league_id,
            "season": season_label(league.year),
            "players": rows,
        },
        "free_agents.json": {
            "generated_at": generated_at,
            "sorted_by": "last_15_avg",
            "averages": average_label(league),
            "players": free_agents,
        },
        "transactions.json": {
            "generated_at": generated_at,
            "days": days,
            "since": (now - timedelta(days=days)).isoformat(timespec="minutes"),
            "transactions": transactions,
        },
        "waivers.json": {"generated_at": generated_at, **waivers},
        "outlook.json": {"generated_at": generated_at, **outlook, "suggestions": suggestions},
        "matchups.json": {"generated_at": generated_at, **matchup_summary},
        "news.json": {"generated_at": generated_at, "opportunities": opportunities,
                      "signals": {r["player_id"]: r["signals"] for r in everyone if r["signals"]}},
        "trades.json": {"generated_at": generated_at, **trade_ideas},
        "players.json": {
            "generated_at": generated_at,
            "players": [
                {"player": row["player"], "player_id": row["player_id"], "nba_team": row["nba_team"],
                 "fantasy_team": team_names.get(row["player_id"], "Free agent"), **player_details[row["player_id"]]}
                for row in rows + free_agents if row["player_id"] in player_details
            ],
        },
    }

    data = {
        "generated_at": generated_at,
        "league": {
            "id": league.league_id,
            "name": league.settings.name,
            "season": season_label(league.year),
            "matchup_period": matchup,
            "scoring": league.settings.scoring_type,
            "average_label": average_label(league),
        },
        "my_team": team_summary(my_team),
        "opponent": team_summary(opponent),
        "teams": [team_summary(t) for t in sorted(league.teams, key=lambda t: espn_source.team_label(t).lower())],
        "rosters": rows,
        "free_agents": free_agents,
        "transactions": transactions,
        "transaction_days": days,
        "waivers": waivers,
        "outlook": outlook,
        "suggestions": suggestions,
        "trades": trade_ideas,
        "news": {"opportunities": opportunities},
        "matchups": matchup_summary,
        "details": player_details,
        "injury_sources": {
            "lookback_days": REPORT_LOOKBACK_DAYS,
            "reports": [{"published": r.published.isoformat(timespec="minutes"), "url": r.url}
                        for r in sorted(reports, key=lambda r: r.published, reverse=True)],
        },
        "warnings": warnings,
        "hosting": hosting_info(),
    }
    return files, data


def hosting_info():
    """When run by the GitHub workflow, where the page updates and how to refresh it."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return None
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    return {
        "every": os.environ.get("DASHBOARD_REFRESH_EVERY", ""),
        "run_url": f"{server}/{repo}/actions/workflows/publish.yml" if repo else None,
    }


def write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def use_system_certificates():
    """Verify HTTPS against the operating system's trust store, as browsers do.

    Work networks often inspect HTTPS with their own root certificate, which
    the Mac trusts but Python's bundled certificates don't.
    """
    try:
        import truststore
    except ImportError:
        return
    truststore.inject_into_ssl()


def run_export(args, log):
    """Pull fresh data and write the files. Returns (dashboard data, paths written).
    Raises ConfigError or EspnError with a message safe to show."""
    credentials = config.get_credentials()
    league_id, year = config.LEAGUE_ID, config.YEAR
    log(f"Loading ESPN league {league_id} for the {season_label(year)} season...")
    league = espn_source.connect(league_id, year, credentials)
    files, data = build_export(league, args.team, args.top, args.days, datetime.now().astimezone(), log=log)

    OUT_DIR.mkdir(exist_ok=True)
    written = []
    for name, payload in files.items():
        write_json(OUT_DIR / name, payload)
        written.append(OUT_DIR / name)
    if not args.no_html:
        written.append(dashboard.write_dashboard(data, OUT_DIR / "dashboard.html"))
    return data, written


def serve(args, data, log):
    """Serve the dashboard on this computer until Ctrl+C, refreshing on request."""
    from fantasy_dash.server import DashboardServer

    latest = {"data": data}

    def render(token):
        return dashboard.build_html({**latest["data"], "refresh": {"token": token}})

    def refresh():
        log("Refreshing...")
        latest["data"], _ = run_export(args, log)
        log("Refreshed.")
        return latest["data"]["generated_at"]

    try:
        server = DashboardServer(render, refresh, port=args.port)
    except OSError as exc:
        print(f"\nError: couldn't serve on port {args.port} ({exc.strerror}). Try another with --port.",
              file=sys.stderr)
        return 2
    print(f"\nServing the dashboard at {server.url}  (Ctrl+C to stop)", flush=True)
    webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


def main(argv=None):
    args = parse_args(argv)
    use_system_certificates()

    def log(message):
        print(message, file=sys.stderr)

    try:
        data, written = run_export(args, log)
    except (config.ConfigError, espn_source.EspnError) as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 2

    print()
    print(summary.console_summary(data))
    print()
    print("Wrote " + ", ".join(str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)
                               for p in written))
    if args.serve:
        return serve(args, data, log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
