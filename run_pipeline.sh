#!/bin/bash
# Runs the full FF podcast pipeline unattended: starts Postgres,
# polls for new episodes, processes them. Triggered by Windows Task
# Scheduler via: wsl.exe -d Ubuntu -e /home/krg77/ff-dashboard/run_pipeline.sh

export PATH=~/.npm-global/bin:~/.local/bin:$PATH

LOG_FILE=~/ff-dashboard/pipeline.log
LOCK_FILE=/tmp/ff_pipeline.lock

exec 200>"$LOCK_FILE"
flock -n 200 || { echo "=== Skipped (already running): $(date) ===" >> "$LOG_FILE"; exit 0; }

echo "=== Run started: $(date) ===" >> "$LOG_FILE"

sudo service postgresql start >> "$LOG_FILE" 2>&1

cd ~/ff-dashboard || exit 1
source venv/bin/activate

python poller.py >> "$LOG_FILE" 2>&1
python worker.py >> "$LOG_FILE" 2>&1

echo "=== Run finished: $(date) ===" >> "$LOG_FILE"
