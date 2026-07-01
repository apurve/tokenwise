#!/usr/bin/env sh
# tokenwise installer — copies the skill into your Claude Code skills directory.
#
#   ./install.sh            # user scope: ~/.claude/skills (available in every project)
#   ./install.sh --project  # project scope: ./.claude/skills (this repo only)
#
set -eu

SRC_DIR="$(cd "$(dirname "$0")" && pwd)/skills/tokenwise"

if [ ! -d "$SRC_DIR" ]; then
  echo "error: cannot find skill source at $SRC_DIR" >&2
  exit 1
fi

if [ "${1:-}" = "--project" ]; then
  DEST="$(pwd)/.claude/skills"
  SCOPE="project ($(pwd))"
else
  DEST="$HOME/.claude/skills"
  SCOPE="user (all projects)"
fi

mkdir -p "$DEST"
rm -rf "$DEST/tokenwise"
cp -r "$SRC_DIR" "$DEST/tokenwise"

echo "✅ Installed tokenwise — scope: $SCOPE"
echo "   → $DEST/tokenwise"
echo
echo "Restart your Claude Code session (or run /skills), then try:"
echo "   \"tokenwise — what used all my tokens this session?\""
