"""Reading the news: who's out, who that helps, and what the player notes say.

Two kinds of analysis, both deliberately simple and explainable:

1. Opportunities. When a regular is out, their minutes and shots go to
   teammates. Half of the injured player's fantasy production is shared
   among healthy teammates in proportion to their minutes and how closely
   their positions match, capped at 30% of each teammate's own value. If the
   injured player hasn't played for a while, recent form already shows the
   effect, so the boost is halved. The boost lasts until ESPN's expected
   return date.

2. Note signals. Each recent Rotowire note is scanned for phrases about
   role (starting, more minutes, coming off the bench), injuries and returns,
   and for teammates it names as absent. The matching sentence is kept as
   evidence, so every tag can be checked against what the note said.
"""

import re
from datetime import datetime, timedelta

from .injuries import name_key
from .nba_report import ET
from .projection import play_chance, talent_value

SHARED = 0.5               # share of an injured player's production taken up by teammates
BOOST_CAP = 0.3            # most boosts can add in all, as a share of the teammate's own value
STALE_ABSENCE = 0.5        # boost kept when the player has already missed most of the last 15 days
MIN_OUT_VALUE = 20         # injured players below this (points a game) aren't worth tracking
MIN_OUT_MINUTES = 20
MIN_ROTATION_MINUTES = 12  # teammates below this aren't in the rotation
NOTE_DAYS = 10
POSITIONS = ("PG", "SG", "SF", "PF", "C")

SIGNALS = {
    "Role up": [
        r"\b(will|to|set to|expected to|should|could|figures to|is slated to) (start|draw the start|enter the starting)",
        r"\b(move[sd]?|moving|enter(s|ed)?|insert(ed)?|join(s|ed)?) (into )?the starting (lineup|five)",
        r"\b(in|into) a starting role", r"\bdraw(s|ing)? the start", r"\bstart in place of\b",
        r"\b(increased|expanded|bigger|larger|heavier|elevated) (role|minutes|usage|workload|playing time)",
        r"\bmore (minutes|shots|touches|usage|opportunit|playing time)",
        r"\b(see|seeing|saw) (more|an uptick|extra|increased|plenty of)", r"\bstep (in|up|into)\b",
        r"\b(uptick|bump|boost|spike) in (minutes|usage|role|playing time)",
        r"\bstarts? for the (first|second|third|\w+) (time|straight)",
    ],
    "Role down": [
        r"\b(come|came|coming|comes) off the bench", r"\bmove(d|s)? (back )?to the bench",
        r"\b(reduced|limited|smaller|diminished|decreased) (role|minutes|workload|playing time)",
        r"\bminutes (restriction|limit|cap)\b", r"\bout of the rotation\b", r"\bDNP\b|\bdid not play\b|\bdidn't play\b",
        r"\blost (his|her|their) (starting )?(spot|role|job)",
    ],
    "Returning": [
        r"\b(will|expected to|set to|is slated to|plans to) (return|play|suit up|be available)",
        r"\b(made|makes|making) (his|her|their) return", r"\bcleared to (play|return)", r"\b(upgraded|listed) (as )?available",
        r"\bnot listed on the injury report", r"\bback in the lineup",
    ],
    "Injury": [
        r"\bruled out\b", r"\bwon't play\b", r"\bwill (miss|sit)\b", r"\b(listed|is) (as )?(questionable|doubtful|out)\b",
        r"\bsidelined\b", r"\bsurgery\b", r"\bre-evaluated\b", r"\bday-to-day\b",
        r"\b(strain|sprain|fracture|tear|torn|soreness|contusion|tendinitis)\b",
    ],
}
COMPILED = {tag: [re.compile(p, re.I) for p in patterns] for tag, patterns in SIGNALS.items()}

# "with Isaiah Jackson (groin) sidelined", "amid Ja Morant's (hamstring) absence",
# "in place of Coby White", "Coby White (ankle) is out"
NAME = r"([A-Z][\w'.-]+(?: [A-Z][\w'.-]+){1,2})"
ABSENCE = [
    re.compile(rf"\b(?i:with|while|as) {NAME}(?:'s)?(?: \([^)]*\))? (?:out|sidelined|unavailable|absent|injured|missing)"),
    re.compile(rf"\b(?i:amid) {NAME}(?:'s)?(?: \([^)]*\))? absence"),
    re.compile(rf"\b(?i:in place of) {NAME}"),
    re.compile(rf"\b{NAME}(?:'s)? (?:\([^)]*\) )?(?:being )?(?:out|sidelined|absence)\b"),
]


def _overlap(a, b):
    """How closely two players' positions match, from 1 (shared) down to 0.15."""
    pa = [POSITIONS.index(p) for p in (a.get("positions") or "").split("/") if p in POSITIONS]
    pb = [POSITIONS.index(p) for p in (b.get("positions") or "").split("/") if p in POSITIONS]
    if not pa or not pb:
        return 0.3
    gap = min(abs(i - j) for i in pa for j in pb)
    return {0: 1.0, 1: 0.6, 2: 0.3}.get(gap, 0.15)


def _out_for_week(row, today):
    return play_chance(row, today, today) == 0 and play_chance(row, today + timedelta(days=3), today) == 0


def _brief(row):
    keys = ("player", "player_id", "nba_team", "positions", "status", "injury", "expected_return",
            "out_for_season", "team", "team_id", "minutes", "shots")
    return {**{k: row.get(k) for k in keys}, "value": round(talent_value(row), 1)}


