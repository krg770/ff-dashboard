"""
Title-based relevance filter for betting podcasts, applied by the poller
before an episode is ever queued. Transcribing an hour or more of audio is
the expensive step, and several betting shows cover every sport - this
dashboard only wants NFL / college football player props, so an episode
that is clearly about something else is marked 'skipped' up front instead
of being downloaded and transcribed.

Titles are a cheap, imperfect signal: a generic title ("Joe Fortenbaugh
Joins The Show") carries no sport information. How strict to be is
therefore per show:

  - NFL_ONLY shows are never filtered.
  - STRICT shows (all-sports radio where most episodes yield nothing) must
    show betting language or a known betting analyst in the title, and not
    be about another sport.
  - Every other betting show is processed unless the title names another
    sport and has no football cue.
"""
import re

NFL_ONLY_SHOWS = {"Sharp Football Analysis"}
STRICT_SHOWS = {"You Better You Bet"}

_NFL_TEAMS = (
    "cardinals|falcons|ravens|bills|panthers|bears|bengals|browns|cowboys|broncos|"
    "lions|packers|texans|colts|jaguars|jags|chiefs|raiders|chargers|rams|dolphins|"
    "vikings|patriots|pats|saints|giants|jets|eagles|steelers|49ers|niners|seahawks|"
    "buccaneers|bucs|titans|commanders"
)

FOOTBALL_CUE = re.compile(
    r"\b(nfl|football|cfb|ncaaf|college football|super bowl|tnf|mnf|snf|"
    r"thursday night|monday night|sunday night|wild card|"
    r"week\s*\d+|wk\s*\d+|quarterback|touchdown|"
    r"fantasy|prop[s]?|" + _NFL_TEAMS + r")\b",
    re.I,
)

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
    r"ncaab|march madness|college basketball|cbb|soccer|premier league|mls|"
    r"champions league|world cup|ufc|mma|boxing|golf|pga|masters|ryder cup|"
    r"f1|formula 1|nascar|tennis|us open|wimbledon|kentucky derby|horse racing)\b",
    re.I,
)


def skip_reason(show_name, category, title):
    """Why this episode should not be processed, or None to process it."""
    if category != "betting" or show_name in NFL_ONLY_SHOWS:
        return None
    title = title or ""
    football = bool(FOOTBALL_CUE.search(title))
    if show_name in STRICT_SHOWS:
        betting = bool(BETTING_CUE.search(title) or BETTING_GUESTS.search(title))
        if not betting:
            return "skipped: no betting cue in title"
        if OTHER_SPORT.search(title) and not football:
            return "skipped: non-football sport in title"
        return None
    if OTHER_SPORT.search(title) and not football:
        return "skipped: non-football sport in title"
    return None
