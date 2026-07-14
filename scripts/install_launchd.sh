#!/bin/bash
# Install (or reinstall) the daily journal-fetch launchd job.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.chaiwut.journal-fetch"
PLIST_SRC="$REPO_DIR/launchd/$LABEL.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$REPO_DIR/logs" "$HOME/Library/LaunchAgents"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
cp "$PLIST_SRC" "$PLIST_DST"
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"

echo "Installed $LABEL (daily 06:30). Test now with:"
echo "  launchctl kickstart -k gui/$(id -u)/$LABEL"
echo "Logs: $REPO_DIR/logs/fetch.log"
