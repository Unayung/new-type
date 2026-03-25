#!/bin/bash
echo "quit" | socat - UNIX-CONNECT:/tmp/new-type.sock 2>/dev/null || true
