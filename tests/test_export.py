import json
from types import SimpleNamespace

import pytest
from espn_api.requests.espn_requests import ESPNAccessDenied

from export import build_export
from fantasy_dash import espn_source
from fantasy_dash.espn_source import EspnError, espn_errors, split_average
from tests.conftest import NZ


@pytest.fixture
def exported(fake_league, now, fake_fetch):
    return build_export(fake_league, "Fish 'n Chips", 4, 7, now, fetch_reports=fake_fetch, local_tz=NZ)


def by_player(rows):
    return {r["player"]: r for r in rows}


def test_statuses_follow_the_nba_report_with_espn_as_fallback(exported):
    files, _ = exported
    rows = by_player(files["rosters.json"]["players"])
    expected = {
        "Ty Jerome": "Out",              # ESPN Out, NBA Out
        "Peyton Watson": "Questionable",  # ESPN day-to-day, NBA questionable
        "Klay Thompson": "Clear",         # ESPN questionable, NBA lists him available
        "Evan Mobley": "Out",             # ESPN shows nothing yet, NBA has him out
        "Zach Edey": "Questionable",      # newest game wins over an older Out listing
        "Jalen Brunson": "Clear",         # no report for NYK, ESPN clear
        "Scottie Barnes": "Unconfirmed",  # ESPN Out, Toronto not covered by any report
        "Ernest Udeh Jr.": "Out",
        "Jalen Smith": "Questionable",
        "Alperen Sengun": "Day-to-day",   # Houston not submitted today, probable yesterday
        "Paolo Banchero": "Out",
        "Pelle Larsson": "Questionable",
        "Caleb Wilson": "Out",
        "Devin Booker": "Clear",
        "Jonathan Isaac": "Out",
        "Khalifa Diop": "Out",
        "Trae Young": "Clear",
    }
    assert {name: rows[name]["status"] for name in expected} == expected


def test_rows_carry_source_and_as_of(exported):
    files, _ = exported
    rows = by_player(files["rosters.json"]["players"])

    mobley = rows["Evan Mobley"]
    assert mobley["source"] == "NBA official injury report"
    assert mobley["as_of"] == "2026-11-16T02:15-05:00"
    assert mobley["injury"] == "Left Great Toe Sprain"
    # His only ESPN note is two weeks old, so the report listing is used instead.
    assert mobley["latest"] == "Listed out for CHI @ CLE, Tue 17 Nov, 1:00pm"

    jerome = rows["Ty Jerome"]
    assert jerome["latest"] == "Jerome (hip) ruled out Monday"
    assert jerome["source"] == "NBA official injury report; note from Rotowire via ESPN, Mon 16 Nov, 11:00am"

    klay = rows["Klay Thompson"]  # listed 'Available', which counts as clear
    assert klay["latest"] == "Listed available for HOU @ MIA, Tue 17 Nov, 1:30pm"

    barnes = rows["Scottie Barnes"]
    assert barnes["latest"] == "Barnes (ankle) considered week-to-week"
    assert barnes["as_of"] == "2026-11-14T00:00+00:00"
    assert barnes["source"].startswith("ESPN flag only")

    assert rows["Jalen Brunson"]["source"] == "ESPN"


def test_every_team_is_exported_for_the_team_picker(exported):
    files, data = exported
    assert {r["team"] for r in files["rosters.json"]["players"]} == {"Fish 'n Chips", "Evil Larry", "Third Wheel"}
    assert {r["team"] for r in data["rosters"]} == {"Fish 'n Chips", "Evil Larry", "Third Wheel"}  # any team can be viewed
    assert [t["name"] for t in data["teams"]] == ["Evil Larry", "Fish 'n Chips", "Third Wheel"]
    assert data["opponent"] == {"id": 4, "name": "Evil Larry", "owner": "Connor O'Toole"}
    assert data["my_team"]["owner"] == "Jake Clearwater"


def test_roster_row_fields_and_lineup_order(exported):
    files, _ = exported
    mine = [r for r in files["rosters.json"]["players"] if r["team_id"] == 9]
    assert [r["slot"] for r in mine] == ["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL", "Bench", "Bench", "IR"]
    first = mine[0]
    assert list(first)[:10] == ["team", "slot", "player", "nba_team", "positions", "status",
                                "injury", "latest", "as_of", "source"]
    assert (first["player"], first["nba_team"], first["positions"]) == ("Ty Jerome", "MEM", "PG/SG")


def test_team_name_matching_tolerates_curly_apostrophes(fake_league):
    assert espn_source.find_team(fake_league, "fish ’n chips").team_id == 9


def test_unknown_team_lists_the_real_names(fake_league):
    with pytest.raises(EspnError, match='"Evil Larry"'):
        espn_source.find_team(fake_league, "Nobody")


