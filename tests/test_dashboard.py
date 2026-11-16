import base64
import hashlib
import json
import re

import pytest

from export import build_export
from fantasy_dash.dashboard import TEMPLATE_PATH, build_html
from tests.conftest import NZ


@pytest.fixture
def data(fake_league, now, fake_fetch):
    return build_export(fake_league, "Fish 'n Chips", 4, 7, now, fetch_reports=fake_fetch, local_tz=NZ)[1]


def embedded_data(html):
    match = re.search(r"const DATA = (.*?);\n", html)
    return json.loads(match.group(1))


def test_data_round_trips_through_the_page(data):
    # Player ids key some maps; JSON turns those keys into strings.
    assert embedded_data(build_html(data)) == json.loads(json.dumps(data))


def test_hostile_names_cannot_break_out_of_the_script(data):
    data["rosters"][0]["player"] = "</script><script>alert(1)</script>"
    data["rosters"][0]["latest"] = "<!-- <img src=x onerror=alert(1)> & more"
    html = build_html(data)
    assert html.count("</script>") == 1
    assert "<img" not in html
    assert embedded_data(html)["rosters"][0]["player"] == "</script><script>alert(1)</script>"


def test_content_security_policy_pins_the_inline_script(data):
    html = build_html(data)
    script = re.search(r"<script>(.*?)</script>", html, re.DOTALL).group(1)
    digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode()
    assert f"script-src 'sha256-{digest}'" in html


def test_template_never_parses_strings_as_html():
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    for risky in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert risky not in template


def test_template_covers_themes_motion_print_and_fonts():
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    for needle in ("prefers-color-scheme: dark", "prefers-reduced-motion", "@media print",
                   "Barlow+Condensed", "IBM+Plex+Sans", ":focus-visible",
                   "Out, in an active lineup spot", "How to read the status", "does not update itself"):
        assert needle in template
    assert "gradient" not in template


def test_refresh_button_only_appears_when_served(data):
    html = build_html(data)
    assert 'id="refresh" hidden' in html and "connect-src 'self'" in html
    assert '"refresh"' not in re.search(r"const DATA = (.*?);\n", html).group(1)  # no token on disk
    served = build_html({**data, "refresh": {"token": "abc"}})
    assert embedded_data(served)["refresh"] == {"token": "abc"}
