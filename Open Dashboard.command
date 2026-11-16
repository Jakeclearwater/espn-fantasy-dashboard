#!/bin/bash
# Double-click to pull fresh ESPN data, start the dashboard server and open
# the dashboard in your browser. Keep this Terminal window open while you use
# the page (its Refresh button needs it); close the window or press Ctrl+C
# to stop.

PORT=8765
URL="http://127.0.0.1:${PORT}/"

cd "$(dirname "$0")" || exit 1

# Already running from an earlier double-click: just open the page.
if curl -sf -o /dev/null -m 2 "$URL"; then
  echo "The dashboard is already running. Opening $URL"
  open "$URL"
  exit 0
fi

if [ ! -x .venv/bin/python ]; then
  echo "This folder's Python environment (.venv) is missing. See README.md, under Setup."
  read -r -p "Press Return to close."
  exit 1
fi

echo "Loading fresh data. The dashboard opens in your browser in about 20 seconds."
echo
.venv/bin/python export.py --serve --port "$PORT"
status=$?
if [ $status -ne 0 ]; then
  echo
  read -r -p "Something went wrong (see above). Press Return to close."
fi
exit $status
