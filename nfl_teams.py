"""
NFL team names/nicknames and the team-bet tables.

Analysts say "the Niners", "Pats", "Bucs +3", rarely the abbreviations used in
team_schedule (SF, NE, TB, ...). This creates the tables for team and
whole-game bets (spreads, moneylines, totals) and seeds the teams with the
names people actually say. Idempotent; safe to re-run.

    python nfl_teams.py
"""
from db import get_conn

# Abbreviations match team_schedule / players.team (note AZ, LA, WAS, JAX).
TEAMS = {
    "AZ": ("Arizona Cardinals", ["Cardinals", "Cards", "Arizona"]),
    "ATL": ("Atlanta Falcons", ["Falcons", "Atlanta", "Dirty Birds"]),
    "BAL": ("Baltimore Ravens", ["Ravens", "Baltimore"]),
    "BUF": ("Buffalo Bills", ["Bills", "Buffalo"]),
    "CAR": ("Carolina Panthers", ["Panthers", "Carolina"]),
    "CHI": ("Chicago Bears", ["Bears", "Chicago"]),
    "CIN": ("Cincinnati Bengals", ["Bengals", "Cincinnati", "Cincy"]),
    "CLE": ("Cleveland Browns", ["Browns", "Cleveland"]),
    "DAL": ("Dallas Cowboys", ["Cowboys", "Dallas"]),
    "DEN": ("Denver Broncos", ["Broncos", "Denver"]),
    "DET": ("Detroit Lions", ["Lions", "Detroit"]),
    "GB": ("Green Bay Packers", ["Packers", "Green Bay", "Pack"]),
    "HOU": ("Houston Texans", ["Texans", "Houston"]),
    "IND": ("Indianapolis Colts", ["Colts", "Indianapolis", "Indy"]),
    "JAX": ("Jacksonville Jaguars", ["Jaguars", "Jags", "Jacksonville"]),
    "KC": ("Kansas City Chiefs", ["Chiefs", "Kansas City"]),
    "LA": ("Los Angeles Rams", ["Rams", "LA Rams"]),
    "LAC": ("Los Angeles Chargers", ["Chargers", "LA Chargers", "Bolts"]),
    "LV": ("Las Vegas Raiders", ["Raiders", "Las Vegas", "Vegas"]),
    "MIA": ("Miami Dolphins", ["Dolphins", "Fins", "Miami"]),
    "MIN": ("Minnesota Vikings", ["Vikings", "Vikes", "Minnesota"]),
    "NE": ("New England Patriots", ["Patriots", "Pats", "New England"]),
    "NO": ("New Orleans Saints", ["Saints", "New Orleans"]),
    "NYG": ("New York Giants", ["Giants", "G-Men"]),
    "NYJ": ("New York Jets", ["Jets"]),
    "PHI": ("Philadelphia Eagles", ["Eagles", "Philadelphia", "Philly"]),
    "PIT": ("Pittsburgh Steelers", ["Steelers", "Pittsburgh"]),
    "SEA": ("Seattle Seahawks", ["Seahawks", "Hawks", "Seattle"]),
    "SF": ("San Francisco 49ers", ["49ers", "Niners", "San Francisco", "Forty Niners"]),
    "TB": ("Tampa Bay Buccaneers", ["Buccaneers", "Bucs", "Tampa Bay", "Tampa"]),
    "TEN": ("Tennessee Titans", ["Titans", "Tennessee"]),
    "WAS": ("Washington Commanders", ["Commanders", "Washington"]),
}

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS teams (
    abbrev TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_aliases (
    id SERIAL PRIMARY KEY,
    team TEXT NOT NULL REFERENCES teams(abbrev),
    alias TEXT NOT NULL,
    UNIQUE (team, alias)
);
CREATE INDEX IF NOT EXISTS idx_team_aliases_trgm ON team_aliases USING gin (alias gin_trgm_ops);

-- Team and whole-game bets (spread, moneyline, totals): a separate table from
-- bet_quotes, which is player props only. subject_type says what the bet is ON.
CREATE TABLE IF NOT EXISTS team_bet_quotes (
    id SERIAL PRIMARY KEY,
    episode_id INTEGER NOT NULL REFERENCES episodes(id),
    subject_type TEXT NOT NULL,          -- team | game
    team TEXT REFERENCES teams(abbrev),  -- the team named / first team of a game bet
    opponent TEXT REFERENCES teams(abbrev),
    raw_mention TEXT NOT NULL,
    match_confidence TEXT NOT NULL DEFAULT 'unreviewed',
    bet_type TEXT NOT NULL,
    side TEXT,                           -- over | under for totals
    lean TEXT NOT NULL DEFAULT 'neutral',
    sharp_or_public TEXT,
    timeframe TEXT,                      -- this_week | past | futures
    line_context TEXT,
    quote_text TEXT NOT NULL,
    speaker TEXT,
    timestamp_sec INTEGER,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    betting_relevance TEXT,
    content_week DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (episode_id, quote_text, raw_mention)
);
CREATE INDEX IF NOT EXISTS idx_team_bet_quotes_team ON team_bet_quotes (team);

-- Marks that the team-bet pass ran for an episode (even when it found nothing),
-- without needing a column on the busy episodes table.
CREATE TABLE IF NOT EXISTS team_bet_extractions (
    episode_id INTEGER PRIMARY KEY REFERENCES episodes(id),
    extracted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    item_count INTEGER NOT NULL DEFAULT 0
);
"""


def main():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(SCHEMA)
    for abbrev, (name, aliases) in TEAMS.items():
        cur.execute(
            "INSERT INTO teams (abbrev, name) VALUES (%s, %s) ON CONFLICT (abbrev) DO UPDATE SET name = EXCLUDED.name",
            (abbrev, name),
        )
        for alias in {abbrev, name, *aliases}:
            cur.execute(
                "INSERT INTO team_aliases (team, alias) VALUES (%s, %s) ON CONFLICT DO NOTHING", (abbrev, alias)
            )
    conn.commit()
    cur.execute("SELECT count(*) FROM teams")
    n = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM team_aliases")
    print(f"Teams: {n}, aliases: {cur.fetchone()[0]}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
