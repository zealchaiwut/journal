#!/bin/bash
# Full morning run: fetch -> OCR -> generate Hermes brief.
# Called by cron at 05:45 Asia/Bangkok (see IMPLEMENTATION_NOTES.md).
set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# cron runs with a minimal env — claude lives in ~/.local/bin on the mini.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

LOCK_DIR="$REPO_DIR/.morning-run.lock"
LOG_PREFIX() { date "+%Y-%m-%d %H:%M:%S"; }

# macOS ships no flock(1); mkdir is atomic and serves as the lock.
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "$(LOG_PREFIX) morning-run: another run holds $LOCK_DIR — exiting" >&2
    exit 1
fi
trap 'rmdir "$LOCK_DIR"' EXIT

echo "$(LOG_PREFIX) morning-run: start"
"$REPO_DIR/venv/bin/python" "$REPO_DIR/generate_brief.py"
rc=$?
echo "$(LOG_PREFIX) morning-run: done rc=$rc"
exit $rc
