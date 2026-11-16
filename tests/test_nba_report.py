from datetime import date, datetime
from types import SimpleNamespace

import pytest

from fantasy_dash import nba_report
from fantasy_dash.nba_report import (
    ET,
    entries_from_words,
    parse_report_time,
    player_display_name,
    report_url,
    team_abbrev,
)
from tests.conftest import load_fixture


@pytest.fixture
def entries():
    return entries_from_words(load_fixture("nba_report_words.json")["pages"])


def find(entries, player):
    return next(e for e in entries if e.player == player)


def test_reads_every_row_from_the_sample_pages(entries):
    assert len(entries) == 30
    assert {e.status for e in entries if e.submitted} == {"Out", "Questionable", "Available"}
    assert all(e.game_date == date(2026, 11, 16) for e in entries)


def test_team_and_matchup_carry_forward_across_a_page_break(entries):
    jerome = find(entries, "Ty Jerome")  # first row on page 2, with no team cell
    assert (jerome.team, jerome.matchup, jerome.status) == ("MEM", "MEM@WAS", "Out")


def test_reason_wrapped_above_and_below_the_player_line_is_joined(entries):
    assert find(entries, "Caleb Wilson").reason == "Injury/Illness - Right Metacarpal Fracture"
    assert find(entries, "Paolo Banchero").reason == "Injury/Illness - Right High Ankle Sprain"


def test_names_are_turned_around_and_keep_suffixes(entries):
    assert find(entries, "Ernest Udeh Jr.").team == "CLE"
    assert find(entries, "Scotty Pippen Jr.").team == "MEM"
    assert player_display_name("Udeh, Jr., Ernest") == "Ernest Udeh Jr."


def test_not_yet_submitted_teams_are_marked(entries):
    pending = {e.team for e in entries if not e.submitted}
    assert pending == {"WAS", "BOS", "HOU", "PHI"}
    assert all(e.player == "" for e in entries if not e.submitted)


def test_report_time_comes_from_the_file_name():
    assert parse_report_time("Injury-Report_2026-11-16_02_15AM.pdf") == datetime(2026, 11, 16, 2, 15, tzinfo=ET)
    assert parse_report_time("Injury-Report_2026-11-15_11_45PM.pdf") == datetime(2026, 11, 15, 23, 45, tzinfo=ET)
    assert parse_report_time("Injury-Report_2024-01-15_05PM.pdf") == datetime(2024, 1, 15, 17, 0, tzinfo=ET)


def test_report_url_uses_twelve_hour_slots():
    assert report_url(datetime(2026, 11, 16, 0, 0, tzinfo=ET)).endswith("Injury-Report_2026-11-16_12_00AM.pdf")
    assert report_url(datetime(2026, 11, 16, 14, 15, tzinfo=ET)).endswith("Injury-Report_2026-11-16_02_15PM.pdf")


def test_team_names_map_to_abbreviations():
    assert team_abbrev("LA Clippers") == "LAC"
    assert team_abbrev("Los Angeles Lakers") == "LAL"
    assert team_abbrev("NewOrleansPelicans") == "NOP"
    assert team_abbrev("Philadelphia 76ers") == "PHI"


class FakeSession:
    """Serves an index page and a set of report URLs that 'exist'."""

    def __init__(self, index_links, existing, broken=()):
        self.index_links = index_links
        self.existing = set(existing) | set(index_links)
        self.broken = set(broken)

    def get(self, url, **kwargs):
        if url.startswith("https://official.nba.com/"):
            text = " ".join(f'<a href="{link}">report</a>' for link in self.index_links)
            return SimpleNamespace(status_code=200, text=text)
        if url in self.broken:
            return SimpleNamespace(status_code=403, content=b"")
        if url in self.existing:
            return SimpleNamespace(status_code=200, content=b"%PDF")
        return SimpleNamespace(status_code=403, content=b"")

    def head(self, url, **kwargs):
        return SimpleNamespace(status_code=200 if url in self.existing else 403)


def test_fetch_uses_the_newest_listed_report_and_each_days_final_report(monkeypatch):
    base = nba_report.REPORT_BASE
    now = datetime(2026, 11, 16, 2, 20, tzinfo=ET)
    session = FakeSession(
        index_links=[base + "Injury-Report_2026-11-16_02_00AM.pdf", base + "Injury-Report_2026-11-16_02_15AM.pdf",
                     base + "Injury-Report_2026-11-16_03_00AM.pdf"],  # not published yet relative to now
        existing=[base + "Injury-Report_2026-11-15_11_45PM.pdf", base + "Injury-Report_2026-11-14_11_30PM.pdf"],
    )
    monkeypatch.setattr(nba_report, "parse_report_pdf", lambda content: [])

    reports, warnings = nba_report.fetch_recent_reports(2027, now, lookback_days=3, session=session)

    assert [r.url.rsplit("_", 3)[-3:] for r in reports] == [
        ["2026-11-16", "02", "15AM.pdf"], ["2026-11-15", "11", "45PM.pdf"], ["2026-11-14", "11", "30PM.pdf"]]
    assert warnings == []


def test_fetch_reports_problems_as_warnings_instead_of_failing(monkeypatch):
    base = nba_report.REPORT_BASE
    now = datetime(2026, 11, 16, 2, 20, tzinfo=ET)
    latest = base + "Injury-Report_2026-11-16_02_15AM.pdf"
    session = FakeSession(index_links=[latest], existing=[], broken=[latest])
    monkeypatch.setattr(nba_report, "parse_report_pdf", lambda content: [])

    reports, warnings = nba_report.fetch_recent_reports(2027, now, lookback_days=1, session=session)

    assert reports == []
    assert any("Injury-Report_2026-11-16_02_15AM.pdf" in w for w in warnings)
    assert any("Unconfirmed" in w for w in warnings)
