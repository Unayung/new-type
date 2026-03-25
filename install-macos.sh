#!/bin/bash
# Install new-type as a macOS LaunchAgent (auto-start on login)
set -e

PLIST_NAME="com.new-type.daemon"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_FILE="$PLIST_DIR/$PLIST_NAME.plist"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
UV="$(which uv)"

mkdir -p "$PLIST_DIR"

cat > "$PLIST_FILE" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_NAME</string>
    <key>ProgramArguments</key>
    <array>
        <string>$UV</string>
        <string>run</string>
        <string>$PROJECT_DIR/main.py</string>
        <string>daemon</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/new-type.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/new-type.log</string>
</dict>
</plist>
EOF

# Unload first if already loaded
launchctl unload "$PLIST_FILE" 2>/dev/null || true
launchctl load "$PLIST_FILE"

echo "✓ new-type installed as LaunchAgent"
echo "  Logs: tail -f /tmp/new-type.log"
echo "  Stop: launchctl unload $PLIST_FILE"
echo "  Start: launchctl load $PLIST_FILE"
