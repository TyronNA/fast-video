"""Stage 2: Per-clip TTS — intro phrase for clip 0, landmark name for clips 1-N."""
import asyncio
import re
from pathlib import Path

from app.schemas.whatif_schema import WhatIfJob
from app.services.tts_service import synthesize_speech
from app.core.logger import get_logger

logger = get_logger(__name__)

_ONES = [
    "", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _two_digits(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + ("-" + _ONES[ones] if ones else "")


def _normalize_year_for_tts(text: str) -> str:
    """Convert 4-digit year numbers to their spoken form so TTS reads naturally.
    E.g. '2800' → 'twenty-eight hundred', '1920' → 'nineteen twenty'.
    Only matches standalone 4-digit numbers (word boundary), so '1880s' is untouched.
    """
    def _year_words(year: int) -> str:
        high, low = divmod(year, 100)
        if low == 0:
            return _two_digits(high) + " hundred"
        return _two_digits(high) + " " + _two_digits(low)

    return re.sub(r"\b([1-9]\d{3})\b", lambda m: _year_words(int(m.group())), text)


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
        if i == 0:
            texts.append(brain.intro_phrase)
        elif v.tts_script:
            texts.append(v.tts_script)
        else:
            texts.append(_short_landmark(v.landmark_name or ""))

    async def _tts_clip(i: int, text: str) -> str:
        if not text.strip():
            logger.info("[%s] Clip %d: no text, skipping TTS", job.job_id, i)
            return ""
        out_path = str(work_dir / f"clip_audio_{i:02d}.mp3")
        text = _normalize_year_for_tts(text)
        logger.info("[%s] TTS clip %d voice=%s text=%r", job.job_id, i, voice, text)
        result = await synthesize_speech(text, out_path, voice=voice)
        return result["audio_path"]

    paths = await asyncio.gather(*[_tts_clip(i, t) for i, t in enumerate(texts)])
    job.clip_audio_paths = list(paths)
    logger.info("[%s] Generated %d clip audio file(s)", job.job_id, len(paths))
    return job.clip_audio_paths
