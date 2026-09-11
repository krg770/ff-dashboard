"""
FastAPI backend for the fantasy football dashboard. Serves JSON for
each widget the frontend renders, plus the static frontend itself.

Usage:
    uvicorn main:app --reload --port 8000
Then open http://localhost:8000 in a browser.
"""
import math
import re
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from fastapi import FastAPI, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from db import get_conn
import poller

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SEASON = 2026
# Minimum quote count within a single run before a player is called out as
# the "top" riser/faller for that run - avoids surfacing a one-off mention
# as if it were a consensus signal.
MIN_RUN_MENTIONS = 2
# 10-team, $200/team auction league settings for the Salary tab.
LEAGUE_TEAMS = 10
LEAGUE_BUDGET = 200
ROSTER_SPOTS = 16  # matches the round<=16 cap used elsewhere (Rankings, Round Focus)
MIN_BID = 1


def compute_auction_values(players):
    """
    Shared with /api/salary and the player-detail panel so the two
    never disagree on a player's value. `players` is an ADP-ascending
    list of tuples whose first element is player_id - see /api/salary
    for the value-curve rationale (exponential decay by rank).
    """
    n = len(players)
    total_pool = LEAGUE_TEAMS * LEAGUE_BUDGET
    value_pool = total_pool - MIN_BID * n
    decay_k = 0.03
    weights = [math.exp(-decay_k * i) for i in range(n)]
    weight_sum = sum(weights) or 1
    return {
        row[0]: MIN_BID + round(value_pool * weight / weight_sum)
        for row, weight in zip(players, weights)
    }
# Mirrors the WSL crontab entry (`0 8-22/2 * * *`) - not read from crontab
# directly, just kept in sync manually. Used only to estimate "next run"
# for the dev panel; the actual schedule lives in cron, this is display-only.
CRON_HOURS = [8, 10, 12, 14, 16, 18, 20, 22]
CRON_INTERVAL_SEC = (CRON_HOURS[1] - CRON_HOURS[0]) * 3600

SERVER_STARTED_AT = datetime.now(timezone.utc)

# Guards /api/dev/run: poll_all() is fast (RSS fetches, seconds) but not
# reentrant-safe against itself - one in-flight poll at a time.
_poll_lock = threading.Lock()


def next_scheduled_run():
    now = datetime.now()
    for h in CRON_HOURS:
        candidate = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if candidate > now:
            return candidate
    tomorrow = now + timedelta(days=1)
    return tomorrow.replace(hour=CRON_HOURS[0], minute=0, second=0, microsecond=0)


def dict_rows(cur):
    cols = [desc[0] for desc in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def current_content_week() -> date:
    today = date.today()
    weekday = today.weekday()
    return today - timedelta(days=weekday)


def current_nfl_week(cur, season=SEASON) -> int:
    """
    Smallest schedule week whose games haven't all happened yet, i.e.
    "the week to look ahead to." Before the season starts (like now -
    Week 1 kicks off 2026-09-09) this just returns 1. Real schedule data,
    no guessing: falls back to 1 if team_schedule hasn't been loaded.
    """
    cur.execute(
        "SELECT MIN(week) FROM team_schedule WHERE season = %s AND game_date >= CURRENT_DATE",
        (season,),
    )
    row = cur.fetchone()
    return row[0] if row and row[0] else 1


@app.get("/api/widgets")
def get_widgets():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT widget_key, title, category, sort_order FROM dashboard_widgets WHERE enabled = true ORDER BY sort_order"
    )
    result = dict_rows(cur)
    cur.close()
    conn.close()
    return result


@app.get("/api/latest_run")
def get_latest_run():
    """
    Shows what changed in the most recent pipeline run: which
    episode(s) were processed, and every quote from them - with
    injury-tagged ones easy for the frontend to call out separately.
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, title, published_at, processed_at
        FROM episodes
        WHERE processed_at IS NOT NULL
        ORDER BY processed_at DESC
        LIMIT 1
        """
    )
    latest = cur.fetchone()
    if not latest:
        cur.close()
        conn.close()
        return {"episodes": [], "quotes": []}

    latest_processed_at = latest[3]
    cur.execute(
        """
        SELECT id, title, published_at, processed_at
        FROM episodes
        WHERE processed_at = %s
        ORDER BY published_at DESC
        """,
        (latest_processed_at,),
    )
    episodes = dict_rows(cur)
    episode_ids = [e["id"] for e in episodes]

    cur.execute(
        """
        SELECT q.episode_id, q.player_id, p.full_name, q.quote_text, q.tags, q.sentiment, q.match_confidence,
               q.fantasy_relevance, q.created_at,
               pod.name AS source_podcast, e.published_at AS source_published_at
        FROM quotes q
        LEFT JOIN players p ON q.player_id = p.id
        LEFT JOIN episodes e ON q.episode_id = e.id
        LEFT JOIN podcasts pod ON e.podcast_id = pod.id
        WHERE q.episode_id = ANY(%s)
        ORDER BY q.created_at DESC
        """,
        (episode_ids,),
    )
    quotes = dict_rows(cur)

    counts = defaultdict(lambda: {"rising": 0, "falling": 0, "player_id": None})
    for q in quotes:
        if not q["full_name"] or q["sentiment"] not in ("rising", "falling"):
            continue
        counts[q["full_name"]][q["sentiment"]] += 1
        counts[q["full_name"]]["player_id"] = q["player_id"]

    def top(sentiment):
        candidates = [(name, c) for name, c in counts.items() if c[sentiment] >= MIN_RUN_MENTIONS]
        if not candidates:
            return None
        name, c = max(candidates, key=lambda nc: nc[1][sentiment])
        return {"full_name": name, "mention_count": c[sentiment], "player_id": c["player_id"]}

    # The "NEW INJURY NEWS" dropdown used to be filtered from `quotes` above,
    # which only covers episodes from this exact run - so it reset to
    # (near-)empty every time the poller ran again, even within the same
    # day. Instead, scope it to calendar-day: every injury-tagged quote from
    # any podcast processed today, so it accumulates across runs and only
    # rolls over at midnight.
    today_start = datetime.combine(date.today(), datetime.min.time())
    cur.execute(
        """
        SELECT q.episode_id, q.player_id, p.full_name, q.quote_text, q.tags, q.sentiment, q.match_confidence,
               q.fantasy_relevance, q.created_at,
               pod.name AS source_podcast, e.published_at AS source_published_at
        FROM quotes q
        LEFT JOIN players p ON q.player_id = p.id
        LEFT JOIN episodes e ON q.episode_id = e.id
        LEFT JOIN podcasts pod ON e.podcast_id = pod.id
        WHERE q.tags @> '["injury"]' AND q.created_at >= %s
        ORDER BY q.created_at DESC
        """,
        (today_start,),
    )
    injury_quotes_today = dict_rows(cur)

    cur.close()
    conn.close()

    return {
        "episodes": episodes,
        "quotes": quotes,
        "top_riser": top("rising"),
        "top_faller": top("falling"),
        "injury_quotes_today": injury_quotes_today,
    }


