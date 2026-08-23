"""
Polls every active podcast's RSS feed for new episodes and inserts
them into the episodes table with status='new'. Run this on a
schedule (Windows Task Scheduler -> wsl.exe -d Ubuntu -e ...).

Usage:
    python poller.py
"""
import feedparser
from datetime import datetime, timedelta, timezone
from db import get_conn


def content_week_for(published_at: datetime) -> datetime.date:
    """
    Bucket a publish timestamp into its content week, anchored to the
    Monday of that week. Window: Monday 00:00 through Sunday 10:59am.
    Anything Sunday 11am onward belongs to the *next* week.
    """
    dt = published_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    weekday = dt.weekday()  # Monday=0 ... Sunday=6
    if weekday == 6 and dt.hour >= 11:
        # Sunday after 11am -> belongs to the week that starts tomorrow
        monday = dt.date() + timedelta(days=1)
    else:
        monday = dt.date() - timedelta(days=weekday)
    return monday


def poll_all():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT id, name, rss_url FROM podcasts WHERE active = true")
    podcasts = cur.fetchall()

    total_new = 0
    for podcast_id, name, rss_url in podcasts:
        print(f"Checking {name}...")
        feed = feedparser.parse(rss_url)

        for entry in feed.entries:
            guid = entry.get("id") or entry.get("link")
            if not guid:
                continue

            cur.execute(
                "SELECT 1 FROM episodes WHERE podcast_id = %s AND guid = %s",
                (podcast_id, guid),
            )
            if cur.fetchone():
                continue  # already seen this episode

            title = entry.get("title", "")
            published_struct = entry.get("published_parsed")
            published_at = (
                datetime(*published_struct[:6], tzinfo=timezone.utc)
                if published_struct
                else datetime.now(timezone.utc)
            )

            audio_url = None
            for link in entry.get("links", []):
                if link.get("type", "").startswith("audio"):
                    audio_url = link.get("href")
                    break

            week = content_week_for(published_at)

            cur.execute(
                """
                INSERT INTO episodes (podcast_id, guid, title, published_at, audio_url, status, content_week)
                VALUES (%s, %s, %s, %s, %s, 'new', %s)
                """,
                (podcast_id, guid, title, published_at, audio_url, week),
            )
            total_new += 1
            print(f"  New episode: {title}")

    conn.commit()
    cur.close()
    conn.close()
    print(f"Done. {total_new} new episode(s) found.")


if __name__ == "__main__":
    poll_all()
