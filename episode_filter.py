"""
Title-based relevance filter for betting podcasts, applied by the poller
before an episode is ever queued. Transcribing an hour or more of audio is
the expensive step, and many betting shows cover every sport - this dashboard
is specifically about NFL betting, so an episode that is clearly about another
sport (baseball, hockey, college football, ...) is marked 'skipped' up front
instead of being downloaded and transcribed.

College football counts as ANOTHER sport here: it is a different betting
market with different players, and "Week 7" is ambiguous (college has weeks
too), so a week number only counts as an NFL cue when the title has no college
marker.

Titles are a cheap, imperfect signal: a generic title ("Prop Points | October
16, 2026", "Joe Fortenbaugh Joins The Show") carries no sport information. How
strict to be is therefore per show:

  - NFL_ONLY shows are never filtered.
  - STRICT shows (all-sports radio where most episodes yield nothing) must
    show betting language or a known betting analyst in the title, and not be
    about another sport.
  - Every other betting show is processed unless the title names another
    sport (or college football) and has no NFL cue.

Episodes older than MAX_AGE_DAYS are also skipped (see poller.py), so a newly
added show's back catalog is never queued.
"""
import re

NFL_ONLY_SHOWS = {"Sharp Football Analysis", "NFL Best Bets"}
STRICT_SHOWS = {"You Better You Bet"}

_NFL_TEAMS = (
    "cardinals|falcons|ravens|bills|panthers|bears|bengals|browns|cowboys|broncos|"
    "lions|packers|texans|colts|jaguars|jags|chiefs|raiders|chargers|rams|dolphins|"
    "vikings|patriots|pats|saints|giants|jets|eagles|steelers|49ers|niners|seahawks|"
    "buccaneers|bucs|titans|commanders"
)

# Unambiguous NFL signals (no bare "football" / "week N" / "props" - those are
# handled separately because college and other sports share them).
NFL_CUE = re.compile(
    r"\b(nfl|super bowl|tnf|mnf|snf|thursday night|monday night|sunday night|wild card|"
    r"quarterback|touchdown|" + _NFL_TEAMS + r")\b",
    re.I,
)
WEEK_CUE = re.compile(r"\b(week|wk)\s*\d+\b", re.I)
COLLEGE = re.compile(r"\b(cfb|ncaaf|ncaa|college|cfp|bowl games?|heisman)\b", re.I)

# For STRICT shows a team name or "football" in the title is NOT enough: on
# You Better You Bet, team-news episodes ("Steelers/Browns TNF Preview")
# overwhelmingly produced zero prop quotes. What did produce them was
# betting language in the title and known betting analysts as guests -
# "Brad Evans Joins The Show!" alone produced 13 quotes with no sport
# word in the title at all.
BETTING_CUE = re.compile(
    r"\b(bets?|betting|props?|odds|lines?|line movement|it moved|breakdown|handicap\w*|"
    r"sharps?|wagers?|parlays?|picks?)\b",
    re.I,
)
BETTING_GUESTS = re.compile(
    r"\b(brad evans|joe fortenbaugh|joe fortinbaugh|drew dinsick|rob pizzola|"
    r"warren sharp|jay croucher|sean koerner|sean kerner|nick costos|prop king|"
    r"matt moore|john ewing)\b",
    re.I,
)

OTHER_SPORT = re.compile(
    r"\b(mlb|baseball|world series|nba|basketball|wnba|nhl|hockey|stanley cup|"
    r"ncaab|march madness|cbb|soccer|premier league|mls|"
    r"champions league|world cup|ufc|mma|boxing|golf|pga|masters|ryder cup|"
    r"f1|formula 1|nascar|tennis|atp|wta|cfl|us open|wimbledon|kentucky derby|horse racing|"
    r"election|midterm|politic\w*|prediction markets?|kalshi|polymarket)\b",
    re.I,
)


def _is_nfl(title):
    if NFL_CUE.search(title):
        return True
    college = bool(COLLEGE.search(title))
    # "Week 20" with CFL (or any other sport named) is not an NFL week.
    if WEEK_CUE.search(title) and not college and not OTHER_SPORT.search(title):
        return True
    return "football" in title.lower() and not college


def _other_sport(title):
    return bool(OTHER_SPORT.search(title) or COLLEGE.search(title))


def skip_reason(show_name, category, title):
    """Why this episode should not be processed, or None to process it."""
    if category != "betting" or show_name in NFL_ONLY_SHOWS:
        return None
    title = title or ""
    nfl = _is_nfl(title)
    if show_name in STRICT_SHOWS:
        if not (BETTING_CUE.search(title) or BETTING_GUESTS.search(title)):
            return "skipped: no betting cue in title"
        if _other_sport(title) and not nfl:
            return "skipped: not NFL (other sport or college in title)"
        return None
    if _other_sport(title) and not nfl:
        return "skipped: not NFL (other sport or college in title)"
    return None
