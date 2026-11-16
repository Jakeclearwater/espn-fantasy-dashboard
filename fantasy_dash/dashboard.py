"""Render the standalone dashboard HTML from exported data."""

import base64
import hashlib
import json
import re
from pathlib import Path

TEMPLATE_PATH = Path(__file__).parent / "templates" / "dashboard.html"
DATA_MARKER = "/*__DASHBOARD_DATA__*/null"
HASH_MARKER = "__SCRIPT_HASH__"
SCRIPT_RE = re.compile(r"<script>(.*?)</script>", re.DOTALL)


def embed_json(data):
    """JSON that is safe inside an inline <script>.

    Escaping <, > and & means a player name or note containing
    '</script>' or '<!--' can't end the script block early.
    """
    text = json.dumps(data, ensure_ascii=True, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def build_html(data, template=None):
    template = TEMPLATE_PATH.read_text(encoding="utf-8") if template is None else template
    if DATA_MARKER not in template or HASH_MARKER not in template:
        raise ValueError("Dashboard template is missing its data or script-hash marker.")
    html = template.replace(DATA_MARKER, embed_json(data), 1)

    # Pin the one inline script with a Content-Security-Policy hash, so the
    # page refuses to run any other script even if something slipped through.
    script = SCRIPT_RE.search(html)
    if not script:
        raise ValueError("Dashboard template has no inline script.")
    digest = base64.b64encode(hashlib.sha256(script.group(1).encode("utf-8")).digest()).decode()
    return html.replace(HASH_MARKER, f"sha256-{digest}", 1)


def write_dashboard(data, path):
    path = Path(path)
    path.write_text(build_html(data), encoding="utf-8")
    return path
