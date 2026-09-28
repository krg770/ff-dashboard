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
./start_server.sh >> "$LOG_FILE" 2>&1
source venv/bin/activate

# worker.py shells out to the claude CLI for extraction. That needs
# CLAUDE_CODE_OAUTH_TOKEN (a long-lived token from `claude setup-token`) in
# the environment, since this runs unattended with no interactive OAuth
# session available. .env is gitignored - never commit the token itself.
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# A prior run that was killed rather than finishing cleanly (e.g. paused
# mid-transcription) leaves is_running stuck at true forever, since that
# flag only ever gets cleared by worker.py's own normal exit path - the
# dashboard would show a stale "Processing..." banner indefinitely. Safe
# to force-reset here: the flock above guarantees nothing else is running.
python -c "from db import get_conn; c=get_conn(); cur=c.cursor(); cur.execute(\"UPDATE pipeline_status SET is_running=false WHERE id=1\"); c.commit()" >> "$LOG_FILE" 2>&1

python poller.py >> "$LOG_FILE" 2>&1
python worker.py >> "$LOG_FILE" 2>&1

echo "=== Run finished: $(date) ===" >> "$LOG_FILE"
