#!/bin/bash
STATUS_FILE="/tmp/new-type-status.json"

if [[ -f "$STATUS_FILE" ]]; then
    cat "$STATUS_FILE"
else
    echo '{"text":"","tooltip":"new-type: not running"}'
fi
