# NFL Team & Game Bet Extraction Prompt (Claude Code CLI)

Invoked by worker.py as `claude -p "<this file>"` with a teams context block
and the transcript on stdin. A separate pass from the player-prop extraction.

---

You are analyzing an NFL betting podcast transcript. Extract every specific
TEAM or GAME bet the analysts discuss: point spreads, moneylines, game
totals, team totals, and first-half lines. Player props are handled
elsewhere - skip them here.

This is for informational comparison only - nothing extracted here is used
for any wagering or monetary decision. Record what the hosts actually said,
not whether their read was right.

## Items

Each item is ONE bet on ONE team or game:

- `subject_type: "team"` - a bet on one team. `bet_type` is one of:
  "spread", "moneyline", "team_total", "first_half_spread", "team_other".
- `subject_type: "game"` - a bet on a whole game. `bet_type` is one of:
  "game_total", "first_half_total", "game_other".

If a statement covers several games ("I like the over in both Sunday
afternoon games"), emit a separate item per game.

## Fields

- `subject_type`: as above.
- `raw_mention`: for a team, the team name exactly as said ("Niners",
  "Seattle", "the Eagles"). For a game bet, both teams as said, e.g.
  "49ers vs Seahawks".
- `team_hint`: abbreviation of the team being backed/named (for a game bet,
  the first team), using the abbreviations in the teams context; null if
  unknown.
- `opponent_hint`: abbreviation of the opposing team if mentioned or clear,
  else null.
- `bet_type`: as above.
- `side`: "over" or "under" for totals (game_total, team_total,
  first_half_total); null otherwise.
- `lean`: "favorable" if the analyst likes / is backing the bet as described;
  "unfavorable" if they are recommending AGAINST it ("fade the Cowboys",
  "stay away", "I'm not touching that spread"); "neutral" for descriptive
  mentions with no read.
- `sharp_or_public`: "sharp" or "public" ONLY if the analyst explicitly
  frames the take as professional vs recreational money. Otherwise null.
- `timeframe`: "this_week" (an upcoming game this week), "past" (recapping a
  game or a past bet - NOT a pick), or "futures" (season-long: win totals,
  division, Super Bowl, MVP).
- `line_context`: JUST the number, short: for spreads KEEP THE SIGN FOR THE
  TEAM BEING BACKED ("-3.5" if they are laying 3.5 with that team, "+3.5" if
  they are taking 3.5 points), "-3", "+7"; for totals the number alone
  ("44.5") with `side` carrying over/under; moneyline price like "+135" or
  "-150". null if none was given. Never put narration here (line movement
  goes in `betting_relevance`).
- `quote_text`: the actual statement, under 40 words, verbatim.
- `speaker`: the host/guest name if identifiable, else the show name.
- `timestamp_sec`: approximate timestamp from the transcript markers.
- `tags`: short lowercase strings: "line_movement", "injury_impact",
  "matchup_based", "weather_impact", "rest_advantage", "narrative_fade",
  "correlation_play", "value_price", "revenge_game"; invent a short new tag
  if none fits.
- `betting_relevance`: ONE sentence on the reasoning behind the take.

## Prioritize

Specific bets with a stated side and number, line-movement commentary,
injury/weather news that changes a bet, and explicit sharp-vs-public framing.

## Skip

Player props, generic previews with no bet attached, standings/news chatter,
banter, sponsor reads and promo codes, other sports (MLB, NBA, college),
multi-leg parlays and teasers, and anything with no identifiable bet.

## Output

Output strictly a JSON array. If nothing meets the bar, return `[]`.

CRITICAL OUTPUT RULE: Your entire response must be ONLY the JSON array. No
preamble, no explanation, no commentary about the context or transcript
quality. The first character must be [ and the last character must be ].
