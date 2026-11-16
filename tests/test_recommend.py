import pytest

from export import build_export
from fantasy_dash.recommend import protection
from tests.conftest import NZ

MINE = 9


@pytest.fixture
def data(fake_league, now, fake_fetch):
    return build_export(fake_league, "Fish 'n Chips", 6, 7, now, fetch_reports=fake_fetch, local_tz=NZ)[1]


def test_week_outlook_looks_ahead_to_the_next_matchup(data):
    outlook = data["outlook"]
    assert outlook["matchup_period"] == 5 and not outlook["is_current"]
    assert data["opponent"]["name"] == "Evil Larry"  # next week's opponent, not this week's
    assert outlook["opponents"][MINE] == 4 and outlook["opponents"][2] is None
    assert outlook["previous"][MINE] == {"matchup_period": 4, "mine": 1050.5, "theirs": 980.0,
                                         "opponent": {"id": 2, "name": "Third Wheel", "owner": "Sam Other"}}
    assert outlook["previous"][2]["opponent"]["name"] == "Fish 'n Chips"  # every team's last result
    assert set(outlook["projections"]) == {MINE, 4, 2}  # every team, for the team picker
    mine = outlook["projections"][MINE]
    assert len(mine["days"]) == 7
    assert mine["points"] > 0 and outlook["projected_finish"][MINE] == round(mine["points"], 1)
    assert outlook["add_limits"][MINE] == {"limit": 1, "per_day": True, "used_this_matchup": 1}
    assert outlook["add_limits"][4]["used_this_matchup"] == 0
    assert outlook["waiver_ranks"] == {9: 3, 4: 1, 2: 2}


def test_out_players_and_ir_sit_out_the_projection(data):
    mine = data["outlook"]["projections"][MINE]
    assert 111 not in mine["player_games"]  # Banchero is on IR
    assert mine["player_games"][107] and not mine["player_starts"].get(107)  # Barnes: flagged out, no report


def test_lineup_checks_flag_out_starters_for_every_team(data):
    checks = data["outlook"]["checks"]
    assert any(c.startswith("Ty Jerome is out but in your PG slot") for c in checks[MINE])
    assert any(c.startswith("Caleb Wilson is out but in Evil Larry's SG slot") for c in checks[4])


def test_keepers_are_never_suggested_as_drops(data):
    moves = data["suggestions"][MINE]
    kept = {k["player"] for k in moves["protected"]}
    assert {"Alperen Sengun", "Jalen Brunson", "Evan Mobley", "Scottie Barnes"} <= kept
    for move in moves["long_term"] + moves["streamers"]:
        assert move["drop"]["player"] not in kept


def test_suggestions_rank_and_explain(data):
    moves = data["suggestions"][MINE]
    long_term = moves["long_term"]
    assert long_term and {m["drop"]["player"] for m in long_term} == {"Ernest Udeh Jr."}  # weakest non-keeper
    gains = [m["value_gain"] for m in long_term]
    assert gains == sorted(gains, reverse=True)
    assert all(m["add"]["status"] != "Out" for m in long_term + moves["streamers"])
    first = long_term[0]
    assert first["reasons"][0].startswith(first["add"]["player"] + ":")
    assert any(r.startswith("Long-term value") for r in first["reasons"])
    assert first["how"]
    for move in moves["streamers"]:  # never worse long term than who they replace
        assert move["add"]["talent"] >= move["drop"]["talent"] - 0.05
    week = [m["week_gain"] for m in moves["streamers"]]
    assert week == sorted(week, reverse=True)


def test_suggestions_exist_for_other_teams_too(data):
    theirs = data["suggestions"][4]
    assert "Evil Larry is" in "".join(m["how"] for m in theirs["long_term"] + theirs["streamers"]) or \
        not (theirs["long_term"] or theirs["streamers"])


def test_injured_free_agents_go_on_the_watch_list(data):
    watch = data["suggestions"][MINE]["watch"]
    assert [w["add"]["player"] for w in watch][:1] == ["Sam Merrill"]
    assert "expected back Wed 25 Nov" in watch[0]["reasons"][0]


def test_an_injured_star_easing_back_is_protected():
    def row(pid, l15, games15, season, slot="Bench", status="Clear"):
        return {"player_id": pid, "player": f"P{pid}", "slot": slot, "status": status, "espn_status": status,
                "last_15_avg": l15, "last_15_games": games15, "season_avg": season, "season_games": 10,
                "projected_avg": season}
    roster = [row(i, 20, 6, 20) for i in range(1, 9)] + [row(9, 15, 6, 15)]
    star = row(10, 5.0, 1, 45.0)  # one quiet game back from injury, a star on the season
    assert 10 in protection(roster + [star])
    assert 9 not in protection(roster + [star])


def test_player_details_cover_cards_games_news_and_notes(data):
    jerome = data["details"][101]
    assert jerome["injury"] == {"type": "Hip", "expected_return": "2026-11-19", "out_for_season": False}
    assert jerome["ownership"] == {"rostered": 44.0, "change": -2.5, "started": 30.1}
    assert jerome["notes"][0]["headline"] == "Jerome (hip) ruled out Monday"
    assert "signals" in jerome and "boosts" in jerome
    assert [a["label"] for a in jerome["averages"]] == ["Season", "Last 30", "Last 15", "Last 7", "ESPN projection"]
    edey = data["details"][105]
    assert [g["fpts"] for g in edey["recent_games"]] == [30.0, 18.0]  # newest first
    assert edey["stat_line"] is not None
    assert all(g["start"] in (True, False) for g in edey["upcoming"])  # rostered: start or bench
    tyson = data["details"][402]
    assert all(g["start"] is None for g in tyson["upcoming"])  # free agent: no lineup yet


def test_matchups_are_rated_for_the_week(data):
    m = data["matchups"]
    assert m["positions"] == ["PG", "SG", "SF", "PF", "C"]
    assert m["soft"] > 1 > m["tough"]
    rated = [r for r in data["rosters"] + data["free_agents"] if r.get("matchups")]
    assert rated and all(r["position"] in m["positions"] for r in rated)
    for r in rated:
        for game in r["matchups"].values():
            assert 0.85 <= game["factor"] <= 1.15 and game["opponent"]
    upcoming = [g for d in data["details"].values() for g in d["upcoming"]]
    assert any(g["matchup"] for g in upcoming)
