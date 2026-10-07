"""
Processes episodes with status='new': downloads audio, speeds it up,
transcribes with faster-whisper, runs the Claude Code CLI extraction
prompt, and inserts results into the quotes table.

Usage:
    python worker.py
"""
import os
import fcntl
import json
import subprocess
import requests
from pathlib import Path
from datetime import datetime, timezone
from faster_whisper import WhisperModel
from db import get_conn
import run_history

WORK_DIR = Path(os.getenv("WORK_DIR", "./work"))
WORK_DIR.mkdir(parents=True, exist_ok=True)

EXTRACTION_PROMPT_PATH = Path(__file__).parent / "extraction_prompt.md"
BETTING_EXTRACTION_PROMPT_PATH = Path(__file__).parent / "betting_extraction_prompt.md"
WHISPER_MODEL_SIZE = "small"
SPEED_FACTOR = "1.5"
TRANSCRIBE_LOCK = "/tmp/whisper_transcribe.lock"
MAX_FANTASY_EPISODES_PER_RUN = 5
# Very long episodes can need more than the old 600s. A timeout leaves the
# episode at status=error, which the dashboard can retry (transcript is kept,
# so a retry skips download + transcription and only re-runs extraction).
EXTRACTION_TIMEOUT_SEC = int(os.getenv("EXTRACTION_TIMEOUT_SEC", "1200"))


def download_audio(url: str, dest: Path):
    resp = requests.get(url, stream=True, timeout=120)
    resp.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)


def speed_up_audio(src: Path, dest: Path, factor: str = SPEED_FACTOR):
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-filter:a", f"atempo={factor}", str(dest)],
        check=True,
        capture_output=True,
    )


