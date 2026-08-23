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
from datetime import datetime
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

        cur.execute(
            """
            INSERT INTO rankings (player_id, source, rank_type, season, week, value)
            VALUES (%s, 'fantasyfootballcalculator', 'adp', %s, NULL, %s)
            ON CONFLICT (player_id, source, rank_type, season, week)
            DO UPDATE SET value = EXCLUDED.value, fetched_at = now()
            """,
            (player_id, SEASON, adp_value),
        )
        matched += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"Done. {matched} matched and loaded, {unmatched} unmatched.")


if __name__ == "__main__":
    load()
