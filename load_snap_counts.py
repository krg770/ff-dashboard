"""
Loads snap count percentages from nflverse (via nfl_data_py) into the
player_stats table (stat_name='snap_pct'). This source uses player
names rather than gsis_id, so matching falls back to the same
exact-then-fuzzy logic as the ADP loader.

Usage:
    python load_snap_counts.py
"""
import nfl_data_py as nfl
from db import get_conn

SEASON = 2026


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
    print(f"Pulling {SEASON} snap counts from nflverse...")
    snaps = nfl.import_snap_counts([SEASON])

    conn = get_conn()
    cur = conn.cursor()

    matched, unmatched = 0, 0
    for _, row in snaps.iterrows():
        name = str(row.get("player", "")).strip()
        week = row.get("week")
        offense_pct = row.get("offense_pct")

        if not name or offense_pct is None:
            unmatched += 1
            continue

        player_id = resolve_player(cur, name)
        if not player_id:
            unmatched += 1
            continue

        cur.execute(
            """
            INSERT INTO player_stats (player_id, season, week, stat_name, stat_value, source)
            VALUES (%s, %s, %s, 'snap_pct', %s, 'nflverse')
            ON CONFLICT (player_id, season, week, stat_name)
            DO UPDATE SET stat_value = EXCLUDED.stat_value
            """,
            (player_id, SEASON, week, offense_pct),
        )
        matched += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"Done. {matched} snap-count record(s) loaded, {unmatched} skipped.")


if __name__ == "__main__":
    load()
