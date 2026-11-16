"""The NBA calendar as ESPN fantasy sees it.

A scoring period is one US day of NBA games, numbered from opening night.
A matchup period is one week, Monday to Sunday in US time, or two weeks in
the playoffs. ESPN only says which days belong to a matchup once they have
been scored, so upcoming days are worked out from the last scored matchup.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from espn_api.basketball.constant import PRO_TEAM_MAP

from .espn_source import team_code
from .nba_report import ET


@dataclass(frozen=True)
class Game:
    period: int
    tip: datetime  # aware, UTC
    opponent: str
    home: bool


class Calendar:
    def __init__(self, pro_schedule, first_period, final_period, scored_periods, matchup_weeks):
        """
        pro_schedule: espn-api's league.pro_schedule, {pro team id: {"27": [game, ...]}}.
        scored_periods: league.matchup_ids, {matchup period: ["21", "22", ...]}.
        matchup_weeks: the league's scheduleSettings.matchupPeriods, {"20": [20, 21], ...}.
        """
        self.first_period = first_period
        self.final_period = final_period
        self.scored = {int(m): sorted(int(p) for p in periods) for m, periods in scored_periods.items() if periods}
        self.weeks = {int(m): list(weeks) for m, weeks in (matchup_weeks or {}).items()}
        self.games = {}  # team code -> {period: Game}

        opening_days = Counter()
        for team_id, periods in (pro_schedule or {}).items():
            code = team_code(PRO_TEAM_MAP.get(team_id, ""))
            if not code:
                continue
            for key, games in periods.items():
                if not games:
                    continue
                game, period = games[0], int(key)
                home = game.get("homeProTeamId") == team_id
                opponent_id = game.get("awayProTeamId") if home else game.get("homeProTeamId")
                tip = datetime.fromtimestamp(game["date"] / 1000, tz=timezone.utc)
                self.games.setdefault(code, {})[period] = Game(
                    period, tip, team_code(PRO_TEAM_MAP.get(opponent_id, "")), home)
                opening_days[tip.astimezone(ET).date() - timedelta(days=period - 1)] += 1
        # Every game implies the same opening night unless a date was moved.
        self.opening_day = opening_days.most_common(1)[0][0] if opening_days else None

    def date_of(self, period):
        """The US date of a scoring period."""
        return self.opening_day + timedelta(days=period - 1)

    def matchup_days(self, matchup):
        """Scoring periods in a matchup period, or [] if they can't be worked out."""
        if self.opening_day is None:
            return []
        if matchup - 1 in self.scored:
            start = self.scored[matchup - 1][-1] + 1
        elif matchup in self.scored:
            start = self.scored[matchup][0]
        elif matchup == 1:
            start = self.first_period
        else:
            return []
        week_count = len(self.weeks.get(matchup) or [matchup])
        to_sunday = 6 - self.date_of(start).weekday()
        end = min(start + to_sunday + 7 * (week_count - 1), self.final_period)
        return list(range(start, end + 1))

    def game(self, team, period):
        return self.games.get(team, {}).get(period)

    def first_tip(self, period):
        tips = [days[period].tip for days in self.games.values() if period in days]
        return min(tips) if tips else None

    def games_after(self, periods, now):
        """Games still to tip off on the given days, across the league."""
        wanted = set(periods)
        return [g for days in self.games.values() for p, g in days.items() if p in wanted and g.tip > now]


def outlook_matchup(calendar, current, now):
    """The matchup worth looking ahead to: the current one while it still has
    games to play, otherwise the next. Returns (matchup period, its days)."""
    days = calendar.matchup_days(current)
    if days and calendar.games_after(days, now):
        return current, days
    upcoming = calendar.matchup_days(current + 1)
    if upcoming:
        return current + 1, upcoming
    return current, days
