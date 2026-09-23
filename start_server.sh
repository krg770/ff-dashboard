#!/bin/bash
# Starts the FF dashboard's uvicorn server in the background, if it isn't
# already running. Triggered by the "FF Dashboard Server" Windows Scheduled
# Task at user logon, so the server survives a laptop restart without
# manual intervention. This script is the actual startup logic and is
# version-controlled - the Task Scheduler entry itself is just a thin
# trigger pointing at it (Task Scheduler settings can't live in git, but
# nothing about *what* gets started or *how* should live anywhere else).

set -e
cd "$(dirname "$0")"

PORT=8000
LOG_FILE=/tmp/ff-dashboard-start.log

if curl -s -o /dev/null -w '%{http_code}' "http://localhost:$PORT" 2>/dev/null | grep -q '^200$'; then
    echo "$(date): already running on port $PORT, skipping" >> "$LOG_FILE"
    exit 0
fi

source venv/bin/activate
nohup uvicorn main:app --port "$PORT" > /tmp/ff-dashboard.log 2>&1 &
disown
echo "$(date): started uvicorn on port $PORT (pid $!)" >> "$LOG_FILE"
