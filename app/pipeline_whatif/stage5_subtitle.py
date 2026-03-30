"""
Stage 5: Burn subtitles using an ASS subtitle file + ffmpeg subtitles filter.
Uses available word timestamps when present; falls back to estimated timing.
ASS format is used instead of drawtext to reliably support Unicode/non-Latin text.
"""
import subprocess
from pathlib import Path

from app.schemas.whatif_schema import WhatIfJob
from app.core.logger import get_logger

logger = get_logger(__name__)

_FONT_SIZE = 52
_MAX_CHARS = 28        # max chars per subtitle line
_WORDS_PER_MIN = 130   # fallback estimate

# ASS centisecond timestamp: H:MM:SS.cs
def _ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds % 1) * 100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _write_ass(subtitles: list[dict], path: Path) -> None:
    """Write an ASS subtitle file.  MarginV=250 puts subs at ~72% on 1920px tall video."""
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        # White text, black outline 3px, centre-aligned (2), MarginV pushes from bottom
        "Style: Default,Arial,52,&H00FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,3,0,2,10,10,250,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for sub in subtitles:
        start = _ass_time(sub["start"])
        end   = _ass_time(sub["end"])
        text  = sub["text"].replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    path.write_text("\n".join(lines), encoding="utf-8")


def _estimate_timestamps(script: str) -> list[dict]:
    """Estimate word-level timings at _WORDS_PER_MIN speaking rate."""
    words = script.split()
    sec_per_word = 60.0 / _WORDS_PER_MIN
    timestamps, t = [], 0.5  # 0.5s lead-in
    for word in words:
        dur = sec_per_word * (1 + len(word) / 12)
        timestamps.append({"word": word, "start": t, "end": t + dur})
        t += dur + 0.04
    return timestamps


def _group_lines(timestamps: list[dict]) -> list[dict]:
    """Group consecutive words into subtitle lines ≤ _MAX_CHARS."""
    lines, current, start = [], [], None
    char_count = 0
    for t in timestamps:
        word = t["word"]
        if start is None:
            start = t["start"]
        if char_count + len(word) + 1 > _MAX_CHARS and current:
            lines.append({"text": " ".join(current), "start": start, "end": t["start"]})
            current, char_count, start = [word], len(word), t["start"]
        else:
            current.append(word)
            char_count += len(word) + 1
    if current:
        end = timestamps[-1]["end"] if timestamps else (start or 0) + 3.0
        lines.append({"text": " ".join(current), "start": start, "end": end})
    return lines


def run(job: WhatIfJob, video_path: str, work_dir: Path) -> str:
    timestamps = job.voiceover_timestamps or []
    script = job.brain_output.script

    if not timestamps:
        logger.info("[%s] No Whisper timestamps — estimating", job.job_id)
        timestamps = _estimate_timestamps(script)

    subtitles = _group_lines(timestamps)
    if not subtitles:
        logger.warning("[%s] No subtitle lines generated, skipping", job.job_id)
        return video_path

    ass_path = work_dir / "subtitles.ass"
    _write_ass(subtitles, ass_path)

    out_path = str(work_dir / "final.mp4")
    # subtitles= filter reads the ASS file; handles UTF-8 (including CJK/Vietnamese) correctly.
    # Escape the path for the ffmpeg filter string (colons must be escaped on Windows; on
    # macOS/Linux the path is unlikely to contain special chars, but be safe).
    safe_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
    subprocess.run(
        ["ffmpeg", "-y",
         "-i", video_path,
         "-vf", f"subtitles={safe_ass}",
         "-c:v", "libx264",
         "-crf", "18",
         "-preset", "fast",
         "-c:a", "copy",
         out_path],
        check=True, capture_output=True,
    )
    logger.info("[%s] Subtitles burned → %s", job.job_id, out_path)
    return out_path
