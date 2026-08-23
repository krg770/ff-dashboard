"""
Loads current NFL injury reports from nflverse (via nfl_data_py) into
the injuries table. Uses gsis_id for an exact match against players -
no fuzzy matching needed since this is the same ID system used when
players were loaded.

Usage:
    python load_injuries.py
"""
import nfl_data_py as nfl
from db import get_conn

SEASON = 2026


def load():
    print(f"Pulling {SEASON} injury reports from nflverse...")
    injuries = nfl.import_injuries([SEASON])

    conn = get_conn()
    cur = conn.cursor()

    matched, unmatched = 0, 0
    for _, row in injuries.iterrows():
        gsis_id = str(row.get("gsis_id", "")).strip()
        week = row.get("week")
        report_status = row.get("report_status")
        practice_status = row.get("practice_status")

        if not gsis_id or gsis_id == "nan":
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
            INSERT INTO injuries (player_id, season, week, report_status, practice_status, source)
            VALUES (%s, %s, %s, %s, %s, 'nflverse')
            ON CONFLICT (player_id, season, week)
            DO UPDATE SET report_status = EXCLUDED.report_status,
                          practice_status = EXCLUDED.practice_status,
                          fetched_at = now()
            """,
            (player_id, SEASON, week, report_status, practice_status),
        )
        matched += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"Done. {matched} injury report(s) loaded, {unmatched} skipped (no player match).")


if __name__ == "__main__":
    load()
