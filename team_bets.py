"""
Database side of the NFL team / whole-game bet pass: build the teams context
for the prompt, resolve what analysts said ("Niners", "Seattle") to a team, and
store the bets in team_bet_quotes. The extraction call itself lives in
worker.py (run_team_extraction) next to the other claude calls.
"""
import json
import re

TYPES_WITH_SIDE = {"game_total", "team_total", "first_half_total"}
AMBIGUOUS_NAMES = {"new york", "ny", "los angeles", "la", "l.a.", "n.y."}


def build_team_context(cur) -> str:
    cur.execute(
        "SELECT t.abbrev, t.name, string_agg(a.alias, ', ' ORDER BY a.alias) "
        "FROM teams t LEFT JOIN team_aliases a ON a.team = t.abbrev GROUP BY t.abbrev, t.name ORDER BY t.abbrev"
    )
    return "---TEAMS---\n" + "\n".join(f"{ab} = {name} (also: {aliases})" for ab, name, aliases in cur.fetchall())


def resolve_team(cur, raw):
    """(abbrev, confidence) for a team as said ("Niners", "Seattle", "SF")."""
    if not raw:
        return None, "unreviewed"
    raw = re.sub(r"^the\s+", "", raw.strip(), flags=re.I)
    # Two teams share these, so there is no right answer from the name alone.
    if raw.lower() in AMBIGUOUS_NAMES:
        return None, "unreviewed"
    cur.execute("SELECT DISTINCT team FROM team_aliases WHERE lower(alias) = lower(%s)", (raw,))
    rows = cur.fetchall()
    if len(rows) == 1:
        return rows[0][0], "high"
    cur.execute("SELECT team, similarity(alias, %s) AS sim FROM team_aliases ORDER BY sim DESC LIMIT 1", (raw,))
    row = cur.fetchone()
    if row and row[1] and row[1] > 0.6:
        return row[0], "low"
    return None, "unreviewed"


def split_game_teams(raw):
    parts = re.split(r"\s*(?:/|\bvs\.?\b|\bversus\b|\bat\b|@|\band\b|-)\s*", raw or "", flags=re.I)
    return [p for p in parts if p]


def insert_team_bet_quotes(cur, episode_id, content_week, items):
    """Stores the items; returns how many were new."""
    n = 0
    for q in items:
        if not isinstance(q, dict) or not q.get("quote_text") or not q.get("raw_mention"):
            continue
        subject = q.get("subject_type")
        if subject not in ("team", "game"):
            continue
        # Multi-leg parlays / teasers are one combined bet, not a team bet.
        if re.search(r"parlay|\d[- ]leg|teaser", f'{q.get("line_context") or ""} {q["quote_text"]}', re.I):
            continue
        raw = q["raw_mention"]
        team_hint, _ = resolve_team(cur, q.get("team_hint"))
        opp, _ = resolve_team(cur, q.get("opponent_hint"))
        team, confidence = team_hint, "unreviewed"

        if subject == "team":
            team, confidence = resolve_team(cur, raw)
            team = team or team_hint
            if not team:
                continue
        else:
            teams = [resolve_team(cur, p)[0] for p in split_game_teams(raw)]
            teams = [t for t in teams if t]
            if len(teams) >= 2:
                team, opp, confidence = teams[0], teams[1], "high"
            elif team_hint and opp:
                team, confidence = team_hint, "high"
            else:
                continue  # a game bet we can't pin to two teams can't be placed in a game
        bet_type = q.get("bet_type") or f"{subject}_other"
        side = q.get("side") if (q.get("side") in ("over", "under") and bet_type in TYPES_WITH_SIDE) else None

        cur.execute(
            """
            INSERT INTO team_bet_quotes (
                episode_id, subject_type, team, opponent, raw_mention, match_confidence, bet_type, side,
                lean, sharp_or_public, timeframe, line_context, quote_text, speaker, timestamp_sec,
                tags, betting_relevance, content_week
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (episode_id, quote_text, raw_mention) DO NOTHING
            """,
            (
                episode_id, subject, team, opp, raw, confidence, bet_type, side,
                q.get("lean") if q.get("lean") in ("favorable", "unfavorable", "neutral") else "neutral",
                q.get("sharp_or_public") if q.get("sharp_or_public") in ("sharp", "public") else None,
                q.get("timeframe"), q.get("line_context"), q["quote_text"], q.get("speaker"),
                q.get("timestamp_sec") if isinstance(q.get("timestamp_sec"), int) else None,
                json.dumps(q.get("tags", [])), q.get("betting_relevance"), content_week,
            ),
        )
        n += cur.rowcount
    return n


def mark_extracted(cur, episode_id, item_count):
    cur.execute(
        """
        INSERT INTO team_bet_extractions (episode_id, item_count) VALUES (%s, %s)
        ON CONFLICT (episode_id) DO UPDATE SET extracted_at = now(), item_count = EXCLUDED.item_count
        """,
        (episode_id, item_count),
    )
