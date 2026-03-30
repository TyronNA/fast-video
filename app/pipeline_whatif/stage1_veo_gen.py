"""Stage 1: Generate Veo clips in parallel (one per visual in brain output)."""
import asyncio
from pathlib import Path

from app.schemas.video_schema import GenerationTask, VideoGenerationConfig
from app.schemas.whatif_schema import WhatIfJob
from app.core.logger import get_logger
import app.services.vertex_service as vertex_service

logger = get_logger(__name__)

_NEGATIVE_PROMPT = (
    "blurry, low quality, camera shake, fast zoom, deformed, cartoon, "
    "text overlays, watermark, distorted faces, nsfw"
)

_SUPPORTED_DURATIONS = (4, 6, 8)


def _normalize_duration(duration: int) -> int:
    # Veo text_to_video currently accepts only 4/6/8 seconds.
    if duration in _SUPPORTED_DURATIONS:
        return duration
    normalized = min(_SUPPORTED_DURATIONS, key=lambda s: abs(s - duration))
    logger.warning("Unsupported duration=%s; normalized to %ss", duration, normalized)
    return normalized


async def run(job: WhatIfJob, work_dir: Path) -> list[str]:
    tasks = [
        _gen_clip(job, visual.prompt, visual.duration, i, work_dir)
        for i, visual in enumerate(job.brain_output.visuals)
    ]
    clip_paths = await asyncio.gather(*tasks)
    job.clip_paths = list(clip_paths)
    return job.clip_paths


async def _gen_clip(
    job: WhatIfJob,
    prompt: str,
    duration: int,
    index: int,
    work_dir: Path,
) -> str:
    duration = _normalize_duration(duration)
    enhanced = (
        f"{prompt} "
        "Cinematic, smooth motion, no text, no watermark, hyper-realistic, 8k."
    )
    config = VideoGenerationConfig(
        aspect_ratio="9:16",
        sample_count=1,
        generate_audio=False,
    )
    logger.info("[%s] Generating clip %d: %.80s...", job.job_id, index + 1, enhanced)

    output_path = await asyncio.to_thread(
        vertex_service.generate_video,
        enhanced,
        duration,
        job.model,
        GenerationTask.TEXT_TO_VIDEO,
        config,
    )
    # vertex_service.generate_video saves to exports/ — move to work_dir
    dest = work_dir / f"clip_{index:02d}.mp4"
    Path(output_path).rename(dest)
    logger.info("[%s] Clip %d saved → %s", job.job_id, index + 1, dest)
    return str(dest)
