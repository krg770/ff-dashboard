"""
Loads the real 2026 NFL schedule from nflverse (via nfl_data_py) into
team_schedule - one row per (team, week), with opponent=NULL marking a
bye week. Schedules are set well before the season starts, so this is
real data even in preseason, unlike stats.

Usage:
    python load_schedule.py
"""
import nfl_data_py as nfl
from db import get_conn

SEASON = 2026

# nflverse's schedule uses "ARI" for Arizona; this project's players
# table (and every existing endpoint) uses "AZ" - normalize on load
# rather than touching the team code everywhere else.
TEAM_CODE_MAP = {"ARI": "AZ"}


def normalize(code):
    return TEAM_CODE_MAP.get(code, code)


def load():
    print(f"Pulling {SEASON} schedule from nflverse...")
    sched = nfl.import_schedules([SEASON])
    sched = sched[sched["game_type"] == "REG"]

    conn = get_conn()
    cur = conn.cursor()

    cur.execute("DELETE FROM team_schedule WHERE season = %s", (SEASON,))

    games_by_team = {}
    max_week = 0
    for _, row in sched.iterrows():
        week = int(row["week"])
        max_week = max(max_week, week)
        away, home = normalize(row["away_team"]), normalize(row["home_team"])
        game_date = row["gameday"]

        for team, opponent, is_home in [(away, home, False), (home, away, True)]:
            games_by_team.setdefault(team, {})[week] = (opponent, is_home, game_date)

    inserted = 0
    for team, weeks in games_by_team.items():
        for week in range(1, max_week + 1):
            if week in weeks:
                opponent, is_home, game_date = weeks[week]
                cur.execute(
                    """
                    INSERT INTO team_schedule (season, team, week, opponent, is_home, game_date)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (SEASON, team, week, opponent, is_home, game_date),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO team_schedule (season, team, week, opponent, is_home, game_date)
                    VALUES (%s, %s, %s, NULL, NULL, NULL)
                    """,
                    (SEASON, team, week),
                )
            inserted += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"Done. {inserted} team-week rows loaded across {len(games_by_team)} teams, {max_week} weeks.")


if __name__ == "__main__":
    load()
