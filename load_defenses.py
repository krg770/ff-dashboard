"""
Seeds the 32 NFL team defenses as players (position='DEF') - nflverse's
individual-player roster feed (load_players.py) has no concept of a team
defense, so D/ST never had any player rows to attach ADP or streaming
data to. One-time seed, not a periodic job (NFL teams don't change).

Full names match Fantasy Football Calculator's naming exactly ("Seattle
Defense", "LA Rams Defense", etc.) so load_adp.py's alias resolution
picks them up on the next run without any special-casing there.

Usage:
    python load_defenses.py
"""
from db import get_conn

# team code (matching team_schedule/players convention) -> full name used
# for the alias, in FFC's "{City} Defense" style.
TEAM_NAMES = {
    "ARI": "Arizona", "AZ": "Arizona", "ATL": "Atlanta", "BAL": "Baltimore",
    "BUF": "Buffalo", "CAR": "Carolina", "CHI": "Chicago", "CIN": "Cincinnati",
    "CLE": "Cleveland", "DAL": "Dallas", "DEN": "Denver", "DET": "Detroit",
    "GB": "Green Bay", "HOU": "Houston", "IND": "Indianapolis", "JAX": "Jacksonville",
    "KC": "Kansas City", "LA": "LA Rams", "LAC": "LA Chargers", "LV": "Las Vegas",
    "MIA": "Miami", "MIN": "Minnesota", "NE": "New England", "NO": "New Orleans",
    "NYG": "NY Giants", "NYJ": "NY Jets", "PHI": "Philadelphia", "PIT": "Pittsburgh",
    "SEA": "Seattle", "SF": "San Francisco", "TB": "Tampa Bay", "TEN": "Tennessee",
    "WAS": "Washington",
}

# This project's own convention is "AZ", not "ARI" (see load_schedule.py).
TEAMS = [code for code in TEAM_NAMES if code != "ARI"]


def load():
    conn = get_conn()
    cur = conn.cursor()

    inserted, updated = 0, 0
    for team in TEAMS:
        full_name = f"{TEAM_NAMES[team]} Defense"
        cur.execute(
            "SELECT id FROM players WHERE team = %s AND position = 'DEF'",
            (team,),
        )
        existing = cur.fetchone()
        if existing:
            db_id = existing[0]
            cur.execute(
                "UPDATE players SET full_name = %s, updated_at = now() WHERE id = %s",
                (full_name, db_id),
            )
            updated += 1
        else:
            cur.execute(
                "INSERT INTO players (full_name, team, position, active) VALUES (%s, %s, 'DEF', true) RETURNING id",
                (full_name, team),
            )
            db_id = cur.fetchone()[0]
            inserted += 1

        cur.execute(
            "INSERT INTO player_aliases (player_id, alias, source) VALUES (%s, %s, 'auto') ON CONFLICT DO NOTHING",
            (db_id, full_name),
        )

    conn.commit()
    cur.close()
    conn.close()
    print(f"Done. {inserted} new defense(s), {updated} updated.")


if __name__ == "__main__":
    load()
