"""Plain-text console summary of an export."""

from datetime import datetime

from .injuries import format_local, urgency

FREE_AGENTS_SHOWN = 10


def _number(value, suffix=""):
    return f"{value:.1f}{suffix}" if isinstance(value, (int, float)) else "n/a"


def _day(iso):
    when = datetime.fromisoformat(iso).astimezone()
    return f"{when:%a} {when.day} {when:%b}"


def _outlook_lines(data):
    outlook = data.get("outlook")
    mine = data["my_team"]["id"]
    projections = (outlook or {}).get("projections") or {}
    if mine not in projections:
        return []
    opponent = data.get("opponent")
    days = [d for d in projections[mine]["days"] if d.get("first_tip")]
    span = f" ({_day(days[0]['first_tip'])} to {_day(days[-1]['first_tip'])})" if days else ""
    lines = [f"Matchup {outlook['matchup_period']}" + (f" v {opponent['name']}" if opponent else "") + span]
    finish = outlook.get("projected_finish") or {}
    scores = outlook.get("scores") or {}
    started = scores.get(mine) or (opponent and scores.get(opponent["id"]))
    so_far = (f", score so far {scores.get(mine) or 0:,.1f} to {scores.get(opponent['id']) or 0:,.1f}"
              if started and opponent else "")
    if opponent and opponent["id"] in finish:
        lines.append(f"  Projected finish: {finish[mine]:,.0f} to {finish[opponent['id']]:,.0f}{so_far}")
    for check in (outlook.get("checks") or {}).get(mine, []):
        lines.append(f"  - {check}")
    suggestions = (data.get("suggestions") or {}).get(mine) or {}
    for key, label in (("long_term", "Best long-term add"), ("streamers", "Best streamer")):
        if suggestions.get(key):
            move = suggestions[key][0]
            gain = (f"{move['value_gain']:+.1f} a game long term" if key == "long_term"
                    else f"{move['week_gain']:+.0f} projected points this matchup")
            lines.append(f"  {label}: add {move['add']['player']}, drop {move['drop']['player']} ({gain})")
    trades = ((data.get("trades") or {}).get("by_team") or {}).get(mine) or []
    for trade in trades[:2]:
        give = " + ".join(p["player"] for p in trade["give"])
        get = " + ".join(p["player"] for p in trade["get"])
        lines.append(f"  Trade idea with {trade['partner']['name']}: give {give}, get {get} "
                     f"({trade['my_gain']:+,} for you, {trade['their_gain']:+,} for them over the season)")
    yours = [o for o in (data.get("news") or {}).get("opportunities", [])
             if any(b.get("team_id") == mine for b in o["beneficiaries"])]
    for opp in yours[:3]:
        helped = [b for b in opp["beneficiaries"] if b.get("team_id") == mine]
        names = ", ".join(f"{b['player']} (+{b['bump']:.1f})" for b in helped)
        lines.append(f"  News: {opp['out']['player']} ({opp['nba_team']}) is out, which helps {names}")
    lines.append("")
    return lines


def console_summary(data):
    mine = data["my_team"]
    opponent = data["opponent"]
    generated = format_local(datetime.fromisoformat(data["generated_at"]))
    title = mine["name"] + (f" v {opponent['name']}" if opponent else "")
    if data["league"].get("matchup_period"):
        title += f", matchup period {data['league']['matchup_period']}"
    lines = [f"{title} (generated {generated})", ""]

    concerns = sorted(
        (r for r in data["rosters"] if r["team_id"] == mine["id"] and r["status"] != "Clear"),
        key=urgency,
    )
    lines.append("Your players who are out, questionable, day-to-day or unconfirmed")
    if concerns:
        for row in concerns:
            where = f"{row['player']} ({row['nba_team']})" if row["nba_team"] else row["player"]
            injury = row["injury"] or "injury not specified"
            lines.append(f"  {row['status']:<13} {row['slot']:<6} {where:<30} {injury}")
            if row["status"] == "Out" and row["slot"] not in ("Bench", "IR"):
                lines.append(f"  {'':<13} {'':<6} ^ out, in an active lineup spot")
    else:
        lines.append("  None. Everyone is clear.")
    lines.append("")

    lines.extend(_outlook_lines(data))

    free_agents = data["free_agents"][:FREE_AGENTS_SHOWN]
    lines.append(f"Top {len(free_agents)} free agents by last-15-day average")
    if free_agents:
        lines.append(f"  {'':>3} {'Player':<30} {'Last 15':>8} {'Last 7':>8} {'Season':>8} {'Rostered':>9}")
        for rank, player in enumerate(free_agents, 1):
            who = f"{player['player']} ({player['nba_team']})" if player["nba_team"] else player["player"]
            flag = f"  [{player['status']}]" if player["status"] not in ("Clear", "") else ""
            lines.append(
                f"  {rank:>2}. {who:<30} {_number(player['last_15_avg']):>8} {_number(player['last_7_avg']):>8} "
                f"{_number(player['season_avg']):>8} {_number(player['percent_rostered'], '%'):>9}{flag}"
            )
        lines.append(f"  ({data['league']['average_label']}.)")
    else:
        lines.append("  No free agents were returned.")

    if data["warnings"]:
        lines.append("")
        lines.append("Warnings")
        lines.extend(f"  - {warning}" for warning in data["warnings"])
    return "\n".join(lines)
