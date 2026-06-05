#!/usr/bin/env bash
# Create the *interactive dev's* clone of the target repo and record its path.
#
# Evergreen runs as two "devs" sharing one machine: the watchdog (project_path)
# and the interactive dev (project_path_interactive). They must be separate clones
# so they never collide on git working-tree state. This clones the same origin into
# a sibling directory and writes project_path_interactive into the configs table.
#
# Usage: scripts/setup-interactive-clone.sh [DEST_PATH]
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cfg() { python3 -c "import sys; sys.path.insert(0,'$REPO/src'); from evergreen.db import $1; print($2)"; }

SRC="$(cfg get_config "get_config('project_path') or ''")"
if [ -z "$SRC" ]; then
  echo "No project_path configured. Run /setup-evergreen first." >&2
  exit 1
fi

DEST="${1:-${SRC%/}-evergreen-interactive}"
REMOTE="$(git -C "$SRC" remote get-url origin)"

if [ -d "$DEST/.git" ]; then
  echo "Interactive clone already exists at $DEST"
else
  echo "Cloning $REMOTE -> $DEST ..."
  git clone "$REMOTE" "$DEST"
fi

python3 -c "import sys; sys.path.insert(0,'$REPO/src'); from evergreen.db import set_config; set_config('project_path_interactive', '$DEST')"
echo "Set project_path_interactive = $DEST"
echo
echo "Next: install the project's dependencies in $DEST so the interactive dev can"
echo "build/test there (e.g. cd '$DEST' && npm install), then start the bot:"
echo "  node $REPO/services/discord-bot/index.js"
