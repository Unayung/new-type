#!/bin/bash
echo "stop" | socat - UNIX-CONNECT:/tmp/new-type.sock
