"""
FastAPI backend for the fantasy football dashboard. Serves JSON for
each widget the frontend renders, plus the static frontend itself.

Usage:
    uvicorn main:app --reload --port 8000
Then open http://localhost:8000 in a browser.
"""
import math
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
# 10-team, $200/team auction league settings for the Salary tab.
LEAGUE_TEAMS = 10
LEAGUE_BUDGET = 200
ROSTER_SPOTS = 16  # matches the round<=16 cap used elsewhere (Rankings, Round Focus)
MIN_BID = 1
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
        SELECT q.episode_id, p.full_name, q.quote_text, q.tags, q.sentiment, q.match_confidence,
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

    n = len(players)
    total_pool = LEAGUE_TEAMS * LEAGUE_BUDGET
    value_pool = total_pool - MIN_BID * n
    decay_k = 0.03
    weights = [math.exp(-decay_k * i) for i in range(n)]
    weight_sum = sum(weights) or 1

    result = []
    for (player_id, full_name, team, position, adp, draft_round), weight in zip(players, weights):
        auction_value = MIN_BID + round(value_pool * weight / weight_sum)
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
        SELECT q.created_at::date AS report_date, p.full_name, q.quote_text,
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
