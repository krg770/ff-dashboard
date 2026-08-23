# Project History

## Concept

Claude listens to fantasy football podcasts and turns them into content for a
dashboard, which will eventually be posted publicly on the internet. News is
inherently semi-late, since the automation can only run after a podcast
episode has actually posted — there's no way around that lag.

## Infrastructure & operations notes

Nothing currently restarts itself after a reboot — everything below needs a
manual trigger:

- **Postgres** — never auto-starts in WSL; needs `sudo service postgresql
  start` every session.
- **Dashboard (`uvicorn`)** — was only ever run as a foreground process in a
  terminal, so it dies when that terminal (or the machine) closes/reboots.
- **`poller.py` / `worker.py`** — have only been run manually so far.
- **`run_pipeline.sh`** — built and tested, but never finished wiring into
  Windows Task Scheduler. This is the last piece needed for the pipeline to
  run on its own — until then, reboot recovery is a manual step every time.

### Manual startup sequence (post-reboot)

```bash
sudo service postgresql start
cd ~/ff-dashboard
source venv/bin/activate
uvicorn main:app --reload --port 8000
```

Then open `http://localhost:8000`.

## Git & Claude Code Desktop setup

- Project lives inside WSL's Linux filesystem at `/home/krg77/ff-dashboard`
  — **not** under `C:\Users\krg77\`. From Windows it's reachable at
  `\\wsl.localhost\Ubuntu\home\krg77\ff-dashboard`. Searching Windows paths
  for it won't find it.
- Git repo initialized (`git init`), with a `.gitignore` added to keep
  `.env` (contains `DB_PASSWORD`), `venv/`, `__pycache__/`, `pipeline.log`,
  and `work/` out of version control.
- Initial commit: `33d6a2f` — "Initial commit - FF podcast dashboard" (13
  files, 1455 lines, no secrets committed).
- Claude Code Desktop's Code tab initially failed to connect to the WSL
  project (suspected WSL networking glitch); a full reboot resolved it.
  Connecting: Code tab → + New → select Ubuntu → browse to `ff-dashboard`
  (or paste `\\wsl.localhost\Ubuntu\home\krg77\ff-dashboard`).

## Known gaps / next steps

- Wire `run_pipeline.sh` into Windows Task Scheduler so the pipeline runs
  automatically instead of requiring manual triggers.
- Dashboard feature next up: "last updated per section" + "NEW" badges.
- Fuzzy player-name matching still misses some cases.
- `fantasy_points_ppr` stat is not being loaded yet.
- Injury-vs-beneficiary distinction in the extraction prompt was recently
  fixed (players who benefit from someone else's injury were previously
  getting conflated with the injured player).
