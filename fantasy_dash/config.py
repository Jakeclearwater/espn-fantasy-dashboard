"""Local settings: your league, your team and the ESPN cookies for the private league."""

import os
from dataclasses import dataclass

# espn-api names a season by the year it ends, so 2026-27 is 2027.
LEAGUE_ID = 294680752
YEAR = 2027
DEFAULT_TEAM = "Fish 'n Chips"

# ESPN cookies: from ESPN_S2 and SWID environment variables when set (as on
# GitHub), otherwise from fantasy_dash/local_secrets.py, which is git-ignored.
try:
    from .local_secrets import ESPN_S2, SWID
except ImportError:
    ESPN_S2 = SWID = ""


class ConfigError(Exception):
    """A setup problem the user can fix. The message never contains secrets."""


@dataclass(frozen=True, repr=False)
class Credentials:
    espn_s2: str
    swid: str

    def __repr__(self):
        # Keep cookie values out of tracebacks and debug output.
        return "Credentials(espn_s2=<hidden>, swid=<hidden>)"


def get_credentials(espn_s2=None, swid=None):
    espn_s2 = (os.environ.get("ESPN_S2") or ESPN_S2 if espn_s2 is None else espn_s2).strip()
    swid = (os.environ.get("SWID") or SWID if swid is None else swid).strip()
    missing = [name for name, value in (("ESPN_S2", espn_s2), ("SWID", swid)) if not value]
    if missing:
        raise ConfigError(
            f"Missing {' and '.join(missing)}. Paste your espn.com cookie values into "
            "fantasy_dash/local_secrets.py, or set them as environment variables "
            "(see README.md, 'Finding espn_s2 and SWID')."
        )
    # ESPN stores SWID with braces; people often copy it without them.
    if not swid.startswith("{"):
        swid = "{" + swid.strip("{}") + "}"
    return Credentials(espn_s2=espn_s2, swid=swid)
