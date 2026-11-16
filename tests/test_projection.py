import itertools
import random
from datetime import date

from fantasy_dash.projection import best_lineup, per_game_value, play_chance, starting_slots

SLOTS = ["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL", "UTIL", "UTIL"]
ELIGIBLE = {
    "PG": ["PG", "G", "UTIL"], "SG": ["SG", "G", "UTIL"], "SF": ["SF", "F", "UTIL"],
    "PF": ["PF", "F", "UTIL"], "C": ["C", "UTIL"], "PG/SG": ["PG", "SG", "G", "UTIL"],
    "SF/PF": ["SF", "PF", "F", "UTIL"], "PF/C": ["PF", "C", "F", "UTIL"],
}


def brute_force(candidates, slots):
    """Best total by trying every way of seating players. Slow, but obviously right."""
    best = 0.0
    for size in range(len(candidates) + 1):
        for group in itertools.combinations(candidates, size):
            for seats in itertools.permutations(range(len(slots)), size):
                if all(slots[s] in c[2] for c, s in zip(group, seats)):
                    best = max(best, sum(c[1] for c in group))
                    break
    return best


def test_greedy_lineup_matches_brute_force():
    rng = random.Random(7)
    slots = ["PG", "SG", "G", "C", "UTIL"]
    for _ in range(60):
        candidates = [(i, round(rng.uniform(5, 50), 1), ELIGIBLE[rng.choice(list(ELIGIBLE))])
                      for i in range(rng.randint(1, 7))]
        chosen = best_lineup(candidates, slots)
        total = sum(c[1] for c in candidates if c[0] in chosen)
        assert abs(total - brute_force(candidates, slots)) < 1e-9


def test_lineup_moves_players_to_fit_a_better_one():
    # The PG/SG starts at PG first, then has to move to SG so the pure PG fits.
    candidates = [("combo", 40, ELIGIBLE["PG/SG"]), ("pg", 30, ELIGIBLE["PG"]), ("pg2", 20, ["PG"])]
    assert best_lineup(candidates, ["PG", "SG"]) == {"combo", "pg"}


def test_starting_slots_from_espn_counts():
    counts = {"0": 1, "1": 1, "2": 1, "3": 1, "4": 1, "5": 1, "6": 1, "11": 3, "12": 3, "13": 1}
    assert starting_slots(counts) == SLOTS


def test_value_leans_on_recent_form_but_not_on_one_game():
    steady = {"last_15_avg": 30.0, "last_15_games": 6, "season_avg": 20.0, "season_games": 12, "projected_avg": 20.0}
    assert round(per_game_value(steady), 1) == 25.0  # 30*0.5 + 20*0.3 + 20*0.2
    one_game = dict(steady, last_15_avg=4.0, last_15_games=1)
    assert per_game_value(one_game) > 15  # a single bad game barely moves it


def test_play_chance_follows_status_and_return_date():
    out = {"status": "Out", "expected_return": "2026-11-19"}
    assert play_chance(out, date(2026, 11, 18)) == 0
    assert play_chance(out, date(2026, 11, 19)) == 0.5
    assert play_chance({"status": "Questionable"}, date(2026, 11, 18)) == 0.5
    assert play_chance({"status": "Unconfirmed", "espn_status": "Out"}, date(2026, 11, 18)) == 0
    assert play_chance({"status": "Clear"}, date(2026, 11, 18)) == 1.0
