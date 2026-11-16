"""Find, download and parse the NBA's official injury report PDFs.

The league publishes a report every 15 minutes during the season. Each one
lists the games for the current US day and the next, team by team, with every
player the team has listed (Out, Doubtful, Questionable, Probable, Available)
or "NOT YET SUBMITTED" when the team has not filed yet.
"""

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
REPORT_BASE = "https://ak-static.cms.nba.com/referee/injury/"
REPORT_URL_RE = re.compile(
    r"https://ak-static\.cms\.nba\.com/referee/injury/Injury-Report_[0-9A-Za-z_-]+\.pdf"
)
REPORT_NAME_RE = re.compile(
    r"Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{2})(?:_(\d{2}))?(AM|PM)\.pdf$"
)
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; fantasy-dash injury check)"}
TIMEOUT = 20

# Late-evening slots tried, in order, when looking for a day's final report.
FINAL_REPORT_TIMES = ((23, 45), (23, 30), (23, 15), (23, 0), (22, 0), (20, 0), (18, 0), (17, 0))

# Report column order, left to right.
COLUMNS = ("game_date", "game_time", "matchup", "team", "player", "status", "reason")
# x positions (PDF points) where each column starts, measured from 2026-27
# reports. Used only if a page's header row can't be found.
DEFAULT_COLUMN_STARTS = (23.0, 119.0, 200.0, 264.0, 425.0, 585.0, 666.0)

# Full names as printed on the report, mapped to the abbreviations shown in
# the dashboard. Keys are compared with spaces and punctuation stripped.
NBA_TEAMS = {
    "Atlanta Hawks": "ATL",
    "Boston Celtics": "BOS",
    "Brooklyn Nets": "BKN",
    "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI",
    "Cleveland Cavaliers": "CLE",
    "Dallas Mavericks": "DAL",
    "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET",
    "Golden State Warriors": "GSW",
    "Houston Rockets": "HOU",
    "Indiana Pacers": "IND",
    "LA Clippers": "LAC",
    "Los Angeles Clippers": "LAC",
    "Los Angeles Lakers": "LAL",
    "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA",
    "Milwaukee Bucks": "MIL",
    "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK",
    "Oklahoma City Thunder": "OKC",
    "Orlando Magic": "ORL",
    "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX",
    "Portland Trail Blazers": "POR",
    "Sacramento Kings": "SAC",
    "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR",
    "Utah Jazz": "UTA",
    "Washington Wizards": "WAS",
}


