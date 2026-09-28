"""
Loads weekly fantasy points (PPR) from nflverse (via nflreadpy) into the
player_stats table (stat_name='fantasy_points_ppr'). Powers the
In-Season tab's hot/cold meter and trade-target views, both of which
were silently empty until this ran, since player_stats had never been
populated - the season didn't exist yet the last time this was
attempted.

Uses gsis_id for an exact match against players - nflreadpy's
player_stats.player_id is the same GSIS ID system used when players
were loaded, so no fuzzy name matching is needed here (unlike
load_snap_counts.py, whose source only provides a player name).

Usage:
    python load_player_stats.py
"""
import nflreadpy as nfl
from db import get_conn

SEASON = 2026


def load():
    print(f"Pulling {SEASON} weekly player stats from nflverse...")
    stats = nfl.load_player_stats(seasons=[SEASON]).to_pandas()

    conn = get_conn()
    cur = conn.cursor()

    matched, unmatched = 0, 0
    for _, row in stats.iterrows():
        gsis_id = str(row.get("player_id", "")).strip()
        week = row.get("week")
        fantasy_points_ppr = row.get("fantasy_points_ppr")

        if not gsis_id or gsis_id == "nan" or fantasy_points_ppr is None:
            unmatched += 1
            continue

        cur.execute("SELECT id FROM players WHERE gsis_id = %s", (gsis_id,))
        result = cur.fetchone()
        if not result:
            unmatched += 1
            continue

        player_id = result[0]

        cur.execute(
            """
            INSERT INTO player_stats (player_id, season, week, stat_name, stat_value, source)
            VALUES (%s, %s, %s, 'fantasy_points_ppr', %s, 'nflverse')
            ON CONFLICT (player_id, season, week, stat_name)
            DO UPDATE SET stat_value = EXCLUDED.stat_value
            """,
            (player_id, SEASON, week, fantasy_points_ppr),
        )
        matched += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"Done. {matched} weekly stat record(s) loaded, {unmatched} skipped (no player match).")


if __name__ == "__main__":
    load()
