"""
FastAPI backend for the fantasy football dashboard. Serves JSON for
each widget the frontend renders, plus the static frontend itself.

Usage:
    uvicorn main:app --reload --port 8000
Then open http://localhost:8000 in a browser.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from db import get_conn

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
# Mirrors the WSL crontab entry (`0 8-22/2 * * *`) - not read from crontab
# directly, just kept in sync manually. Used only to estimate "next run"
# for the dev panel; the actual schedule lives in cron, this is display-only.
CRON_HOURS = [8, 10, 12, 14, 16, 18, 20, 22]


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
        SELECT q.episode_id, p.full_name, q.quote_text, q.tags, q.sentiment, q.match_confidence, q.fantasy_relevance
        FROM quotes q
        LEFT JOIN players p ON q.player_id = p.id
        WHERE q.episode_id = ANY(%s)
        ORDER BY q.created_at DESC
        """,
        (episode_ids,),
    )
    quotes = dict_rows(cur)

    cur.close()
    conn.close()

    counts = defaultdict(lambda: {"rising": 0, "falling": 0})
    for q in quotes:
        if not q["full_name"] or q["sentiment"] not in ("rising", "falling"):
            continue
        counts[q["full_name"]][q["sentiment"]] += 1

    def top(sentiment):
        candidates = [(name, c[sentiment]) for name, c in counts.items() if c[sentiment] >= MIN_RUN_MENTIONS]
        if not candidates:
            return None
        name, count = max(candidates, key=lambda nc: nc[1])
        return {"full_name": name, "mention_count": count}

    return {
        "episodes": episodes,
        "quotes": quotes,
        "top_riser": top("rising"),
        "top_faller": top("falling"),
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
def get_round_focus(round: int | None = Query(None, ge=1)):
    conn = get_conn()
    cur = conn.cursor()
    week = current_content_week()
    round_filter = "AND CEIL(r.value / 10) = %s" if round is not None else "AND CEIL(r.value / 10) <= 16"
    params = [SEASON, week] + ([round] if round is not None else [])
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
                    )
                ) FILTER (WHERE q.quote_text IS NOT NULL),
                '[]'
            ) AS weekly_quotes
        FROM players p
        JOIN rankings r ON r.player_id = p.id AND r.rank_type = 'adp' AND r.season = %s
        LEFT JOIN quotes q ON q.player_id = p.id AND q.content_week = %s
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
    cur.execute(
        """
        SELECT p.full_name, p.team, r.value AS adp
        FROM players p
        LEFT JOIN rankings r ON r.player_id = p.id AND r.rank_type = 'adp' AND r.season = %s
        WHERE p.position = 'DEF'
        ORDER BY r.value ASC NULLS LAST
        LIMIT 15
        """,
        (SEASON,),
    )
    result = dict_rows(cur)
    cur.close()
    conn.close()
    return result


@app.get("/api/stream_k")
def get_stream_k():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.full_name, p.team, r.value AS adp
        FROM players p
        LEFT JOIN rankings r ON r.player_id = p.id AND r.rank_type = 'adp' AND r.season = %s
        WHERE p.position = 'K'
        ORDER BY r.value ASC NULLS LAST
        LIMIT 15
        """,
        (SEASON,),
    )
    result = dict_rows(cur)
    cur.close()
    conn.close()
    return result


app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def serve_index():
    return FileResponse("static/index.html")
