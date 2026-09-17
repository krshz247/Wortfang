#!/bin/bash
# Publish Wortfang to GitHub Pages using the GitHub CLI (gh).
#   cd ~/Claude/Wortschatz && bash publish.sh            # repo name: wortschatz
#   bash publish.sh my-other-name                         # custom repo name
set -euo pipefail
cd "$(dirname "$0")"
REPO="${1:-wortschatz}"

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) is not installed."
  if command -v brew >/dev/null 2>&1; then
    echo "Installing it with Homebrew…"; brew install gh
  else
    echo "Install it from https://cli.github.com (or use the web upload steps in README.md), then run this again."
    exit 1
  fi
fi

# Sign in once (opens the browser; nothing to type here).
gh auth status >/dev/null 2>&1 || gh auth login --hostname github.com --git-protocol https --web
OWNER="$(gh api user --jq .login)"
echo "→ Publishing as $OWNER/$REPO"

[ -f dict/nouns.json ] || { echo "dict/ is missing — run: python3 tools/build_dict.py"; exit 1; }

if [ ! -d .git ]; then
  git init -q -b main
fi
git add index.html manifest.webmanifest sw.js README.md LICENSE .gitignore icons dict tools/build_dict.py publish.sh
git -c user.name="$OWNER" -c user.email="$OWNER@users.noreply.github.com" commit -q -m "Update Wortfang" || echo "(nothing new to commit)"

if gh repo view "$OWNER/$REPO" >/dev/null 2>&1; then
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$OWNER/$REPO.git"
  git push -u origin main
else
  gh repo create "$REPO" --public --description "Wortfang — catch German words as you meet them (offline PWA)" --source . --remote origin --push
fi

# Turn on GitHub Pages (main branch, root folder).
gh api -X POST "repos/$OWNER/$REPO/pages" -f "source[branch]=main" -f "source[path]=/" >/dev/null 2>&1 \
  || gh api -X PUT "repos/$OWNER/$REPO/pages" -f "source[branch]=main" -f "source[path]=/" >/dev/null 2>&1 \
  || echo "Couldn't switch on Pages automatically — do it in the repo: Settings → Pages → Deploy from a branch → main / (root)."

echo
echo "✓ Done. In 1–2 minutes the app will be live at:"
echo "  https://$OWNER.github.io/$REPO/"
echo "Open that in Safari on your iPhone → Share → Add to Home Screen."
