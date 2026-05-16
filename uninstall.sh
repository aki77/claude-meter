#!/bin/bash
set -e

LABEL="com.github.aki77.claude-meter"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST" 2>/dev/null || true
    rm "$PLIST"
    echo "claude-meter uninstalled."
else
    echo "claude-meter is not installed."
fi
