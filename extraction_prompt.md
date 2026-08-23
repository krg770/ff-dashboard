# Podcast Quote Extraction Prompt (Claude Code CLI)

Invoke as:
```
claude -p "$(cat extraction_prompt.md)" < transcript.txt > quotes.json
```

Pass the current NFL roster (name + team + position, from nflverse) as
additional context alongside the transcript so player matching has
something to resolve against — append it after the prompt or pipe it
in as a second input block.

---

You are analyzing a fantasy football podcast transcript for a 10-team
redraft league. Extract actionable player news useful for both draft
prep and in-season roster decisions.

CRITICAL - one player per item: if a single statement discusses multiple
players (e.g. "You could see Judkins and Skattebo not working on passing
down"), do NOT combine their names into one `raw_player_mention` like
"Judkins and Scattaboo" - that can't be matched to any one player. Emit a
SEPARATE array item per player instead, each with its own isolated
`raw_player_mention` (just that one player's name), sharing the same
`quote_text` and other context fields, with `fantasy_relevance` adjusted
per player if the implication differs between them.

For each item found, extract:

- `raw_player_mention`: the player name exactly as said in the transcript,
  for ONE player only (don't normalize it — the matching step downstream
  handles that; see the multi-player rule above)
- `best_guess_player_id`: if you can confidently match the mention to a
  player in the provided roster list, return their canonical id;
  otherwise return null
- `match_confidence`: "high" | "medium" | "low" — low or ambiguous
  matches (common last names, unclear team context) should be flagged
  low rather than guessed
- `quote_text`: the actual statement, under 40 words, verbatim from transcript
- `speaker`: host/guest name or show name
- `timestamp_sec`: approximate timestamp if available in the transcript
- `tags`: an array of short lowercase strings describing what kind of
  news this is. Use existing tags where they fit: "injury",
  "injury_beneficiary", "depth_chart_change", "target_share_change",
  "coaching_comment", "rookie_evaluation", "trade_impact",
  "boom_bust_take", "waiver_mention". If something doesn't fit any
  existing tag, invent a new short tag rather than forcing a bad fit —
  new tags don't require any schema change downstream.
- `sentiment`: "rising" | "falling" | "neutral" — is this news generally
  bullish, bearish, or neutral for the player's fantasy value
- `fantasy_relevance`: one sentence on why this matters for a lineup or
  draft decision

CRITICAL DISTINCTION - injury vs injury_beneficiary:
This is a common and consequential mistake to avoid. The "injury" tag
means the player named in `raw_player_mention` is THEMSELVES hurt,
questionable, out, or dealing with a health issue. Do NOT use "injury"
for a player who is merely benefiting from a TEAMMATE's injury (more
targets, bigger role, etc. because someone else got hurt) - that is a
completely different, often opposite, fantasy implication. Use
"injury_beneficiary" for that case instead, and if the transcript
names the injured teammate, include their name in `fantasy_relevance`
(e.g. "Expected to see more targets with [teammate] injured").
If a quote is ambiguous about which player is actually hurt - for
example "Player X now with the injuries in [City]" without specifying
who is injured - do NOT default to tagging it "injury" for Player X.
Tag it "injury_beneficiary" with low match_confidence and note the
ambiguity in `fantasy_relevance` instead of guessing. An incorrect
"injury" tag on a healthy player is a serious error that could cause
someone to bench a player who doesn't need to be benched.

Prioritize: injury status changes, depth chart movement, target/touch
share shifts, coaching staff comments on usage, rookie evaluations,
sleeper/bust calls, and waiver wire recommendations.

Skip: generic banter, recap of stats already public record (final
score, box score lines), and repeated information already covered
earlier in the same transcript.

Output strictly as a JSON array. If nothing meets the bar for a given
segment, return an empty array — do not force low-value extractions.

CRITICAL OUTPUT RULE: Your entire response must be ONLY the JSON array. No preamble, no explanation, no commentary about roster context or transcription quality. If roster context is empty, return best_guess_player_id as null and match_confidence as "low" without mentioning it. The first character must be [ and the last character must be ].