@app.get("/api/pipeline_status")
def get_pipeline_status():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM pipeline_status WHERE id = 1")
    rows = dict_rows(cur)
    cur.execute("SELECT count(*) FROM episodes WHERE status = 'new'")
    queued = cur.fetchone()[0]
    cur.close()
    conn.close()

    status = rows[0] if rows else {}
    status["episodes_queued_total"] = queued
    total = status.get("episodes_total_this_run")
    done = status.get("episodes_done_this_run")
    status["episodes_remaining_this_run"] = (total - done) if (total is not None and done is not None) else None
    return status


@app.get("/api/dev_status")
def get_dev_status():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM pipeline_status WHERE id = 1")
    rows = dict_rows(cur)
    pipeline = rows[0] if rows else {}

    cur.execute(
        """
        SELECT pod.name, e.status, count(*)
        FROM episodes e JOIN podcasts pod ON e.podcast_id = pod.id
        GROUP BY pod.name, e.status
        ORDER BY pod.name, e.status
        """
    )
    by_podcast = defaultdict(dict)
    for name, status, count in cur.fetchall():
        by_podcast[name][status] = count

    cur.execute("SELECT count(*) FROM episodes WHERE status = 'error'")
    error_count = cur.fetchone()[0]

    cur.close()
    conn.close()

    return {
        "pipeline": pipeline,
        "podcasts": [{"name": name, "counts": counts} for name, counts in by_podcast.items()],
        "total_error_episodes": error_count,
        "cron_schedule": "Every 2h, 8am-10pm daily (0 8-22/2 * * *)",
        "next_scheduled_run": next_scheduled_run().isoformat(),
        "server_time": datetime.now().isoformat(),
    }


def _log_error_message(episode_id: int) -> str:
    """
    worker.py only logs failures to pipeline.log (episodes table has no
    error-message column) - so this is a best-effort scrape of the most
    recent "ERROR processing episode {id}: ..." line for that episode.
    Some of those lines are enormous (a raised subprocess error can embed
    the full extraction prompt), hence the truncation.
    """
    log_path = Path(__file__).parent / "pipeline.log"
    if not log_path.exists():
        return "processing failed (see pipeline.log)"
    pattern = re.compile(rf"ERROR processing episode {episode_id}: (.*)")
    last_match = None
    with log_path.open(errors="ignore") as f:
        for line in f:
            m = pattern.search(line)
            if m:
                last_match = m.group(1).strip()
    if not last_match:
        return "processing failed (see pipeline.log)"
    return last_match if len(last_match) <= 240 else last_match[:240] + "…"


