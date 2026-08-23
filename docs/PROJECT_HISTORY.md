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
- **`run_pipeline.sh`** — as of 2026-08-23, runs automatically via a WSL
  cron job (see "Poller schedule" below). `cron.service` is enabled via
  systemd (`/etc/wsl.conf` has `systemd=true`), so it starts on its own
  whenever WSL boots — unlike Postgres, which still needs a manual start.
  Windows Task Scheduler was considered but never finished; cron replaced
  it as the simpler path since it lives entirely inside WSL.

### Manual startup sequence (post-reboot)

```bash
sudo service postgresql start
cd ~/ff-dashboard
source venv/bin/activate
uvicorn main:app --reload --port 8000
```

Then open `http://localhost:8000`.

## Poller schedule

As of 2026-08-23, `run_pipeline.sh` runs automatically via cron, every 2
hours from 8am–10pm:

```
0 8-22/2 * * * /usr/bin/flock -n /tmp/ff_pipeline.lock /home/krg77/ff-dashboard/run_pipeline.sh
```

- `flock -n` prevents overlapping runs if one takes longer than the
  2-hour interval — a run that's still going when the next one fires is
  skipped rather than stacking up.
- Passwordless `sudo` is already scoped to exactly `service postgresql
  start` (`sudo -l` confirms the `NOPASSWD` rule), so the script's
  Postgres-start step works fine unattended.
- This is a starting cadence, not a tuned one — the real posting schedule
  of the podcasts being tracked isn't known yet. Adjust via `crontab -e`
  once that pattern is clearer.
- View recent runs in `pipeline.log`; check schedule with `crontab -l`.

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

## Podcast sources & processing status (as of 2026-08-23)

4 podcast sources are now configured in the `podcasts` table:

| Show | RSS feed | Status |
|---|---|---|
| Fantasy Football Today (CBS Sports) | `rss.amperwave.net/...` | 3 episodes extracted |
| RotoWire Fantasy Football With Theo Gremminger | `feeds.simplecast.com/D9lea_gL` | pilot episode queued |
| Fantasy Footballers - Fantasy Football Podcast | `feeds.simplecast.com/sw7PGWfw` | pilot episode queued |
| Locked On Fantasy Football with Fabs & Marcus | `feeds.simplecast.com/GKvIomw5` | pilot episode queued |

A 5th URL the user pasted (`https://megaphone.fm`) is just the bare
hosting platform, not a real per-show feed — skipped until the actual
show's RSS URL is found.

- The dashboard's Latest News table now shows a **Source** column (podcast
  name, air date, and processed/downloaded date) per item, added
  2026-08-23 — previously that info existed in the database
  (`episodes.published_at` / `processed_at`, `podcasts.name`) but wasn't
  surfaced anywhere in the UI.
- **Backlog handling:** `poller.py` has no recency filter — polling a
  freshly-added feed for the first time inserts its *entire* history as
  `status = 'new'` (the 3 new feeds added ~4,400 episodes going back to
  2014). After polling, episodes older than 7 days were bulk-marked
  `status = 'skipped'` to avoid the pipeline trying to transcribe over a
  decade of backlog. This isn't automated — it was a manual cleanup step
  done once for these 3 feeds. Same pattern was apparently used earlier
  for Fantasy Football Today (2,990 skipped there).
- **Pilot run (2026-08-23):** rather than transcribe all 20 episodes from
  the last 7 days across the 3 new shows at once (each episode is a real
  chunk of wall-clock time — audio download, Whisper transcription, a
  Claude extraction call — 20 could run 3-5+ hours), only the single
  newest episode per new show was left as `status = 'new'` and run
  through `worker.py` as a quality check; the other 17 were deferred
  (marked `skipped`, not permanently excluded — flip back to `new` and
  re-run `worker.py` to process them once the pilot looks good).

## Known gaps / next steps

- Dashboard feature next up: "last updated per section" + "NEW" badges.
- Fuzzy player-name matching still misses some cases — see "Fuzzy matching"
  below for how corrections get added.
