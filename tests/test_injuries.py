from datetime import date, datetime

from fantasy_dash.injuries import (
    Coverage,
    ReportIndex,
    espn_status,
    name_key,
    reconcile,
    tip_off,
    urgency,
)
from fantasy_dash.nba_report import ET, Report, ReportEntry


def entry(player, status="Out", team="BKN", day=date(2026, 11, 16), submitted=True):
    return ReportEntry(game_date=day, game_time="07:30 (ET)", matchup="PHI@BKN", team=team,
                       player=player, status=status, reason="Injury/Illness - Knee", submitted=submitted)


def test_names_match_across_sources():
    assert name_key("Ernest Udeh Jr.") == name_key("Ernest Udeh")
    assert name_key("T.J. McConnell") == name_key("TJ McConnell")
    assert name_key("Nikola Jokić") == name_key("Nikola Jokic")
    assert name_key("Robert Williams III") == name_key("Robert Williams")
    assert name_key("Nae'Qwan Tomlin") == name_key("NaeQwan Tomlin")
    assert name_key("Jalen Smith") != name_key("Jabari Smith")


def test_espn_flags_map_to_dashboard_statuses():
    assert espn_status("OUT") == "Out"
    assert espn_status("DAY_TO_DAY") == "Day-to-day"
    assert espn_status("QUESTIONABLE") == "Questionable"
    assert espn_status("ACTIVE") == "Clear"
    assert espn_status([]) == "Clear"  # espn-api returns [] when the field is missing


def test_unique_surname_on_the_same_team_still_matches():
    cov = Coverage("BKN", date(2026, 11, 16), "07:30 (ET)", "PHI@BKN",
                   datetime(2026, 11, 16, 17, 0, tzinfo=ET), [entry("Nicolas Claxton")])
    assert cov.find("Nic Claxton").player == "Nicolas Claxton"


def test_shared_surname_does_not_guess():
    cov = Coverage("BKN", date(2026, 11, 16), "07:30 (ET)", "PHI@BKN",
                   datetime(2026, 11, 16, 17, 0, tzinfo=ET), [entry("Jalen Smith"), entry("Jabari Smith")])
    assert cov.find("Jaylen Smith") is None


def test_index_prefers_the_next_game_over_an_older_one():
    older = Report("old", datetime(2026, 11, 15, 23, 45, tzinfo=ET), [entry("A Player", day=date(2026, 11, 15))])
    newer = Report("new", datetime(2026, 11, 16, 2, 15, tzinfo=ET), [entry("A Player", "Questionable")])
    cov = ReportIndex([older, newer], date(2026, 11, 16)).coverage("BKN")
    assert cov.game_date == date(2026, 11, 16)
    assert cov.find("A Player").status == "Questionable"


def test_index_falls_back_to_an_older_report_when_the_newest_is_not_submitted():
    older = Report("old", datetime(2026, 11, 15, 23, 45, tzinfo=ET), [entry("A Player", day=date(2026, 11, 15))])
    newer = Report("new", datetime(2026, 11, 16, 2, 15, tzinfo=ET), [entry("", "", submitted=False)])
    cov = ReportIndex([older, newer], date(2026, 11, 16)).coverage("BKN")
    assert cov.game_date == date(2026, 11, 15)
    assert cov.published == older.published


def test_team_with_no_report_has_no_coverage():
    assert ReportIndex([], date(2026, 11, 16)).coverage("TOR") is None


def test_player_missing_from_a_covering_report_is_clear_with_espn_noted():
    report = Report("r", datetime(2026, 11, 16, 17, 0, tzinfo=ET), [entry("Someone Else")])
    row = {"player": "Cam Thomas", "nba_team": "BKN", "espn_status": "Day-to-day"}
    reconcile(row, ReportIndex([report], date(2026, 11, 16)), datetime(2026, 11, 16, 18, 0, tzinfo=ET), 3)
    assert row["status"] == "Clear"
    assert row["source"] == "NBA official injury report"
    assert "though ESPN lists day-to-day" in row["latest"]


def test_tip_off_reads_report_times_as_afternoon_or_evening():
    assert tip_off(date(2026, 11, 16), "07:00 (ET)") == datetime(2026, 11, 16, 19, 0, tzinfo=ET)
    assert tip_off(date(2026, 11, 16), "12:00 (ET)") == datetime(2026, 11, 16, 12, 0, tzinfo=ET)
    assert tip_off(date(2026, 11, 16), "") is None


def test_most_urgent_order():
    rows = [
        {"slot": "Bench", "status": "Clear"},
        {"slot": "IR", "status": "Out"},
        {"slot": "Bench", "status": "Out"},
        {"slot": "PG", "status": "Clear"},
        {"slot": "C", "status": "Unconfirmed"},
        {"slot": "SG", "status": "Questionable"},
        {"slot": "UTIL", "status": "Out"},
    ]
    ordered = [(r["slot"], r["status"]) for r in sorted(rows, key=urgency)]
    assert ordered == [
        ("UTIL", "Out"), ("SG", "Questionable"), ("C", "Unconfirmed"),
        ("Bench", "Out"), ("PG", "Clear"), ("Bench", "Clear"), ("IR", "Out"),
    ]