def find_opportunities(players, now, horizon=timedelta(days=14)):
    """Share out injured regulars' production among their teammates.

    players: every known player row (rostered and free agents), each with
    'team' set to a fantasy team name or 'Free agent'. Adds a 'boosts' list
    to the rows that benefit, and returns the opportunities, biggest first.
    """
    today = now.astimezone(ET).date()
    by_nba_team = {}
    for row in players:
        if row.get("nba_team"):
            by_nba_team.setdefault(row["nba_team"], []).append(row)

    found = []
    for nba_team, group in by_nba_team.items():
        healthy = [r for r in group if not _out_for_week(r, today)
                   and (r.get("minutes") or 0) >= MIN_ROTATION_MINUTES and (r.get("season_games") or 0) >= 3]
        for out in group:
            value = talent_value(out)
            if not _out_for_week(out, today) or value < MIN_OUT_VALUE or (out.get("minutes") or 0) < MIN_OUT_MINUTES:
                continue
            weights = {r["player_id"]: (r.get("minutes") or 0) * _overlap(out, r) for r in healthy}
            total = sum(weights.values())
            if not total:
                continue
            if out.get("out_for_season"):
                until = None
            elif out.get("expected_return"):
                until = out["expected_return"]
            else:  # no date yet: assume a couple of weeks
                until = (today + horizon).isoformat()
            stale = (out.get("last_15_games") or 0) < 3 and (out.get("season_games") or 0) > (out.get("last_15_games") or 0)
            shared = SHARED * value * (STALE_ABSENCE if stale else 1.0)
            beneficiaries = []
            for row in healthy:
                share = weights[row["player_id"]] / total
                # The cap is on a player's total boost, however many teammates are out.
                room = BOOST_CAP * talent_value(row) - sum(b["bump"] for b in row.get("boosts", []))
                bump = min(shared * share, room)
                if bump < 0.5:
                    continue
                row.setdefault("boosts", []).append({
                    "from": out["player"], "from_id": out["player_id"], "bump": round(bump, 1), "until": until,
                })
                beneficiaries.append({**_brief(row), "bump": round(bump, 1)})
            if beneficiaries:
                beneficiaries.sort(key=lambda b: -b["bump"])
                found.append({
                    "out": _brief(out), "nba_team": nba_team, "until": until,
                    "already_missing": stale, "minutes": out.get("minutes"), "shots": out.get("shots"),
                    "beneficiaries": beneficiaries[:6],
                })
    found.sort(key=lambda o: -o["out"]["value"])
    return found


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text or "") if s.strip()]


def note_signals(notes, teammates, now, player=""):
    """Tag a player's recent notes. teammates: names of others on the same NBA team.

    Notes often describe other players too ("Dean Wade could see more
    minutes"), so a role sentence that names a teammate but not the player
    is skipped.

    Returns [{published, headline, tags, evidence, absences}], newest first.
    """
    known = {name_key(n): n for n in teammates}
    signals = []
    for note in notes or []:
        try:
            published = datetime.fromisoformat(note["published"].replace("Z", "+00:00"))
        except (KeyError, AttributeError, ValueError):
            continue
        if now - published > timedelta(days=NOTE_DAYS):
            continue
        tags, evidence = [], None
        sentences = _sentences(note.get("headline", "")) + _sentences(note.get("story", ""))
        surname = player.split()[-1] if player else ""
        about_player = [s for s in sentences if (surname and surname in s)
                        or not any(name.split()[-1] in s for name in teammates)]
        for tag, patterns in COMPILED.items():
            for sentence in (sentences if tag == "Injury" else about_player):
                if any(p.search(sentence) for p in patterns):
                    tags.append(tag)
                    if evidence is None and tag != "Injury":
                        evidence = sentence
                    break
        absences = []
        for sentence in sentences:
            for pattern in ABSENCE:
                for name in pattern.findall(sentence):
                    # A capitalised word just before the name ("With Coby White") can be swept in.
                    words = name.split()
                    match = next((known[k] for k in (name_key(" ".join(words[i:])) for i in range(len(words) - 1))
                                  if k in known), None)
                    if match and match not in absences:
                        absences.append(match)
                        evidence = evidence or sentence
        if absences and "Role up" not in tags:
            tags.insert(0, "Teammate out")
        if tags:
            signals.append({"published": note["published"], "headline": note.get("headline", ""),
                            "tags": tags, "evidence": (evidence or note.get("headline", ""))[:260],
                            "absences": absences})
    return signals


def describe(opportunity, local_day):
    """'Coby White is out until Wed 25 Nov, leaving about 31 minutes and 14 shots a game.'"""
    out = opportunity["out"]
    if out.get("out_for_season"):
        when = "out for the season"
    elif out.get("expected_return"):
        when = f"out until about {local_day(out['expected_return'])}"
    else:
        when = "out with no return date yet"
    parts = []
    if opportunity.get("minutes"):
        parts.append(f"{opportunity['minutes']:.0f} minutes")
    if opportunity.get("shots"):
        parts.append(f"{opportunity['shots']:.0f} shots")
    left = f", leaving about {' and '.join(parts)} a game to share" if parts else ""
    return f"{out['player']} ({out['nba_team']}) is {when}{left}."
