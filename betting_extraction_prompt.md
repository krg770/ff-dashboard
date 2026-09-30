# Betting Podcast Quote Extraction Prompt (Claude Code CLI)

Invoke as:
```
claude -p "$(cat betting_extraction_prompt.md)" < transcript.txt > bet_quotes.json
```

Pass the current NFL roster (name + team + position, from nflverse) as
additional context alongside the transcript so player matching has
something to resolve against — append it after the prompt or pipe it
in as a second input block.

---

You are analyzing a sports-betting podcast transcript. This is NOT a
fantasy football show — hosts discuss lines, player props, and how
sharp or public money is moving, not roster/lineup advice. Extract
commentary specifically about OFFENSIVE PLAYER prop bets: anytime
touchdown, passing/rushing/receiving yards, receptions, passing
touchdowns, longest reception, and similar player-specific props.

This is for informational comparison only — nothing extracted here is
used for any wagering or monetary decision. Treat it the same as any
other podcast-buzz signal: what did the hosts actually say, not
whether their read was right.

CRITICAL - one player per item: if a single statement discusses
multiple players (e.g. "I like both McCaffrey and CMC's backup for
anytime TD if he sits"), do NOT combine their names into one
`raw_player_mention` — emit a SEPARATE array item per player, each with
its own isolated `raw_player_mention`, sharing the same `quote_text`
and other context fields.

For each item found, extract:

- `raw_player_mention`: the player name exactly as said in the
  transcript, for ONE player only (don't normalize it — the matching
  step downstream handles that; see the multi-player rule above)
- `match_confidence`: "high" | "medium" | "low" — low or ambiguous
  matches (common last names, unclear team context) should be flagged
  low rather than guessed
- `bet_type`: one of "anytime_td", "first_td", "passing_yards",
  "rushing_yards", "receiving_yards", "receptions", "passing_tds",
  "interceptions", "rushing_attempts", "longest_reception", "longest_rush",
  "game_leader" (a comparative/field prop like "most rushing yards in the
  game" or "highest-scoring player" - not a personal over/under), or
  "player_other". Use "player_other" only for a real offensive-player prop
  that genuinely doesn't fit any of these - not as a default when unsure;
  re-read the list above first.
- `lean`: "favorable" | "unfavorable" | "neutral" — is the chatter
  bullish or bearish on this prop actually hitting. "neutral" for
  purely descriptive commentary (a line number stated with no read on
  it) or genuinely mixed/uncertain takes.
- `sharp_or_public`: "sharp" | "public" | null — only set this when the
  transcript explicitly frames the take as informed/professional money
  vs. public/recreational money (e.g. "the sharps are all over this
  under", "this is a square play"). Leave null otherwise — do not infer
  it from tone alone.
- `line_context`: JUST the number/line, kept short - "over 74.5 yards",
  "+150", "under 39.5", "-3.5" - or null if no specific number was given.
  Do NOT put line-movement narration here ("opened +105, now -112",
  "moved from 16.5 to 17.5") - that belongs in `betting_relevance`
  instead, tagged "line_movement". This field should always be scannable
  in isolation, never a sentence. Exact odds vary by sportsbook and go
  stale fast - this is context for the quote, not a live line to act on.
- `quote_text`: the actual statement, under 40 words, verbatim from transcript
- `speaker`: host/guest name or show name
- `timestamp_sec`: approximate timestamp if available in the transcript
- `tags`: an array of short lowercase strings describing what kind of
  take this is. Use existing tags where they fit: "line_movement",
  "injury_impact", "matchup_based", "weather_impact", "usage_based",
  "narrative_fade" (betting against a popular storyline),
  "correlation_play" (tied to a game script/total read). If something
  doesn't fit any existing tag, invent a new short tag rather than
  forcing a bad fit — new tags don't require any schema change
  downstream.
- `betting_relevance`: one sentence on why this take matters — what's
  the reasoning behind the lean, not just the number.

Prioritize: specific player prop reads with real reasoning behind them,
explicit line-movement commentary, sharp-vs-public framing, and
matchup/usage-based reasoning for a specific player.

Skip: generic game previews with no specific player prop mentioned,
spread/moneyline/total-only discussion with no player tie-in, banter,
and injury news repeated verbatim from a report without independent
betting commentary added.

Output strictly as a JSON array. If nothing meets the bar for a given
segment, return an empty array — do not force low-value extractions.

CRITICAL OUTPUT RULE: Your entire response must be ONLY the JSON array. No preamble, no explanation, no commentary about roster context or transcription quality. If roster context is empty, return match_confidence as "low" without mentioning it. The first character must be [ and the last character must be ].
