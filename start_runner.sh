#!/bin/bash
# Safe background launcher for runner.py

set -euo pipefail

LOG_DIR="logs"
mkdir -p "$LOG_DIR"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_FILE="$LOG_DIR/runner_$TIMESTAMP.log"

echo "Starting runner in background..."
nohup python runner.py >"$LOG_FILE" 2>&1 &

PID=$!
echo "Runner started with PID $PID. Logs: $LOG_FILE"
