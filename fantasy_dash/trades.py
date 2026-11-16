"""Trade ideas: swaps with other teams that raise your projected points for
the rest of the season while costing the other team little or nothing.

Each roster is projected day by day to the end of the fantasy season: every
day the best lineup is picked from the players with a game, and only
starters score. Candidate trades are screened with a quick version that
ignores positions, and the most promising are re-checked with the full
lineup rules. After a trade, a team left with an open roster spot is
assumed to add the best healthy free agent, and a team with one player too
many drops its weakest.

The other team's projection matters as much as yours: an offer that leaves
them clearly worse off is unlikely to be accepted, so those are left out.
"""

from collections import defaultdict
from datetime import date
from itertools import combinations

from .nba_report import ET
from .projection import best_lineup, per_game_value, play_chance, value_on

MY_MIN_GAIN = 100        # projected points over the rest of the season
THEIR_MAX_LOSS = 0.005   # the most of their projection they'd give up
VALUE_RANGE = (0.6, 1.6)  # value per game received / given, outside which an offer looks lopsided
# In a two-for-one, the best player on the side giving two must be worth at
# least this share of the one coming back. Few managers swap a star for two
# lesser players, however the projections add up.
STAR_SHARE = 0.65
SCREEN_KEEP = 80         # candidates re-checked with full lineup rules
SUGGESTIONS = 5
PER_PARTNER = 1
PER_PLAYER = 2           # times one of your players can appear in the list
REPLACEMENTS = 3         # free agents available to fill open roster spots
FORM_GAP = 0.15          # recent form this far from the season average is worth a mention


def _value(row):
    return per_game_value(row)


def _brief(row):
    keys = ("player", "player_id", "nba_team", "positions", "slot", "status", "injury",
            "expected_return", "out_for_season", "last_15_avg", "season_avg")
    return {**{key: row.get(key) for key in keys}, "value": round(_value(row), 1)}


class SeasonModel:
    """Projects rosters from the next game to the end of the fantasy season."""

    def __init__(self, calendar, now, last_period, slots):
        self.calendar = calendar
        self.now = now
        self.today = now.astimezone(ET).date()
        self.last_period = last_period
        self.slots = slots
        self._daily = {}
        self._days = sorted({p for games in calendar.games.values() for p, g in games.items()
                             if p <= last_period and g.tip > now})

    def daily(self, row):
        """{period: expected points if started} for each of the player's remaining games."""
        key = (row["player_id"], row.get("status"))
        if key not in self._daily:
            games = {}
            if _value(row) > 0:
                for period, game in self.calendar.games.get(row["nba_team"], {}).items():
                    if period <= self.last_period and game.tip > self.now:
                        day = self.calendar.date_of(period)
                        chance = play_chance(row, day, self.today)
                        if chance:
                            games[period] = value_on(row, day) * chance
            self._daily[key] = games
        return self._daily[key]

    def days(self):
        return self._days

    def quick(self, rows):
        """Projected points, starting each day's best players regardless of position."""
        per_day = defaultdict(list)
        for row in rows:
            if row["slot"] != "IR":
                for period, value in self.daily(row).items():
                    per_day[period].append(value)
        size = len(self.slots)
        return sum(sum(sorted(values, reverse=True)[:size]) for values in per_day.values())

    def full(self, rows):
        """Projected points with the league's lineup slots, and lineup slots left empty."""
        per_day = defaultdict(list)
        for row in rows:
            if row["slot"] != "IR":
                for period, value in self.daily(row).items():
                    per_day[period].append((row["player_id"], value, row.get("eligible_slots") or []))
        points, empty = 0.0, 0
        for period in self.days():
            candidates = per_day.get(period, [])
            starters = best_lineup(candidates, self.slots)
            points += sum(value for key, value, _ in candidates if key in starters)
            empty += len(self.slots) - len(starters)
        return points, empty


def settle(rows, max_active, ir_slots, replacements, keep=()):
    """Make a roster legal after a trade. Returns (rows, dropped, added)."""
    active = [r for r in rows if r["slot"] != "IR"]
    on_ir = [r for r in rows if r["slot"] == "IR"]
    dropped, added = [], []
    while len(active) > max_active and len(on_ir) < ir_slots:
        hurt = [r for r in active if r["espn_status"] == "Out" and r["player_id"] not in keep]
        if not hurt:
            break
        move = max(hurt, key=_value)
        active.remove(move)
        on_ir.append({**move, "slot": "IR"})
    while len(active) > max_active:
        pool = [r for r in active if r["player_id"] not in keep] or active
        weakest = min(pool, key=_value)
        active.remove(weakest)
        dropped.append(weakest)
    taken = {r["player_id"] for r in active + on_ir}
    for free_agent in replacements:
        if len(active) >= max_active:
            break
        if free_agent["player_id"] not in taken:
            active.append({**free_agent, "slot": "Bench"})
            added.append(free_agent)
    return active + on_ir, dropped, added


