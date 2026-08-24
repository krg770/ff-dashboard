"""
Loads ADP (Average Draft Position) data for your 10-team PPR league from
the Fantasy Football Calculator's free public REST API. Matches players
against the existing player_aliases table (same resolution logic as the
extraction pipeline) and stores results in the rankings table.

Free, no API key, no cost - attribution: fantasyfootballcalculator.com

Usage:
    python load_adp.py
"""
import requests
from datetime import datetime, timezone
from db import get_conn

TEAMS = 10
SCORING = "ppr"
SEASON = 2026
API_URL = f"https://fantasyfootballcalculator.com/api/v1/adp/{SCORING}?teams={TEAMS}&year={SEASON}"


def resolve_player(cur, name: str):
    cur.execute(
        "SELECT player_id FROM player_aliases WHERE lower(alias) = lower(%s)",
        (name,),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    cur.execute(
        """
        SELECT player_id, similarity(alias, %s) AS sim
        FROM player_aliases
        ORDER BY sim DESC
        LIMIT 1
        """,
        (name,),
    )
    row = cur.fetchone()
    if row and row[1] and row[1] > 0.5:
        return row[0]

    return None


def load():
    print(f"Pulling ADP from Fantasy Football Calculator ({TEAMS}-team {SCORING})...")
    resp = requests.get(API_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    players = data.get("players", [])
    print(f"Got {len(players)} players.")

    run_timestamp = datetime.now(timezone.utc)
    conn = get_conn()
    cur = conn.cursor()

    matched, unmatched = 0, 0
    for p in players:
        name = p.get("name", "").strip()
        adp_value = p.get("adp")
        if not name or adp_value is None:
            continue

        player_id = resolve_player(cur, name)
        if not player_id:
            unmatched += 1
            continue

        # `rankings` holds only the current/latest ADP (every other endpoint
        # in main.py queries it expecting one row per player) - this upsert
        # overwrites in place, on purpose, every run. Targets
        # rankings_current_adp_idx (a partial unique index on
        # (player_id, source, rank_type, season) WHERE week IS NULL) rather
        # than the table's general unique constraint, which includes `week`
        # and therefore never actually enforced uniqueness here: Postgres
        # treats NULL <> NULL, so the plain constraint silently let this
        # upsert insert duplicate rows every run instead of updating one.
        cur.execute(
            """
            INSERT INTO rankings (player_id, source, rank_type, season, week, value)
            VALUES (%s, 'fantasyfootballcalculator', 'adp', %s, NULL, %s)
            ON CONFLICT (player_id, source, rank_type, season) WHERE week IS NULL
            DO UPDATE SET value = EXCLUDED.value, fetched_at = now()
            """,
            (player_id, SEASON, adp_value),
        )
        # `adp_history` is append-only - this is what makes ADP drift over
        # time (and buzz-vs-ADP-drift correlation) possible at all. Without
        # this, re-running the loader would just silently overwrite the one
        # ADP number every time with no trail left behind.
        cur.execute(
            """
            INSERT INTO adp_history (player_id, source, season, value, fetched_at)
            VALUES (%s, 'fantasyfootballcalculator', %s, %s, %s)
            """,
            (player_id, SEASON, adp_value, run_timestamp),
        )
        matched += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"Done. {matched} matched and loaded, {unmatched} unmatched.")


if __name__ == "__main__":
    load()
