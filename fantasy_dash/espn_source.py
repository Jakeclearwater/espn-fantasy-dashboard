"""Pull league data through espn-api and flatten it into plain dicts.

espn-api is unofficial and ESPN changes its private API without notice, so
every call is wrapped to turn library and HTTP failures into an EspnError
whose message says what went wrong and what to try.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import requests
from espn_api.basketball import League
from urllib3.exceptions import NameResolutionError
from espn_api.basketball.player import Player
from espn_api.requests.espn_requests import (
    ESPNAccessDenied,
    ESPNInvalidLeague,
    ESPNUnknownError,
)

from .injuries import espn_status

SLOT_NAMES = {"UT": "UTIL", "BE": "Bench"}
SLOT_ORDER = ("PG", "SG", "SF", "PF", "C", "G", "F", "UTIL", "Bench", "IR")
POSITIONS = ("PG", "SG", "SF", "PF", "C")
# espn-api uses older codes for two teams; show the ones the NBA uses.
TEAM_CODES = {"PHL": "PHI", "PHO": "PHX", "FA": ""}
# For trades ESPN names the team that sent the player away.
ACTIVITY_TYPES = {"FA ADDED": "Add", "WAIVER ADDED": "Waiver claim", "DROPPED": "Drop", "TRADED": "Traded away"}
# Slots a player can be started in, as named in the dashboard.
NOT_STARTING = {"Bench", "IR", "", "Rookie"}
SPLITS = (("season_avg", "total"), ("last_30_avg", "last_30"), ("last_15_avg", "last_15"),
          ("last_7_avg", "last_7"), ("projected_avg", "projected"))

# ESPN's default points scoring, used to rank players in category leagues,
# where ESPN doesn't compute a single fantasy average.
STANDARD_POINTS = {
    "PTS": 1, "3PM": 1, "FGA": -1, "FGM": 2, "FTA": -1, "FTM": 1,
    "REB": 1, "AST": 2, "STL": 4, "BLK": 4, "TO": -2,
}


class EspnError(Exception):
    """A failure talking to ESPN. The message is safe to print."""


@contextmanager
def espn_errors(action, league_id=None, year=None):
    try:
        yield
    except ESPNAccessDenied as exc:
        raise EspnError(
            f"ESPN refused access while {action}. Your espn_s2 or SWID cookie is missing, "
            "expired or from a different account. Sign in at espn.com, copy both cookies "
            "again into fantasy_dash/local_secrets.py (or the ESPN_S2 and SWID secrets on GitHub) and rerun."
        ) from exc
    except ESPNInvalidLeague as exc:
        raise EspnError(
            f"ESPN says league {league_id} doesn't exist for the {year} season. "
            "Check LEAGUE_ID and YEAR in fantasy_dash/config.py (2027 means the 2026-27 season)."
        ) from exc
    except ESPNUnknownError as exc:
        raise EspnError(
            f"ESPN returned an error while {action} ({exc}). ESPN may be down or "
            "rate limiting; try again in a few minutes."
        ) from exc
    except requests.exceptions.SSLError as exc:
        raise EspnError(
            f"The HTTPS certificate check failed while {action}. On a network that inspects "
            "HTTPS (common at work), run `pip install -r requirements.txt` so the tool can use "
            "your computer's trusted certificates."
        ) from exc
    except requests.ConnectionError as exc:
        if _is_dns_failure(exc):
            raise EspnError(
                f"Couldn't look up ESPN's server address while {action}. This is a DNS "
                "problem on the network, usually brief. Wait a few minutes, or clear the "
                "Mac's DNS cache with `sudo dscacheutil -flushcache; sudo killall -HUP "
                "mDNSResponder`, then rerun."
            ) from exc
        raise EspnError(
            f"Couldn't reach ESPN while {action} ({type(exc).__name__}). "
            "Check your internet connection."
        ) from exc
    except requests.RequestException as exc:
        raise EspnError(
            f"Couldn't reach ESPN while {action} ({type(exc).__name__}). "
            "Check your internet connection."
        ) from exc
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
        raise EspnError(
            f"ESPN's data didn't match what espn-api expects while {action} "
            f"({type(exc).__name__}: {exc}). The unofficial API has probably changed. "
            "Try `pip install -U espn-api`, or check "
            "https://github.com/cwendt94/espn-api/issues for reports."
        ) from exc


def _is_dns_failure(exc):
    reason = getattr(exc.args[0], "reason", None) if exc.args else None
    return isinstance(reason, NameResolutionError)


def connect(league_id, year, credentials):
    with espn_errors("loading the league", league_id, year):
        return League(league_id=league_id, year=year,
                      espn_s2=credentials.espn_s2, swid=credentials.swid)


def _squash(text):
    text = text.replace("’", "'").replace("‘", "'")
    return re.sub(r"[^a-z0-9]", "", text.lower())


def team_label(team):
    """The team's name without the stray spaces ESPN keeps from what owners typed."""
    return (getattr(team, "team_name", "") or "").strip()


