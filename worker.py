"""
Processes episodes with status='new': downloads audio, speeds it up,
transcribes with faster-whisper, runs the Claude Code CLI extraction
prompt, and inserts results into the quotes table.

Usage:
    python worker.py
"""
import os
import json
import subprocess
import requests
from pathlib import Path
from datetime import datetime, timezone
from faster_whisper import WhisperModel
from db import get_conn

WORK_DIR = Path(os.getenv("WORK_DIR", "./work"))
WORK_DIR.mkdir(parents=True, exist_ok=True)

EXTRACTION_PROMPT_PATH = Path(__file__).parent / "extraction_prompt.md"
WHISPER_MODEL_SIZE = "small"
SPEED_FACTOR = "1.5"


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
    segments, _info = model.transcribe(str(audio_path))
    lines = []
    for seg in segments:
        lines.append(f"[{seg.start:.0f}s] {seg.text.strip()}")
    return "\n".join(lines)


def build_roster_context(cur) -> str:
    cur.execute("SELECT full_name, team, position FROM players WHERE active = true")
    rows = cur.fetchall()
    return "\n".join(f"{name} ({team} - {pos})" for name, team, pos in rows)


def run_extraction(transcript: str, roster_context: str) -> list:
    prompt = EXTRACTION_PROMPT_PATH.read_text()
    stdin_content = (
        f"---ROSTER CONTEXT---\n{roster_context}\n\n"
        f"---TRANSCRIPT---\n{transcript}"
    )
    result = subprocess.run(
        ["claude", "-p", prompt],
        input=stdin_content,
        capture_output=True,
        text=True,
        timeout=600,
    )
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


def process_episode(episode_id, audio_url, content_week, model, cur, conn):
    raw_path = WORK_DIR / f"{episode_id}_raw.mp3"
    fast_path = WORK_DIR / f"{episode_id}_fast.mp3"

    print(f"  Downloading episode {episode_id}...")
    download_audio(audio_url, raw_path)

    print("  Speeding up audio...")
    speed_up_audio(raw_path, fast_path)

    print("  Transcribing...")
    transcript = transcribe(fast_path, model)

    cur.execute(
        "UPDATE episodes SET status = 'transcribed', transcript = %s WHERE id = %s",
        (transcript, episode_id),
    )
    conn.commit()

    print("  Extracting quotes...")
    roster_context = build_roster_context(cur)
    quotes = run_extraction(transcript, roster_context)

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

    cur.execute("UPDATE episodes SET status = 'extracted' WHERE id = %s", (episode_id,))
    conn.commit()
    print(f"  Done. {len(quotes)} quote(s) extracted.")

    raw_path.unlink(missing_ok=True)
    fast_path.unlink(missing_ok=True)


def run():
    run_timestamp = datetime.now(timezone.utc)

    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        "SELECT id, audio_url, content_week FROM episodes WHERE status = 'new' AND audio_url IS NOT NULL"
    )
    episodes = cur.fetchall()

    if not episodes:
        print("No new episodes to process.")
        return

    print(f"Loading Whisper model ({WHISPER_MODEL_SIZE})...")
    model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")

    for episode_id, audio_url, content_week in episodes:
        try:
            process_episode(episode_id, audio_url, content_week, model, cur, conn)
            cur.execute("UPDATE episodes SET processed_at = %s WHERE id = %s", (run_timestamp, episode_id))
            conn.commit()
        except Exception as e:
            print(f"  ERROR processing episode {episode_id}: {e}")
            cur.execute("UPDATE episodes SET status = 'error' WHERE id = %s", (episode_id,))
            conn.commit()

    cur.close()
    conn.close()


if __name__ == "__main__":
    run()