@app.get("/api/dev/status")
def get_dev_console_status():
    """
    Backs the standalone dev console (static/dev.html). Distinct from
    /api/dev_status above, which feeds the in-app DevPanel with a
    different shape - this one matches dev.html's documented contract.
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT * FROM pipeline_status WHERE id = 1")
    rows = dict_rows(cur)
    ps = rows[0] if rows else {}

    run_started = ps.get("run_started_at")
    last_finished = ps.get("last_finished_at")
    last_duration_ms = None
    if run_started and last_finished and last_finished >= run_started:
        last_duration_ms = int((last_finished - run_started).total_seconds() * 1000)

    cur.execute(
        "SELECT count(*) FROM quotes q JOIN episodes e ON e.id = q.episode_id WHERE e.processed_at = %s",
        (run_started,),
    )
    count_last_run = cur.fetchone()[0] if run_started else 0

    cur.execute("SELECT count(*) FROM quotes WHERE created_at::date = CURRENT_DATE")
    count_today = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM quotes")
    count_total = cur.fetchone()[0]

    cur.execute(
        """
        SELECT pod.id, pod.name, pod.active,
               MAX(e.processed_at) FILTER (WHERE e.status = 'extracted') AS last_ok_at,
               count(*) FILTER (WHERE e.status = 'error' AND e.processed_at = %s) AS errors_last_run,
               (SELECT count(*) FROM quotes q2 JOIN episodes e2 ON e2.id = q2.episode_id
                WHERE e2.podcast_id = pod.id AND e2.processed_at = %s) AS items_last_run
        FROM podcasts pod
        LEFT JOIN episodes e ON e.podcast_id = pod.id
        GROUP BY pod.id, pod.name, pod.active
        ORDER BY pod.name
        """,
        (run_started, run_started),
    )
    sources = []
    for pod_id, name, active, last_ok_at, errors_last_run, items_last_run in cur.fetchall():
        source = {
            "id": pod_id,
            "name": name,
            "enabled": active,
            "lastOkAt": last_ok_at.isoformat() if last_ok_at else None,
            "lastRunAt": ps.get("last_poll_at").isoformat() if ps.get("last_poll_at") else None,
            "itemsLastRun": items_last_run,
        }
        if errors_last_run:
            source["state"] = "error"
            source["message"] = f"{errors_last_run} episode(s) failed in last run"
        sources.append(source)

    cur.execute(
        """
        SELECT e.processed_at, pod.name, e.title, e.status,
               (SELECT count(*) FROM quotes q WHERE q.episode_id = e.id) AS quote_count
        FROM episodes e JOIN podcasts pod ON pod.id = e.podcast_id
        WHERE e.processed_at IS NOT NULL
        ORDER BY e.processed_at DESC
        LIMIT 20
        """
    )
    imports = [
        {
            "at": at.isoformat(),
            "source": name,
            "kind": "episode",
            "count": quote_count,
            "status": "error" if status == "error" else "ok",
            "detail": title,
        }
        for at, name, title, status, quote_count in cur.fetchall()
    ]

    cur.execute(
        """
        SELECT e.id, e.processed_at, pod.name, e.title
        FROM episodes e JOIN podcasts pod ON pod.id = e.podcast_id
        WHERE e.status = 'error'
        ORDER BY e.processed_at DESC NULLS LAST
        LIMIT 20
        """
    )
    error_rows = cur.fetchall()
    errors = [
        {
            "at": at.isoformat() if at else None,
            "source": name,
            "message": f"{title}: {_log_error_message(episode_id)}",
        }
        for episode_id, at, name, title in error_rows
    ]

    cur.close()
    conn.close()

    return {
        "poller": {
            "state": "running" if ps.get("is_running") else "idle",
            "lastRunAt": last_finished.isoformat() if last_finished else None,
            "lastDurationMs": last_duration_ms,
            "nextRunAt": next_scheduled_run().isoformat(),
            "intervalSec": CRON_INTERVAL_SEC,
            "startedAt": SERVER_STARTED_AT.isoformat(),
        },
        "counts": {"lastRun": count_last_run, "today": count_today, "total": count_total},
        "sources": sources,
        "imports": imports,
        "errors": errors,
    }


def _run_poll_background(podcast_id):
    try:
        poller.poll_all(podcast_id=podcast_id)
    finally:
        _poll_lock.release()


@app.post("/api/dev/run")
def trigger_dev_run(body: dict = Body(default={})):
    """
    Kicks off poller.py's RSS check in the background - the same thing
    run_pipeline.sh does before invoking worker.py on its cron schedule.
    Deliberately does NOT trigger worker.py: transcription/extraction
    takes on the order of an hour per episode (see
    pipeline_status.avg_seconds_per_episode), so running it synchronously
    - or even fire-and-forget from a web request - isn't appropriate for
    a "run now" button. This button only does what its label says: runs
    the poller. Extraction still happens on the regular cron schedule.
    """
    source = body.get("source")
    podcast_id = None
    if source is not None:
        try:
            podcast_id = int(source)
        except (TypeError, ValueError):
            return {"ok": False, "message": f"unknown source {source!r}"}

    if not _poll_lock.acquire(blocking=False):
        return {"ok": False, "message": "a poll is already in progress"}

    threading.Thread(target=_run_poll_background, args=(podcast_id,), daemon=True).start()
    return {
        "ok": True,
        "message": "poll started - checking RSS feed(s) for new episodes "
                    "(extraction still runs on the regular schedule)",
    }


@app.get("/api/reliability")
def get_reliability():
    """
    First pass at source-reliability tracking (experimental - see
    docs/PROJECT_HISTORY.md). Only "first to cover" is real, computed
    data; it's an approximation (same player mentioned != same underlying
    news event, since we don't do semantic event-clustering yet - see
    docs). Outcome/accuracy tracking isn't included here at all: it
    can't be computed until real season stats exist. The frontend shows
    a clearly-labeled mock table for that part.
    """
    conn = get_conn()
    cur = conn.cursor()
    week = current_content_week()

    cur.execute(
        """
        WITH player_podcast_first AS (
            SELECT q.player_id, p.full_name, pod.id AS podcast_id, pod.name AS podcast_name,
                   MIN(e.published_at) AS first_mention_at
            FROM quotes q
            JOIN episodes e ON q.episode_id = e.id
            JOIN podcasts pod ON e.podcast_id = pod.id
            JOIN players p ON q.player_id = p.id
            WHERE q.content_week = %s
            GROUP BY q.player_id, p.full_name, pod.id, pod.name
        ),
        shared AS (
            SELECT player_id FROM player_podcast_first
            GROUP BY player_id HAVING count(DISTINCT podcast_id) >= 2
        ),
        ranked AS (
            SELECT ppf.*,
                   ROW_NUMBER() OVER (PARTITION BY ppf.player_id ORDER BY first_mention_at ASC) AS rn
            FROM player_podcast_first ppf
            JOIN shared s ON s.player_id = ppf.player_id
        )
        SELECT podcast_name, full_name, first_mention_at
        FROM ranked
        WHERE rn = 1
        ORDER BY first_mention_at ASC
        """,
        (week,),
    )
    first_to_cover_detail = dict_rows(cur)

    tally = defaultdict(int)
    for row in first_to_cover_detail:
        tally[row["podcast_name"]] += 1

    cur.close()
    conn.close()

    return {
        "week": str(week),
        "first_to_cover_tally": [{"podcast": k, "first_mentions": v} for k, v in sorted(tally.items(), key=lambda kv: -kv[1])],
        "first_to_cover_detail": first_to_cover_detail,
        "shared_player_count": len(first_to_cover_detail),
    }


@app.get("/api/sentiment_trend")
def get_sentiment_trend():
    """
    Real data only - no mocking here. Sentiment mix (rising/falling/
    neutral mention counts) per player per content_week, for players with
    quotes in 2+ distinct weeks (a single week isn't a trend). Naturally
    grows more useful as more weeks accumulate; nothing to backfill or
    fake in the meantime.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT q.player_id, p.full_name, p.team, p.position, q.content_week,
               COUNT(*) FILTER (WHERE q.sentiment = 'rising') AS rising,
               COUNT(*) FILTER (WHERE q.sentiment = 'falling') AS falling,
               COUNT(*) FILTER (WHERE q.sentiment = 'neutral') AS neutral,
               COUNT(DISTINCT e.podcast_id) AS podcast_count
        FROM quotes q
        JOIN players p ON q.player_id = p.id
        JOIN episodes e ON q.episode_id = e.id
        WHERE q.player_id IS NOT NULL
        GROUP BY q.player_id, p.full_name, p.team, p.position, q.content_week
        ORDER BY q.content_week ASC
        """
    )
    rows = dict_rows(cur)
    cur.close()
    conn.close()

    by_player = defaultdict(list)
    for r in rows:
        by_player[r["player_id"]].append(r)

    all_weeks = sorted({str(r["content_week"]) for r in rows})

    players = []
    for player_id, weekrows in by_player.items():
        if len(weekrows) < 2:
            continue
        first = weekrows[0]
        weeks = {
            str(r["content_week"]): {
                "rising": r["rising"], "falling": r["falling"],
                "neutral": r["neutral"], "podcast_count": r["podcast_count"],
            }
            for r in weekrows
        }
        total_mentions = sum(w["rising"] + w["falling"] + w["neutral"] for w in weeks.values())
        players.append({
            "player_id": player_id, "full_name": first["full_name"],
            "team": first["team"], "position": first["position"],
            "weeks": weeks, "total_mentions": total_mentions,
        })

    players.sort(key=lambda p: -p["total_mentions"])

    return {"weeks": all_weeks, "players": players}


@app.get("/api/recent_buzz")
def get_recent_buzz(weeks: int = Query(3, ge=1, le=12)):
    """
    Rolling digest of the last N content_weeks of quotes, grouped by
    player - for in-season start/sit calls where "this week alone"
    undersells a player who's had sustained buzz building over several
    weeks. Real data; same shape as /api/news (frontend reuses the same
    grouping component) but widens the window instead of a single week.
    Window shrinks gracefully if fewer than `weeks` weeks exist yet.
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT content_week FROM quotes ORDER BY content_week DESC LIMIT %s", (weeks,))
    included_weeks = [r[0] for r in cur.fetchall()]
    if not included_weeks:
        cur.close()
        conn.close()
        return {"weeks_included": [], "quotes": []}

    cur.execute(
        """
        SELECT p.full_name, p.team, p.position, q.quote_text, q.speaker,
               q.tags, q.sentiment, q.fantasy_relevance, q.match_confidence,
               q.created_at, q.content_week,
               pod.name AS source_podcast, e.published_at AS source_published_at
        FROM quotes q
        LEFT JOIN players p ON q.player_id = p.id
        LEFT JOIN episodes e ON q.episode_id = e.id
        LEFT JOIN podcasts pod ON e.podcast_id = pod.id
        WHERE q.content_week = ANY(%s)
        ORDER BY q.created_at DESC
        """,
        (included_weeks,),
    )
    quotes = dict_rows(cur)
    cur.close()
    conn.close()
    return {"weeks_included": [str(w) for w in sorted(included_weeks)], "quotes": quotes}


@app.get("/api/rankings")
def get_rankings():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.full_name, p.team, p.position, r.value AS adp,
               CEIL(r.value / 10) AS draft_round, r.fetched_at
        FROM rankings r
        JOIN players p ON r.player_id = p.id
        WHERE r.rank_type = 'adp' AND r.season = %s
        ORDER BY r.value ASC
        """,
        (SEASON,),
    )
    result = dict_rows(cur)
    cur.close()
    conn.close()
    return result


@app.get("/api/round_focus")
def get_round_focus(round: int | None = Query(None, ge=1), days: int = Query(5, ge=1, le=30)):
    """
    Buzz used to be scoped to the current content_week only, which left
    most players showing "No mentions this week" even when real quotes
    about them existed a few days ago - a wide content_week miss reads
    the same as genuinely no coverage. Widened to a rolling day-window
    (default 5, matching the Recent Buzz digest's pattern) instead.
    """
    conn = get_conn()
    cur = conn.cursor()
    since = datetime.now() - timedelta(days=days)
    round_filter = "AND CEIL(r.value / 10) = %s" if round is not None else "AND CEIL(r.value / 10) <= 16"
    params = [SEASON, since] + ([round] if round is not None else [])
    cur.execute(
        f"""
        SELECT
            p.id AS player_id, p.full_name, p.team, p.position,
            r.value AS adp, CEIL(r.value / 10) AS draft_round,
            COALESCE(
                json_agg(
                    json_build_object(
                        'quote', q.quote_text,
                        'sentiment', q.sentiment,
                        'tags', q.tags,
                        'speaker', q.speaker,
                        'created_at', q.created_at
                    ) ORDER BY q.created_at DESC
                ) FILTER (WHERE q.quote_text IS NOT NULL),
                '[]'
            ) AS weekly_quotes
        FROM players p
        JOIN rankings r ON r.player_id = p.id AND r.rank_type = 'adp' AND r.season = %s
        LEFT JOIN quotes q ON q.player_id = p.id AND q.created_at >= %s
        WHERE r.value IS NOT NULL {round_filter}
        GROUP BY p.id, p.full_name, p.team, p.position, r.value
        ORDER BY r.value ASC
        """,
        params,
    )
    result = dict_rows(cur)
    cur.close()
    conn.close()
    return result


@app.get("/api/salary")
def get_salary():
    """
    Derives auction dollar values for a 10-team, $200 league from ADP -
    there's no real auction-value data source wired up. Every rostered
    player gets a $1 floor; the rest of the pool is split by ADP rank
    using exponential decay (weight = e^(-k * (rank-1)), k=0.03, tuned
    so rank 1 lands ~$55 and rank 60 ~$10, matching how real auction
    values actually curve - steep at the top, flat by mid-bench). A
    straight linear split on raw ADP was tried first and rejected: it
    compressed the whole first round to ~$23 each, since ADP 1.6 and
    9.9 are numerically close relative to the ADP range - realistic
    auction value drops off far faster than that at the top.

    Pay meter (pay_up / pay_down / pay_average) reuses the same
    distinct-podcast buzz signal as the Hot/Cold Meter's Buzz column.
    """
    conn = get_conn()
    cur = conn.cursor()
    week = current_content_week()

    cur.execute(
        """
        SELECT p.id, p.full_name, p.team, p.position, r.value AS adp, CEIL(r.value / 10) AS draft_round
        FROM players p
        JOIN rankings r ON r.player_id = p.id AND r.rank_type = 'adp' AND r.season = %s
        WHERE r.value IS NOT NULL AND CEIL(r.value / 10) <= %s
        ORDER BY r.value ASC
        """,
        (SEASON, ROSTER_SPOTS),
    )
    players = cur.fetchall()

    cur.execute(
        """
        SELECT q.player_id,
               COUNT(DISTINCT e.podcast_id) AS podcast_count,
               COUNT(*) FILTER (WHERE q.sentiment = 'rising') AS rising_count,
               COUNT(*) FILTER (WHERE q.sentiment = 'falling') AS falling_count,
               COALESCE(
                   json_agg(
                       json_build_object(
                           'quote', q.quote_text, 'sentiment', q.sentiment,
                           'tags', q.tags, 'speaker', q.speaker, 'created_at', q.created_at
                       )
                   ) FILTER (WHERE q.quote_text IS NOT NULL),
                   '[]'
               ) AS weekly_quotes
        FROM quotes q
        JOIN episodes e ON q.episode_id = e.id
        WHERE q.content_week = %s AND q.player_id IS NOT NULL
        GROUP BY q.player_id
        """,
        (week,),
    )
    buzz_cols = [d[0] for d in cur.description]
    buzz_by_player = {row[0]: dict(zip(buzz_cols, row)) for row in cur.fetchall()}

    cur.close()
    conn.close()

    values_by_id = compute_auction_values(players)

    result = []
    for (player_id, full_name, team, position, adp, draft_round) in players:
        auction_value = values_by_id[player_id]
        buzz = buzz_by_player.get(player_id, {})
        rising = buzz.get("rising_count", 0)
        falling = buzz.get("falling_count", 0)
        if rising > falling:
            pay_meter = "pay_up"
        elif falling > rising:
            pay_meter = "pay_down"
        else:
            pay_meter = "pay_average"
        result.append({
            "player_id": player_id, "full_name": full_name, "team": team, "position": position,
            "adp": adp, "draft_round": draft_round, "auction_value": auction_value,
            "pay_meter": pay_meter, "buzz_podcast_count": buzz.get("podcast_count", 0),
            "weekly_quotes": buzz.get("weekly_quotes", []),
        })

    return {
        "league_teams": LEAGUE_TEAMS, "league_budget": LEAGUE_BUDGET,
        "roster_spots": ROSTER_SPOTS, "players": result,
    }


@app.get("/api/players/search")
def search_players(q: str = Query(..., min_length=1)):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.id, p.full_name, p.team, p.position, r.value AS adp
        FROM players p
        LEFT JOIN rankings r ON r.player_id = p.id AND r.rank_type = 'adp' AND r.season = %s
        WHERE p.full_name ILIKE %s
        ORDER BY r.value ASC NULLS LAST
        LIMIT 15
        """,
        (SEASON, f"%{q}%"),
    )
    result = dict_rows(cur)
    cur.close()
    conn.close()
    return result


@app.get("/api/players/{player_id}")
def get_player_detail(player_id: int):
    """
    The "isolated panel" view - everything known about one player in one
    place: ADP/value (same computation as /api/salary, via the shared
    helper so the two numbers can never disagree), bye week, and every
    quote ever recorded about them (no day/week window - this is meant
    to be the definitive view, not a recent-activity digest).
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id, full_name, team, position FROM players WHERE id = %s", (player_id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {"error": "player not found"}
    player = {"id": row[0], "full_name": row[1], "team": row[2], "position": row[3]}

    cur.execute(
        """
        SELECT p.id, r.value
        FROM players p
        JOIN rankings r ON r.player_id = p.id AND r.rank_type = 'adp' AND r.season = %s
        WHERE r.value IS NOT NULL AND CEIL(r.value / 10) <= %s
        ORDER BY r.value ASC
        """,
        (SEASON, ROSTER_SPOTS),
    )
    ranked = cur.fetchall()
    values = compute_auction_values(ranked)
    player["adp"] = None
    player["draft_round"] = None
    player["auction_value"] = values.get(player_id)
    for pid, adp in ranked:
        if pid == player_id:
            player["adp"] = adp
            player["draft_round"] = math.ceil(adp / 10)
            break

    cur.execute(
        """
        SELECT ts.week FROM team_schedule ts
        WHERE ts.team = %s AND ts.season = %s AND ts.opponent IS NULL
        """,
        (player["team"], SEASON),
    )
    bye = cur.fetchone()
    player["bye_week"] = bye[0] if bye else None

    cur.execute(
        """
        SELECT q.quote_text, q.speaker, q.tags, q.sentiment, q.fantasy_relevance,
               q.match_confidence, q.created_at, q.content_week,
               pod.name AS source_podcast, e.published_at AS source_published_at
        FROM quotes q
        LEFT JOIN episodes e ON q.episode_id = e.id
        LEFT JOIN podcasts pod ON e.podcast_id = pod.id
        WHERE q.player_id = %s
        ORDER BY q.created_at DESC
        """,
        (player_id,),
    )
    quotes = dict_rows(cur)
    player["quotes"] = quotes
    player["podcast_count"] = len({q["source_podcast"] for q in quotes if q["source_podcast"]})
    player["rising_count"] = sum(1 for q in quotes if q["sentiment"] == "rising")
    player["falling_count"] = sum(1 for q in quotes if q["sentiment"] == "falling")

    cur.close()
    conn.close()
    return player


# Positions where stashing a same-team backup ("handcuff") is a real
# fantasy strategy - RB is the classic case (bellcow goes down, backup
# becomes a league-winner), WR/TE included since a vacated target share
# is also chaseable.
HANDCUFF_POSITIONS = ("RB", "WR", "TE")
ROSTER_NEWS_LOOKBACK_DAYS = 21
WAIVER_LOOKBACK_DAYS = 14


def _match_roster_name(cur, raw_name: str):
    """
    Resolves one pasted roster line to a player_id. Same escalation as
    stock-dashboard's ticker resolver: exact name, then a known alias,
    then trigram fuzzy similarity - in that order of confidence.
    """
    name = raw_name.strip()
    if not name:
        return None, "unmatched"

    # Same name can belong to multiple players (a skill-position player and
    # an unrelated DL/LB, e.g. two "Justin Jefferson"s) - prefer whichever
    # candidate has a current ADP, since a roster is inherently about
    # fantasy-relevant players. NULLS LAST so an unranked player is still
    # matched when they're the only candidate.
    cur.execute(
        """
        SELECT p.id
        FROM players p
        LEFT JOIN rankings r ON r.player_id = p.id AND r.rank_type = 'adp' AND r.season = %s
        WHERE lower(p.full_name) = lower(%s)
        ORDER BY r.value ASC NULLS LAST
        LIMIT 1
        """,
        (SEASON, name),
    )
    row = cur.fetchone()
    if row:
        return row[0], "high"

    cur.execute(
        """
        SELECT pa.player_id
        FROM player_aliases pa
        LEFT JOIN rankings r ON r.player_id = pa.player_id AND r.rank_type = 'adp' AND r.season = %s
        WHERE lower(pa.alias) = lower(%s)
        ORDER BY r.value ASC NULLS LAST
        LIMIT 1
        """,
        (SEASON, name),
    )
    row = cur.fetchone()
    if row:
        return row[0], "high"

    cur.execute(
        """
        SELECT p.id, similarity(p.full_name, %s) AS sim
        FROM players p
        LEFT JOIN rankings r ON r.player_id = p.id AND r.rank_type = 'adp' AND r.season = %s
        ORDER BY sim DESC, r.value ASC NULLS LAST
        LIMIT 1
        """,
        (name, SEASON),
    )
    row = cur.fetchone()
    if row and row[1] and row[1] > 0.4:
        return row[0], "low"

    return None, "unmatched"


@app.get("/api/roster/teams")
def list_fantasy_teams():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT t.id, t.name, t.created_at,
               count(rp.id) AS player_count,
               count(rp.id) FILTER (WHERE rp.player_id IS NULL) AS unmatched_count
        FROM fantasy_teams t
        LEFT JOIN roster_players rp ON rp.team_id = t.id
        GROUP BY t.id
        ORDER BY t.id
        """
    )
    result = dict_rows(cur)
    cur.close()
    conn.close()
    return result


