"""Stage 2: Per-clip TTS — intro phrase for clip 0, landmark name for clips 1-N."""
import asyncio
from pathlib import Path

from app.schemas.whatif_schema import WhatIfJob
from app.services.tts_service import synthesize_speech
from app.core.logger import get_logger

logger = get_logger(__name__)


async def run(job: WhatIfJob, work_dir: Path) -> list[str]:
    brain = job.brain_output
    voice = job.voice_model

    # Build per-clip text list — exactly N texts for N clips:
    # clip 0 (overview)     → intro_phrase
    # clip i (i ≥ 1)        → visuals[i].landmark_name
    def _short_landmark(name: str) -> str:
        words = name.strip().split()
        return " ".join(words[:5])

    texts: list[str] = []
    for i, v in enumerate(brain.visuals):
        texts.append(brain.intro_phrase if i == 0 else _short_landmark(v.landmark_name or ""))

    async def _tts_clip(i: int, text: str) -> str:
        if not text.strip():
            logger.info("[%s] Clip %d: no text, skipping TTS", job.job_id, i)
            return ""
        out_path = str(work_dir / f"clip_audio_{i:02d}.mp3")
        logger.info("[%s] TTS clip %d voice=%s text=%r", job.job_id, i, voice, text)
        result = await synthesize_speech(text, out_path, voice=voice)
        return result["audio_path"]

    paths = await asyncio.gather(*[_tts_clip(i, t) for i, t in enumerate(texts)])
    job.clip_audio_paths = list(paths)
    logger.info("[%s] Generated %d clip audio file(s)", job.job_id, len(paths))
    return job.clip_audio_paths
