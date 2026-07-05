#!/usr/bin/env sh
# tokenwise installer — copies the skill into your agent's skills directory.
#
#   ./install.sh [--platform claude|antigravity]            # user scope
#   ./install.sh [--platform claude|antigravity] --project  # project scope
#
set -eu

SRC_DIR="$(cd "$(dirname "$0")" && pwd)/skills/tokenwise"

if [ ! -d "$SRC_DIR" ]; then
  echo "error: cannot find skill source at $SRC_DIR" >&2
  exit 1
fi

PLATFORM="claude"
PROJECT_SCOPE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --platform)
      PLATFORM="$2"
      shift 2
      ;;
    --project)
      PROJECT_SCOPE=1
      shift
      ;;
    *)
      echo "Unknown argument: $1"
      exit 1
      ;;
  esac
done

if [ "$PLATFORM" = "claude" ]; then
  if [ "$PROJECT_SCOPE" -eq 1 ]; then
    DEST="$(pwd)/.claude/skills"
    SCOPE="project ($(pwd)) - Claude"
  else
    DEST="$HOME/.claude/skills"
    SCOPE="user (all projects) - Claude"
  fi
elif [ "$PLATFORM" = "antigravity" ]; then
  if [ "$PROJECT_SCOPE" -eq 1 ]; then
    DEST="$(pwd)/.agents/skills"
    SCOPE="project ($(pwd)) - Antigravity"
  else
    DEST="$HOME/.gemini/config/skills"
    SCOPE="user (all projects) - Antigravity"
  fi
else
  echo "Unknown platform: $PLATFORM. Supported: claude, antigravity"
  exit 1
fi

mkdir -p "$DEST"
rm -rf "$DEST/tokenwise"
cp -r "$SRC_DIR" "$DEST/tokenwise"

echo "✅ Installed tokenwise — scope: $SCOPE"
echo "   → $DEST/tokenwise"
echo
echo "Restart your agent session (or reload skills), then try:"
echo "   \"tokenwise — what used all my tokens this session?\""
