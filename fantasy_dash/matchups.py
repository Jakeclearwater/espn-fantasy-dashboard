"""Matchups: how each NBA team defends each position, and which of its
defenders are missing on a given night.

1. Defence against each position. For every game in the players' game logs,
   the fantasy points a player scored are compared with their own season
   average and totalled by the defending team and the player's position. A
   team that holds players to 92% of their usual output is a tough matchup;
   one that gives up 110% is a soft one. Early in the season a few big nights
   can swing these figures, so each starts from league average and moves only
   as games build up: a team's overall figure behaves as if it began with
   about 500 points of average games, and each position figure leans towards
   the team's overall figure in the same way.

2. Missing defenders. A team's key defenders are rotation players with high
   steals and blocks per minute. When one is out, opponents at the same
   position (and, by half as much, next to it) get a small lift. Steals and
   blocks only partly capture defence, so the lift is kept small.

A game's matchup factor multiplies the player's projected points for that game.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass

from .espn_source import team_code
from .nba_report import ET
from .projection import play_chance

POSITIONS = ("PG", "SG", "SF", "PF", "C")
TEAM_PRIOR = 500        # points of league-average games each team's figure starts from
POSITION_PRIOR = 900    # the same for each position, starting from the team's overall figure
MIN_GAMES = 3           # players with fewer games don't have a usable average yet
MIN_AVERAGE = 8         # deep-bench players swing wildly from game to game
SHORT_GAME = 0.4        # games under this share of a player's usual minutes (injury exits) are left out
FACTOR_RANGE = (0.85, 1.15)
SOFT, TOUGH = 1.04, 0.96  # labels for the dashboard and reasons

KEY_MINUTES = 20
KEY_STOCKS = 2.8        # steals plus blocks per 36 minutes
KEY_BLOCKS = 1.8        # or blocks alone per 36 minutes
KEY_PER_TEAM = 3
DEFENDER_LIFT = 0.03    # per missing key defender at the same position
DEFENDER_CAP = 0.06
NEAR = {0: 1.0, 1: 0.5}  # share of the lift by gap between positions


@dataclass(frozen=True)
class GameLog:
    player_id: int
    position: str
    period: int
    opponent: str
    fpts: float
    minutes: float


def game_logs(cards):
    """Every game played by a player with a card, from ESPN's game logs."""
    logs = []
    for player_id, (player, _) in cards.items():
        for key, game in player.stats.items():
            totals = game.get("total") or {}
            if key.isdigit() and totals.get("GP") and game.get("team"):
                logs.append(GameLog(player_id, player.position, int(key), team_code(game["team"]),
                                    float(game.get("applied_total") or 0), float(totals.get("MIN") or 0)))
    return logs


def _gap(a, b):
    if a in POSITIONS and b in POSITIONS:
        return abs(POSITIONS.index(a) - POSITIONS.index(b))
    return None


class Defence:
    """Points allowed against each player's own average, by team and position."""

    def __init__(self, logs):
        by_player = defaultdict(list)
        for log in logs:
            by_player[log.player_id].append(log)
        allowed, expected = Counter(), Counter()
        self.games = Counter()
        self.players = 0
        for games in by_player.values():
            average = sum(g.fpts for g in games) / len(games)
            minutes = sum(g.minutes for g in games) / len(games)
            if len(games) < MIN_GAMES or average < MIN_AVERAGE:
                continue
            self.players += 1
            for game in games:
                if game.minutes < SHORT_GAME * minutes:
                    continue
                for key in (game.opponent, (game.opponent, game.position)):
                    allowed[key] += game.fpts
                    expected[key] += average
                self.games[game.opponent] += 1
        self.overall = {team: (allowed[team] + TEAM_PRIOR) / (expected[team] + TEAM_PRIOR)
                        for team in expected if isinstance(team, str)}
        self.by_position = {
            key: (allowed[key] + POSITION_PRIOR * self.overall[key[0]]) / (expected[key] + POSITION_PRIOR)
            for key in expected if isinstance(key, tuple)
        }

    def factor(self, team, position):
        """How much a player at this position scores against this team, as a share of their average."""
        return self.by_position.get((team, position), self.overall.get(team, 1.0))

    def table(self):
        return {team: {"overall": round(self.overall[team], 3), "games": self.games[team],
                       **{pos: round(self.factor(team, pos), 3) for pos in POSITIONS}}
                for team in sorted(self.overall)}