- `fantasy_points_ppr` stat is not being loaded yet.
- Production-readiness goal: get the dashboard usable in mock and live
  drafts, including incorporating salary/auction leagues and how breaking
  news should adjust salary value. Not yet scoped — flagged 2026-08-23 as
  the end-of-day target, to be broken into chunks.

## Fixed issues

- **2026-08-23 — misleading "beneficiary" news read as an injury.** The
  extraction backend was already correctly tagging these (e.g. Dalton
  Schultz getting more Houston targets due to a *teammate's* injury was
  tagged `injury_beneficiary`, not `injury`), but the frontend
  (`static/index.html`) displayed the raw quote text and a same-styled
  gray tag badge for every tag, so `injury` and `injury_beneficiary` were
  visually indistinguishable and the ambiguous raw quote read as bad news.
  Fixed by: color-coding the `injury` (red) vs `injury_beneficiary`
  (green) tag badges, and surfacing the `fantasy_relevance` field (which
  already existed in the data but wasn't shown anywhere) under the quote
  in both the Latest News table and the top injury-alert bar.

## Fuzzy matching

Player-name matching (`resolve_player()` in `worker.py`) works in two
steps against the `player_aliases` table (`id`, `player_id`, `alias`,
`source`):

1. Exact (case-insensitive) alias match → high confidence.
2. Trigram similarity (`pg_trgm`) fallback, threshold 0.35 → low
   confidence, flagged "possible mismatch" in the UI.

To improve a specific mismatch, insert a row mapping the bad transcription
to the correct player:

```sql
INSERT INTO player_aliases (player_id, alias, source)
VALUES ((SELECT id FROM players WHERE full_name = 'Chuba Hubbard'), 'Chiba Hubbard', 'manual_correction');
```

Real examples already visible in the dashboard's "possible mismatch" rows
as of 2026-08-23 (Whisper mis-transcriptions of player names):
"Fadil Diggs" / "Stefan digs" → likely Stefon Diggs, "Chiba Hubbard" →
Chuba Hubbard, "Rashad White" → Rachaad White, "Josh Harris" → Najee
Harris. A batch-import path for a larger correction list (rather than
one-off INSERTs) is still TBD — pending the user's example set.

### Corrections applied 2026-08-23

- **"Stefan digs" → Stefon Diggs** — was matching "Fadil Diggs" (an
  unrelated real player, LB on NO) via trigram similarity. Root cause was
  bigger than a bad alias: **Stefon Diggs wasn't in the `players` table at
  all.** He signed with Washington on 2026-08-05, and nflverse's seasonal
  roster feed (`load_players.py`'s data source) hadn't picked up the move
  yet — re-running the loader confirmed 0 new players. Manually inserted
  him (id 2931, WAS/WR, `gsis_id` left `NULL`) plus aliases for "Stefon
  Diggs" and the Whisper misspellings "Stefan Diggs"/"Stefan digs".
  **Follow-up risk:** when nflverse's feed eventually includes him with a
  real `gsis_id`, `load_players.py` matches existing rows by `gsis_id`, so
  it won't find this manually-inserted row (`gsis_id IS NULL`) and will
  insert a **duplicate** Stefon Diggs row instead of updating this one.
  Needs a manual `gsis_id` backfill or a dedup pass once that happens.
- **"Gangwell" → Kenneth Gainwell** (id 702, TB/RB) — added alias,
  backfilled quote id 160.
- **"Scattaboo" → Cam Skattebo** (id 2200, NYG/RB) — added alias,
  backfilled quote id 209. A second quote (id 208, raw mention "Judkins
  and Scattaboo") extracted *two* players' names as one combined mention
  and is still unmatched — that's an extraction-prompt gap (it should
  split multi-player mentions into separate array items), not something
  an alias can fix. Left as-is pending a prompt update.
- **"Patrick Wilholms" (quote id 181, currently mismatched to "Lucas
  Patrick")** — unresolved. "Schaeffler" in that same quote is clearly a
  mangled "Schefter" (Adam Schefter, the reporter — not a player), so
  there's no reliable anchor to guess who "Wilholms" actually is. Waiting
  on the user to identify the player before adding a correction.
