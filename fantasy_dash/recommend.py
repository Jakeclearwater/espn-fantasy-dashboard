"""Waiver suggestions and lineup checks for your team.

Every suggestion is tested by re-running the week projection with the free
agent in place of one of your weakest players, so open lineup slots,
positions and game days all count, not just averages.
"""

from datetime import datetime

from .injuries import format_local
from .matchups import SOFT, TOUGH, describe
from .nba_report import ET
from .projection import per_game_value, play_chance, project_team, talent_value

DROP_POOL = 3                # weakest unprotected players considered for dropping
CANDIDATES = 40              # free agents tested, best current value first
CORE_PLAYERS = 8             # a team's best players by long-term value are never dropped
PROTECT_ROSTERED = 60.0      # nor anyone rostered in at least this share of ESPN leagues
LONG_TERM_MIN_GAIN = 1.5     # long-term points a game better than who they'd replace
STREAM_TALENT_SLACK = 0.0    # a streamer must be at least as good long term as who they replace
STREAM_MIN_GAIN = 5.0        # projected points added this matchup
SUGGESTIONS = 5
WATCH = 3
BOOST_WORTH_MENTION = 2.0    # smaller knock-on effects from injured teammates aren't flagged


def _number(value):
    return f"{value:.1f}" if isinstance(value, (int, float)) else "n/a"


def _day(value, local_tz=None):
    """'Tue 17 Nov' for an ISO datetime (in local time) or an ISO date."""
    if "T" in value:
        value_dt = datetime.fromisoformat(value).astimezone(local_tz)
    else:
        value_dt = datetime.fromisoformat(value)
    return f"{value_dt:%a} {value_dt.day} {value_dt:%b}"


