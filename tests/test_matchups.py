from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from fantasy_dash.matchups import (DEFENDER_CAP, DEFENDER_LIFT, FACTOR_RANGE, Defence, GameLog, Matchups, describe,
                                   game_logs, key_defenders)
from fantasy_dash.projection import project_team
from fantasy_dash.recommend import next_lineup
from fantasy_dash.schedule import Game

NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
FIRST = 28  # scoring period for Wed 18 Nov (US)


class FakeCalendar:
    def __init__(self, games):
        self.games_by = games  # {(team, period): Game}

    def game(self, team, period):
        return self.games_by.get((team, period))

    def date_of(self, period):
        return date(2026, 11, 18) + timedelta(days=period - FIRST)

    def first_tip(self, period):
        return datetime(2026, 11, 19, 0, tzinfo=timezone.utc) + timedelta(days=period - FIRST)

    def games_after(self, periods, now):
        return [g for (_, p), g in self.games_by.items() if p in periods and g.tip > now]


def game(period, opponent, home=True):
    return Game(period, datetime(2026, 11, 19, 0, tzinfo=timezone.utc) + timedelta(days=period - FIRST), opponent, home)


def logs_for(player_id, position, games):
    """games: [(opponent, fpts)], all at 30 minutes."""
    return [GameLog(player_id, position, 10 + i, opponent, fpts, 30) for i, (opponent, fpts) in enumerate(games)]


def test_soft_and_tough_defences_are_rated_against_players_own_averages():
    logs = []
    for pid in range(10):  # ten centres averaging 30: big nights against ATL, quiet ones against BOS
        logs += logs_for(pid, "C", [("ATL", 39), ("BOS", 21), ("NYK", 30), ("NYK", 30)])
    defence = Defence(logs)
    assert defence.factor("ATL", "C") > 1.05 > 1 > 0.95 > defence.factor("BOS", "C")
    assert abs(defence.factor("NYK", "C") - 1) < 0.02
    # Shrunk towards average: ten games at 130% don't produce 130%.
    assert defence.factor("ATL", "C") < 1.2
    # No guards have played ATL, so guards fall back to ATL's overall figure.
    assert defence.factor("ATL", "PG") == defence.overall["ATL"]
    assert defence.factor("XXX", "PG") == 1.0


def test_one_big_night_moves_a_rating_only_a_little():
    # 60 points against a 33 average is 80% above, but it's one game.
    defence = Defence(logs_for(1, "PG", [("ATL", 60), ("NYK", 20), ("NYK", 20)]))
    assert 1 < defence.factor("ATL", "PG") < 1.1


def test_injury_exits_and_deep_bench_players_are_left_out():
    logs = logs_for(1, "SF", [("NYK", 30)] * 4)
    logs.append(GameLog(1, "SF", 99, "BOS", 2, 4))  # left after four minutes
    logs += logs_for(2, "SF", [("BOS", 1), ("BOS", 2), ("NYK", 15)])  # averages 6
    defence = Defence(logs)
    assert "BOS" not in defence.overall
    assert defence.players == 1


def test_game_logs_read_espn_player_cards():
    player = SimpleNamespace(position="PF", proTeam="SAS", stats={
        "2027_total": {"applied_total": 300, "total": {"GP": 10}},
        "30": {"applied_total": 41.5, "team": "PHL", "total": {"GP": 1.0, "MIN": 33.0}},
        "31": {"applied_total": 0, "team": "BOS", "total": {}},  # no game played
    })
    assert game_logs({7: (player, {})}) == [GameLog(7, "PF", 30, "PHI", 41.5, 33.0)]


def card(name, team, position, games, minutes, steals, blocks):
    return SimpleNamespace(name=name, proTeam=team, position=position, stats={
        "2027_total": {"total": {"GP": games, "MIN": minutes * games, "STL": steals * games, "BLK": blocks * games}}})


def test_key_defenders_need_minutes_and_steals_or_blocks():
    cards = {
        1: (card("Rim Protector", "SAS", "C", 10, 30, 0.6, 3.0), {}),
        2: (card("Pest", "SAS", "SG", 10, 28, 2.0, 0.4), {}),
        3: (card("Scorer", "SAS", "SF", 10, 34, 0.8, 0.3), {}),
        4: (card("Spot Minutes", "SAS", "C", 10, 12, 1.0, 2.0), {}),
    }
    found = key_defenders(cards, 2027)
    assert [d["player"] for d in found["SAS"]] == ["Rim Protector", "Pest"]
    assert found["SAS"][0]["blocks"] == 3.0


