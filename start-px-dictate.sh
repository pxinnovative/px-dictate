#!/bin/bash
# Launch PX Dictate menu bar app
# Run: ./start-px-dictate.sh
# Or add to Login Items for auto-start

cd "$(dirname "$0")"
LOG_DIR="$HOME/Library/Logs/PX Dictate"
mkdir -p "$LOG_DIR"
nohup python3 px_dictate_app.py > "$LOG_DIR/launcher.log" 2>&1 &
echo "PX Dictate started (PID: $!)"
echo "Look for 🎙️ in your menu bar"
echo "Hotkey: Hold fn to record, tap Control to pause"
