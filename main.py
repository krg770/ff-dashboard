"""
FastAPI backend for the fantasy football dashboard. Serves JSON for
each widget the frontend renders, plus the static frontend itself.

Usage:
    uvicorn main:app --reload --port 8000
Then open http://localhost:8000 in a browser.
"""
from datetime import date, timedelta
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
    return {"episodes": episodes, "quotes": quotes}


@app.get("/api/rankings")
def get_rankings():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.full_name, p.team, p.position, r.value AS adp,
               CEIL(r.value / 10) AS draft_round
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
def get_round_focus(round: int = Query(..., ge=1)):
    conn = get_conn()
    cur = conn.cursor()
    week = current_content_week()
    cur.execute(
        """
        SELECT
            p.id AS player_id, p.full_name, p.team, p.position,
            r.value AS adp,
            COALESCE(
                json_agg(
                    json_build_object(
                        'quote', q.quote_text,
                        'sentiment', q.sentiment,
                        'tags', q.tags,
                        'speaker', q.speaker
                    )
                ) FILTER (WHERE q.quote_text IS NOT NULL),
                '[]'
            ) AS weekly_quotes
        FROM players p
        JOIN rankings r ON r.player_id = p.id AND r.rank_type = 'adp' AND r.season = %s
        LEFT JOIN quotes q ON q.player_id = p.id AND q.content_week = %s
        WHERE CEIL(r.value / 10) = %s
        GROUP BY p.id, p.full_name, p.team, p.position, r.value
        ORDER BY r.value ASC
        """,
        (SEASON, week, round),
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
               q.tags, q.sentiment, q.fantasy_relevance, q.match_confidence
        FROM quotes q
        LEFT JOIN players p ON q.player_id = p.id
        WHERE q.content_week = %s
        ORDER BY q.created_at DESC
        LIMIT 50
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
               i.practice_status, i.week
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
    cur.execute(
        """
        WITH ranked AS (
            SELECT player_id, week, stat_value,
                   ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY week DESC) AS rn
            FROM player_stats
            WHERE stat_name = 'snap_pct' AND season = %s
        )
        SELECT p.full_name, p.team, p.position,
               latest.stat_value AS current_snap_pct,
               prior.stat_value AS prior_snap_pct,
               (latest.stat_value - prior.stat_value) AS delta
        FROM ranked latest
        JOIN ranked prior ON prior.player_id = latest.player_id AND prior.rn = 2
        JOIN players p ON p.id = latest.player_id
        WHERE latest.rn = 1
        ORDER BY ABS(latest.stat_value - prior.stat_value) DESC
        LIMIT 30
        """,
        (SEASON,),
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
