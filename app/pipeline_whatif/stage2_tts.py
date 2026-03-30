"""Stage 2: TTS voiceover synthesis + word timestamps."""
from pathlib import Path

from app.schemas.whatif_schema import WhatIfJob
from app.services.tts_service import synthesize_speech
from app.core.logger import get_logger

logger = get_logger(__name__)


async def run(job: WhatIfJob, work_dir: Path) -> str:
    script = job.brain_output.script
    voice = job.voice_model
    output_path = str(work_dir / "voiceover.mp3")

    logger.info("[%s] TTS voice=%s script_len=%d", job.job_id, voice, len(script))
    result = await synthesize_speech(script, output_path, voice=voice)

    job.voiceover_path = result["audio_path"]
    job.voiceover_timestamps = result.get("timestamps", [])
    return job.voiceover_path
