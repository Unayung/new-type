#!/bin/bash
echo "start" | socat - UNIX-CONNECT:/tmp/new-type.sock
