"""
Loads the current season's NFL roster from nflverse (via nfl_data_py)
into the players table, and seeds player_aliases with a few common
name variants so extraction matching has something to work against
from day one. Run once at season start, and again if you want to
refresh (e.g. after trades/roster moves) - it's safe to re-run,
existing players are updated rather than duplicated.

Usage:
    python load_players.py
"""
import nfl_data_py as nfl
from db import get_conn

SEASON = 2026


def load():
    print(f"Pulling {SEASON} roster from nflverse...")
    roster = nfl.import_seasonal_rosters([SEASON])

    roster = roster[["player_name", "team", "position", "player_id"]].dropna(subset=["player_id"])
    roster = roster.drop_duplicates(subset=["player_id"])

    # Full last name, not just the final whitespace-split token - "Amon-Ra
    # St. Brown".split()[-1] is "Brown", which silently drops the "St."
    # and produces a short-form alias ("A. Brown") that collides with any
    # other Brown on the roster. Same bug turns "Odell Beckham Jr." into
    # the useless alias "O. Jr.". Compute last-name frequency across the
    # whole roster first so a bare last-name alias only gets added when
    # it's actually unambiguous (a bare "Brown" would still be genuinely
    # ambiguous - "St. Brown" as a whole phrase isn't).
    last_name_counts = {}
    for _, row in roster.iterrows():
        parts = str(row["player_name"]).strip().split()
        if len(parts) >= 2:
            last_name = " ".join(parts[1:])
            last_name_counts[last_name] = last_name_counts.get(last_name, 0) + 1

    conn = get_conn()
    cur = conn.cursor()

    inserted, updated = 0, 0
    for _, row in roster.iterrows():
        full_name = str(row["player_name"]).strip()
        team = str(row["team"]).strip() if row["team"] else None
        position = str(row["position"]).strip() if row["position"] else None
        ext_player_id = str(row["player_id"]).strip()

        cur.execute("SELECT id FROM players WHERE gsis_id = %s", (ext_player_id,))
        existing = cur.fetchone()

        if existing:
            db_id = existing[0]
            cur.execute(
                "UPDATE players SET full_name = %s, team = %s, position = %s, updated_at = now() WHERE id = %s",
                (full_name, team, position, db_id),
            )
            updated += 1
        else:
            cur.execute(
                "INSERT INTO players (gsis_id, full_name, team, position) VALUES (%s, %s, %s, %s) RETURNING id",
                (ext_player_id, full_name, team, position),
            )
            db_id = cur.fetchone()[0]
            inserted += 1

        cur.execute(
            "INSERT INTO player_aliases (player_id, alias, source) VALUES (%s, %s, 'auto') ON CONFLICT DO NOTHING",
            (db_id, full_name),
        )

        parts = full_name.split()
        if len(parts) >= 2:
            last_name = " ".join(parts[1:])
            short_form = f"{parts[0][0]}. {last_name}"
            cur.execute(
                "INSERT INTO player_aliases (player_id, alias, source) VALUES (%s, %s, 'auto') ON CONFLICT DO NOTHING",
                (db_id, short_form),
            )
            if last_name_counts.get(last_name) == 1:
                cur.execute(
                    "INSERT INTO player_aliases (player_id, alias, source) VALUES (%s, %s, 'auto') ON CONFLICT DO NOTHING",
                    (db_id, last_name),
                )

    conn.commit()
    cur.close()
    conn.close()
    print(f"Done. {inserted} new player(s), {updated} updated.")


if __name__ == "__main__":
    load()
