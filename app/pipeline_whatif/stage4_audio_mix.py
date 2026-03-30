"""
Stage 4: Audio mixing — voiceover + BG music with side-chain ducking.
BG music ducks to a lower volume while the voiceover plays.
"""
import subprocess
from pathlib import Path

from pydub import AudioSegment

from app.schemas.whatif_schema import WhatIfJob
from app.core.logger import get_logger

logger = get_logger(__name__)

_BG_FULL_DB = -12     # BG volume during silence / intro / outro
_BG_DUCKED_DB = -26   # BG volume during voiceover
_DUCK_FADE_MS = 300   # Crossfade duration for ducking transitions
_BEST_SEG_WINDOW_MS = 500  # RMS window size for energy analysis


def _find_best_segment(audio: AudioSegment, target_ms: int) -> AudioSegment:
    """Return the most energetic contiguous slice of *audio* with length *target_ms*.

    Slides a window of size _BEST_SEG_WINDOW_MS across the track, computes RMS
    energy for each position, then picks the window-start with the highest
    cumulative energy that still fits *target_ms* of content.
    """
    total_ms = len(audio)
    if total_ms <= target_ms:
        # Track shorter than needed — loop it; no need to search
        looped = audio
        while len(looped) < target_ms:
            looped = looped + audio
        return looped[:target_ms]

    step = _BEST_SEG_WINDOW_MS
    best_start = 0
    best_energy = -1.0

    # We can only start a segment if it fits entirely within the track
    max_start = total_ms - target_ms
    for start in range(0, max_start + 1, step):
        window = audio[start: start + _BEST_SEG_WINDOW_MS]
        rms = window.rms
        if rms > best_energy:
            best_energy = rms
            best_start = start

    logger.info("Best BG segment: start=%dms rms=%.1f", best_start, best_energy)
    segment = audio[best_start: best_start + target_ms]
    # Fade in/out edges to avoid hard cuts
    fade_edge = min(500, target_ms // 8)
    return segment.fade_in(fade_edge).fade_out(fade_edge)


def _video_duration_ms(video_path: str) -> int:
    result = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1",
         video_path],
        capture_output=True, text=True, check=True,
    )
    return int(float(result.stdout.strip()) * 1000)


def run(job: WhatIfJob, video_path: str, work_dir: Path) -> str:
    vo_path = job.voiceover_path
    if not vo_path or not Path(vo_path).exists():
        logger.warning("[%s] No voiceover found, skipping audio mix", job.job_id)
        return video_path

    video_ms = _video_duration_ms(video_path)
    voiceover = AudioSegment.from_file(vo_path)
    vo_ms = len(voiceover)

    if vo_ms <= 0:
        logger.warning("[%s] Empty voiceover audio, skipping ducking", job.job_id)
        return video_path

    # Build BG music track
    bg_file = job.bg_music_path
    if bg_file and Path(bg_file).exists():
        bg_raw = AudioSegment.from_file(bg_file)
        logger.info("[%s] Using BG music: %s (%dms)", job.job_id, bg_file, len(bg_raw))
        bg_raw = _find_best_segment(bg_raw, video_ms)
    else:
        bg_raw = AudioSegment.silent(duration=video_ms)

    # Normalise BG to full level then build ducked version
    bg_full = bg_raw + _BG_FULL_DB
    bg_ducked = bg_raw + _BG_DUCKED_DB

    # Ducked window: [0, _DUCK_FADE_MS] fade down, [fade_ms, vo_ms - fade_ms] ducked,
    # [vo_ms - fade_ms, vo_ms] fade back up, [vo_ms, end] full again
    fade_ms = min(_DUCK_FADE_MS, max(0, vo_ms // 4))
    duck_middle_ms = max(0, vo_ms - 2 * fade_ms)

    if fade_ms == 0:
        mixed_bg = bg_ducked[:vo_ms] + bg_full[vo_ms:]
    else:
        fade_down = bg_full[:fade_ms].fade(
            to_gain=_BG_DUCKED_DB - _BG_FULL_DB,
            start=0,
            duration=fade_ms,
        )
        middle = bg_ducked[fade_ms: fade_ms + duck_middle_ms]
        fade_up_seg = bg_ducked[fade_ms + duck_middle_ms: vo_ms]
        fade_up = fade_up_seg.fade(
            from_gain=_BG_DUCKED_DB - _BG_FULL_DB,
            start=0,
            duration=min(fade_ms, len(fade_up_seg)),
        )
        tail = bg_full[vo_ms:]
        mixed_bg = fade_down + middle + fade_up + tail
    # Trim / pad to exact video length
    if len(mixed_bg) > video_ms:
        mixed_bg = mixed_bg[:video_ms]
    elif len(mixed_bg) < video_ms:
        mixed_bg = mixed_bg + AudioSegment.silent(duration=video_ms - len(mixed_bg))

    final_audio = mixed_bg.overlay(voiceover, position=0)

    audio_out = str(work_dir / "final_audio.mp3")
    final_audio.export(audio_out, format="mp3")

    # Mux audio into video
    out_path = str(work_dir / "with_audio.mp4")
    subprocess.run(
        ["ffmpeg", "-y",
         "-i", video_path,
         "-i", audio_out,
         "-c:v", "copy",
         "-c:a", "aac",
         "-b:a", "192k",
         "-shortest",
         out_path],
        check=True, capture_output=True,
    )
    logger.info("[%s] Audio mixed → %s", job.job_id, out_path)
    return out_path