def find_team(league, name):
    wanted = _squash(name)
    for team in league.teams:
        if _squash(team.team_name) == wanted:
            return team
    partial = [t for t in league.teams if wanted and wanted in _squash(t.team_name)]
    if len(partial) == 1:
        return partial[0]
    names = ", ".join(f'"{team_label(t)}"' for t in league.teams)
    raise EspnError(f'No team called "{name}" in this league. Teams are: {names}. Use --team.')


def owner_name(team):
    for member in team.owners or []:
        full = f"{member.get('firstName', '')} {member.get('lastName', '')}".strip()
        return full or member.get("displayName", "")
    return ""


def find_opponent(league, team, matchup_period=None):
    """The opponent in a matchup period (default: the current one), or None on a bye."""
    matchup_period = matchup_period or league.currentMatchupPeriod
    try:
        for matchup in league.scoreboard(matchup_period):
            home, away = matchup.home_team, matchup.away_team
            if getattr(home, "team_id", None) == team.team_id:
                return away if hasattr(away, "team_id") else None
            if getattr(away, "team_id", None) == team.team_id:
                return home if hasattr(home, "team_id") else None
    except Exception:  # fall back to the schedule already loaded
        pass
    index = matchup_period - 1
    if 0 <= index < len(team.schedule):
        matchup = team.schedule[index]
        other = matchup.away_team if matchup.home_team is team else matchup.home_team
        return other if hasattr(other, "team_id") else None
    return None


def team_code(pro_team):
    return TEAM_CODES.get(pro_team, pro_team or "")


def positions(eligible_slots):
    return "/".join(p for p in POSITIONS if p in (eligible_slots or []))


def slot_name(lineup_slot):
    return SLOT_NAMES.get(lineup_slot, lineup_slot or "Bench")


def _blank_injury_fields():
    return {"status": "", "injury": "", "latest": "", "as_of": None, "source": ""}


def starting_slots_for(player):
    """Lineup slots the player can start in, e.g. ['PG', 'SG', 'G', 'UTIL']."""
    return [name for name in (slot_name(s) for s in player.eligibleSlots or []) if name not in NOT_STARTING]


def _form_fields(player, year, points_league):
    """Per-game averages for each split, plus what projections need."""
    fields = {key: split_average(player, year, split, points_league) for key, split in SPLITS}
    for key, split in (("season_games", "total"), ("last_15_games", "last_15")):
        fields[key] = int(((player.stats.get(f"{year}_{split}") or {}).get("total") or {}).get("GP") or 0)
    # Minutes and shot attempts a game, used to share out an injured teammate's role.
    totals = (player.stats.get(f"{year}_total") or {}).get("total") or {}
    games = totals.get("GP") or 0
    fields["minutes"] = round(totals.get("MIN", 0) / games, 1) if games else None
    fields["shots"] = round(totals.get("FGA", 0) / games, 1) if games else None
    fields["eligible_slots"] = starting_slots_for(player)
    fields["expected_return"] = player.expected_return_date.isoformat() if player.expected_return_date else None
    return fields


