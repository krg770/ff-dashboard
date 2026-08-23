# Project History

## Project priorities (stated 2026-08-23)

Explicit framing from the user: **the UI is barebones/functional by
design right now, not a polish target.** The priority is getting the
backend, data pipeline, and stats correct — matching accuracy, extraction
quality, schema completeness, the pipeline actually running unattended.
UI work should mainly serve verification ("can I see that this is
working correctly") rather than aesthetics. Matches how the work has
actually gone: most of this session was fixing matching bugs, pipeline
mechanics, and data-completeness issues (the News 50-item cap, missing
players, duplicate names) rather than visual design.

**Future direction, not yet scoped**: as the dataset grows (more
podcasts, more weeks), the user wants AI-assisted querying over it — the
ability to ask questions of the data directly (e.g. "how often has this
podcast's injury calls been directionally right?", "show me every quote
about X across all shows") rather than only browsing pre-built dashboard
sections. Distinct from the reliability-tracking work above, though
related. Not much to design yet: with only 4 podcasts and ~1 week of
data, there isn't enough volume for this to be useful — worth revisiting
once the dataset is substantially bigger.

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

- Fuzzy player-name matching still misses some cases — see "Fuzzy matching"
  below for how corrections get added.
- `fantasy_points_ppr` stat is not being loaded yet.
- `player_stats` has no timestamp column at all, so the Hot/Cold, Waiver
  Adds/Avoids, and Trade Buy/Sell widgets can't show a real "last updated"
  — see "Last updated + NEW badges" below.
- **Hot/Cold Meter shows empty — confirmed not a bug.** Ran
  `load_snap_counts.py` (2026-08-23) to check; nflverse's 2026 snap-count
  source 404s outright — the data doesn't exist upstream yet since real
  Week 1 hasn't happened. Will populate automatically once nflverse
  publishes it; no dashboard fix needed.
- Production-readiness goal: get the dashboard usable in mock and live
  drafts, including incorporating salary/auction leagues and how breaking
  news should adjust salary value. Not yet scoped — flagged 2026-08-23 as
  the end-of-day target, to be broken into chunks.

## Last updated + NEW badges (2026-08-23)

Each dashboard section header now shows "Updated Xh ago", and individual
rows get a blue "NEW" badge if their underlying data is less than 24h old
(`NEW_WINDOW_HOURS` in `static/index.html`).

This only works where the database actually has a real timestamp to point
to — no section fakes one:

- **Round Focus, Latest News** — use `quotes.created_at`.
- **Player Rankings** — uses `rankings.fetched_at`.
- **Injuries** — uses `injuries.fetched_at`.
- **Hot/Cold Meter, Waiver Adds/Avoids, Trade Buy/Sell** — **no
  last-updated shown.** `player_stats` has no timestamp column
  (`id, player_id, season, week, stat_name, stat_value, source`), so
  there's nothing to point to. Adding one (and setting it in
  `load_snap_counts.py` / wherever stats get loaded) is a prerequisite if
  this is wanted here later.
- **Stream D/ST, Stream K** — skipped for now; the underlying data
  (`rankings` left-joined against DEF/K players) is mostly empty until
  matchup-based streaming logic exists anyway.

The `SimpleTable` frontend component takes an optional `timestampKey`
prop — pass the column name and it shows the header + per-row badges;
omit it and it renders exactly as before.

## Consensus / repeated-mention tracking (2026-08-23)

Question raised: if multiple podcasts independently say the same bearish
(or bullish) thing about a player, should that count for more than one
mention?

**Decision: weight by distinct podcasts, not raw quote count.** Three
mentions from the same show repeating itself across an episode isn't
independent corroboration — three different shows converging on the same
read is a real signal. Implemented two places:

- **Hot/Cold Meter** gets a new **Buzz** column: number of distinct
  podcasts that mentioned the player this content week, color-coded green
  (net rising), red (net falling), or yellow (mixed/even split) —
  `COUNT(DISTINCT e.podcast_id)` in `/api/hot_cold`, grouped by sentiment
  count. Shows "—" when there's no podcast buzz on a player at all.
- **Top riser / top faller callout**, added to the top "Latest Update"
  bar: for the most recently processed pipeline run specifically (not the
  whole week), whichever player has the most `rising`-tagged quotes and
  whichever has the most `falling`-tagged quotes. Requires **at least 2**
  mentions in that run to show (`MIN_RUN_MENTIONS` in `main.py`) — a
  single mention isn't "most talked about," so nothing displays for that
  side rather than showing a real but non-repeated one-off as if it were
  consensus. (Note: within one run this counts quotes, not distinct
  podcasts, since a single run is often one show's episode(s) — the
  distinct-podcast weighting matters more at the weekly Hot/Cold level.)

## Pipeline status widget (2026-08-23)

Answers "is the poller running, and when will it finish?" live on the
dashboard, refreshing every 15s:

- **New `pipeline_status` table** (singleton row, `id = 1`) —
  `worker.py` updates it at each stage transition (`downloading` →
  `transcribing` → `extracting`) via a small `update_pipeline_status()`
  helper; `poller.py` updates `last_poll_at` / `last_poll_new_count`.
- **ETA** is a rolling average: `avg_seconds_per_episode` = elapsed time
  in the current run ÷ episodes completed so far, × episodes remaining.
  Rough by design (no per-episode duration estimate ahead of time, e.g.
  from audio length) but self-corrects as a run progresses.
- **`/api/pipeline_status`** also reports `episodes_queued_total` (all
  `status = 'new'` episodes, not just the current run) so the idle state
  shows whether anything is waiting.
- **Confirmed: processing is strictly one episode at a time.**
  `worker.py` loads a single Whisper model and runs a plain sequential
  loop (download → transcribe → extract → insert) to completion before
  starting the next episode. No parallelism. The cron job's `flock` also
  prevents two pipeline runs overlapping.
- **Caveat:** the pilot transcription run kicked off earlier today started
  under the *old* `worker.py` (before this tracking existed), so it won't
  report live status — only runs started after this change will show up
  as "running." Confirmed via a direct API check that the idle state
  itself renders correctly ("Pipeline idle — 2 episode(s) queued").

## Injury-linkage tracking (deferred, scoped 2026-08-23)

Raised: when a player is out injured and a teammate benefits (more
targets/snaps), that's currently captured only as prose in
`fantasy_relevance` — there's no structured link saying *which* teammate
is hurt. That means we also can't detect the inverse: when the injured
player returns, the beneficiary's role (and fantasy value) predictably
cools off, but nothing currently flags that.

**Deferred, not built yet** — needs its own chunk. Scope as discussed:

- A `related_player_id` (or similar) column on `quotes`, populated by the
  extraction step when tagging `injury_beneficiary`, pointing at the
  specific injured teammate rather than just naming them in prose.
- Extraction prompt changes to identify that teammate by name/id.
- Logic to detect "return from injury" events (e.g. an `injury` tag with
  rising/neutral sentiment about playing status) and cross-reference
  existing `injury_beneficiary` rows pointing at that player, to surface
  a "cooling down" signal for whoever benefited.
- Re-running extraction on already-processed episodes to backfill the new
  field would cost additional Claude API calls — a one-time cost to
  weigh when this gets picked up.

## Latest News grouped by player (2026-08-23)

The News table was a flat, quote-by-quote list — hard to tell at a glance
when multiple shows covered the same player. Restructured to group by
player (`groupNewsByPlayer()` in `static/index.html`), each group headed
by a `BuzzBadge` (distinct podcast count, colored by net sentiment) with
every individual quote listed underneath, unabbreviated. Sorted by
podcast count first (highest-consensus players surface at the top), then
recency. Unmatched mentions get their own group at the end. This makes
the list longer/scrollier by design — the goal right now is surfacing
every piece of information, not compressing it; visual/UX trimming is a
deliberately later pass.

Verified live: 45 player groups currently, all "1 show" — expected, since
only 2 of the 4 podcasts have processed episodes so far and haven't
covered any of the same players yet. Will show real "N shows" badges once
Fantasy Footballers and Locked On finish their pilot episodes and/or more
episodes get processed.

**Player Rankings (ADP)** got the same round-grouping treatment right
after, for visual consistency with Round Focus — "Round 1" / "Round 2" /
etc. section headers instead of a plain "Rd" column in a flat table.

**Made collapsible right after that** — both Rankings and Round Focus:
round sections now default to only Round 1 expanded, click any round
header to expand/collapse it (arrow indicator + player count in the
header). Round Focus's dropdown (fetch-one-round-at-a-time) was replaced
entirely — `/api/round_focus`'s `round` query param is now optional; when
omitted it returns all rounds at once (capped to round ≤ 16, matching the
old dropdown's range, to avoid ~24 "rounds" worth of barely-drafted
players), and the frontend groups/collapses client-side the same way
Rankings does.

## Source reliability tracking (future initiative, scoped 2026-08-23)

Vision: identify which shows break news first vs. which just repeat what
they hear, and eventually score each podcast by whether their calls
actually panned out (e.g. "Podcast X said Player A would see a bigger
role because his teammate was hurt — did Player A's actual output back
that up?").

**Not started — this is a multi-part system, not a quick add, and it
inherently can't produce real signal until actual game outcomes exist to
check predictions against** (i.e. it needs the season to actually play
out, not just more engineering). Rough shape for when this gets picked
up:

1. **Event/claim clustering** — the real unlock underneath both halves of
   this idea. Different shows phrase the same underlying news differently
   ("Diggs getting more slot snaps" vs. "Commanders leaning on Diggs
   underneath"), so "who said it first" and "which podcasts agree"
   both require grouping quotes across podcasts by the underlying claim,
   not just by player. Likely needs its own Claude call comparing
   same-player quotes across episodes ("are these about the same
   event?"), not simple text matching.
2. **First-mention detection** — once claims are clustered, compare
   `episodes.published_at` across the cluster to find which podcast said
   it first vs. which repeated it afterward.
3. **Outcome linking** — connect a quote's claim (e.g. "primed for a big
   game," "more targets incoming") to the actual following week's
   `player_stats` once real season data exists (currently empty — see
   "Hot/Cold Meter shows empty" above). Needs a definition of "the
   prediction was right" per claim type, which is inherently a judgment
   call, not a pure data lookup.
4. **Per-podcast reliability score** — aggregate outcome-linked claims per
   podcast over time, weighted for how early they called it.

First practical step whenever this gets picked up: #1 (claim clustering),
since it's the shared foundation both halves of the idea depend on.

## Dev status panel (2026-08-23)

A "▸ Dev" toggle in the header (right side) reveals a debug panel via
`/api/dev_status`: server time, cron schedule (`0 8-22/2 * * *`,
hardcoded as `CRON_HOURS` in `main.py` — **kept in sync manually, not
read from crontab directly**, so if the schedule changes in cron this
constant needs updating too), computed next-scheduled-run time, whether
the pipeline is actively running, last poll result, last run finish
time, error episode count, and a per-podcast breakdown of episode counts
by status. Meant for exactly this kind of question: "did tomorrow's runs
actually happen" — check it after a scheduled cron slot passes.

## Source-reliability tracking, first pass (2026-08-23)

Picked back up per explicit request not to let this drop. Real
challenges going in (stated to the user before building):

1. Event clustering is unsolved — same player mentioned by 2 shows isn't
   proof it's the same underlying news story.
2. Outcome tracking needs real season stats, which don't exist yet
   (`player_stats` is empty - confirmed via the nflverse 404 earlier).
3. "The prediction was right" is a judgment call, not a data lookup.
4. Sample size is tiny right now (4 podcasts, ~1 week) - not enough to
   mean anything yet even once the mechanics exist.

**Built as an "Reliability 🧪" tab**, clearly marked experimental with a
persistent on-page banner (not just in docs) warning not to use it for
decisions:

- **"First to Cover This Week" — real, computed data.**
  `/api/reliability`: for players mentioned by 2+ distinct podcasts this
  week, finds which podcast's episode `published_at` is earliest, tallies
  wins per podcast, and lists the detail (player, who was first, when).
  This is a genuine approximation of "who broke it first," not a mock —
  but the banner is explicit about its limit (same player ≠ same story,
  since there's no event-clustering yet).
- **"Prediction Accuracy" — entirely mock data**, hardcoded in the
  frontend (`MOCK_ACCURACY_DATA` in `static/index.html`, not fetched from
  any endpoint) illustrating what the eventual feature will look like.
  Deliberately kept out of the backend so it can never be mistaken for
  something the API actually computed.

**Next real steps whenever this gets picked up further**: event
clustering (the same blocker noted in the original scoping entry above)
would upgrade "first to cover" from an approximation to something
trustworthy; real accuracy tracking is blocked on the season actually
starting.

## Salary tab (2026-08-23)

New "Salary" tab for a 10-team, $200-budget auction league — same
podcast-buzz data as Round Focus/Rankings, reframed for auction dollars
instead of ADP rounds alone.

- **No real auction-value data source exists**, so dollar values are
  derived from ADP: every rostered player (top 16 rounds, same cap as
  Rankings/Round Focus) gets a $1 floor, and the rest of the
  `LEAGUE_TEAMS * LEAGUE_BUDGET` pool (2000) splits by ADP rank using
  exponential decay, `weight = e^(-0.03 * (rank-1))`.
  - **First attempt was linear on raw ADP value** (weight = max_adp -
    player_adp) — rejected after testing: it compressed the entire first
    round to ~$23 flat, since ADP 1.6 and ADP 9.9 are numerically close
    relative to the full ADP range. Real auction value drops off much
    faster than that near the top.
  - Tuned `k=0.03` empirically so rank 1 lands ~$55 and rank 60 ~$10,
    checked against realistic $200-league auction behavior (elite
    RB1/WR1 in the $50-65 range). Total pool comes out to ~$1990-1994
    after rounding, close enough to the nominal $2000 to not need forced
    reconciliation.
- **Pay meter** (`pay_up` / `pay_down` / `pay_average`) reuses the exact
  same distinct-podcast buzz signal as the Hot/Cold Meter's Buzz column
  (net rising → pay up, net falling → pay down, tied/no buzz → pay
  average) - no new logic, just relabeled for the auction framing.
- On-page banner (not just docs) makes clear these are derived estimates,
  not real published prices.
- `LEAGUE_TEAMS`, `LEAGUE_BUDGET`, `ROSTER_SPOTS`, `MIN_BID` are constants
  in `main.py` - update those if the league's actual settings differ.

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
- **"Judkins and Scattaboo" (quote id 208, unmatched) → Quinshon Judkins**
  — the extraction had combined two players' names into one
  `raw_player_mention`, which no alias can fix (there's no single player
  called "Judkins and Scattaboo"). Backfilled id 208 to Quinshon Judkins
  (id 2245) directly; a sibling row (id 209, "Scattaboo" alone) already
  resolved correctly to Cam Skattebo earlier today. **Root-caused and
  fixed the actual bug**, not just this instance: added an explicit rule
  to `extraction_prompt.md` requiring one `raw_player_mention` per player
  — a quote naming multiple players now becomes multiple array items
  instead of one combined, unmatchable mention. Also discovered while
  investigating: there are **two different real NFL players both named
  "Quinshon Judkins"** in the roster (RB, Cleveland vs. DL, Green Bay —
  different `gsis_id`s, looks like an nflverse data quirk rather than
  something on our end). Added an explicit alias for bare "Judkins" →
  the RB (id 2245), since that's virtually always the one relevant to a
  fantasy dashboard, but it's worth knowing this name is ambiguous if
  weird mismatches show up again.
- **"sadeek" (quote id 246, unmatched) → Kenyon Sadiq** — Whisper's
  phonetic spelling of "Sadiq" didn't clear the trigram similarity
  threshold against the real name. Quote content ("most physically
  gifted tight ends we've ever seen") also confirms the TE. Added alias,
  backfilled the quote. **Turned out to expose a second, unrelated bug**:
  the user still saw it as "unmatched" after this fix because
  `/api/news` had a hardcoded `LIMIT 50` left over from when only one
  podcast (a handful of quotes/week) existed. With 4 podcasts now
  processed, this content week has 175 quotes — the cap was silently
  dropping 125 of them, Sadiq's among them, with no indication anywhere
  that data was missing. Removed the limit entirely (News is meant to
  surface everything, not a top-N leaderboard, unlike Hot/Cold, Waiver,
  Trade, and Stream widgets which intentionally keep their `LIMIT`s —
  those are legitimately top-N by design). Player-group count in the UI
  went from 45 to 119 immediately after the fix.
