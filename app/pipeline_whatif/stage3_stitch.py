"""
Stage 3: ffmpeg stitch + optional slow-mo to reach 18-20s target duration.
Uses setpts filter for frame-rate-independent slow-motion (no audio needed here).
"""
import subprocess
from pathlib import Path

from app.schemas.whatif_schema import WhatIfJob
from app.core.logger import get_logger

logger = get_logger(__name__)

_TARGET_MIN_S = 18.0
_TARGET_MAX_S = 20.0
_SLOWMO_FLOOR = 0.5   # Maximum slow-down: 0.5x (2× slower)


def _duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1",
         path],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def run(job: WhatIfJob, work_dir: Path) -> str:
    clips = job.clip_paths
    if not clips:
        raise RuntimeError(f"[{job.job_id}] No clips to stitch")

    total_raw = sum(_duration(c) for c in clips)
    logger.info("[%s] Raw clips: %.1fs, target: %.0f-%.0fs", job.job_id, total_raw, _TARGET_MIN_S, _TARGET_MAX_S)

    # Compute speed factor: < 1.0 means slow-motion
    speed = 1.0
    if total_raw < _TARGET_MIN_S:
        speed = max(total_raw / _TARGET_MIN_S, _SLOWMO_FLOOR)
        logger.info("[%s] Applying slow-mo: speed=%.3fx", job.job_id, speed)

    # Build ffmpeg filter_complex: setpts for each clip, then concat
    inputs = []
    filter_parts = []
    for i, clip in enumerate(clips):
        inputs += ["-i", clip]
        pts_factor = 1.0 / speed  # setpts=N*PTS slows by factor N
        filter_parts.append(f"[{i}:v]setpts={pts_factor:.4f}*PTS[v{i}]")

    concat_refs = "".join(f"[v{i}]" for i in range(len(clips)))
    concat_filter = ";".join(filter_parts) + f";{concat_refs}concat=n={len(clips)}:v=1:a=0[outv]"

    out_path = str(work_dir / "stitched.mp4")
    cmd = (
        ["ffmpeg", "-y"]
        + inputs
        + ["-filter_complex", concat_filter,
           "-map", "[outv]",
           "-c:v", "libx264",
           "-crf", "18",
           "-preset", "fast",
           "-pix_fmt", "yuv420p",
           out_path]
    )
    logger.info("[%s] ffmpeg stitch: %s", job.job_id, " ".join(cmd))
    subprocess.run(cmd, check=True, capture_output=True)

    final_dur = _duration(out_path)
    logger.info("[%s] Stitched → %.1fs at %s", job.job_id, final_dur, out_path)
    return out_path