def rater(defenders=None, status=None, games=None, logs=None):
    defence = Defence(logs or [])
    calendar = FakeCalendar(games or {})
    return Matchups(defence, defenders or {}, status or {}, calendar, NOW), calendar


def test_a_missing_defender_lifts_his_position_most():
    defenders = {"SAS": [{"player_id": 50, "player": "Victor Wembanyama", "position": "C"}]}
    status = {50: {"status": "Out", "expected_return": "2026-12-01", "espn_status": "Out"}}
    games = {(team, FIRST): game(FIRST, "SAS") for team in ("CHA", "MIA", "NYK")}
    matchups, _ = rater(defenders, status, games)
    centre = matchups.game({"nba_team": "CHA", "position": "C"}, FIRST)
    forward = matchups.game({"nba_team": "MIA", "position": "PF"}, FIRST)
    guard = matchups.game({"nba_team": "NYK", "position": "PG"}, FIRST)
    assert centre["factor"] == round(1 + DEFENDER_LIFT, 3) and centre["missing"] == ["Victor Wembanyama"]
    assert forward["factor"] == round(1 + DEFENDER_LIFT / 2, 3)
    assert guard["factor"] == 1.0 and guard["missing"] == []
    # Back in the lineup: no lift.
    status[50] = {"status": "Clear", "espn_status": "Clear"}
    assert matchups.game({"nba_team": "CHA", "position": "C"}, FIRST)["factor"] == 1.0


def test_lifts_are_capped_and_ratings_stay_in_range():
    out = {"status": "Out", "out_for_season": True, "espn_status": "Out"}
    defenders = {"ATL": [{"player_id": i, "player": f"D{i}", "position": "C"} for i in range(3)]}
    logs = []
    for pid in range(10, 40):
        logs += logs_for(pid, "C", [("ATL", 60)] * 3 + [("NYK", 20)] * 3)
    matchups, _ = rater(defenders, {i: out for i in range(3)}, {("CHA", FIRST): game(FIRST, "ATL")}, logs)
    result = matchups.game({"nba_team": "CHA", "position": "C"}, FIRST)
    assert result["factor"] == FACTOR_RANGE[1]
    assert len(result["missing"]) == 3
    assert result["defence"] * (1 + DEFENDER_CAP) > FACTOR_RANGE[1]


def test_games_already_started_are_not_rated():
    games = {("CHA", FIRST): Game(FIRST, NOW - timedelta(hours=1), "ATL", True)}
    matchups, _ = rater(games=games)
    assert matchups.game({"nba_team": "CHA", "position": "C"}, FIRST) is None


def row(pid, slot, value, team, position="C"):
    return {"player_id": pid, "player": f"P{pid}", "slot": slot, "nba_team": team, "position": position,
            "status": "Clear", "espn_status": "Clear", "season_avg": value, "season_games": 10,
            "eligible_slots": ["C", "UTIL"]}


def test_projection_and_lineup_advice_use_the_matchup():
    logs = []
    for pid in range(100, 130):  # BOS shuts down centres, ATL gives up plenty
        logs += logs_for(pid, "C", [("BOS", 18), ("ATL", 42), ("NYK", 30), ("NYK", 30)])
    games = {("CHA", FIRST): game(FIRST, "BOS"), ("MIA", FIRST): game(FIRST, "ATL")}
    matchups, calendar = rater(games=games, logs=logs)
    starter, backup = row(1, "UTIL", 30, "CHA"), row(2, "Bench", 28, "MIA")
    matchups.attach([starter, backup], [FIRST])
    assert starter["matchups"][FIRST]["factor"] < 0.9 < 1.1 < backup["matchups"][FIRST]["factor"]
    projection = project_team([starter, backup], calendar, [FIRST], NOW, ["UTIL"])
    assert projection["days"][0]["starters"] == [2]
    plan = next_lineup([starter, backup], projection)
    assert [s["player"] for s in plan["start"]] == ["P2"]
    assert plan["sit"][0]["reason"] == "tough matchup"
    assert describe(starter["matchups"][FIRST]).startswith("v BOS (tough, -")
