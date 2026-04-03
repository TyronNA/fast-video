"""
Stage 4: Mux per-clip TTS voiceover into the stitched video, then speed up 1.2x.
Exports captions.srt alongside the video for CapCut import.
"""
import subprocess
from pathlib import Path

from pydub import AudioSegment

from app.schemas.whatif_schema import WhatIfJob
from app.core.logger import get_logger

logger = get_logger(__name__)

_SPEED = 1.2


def _clip_duration_ms(video_path: str) -> int:
    result = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1",
         video_path],
        capture_output=True, text=True, check=True,
    )
    return int(float(result.stdout.strip()) * 1000)


def _video_duration_ms(video_path: str) -> int:
    return _clip_duration_ms(video_path)


def _ms_to_srt_time(ms: int) -> str:
    h = ms // 3_600_000
    ms %= 3_600_000
    m = ms // 60_000
    ms %= 60_000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _export_srt(job: WhatIfJob, clip_durations_ms: list[int], work_dir: Path) -> None:
    brain = job.brain_output
    if not brain:
        return

    texts: list[str] = []
    for i, v in enumerate(brain.visuals):
        if i == 0:
            texts.append(brain.intro_phrase)
        elif v.tts_script:
            texts.append(v.tts_script)
        else:
            words = (v.landmark_name or "").strip().split()
            texts.append(" ".join(words[:5]))

    lines: list[str] = []
    position_ms = job.audio_offset_ms
    counter = 1
    for i, text in enumerate(texts):
        dur_ms = clip_durations_ms[i] if i < len(clip_durations_ms) else 4000
        if text.strip():
            # Adjust timestamps for 1.2x speed so SRT aligns with final video
            start_ms = int(position_ms / _SPEED)
            end_ms = int((position_ms + dur_ms) / _SPEED)
            lines.append(f"{counter}\n{_ms_to_srt_time(start_ms)} --> {_ms_to_srt_time(end_ms)}\n{text}")
            counter += 1
        position_ms += dur_ms

    srt_path = work_dir / "captions.srt"
    srt_path.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    logger.info("[%s] SRT exported → %s", job.job_id, srt_path)


def run(job: WhatIfJob, video_path: str, work_dir: Path) -> str:
    clip_audio_paths = job.clip_audio_paths
    if not clip_audio_paths:
        logger.warning("[%s] No clip audio, skipping audio mux", job.job_id)
        return video_path

    video_ms = _video_duration_ms(video_path)
    track = AudioSegment.silent(duration=video_ms)

    # Get actual duration of each source clip for accurate positioning
    clip_durations_ms: list[int] = []
    for clip_path in job.clip_paths:
        try:
            clip_durations_ms.append(_clip_duration_ms(clip_path))
        except Exception:
            clip_durations_ms.append(4000)

    position_ms = job.audio_offset_ms  # non-zero for timeline hook snippet (silent prepend)
    for i, audio_path in enumerate(clip_audio_paths):
        if not audio_path or not Path(audio_path).exists():
            position_ms += clip_durations_ms[i] if i < len(clip_durations_ms) else 4000
            continue
        if position_ms >= video_ms:
            break
        clip_audio = AudioSegment.from_file(audio_path)
        track = track.overlay(clip_audio, position=position_ms)
        logger.info("[%s] Clip %d TTS at %.2fs", job.job_id, i, position_ms / 1000)
        position_ms += clip_durations_ms[i] if i < len(clip_durations_ms) else 4000

    audio_out = str(work_dir / "voiceover.mp3")
    track.export(audio_out, format="mp3")

    muxed_path = str(work_dir / "with_audio.mp4")
    subprocess.run(
        ["ffmpeg", "-y",
         "-i", video_path,
         "-i", audio_out,
         "-c:v", "copy",
         "-c:a", "aac",
         "-b:a", "320k",
         "-shortest",
         muxed_path],
        check=True, capture_output=True,
    )
    logger.info("[%s] Voiceover muxed → %s", job.job_id, muxed_path)

    # Speed up 1.2x for higher Shorts retention
    final_path = str(work_dir / "final.mp4")
    subprocess.run(
        ["ffmpeg", "-y",
         "-i", muxed_path,
         "-vf", f"setpts=PTS/{_SPEED}",
         "-af", f"atempo={_SPEED}",
         "-c:v", "libx264", "-crf", "16", "-preset", "fast",
         "-pix_fmt", "yuv420p",
         final_path],
        check=True, capture_output=True,
    )
    logger.info("[%s] Speed %.1fx → %s", job.job_id, _SPEED, final_path)

    # Export SRT with speed-adjusted timestamps for CapCut import
    _export_srt(job, clip_durations_ms, work_dir)

    return final_path
