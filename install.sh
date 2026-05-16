#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.github.aki77.claude-meter"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

# 依存関係をインストール
echo "Installing dependencies..."
cd "$SCRIPT_DIR"
uv sync

VENV_BIN="$SCRIPT_DIR/.venv/bin"

# plistを生成
sed "s|CLAUDE_METER_VENV|$VENV_BIN|g" "$SCRIPT_DIR/com.github.aki77.claude-meter.plist" > "$PLIST"

# 既に登録済みなら一旦停止
launchctl unload "$PLIST" 2>/dev/null || true

# 登録・起動
launchctl load "$PLIST"

echo "claude-meter installed and started."
echo "Log: /tmp/claude-meter.log"