@app.post("/api/roster/teams")
def create_fantasy_team(body: dict = Body(...)):
    name = (body.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO fantasy_teams (name) VALUES (%s) RETURNING id", (name,))
    team_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return {"id": team_id, "name": name}


@app.delete("/api/roster/teams/{team_id}")
def delete_fantasy_team(team_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM fantasy_teams WHERE id = %s", (team_id,))
    conn.commit()
    cur.close()
    conn.close()
    return {"ok": True}


MIN_SCAN_NAME_LEN = 6  # skip very short names/aliases - too likely to false-positive as a substring
MAX_FALLBACK_LINE_LEN = 60  # a "clean" one-name paste line; anything longer is stat-table noise, not a name


def _scan_known_names(cur, raw_text: str):
    """
    Finds every known player mentioned anywhere in the pasted text by
    substring, rather than first trying to split the paste into clean
    one-player rows. Needed because a roster copy-pasted straight out of
    a fantasy site's table (Yahoo, ESPN, ...) usually isn't clean lines -
    stat columns are comma-separated too (so comma-splitting shreds it),
    and the player's name is often glued directly onto trailing tooltip
    text with no separating space (e.g. "Joe BurrowVideo ForecastPlayer
    Note, Cin - QB, Sun 1:00pm vs TB, 6, -, 24.92, 96%, 100%, ..."). This
    just looks for real player names inside that noise instead of trying
    to parse the noise's structure.

    Returns [(player_id, matched_name, position_in_text), ...] sorted by
    where each name first appears, so the roster comes back roughly in
    its original (draft/depth-chart) order.
    """
    cur.execute(
        """
        SELECT id, full_name AS name FROM players WHERE length(full_name) >= %s
        UNION ALL
        SELECT player_id, alias AS name FROM player_aliases WHERE length(alias) >= %s
        """,
        (MIN_SCAN_NAME_LEN, MIN_SCAN_NAME_LEN),
    )
    candidates = cur.fetchall()

    # Fantasy sites routinely display names without the generational suffix
    # ("Kenneth Walker" for "Kenneth Walker III") - add the stripped form as
    # an extra candidate for the same player_id so that still matches.
    suffix_pattern = re.compile(r"\s+(Jr\.?|Sr\.?|I{2,3}|IV)$", re.IGNORECASE)
    extra = []
    for player_id, name in candidates:
        stripped = suffix_pattern.sub("", name).strip()
        # Require the stripped form to still be "First Last" (a space in
        # it) - a bare surname like "Walker" (from alias "Walker Jr.") is
        # far too generic and false-positives against any unrelated text
        # containing that word.
        if stripped != name and " " in stripped and len(stripped) >= MIN_SCAN_NAME_LEN:
            extra.append((player_id, stripped))
    candidates = candidates + extra

    candidates.sort(key=lambda row: -len(row[1]))  # longest name first, so "Michael Thomas" beats "Michael"

    lower_text = raw_text.lower()
    found = {}
    for player_id, name in candidates:
        if player_id in found:
            continue
        needle = name.lower()
        start = 0
        while True:
            pos = lower_text.find(needle, start)
            if pos == -1:
                break
            before = lower_text[pos - 1] if pos > 0 else " "
            # Only require a non-letter boundary *before* the match (start of
            # a name) - deliberately not checking what follows, since that's
            # exactly where the glued-on junk like "...BurrowVideo Forecast"
            # shows up.
            if not before.isalpha():
                found[player_id] = (name, pos)
                break
            start = pos + 1

    return sorted(((pid, name, pos) for pid, (name, pos) in found.items()), key=lambda t: t[2])


@app.post("/api/roster/teams/{team_id}/import")
def import_roster(team_id: int, body: dict = Body(...)):
    """
    Takes a raw pasted roster and matches it against the players table.
    Replaces this team's existing roster wholesale each call, so re-pasting
    an updated list (add/drop, corrected typo) just works without a
    separate edit flow.

    Two passes: first scan the whole paste for known player names by
    substring (handles messy fantasy-site table copies - see
    _scan_known_names). Then, separately, walk each short newline-separated
    line through the old exact/alias/fuzzy matcher, skipping anything
    already found - this catches a typo in an otherwise-clean one-name-
    per-line paste (which the substring scan can't, since it only finds
    exact known names) without re-introducing noise from a giant glued
    blob (which won't produce any short lines to try).
    """
    raw = body.get("players", "")
    raw_text = raw if isinstance(raw, str) else "\n".join(str(n) for n in raw if str(n).strip())

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM fantasy_teams WHERE id = %s", (team_id,))
    if not cur.fetchone():
        cur.close()
        conn.close()
        return {"error": "team not found"}

    cur.execute("DELETE FROM roster_players WHERE team_id = %s", (team_id,))

    results = []
    matched_ids = set()

    for player_id, name, _pos in _scan_known_names(cur, raw_text):
        cur.execute(
            "INSERT INTO roster_players (team_id, player_id, raw_name, match_confidence) VALUES (%s, %s, %s, 'high')",
            (team_id, player_id, name),
        )
        results.append({"raw_name": name, "player_id": player_id, "match_confidence": "high"})
        matched_ids.add(player_id)

    scanned_names_lower = [r["raw_name"].lower() for r in results]
    for line in (ln.strip() for ln in raw_text.split("\n")):
        if not line or len(line) > MAX_FALLBACK_LINE_LEN:
            continue
        if any(name in line.lower() for name in scanned_names_lower):
            continue
        player_id, confidence = _match_roster_name(cur, line)
        if player_id and player_id in matched_ids:
            continue
        if player_id:
            matched_ids.add(player_id)
        cur.execute(
            "INSERT INTO roster_players (team_id, player_id, raw_name, match_confidence) VALUES (%s, %s, %s, %s)",
            (team_id, player_id, line, confidence),
        )
        results.append({"raw_name": line, "player_id": player_id, "match_confidence": confidence})

    conn.commit()
    cur.close()
    conn.close()
    matched_count = sum(1 for r in results if r["player_id"])
    return {"team_id": team_id, "imported": len(results), "matched": matched_count, "results": results}


@app.get("/api/roster/teams/{team_id}")
def get_fantasy_team(team_id: int):
    """
    The "My Team" view: each rostered player with recent podcast buzz and
    injury status, plus two recommendation sections - same-team/same-position
    handcuffs for your RB/WR/TE (ranked by ADP, since there's no real snap-share
    or depth-chart data source wired up yet - see PROJECT_HISTORY), and general
    waiver targets (undrafted-by-you players with recent rising buzz). Both are
    buzz/ADP-based approximations, not a stats-backed "better player" comparison -
    player_stats is empty (no nflverse feed wired up), so that comparison can't be
    made honestly yet.
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id, name FROM fantasy_teams WHERE id = %s", (team_id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return {"error": "team not found"}
    team = {"id": row[0], "name": row[1]}

    cur.execute(
        """
        SELECT rp.id, rp.raw_name, rp.match_confidence, p.id, p.full_name, p.team, p.position, r.value
        FROM roster_players rp
        LEFT JOIN players p ON p.id = rp.player_id
        LEFT JOIN rankings r ON r.player_id = p.id AND r.rank_type = 'adp' AND r.season = %s
        WHERE rp.team_id = %s
        ORDER BY rp.id
        """,
        (SEASON, team_id),
    )
    roster_rows = cur.fetchall()
    rostered_player_ids = [r[3] for r in roster_rows if r[3] is not None]

    cur.execute(
        """
        SELECT player_id, report_status, practice_status, description
        FROM injuries
        WHERE player_id = ANY(%s) AND season = %s
        ORDER BY week DESC NULLS LAST
        """,
        (rostered_player_ids or [0], SEASON),
    )
    injury_by_player = {}
    for pid, status, practice, desc in cur.fetchall():
        if pid not in injury_by_player:
            injury_by_player[pid] = {"report_status": status, "practice_status": practice, "description": desc}

    cur.execute(
        """
        SELECT q.player_id, q.quote_text, q.speaker, q.tags, q.sentiment, q.fantasy_relevance,
               q.created_at, pod.name AS source_podcast, e.published_at AS source_published_at
        FROM quotes q
        LEFT JOIN episodes e ON q.episode_id = e.id
        LEFT JOIN podcasts pod ON e.podcast_id = pod.id
        WHERE q.player_id = ANY(%s) AND q.created_at >= now() - make_interval(days => %s)
        ORDER BY q.created_at DESC
        """,
        (rostered_player_ids or [0], ROSTER_NEWS_LOOKBACK_DAYS),
    )
    quotes_by_player = defaultdict(list)
    for pid, quote_text, speaker, tags, sentiment, relevance, created_at, podcast, published_at in cur.fetchall():
        quotes_by_player[pid].append({
            "quote_text": quote_text, "speaker": speaker, "tags": tags, "sentiment": sentiment,
            "fantasy_relevance": relevance, "created_at": created_at.isoformat() if created_at else None,
            "source_podcast": podcast, "source_published_at": published_at.isoformat() if published_at else None,
        })

    roster = []
    for rp_id, raw_name, confidence, pid, full_name, pteam, pos, adp in roster_rows:
        roster.append({
            "roster_player_id": rp_id, "raw_name": raw_name, "match_confidence": confidence,
            "player_id": pid, "full_name": full_name, "team": pteam, "position": pos,
            "adp": float(adp) if adp is not None else None,
            "injury": injury_by_player.get(pid),
            "quotes": quotes_by_player.get(pid, []),
        })

    handcuffs = []
    skill_ids = [pid for pid, pos in [(rw[3], rw[6]) for rw in roster_rows] if pid and pos in HANDCUFF_POSITIONS]
    if skill_ids:
        cur.execute(
            """
            SELECT pl.id, pl.full_name, cand.id, cand.full_name, cand.position, cand.team, r.value
            FROM players pl
            JOIN players cand ON cand.team = pl.team AND cand.position = pl.position AND cand.id != pl.id
            LEFT JOIN rankings r ON r.player_id = cand.id AND r.rank_type = 'adp' AND r.season = %s
            WHERE pl.id = ANY(%s) AND cand.active = true AND NOT (cand.id = ANY(%s))
            ORDER BY pl.full_name, r.value ASC NULLS LAST
            """,
            (SEASON, skill_ids, rostered_player_ids or [0]),
        )
        by_rostered = defaultdict(list)
        for rostered_id, rostered_name, cand_id, cand_name, cand_pos, cand_team, cand_adp in cur.fetchall():
            by_rostered[(rostered_id, rostered_name)].append({
                "player_id": cand_id, "full_name": cand_name, "position": cand_pos,
                "team": cand_team, "adp": float(cand_adp) if cand_adp is not None else None,
            })
        for (rostered_id, rostered_name), candidates in by_rostered.items():
            handcuffs.append({
                "rostered_player_id": rostered_id,
                "rostered_name": rostered_name,
                "candidates": candidates[:2],
            })

    cur.execute(
        """
        SELECT p.id, p.full_name, p.team, p.position, r.value AS adp,
               count(*) FILTER (WHERE q.sentiment = 'rising') AS rising_count,
               count(DISTINCT pod.id) AS podcast_count,
               max(q.created_at) AS latest
        FROM quotes q
        JOIN players p ON p.id = q.player_id
        LEFT JOIN episodes e ON q.episode_id = e.id
        LEFT JOIN podcasts pod ON e.podcast_id = pod.id
        LEFT JOIN rankings r ON r.player_id = p.id AND r.rank_type = 'adp' AND r.season = %s
        WHERE q.created_at >= now() - make_interval(days => %s)
          AND q.sentiment = 'rising'
          AND NOT (p.id = ANY(%s))
        GROUP BY p.id, p.full_name, p.team, p.position, r.value
        ORDER BY rising_count DESC, podcast_count DESC, latest DESC
        LIMIT 15
        """,
        (SEASON, WAIVER_LOOKBACK_DAYS, rostered_player_ids or [0]),
    )
    waiver_targets = [
        {
            "player_id": pid, "full_name": name, "team": pteam, "position": pos,
            "adp": float(adp) if adp is not None else None,
            "rising_count": rising, "podcast_count": podcasts,
            "latest": latest.isoformat() if latest else None,
        }
        for pid, name, pteam, pos, adp, rising, podcasts, latest in cur.fetchall()
    ]

    cur.close()
    conn.close()

    return {
        "team": team,
        "roster": roster,
        "handcuffs": handcuffs,
        "waiver_targets": waiver_targets,
    }


@app.get("/api/news")
def get_news():
    conn = get_conn()
    cur = conn.cursor()
    week = current_content_week()
    cur.execute(
        """
        SELECT p.full_name, p.team, p.position, q.quote_text, q.speaker,
               q.tags, q.sentiment, q.fantasy_relevance, q.match_confidence,
               q.created_at,
               pod.name AS source_podcast, e.title AS source_episode,
               e.published_at AS source_published_at, e.processed_at AS source_downloaded_at
        FROM quotes q
        LEFT JOIN players p ON q.player_id = p.id
        LEFT JOIN episodes e ON q.episode_id = e.id
        LEFT JOIN podcasts pod ON e.podcast_id = pod.id
        WHERE q.content_week = %s
        ORDER BY q.created_at DESC
        """,
        (week,),
    )
    result = dict_rows(cur)
    cur.close()
    conn.close()
    return result


@app.get("/api/injuries")
def get_injuries():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.full_name, p.team, p.position, i.report_status,
               i.practice_status, i.week, i.fetched_at
        FROM injuries i
        JOIN players p ON i.player_id = p.id
        WHERE i.season = %s
        ORDER BY i.week DESC NULLS LAST, p.full_name
        LIMIT 100
        """,
        (SEASON,),
    )
    result = dict_rows(cur)
    cur.close()
    conn.close()
    return result


@app.get("/api/hot_cold")
def get_hot_cold():
    conn = get_conn()
    cur = conn.cursor()
    week = current_content_week()
    cur.execute(
        """
        WITH ranked AS (
            SELECT player_id, week, stat_value,
                   ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY week DESC) AS rn
            FROM player_stats
            WHERE stat_name = 'snap_pct' AND season = %s
        ),
        buzz AS (
            -- Distinct podcasts, not raw quote count: 3 mentions from one
            -- show repeating itself isn't the same signal as 3 different
            -- shows independently converging on the same read.
            SELECT q.player_id,
                   COUNT(DISTINCT e.podcast_id) AS podcast_count,
                   COUNT(*) FILTER (WHERE q.sentiment = 'rising') AS rising_count,
                   COUNT(*) FILTER (WHERE q.sentiment = 'falling') AS falling_count
            FROM quotes q
            JOIN episodes e ON q.episode_id = e.id
            WHERE q.content_week = %s AND q.player_id IS NOT NULL
            GROUP BY q.player_id
        )
        SELECT p.full_name, p.team, p.position,
               latest.stat_value AS current_snap_pct,
               prior.stat_value AS prior_snap_pct,
               (latest.stat_value - prior.stat_value) AS delta,
               COALESCE(buzz.podcast_count, 0) AS buzz_podcast_count,
               COALESCE(buzz.rising_count, 0) AS buzz_rising_count,
               COALESCE(buzz.falling_count, 0) AS buzz_falling_count
        FROM ranked latest
        JOIN ranked prior ON prior.player_id = latest.player_id AND prior.rn = 2
        JOIN players p ON p.id = latest.player_id
        LEFT JOIN buzz ON buzz.player_id = p.id
        WHERE latest.rn = 1
        ORDER BY ABS(latest.stat_value - prior.stat_value) DESC
        LIMIT 30
        """,
        (SEASON, week),
    )
    result = dict_rows(cur)
    cur.close()
    conn.close()
    return result


@app.get("/api/waiver_adds")
def get_waiver_adds():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        WITH ranked AS (
            SELECT player_id, week, stat_value,
                   ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY week DESC) AS rn
            FROM player_stats
            WHERE stat_name = 'snap_pct' AND season = %s
        )
        SELECT p.full_name, p.team, p.position,
               (latest.stat_value - prior.stat_value) AS snap_trend,
               r.value AS adp
        FROM ranked latest
        JOIN ranked prior ON prior.player_id = latest.player_id AND prior.rn = 2
        JOIN players p ON p.id = latest.player_id
        LEFT JOIN rankings r ON r.player_id = p.id AND r.rank_type = 'adp' AND r.season = %s
        WHERE latest.rn = 1
          AND (latest.stat_value - prior.stat_value) > 0.1
          AND (r.value IS NULL OR r.value > 80)
        ORDER BY snap_trend DESC
        LIMIT 20
        """,
        (SEASON, SEASON),
    )
    result = dict_rows(cur)
    cur.close()
    conn.close()
    return result


@app.get("/api/waiver_avoids")
def get_waiver_avoids():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        WITH ranked AS (
            SELECT player_id, week, stat_value,
                   ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY week DESC) AS rn
            FROM player_stats
            WHERE stat_name = 'snap_pct' AND season = %s
        )
        SELECT p.full_name, p.team, p.position,
               (latest.stat_value - prior.stat_value) AS snap_trend
        FROM ranked latest
        JOIN ranked prior ON prior.player_id = latest.player_id AND prior.rn = 2
        JOIN players p ON p.id = latest.player_id
        WHERE latest.rn = 1
          AND (latest.stat_value - prior.stat_value) < -0.1
        ORDER BY snap_trend ASC
        LIMIT 20
        """,
        (SEASON,),
    )
    result = dict_rows(cur)
    cur.close()
    conn.close()
    return result


