"""
Runs the team / whole-game bet pass over betting episodes that were processed
before that pass existed. Uses the saved transcripts, so there is no download
or transcription - just one claude call per episode. Episodes already handled
(team_bet_extractions) are skipped, so it is safe to re-run.

Usage:
    python backfill_team_bets.py [days]      # default: episodes from the last 10 days
"""
import sys
from db import get_conn
import episode_filter
from worker import run_team_extraction
from team_bets import build_team_context, insert_team_bet_quotes, mark_extracted


def main(days=10):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT e.id, e.title, e.content_week, e.transcript, p.name
        FROM episodes e
        JOIN podcasts p ON p.id = e.podcast_id
        LEFT JOIN team_bet_extractions x ON x.episode_id = e.id
        WHERE p.category = 'betting' AND e.status = 'extracted' AND e.transcript IS NOT NULL
          AND x.episode_id IS NULL AND e.published_at >= now() - make_interval(days => %s)
        ORDER BY e.published_at DESC
        """,
        (days,),
    )
    # Same relevance rules the poller applies to new episodes, so the backfill
    # doesn't spend claude calls on baseball radio shows from before the filter.
    todo = [r[:4] for r in cur.fetchall() if episode_filter.skip_reason(r[4], "betting", r[1]) is None]
    print(f"{len(todo)} episode(s) to process.")
    context = build_team_context(cur)
    for episode_id, title, content_week, transcript in todo:
        print(f"- {episode_id}: {title[:70]}")
        try:
            items = run_team_extraction(transcript, context)
            n = insert_team_bet_quotes(cur, episode_id, content_week, items)
            mark_extracted(cur, episode_id, len(items))
            conn.commit()
            print(f"    {len(items)} item(s) extracted, {n} new.")
        except Exception as e:
            conn.rollback()
            print(f"    FAILED (will be retried on the next backfill run): {e}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 10)
