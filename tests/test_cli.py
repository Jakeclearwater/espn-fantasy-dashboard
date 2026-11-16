"""Runs export.main() end to end against the sample league, with no network."""

import json

import pytest

import export
from fantasy_dash.espn_source import EspnError

SECRET_S2 = "SECRET-S2-VALUE-DO-NOT-PRINT"
SECRET_SWID = "SECRET-SWID-VALUE"


@pytest.fixture
def offline(monkeypatch, tmp_path, fake_league, fake_fetch):
    """Point main() at the sample league, a temp folder and fake credentials."""
    monkeypatch.setattr(export, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(export.espn_source, "connect", lambda league_id, year, creds: fake_league)
    monkeypatch.setattr(export.nba_report, "fetch_recent_reports", fake_fetch)
    monkeypatch.setattr(export.config, "ESPN_S2", SECRET_S2)
    monkeypatch.setattr(export.config, "SWID", SECRET_SWID)
    return tmp_path / "out"


def test_one_command_writes_json_and_html(offline, capsys):
    assert export.main([]) == 0
    for name in ("rosters.json", "free_agents.json", "transactions.json", "waivers.json",
                 "outlook.json", "players.json"):
        json.loads((offline / name).read_text())
    html = (offline / "dashboard.html").read_text()
    assert "Fish 'n Chips" in html

    out = capsys.readouterr()
    assert "Your players who are out, questionable" in out.out
    assert "Ty Jerome (MEM)" in out.out
    assert "out, in an active lineup spot" in out.out
    assert "free agents by last-15-day average" in out.out


def test_secrets_never_reach_output_or_files(offline, capsys):
    export.main([])
    out = capsys.readouterr()
    written = "".join(p.read_text() for p in offline.iterdir())
    for secret in (SECRET_S2, SECRET_SWID):
        assert secret not in out.out + out.err
        assert secret not in written


def test_flags(offline, capsys):
    assert export.main(["--no-html", "--top", "2", "--days", "14", "--team", "Fish ’n Chips"]) == 0
    assert not (offline / "dashboard.html").exists()
    assert len(json.loads((offline / "free_agents.json").read_text())["players"]) == 2
    assert json.loads((offline / "transactions.json").read_text())["days"] == 14


def test_missing_credentials_exit_cleanly(offline, monkeypatch, capsys):
    monkeypatch.setattr(export.config, "ESPN_S2", "")
    assert export.main([]) == 2
    assert "Missing ESPN_S2" in capsys.readouterr().err


def test_espn_failure_exits_cleanly(offline, monkeypatch, capsys):
    def refuse(league_id, year, creds):
        raise EspnError("ESPN refused access while loading the league.")
    monkeypatch.setattr(export.espn_source, "connect", refuse)
    assert export.main([]) == 2
    assert "ESPN refused access" in capsys.readouterr().err
