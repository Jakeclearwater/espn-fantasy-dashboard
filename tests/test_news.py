from datetime import date, datetime, timezone

from fantasy_dash.news import BOOST_CAP, find_opportunities, note_signals
from fantasy_dash.projection import talent_value, value_on

NOW = datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc)


def note(headline, story="", published="2026-11-16T20:00:00Z"):
    return {"headline": headline, "story": story, "published": published}


def test_teammate_absence_and_role_are_read_from_a_note():
    signals = note_signals([note(
        "Miller scored 30 points in Sunday's win.",
        "With Coby White (ankle) sidelined, Miller should see more shots this week.")],
        ["Coby White", "LaMelo Ball"], NOW, "Brandon Miller")
    assert signals[0]["absences"] == ["Coby White"]
    assert "Role up" in signals[0]["tags"]
    assert "Coby White" in signals[0]["evidence"]


def test_bench_role_and_returns_are_tagged():
    down = note_signals([note("Smith will come off the bench Monday.")], [], NOW)
    back = note_signals([note("Jones (knee) will return Wednesday against Utah.")], [], NOW)
    assert down[0]["tags"][0] == "Role down"
    assert "Returning" in back[0]["tags"]


def test_old_notes_are_ignored():
    assert note_signals([note("Smith will start.", published="2026-10-01T00:00:00Z")], [], NOW) == []


def player(pid, positions, minutes, value, status="Clear", **extra):
    return {"player_id": pid, "player": f"P{pid}", "nba_team": "CHA", "positions": positions, "minutes": minutes,
            "season_avg": value, "season_games": 12, "last_15_avg": value, "last_15_games": 6,
            "projected_avg": value, "status": status, "espn_status": status, "team": "Free agent", "team_id": None,
            **extra}


def test_an_injured_regulars_role_goes_to_matching_teammates():
    out = player(1, "PG/SG", 32, 40, status="Out", expected_return="2026-11-25")
    guard = player(2, "SG/SF", 30, 25)
    centre = player(3, "C", 30, 25)
    found = find_opportunities([out, guard, centre], NOW)
    assert found[0]["out"]["player_id"] == 1 and found[0]["until"] == "2026-11-25"
    bumps = {b["player_id"]: b["bump"] for b in found[0]["beneficiaries"]}
    assert bumps[2] > bumps[3]  # the guard shares a position, the centre doesn't
    assert bumps[2] <= BOOST_CAP * talent_value(guard)
    assert guard["boosts"][0]["from"] == "P1"


def test_a_long_absence_counts_for_half():
    team = lambda: [player(2, "PG", 30, 40), player(3, "PG", 30, 40)]  # two teammates, so no cap applies
    fresh = find_opportunities([player(1, "PG", 32, 40, status="Out")] + team(), NOW)
    stale = find_opportunities([player(1, "PG", 32, 40, status="Out", last_15_games=0)] + team(), NOW)
    assert stale[0]["beneficiaries"][0]["bump"] < fresh[0]["beneficiaries"][0]["bump"]
    assert stale[0]["already_missing"]


def test_boosts_end_on_the_return_date():
    row = player(2, "PG", 30, 30, boosts=[{"from": "P1", "bump": 4.0, "until": "2026-11-25"}])
    assert value_on(row, date(2026, 11, 24)) == talent_value(row) + 4.0
    assert value_on(row, date(2026, 11, 25)) == talent_value(row)


def test_sentences_about_other_players_are_not_credited():
    signals = note_signals([note(
        "Embiid (knee) is questionable for Friday.",
        "If he sits, Dean Wade could see more minutes.")], ["Dean Wade"], NOW, "Joel Embiid")
    assert signals and "Role up" not in signals[0]["tags"]