def transcribe(audio_path: Path, model: WhisperModel) -> str:
    # One transcription at a time across ALL dashboards (ff, stock, nhl share
    # this lock file). Each worker uses most of the CPU on its own; running
    # two or three at once oversubscribes the cores and every one of them
    # slows to a crawl (observed: a ~5 min episode taking 40+ min).
    with open(TRANSCRIBE_LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        segments, _info = model.transcribe(str(audio_path))
        lines = []
        for seg in segments:
            lines.append(f"[{seg.start:.0f}s] {seg.text.strip()}")
        return "\n".join(lines)


def build_roster_context(cur) -> str:
    cur.execute("SELECT full_name, team, position FROM players WHERE active = true")
    rows = cur.fetchall()
    return "\n".join(f"{name} ({team} - {pos})" for name, team, pos in rows)


def _run_claude_extraction(prompt_path: Path, transcript: str, roster_context: str) -> list:
    prompt = prompt_path.read_text()
    stdin_content = (
        f"---ROSTER CONTEXT---\n{roster_context}\n\n"
        f"---TRANSCRIPT---\n{transcript}"
    )
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            input=stdin_content,
            capture_output=True,
            text=True,
            timeout=EXTRACTION_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        # Re-raised without the original message, which embeds the whole prompt.
        raise RuntimeError(f"claude extraction timed out after {EXTRACTION_TIMEOUT_SEC}s")
    output = result.stdout.strip()
    start = output.find("[")
    end = output.rfind("]")
    if start != -1 and end != -1 and end > start:
        output = output[start:end + 1]
    if output.startswith("```"):
        output = output.strip("`")
        if output.startswith("json"):
            output = output[4:]
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        print("WARNING: could not parse extraction output as JSON. Raw output:")
        print(output[:500])
        return []


def run_extraction(transcript: str, roster_context: str) -> list:
    return _run_claude_extraction(EXTRACTION_PROMPT_PATH, transcript, roster_context)


def run_betting_extraction(transcript: str, roster_context: str) -> list:
    return _run_claude_extraction(BETTING_EXTRACTION_PROMPT_PATH, transcript, roster_context)


def update_pipeline_status(cur, conn, **fields):
    if not fields:
        return
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    cur.execute(f"UPDATE pipeline_status SET {set_clause} WHERE id = 1", list(fields.values()))
    conn.commit()


def resolve_player(cur, raw_mention: str):
    cur.execute(
        "SELECT player_id FROM player_aliases WHERE lower(alias) = lower(%s)",
        (raw_mention,),
    )
    row = cur.fetchone()
    if row:
        return row[0], "high"

    cur.execute(
        """
        SELECT player_id, similarity(alias, %s) AS sim
        FROM player_aliases
        ORDER BY sim DESC
        LIMIT 1
        """,
        (raw_mention,),
    )
    row = cur.fetchone()
    if row and row[1] and row[1] > 0.35:
        return row[0], "low"

    return None, "unreviewed"


def insert_fantasy_quotes(cur, episode_id, content_week, quotes):
    for q in quotes:
        raw_mention = q.get("raw_player_mention", "")
        if raw_mention:
            player_id, confidence = resolve_player(cur, raw_mention)
        else:
            player_id, confidence = None, "unreviewed"

        cur.execute(
            """
            INSERT INTO quotes (
                episode_id, player_id, raw_player_mention, match_confidence,
                quote_text, speaker, timestamp_sec, tags, sentiment,
                fantasy_relevance, content_week
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (episode_id, quote_text, raw_player_mention) DO NOTHING
            """,
            (
                episode_id,
                player_id,
                raw_mention,
                confidence,
                q.get("quote_text", ""),
                q.get("speaker"),
                q.get("timestamp_sec"),
                json.dumps(q.get("tags", [])),
                q.get("sentiment", "neutral"),
                q.get("fantasy_relevance"),
                content_week,
            ),
        )


def insert_bet_quotes(cur, episode_id, content_week, bet_quotes):
    for q in bet_quotes:
        raw_mention = q.get("raw_player_mention", "")
        if raw_mention:
            player_id, confidence = resolve_player(cur, raw_mention)
        else:
            player_id, confidence = None, "unreviewed"

        cur.execute(
            """
            INSERT INTO bet_quotes (
                episode_id, player_id, raw_player_mention, match_confidence,
                bet_type, lean, sharp_or_public, line_context,
                quote_text, speaker, timestamp_sec, tags,
                betting_relevance, content_week
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (episode_id, quote_text, raw_player_mention) DO NOTHING
            """,
            (
                episode_id,
                player_id,
                raw_mention,
                confidence,
                q.get("bet_type", "player_other"),
                q.get("lean", "neutral"),
                q.get("sharp_or_public"),
                q.get("line_context"),
                q.get("quote_text", ""),
                q.get("speaker"),
                q.get("timestamp_sec"),
                json.dumps(q.get("tags", [])),
                q.get("betting_relevance"),
                content_week,
            ),
        )


def process_episode(episode_id, title, audio_url, content_week, category, model, cur, conn):
    raw_path = WORK_DIR / f"{episode_id}_raw.mp3"
    fast_path = WORK_DIR / f"{episode_id}_fast.mp3"

    update_pipeline_status(
        cur, conn,
        current_episode_id=episode_id, current_episode_title=title,
        stage="downloading", stage_started_at=datetime.now(timezone.utc),
    )

    # A retried episode (extraction timed out) already has its transcript.
    cur.execute("SELECT transcript FROM episodes WHERE id = %s", (episode_id,))
    transcript = (cur.fetchone() or [None])[0]

    if transcript:
        print(f"  Reusing saved transcript for episode {episode_id} (skipping download/transcribe).")
    else:
        print(f"  Downloading episode {episode_id}...")
        download_audio(audio_url, raw_path)

        print("  Speeding up audio...")
        speed_up_audio(raw_path, fast_path)

        update_pipeline_status(cur, conn, stage="transcribing", stage_started_at=datetime.now(timezone.utc))
        print("  Transcribing...")
        transcript = transcribe(fast_path, model)

        cur.execute(
            "UPDATE episodes SET status = 'transcribed', transcript = %s WHERE id = %s",
            (transcript, episode_id),
        )
        conn.commit()

    update_pipeline_status(cur, conn, stage="extracting", stage_started_at=datetime.now(timezone.utc))
    roster_context = build_roster_context(cur)

    if category == "betting":
        print("  Extracting betting takes...")
        bet_quotes = run_betting_extraction(transcript, roster_context)
        insert_bet_quotes(cur, episode_id, content_week, bet_quotes)
        n = len(bet_quotes)
    else:
        print("  Extracting quotes...")
        quotes = run_extraction(transcript, roster_context)
        insert_fantasy_quotes(cur, episode_id, content_week, quotes)
        n = len(quotes)

    cur.execute("UPDATE episodes SET status = 'extracted' WHERE id = %s", (episode_id,))
    conn.commit()
    print(f"  Done. {n} quote(s) extracted.")

    raw_path.unlink(missing_ok=True)
    fast_path.unlink(missing_ok=True)
    return n


def run():
    run_timestamp = datetime.now(timezone.utc)

    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT e.id, e.title, e.audio_url, e.content_week, p.category
        FROM episodes e JOIN podcasts p ON p.id = e.podcast_id
        WHERE e.status IN ('new', 'transcribed') AND e.audio_url IS NOT NULL
        ORDER BY (p.category = 'betting') DESC, e.id
        """
    )
    all_episodes = cur.fetchall()

    # Betting takes priority over fantasy - not just processed first within
    # a run (the ORDER BY above), but structurally guaranteed: a large
    # fantasy backlog can never crowd out betting content, since this run's
    # episode list is fixed once fetched (no re-checking the DB mid-run).
    # Capping fantasy episodes per run means the worst case is "fantasy
    # backlog takes a few extra runs to clear," never "betting has to wait
    # behind it." Uncapped betting episodes are rare enough (a handful of
    # shows) that no cap is needed on that side.
    betting_episodes = [e for e in all_episodes if e[4] == "betting"]
    fantasy_episodes = [e for e in all_episodes if e[4] != "betting"][:MAX_FANTASY_EPISODES_PER_RUN]
    episodes = betting_episodes + fantasy_episodes

    run_id = run_history.open_run(cur, conn)
    run_history.set_planned(cur, conn, run_id, len(episodes))

    if not episodes:
        print("No new episodes to process.")
        run_history.finish_run(cur, conn, run_id)
        cur.close()
        conn.close()
        return

    update_pipeline_status(
        cur, conn,
        is_running=True, run_started_at=run_timestamp,
        episodes_total_this_run=len(episodes), episodes_done_this_run=0,
        current_episode_id=None, current_episode_title=None,
        stage="loading_model", stage_started_at=run_timestamp,
        # Cleared here, not just left over from whatever this run's first
        # episode happens to take - otherwise a slow episode (or a stale
        # value from a resource-contention period) inflates the ETA shown
        # on the dashboard until the first episode of *this* run finishes.
        avg_seconds_per_episode=None,
    )

    print(f"Loading Whisper model ({WHISPER_MODEL_SIZE})...")
    # No GPU on this machine (nvidia-smi absent, ctranslate2 sees 0 CUDA
    # devices) - CPU is the only option. Default cpu_threads left the model
    # at ~4 threads while 12 cores were available (confirmed via top during
    # a live run: ~410% CPU, 64% idle overall). Raising it doesn't change
    # what gets transcribed or how - same model, same audio - so there's no
    # accuracy tradeoff, just more of the idle CPU put to use.
    model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8", cpu_threads=10)

    done = 0
    for episode_id, title, audio_url, content_week, category in episodes:
        try:
            n = process_episode(episode_id, title, audio_url, content_week, category, model, cur, conn)
            # Stamped when the episode actually finishes, not at run start, so
            # the timestamp says when its data landed.
            cur.execute(
                "UPDATE episodes SET processed_at = %s, error_message = NULL WHERE id = %s",
                (datetime.now(timezone.utc), episode_id),
            )
            conn.commit()
            run_history.add_progress(cur, conn, run_id, processed=1, items=n)
        except Exception as e:
            print(f"  ERROR processing episode {episode_id}: {e}")
            conn.rollback()
            cur.execute(
                "UPDATE episodes SET status = 'error', error_message = %s, processed_at = %s WHERE id = %s",
                (str(e)[:500], datetime.now(timezone.utc), episode_id),
            )
            conn.commit()
            run_history.add_progress(cur, conn, run_id, errored=1)
        finally:
            done += 1
            elapsed_total = (datetime.now(timezone.utc) - run_timestamp).total_seconds()
            update_pipeline_status(
                cur, conn,
                episodes_done_this_run=done,
                avg_seconds_per_episode=elapsed_total / done,
            )

    update_pipeline_status(
        cur, conn,
        is_running=False, current_episode_id=None, current_episode_title=None,
        stage=None, last_finished_at=datetime.now(timezone.utc),
    )
    run_history.finish_run(cur, conn, run_id)

    cur.close()
    conn.close()


if __name__ == "__main__":
    run()
