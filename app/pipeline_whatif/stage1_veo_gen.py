"""Stage 1: Generate Veo clips in parallel (one per visual in brain output)."""
import asyncio
from pathlib import Path

from app.schemas.video_schema import GenerationTask, VideoGenerationConfig
from app.schemas.whatif_schema import WhatIfJob
from app.core.logger import get_logger
import app.services.vertex_service as vertex_service

logger = get_logger(__name__)

_NEGATIVE_PROMPT = (
    "blurry, low quality, static shot, locked-off camera, motionless camera, deformed, cartoon, "
    "text overlays, watermark, distorted faces, nsfw"
)

_SUPPORTED_DURATIONS = (4, 6, 8)
_MAX_RETRIES = 3


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
        "Cinematic, fast dynamic camera movement, sweeping pan, wide epic angle, "
        "no text, no watermark, hyper-realistic, 8k."
    )
    config = VideoGenerationConfig(
        aspect_ratio="9:16",
        sample_count=1,
        generate_audio=False,
    )
    logger.info("[%s] Generating clip %d:\n  prompt: %s", job.job_id, index + 1, enhanced)

    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            output_path = await asyncio.to_thread(
                vertex_service.generate_video,
                enhanced,
                duration,
                job.model,
                GenerationTask.TEXT_TO_VIDEO,
                config,
            )
            dest = work_dir / f"clip_{index:02d}.mp4"
            Path(output_path).rename(dest)
            logger.info("[%s] Clip %d saved → %s", job.job_id, index + 1, dest)
            return str(dest)
        except Exception as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                wait = 2 ** attempt  # 2s, then 4s
                logger.warning(
                    "[%s] Clip %d attempt %d/%d failed (%s), retrying in %ds...",
                    job.job_id, index + 1, attempt, _MAX_RETRIES, exc, wait,
                )
                await asyncio.sleep(wait)
            else:
                logger.error(
                    "[%s] Clip %d failed after %d attempts",
                    job.job_id, index + 1, _MAX_RETRIES,
                )
    raise last_exc
