"""Offline stand-ins for espn-api's League, built from saved sample data.

Roster and free-agent players are real espn_api Player objects made from
ESPN-shaped JSON, so the tests also check how this tool reads the library.
"""

import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from espn_api.basketball.player import Player

from fantasy_dash.nba_report import REPORT_BASE, Report, ReportEntry, entries_from_words, parse_report_time

FIXTURES = Path(__file__).parent / "fixtures"
NZ = ZoneInfo("Pacific/Auckland")


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeTeam:
    def __init__(self, raw, year):
        self.team_id = raw["id"]
        self.team_name = raw["name"]
        self.owners = [raw["owner"]]
        self.acquisition_budget_spent = raw["acquisition_budget_spent"]
        self.roster = [Player(entry, year) for entry in raw["roster"]]
        self.schedule = []

    def __repr__(self):
        return f"FakeTeam({self.team_name})"


class FakeRequests:
    """Answers the raw requests the tool makes through league.espn_request."""

    def __init__(self, fixture):
        self.fixture = fixture

    def league_get(self, params=None, headers=None, extend=""):
        view = (params or {}).get("view")
        if view == "kona_player_info":
            limit = json.loads(headers["x-fantasy-filter"])["players"]["limit"]
            return {"players": self.fixture["free_agents"][:limit]}
        if view == ["mSettings", "mTeam"]:
            return {
                "settings": self.fixture["settings"],
                "teams": [{"id": t["id"], "waiverRank": t["waiverRank"],
                           "transactionCounter": t["transactionCounter"]} for t in self.fixture["teams"]],
            }
        raise AssertionError(f"Unexpected ESPN request: {params}")

    def get_player_news(self, player_id):
        return self.fixture["news"].get(str(player_id), {})

    def get_player_card(self, player_ids, max_scoring_period, additional_filters=None):
        """Player cards: the sample player data plus card-only extras and game logs."""
        raw = {e["playerPoolEntry"]["player"]["id"]: e["playerPoolEntry"]["player"]
               for t in self.fixture["teams"] for e in t["roster"]}
        raw.update({e["player"]["id"]: e["player"] for e in self.fixture["free_agents"]})
        cards = []
        for player_id in player_ids:
            player = json.loads(json.dumps(raw[player_id]))
            extra = self.fixture["cards"].get(str(player_id), {})
            for key in ("injuryDetails", "ownership"):
                if key in extra:
                    player[key] = extra[key]
            for period, fpts, minutes, pts, reb, ast in extra.get("games", []):
                player["stats"].append({
                    "id": f"05{400000000 + period}", "seasonId": 2027, "scoringPeriodId": period,
                    "statSplitTypeId": 5, "appliedTotal": fpts,
                    "stats": {"42": 1.0, "40": minutes, "0": pts, "6": reb, "3": ast},
                })
            cards.append({"id": player_id, "player": player})
        return {"players": cards}


class FakeLeague:
    def __init__(self, fixture):
        info = fixture["league"]
        self.league_id = info["id"]
        self.year = info["year"]
        self.currentMatchupPeriod = info["current_matchup_period"]
        self.current_week = info["current_week"]
        self.scoringPeriodId = info["current_week"]
        self.firstScoringPeriod = info["first_scoring_period"]
        self.finalScoringPeriod = info["final_scoring_period"]
        self.matchup_ids = {int(m): [str(p) for p in periods] for m, periods in info["scored_periods"].items()}
        self.pro_schedule = {int(team): periods for team, periods in fixture["pro_schedule"].items()}
        self.settings = SimpleNamespace(
            name=info["name"], scoring_type=info["scoring_type"],
            faab=info["faab"], acquisition_budget=info["acquisition_budget"], trade_deadline=0,
        )
        self.teams = [FakeTeam(t, self.year) for t in fixture["teams"]]
        self.espn_request = FakeRequests(fixture)
        self._fixture = fixture
        for team in self.teams:
            for period in sorted(fixture["schedule"], key=int):
                for m in fixture["schedule"][period]:
                    if team.team_id in (m["home"], m["away"]):
                        team.schedule.append(SimpleNamespace(home_team=self._team(m["home"]),
                                                             away_team=self._team(m["away"]) or 0))

    def _team(self, team_id):
        return next((t for t in self.teams if t.team_id == team_id), None)

    def scoreboard(self, matchupPeriod=None):
        period = str(matchupPeriod or self.currentMatchupPeriod)
        return [
            SimpleNamespace(home_team=self._team(m["home"]),
                            away_team=self._team(m["away"]) if m["away"] else 0)
            for m in self._fixture["schedule"].get(period, [])
        ]

    def box_scores(self):
        scores = self._fixture["box_scores"].get(str(self.currentMatchupPeriod), {})
        return [
            SimpleNamespace(home_team=box.home_team, away_team=box.away_team,
                            home_score=scores.get(str(box.home_team.team_id), 0),
                            away_score=scores.get(str(getattr(box.away_team, "team_id", "")), 0))
            for box in self.scoreboard()
        ]

    def recent_activity(self, size=25, offset=0):
        activity = [
            SimpleNamespace(date=a["date"],
                            actions=[(self._team(team_id), action, player, "")
                                     for team_id, action, player in a["actions"]])
            for a in self._fixture["activity"]
        ]
        return activity[offset:offset + size]


@pytest.fixture
def league_fixture():
    return load_fixture("league.json")


@pytest.fixture
def fake_league(league_fixture):
    return FakeLeague(league_fixture)


@pytest.fixture
def now(league_fixture):
    return datetime.fromisoformat(league_fixture["now"])


@pytest.fixture
def reports(league_fixture):
    """The real 16 Nov 2:15am report (first two pages) plus a small older one."""
    latest_url = REPORT_BASE + "Injury-Report_2026-11-16_02_15AM.pdf"
    latest = Report(latest_url, parse_report_time(latest_url),
                    entries_from_words(load_fixture("nba_report_words.json")["pages"]))
    older = league_fixture["older_report"]
    entries = [
        ReportEntry(game_date=date.fromisoformat(e["game_date"]), game_time=e["game_time"],
                    matchup=e["matchup"], team=e["team"], player=e["player"], status=e["status"],
                    reason=e["reason"], submitted=e.get("submitted", True))
        for e in older["entries"]
    ]
    return [latest, Report(older["url"], parse_report_time(older["url"]), entries)]


@pytest.fixture
def fake_fetch(reports):
    def fetch(year, now, lookback_days):
        return reports, []
    return fetch
