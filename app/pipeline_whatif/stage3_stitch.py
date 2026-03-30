"""Stage 3: ffmpeg stitch — concatenate clips at original speed (no slow-mo)."""
import subprocess
from pathlib import Path

from app.schemas.whatif_schema import WhatIfJob
from app.core.logger import get_logger

logger = get_logger(__name__)


def run(job: WhatIfJob, work_dir: Path) -> str:
    clips = job.clip_paths
    if not clips:
        raise RuntimeError(f"[{job.job_id}] No clips to stitch")

    inputs = []
    for clip in clips:
        inputs += ["-i", clip]

    concat_refs = "".join(f"[{i}:v]" for i in range(len(clips)))
    concat_filter = f"{concat_refs}concat=n={len(clips)}:v=1:a=0[outv]"

    out_path = str(work_dir / "stitched.mp4")
    cmd = (
        ["ffmpeg", "-y"]
        + inputs
        + ["-filter_complex", concat_filter,
           "-map", "[outv]",
           "-c:v", "libx264",
           "-crf", "16",
           "-preset", "slow",
           "-pix_fmt", "yuv420p",
           "-movflags", "+faststart",
           out_path]
    )
    logger.info("[%s] ffmpeg stitch %d clips: %s", job.job_id, len(clips), " ".join(cmd))
    subprocess.run(cmd, check=True, capture_output=True)
    logger.info("[%s] Stitched → %s", job.job_id, out_path)
    return out_path
