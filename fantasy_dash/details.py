"""The expandable detail behind each player row: averages, stat line, games
this week, recent games, injury details, ownership and recent notes."""

from datetime import date

from .espn_source import SPLITS, split_average, team_code

SPLIT_LABELS = {
    "season_avg": "Season", "last_30_avg": "Last 30", "last_15_avg": "Last 15",
    "last_7_avg": "Last 7", "projected_avg": "ESPN projection",
}
STAT_LINE = ("MIN", "PTS", "REB", "AST", "STL", "BLK", "3PM", "TO")
RECENT_GAMES = 5


def _per_game(totals, games):
    line = {stat: round(totals.get(stat, 0) / games, 1) for stat in STAT_LINE}
    for label, made, tried in (("FG%", "FGM", "FGA"), ("FT%", "FTM", "FTA")):
        line[label] = round(100 * totals[made] / totals[tried], 1) if totals.get(tried) else None
    return line


def _averages(player, year, points_league):
    rows = []
    for key, split in SPLITS:
        games = ((player.stats.get(f"{year}_{split}") or {}).get("total") or {}).get("GP") or 0
        rows.append({"label": SPLIT_LABELS[key], "avg": split_average(player, year, split, points_league),
                     "games": int(games)})
    return rows


def _recent_games(player):
    played = []
    for key, game in player.stats.items():
        totals = game.get("total") or {}
        if key.isdigit() and totals.get("GP"):
            played.append((int(key), game, totals))
    played.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            # espn-api gives a naive local time; astimezone() reads it as such.
            "tip": game["date"].astimezone().isoformat(timespec="minutes") if game.get("date") else None,
            "opponent": team_code(game.get("team") or ""),
            "fpts": round(game.get("applied_total") or 0, 1),
            "min": totals.get("MIN"), "pts": totals.get("PTS"),
            "reb": totals.get("REB"), "ast": totals.get("AST"),
        }
        for _, game, totals in played[:RECENT_GAMES]
    ]


def card_injury(card_entry):
    """(expected return ISO date or None, out for season) from a player card."""
    injury = _injury((card_entry or {}).get("player") or {})
    return (injury["expected_return"], injury["out_for_season"]) if injury else (None, False)


def _injury(card_player):
    details = card_player.get("injuryDetails") or {}
    if not details:
        return None
    back = details.get("expectedReturnDate")
    return {
        "type": details.get("type") or "",
        "expected_return": date(*back).isoformat() if isinstance(back, list) and len(back) == 3 else None,
        "out_for_season": bool(details.get("outForSeason")),
    }


def card_rostered(card_entry):
    own = _ownership((card_entry or {}).get("player") or {})
    return own["rostered"] if own else None


def _ownership(card_player):
    own = card_player.get("ownership") or {}
    if "percentOwned" not in own:
        return None
    return {key: round(own[src], 1) if isinstance(own.get(src), (int, float)) else None
            for key, src in (("rostered", "percentOwned"), ("change", "percentChange"), ("started", "percentStarted"))}


def build_details(cards, notes, calendar, days, now, starters_by_day, year, points_league,
                  signals=None, boosts=None):
    """{player_id: detail} for every player with a card.

    starters_by_day: {player_id: {period: True if projected to start}} for
    players on the two rosters in the matchup, so their games show start or bench.
    """
    signals, boosts = signals or {}, boosts or {}
    details = {}
    for player_id, (player, entry) in cards.items():
        card_player = entry.get("player") or {}
        team = team_code(player.proTeam)
        starts = starters_by_day.get(player_id)
        upcoming = []
        for period in days:
            game = calendar.game(team, period)
            if game and game.tip > now:
                upcoming.append({
                    "tip": game.tip.isoformat(timespec="minutes"),
                    "opponent": game.opponent,
                    "home": game.home,
                    "start": None if starts is None else starts.get(period, False),
                })
        totals = (player.stats.get(f"{year}_total") or {}).get("total") or {}
        games = totals.get("GP") or 0
        details[player_id] = {
            "averages": _averages(player, year, points_league),
            "stat_line": _per_game(totals, games) if games else None,
            "upcoming": upcoming,
            "recent_games": _recent_games(player),
            "injury": _injury(card_player),
            "ownership": _ownership(card_player),
            "notes": notes.get(player_id, []),
            "signals": signals.get(player_id, []),
            "boosts": boosts.get(player_id, []),
        }
    return details