def _squash(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


_TEAM_KEYS = {_squash(name): abbrev for name, abbrev in NBA_TEAMS.items()}


@dataclass
class ReportEntry:
    game_date: date
    game_time: str
    matchup: str
    team: str
    player: str  # "First Last", or "" on a NOT YET SUBMITTED line
    status: str  # Out, Doubtful, Questionable, Probable, Available, or ""
    reason: str
    submitted: bool = True


@dataclass
class Report:
    url: str
    published: datetime
    entries: list = field(default_factory=list)


class ReportError(Exception):
    pass


def index_url(year):
    return f"https://official.nba.com/nba-injury-report-{year - 1}-{str(year)[2:]}-season/"


def report_url(when):
    """URL of the report published at `when` (a US Eastern datetime)."""
    hour = when.hour % 12 or 12
    meridiem = "AM" if when.hour < 12 else "PM"
    return f"{REPORT_BASE}Injury-Report_{when:%Y-%m-%d}_{hour:02d}_{when:%M}{meridiem}.pdf"


def parse_report_time(url):
    match = REPORT_NAME_RE.search(url)
    if not match:
        raise ReportError(f"Unrecognised report name: {url.rsplit('/', 1)[-1]}")
    day, hour, minute, meridiem = match.groups()
    hour = int(hour) % 12 + (12 if meridiem == "PM" else 0)
    return datetime.strptime(day, "%Y-%m-%d").replace(
        hour=hour, minute=int(minute or 0), tzinfo=ET
    )


def team_abbrev(name):
    return _TEAM_KEYS.get(_squash(name), name)


def player_display_name(report_name):
    """'Udeh, Jr., Ernest' -> 'Ernest Udeh Jr.'"""
    parts = [part.strip() for part in report_name.split(",") if part.strip()]
    if len(parts) < 2:
        return report_name.strip()
    return f"{parts[-1]} {' '.join(parts[:-1])}"


# --- parsing -----------------------------------------------------------------


def _group_lines(words, tolerance=1.5):
    lines = []
    for word in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        if lines and abs(lines[-1]["top"] - word["top"]) <= tolerance:
            lines[-1]["words"].append(word)
        else:
            lines.append({"top": word["top"], "words": [word]})
    for line in lines:
        line["words"].sort(key=lambda w: w["x0"])
        line["text"] = " ".join(w["text"] for w in line["words"])
    return lines


def _header_starts(line):
    """Column start positions from the 'Game Date ... Reason' header line."""
    words = line["words"]
    texts = [w["text"] for w in words]
    if "Matchup" not in texts or "Reason" not in texts:
        return None
    games = [w["x0"] for w in words if w["text"] == "Game"]
    named = {w["text"]: w["x0"] for w in words}
    try:
        return (games[0], games[1], named["Matchup"], named["Team"], named["Player"],
                named["Current"], named["Reason"])
    except (IndexError, KeyError):
        return None


def _cells(words, starts):
    cells = {}
    for word in words:
        index = 0
        for i, start in enumerate(starts):
            if word["x0"] + 2 >= start:
                index = i
        column = COLUMNS[index]
        cells[column] = f"{cells[column]} {word['text']}" if column in cells else word["text"]
    return cells


def _is_not_submitted(text):
    return _squash(text) == "notyetsubmitted"


def _is_furniture(text):
    return text.startswith("Injury Report:") or re.fullmatch(r"Page \d+ of \d+", text) is not None


def entries_from_words(pages):
    """Turn per-page word boxes (as from pdfplumber's extract_words) into entries.

    Long reasons wrap above and below the player's line, so each line
    without a player is attached to the nearest player line on the page.
    Game, matchup and team cells only appear on a group's first line and
    carry forward, including across pages.
    """
    entries = []
    starts = DEFAULT_COLUMN_STARTS
    current = {"game_date": None, "game_time": "", "matchup": "", "team": ""}

    for words in pages:
        rows, loose = [], []
        for line in _group_lines(words):
            if _is_furniture(line["text"]):
                continue
            header = _header_starts(line)
            if header:
                starts = header
                continue
            cells = _cells(line["words"], starts)
            if "player" in cells or _is_not_submitted(cells.get("reason", "")):
                rows.append({"top": line["top"], "cells": cells,
                             "reason": [(line["top"], cells.get("reason", ""))]})
            else:
                loose.append((line["top"], cells))

        for top, cells in loose:
            if not rows:
                continue
            nearest = min(rows, key=lambda row: abs(row["top"] - top))
            for column, value in cells.items():
                if column == "reason":
                    nearest["reason"].append((top, value))
                else:
                    nearest["cells"].setdefault(column, value)

        for row in sorted(rows, key=lambda r: r["top"]):
            cells = row["cells"]
            if "game_date" in cells:
                try:
                    current["game_date"] = datetime.strptime(cells["game_date"], "%m/%d/%Y").date()
                except ValueError:
                    pass
            for column in ("game_time", "matchup"):
                if column in cells:
                    current[column] = cells[column]
            if "team" in cells:
                current["team"] = team_abbrev(cells["team"])
            if current["game_date"] is None or not current["team"]:
                continue
            reason = " ".join(text for _, text in sorted(row["reason"]) if text)
            submitted = not _is_not_submitted(reason)
            entries.append(ReportEntry(
                game_date=current["game_date"],
                game_time=current["game_time"],
                matchup=current["matchup"],
                team=current["team"],
                player=player_display_name(cells.get("player", "")) if submitted else "",
                status=cells.get("status", "") if submitted else "",
                reason=reason if submitted else "",
                submitted=submitted,
            ))
    return entries


def parse_report_pdf(pdf_bytes):
    import pdfplumber  # imported here so the rest of the tool loads without it

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = [
            [{"text": w["text"], "x0": w["x0"], "top": w["top"]}
             for w in page.extract_words(x_tolerance=1.2)]
            for page in pdf.pages
        ]
    return entries_from_words(pages)


# --- fetching ----------------------------------------------------------------


def _exists(session, url):
    try:
        response = session.head(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException:
        return False
    return response.status_code == 200


def latest_report_url(session, now, year):
    """The newest report: from the season index page, else by probing back from now.

    Returns (url or None, error text or None).
    """
    now_et = now.astimezone(ET)
    error = None
    try:
        page = session.get(index_url(year), headers=HEADERS, timeout=TIMEOUT)
        if page.status_code == 200:
            found = []
            for url in set(REPORT_URL_RE.findall(page.text)):
                try:
                    published = parse_report_time(url)
                except ReportError:
                    continue
                if published <= now_et + timedelta(minutes=5):
                    found.append((published, url))
            if found:
                return max(found)[1], None
        error = f"index page returned HTTP {page.status_code}"
    except requests.RequestException as exc:
        error = type(exc).__name__

    slot = now_et.replace(minute=now_et.minute - now_et.minute % 15, second=0, microsecond=0)
    for step in range(12):
        url = report_url(slot - timedelta(minutes=15 * step))
        if _exists(session, url):
            return url, None
    return None, error


def final_report_url(session, day):
    """The last report published on a given US Eastern day."""
    for hour, minute in FINAL_REPORT_TIMES:
        url = report_url(datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET))
        if _exists(session, url):
            return url
    return None


def fetch_recent_reports(year, now, lookback_days=3, session=None):
    """Download and parse the latest report plus each recent day's final report.

    Returns (reports, warnings). Never raises for network or parsing problems;
    the warnings explain what was missed.
    """
    session = session or requests.Session()
    warnings = []
    urls = []

    latest, error = latest_report_url(session, now, year)
    if latest:
        urls.append(latest)
    else:
        warnings.append("Couldn't find a current NBA injury report" + (f" ({error})." if error else "."))

    today_et = now.astimezone(ET).date()
    for days_back in range(1, lookback_days + 1):
        url = final_report_url(session, today_et - timedelta(days=days_back))
        if url and url not in urls:
            urls.append(url)

    reports = []
    for url in urls:
        name = url.rsplit("/", 1)[-1]
        try:
            response = session.get(url, headers=HEADERS, timeout=TIMEOUT)
            if response.status_code != 200:
                raise ReportError(f"HTTP {response.status_code}")
            reports.append(Report(url=url, published=parse_report_time(url),
                                  entries=parse_report_pdf(response.content)))
        except Exception as exc:  # one bad PDF shouldn't sink the export
            warnings.append(f"Couldn't read NBA injury report {name} ({type(exc).__name__}: {exc}).")

    if not reports:
        warnings.append(
            "No NBA injury report could be read, so every player ESPN flags is shown as "
            "Unconfirmed. Check your connection and run the export again."
        )
    return reports, warnings