def roster_rows(league):
    """One row per rostered player, each team in lineup order."""
    rows = []
    points_league = is_points_league(league)
    with espn_errors("reading rosters"):
        for team in league.teams:
            team_rows = []
            for player in team.roster:
                row = {
                    "team": team_label(team),
                    "slot": slot_name(player.lineupSlot),
                    "player": player.name,
                    "nba_team": team_code(player.proTeam),
                    "positions": positions(player.eligibleSlots),
                    **_blank_injury_fields(),
                    "team_id": team.team_id,
                    "position": player.position,
                    "player_id": player.playerId,
                    "espn_status": espn_status(player.injuryStatus),
                    **_form_fields(player, league.year, points_league),
                }
                team_rows.append(row)
            team_rows.sort(key=lambda r: SLOT_ORDER.index(r["slot"]) if r["slot"] in SLOT_ORDER else len(SLOT_ORDER))
            rows.extend(team_rows)
    return rows


def is_points_league(league):
    return "POINTS" in (league.settings.scoring_type or "").upper()


def split_average(player, year, split, points_league):
    """Fantasy points per game for a stat split ('total', 'last_7', 'last_15'),
    or None if the player had no games in it.

    Worked out as total over games played rather than read from ESPN's own
    average: ESPN sometimes sends a split with games and points but an average
    of 0, and espn-api also fills in 0 for a window with no games at all.
    """
    stats = player.stats.get(f"{year}_{split}") or {}
    totals = stats.get("total") or {}
    games = totals.get("GP") or 0
    if not games:
        return None
    if points_league:
        points = stats.get("applied_total") or 0
    else:
        points = sum(totals.get(stat, 0) * weight for stat, weight in STANDARD_POINTS.items())
    return round(points / games, 1)


def free_agent_pool(league, top, pool_size=250):
    """Raw free-agent entries, including players on waivers.

    Asks ESPN for its best free agents by last-15-day average, plus the most
    rostered ones, and merges the two; the final order is set locally, so the
    list still holds up if ESPN ever ignores the sort. Uses espn-api's request
    layer directly because League.free_agents() drops the ownership and
    waiver fields this export needs.
    """
    params = {"view": "kona_player_info", "scoringPeriodId": league.current_week}
    sorts = (
        (top * 2, {"sortAppliedStatAverage": {"sortPriority": 1, "sortAsc": False, "value": f"02{league.year}"}}),
        (pool_size, {"sortPercOwned": {"sortPriority": 1, "sortAsc": False}}),
    )
    entries = {}
    with espn_errors("loading free agents"):
        for limit, sort in sorts:
            filters = {"players": {"filterStatus": {"value": ["FREEAGENT", "WAIVERS"]}, "limit": limit, **sort}}
            data = league.espn_request.league_get(
                params=params, headers={"x-fantasy-filter": json.dumps(filters)})
            for entry in data.get("players", []):
                entries.setdefault(entry.get("id"), entry)
    return list(entries.values())


def free_agent_rows(league, entries, top):
    points_league = is_points_league(league)
    rows = []
    with espn_errors("reading free agents"):
        for entry in entries:
            player = Player(entry, league.year)
            ownership = (entry.get("player") or {}).get("ownership") or {}
            percent = ownership.get("percentOwned")
            form = _form_fields(player, league.year, points_league)
            clears = entry.get("waiverProcessDate")
            rows.append({
                "player": player.name,
                "nba_team": team_code(player.proTeam),
                "positions": positions(player.eligibleSlots),
                "season_avg": form["season_avg"],
                "last_7_avg": form["last_7_avg"],
                "last_15_avg": form["last_15_avg"],
                "percent_rostered": round(percent, 1) if isinstance(percent, (int, float)) else None,
                "availability": "Waivers" if entry.get("status") == "WAIVERS" else "Free agent",
                **_blank_injury_fields(),
                "player_id": player.playerId,
                "espn_status": espn_status(player.injuryStatus),
                "last_30_avg": form["last_30_avg"],
                "projected_avg": form["projected_avg"],
                "season_games": form["season_games"],
                "last_15_games": form["last_15_games"],
                "minutes": form["minutes"],
                "shots": form["shots"],
                "eligible_slots": form["eligible_slots"],
                "expected_return": form["expected_return"],
                "position": player.position,
                "waiver_clears": (
                    datetime.fromtimestamp(clears / 1000, tz=timezone.utc).isoformat(timespec="minutes")
                    if entry.get("status") == "WAIVERS" and isinstance(clears, (int, float)) else None
                ),
            })
    rows.sort(key=lambda r: (r["last_15_avg"] is None, -(r["last_15_avg"] or 0)))
    return rows[:top]