@app.get("/api/trade_buy")
def get_trade_buy():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.full_name, p.team, p.position, r.value AS preseason_adp,
               ps.stat_value AS season_fantasy_points
        FROM rankings r
        JOIN players p ON r.player_id = p.id
        JOIN player_stats ps ON ps.player_id = p.id AND ps.stat_name = 'fantasy_points_ppr' AND ps.season = %s
        WHERE r.rank_type = 'adp' AND r.season = %s
        ORDER BY r.value ASC
        LIMIT 20
        """,
        (SEASON, SEASON),
    )
    result = dict_rows(cur)
    cur.close()
    conn.close()
    return result


@app.get("/api/trade_sell")
def get_trade_sell():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.full_name, p.team, p.position, r.value AS preseason_adp,
               ps.stat_value AS season_fantasy_points
        FROM rankings r
        JOIN players p ON r.player_id = p.id
        JOIN player_stats ps ON ps.player_id = p.id AND ps.stat_name = 'fantasy_points_ppr' AND ps.season = %s
        WHERE r.rank_type = 'adp' AND r.season = %s
        ORDER BY r.value DESC
        LIMIT 20
        """,
        (SEASON, SEASON),
    )
    result = dict_rows(cur)
    cur.close()
    conn.close()
    return result


@app.get("/api/stream_dst")
def get_stream_dst():
    conn = get_conn()
    cur = conn.cursor()
    week = current_nfl_week(cur)
    cur.execute(
        """
        SELECT p.full_name, p.team, r.value AS adp,
               ts.week AS next_week, ts.opponent AS next_opponent, ts.is_home
        FROM players p
        LEFT JOIN rankings r ON r.player_id = p.id AND r.rank_type = 'adp' AND r.season = %s
        LEFT JOIN team_schedule ts ON ts.team = p.team AND ts.season = %s AND ts.week = %s
        WHERE p.position = 'DEF'
        ORDER BY r.value ASC NULLS LAST
        LIMIT 15
        """,
        (SEASON, SEASON, week),
    )
    result = dict_rows(cur)
    cur.close()
    conn.close()
    return result