def _candidates(mine, theirs, starter_bar):
    """(give, get) pairs worth testing: one for one, two for one and one for two."""
    targets = [r for r in theirs if _value(r) >= starter_bar]
    pairs = []
    for give in mine:
        for get in targets:
            pairs.append(((give,), (get,)))
    for get in targets:
        for a, b in combinations(mine, 2):
            if max(_value(a), _value(b)) < _value(get) and max(_value(a), _value(b)) >= STAR_SHARE * _value(get):
                pairs.append(((a, b), (get,)))
    for give in mine:
        for a, b in combinations(theirs, 2):
            best, worst = max(_value(a), _value(b)), min(_value(a), _value(b))
            if best < _value(give) and best >= STAR_SHARE * _value(give) and worst >= starter_bar * 0.8:
                pairs.append(((give,), (a, b)))
    plausible = []
    for give, get in pairs:
        given = sum(_value(r) for r in give) or 0.1
        if VALUE_RANGE[0] <= sum(_value(r) for r in get) / given <= VALUE_RANGE[1]:
            plausible.append((give, get))
    return plausible


def _swap(rows, out_ids, incoming):
    return [r for r in rows if r["player_id"] not in out_ids] + [{**r, "slot": "Bench"} for r in incoming]


def _day(iso):
    day = date.fromisoformat(iso)
    return f"{day:%a} {day.day} {day:%b}"


def _reasons(trade, partner_name):
    reasons = []
    give, get = trade["give"], trade["get"]
    if len(give) == 2:
        added = trade["you_add"]
        reasons.append("Turns two of your players into one stronger one" +
                       (f", and frees a roster spot for {added['player']} ({added['value']:.1f} a game)." if added else "."))
    if len(get) == 2:
        dropped = trade["you_drop"]
        reasons.append("Adds depth for busy days" +
                       (f"; to make room you'd drop {dropped['player']} ({dropped['value']:.1f} a game)." if dropped else "."))
    if trade["they_drop"]:
        reasons.append(f"{partner_name} would need to drop {trade['they_drop']['player']}.")
    if trade["they_add"]:
        reasons.append(f"{partner_name} gets a roster spot back to fill.")
    if trade["their_extra_starts"] >= 10:
        reasons.append(f"{partner_name} would fill about {trade['their_extra_starts']} more lineup spots over the "
                       "rest of the season, which is where their projected gain comes from.")
    for player in give + get:
        if player["status"] == "Out":
            if player.get("out_for_season"):
                reasons.append(f"{player['player']} is out for the season.")
            else:
                back = f", expected back {_day(player['expected_return'])}" if player.get("expected_return") else ", with no return date yet"
                reasons.append(f"{player['player']} is out{back}; the projection counts them from then.")
    for player in get:
        recent, season = player.get("last_15_avg"), player.get("season_avg")
        if isinstance(recent, (int, float)) and isinstance(season, (int, float)) and recent < season * (1 - FORM_GAP):
            reasons.append(f"{player['player']}'s last 15 days ({recent:.1f}) trail their season average "
                           f"({season:.1f}), so they may cost less right now.")
    for player in give:
        recent, season = player.get("last_15_avg"), player.get("season_avg")
        if isinstance(recent, (int, float)) and isinstance(season, (int, float)) and recent > season * (1 + FORM_GAP):
            reasons.append(f"{player['player']} is playing above their season average ({recent:.1f} v "
                           f"{season:.1f}), a good time to sell.")
    return reasons