def test_free_agents_are_ranked_by_last_15_day_average(exported):
    files, _ = exported
    players = files["free_agents.json"]["players"]
    assert [p["player"] for p in players] == ["Russell Westbrook", "Jaylon Tyson", "Sam Merrill", "Gary Payton II"]
    tyson = players[1]
    assert (tyson["last_7_avg"], tyson["last_15_avg"], tyson["season_avg"]) == (27.0, 25.3, 19.5)
    assert tyson["percent_rostered"] == 31.0
    assert tyson["availability"] == "Waivers"
    assert tyson["status"] == "Questionable"
    assert players[2]["status"] == "Out"
    assert players[2]["last_7_avg"] is None  # an empty split means no games, not an average of 0
    # ESPN's own average for this split is 0, but 3 games and 59.7 points is 19.9 a game.
    assert players[3]["last_7_avg"] == 19.9


def test_free_agents_ask_espn_for_its_last_15_day_leaders(fake_league):
    sent = []
    original = fake_league.espn_request.league_get

    def recording(params=None, headers=None, extend=""):
        sent.append(json.loads(headers["x-fantasy-filter"])["players"])
        return original(params=params, headers=headers, extend=extend)

    fake_league.espn_request.league_get = recording
    entries = espn_source.free_agent_pool(fake_league, 4)
    assert sent[0]["sortAppliedStatAverage"]["value"] == "022027"
    assert len(entries) == len({e["id"] for e in entries})  # merged without duplicates


def test_transactions_cover_only_the_requested_days(exported, fake_league, now):
    files, _ = exported
    moves = files["transactions.json"]["transactions"]
    assert [(m["type"], m["team"], m["player"]) for m in moves] == [
        ("Add", "Evil Larry", "Russell Westbrook"),
        ("Drop", "Evil Larry", "Gary Payton II"),
        ("Waiver claim", "Fish 'n Chips", "Jalen Smith"),
        ("Drop", "Fish 'n Chips", "Sam Merrill"),
        ("Traded away", "Third Wheel", "Trae Young"),  # ESPN names the sending team
    ]
    longer = espn_source.recent_transactions(fake_league, 14, now)
    assert any(m["player"] == "Too Old" for m in longer)


def test_waivers_report_priority_faab_and_next_run(exported):
    files, _ = exported
    waivers = files["waivers.json"]
    assert waivers["waiver_priority"] == 3
    assert waivers["team_count"] == 3
    assert (waivers["faab_budget"], waivers["faab_spent"], waivers["faab_remaining"]) == (100, 13, 87)
    assert waivers["next_processing"] == "2026-11-17T08:00+00:00"  # the past deadline is ignored
    assert [o["team"] for o in waivers["waiver_order"]] == ["Evil Larry", "Third Wheel", "Fish 'n Chips"]


def test_category_leagues_rank_on_standard_points():
    player = SimpleNamespace(stats={"2027_last_15": {"applied_total": 0, "total": {
        "GP": 2, "PTS": 40, "REB": 10, "AST": 8, "STL": 2, "BLK": 2, "TO": 4,
        "FGM": 16, "FGA": 32, "FTM": 6, "FTA": 8, "3PM": 2}}})
    # Per game: 20 + 5 + 8 + 4 + 4 - 4 + 16 - 16 + 3 - 4 + 1
    assert split_average(player, 2027, "last_15", points_league=False) == 37.0
    assert split_average(player, 2027, "last_7", points_league=False) is None


def test_auth_failures_explain_what_to_do():
    with pytest.raises(EspnError, match="espn_s2 or SWID"):
        with espn_errors("loading the league"):
            raise ESPNAccessDenied("League 1 cannot be accessed")


def test_unexpected_data_points_at_an_api_change():
    with pytest.raises(EspnError, match="unofficial API has probably changed"):
        with espn_errors("reading rosters"):
            raise KeyError("lineupSlotId")


def test_transactions_keep_paging_when_espn_caps_the_page_size(fake_league, now):
    calls = []
    full = fake_league.recent_activity

    def capped(size=25, offset=0):
        calls.append(offset)
        return full(size=1, offset=offset)  # ESPN handing back one activity at a time

    fake_league.recent_activity = capped
    moves = espn_source.recent_transactions(fake_league, 7, now)
    assert {m["player"] for m in moves} >= {"Russell Westbrook", "Jalen Smith", "Trae Young"}
    assert calls == [0, 1, 2, 3]  # stops once a page reaches past the window


def test_dns_failures_say_so():
    import socket

    import requests
    from urllib3.exceptions import MaxRetryError, NameResolutionError

    reason = NameResolutionError("lm-api-reads.fantasy.espn.com", None, socket.gaierror(8, "not known"))
    with pytest.raises(EspnError, match="look up ESPN's server address"):
        with espn_errors("loading the league"):
            raise requests.ConnectionError(MaxRetryError(None, "/", reason))
