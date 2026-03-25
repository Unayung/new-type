#!/bin/bash
echo "toggle" | socat - UNIX-CONNECT:/tmp/new-type.sock
