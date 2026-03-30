import asyncio
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class WhatIfStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class VisualConfig(BaseModel):
    prompt: str
    duration: int = 4


class BrainOutput(BaseModel):
    script: str
    voice_model: str = "vi-VN-Neural2-D"
    visuals: list[VisualConfig]
    vibe: str = "Cinematic"
    bg_music_suggestion: str = "epic_ambient.mp3"


class WhatIfRequest(BaseModel):
    topic: str
    model: str = "veo-3.1-fast-generate-preview"
    voice_model: str = "vi-VN-Neural2-D"
    language: str = "vi"
    bg_music: str = "assets/music/Phonk-Phonk-pr.mp3"


class WhatIfJob(BaseModel):
    job_id: str
    topic: str
    model: str
    voice_model: str
    status: WhatIfStatus = WhatIfStatus.queued
    current_stage: Optional[str] = None
    stage_percent: int = 0
    brain_output: Optional[BrainOutput] = None
    clip_paths: list[str] = []
    voiceover_path: Optional[str] = None
    voiceover_timestamps: list[dict] = []
    bg_music_path: Optional[str] = None
    output_video: Optional[str] = None
    output_duration_sec: Optional[float] = None
    logs: list[dict] = []
    error: Optional[str] = None
    event_queue: Optional[asyncio.Queue] = None

    model_config = {"arbitrary_types_allowed": True}


class WhatIfStartResponse(BaseModel):
    job_id: str
    status: WhatIfStatus


class WhatIfResultResponse(BaseModel):
    job_id: str
    status: WhatIfStatus
    output_video: Optional[str] = None
    duration_sec: Optional[float] = None
    brain_output: Optional[BrainOutput] = None
    error: Optional[str] = None
