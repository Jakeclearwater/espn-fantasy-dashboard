from datetime import datetime, timezone

import pytest

from export import build_export
from fantasy_dash.projection import per_game_value, starting_slots
from fantasy_dash.schedule import Calendar
from fantasy_dash.trades import STAR_SHARE, THEIR_MAX_LOSS, SeasonModel, settle, suggest_trades
from tests.conftest import NZ

SLOT_COUNTS = {"0": 1, "1": 1, "2": 1, "3": 1, "4": 1, "5": 1, "6": 1, "11": 3, "12": 3, "13": 1}


@pytest.fixture
def exported(fake_league, now, fake_fetch):
    return build_export(fake_league, "Fish 'n Chips", 6, 7, now, fetch_reports=fake_fetch, local_tz=NZ)


def row(player_id, value, slot="Bench", status="Clear", **extra):
    return {"player_id": player_id, "player": f"P{player_id}", "season_avg": value, "slot": slot,
            "status": status, "espn_status": status, "nba_team": "MEM", "eligible_slots": ["UTIL"], **extra}


def test_settle_drops_the_weakest_and_fills_open_spots():
    roster = [row(i, 10 + i) for i in range(5)]
    trimmed, dropped, added = settle(roster, 4, 0, [], keep={0})
    assert [r["player_id"] for r in dropped] == [1]  # player 0 is weaker but was just traded for
    filled, dropped, added = settle(roster[:3], 4, 0, [row(99, 30)])
    assert [r["player_id"] for r in added] == [99] and len(filled) == 4


def test_settle_uses_a_free_ir_slot_before_dropping_anyone():
    roster = [row(i, 20) for i in range(4)] + [row(9, 40, status="Out")]
    settled, dropped, _ = settle(roster, 4, 1, [])
    assert dropped == [] and next(r for r in settled if r["player_id"] == 9)["slot"] == "IR"


def test_quick_projection_is_never_below_the_positional_one(fake_league, now, exported):
    files, _ = exported
    rows = [r for r in files["rosters.json"]["players"] if r["team_id"] == 9]
    weeks = {str(m): [m] for m in range(1, 20)}
    calendar = Calendar(fake_league.pro_schedule, 1, 167, fake_league.matchup_ids, weeks)
    model = SeasonModel(calendar, now, 167, starting_slots(SLOT_COUNTS))
    full, empty = model.full(rows)
    assert model.quick(rows) >= full > 0
    assert empty >= 0


def test_trade_ideas_help_you_without_costing_them_much(exported):
    _, data = exported
    ideas = data["trades"]
    assert ideas["deadline"] is None and not ideas["deadline_passed"]
    assert ideas["horizon"]["days"] > 7
    assert set(ideas["by_team"]) == {9, 4, 2}  # worked out for every team
    every = [t for team_ideas in ideas["by_team"].values() for t in team_ideas]
    for team_id, team_ideas in ideas["by_team"].items():
        partners = [t["partner"]["id"] for t in team_ideas]
        assert len(partners) == len(set(partners)) and team_id not in partners  # one idea per other team
    for trade in every:
        assert trade["my_gain"] >= 100
        assert trade["their_gain"] >= -THEIR_MAX_LOSS * 100 * abs(trade["their_gain_pct"] or 1) - 1
        assert trade["kind"] == ("both_gain" if trade["their_gain"] >= 0 else "close")
        sides = (trade["give"], trade["get"])
        if len(sides[0]) != len(sides[1]):
            two, one = sides if len(sides[0]) == 2 else sides[::-1]
            assert max(p["value"] for p in two) >= STAR_SHARE * one[0]["value"] - 0.1
        assert trade["reasons"] is not None
    traded = {p["player_id"] for t in every for p in t["give"] + t["get"]}
    assert traded <= set(int(k) for k in data["details"])  # every traded player has a details panel


def test_other_teams_ideas_are_worded_for_that_team(exported):
    _, data = exported
    for team_id, team_ideas in data["trades"]["by_team"].items():
        for trade in team_ideas:
            text = " ".join(trade["reasons"])
            assert ("you'd" in text) <= (team_id == 9)  # "you" only ever means your own team


def test_no_ideas_after_the_deadline(fake_league, now, fake_fetch):
    fake_league.settings.trade_deadline = int(datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp() * 1000)
    _, data = build_export(fake_league, "Fish 'n Chips", 6, 7, now, fetch_reports=fake_fetch, local_tz=NZ)
    assert data["trades"]["deadline_passed"] and all(ideas == [] for ideas in data["trades"]["by_team"].values())


def test_a_depth_for_star_trade_that_helps_both_teams(now):
    """A deep team and a stars-and-scrubs team, three starters each, everyone
    playing every day. Giving 26 + 25 for the 40 lifts your lineup and gives
    them two starters for one; they drop their weakest to make room."""
    tip = int(datetime(2026, 11, 17, tzinfo=timezone.utc).timestamp() * 1000)
    schedule = {29: {str(p): [{"date": tip + (p - 28) * 86_400_000, "homeProTeamId": 29, "awayProTeamId": 1}]
                     for p in range(28, 48)}}
    calendar = Calendar(schedule, 1, 167, {}, {})
    mine = [row(100 + v, v, team_id=1) for v in (30, 28, 26, 25, 24)]
    theirs = [row(200 + v, v, team_id=2) for v in (40, 10, 9, 8, 7)]
    free_agent = row(999, 5)
    teams = {1: {"id": 1, "name": "Mine", "owner": ""}, 2: {"id": 2, "name": "Theirs", "owner": "Them"}}

    ideas = suggest_trades(mine + theirs, 1, teams, [free_agent], calendar, now,
                           ["UTIL"] * 3, {"11": 3, "12": 2}, 167)

    assert len(ideas["suggestions"]) == 1
    trade = ideas["suggestions"][0]
    assert {p["value"] for p in trade["give"]} == {26, 25} and [p["value"] for p in trade["get"]] == [40]
    assert trade["kind"] == "both_gain"
    assert trade["my_gain"] == 14 * 20 and trade["their_gain"] == 2 * 20  # per day, over 20 days
    assert trade["you_add"]["player_id"] == 999 and trade["they_drop"]["value"] == 7
    assert trade["reasons"][0].startswith("Turns two players into one stronger one")