def key_defenders(cards, year):
    """{NBA team: [defender, ...]}, up to three per team, most active first."""
    found = defaultdict(list)
    for player_id, (player, _) in cards.items():
        totals = (player.stats.get(f"{year}_total") or {}).get("total") or {}
        games, minutes = totals.get("GP") or 0, totals.get("MIN") or 0
        if games < MIN_GAMES or minutes / games < KEY_MINUTES:
            continue
        steals, blocks = totals.get("STL", 0), totals.get("BLK", 0)
        stocks_36, blocks_36 = 36 * (steals + blocks) / minutes, 36 * blocks / minutes
        if stocks_36 >= KEY_STOCKS or blocks_36 >= KEY_BLOCKS:
            found[team_code(player.proTeam)].append({
                "player_id": player_id, "player": player.name, "position": player.position,
                "minutes": round(minutes / games, 1), "steals": round(steals / games, 1),
                "blocks": round(blocks / games, 1), "stocks_36": round(stocks_36, 1),
            })
    for team, defenders in found.items():
        defenders.sort(key=lambda d: -d["stocks_36"] * d["minutes"])
        del defenders[KEY_PER_TEAM:]
    return dict(found)


class Matchups:
    """Rates each remaining game in a matchup for any player row."""

    def __init__(self, defence, defenders, status_rows, calendar, now):
        """status_rows: {player_id: row} with injury status, for checking who is out."""
        self.defence = defence
        self.defenders = defenders
        self.status_rows = status_rows
        self.calendar = calendar
        self.now = now
        self.today = now.astimezone(ET).date()

    def missing(self, team, period):
        """[(defender, chance they miss the game)] for a team's key defenders likely out that day."""
        out = []
        day = self.calendar.date_of(period)
        for defender in self.defenders.get(team, []):
            row = self.status_rows.get(defender["player_id"])
            absent = 1 - play_chance(row, day, self.today) if row else 0.0
            if absent >= 0.5:
                out.append((defender, absent))
        return out

    def game(self, row, period):
        """The matchup for a row's game that day, or None if it has no game still to play."""
        game = self.calendar.game(row["nba_team"], period)
        if not game or game.tip <= self.now:
            return None
        position = row.get("position")
        defence = self.defence.factor(game.opponent, position)
        lift, missing = 0.0, []
        for defender, absent in self.missing(game.opponent, period):
            share = NEAR.get(_gap(defender["position"], position), 0.0)
            if share:
                lift += DEFENDER_LIFT * share * absent
                missing.append(defender["player"])
        low, high = FACTOR_RANGE
        factor = min(high, max(low, defence * (1 + min(lift, DEFENDER_CAP))))
        return {"opponent": game.opponent, "home": game.home, "defence": round(defence, 3),
                "missing": missing, "factor": round(factor, 3)}

    def attach(self, rows, days):
        """Set row['matchups'] = {period: matchup} for each game left in the given days."""
        for row in rows:
            row["matchups"] = {}
            for period in days:
                matchup = self.game(row, period)
                if matchup:
                    row["matchups"][period] = matchup

    def summary(self, days):
        """For the dashboard: the defence table, and key defenders with the days they're likely out."""
        defenders = {}
        for team, group in self.defenders.items():
            defenders[team] = []
            for defender in group:
                row = self.status_rows.get(defender["player_id"]) or {}
                out_days = [p for p in days if any(d is defender for d, _ in self.missing(team, p))]
                defenders[team].append({**defender, "status": row.get("status") or "",
                                        "injury": row.get("injury") or "", "out_periods": out_days})
        return {
            "positions": list(POSITIONS),
            "teams": self.defence.table(),
            "defenders": defenders,
            "players_counted": self.defence.players,
            "soft": SOFT, "tough": TOUGH,
        }


def label(factor):
    if factor >= SOFT:
        return "soft"
    if factor <= TOUGH:
        return "tough"
    return "neutral"


def describe(matchup):
    """'v WAS (soft, +9%)'"""
    where = "v" if matchup["home"] else "@"
    change = round(100 * (matchup["factor"] - 1))
    return f"{where} {matchup['opponent']} ({label(matchup['factor'])}, {change:+d}%)"
