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
    landmark_name: Optional[str] = None  # clip 0 = None (overview), clips 1-N = landmark name for TTS


class BrainOutput(BaseModel):
    intro_phrase: str                    # short 6-8 word overview phrase for clip 0 TTS
    script: Optional[str] = None        # unused (kept for schema compat)
    voice_model: str = "en-US-Neural2-J"
    visuals: list[VisualConfig]
    vibe: str = "Cinematic"


class WhatIfRequest(BaseModel):
    topic: str
    model: str = "veo-3.1-fast-generate-preview"
    voice_model: str = "en-US-Neural2-J"
    language: str = "en"
    topic_type: str = "city_future"  # "city_future" | "fictional_realm"


class WhatIfJob(BaseModel):
    job_id: str
    topic: str
    model: str
    voice_model: str
    topic_type: str = "city_future"
    status: WhatIfStatus = WhatIfStatus.queued
    current_stage: Optional[str] = None
    stage_percent: int = 0
    brain_output: Optional[BrainOutput] = None
    clip_paths: list[str] = []
    voiceover_path: Optional[str] = None
    voiceover_timestamps: list[dict] = []
    clip_audio_paths: list[str] = []     # per-clip TTS audio, index-aligned with clip_paths
    output_video: Optional[str] = None
    output_duration_sec: Optional[float] = None
    logs: list[dict] = []
    error: Optional[str] = None
    # SSE: one Queue per connected client; terminal_event replayed for late joiners
    subscribers: list[asyncio.Queue] = []
    terminal_event: Optional[dict] = None

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