def recent_transactions(league, days, now, page_size=25, max_pages=40):
    """Adds, drops, trades and waiver claims across the league in the last `days`.

    ESPN returns activity newest first and may cap each page, so keep paging
    until a page reaches past the start of the window or comes back empty.
    """
    since_ms = (now - timedelta(days=days)).timestamp() * 1000
    rows = []
    group = 0
    offset = 0
    with espn_errors("loading league activity"):
        for _ in range(max_pages):
            batch = league.recent_activity(size=page_size, offset=offset)
            if not batch:
                break
            offset += len(batch)
            for activity in batch:
                if activity.date < since_ms:
                    continue
                group += 1
                when = datetime.fromtimestamp(activity.date / 1000, tz=timezone.utc)
                for team, action, player, _position in activity.actions:
                    if action not in ACTIVITY_TYPES:
                        continue
                    rows.append({
                        "date": when.isoformat(timespec="minutes"),
                        "type": ACTIVITY_TYPES[action],
                        "team": team_label(team),
                        "player": player,
                        "group": group,
                    })
            if min(a.date for a in batch) < since_ms:
                break
    rows.sort(key=lambda r: (r["date"], -r["group"]), reverse=True)
    return rows


def league_extras(league):
    """League settings and per-team counters that espn-api's objects leave out:
    lineup slots, matchup weeks, add limits, waiver ranks and adds used.
    Returns (settings, teams by id); both empty if ESPN won't say."""
    with espn_errors("loading league settings"):
        raw = league.espn_request.league_get(params={"view": ["mSettings", "mTeam"]})
        return raw.get("settings") or {}, {t["id"]: t for t in raw.get("teams", [])}


def add_limit(settings, raw_team, matchup_period):
    """The league's limit on adds, and how many this team has used this matchup."""
    acquisition = settings.get("acquisitionSettings") or {}
    limit = acquisition.get("matchupAcquisitionLimit")
    used = ((raw_team or {}).get("transactionCounter") or {}).get("matchupAcquisitionTotals", {})
    return {
        "limit": int(limit) if isinstance(limit, (int, float)) and limit > 0 else None,
        "per_day": bool(acquisition.get("matchupLimitPerScoringPeriod")),
        "used_this_matchup": used.get(str(matchup_period), 0),
    }


def matchup_scores(league, team):
    """(team score, opponent score) in the current matchup, from ESPN's live box score."""
    with espn_errors("loading the matchup score"):
        for box in league.box_scores():
            if getattr(box.home_team, "team_id", None) == team.team_id:
                return box.home_score, box.away_score
            if getattr(box.away_team, "team_id", None) == team.team_id:
                return box.away_score, box.home_score
    return None


def matchup_pairs(league, matchup_period):
    """{team id: opponent's team id, or None on a bye} for a matchup period."""
    pairs = {t.team_id: None for t in league.teams}
    try:
        for matchup in league.scoreboard(matchup_period):
            home, away = getattr(matchup.home_team, "team_id", None), getattr(matchup.away_team, "team_id", None)
            if home is not None:
                pairs[home] = away
            if away is not None:
                pairs[away] = home
    except Exception:  # fall back to each team's own schedule
        for team in league.teams:
            other = find_opponent(league, team, matchup_period)
            pairs[team.team_id] = other.team_id if other else None
    return pairs


