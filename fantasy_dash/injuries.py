"""Reconcile ESPN injury flags with the NBA's official injury report.

Rules, applied to every player row:
- On a recent NBA report for their team's game: take the report's status.
- Team's game is on a recent report but the player isn't listed: Clear.
- No recent report covers the team: keep Clear if ESPN shows nothing, but a
  player ESPN flags becomes Unconfirmed rather than trusting a flag that
  may be stale.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .nba_report import ET

STATUSES = ("Out", "Questionable", "Day-to-day", "Clear", "Unconfirmed")
FLAGGED = {"Out", "Questionable", "Day-to-day"}

ESPN_STATUS = {
    "OUT": "Out",
    "INJURY_RESERVE": "Out",
    "SUSPENSION": "Out",
    "DOUBTFUL": "Questionable",
    "QUESTIONABLE": "Questionable",
    "GAME_TIME_DECISION": "Questionable",
    "DAY_TO_DAY": "Day-to-day",
    "PROBABLE": "Day-to-day",
}

# The report also uses Doubtful and Probable; they fold into the dashboard's
# five statuses, and the original wording is kept in the row's note.
NBA_STATUS = {
    "Out": "Out",
    "Doubtful": "Questionable",
    "Questionable": "Questionable",
    "Probable": "Day-to-day",
    "Available": "Clear",
}

SOURCE_NBA = "NBA official injury report"
SOURCE_ESPN = "ESPN"
SOURCE_ESPN_ONLY = "ESPN flag only, no recent NBA report"

NOTE_MAX_AGE = timedelta(days=7)
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def espn_status(raw):
    if isinstance(raw, str):
        return ESPN_STATUS.get(raw.upper(), "Clear")
    return "Clear"


def _name_tokens(name):
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    tokens = [t for t in re.split(r"[^a-z]+", plain.replace("'", "")) if t]
    while len(tokens) > 1 and tokens[-1] in NAME_SUFFIXES:
        tokens.pop()
    return tokens


def name_key(name):
    return "".join(_name_tokens(name))


def last_name_key(name):
    tokens = _name_tokens(name)
    return tokens[-1] if tokens else ""


def clean_reason(reason):
    return re.sub(r"^Injury/Illness\s*-\s*", "", reason).strip()


def tip_off(game_date, game_time):
    """Tip-off as an aware datetime. The report prints '07:00 (ET)' with no am/pm;
    NBA games start between noon and late evening, so hours 1 to 11 are pm."""
    match = re.match(r"(\d{1,2}):(\d{2})", game_time or "")
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour != 12:
        hour += 12
    if hour > 23:
        return None
    return datetime(game_date.year, game_date.month, game_date.day, hour, minute, tzinfo=ET)


def format_local(dt, local_tz=None):
    """'Tue 17 Nov, 1:00pm' in the viewer's (or given) time zone."""
    local = dt.astimezone(local_tz)
    hour = local.hour % 12 or 12
    meridiem = "am" if local.hour < 12 else "pm"
    return f"{local:%a} {local.day} {local:%b}, {hour}:{local:%M}{meridiem}"


@dataclass
class Coverage:
    """One team's listings for one game, from the newest report that had them."""

    team: str
    game_date: date
    game_time: str
    matchup: str
    published: datetime
    entries: list = field(default_factory=list)

    def find(self, player_name):
        key = name_key(player_name)
        for entry in self.entries:
            if name_key(entry.player) == key:
                return entry
        # Fall back to a unique surname on the same team, which catches
        # "Nic" v "Nicolas" Claxton style differences between sources.
        last = last_name_key(player_name)
        same_last = [e for e in self.entries if last_name_key(e.player) == last]
        return same_last[0] if len(same_last) == 1 else None

    def game_label(self, local_tz=None):
        matchup = self.matchup.replace("@", " @ ")
        tip = tip_off(self.game_date, self.game_time)
        when = format_local(tip, local_tz) if tip else f"{self.game_date:%d %b} (US date)"
        return f"{matchup}, {when}"


