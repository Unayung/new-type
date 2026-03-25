#!/bin/bash
cd /home/unayung/Projects/new-type
export LD_LIBRARY_PATH="/home/unayung/Projects/new-type/lib:/opt/cuda/targets/x86_64-linux/lib:$LD_LIBRARY_PATH"
exec uv run main.py daemon