def live_scores(league):
    """{team id: score} in the current matchup, from ESPN's box scores."""
    scores = {}
    with espn_errors("loading matchup scores"):
        for box in league.box_scores():
            for side in ("home", "away"):
                team = getattr(box, f"{side}_team")
                if hasattr(team, "team_id"):
                    scores[team.team_id] = getattr(box, f"{side}_score")
    return scores


def player_cards(league, player_ids, chunk=50):
    """ESPN's full player cards, with game logs, injury details and ownership.
    Returns {player_id: (Player, raw card entry)}."""
    cards = {}
    ids = sorted(set(player_ids))
    extra = [f"{prefix}{league.year}" for prefix in ("01", "02", "03")]
    with espn_errors("loading player cards"):
        for start in range(0, len(ids), chunk):
            data = league.espn_request.get_player_card(ids[start:start + chunk], league.finalScoringPeriod, extra)
            for entry in data.get("players", []):
                player = Player(entry, league.year, league.pro_schedule)
                cards[player.playerId] = (player, entry)
    return cards


def waiver_info(league, team, pool_entries, now, raw_teams):
    """Waiver order, FAAB and the next processing time."""
    ranks = {team_id: raw.get("waiverRank") for team_id, raw in raw_teams.items()}

    names = {t.team_id: team_label(t) for t in league.teams}
    order = sorted(
        ({"rank": rank, "team": names.get(team_id, f"Team {team_id}")}
         for team_id, rank in ranks.items() if isinstance(rank, int) and rank > 0),
        key=lambda r: r["rank"],
    )

    settings = league.settings
    faab_budget = settings.acquisition_budget if settings.faab else None
    faab_spent = team.acquisition_budget_spent if settings.faab else None

    # A player on waivers carries the time their claim window closes, which is
    # the league's next waiver run. ESPN doesn't publish the run time anywhere else.
    now_ms = now.timestamp() * 1000
    upcoming = sorted(
        e["waiverProcessDate"] for e in pool_entries
        if e.get("status") == "WAIVERS" and isinstance(e.get("waiverProcessDate"), (int, float))
        and e["waiverProcessDate"] > now_ms
    )
    next_processing = (
        datetime.fromtimestamp(upcoming[0] / 1000, tz=timezone.utc).isoformat(timespec="minutes")
        if upcoming else None
    )

    info = {
        "team": team_label(team),
        "waiver_priority": ranks.get(team.team_id),
        "team_count": len(league.teams),
        "uses_faab": bool(settings.faab),
        "faab_budget": faab_budget,
        "faab_spent": faab_spent,
        "faab_remaining": (faab_budget - faab_spent) if settings.faab else None,
        "next_processing": next_processing,
        "next_processing_note": (
            "From the claim deadline on players currently on waivers."
            if next_processing else
            "ESPN doesn't expose the next run when no players are on waivers."
        ),
        "waiver_order": order,
    }
    return info


def _excerpt(text, limit=320):
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "..."


def player_notes(league, player_ids, keep=3, workers=16):
    """Recent Rotowire notes ESPN carries for each player, newest first:
    {player_id: [{headline, story, published}, ...]}.

    Failures are skipped quietly; a missing note just means no 'latest' line.
    """
    def fetch(player_id):
        try:
            data = league.espn_request.get_player_news(player_id) or {}
        except Exception:
            return player_id, []
        feed = (data.get("news") or {}).get("feed") or data.get("feed") or []
        notes = [item for item in feed if item.get("type") == "Rotowire" and item.get("headline")]
        notes.sort(key=lambda item: item.get("published", ""), reverse=True)
        return player_id, [
            {"headline": item["headline"].strip(), "story": _excerpt(item.get("story")),
             "published": item.get("published")}
            for item in notes[:keep]
        ]

    ids = sorted(set(player_ids))
    if not ids:
        return {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return {pid: notes for pid, notes in pool.map(fetch, ids) if notes}