def suggest_trades(rows, my_team_id, teams, free_agents, calendar, now, slots, slot_counts,
                   last_period, deadline=None):
    """Ranked trade ideas with every other team.

    rows: every rostered player (from espn_source.roster_rows, reconciled).
    teams: {team id: {"id", "name", "owner"}}.
    deadline: the league's trade deadline as an aware datetime, if any.
    """
    model = SeasonModel(calendar, now, last_period, slots)
    days = model.days()
    result = {
        "horizon": {
            "first_day": calendar.date_of(days[0]).isoformat() if days else None,
            "last_day": calendar.date_of(days[-1]).isoformat() if days else None,
            "days": len(days),
        },
        "deadline": deadline.isoformat(timespec="minutes") if deadline else None,
        "deadline_passed": bool(deadline and deadline <= now),
        "suggestions": [],
    }
    if result["deadline_passed"] or not days:
        return result

    counts = {int(k): int(v) for k, v in (slot_counts or {}).items()}
    max_active = len(slots) + counts.get(12, 0)
    ir_slots = counts.get(13, 0)
    today = now.astimezone(ET).date()
    replacements = sorted((fa for fa in free_agents if play_chance(fa, today, today) > 0),
                          key=_value, reverse=True)[:REPLACEMENTS]

    by_team = defaultdict(list)
    for row in rows:
        by_team[row["team_id"]].append(row)
    mine = by_team.get(my_team_id, [])
    if not mine:
        return result
    my_base_rows = settle(mine, max_active, ir_slots, replacements)[0]
    my_quick = model.quick(my_base_rows)
    my_full, _ = model.full(my_base_rows)
    active_values = sorted((_value(r) for r in mine if r["slot"] != "IR"), reverse=True)
    starter_bar = active_values[min(len(slots), len(active_values)) - 1] if active_values else 0

    screened = []
    bases = {}
    for team_id, theirs in by_team.items():
        if team_id == my_team_id:
            continue
        their_base_rows = settle(theirs, max_active, ir_slots, replacements)[0]
        their_quick = model.quick(their_base_rows)
        bases[team_id] = (their_base_rows, their_quick)
        for give, get in _candidates(mine, theirs, starter_bar):
            give_ids = {r["player_id"] for r in give}
            get_ids = {r["player_id"] for r in get}
            my_rows = settle(_swap(mine, give_ids, get), max_active, ir_slots, replacements, keep=get_ids)[0]
            their_rows = settle(_swap(theirs, get_ids, give), max_active, ir_slots, replacements, keep=give_ids)[0]
            my_gain = model.quick(my_rows) - my_quick
            their_gain = model.quick(their_rows) - their_quick
            if my_gain > MY_MIN_GAIN * 0.5 and their_gain > -THEIR_MAX_LOSS * 2 * their_quick:
                screened.append((my_gain + 0.5 * their_gain, team_id, give, get))
    screened.sort(key=lambda item: -item[0])

    checked = []
    full_bases = {}
    for _, team_id, give, get in screened[:SCREEN_KEEP]:
        theirs = by_team[team_id]
        if team_id not in full_bases:
            full_bases[team_id] = model.full(bases[team_id][0])
        their_full, their_empty = full_bases[team_id]
        give_ids = {r["player_id"] for r in give}
        get_ids = {r["player_id"] for r in get}
        my_rows, my_dropped, my_added = settle(_swap(mine, give_ids, get), max_active, ir_slots, replacements, keep=get_ids)
        their_rows, their_dropped, their_added = settle(_swap(theirs, get_ids, give), max_active, ir_slots, replacements,
                                                        keep=give_ids)
        my_gain = model.full(my_rows)[0] - my_full
        their_after, their_empty_after = model.full(their_rows)
        their_gain = their_after - their_full
        if my_gain < MY_MIN_GAIN or their_gain < -THEIR_MAX_LOSS * their_full:
            continue
        checked.append({
            "partner": teams.get(team_id, {"id": team_id, "name": f"Team {team_id}", "owner": ""}),
            "give": [_brief(r) for r in give],
            "get": [_brief(r) for r in get],
            "kind": "both_gain" if their_gain >= 0 else "close",
            "my_gain": round(my_gain),
            "their_gain": round(their_gain),
            "my_gain_pct": round(100 * my_gain / my_full, 1) if my_full else None,
            "their_gain_pct": round(100 * their_gain / their_full, 1) if their_full else None,
            "value_given": round(sum(_value(r) for r in give), 1),
            "value_received": round(sum(_value(r) for r in get), 1),
            "you_add": _brief(my_added[0]) if my_added else None,
            "you_drop": _brief(my_dropped[0]) if my_dropped else None,
            "they_add": _brief(their_added[0]) if their_added else None,
            "they_drop": _brief(their_dropped[0]) if their_dropped else None,
            "their_extra_starts": their_empty - their_empty_after,
            "score": my_gain + 0.5 * their_gain,
        })

    checked.sort(key=lambda t: -t["score"])
    chosen, per_partner, per_player = [], defaultdict(int), defaultdict(int)
    for trade in checked:
        partner = trade["partner"]["id"]
        mine_out = [p["player_id"] for p in trade["give"]]
        if per_partner[partner] >= PER_PARTNER or any(per_player[p] >= PER_PLAYER for p in mine_out):
            continue
        per_partner[partner] += 1
        for p in mine_out:
            per_player[p] += 1
        trade["reasons"] = _reasons(trade, trade["partner"]["name"])
        del trade["score"]
        chosen.append(trade)
        if len(chosen) == SUGGESTIONS:
            break
    result["suggestions"] = chosen
    return result