@app.get("/api/stream_k")
def get_stream_k():
    conn = get_conn()
    cur = conn.cursor()
    week = current_nfl_week(cur)
    cur.execute(
        """
        SELECT p.full_name, p.team, r.value AS adp,
               ts.week AS next_week, ts.opponent AS next_opponent, ts.is_home
        FROM players p
        LEFT JOIN rankings r ON r.player_id = p.id AND r.rank_type = 'adp' AND r.season = %s
        LEFT JOIN team_schedule ts ON ts.team = p.team AND ts.season = %s AND ts.week = %s
        WHERE p.position = 'K'
        ORDER BY r.value ASC NULLS LAST
        LIMIT 15
        """,
        (SEASON, SEASON, week),
    )
    result = dict_rows(cur)
    cur.close()
    conn.close()
    return result


@app.get("/api/waiver_news")
def get_waiver_news():
    """
    Real data: quotes tagged 'waiver_mention', same shape as /api/news
    (frontend reuses the same grouping component). Not limited to this
    week - waiver value calls stay relevant for a bit, and the volume is
    nowhere near /api/news's scale.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.full_name, p.team, p.position, q.quote_text, q.speaker,
               q.tags, q.sentiment, q.fantasy_relevance, q.match_confidence,
               q.created_at, q.content_week,
               pod.name AS source_podcast, e.published_at AS source_published_at
        FROM quotes q
        LEFT JOIN players p ON q.player_id = p.id
        LEFT JOIN episodes e ON q.episode_id = e.id
        LEFT JOIN podcasts pod ON e.podcast_id = pod.id
        WHERE q.tags @> '["waiver_mention"]'
        ORDER BY q.created_at DESC
        """
    )
    result = dict_rows(cur)
    cur.close()
    conn.close()
    return result


