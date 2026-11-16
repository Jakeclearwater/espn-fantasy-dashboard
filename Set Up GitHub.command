#!/bin/bash
# Double-click to put the dashboard online with GitHub Pages, or to update it.
# Safe to run again: it pushes new code, refreshes the ESPN cookie secrets
# (handy when the cookies expire) and republishes the page.

REPO="Jakeclearwater/espn-fantasy-dashboard"
SITE="https://jakeclearwater.github.io/espn-fantasy-dashboard/"

cd "$(dirname "$0")" || exit 1
fail() { echo; echo "Stopped: $1"; read -r -p "Press Return to close."; exit 1; }

command -v gh >/dev/null || fail "GitHub's command-line tool isn't installed. Run: brew install gh"

# 1. Sign in to GitHub (opens your browser the first time).
if ! gh auth status >/dev/null 2>&1; then
  echo "Signing in to GitHub. Follow the prompts; a browser window opens."
  gh auth login --hostname github.com --git-protocol https --web || fail "GitHub sign-in didn't finish."
fi
gh auth setup-git >/dev/null 2>&1

# 2. Create the repository if needed, and push the code. The ESPN cookie file
#    is git-ignored, so it is never pushed.
if ! gh repo view "$REPO" >/dev/null 2>&1; then
  echo "Creating $REPO..."
  gh repo create "$REPO" --public \
    --description "ESPN fantasy basketball injury, waiver and trade dashboard" || fail "couldn't create the repository."
fi
git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$REPO.git"
echo "Pushing the code..."
git push -u origin main || fail "the push didn't go through."

# 3. Store the ESPN cookies as encrypted secrets, read straight from the
#    local cookie file so they're never shown or typed.
echo "Setting the ESPN cookie secrets..."
for name in ESPN_S2 SWID; do
  .venv/bin/python -c "from fantasy_dash.local_secrets import $name; print($name, end='')" \
    | gh secret set "$name" --repo "$REPO" || fail "couldn't set the $name secret."
done

# 4. Turn on GitHub Pages (published by the workflow), then publish now.
gh api -X POST "repos/$REPO/pages" -f build_type=workflow >/dev/null 2>&1 \
  || gh api -X PUT "repos/$REPO/pages" -f build_type=workflow >/dev/null 2>&1
echo "Publishing..."
gh workflow run publish.yml --repo "$REPO" || fail "couldn't start the publish workflow."
sleep 6
RUN_ID=$(gh run list --repo "$REPO" --workflow publish.yml --limit 1 --json databaseId --jq '.[0].databaseId')
if [ -n "$RUN_ID" ]; then
  gh run watch "$RUN_ID" --repo "$REPO" --exit-status || fail "the publish run failed. See https://github.com/$REPO/actions"
fi

echo
echo "Done. The dashboard is at $SITE"
echo "It updates itself about every two hours. Share that link with your friend."
open "$SITE"
read -r -p "Press Return to close."
