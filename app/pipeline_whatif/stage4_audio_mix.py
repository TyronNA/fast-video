"""
Stage 4: Mux per-clip TTS voiceover into the stitched video.
No background music — place each clip's short TTS audio at its clip start position.
"""
import subprocess
from pathlib import Path

from pydub import AudioSegment

from app.schemas.whatif_schema import WhatIfJob
from app.core.logger import get_logger

logger = get_logger(__name__)


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

    position_ms = 0
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

    out_path = str(work_dir / "with_audio.mp4")
    subprocess.run(
        ["ffmpeg", "-y",
         "-i", video_path,
         "-i", audio_out,
         "-c:v", "copy",
         "-c:a", "aac",
         "-b:a", "320k",
         "-shortest",
         out_path],
        check=True, capture_output=True,
    )
    logger.info("[%s] Voiceover muxed → %s", job.job_id, out_path)
    return out_path