def _join(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _brief(row):
    keys = ("player", "player_id", "nba_team", "positions", "slot", "status", "injury", "availability",
            "waiver_clears", "last_15_avg", "last_15_games", "season_avg", "expected_return", "percent_rostered")
    return {**{key: row.get(key) for key in keys}, "value": round(per_game_value(row), 1),
            "talent": round(talent_value(row), 1)}


def protection(rows):
    """{player_id: why they shouldn't be dropped} for a roster's keepers.

    Long-term talent decides, not current form, so an injured star or a
    player easing back from injury is kept."""
    active = [r for r in rows if r["slot"] != "IR"]
    healthy_long_term = [r for r in active if not r.get("out_for_season")]
    core = sorted(healthy_long_term, key=talent_value, reverse=True)[:CORE_PLAYERS]
    reasons = {r["player_id"]: "one of the team's 8 best players long term" for r in core}
    for row in active:
        rostered = row.get("percent_rostered")
        if isinstance(rostered, (int, float)) and rostered >= PROTECT_ROSTERED and not row.get("out_for_season"):
            reasons.setdefault(row["player_id"], f"rostered in {rostered:.0f}% of ESPN leagues")
    return reasons


def drop_candidates(rows):
    """The weakest players by long-term value who aren't keepers. IR players
    are never suggested: they don't use a roster spot."""
    kept = protection(rows)
    pool = [r for r in rows if r["slot"] != "IR" and r["player_id"] not in kept]
    return sorted(pool, key=talent_value)[:DROP_POOL]


def ordinal(n):
    if 11 <= n % 100 <= 13:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _availability(fa, rank, team_count, subject, now, local_tz):
    if fa.get("availability") == "Waivers":
        when = ""
        if fa.get("waiver_clears"):
            clears = datetime.fromisoformat(fa["waiver_clears"])
            when = (f" until {format_local(clears, local_tz)}" if clears > now
                    else f"; claims were due to process {format_local(clears, local_tz)}")
        priority = (f" Claims go by waiver priority, and {subject} {ordinal(rank)} of {team_count}."
                    if isinstance(rank, int) else "")
        return f"On waivers{when}.{priority}"
    return "Free agent, so can be added straight away."


def _form(row):
    games = row.get("last_15_games")
    count = f" ({games} game{'s' if games != 1 else ''})" if isinstance(games, int) else ""
    return (f"{_number(row.get('last_15_avg'))} a game over the last 15 days{count}, "
            f"{_number(row.get('season_avg'))} for the season")


def _news(row, days, calendar):
    """Sentences about teammates' injuries and recent notes that bear on the pick."""
    lines = []
    last_day = calendar.date_of(days[-1]).isoformat() if days else None
    for boost in row.get("boosts") or []:
        if last_day and boost["until"] and boost["until"] <= calendar.date_of(days[0]).isoformat():
            continue
        until = f" until about {_day(boost['until'])}" if boost.get("until") else ""
        lines.append(f"{boost['from']} is out{until}, so {row['player']} should see more of the ball: "
                     f"about +{boost['bump']:.1f} a game while that lasts.")
    for signal in (row.get("signals") or [])[:1]:
        if signal["tags"][0] in ("Role up", "Teammate out", "Role down", "Returning"):
            lines.append(f"Latest note ({signal['tags'][0].lower()}): \u201c{signal['evidence']}\u201d")
    return lines


def _matchups(row):
    """'Matchups left: v WAS (soft, +9%), @ BOS (tough, -7%).' when any game stands out."""
    games = [m for _, m in sorted((row.get("matchups") or {}).items())]
    if not any(m["factor"] >= SOFT or m["factor"] <= TOUGH for m in games):
        return []
    line = f"Matchups left: {', '.join(describe(m) for m in games)}."
    missing = sorted({name for m in games for name in m["missing"]})
    if missing:
        line += f" Opponents missing {_join(missing)}."
    return [line]


def _reasons(fa, drop, fa_games, days, calendar, kept_reason=None):
    reasons = [f"{fa['player']}: {_form(fa)}. {drop['player']}: {_form(drop)}."]
    reasons.append(f"Long-term value, which leans on the season and ESPN's projection: "
                   f"{talent_value(fa):.1f} a game against {talent_value(drop):.1f}.")
    if fa_games:
        reasons.append(f"{fa_games} game{'s' if fa_games != 1 else ''} left this matchup.")
    reasons.extend(_matchups(fa))
    reasons.extend(_news(fa, days, calendar))
    if fa["status"] in ("Questionable", "Day-to-day"):
        reasons.append(f"Listed {fa['status'].lower()}" + (f" ({fa['injury']})." if fa.get("injury") else "."))
    rostered = drop.get("percent_rostered")
    reasons.append(f"{drop['player']} isn't one of the team's core players"
                   + (f" and is rostered in {rostered:.0f}% of ESPN leagues." if isinstance(rostered, (int, float)) else "."))
    if drop["status"] == "Out":
        back = f", expected back {_day(drop['expected_return'])}" if drop.get("expected_return") else ""
        reasons.append(f"{drop['player']} is out" + (f" ({drop['injury']})" if drop.get("injury") else "") + f"{back}.")
    return reasons


def suggest_moves(rows, free_agents, calendar, days, now, slots, rank=None, team_count=None,
                  subject="you're", local_tz=None):
    """Ranked add and drop suggestions for one roster: long_term, streamers and watch lists."""
    empty = {"long_term": [], "streamers": [], "watch": [], "protected": []}
    kept = protection(rows)
    drops = drop_candidates(rows)
    by_id = {r["player_id"]: r for r in rows}
    empty["protected"] = [{"player": by_id[pid]["player"], "why": why} for pid, why in kept.items()]
    if not drops:
        return empty
    base = project_team(rows, calendar, days, now, slots)
    weakest = drops[0]
    today = now.astimezone(ET).date()
    healthy = [fa for fa in free_agents if play_chance(fa, today, today) > 0]
    tested = sorted(healthy, key=lambda r: max(per_game_value(r), talent_value(r)), reverse=True)[:CANDIDATES]

    options = []
    for fa in tested:
        added = {**fa, "slot": "Bench"}
        week = {}
        for drop in drops:
            swapped = [r for r in rows if r["player_id"] != drop["player_id"]] + [added]
            projection = project_team(swapped, calendar, days, now, slots)
            week[drop["player_id"]] = (round(projection["points"] - base["points"], 1), projection)
        # Streamers may only replace someone who is no better long term.
        fair = [d for d in drops if talent_value(fa) >= talent_value(d) - STREAM_TALENT_SLACK]
        best_drop = max(fair, key=lambda d: week[d["player_id"]][0]) if fair else None
        options.append((fa, best_drop, week))

    def suggestion(fa, drop, week, kind):
        gain, projection = week[drop["player_id"]]
        games = projection["player_games"].get(fa["player_id"], 0)
        return {
            "kind": kind,
            "add": _brief(fa),
            "drop": _brief(drop),
            "value_gain": round(talent_value(fa) - talent_value(drop), 1),
            "week_gain": gain,
            "games_left": games,
            "starts_left": projection["player_starts"].get(fa["player_id"], 0),
            "reasons": _reasons(fa, drop, games, days, calendar),
            "how": _availability(fa, rank, team_count, subject, now, local_tz),
        }

    long_term = sorted((suggestion(fa, weakest, week, "long_term") for fa, _, week in options),
                       key=lambda s: (-s["value_gain"], -s["week_gain"]))
    long_term = [s for s in long_term if s["value_gain"] >= LONG_TERM_MIN_GAIN][:SUGGESTIONS]

    streamers = sorted((suggestion(fa, best, week, "streamer") for fa, best, week in options if best),
                       key=lambda s: -s["week_gain"])
    streamers = [s for s in streamers if s["week_gain"] >= STREAM_MIN_GAIN][:SUGGESTIONS]

    injured = sorted((fa for fa in free_agents if play_chance(fa, today, today) == 0),
                     key=talent_value, reverse=True)[:WATCH]
    watch = []
    for fa in injured:
        back = f"expected back {_day(fa['expected_return'])}" if fa.get("expected_return") else "no return date yet"
        injury = f" ({fa['injury']})" if fa.get("injury") else ""
        watch.append({
            "add": _brief(fa),
            "reasons": [f"Long-term value {talent_value(fa):.1f} a game, but out{injury}, {back}."],
            "how": _availability(fa, rank, team_count, subject, now, local_tz),
        })
    return {"long_term": long_term, "streamers": streamers, "watch": watch, "protected": empty["protected"]}


def lineup_checks(rows, projection, slot_counts, local_tz=None, possessive="your"):
    """Plain-language things to sort out in a lineup this matchup."""
    Possessive = possessive[:1].upper() + possessive[1:]
    checks = []
    games = projection["player_games"]
    for row in rows:
        left = games.get(row["player_id"], 0)
        if row["status"] == "Out" and row["slot"] not in ("Bench", "IR") and left:
            checks.append(f"{row['player']} is out but in {possessive} {row['slot']} slot, with "
                          f"{left} game{'s' if left != 1 else ''} left this matchup.")
        if row["slot"] == "IR" and row["espn_status"] != "Out":
            checks.append(f"{row['player']} is on IR but ESPN no longer lists them as out. ESPN won't "
                          "allow adds or drops until they're moved off IR.")

    ir_slots = int((slot_counts or {}).get("13", 0))
    if sum(1 for r in rows if r["slot"] == "IR") < ir_slots:
        for row in rows:
            if row["slot"] != "IR" and row["espn_status"] == "Out":
                checks.append(f"{Possessive} IR slot is free and {row['player']} is out: moving them there frees a roster spot.")

    boosted = sorted((r for r in rows if r["slot"] != "IR" and games.get(r["player_id"])
                      and sum(b["bump"] for b in r.get("boosts") or []) >= BOOST_WORTH_MENTION),
                     key=lambda r: -sum(b["bump"] for b in r["boosts"]))
    for row in boosted[:3]:
        names = _join([b["from"] for b in row["boosts"]])
        bump = sum(b["bump"] for b in row["boosts"])
        checks.append(f"{row['player']} should see more of the ball with {names} out: about +{bump:.1f} a game "
                      "while that lasts.")

    for row in rows:
        latest = (row.get("signals") or [None])[0]
        if latest and "Role down" in latest["tags"] and row["slot"] != "IR":
            owner = row["player"] + ("'" if row["player"].endswith("s") else "'s")
            checks.append(f"{owner} latest note points to a smaller role: \u201c{latest['evidence']}\u201d")

    live = [d for d in projection["days"] if not d["done"]]
    # With a 13-player roster some slots sit empty most days; flag the days
    # where fewer than half the lineup has a game.
    thin_days = [d for d in live if d["open_slots"] > len(d["starters"])]
    if thin_days:
        labels = [_day(d["first_tip"] or d["date"], local_tz) for d in thin_days]
        total = sum(d["open_slots"] for d in thin_days)
        checks.append(f"Few of {possessive} players have games on {_join(labels)}, leaving {total} lineup "
                      "slots empty. A pickup who plays on those days helps most.")
    full_days = [d for d in live if d["benched"]]
    if full_days:
        labels = [_day(d["first_tip"] or d["date"], local_tz) for d in full_days]
        total = sum(d["benched"] for d in full_days)
        checks.append(f"{Possessive} lineup is full on {_join(labels)}, so {total} game{'s' if total != 1 else ''} "
                      f"would sit on {possessive} bench.")
    return checks


def next_lineup(rows, projection, local_tz=None):
    """Start and sit changes for the next day with games still to play.

    Compares who is in an active slot now (ESPN carries a lineup forward
    day to day) with the best lineup for that day's games. Returns None when
    nothing would change.
    """
    day = next((d for d in projection["days"] if not d["done"] and d["available"]), None)
    if not day:
        return None
    values = {int(k): v for k, v in day["values"].items()}
    best = set(day["starters"])
    active = [r for r in rows if r["slot"] not in ("Bench", "IR")]
    current = sum(values.get(r["player_id"], 0) for r in active)
    gain = day["points"] - current
    start = [r for r in rows if r["player_id"] in best and r["slot"] == "Bench"]
    if not start or gain < 0.5:
        return None
    sit = []
    for row in active:
        if row["player_id"] in best:
            continue
        matchup = (row.get("matchups") or {}).get(day["period"])
        if row["player_id"] not in values:
            reason = "out" if row["status"] == "Out" else "no game"
        elif matchup and matchup["factor"] <= TOUGH:
            reason = "tough matchup"
        else:
            reason = "lower projection"
        sit.append({"player": row["player"], "player_id": row["player_id"], "slot": row["slot"], "reason": reason,
                    "matchup": matchup})
    return {
        "period": day["period"],
        "first_tip": day["first_tip"],
        "start": [{"player": r["player"], "player_id": r["player_id"], "value": values[r["player_id"]],
                   "positions": r.get("positions"), "matchup": (r.get("matchups") or {}).get(day["period"])}
                  for r in sorted(start, key=lambda r: -values[r["player_id"]])],
        "sit": sit,
        "gain": round(gain, 1),
    }