@app.get("/api/injury_report")
def get_injury_report(days: int = Query(3, ge=1, le=14)):
    """
    The top 'NEW INJURY NEWS' bar only ever shows the *latest pipeline
    run's* injury quotes - as soon as another run happens, yesterday's
    injury news drops out of view even though it's still in the
    database. This gives a day-by-day breakdown over a real window
    instead, so nothing disappears just because a newer run occurred.
    """
    conn = get_conn()
    cur = conn.cursor()
    # Clean calendar-day boundaries (today, yesterday, ...) rather than a
    # rolling N*24h window, which would span partial days at each end -
    # the point of this view is "for a specific day," so the day buckets
    # need to actually line up with real days.
    since = datetime.combine(date.today() - timedelta(days=days - 1), datetime.min.time())
    cur.execute(
        """
        SELECT q.created_at::date AS report_date, q.player_id, p.full_name, q.quote_text,
               q.fantasy_relevance, q.sentiment, q.tags, q.match_confidence,
               q.created_at, pod.name AS source_podcast,
               e.published_at AS source_published_at
        FROM quotes q
        LEFT JOIN players p ON q.player_id = p.id
        LEFT JOIN episodes e ON q.episode_id = e.id
        LEFT JOIN podcasts pod ON e.podcast_id = pod.id
        WHERE q.tags @> '["injury"]' AND q.created_at >= %s
        ORDER BY q.created_at DESC
        """,
        (since,),
    )
    result = dict_rows(cur)
    cur.close()
    conn.close()
    return {"days": days, "quotes": result}


@app.get("/api/bye_weeks")
def get_bye_weeks():
    """
    Real schedule data (team_schedule, opponent IS NULL = bye). Players
    grouped by their team's bye week, sorted by ADP within each week so
    the most rosterable names float to the top - for stashing ahead of a
    bye or avoiding stacking too many byes in the same week.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT ts.week, p.full_name, p.team, p.position, r.value AS adp
        FROM team_schedule ts
        JOIN players p ON p.team = ts.team
        LEFT JOIN rankings r ON r.player_id = p.id AND r.rank_type = 'adp' AND r.season = %s
        WHERE ts.season = %s AND ts.opponent IS NULL AND r.value IS NOT NULL
        ORDER BY ts.week ASC, r.value ASC
        """,
        (SEASON, SEASON),
    )
    result = dict_rows(cur)
    cur.close()
    conn.close()
    return result


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def serve_index():
    return FileResponse("static/index.html")


@app.get("/dev")
def serve_dev_console():
    return FileResponse("static/dev.html")
