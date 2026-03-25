#!/bin/bash
# Launch new-type daemon — safe to call multiple times (no-op if already running)
STATUS=$(echo "status" | socat -T1 - UNIX-CONNECT:/tmp/new-type.sock 2>/dev/null)
if [[ -n "$STATUS" ]]; then
    notify-send "new-type" "Already running — $STATUS" --icon=audio-input-microphone
    exit 0
fi

export LD_LIBRARY_PATH="/home/unayung/Projects/new-type/lib:/opt/cuda/targets/x86_64-linux/lib:$LD_LIBRARY_PATH"
cd /home/unayung/Projects/new-type
exec uv run main.py daemon
