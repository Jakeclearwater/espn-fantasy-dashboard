"""Week projections: who can play each day, and the best lineup from them.

A player's value per game is a blend of recent form, the full season and
ESPN's projection. Each remaining day, the players with a game and a chance
of playing are put into the best lineup the league's slots allow, and only
starters count. That assumes lineups are set well every day, which ESPN's
per-game lineup locks make possible.
"""

from collections import Counter
from datetime import date, timedelta

from espn_api.basketball.constant import POSITION_MAP

from .espn_source import NOT_STARTING, slot_name
from .nba_report import ET

# How much each average counts towards a player's value per game:
# (average, games it covers, weight, games needed for the full weight).
# A last-15 figure from one game counts for a quarter of its weight.
BLEND = (
    ("last_15_avg", "last_15_games", 0.5, 4),
    ("season_avg", "season_games", 0.3, 8),
    ("projected_avg", None, 0.2, None),
)
QUESTIONABLE_CHANCE = 0.5
QUESTIONABLE_SPAN = timedelta(days=3)   # questionable is game to game, not for the season
RETURNING_CHANCE = 0.5                  # listed out, but past ESPN's expected return date
RETURN_RAMP = timedelta(days=7)         # after which a returning player counts in full
UNKNOWN_RETURN = timedelta(days=14)     # out with no return date: assumed out at least this long


# Long-term talent, for deciding who is safe to drop: leans on the season and
# ESPN's projection, so a player coming back from injury on low minutes, or
# stuck in a short slump, isn't mistaken for a bench player.
TALENT_BLEND = (
    ("last_15_avg", "last_15_games", 0.2, 4),
    ("season_avg", "season_games", 0.4, 8),
    ("projected_avg", None, 0.4, None),
)


def _blend(row, blend):
    parts = []
    for avg_key, games_key, weight, full_at in blend:
        value = row.get(avg_key)
        if not isinstance(value, (int, float)):
            continue
        games = row.get(games_key) if games_key else None
        if isinstance(games, int):
            weight *= min(1.0, games / full_at)
        parts.append((value, weight))
    total = sum(weight for _, weight in parts)
    return sum(value * weight for value, weight in parts) / total if total else 0.0


def per_game_value(row):
    """Expected fantasy points a game on current form (recent form weighs most)."""
    return _blend(row, BLEND)


def talent_value(row):
    """Long-term fantasy points a game, ignoring injuries and short slumps."""
    return _blend(row, TALENT_BLEND)


def boost_on(row, game_date):
    """Extra points a game from teammates being out on that US date (see news.py)."""
    total = 0.0
    for boost in row.get("boosts") or []:
        until = boost.get("until")
        if until is None or game_date < date.fromisoformat(until):
            total += boost["bump"]
    return total


def value_on(row, game_date):
    """Value per game on a given date, including any boost from injured teammates."""
    return per_game_value(row) + boost_on(row, game_date)


def play_chance(row, game_date, today=None):
    """Chance the player plays on a US date, from their injury status.

    `today` (a US date) lets short-term doubts fade over a long projection;
    without it, the status is taken to hold for every game.
    """
    status = row["status"]
    if status == "Unconfirmed":  # nothing recent to check ESPN's flag against
        status = row.get("espn_status", "Clear")
    if status == "Out":
        if row.get("out_for_season"):
            return 0.0
        back = row.get("expected_return")
        if back:
            back = date.fromisoformat(back)
            if game_date < back:
                return 0.0
            return RETURNING_CHANCE if game_date < back + RETURN_RAMP else 1.0
        if today is None or game_date < today + UNKNOWN_RETURN:
            return 0.0
        return RETURNING_CHANCE
    if status == "Questionable":
        return QUESTIONABLE_CHANCE if today is None or game_date < today + QUESTIONABLE_SPAN else 1.0
    return 1.0


def starting_slots(slot_counts):
    """Starting slots from ESPN's lineupSlotCounts, e.g. ['PG', ..., 'UTIL', 'UTIL', 'UTIL']."""
    slots = []
    for slot_id, count in sorted((int(k), v) for k, v in (slot_counts or {}).items()):
        name = slot_name(POSITION_MAP.get(slot_id, ""))
        if name not in NOT_STARTING:
            slots.extend([name] * int(count))
    return slots


def _seat(player, eligible, slots, seat_of, tried):
    """Find a slot for `player`, moving others along if needed (Kuhn's algorithm).
    Leaves seat_of untouched when there's no room."""
    for index, slot in enumerate(slots):
        if slot in eligible[player] and index not in tried:
            tried.add(index)
            if index not in seat_of or _seat(seat_of[index], eligible, slots, seat_of, tried):
                seat_of[index] = player
                return True
    return False


def best_lineup(candidates, slots):
    """The keys of the candidates who start, maximising total value.

    candidates: [(key, value, eligible slot names)]. Sets of players that can
    all be seated form a matroid, so taking players in value order and keeping
    each one who still fits gives the best lineup.
    """
    eligible = {key: set(names) for key, _, names in candidates}
    seat_of = {}
    for key, value, _ in sorted(candidates, key=lambda c: -c[1]):
        if value > 0:
            _seat(key, eligible, slots, seat_of, set())
    return set(seat_of.values())


def project_team(rows, calendar, days, now, slots):
    """Project the rest of a matchup for one roster.

    Returns points, starts and benched games in total and per day, plus each
    player's games and projected starts. IR players are left out; days or
    games that have already tipped off count as played.
    """
    per_day, games, starts = [], Counter(), Counter()
    today = now.astimezone(ET).date()
    for period in days:
        game_date = calendar.date_of(period)
        playing = []
        for row in rows:
            game = calendar.game(row["nba_team"], period)
            if row["slot"] == "IR" or not game or game.tip <= now:
                continue
            games[row["player_id"]] += 1
            value = value_on(row, game_date) * play_chance(row, game_date, today)
            if value > 0:
                playing.append((row["player_id"], value, row.get("eligible_slots") or []))
        starters = best_lineup(playing, slots)
        starts.update(starters)
        first_tip = calendar.first_tip(period)
        per_day.append({
            "period": period,
            "date": game_date.isoformat(),
            "first_tip": first_tip.isoformat(timespec="minutes") if first_tip else None,
            "done": not calendar.games_after([period], now),
            "available": len(playing),
            "starters": sorted(starters),
            "open_slots": max(0, len(slots) - len(starters)),
            "benched": len(playing) - len(starters),
            "points": round(sum(value for key, value, _ in playing if key in starters), 1),
        })
    live_days = [d for d in per_day if not d["done"]]
    return {
        "points": round(sum(d["points"] for d in per_day), 1),
        "starts": sum(len(d["starters"]) for d in per_day),
        "benched": sum(d["benched"] for d in live_days),
        "open_slots": sum(d["open_slots"] for d in live_days),
        "days": per_day,
        "player_games": dict(games),
        "player_starts": dict(starts),
    }