class ReportIndex:
    def __init__(self, reports, today_et):
        self.today_et = today_et
        self._by_team = {}
        seen = set()
        for report in sorted(reports, key=lambda r: r.published, reverse=True):
            games = {}
            for entry in report.entries:
                games.setdefault((entry.team, entry.game_date), []).append(entry)
            for key, entries in games.items():
                listed = [e for e in entries if e.submitted]
                # NOT YET SUBMITTED here: an older report may still have this game.
                if key in seen or not listed:
                    continue
                seen.add(key)
                first = listed[0]
                self._by_team.setdefault(key[0], []).append(Coverage(
                    team=key[0], game_date=key[1], game_time=first.game_time,
                    matchup=first.matchup, published=report.published, entries=listed,
                ))

    def coverage(self, team):
        """The team's next game today or later, else its most recent past game."""
        options = self._by_team.get(team, [])
        upcoming = [c for c in options if c.game_date >= self.today_et]
        if upcoming:
            return min(upcoming, key=lambda c: c.game_date)
        return max(options, key=lambda c: c.game_date, default=None)


def reconcile(row, index, generated_at, lookback_days, local_tz=None):
    """Set status, injury, latest, as_of and source on a row that has
    player, nba_team and espn_status."""
    espn = row["espn_status"]
    cov = index.coverage(row["nba_team"]) if row["nba_team"] else None
    entry = cov.find(row["player"]) if cov else None

    if entry:
        row["status"] = NBA_STATUS.get(entry.status, "Unconfirmed")
        row["injury"] = clean_reason(entry.reason)
        row["latest"] = f"Listed {entry.status.lower()} for {cov.game_label(local_tz)}"
        row["as_of"] = cov.published.isoformat(timespec="minutes")
        row["source"] = SOURCE_NBA
    elif cov:
        row["status"] = "Clear"
        row["injury"] = ""
        row["latest"] = (
            f"Not on the NBA report for {cov.game_label(local_tz)}, though ESPN lists {espn.lower()}"
            if espn in FLAGGED else ""
        )
        row["as_of"] = cov.published.isoformat(timespec="minutes")
        row["source"] = SOURCE_NBA
    elif espn in FLAGGED:
        row["status"] = "Unconfirmed"
        row["injury"] = ""
        row["latest"] = f"ESPN lists {espn.lower()}; no NBA report in the last {lookback_days} days covers this team"
        row["as_of"] = None
        row["source"] = SOURCE_ESPN_ONLY
    else:
        row["status"] = "Clear"
        row["injury"] = ""
        row["latest"] = ""
        row["as_of"] = generated_at.isoformat(timespec="minutes")
        row["source"] = SOURCE_ESPN
    return row


def wants_note(row):
    return row["status"] != "Clear" or row["espn_status"] != "Clear"


def attach_note(row, notes, now, local_tz=None):
    """Use the newest ESPN (Rotowire) player note, if recent, as the row's latest line.

    A Clear row ESPN still flags keeps its explanation instead, since the
    disagreement between sources is the more useful thing to show.
    """
    note = notes[0] if notes else None
    if not note or not note.get("headline") or not wants_note(row):
        return row
    try:
        published = datetime.fromisoformat(note["published"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, ValueError):
        return row
    if now - published > NOTE_MAX_AGE:
        return row
    if row["status"] == "Clear" and row["espn_status"] in FLAGGED:
        return row
    row["latest"] = note["headline"]
    if row["as_of"] is None:
        row["as_of"] = published.isoformat(timespec="minutes")
    row["source"] = f"{row['source']}; note from Rotowire via ESPN, {format_local(published, local_tz)}"
    return row


def urgency(row):
    """Sort key for 'Most urgent first', lower first. Concerns in active spots,
    then concerns on the bench, then everyone clear, then IR. Mirrors the
    dashboard's urgencyRank()."""
    concern = {"Out": 0, "Questionable": 1, "Day-to-day": 1, "Unconfirmed": 2}
    if row["slot"] == "IR":
        return 8
    bench = row["slot"] == "Bench"
    if row["status"] in concern:
        return concern[row["status"]] + (3 if bench else 0)
    return 7 if bench else 6
