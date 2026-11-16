from datetime import date, datetime, timezone

from fantasy_dash.schedule import Calendar, outlook_matchup


def calendar(league):
    weeks = {str(m): [m] for m in range(1, 20)}
    return Calendar(league.pro_schedule, league.firstScoringPeriod, league.finalScoringPeriod,
                    league.matchup_ids, weeks)


def test_opening_night_and_dates_come_from_the_game_schedule(fake_league):
    cal = calendar(fake_league)
    assert cal.opening_day == date(2026, 10, 20)
    assert cal.date_of(28) == date(2026, 11, 16)  # a Monday, US time


def test_matchup_days_run_monday_to_sunday_after_the_last_scored_matchup(fake_league):
    cal = calendar(fake_league)
    assert cal.matchup_days(4) == list(range(21, 28))
    assert cal.matchup_days(5) == list(range(28, 35))
    assert cal.matchup_days(9) == []  # too far ahead to work out


def test_two_week_matchups_run_to_the_second_sunday(fake_league):
    weeks = {"5": [5, 6]}
    cal = Calendar(fake_league.pro_schedule, 1, 167, fake_league.matchup_ids, weeks)
    assert cal.matchup_days(5) == list(range(28, 42))


def test_outlook_moves_on_once_the_current_matchup_has_no_games_left(fake_league, now):
    cal = calendar(fake_league)
    assert outlook_matchup(cal, 4, now) == (5, list(range(28, 35)))
    earlier = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    assert outlook_matchup(cal, 4, earlier) == (4, list(range(21, 28)))


def test_games_carry_tip_off_and_opponent(fake_league):
    game = calendar(fake_league).game("MEM", 29)
    assert game.tip == datetime(2026, 11, 18, 0, 0, tzinfo=timezone.utc)
    assert (game.opponent, game.home) == ("CHA", True)
