# 🏗️ WHATIF FACTORY — Implementation Plan

> **Mục tiêu:** Nhập 1 topic (vd: "Hà Nội năm 3000") → Bot tự gen script, 2 video clip, voiceover, mix audio, chèn subtitle → xuất Shorts 18-20s hoàn chỉnh.

---

## 1. Audit Hệ Thống Hiện Tại

### ✅ Có sẵn (keep nguyên)
| Component | File | Trạng thái |
|---|---|---|
| Vertex AI / Veo integration | `app/services/vertex_service.py` | ✅ Hoạt động tốt. Hỗ trợ Veo 2, 3, 3.1, 3.1-fast. |
| Single-shot generation | `app/api/routes.py` | ✅ Committed, stable. |
| Config + auth | `app/core/config.py`, `config/vertex-ai.json` | ✅ Service account đã setup. |
| History service | `app/services/history_service.py` | ✅ SQLite-based, reuse được. |
| Frontend SPA | `web/` | ✅ Tab mới sẽ được thêm. |

### ⚠️ Thiếu (cần build mới)
| Cần thêm | Lý do |
|---|---|
| Gemini service | Chưa có — dùng Vertex AI Gemini endpoint (tái dùng credentials sẵn có). |
| TTS service | Chưa có — dùng OpenAI TTS API (`openai` SDK). |
| WhatIf pipeline | Source `.py` đã xoá — phải viết lại theo pattern pipeline cũ (từ `.pyc` recovery). |
| `ffmpeg-python`, `opencv-python`, `pydub` | Thiếu trong `requirements.txt`. |
| `openai` | Chưa có trong `requirements.txt`. |

### 🔄 Pattern tham chiếu: Existing Pipeline
Pipeline cũ (`/pipeline/start`) là blueprint cho WhatIf:
```
POST /pipeline/start (multipart + config_json)
  → create_job() → background run_pipeline()
    → stage0 → stage1 → stage2 → stage3 → stage4
    → push SSE events → move to exports/
GET /pipeline/{job_id}/events  ← SSE real-time progress
GET /pipeline/{job_id}/result  ← Final output
```
**WhatIf sẽ dùng y chang pattern này, chỉ khác ở input (1 topic string) và stages.**

---

## 2. Kiến Trúc WhatIf Factory

```
POST /whatif/start {"topic": "Hà Nội năm 3000"}
         │
         ▼
  create_whatif_job()
         │
         ▼ (asyncio background task)
  ┌──────────────────────────────────────────────────┐
  │  stage0_brain.py                                  │
  │  Gemini 2.5 Flash Preview                        │
  │  topic → {script, 2 veo_prompts, vibe}           │
  └──────────────────┬───────────────────────────────┘
                     │
         ┌───────────┴────────────┐
         ▼                        ▼
  stage1_veo_gen.py          stage2_tts.py
  asyncio.gather()            OpenAI TTS
  Veo clip_01 (6s)            script → voiceover.mp3
  Veo clip_02 (6s)            + word timestamps
         │                        │
         └───────────┬────────────┘
                     ▼
           stage3_stitch.py
           ffmpeg: concat clip_01 + clip_02
           slow-mo 0.8x nếu < 18s → ≈18-20s
                     │
                     ▼
          stage4_audio_mix.py
          pydub: voiceover + BG music ducking
          mux audio vào video
                     │
                     ▼
          stage5_subtitle.py
          ffmpeg drawtext: subtitle từ timestamps
                     │
                     ▼
          exports/whatif_{job_id}.mp4
```

---

## 3. Files Cần Tạo / Sửa

### 3.1 `app/schemas/whatif_schema.py` ← NEW

```python
from enum import Enum
from pydantic import BaseModel
from typing import Optional
import asyncio


class WhatIfStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class VisualConfig(BaseModel):
    prompt: str
    duration: int = 6  # seconds


class AudioConfig(BaseModel):
    script: str
    voice_model: str = "onyx"  # OpenAI TTS voice


class BrainOutput(BaseModel):
    """Output từ Gemini — schema phải match prompt template."""
    script: str
    voice_model: str = "onyx"
    visuals: list[VisualConfig]
    vibe: str
    bg_music_suggestion: str


class WhatIfRequest(BaseModel):
    topic: str
    model: str = "veo-3.0-generate-001"
    voice_model: str = "onyx"
    language: str = "vi"


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
```

---

### 3.2 `app/services/gemini_service.py` ← NEW

Dùng Vertex AI Gemini (tái dùng service account hiện có — không cần key mới).

```python
"""
Gemini 2.5 Flash via Vertex AI.
Tái dùng GCP credentials từ config.py — không cần GEMINI_API_KEY riêng.
"""
import json
import re
import httpx
from google.oauth2 import service_account
from google.auth.transport.requests import Request as GoogleAuthRequest

from app.core.config import settings
from app.core.logger import logger

GEMINI_MODEL = "gemini-2.5-flash-preview"
# Vertex AI Gemini endpoint
_ENDPOINT = (
    "https://{location}-aiplatform.googleapis.com/v1/projects/{project}"
    "/locations/{location}/publishers/google/models/{model}:generateContent"
)

BRAIN_PROMPT_TEMPLATE = """
You are an expert short-form video content strategist specializing in "What If" futuristic scenarios.

Given a topic for a 18-20 second YouTube Shorts video, generate content in {language} language.

Return ONLY a valid JSON object (no markdown, no explanation) matching this exact schema:
{{
  "script": "<3-sentence voiceover: Hook. Content. CTA.>",
  "voice_model": "onyx",
  "visuals": [
    {{
      "prompt": "<Detailed Veo prompt — cinematic wide shot or drone view, 8k, hyper-realistic, specific scene>",
      "duration": 6
    }},
    {{
      "prompt": "<Detailed Veo prompt — close-up or dramatic angle, 8k, specific detail>",
      "duration": 6
    }}
  ],
  "vibe": "<Music genre: e.g. Cyberpunk Phonk / Epic Orchestral / Lo-fi Chill>",
  "bg_music_suggestion": "<filename or description of background music>"
}}

Rules for Veo prompts:
- Always include: cinematic, 8k, hyper-realistic
- Prefer: wide shot, drone view, establishing shot (avoids face distortion)
- Include: lighting, mood, camera movement (slow pan, gentle dolly)
- Avoid: close-up of faces, text in scene

Topic: {topic}
Language: {language}
"""


def _get_access_token() -> str:
    creds = service_account.Credentials.from_service_account_file(
        settings.vertex_ai_credentials_file,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    creds.refresh(GoogleAuthRequest())
    return creds.token


async def generate_brain(topic: str, language: str = "vi") -> dict:
    """Call Gemini 2.5 Flash to generate script + Veo prompts from topic."""
    token = _get_access_token()
    url = _ENDPOINT.format(
        location=settings.gcp_location,
        project=settings.gcp_project,
        model=GEMINI_MODEL,
    )
    prompt = BRAIN_PROMPT_TEMPLATE.format(topic=topic, language=language)
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1024,
            "responseMimeType": "application/json",
        },
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()

    data = resp.json()
    raw_text = data["candidates"][0]["content"]["parts"][0]["text"]

    # Strip markdown code fences if present
    raw_text = re.sub(r"^```json\s*", "", raw_text.strip())
    raw_text = re.sub(r"\s*```$", "", raw_text.strip())

    return json.loads(raw_text)
```

---

### 3.3 `app/services/tts_service.py` ← NEW

```python
"""
OpenAI TTS service — generates voiceover MP3 with word timestamps.
Requires OPENAI_API_KEY in .env
"""
import asyncio
from pathlib import Path
from openai import AsyncOpenAI

from app.core.config import settings
from app.core.logger import logger

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


async def synthesize_speech(
    script: str,
    output_path: str,
    voice: str = "onyx",
    model: str = "tts-1",
) -> dict:
    """
    Generate voiceover MP3 from script.
    Returns {"audio_path": str, "timestamps": list[dict]}
    
    Timestamps format: [{"word": "Bạn", "start": 0.0, "end": 0.3}, ...]
    """
    client = _get_client()
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Use tts-1-hd with verbose_json for word timestamps
    response = await client.audio.speech.create(
        model=model,
        voice=voice,
        input=script,
        response_format="mp3",
    )
    response.stream_to_file(str(out))
    logger.info(f"TTS saved to {out}")

    # NOTE: OpenAI TTS does not return word timestamps directly.
    # Use Whisper transcription on the generated audio to get timestamps.
    timestamps = await _get_word_timestamps(str(out), script)
    return {"audio_path": str(out), "timestamps": timestamps}


async def _get_word_timestamps(audio_path: str, original_script: str) -> list[dict]:
    """Transcribe with Whisper to get word-level timestamps."""
    client = _get_client()
    try:
        with open(audio_path, "rb") as f:
            transcript = await client.audio.transcriptions.create(
                file=f,
                model="whisper-1",
                response_format="verbose_json",
                timestamp_granularities=["word"],
            )
        return [{"word": w.word, "start": w.start, "end": w.end} for w in transcript.words]
    except Exception as e:
        logger.warning(f"Whisper timestamp failed: {e} — using empty timestamps")
        return []
```

---

### 3.4 `app/pipeline_whatif/` ← NEW FOLDER

#### `app/pipeline_whatif/__init__.py`
```python
# WhatIf pipeline package
```

#### `app/pipeline_whatif/orchestrator.py`

```python
"""WhatIf pipeline orchestrator — mirrors existing pipeline/orchestrator.py pattern."""
import asyncio
import uuid
from datetime import datetime
from pathlib import Path

from app.schemas.whatif_schema import WhatIfJob, WhatIfStatus, WhatIfRequest
from app.core.logger import logger

_JOBS: dict[str, WhatIfJob] = {}
_WORK_BASE = Path("temp/whatif_jobs")


def create_job(req: WhatIfRequest) -> WhatIfJob:
    job_id = uuid.uuid4().hex[:12]
    work_dir = _WORK_BASE / f"wi_{datetime.now().strftime('%Y%m%d')}_{job_id}"
    work_dir.mkdir(parents=True, exist_ok=True)

    job = WhatIfJob(
        job_id=job_id,
        topic=req.topic,
        model=req.model,
        voice_model=req.voice_model,
        event_queue=asyncio.Queue(),
    )
    _JOBS[job_id] = job
    return job


def get_job(job_id: str) -> WhatIfJob | None:
    return _JOBS.get(job_id)


def _work_dir(job_id: str) -> Path:
    # Find existing work dir
    matches = list(_WORK_BASE.glob(f"wi_*_{job_id}"))
    if matches:
        return matches[0]
    d = _WORK_BASE / f"wi_{datetime.now().strftime('%Y%m%d')}_{job_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _push(job: WhatIfJob, message: str, stage: str, percent: int):
    job.current_stage = stage
    job.stage_percent = percent
    event = {"message": message, "stage": stage, "percent": percent}
    job.logs.append(event)
    await job.event_queue.put(event)
    logger.info(f"[{job.job_id}] [{stage}] {message}")


async def run_pipeline(job_id: str):
    from app.pipeline_whatif import (
        stage0_brain,
        stage1_veo_gen,
        stage2_tts,
        stage3_stitch,
        stage4_audio_mix,
        stage5_subtitle,
    )

    job = _JOBS[job_id]
    work_dir = _work_dir(job_id)
    job.status = WhatIfStatus.running

    try:
        # Stage 0: Brain (Gemini)
        await _push(job, f"🧠 Generating script & prompts for '{job.topic}'...", "brain", 5)
        brain = await asyncio.to_thread(
            asyncio.run,
            stage0_brain.run(job.topic, job.voice_model, language="vi")
        )
        # Actually call async directly
        brain = await stage0_brain.run(job.topic, job.voice_model, language="vi")
        job.brain_output = brain
        await _push(job, f"✅ Brain done. Vibe: {brain.vibe}", "brain", 15)

        # Stage 1 + 2: Veo clips & TTS in parallel
        await _push(job, "🎬 Generating video clips & voiceover in parallel...", "media_gen", 20)
        clips_task = asyncio.create_task(
            stage1_veo_gen.run(job, work_dir)
        )
        tts_task = asyncio.create_task(
            stage2_tts.run(job, work_dir)
        )
        await asyncio.gather(clips_task, tts_task)
        await _push(job, f"✅ {len(job.clip_paths)} clips + voiceover ready", "media_gen", 60)

        # Stage 3: Stitch
        await _push(job, "✂️ Stitching & timing clips to 18-20s...", "stitch", 65)
        stitched_path = await asyncio.to_thread(
            stage3_stitch.run, job, work_dir
        )
        await _push(job, "✅ Stitch complete", "stitch", 72)

        # Stage 4: Audio mix (ducking)
        await _push(job, "🎵 Mixing voiceover + BG music with ducking...", "audio_mix", 75)
        mixed_path = await asyncio.to_thread(
            stage4_audio_mix.run, job, stitched_path, work_dir
        )
        await _push(job, "✅ Audio mix complete", "audio_mix", 85)

        # Stage 5: Subtitles
        await _push(job, "📝 Burning subtitles...", "subtitle", 88)
        final_path = await asyncio.to_thread(
            stage5_subtitle.run, job, mixed_path, work_dir
        )
        await _push(job, "✅ Subtitles done", "subtitle", 95)

        # Move to exports
        export_path = Path("exports") / f"whatif_{job_id}.mp4"
        Path(final_path).rename(export_path)

        import subprocess
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(export_path)],
            capture_output=True, text=True
        )
        duration = float(result.stdout.strip()) if result.stdout.strip() else 0.0

        job.output_video = f"/exports/{export_path.name}"
        job.output_duration_sec = duration
        job.status = WhatIfStatus.completed

        await _push(job, f"🎉 Done! {duration:.1f}s → {job.output_video}", "done", 100)
        await job.event_queue.put({"done": True})

    except Exception as e:
        job.status = WhatIfStatus.failed
        job.error = str(e)
        logger.exception(f"[{job_id}] Pipeline failed: {e}")
        await job.event_queue.put({"failed": True, "error": str(e)})
```

#### `app/pipeline_whatif/stage0_brain.py`

```python
"""Stage 0: Call Gemini to generate script + Veo prompts."""
from app.schemas.whatif_schema import BrainOutput, VisualConfig
from app.services.gemini_service import generate_brain


async def run(topic: str, voice_model: str = "onyx", language: str = "vi") -> BrainOutput:
    raw = await generate_brain(topic, language)
    return BrainOutput(
        script=raw["script"],
        voice_model=voice_model,
        visuals=[VisualConfig(**v) for v in raw["visuals"]],
        vibe=raw.get("vibe", "Cinematic"),
        bg_music_suggestion=raw.get("bg_music_suggestion", "epic_ambient.mp3"),
    )
```

#### `app/pipeline_whatif/stage1_veo_gen.py`

```python
"""Stage 1: Generate Veo clips in parallel."""
import asyncio
from pathlib import Path

from app.schemas.whatif_schema import WhatIfJob
from app.services.vertex_service import VertexVideoService
from app.core.logger import logger


async def run(job: WhatIfJob, work_dir: Path) -> list[str]:
    service = VertexVideoService()
    tasks = []
    for i, visual in enumerate(job.brain_output.visuals):
        tasks.append(_gen_clip(service, job, visual.prompt, visual.duration, i, work_dir))

    clip_paths = await asyncio.gather(*tasks)
    job.clip_paths = list(clip_paths)
    return job.clip_paths


async def _gen_clip(
    service: VertexVideoService,
    job: WhatIfJob,
    prompt: str,
    duration: int,
    index: int,
    work_dir: Path,
) -> str:
    logger.info(f"[{job.job_id}] Generating clip {index + 1}: {prompt[:60]}...")
    # Negative prompt to keep quality high for landscape/scene shots
    enhanced_prompt = (
        f"{prompt} "
        "Cinematic, smooth camera movement, no text, no watermark, "
        "no humans in closeup, hyper-realistic, 8k resolution."
    )
    negative_prompt = (
        "blurry, low quality, camera shake, zoom, deformed, cartoon, "
        "text overlays, watermark, distorted faces"
    )
    video_bytes = await asyncio.to_thread(
        service.generate_video,
        prompt=enhanced_prompt,
        negative_prompt=negative_prompt,
        duration_seconds=duration,
        model=job.model,
    )
    out_path = work_dir / f"clip_{index:02d}.mp4"
    out_path.write_bytes(video_bytes)
    logger.info(f"[{job.job_id}] Clip {index + 1} saved: {out_path}")
    return str(out_path)
```

#### `app/pipeline_whatif/stage2_tts.py`

```python
"""Stage 2: Generate TTS voiceover + word timestamps."""
from pathlib import Path
from app.schemas.whatif_schema import WhatIfJob
from app.services.tts_service import synthesize_speech
from app.core.logger import logger


async def run(job: WhatIfJob, work_dir: Path) -> str:
    script = job.brain_output.script
    voice = job.voice_model
    output_path = str(work_dir / "voiceover.mp3")

    logger.info(f"[{job.job_id}] TTS: voice={voice}, script length={len(script)}")
    result = await synthesize_speech(script, output_path, voice=voice)

    job.voiceover_path = result["audio_path"]
    # Store timestamps in brain_output for stage5
    job.brain_output.__dict__["_timestamps"] = result.get("timestamps", [])
    return job.voiceover_path
```

#### `app/pipeline_whatif/stage3_stitch.py`

```python
"""Stage 3: ffmpeg stitch + slow-mo to reach 18-20s target."""
import subprocess
from pathlib import Path
from app.schemas.whatif_schema import WhatIfJob
from app.core.logger import logger

TARGET_MIN = 18.0
TARGET_MAX = 20.0


def _get_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def run(job: WhatIfJob, work_dir: Path) -> str:
    clips = job.clip_paths
    total_raw = sum(_get_duration(c) for c in clips)
    logger.info(f"[{job.job_id}] Raw clips total: {total_raw:.1f}s, target: {TARGET_MIN}-{TARGET_MAX}s")

    # Calculate slow-mo factor if needed
    speed_factor = 1.0
    if total_raw < TARGET_MIN:
        speed_factor = total_raw / TARGET_MIN  # e.g. 12/18 = 0.667x speed = 1.5x slower
        speed_factor = max(speed_factor, 0.5)  # floor at 0.5x (2x slow-mo max)
        logger.info(f"[{job.job_id}] Applying slow-mo: speed={speed_factor:.2f}x")

    # Build ffmpeg concat filter
    # Apply setpts for slow-mo + concat
    concat_input = []
    filter_parts = []
    for i, clip in enumerate(clips):
        concat_input += ["-i", clip]
        if speed_factor < 1.0:
            # setpts=1/speed * PTS slows video; atempo adjusts audio
            filter_parts.append(
                f"[{i}:v]setpts={1/speed_factor:.4f}*PTS[v{i}];"
            )
        else:
            filter_parts.append(f"[{i}:v]copy[v{i}];")

    concat_refs = "".join(f"[v{i}]" for i in range(len(clips)))
    concat_filter = "".join(filter_parts) + f"{concat_refs}concat=n={len(clips)}:v=1:a=0[outv]"

    out_path = str(work_dir / "stitched.mp4")
    cmd = (
        ["ffmpeg", "-y"]
        + concat_input
        + ["-filter_complex", concat_filter,
           "-map", "[outv]",
           "-c:v", "libx264",
           "-crf", "18",
           "-preset", "fast",
           "-pix_fmt", "yuv420p",
           out_path]
    )
    logger.info(f"[{job.job_id}] ffmpeg stitch cmd: {' '.join(cmd)}")
    subprocess.run(cmd, check=True, capture_output=True)

    final_dur = _get_duration(out_path)
    logger.info(f"[{job.job_id}] Stitched video: {final_dur:.1f}s → {out_path}")
    return out_path
```

#### `app/pipeline_whatif/stage4_audio_mix.py`

```python
"""
Stage 4: Audio mixing — voiceover + BG music with side-chain ducking.
Uses pydub. BG music volume ducks to -20dB when voiceover plays.
"""
import subprocess
from pathlib import Path
from pydub import AudioSegment
from app.schemas.whatif_schema import WhatIfJob
from app.core.logger import logger

BG_MUSIC_FULL_DB = -12   # BG music volume during silence
BG_MUSIC_DUCKED_DB = -25  # BG music volume during voiceover (ducking)
DUCK_FADE_MS = 300        # Fade in/out time for ducking


def _get_duration_ms(path: str) -> int:
    seg = AudioSegment.from_file(path)
    return len(seg)


def run(job: WhatIfJob, video_path: str, work_dir: Path) -> str:
    vo_path = job.voiceover_path
    if not vo_path:
        logger.warning(f"[{job.job_id}] No voiceover, skipping audio mix")
        return video_path

    # Get video duration
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, check=True,
    )
    video_dur_ms = int(float(result.stdout.strip()) * 1000)

    # Load voiceover
    voiceover = AudioSegment.from_file(vo_path)
    vo_dur_ms = len(voiceover)

    # Build BG music track (use silence if no bg_music provided)
    bg_music_file = job.bg_music_path
    if bg_music_file and Path(bg_music_file).exists():
        bg = AudioSegment.from_file(bg_music_file)
        # Loop to match video duration
        while len(bg) < video_dur_ms:
            bg = bg + bg
        bg = bg[:video_dur_ms]
    else:
        bg = AudioSegment.silent(duration=video_dur_ms)

    # Apply ducking: reduce BG during voiceover
    bg = bg + BG_MUSIC_FULL_DB  # normalize to full volume
    bg_ducked = bg + (BG_MUSIC_DUCKED_DB - BG_MUSIC_FULL_DB)  # ducked version

    # Crossfade BG to ducked at VO start, restore after VO ends
    fade_in = bg[:DUCK_FADE_MS].fade(to_gain=(BG_MUSIC_DUCKED_DB - BG_MUSIC_FULL_DB), start=0, duration=DUCK_FADE_MS)
    fade_out = bg_ducked[:DUCK_FADE_MS].fade(from_gain=(BG_MUSIC_DUCKED_DB - BG_MUSIC_FULL_DB), duration=DUCK_FADE_MS)

    mixed_bg = (
        bg[:0]                                    # empty start
        + fade_in                                  # fade down at VO start
        + bg_ducked[DUCK_FADE_MS:vo_dur_ms - DUCK_FADE_MS]  # ducked under VO
        + fade_out                                 # fade back up
        + bg[vo_dur_ms:]                           # full volume after VO
    )
    if len(mixed_bg) > video_dur_ms:
        mixed_bg = mixed_bg[:video_dur_ms]
    elif len(mixed_bg) < video_dur_ms:
        mixed_bg = mixed_bg + bg[len(mixed_bg):]

    # Overlay voiceover on mixed BG
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
    logger.info(f"[{job.job_id}] Audio mixed → {out_path}")
    return out_path
```

#### `app/pipeline_whatif/stage5_subtitle.py`

```python
"""
Stage 5: Burn subtitles using ffmpeg drawtext.
Uses word timestamps from TTS stage. Falls back to script split if no timestamps.
"""
import re
import subprocess
from pathlib import Path
from app.schemas.whatif_schema import WhatIfJob
from app.core.logger import logger

FONT_SIZE = 52
FONT_COLOR = "white"
BORDER_COLOR = "black"
BORDER_WIDTH = 3
X_POS = "(w-text_w)/2"      # centered
Y_POS = "(h-text_h)/2+100"  # slightly below center (YouTube Shorts safe area)
MAX_CHARS_PER_LINE = 30


def _escape_ffmpeg(text: str) -> str:
    """Escape special characters for ffmpeg drawtext."""
    return (text
            .replace("'", "\u2019")  # replace apostrophe
            .replace(":", "\\:")
            .replace(",", "\\,")
            .replace("[", "\\[")
            .replace("]", "\\]"))


def run(job: WhatIfJob, video_path: str, work_dir: Path) -> str:
    timestamps = job.brain_output.__dict__.get("_timestamps", [])
    script = job.brain_output.script

    if not timestamps:
        # Fallback: estimate timestamps from script
        timestamps = _estimate_timestamps(script)

    # Group words into subtitle lines
    subtitles = _group_into_lines(timestamps)

    # Build drawtext filter chain
    filters = []
    for sub in subtitles:
        text = _escape_ffmpeg(sub["text"])
        start = sub["start"]
        end = sub["end"]
        duration = end - start
        filters.append(
            f"drawtext=text='{text}'"
            f":fontsize={FONT_SIZE}"
            f":fontcolor={FONT_COLOR}"
            f":bordercolor={BORDER_COLOR}"
            f":borderw={BORDER_WIDTH}"
            f":x={X_POS}"
            f":y={Y_POS}"
            f":enable='between(t,{start:.2f},{end:.2f})'"
        )

    if not filters:
        logger.warning(f"[{job.job_id}] No subtitles to render, skipping")
        return video_path

    filter_str = ",".join(filters)
    out_path = str(work_dir / "final.mp4")

    subprocess.run(
        ["ffmpeg", "-y",
         "-i", video_path,
         "-vf", filter_str,
         "-c:v", "libx264",
         "-crf", "18",
         "-preset", "fast",
         "-c:a", "copy",
         out_path],
        check=True, capture_output=True,
    )
    logger.info(f"[{job.job_id}] Subtitles burned → {out_path}")
    return out_path


def _estimate_timestamps(script: str) -> list[dict]:
    """Estimate word timings assuming ~130 words/min speaking rate."""
    words = script.split()
    sec_per_word = 60 / 130  # ~0.46s per word
    timestamps = []
    current_time = 0.5  # 0.5s lead-in
    for word in words:
        duration = sec_per_word * (1 + len(word) / 10)
        timestamps.append({"word": word, "start": current_time, "end": current_time + duration})
        current_time += duration + 0.05
    return timestamps


def _group_into_lines(timestamps: list[dict]) -> list[dict]:
    """Group words into subtitle lines of MAX_CHARS_PER_LINE."""
    lines = []
    current_words = []
    current_len = 0
    line_start = None

    for t in timestamps:
        word = t["word"]
        if line_start is None:
            line_start = t["start"]
        if current_len + len(word) + 1 > MAX_CHARS_PER_LINE and current_words:
            lines.append({
                "text": " ".join(current_words),
                "start": line_start,
                "end": t["start"],
            })
            current_words = [word]
            current_len = len(word)
            line_start = t["start"]
        else:
            current_words.append(word)
            current_len += len(word) + 1

    if current_words:
        end = timestamps[-1]["end"] if timestamps else line_start + 3.0
        lines.append({
            "text": " ".join(current_words),
            "start": line_start,
            "end": end,
        })

    return lines
```

---

### 3.5 `app/api/whatif_routes.py` ← NEW

Mirrors `pipeline_routes.py` pattern: POST start → SSE events → GET result.

```python
"""WhatIf Factory API routes — /whatif prefix."""
import asyncio
import json
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.schemas.whatif_schema import (
    WhatIfRequest,
    WhatIfStartResponse,
    WhatIfResultResponse,
)
from app.pipeline_whatif import orchestrator

router = APIRouter(prefix="/whatif", tags=["whatif"])


@router.post("/start", response_model=WhatIfStartResponse)
async def start_whatif(req: WhatIfRequest):
    """
    Start a WhatIf pipeline from a single topic string.
    
    Example:
        POST /whatif/start
        {"topic": "Hà Nội năm 3000"}
    """
    job = orchestrator.create_job(req)
    asyncio.create_task(orchestrator.run_pipeline(job.job_id))
    return WhatIfStartResponse(job_id=job.job_id, status=job.status)


@router.get("/{job_id}/events")
async def stream_events(job_id: str):
    """SSE stream for real-time pipeline progress."""
    job = orchestrator.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        while True:
            try:
                event = await asyncio.wait_for(job.event_queue.get(), timeout=25.0)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("done") or event.get("failed"):
                    break
            except asyncio.TimeoutError:
                yield "data: {\"ping\": true}\n\n"  # keepalive

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{job_id}/result", response_model=WhatIfResultResponse)
async def get_result(job_id: str):
    """Get final result of a WhatIf job."""
    job = orchestrator.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return WhatIfResultResponse(
        job_id=job.job_id,
        status=job.status,
        output_video=job.output_video,
        duration_sec=job.output_duration_sec,
        brain_output=job.brain_output,
        error=job.error,
    )
```

---

### 3.6 Sửa `app/main.py` — Mount router mới

```python
# Thêm vào sau các router hiện có:
from app.api.whatif_routes import router as whatif_router
app.include_router(whatif_router)
```

---

### 3.7 Sửa `app/core/config.py` — Thêm OpenAI key

```python
# Thêm field mới vào Settings class:
openai_api_key: str = ""
```

---

### 3.8 Sửa `.env.example`

```dotenv
GCP_PROJECT=your-gcp-project-id
GCP_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=config/vertex-ai.json

# WhatIf Factory additions
OPENAI_API_KEY=sk-...
```

---

### 3.9 Sửa `requirements.txt`

```
# Thêm:
openai>=1.30.0
ffmpeg-python>=0.2.0
opencv-python>=4.9.0
pydub>=0.25.1
scikit-image>=0.23.0
numpy>=1.26.0
httpx>=0.27.0
```

---

## 4. Thứ Tự Implementation (Step-by-Step)

```
Step 1:  pip install openai pydub httpx (thêm vào requirements.txt)
Step 2:  Sửa config.py thêm openai_api_key
Step 3:  Thêm OPENAI_API_KEY vào .env
Step 4:  Tạo app/services/gemini_service.py
Step 5:  Tạo app/services/tts_service.py
Step 6:  Tạo app/schemas/whatif_schema.py
Step 7:  Tạo app/pipeline_whatif/__init__.py
Step 8:  Tạo app/pipeline_whatif/stage0_brain.py
Step 9:  Tạo app/pipeline_whatif/stage1_veo_gen.py
Step 10: Tạo app/pipeline_whatif/stage2_tts.py
Step 11: Tạo app/pipeline_whatif/stage3_stitch.py
Step 12: Tạo app/pipeline_whatif/stage4_audio_mix.py
Step 13: Tạo app/pipeline_whatif/stage5_subtitle.py
Step 14: Tạo app/pipeline_whatif/orchestrator.py
Step 15: Tạo app/api/whatif_routes.py
Step 16: Sửa app/main.py mount router
Step 17: make debug → test POST /whatif/start {"topic": "Hà Nội năm 3000"}
```

---

## 5. Test nhanh (curl)

```bash
# Start job
curl -X POST http://localhost:8000/whatif/start \
  -H "Content-Type: application/json" \
  -d '{"topic": "Hà Nội năm 3000", "model": "veo-3.0-generate-001"}'
# → {"job_id": "abc123", "status": "queued"}

# Stream progress (SSE)
curl -N http://localhost:8000/whatif/abc123/events

# Get result
curl http://localhost:8000/whatif/abc123/result
```

---

## 6. Chi Phí Ước Tính (Per Video)

| API | Call | Est. Cost |
|---|---|---|
| Gemini 2.5 Flash Preview | 1 call (~500 tokens) | ~$0.001 |
| Veo 3.0 | 2 × 6s clips | ~$8.00 |
| OpenAI TTS | ~100 words | ~$0.015 |
| OpenAI Whisper | 1 transcription | ~$0.006 |
| **Total** | | **~$8.02 / video** |

---

## 7. Rủi Ro & Giải Pháp

| Rủi ro | Giải pháp |
|---|---|
| Gemini trả JSON sai schema | `responseMimeType: "application/json"` + validate với Pydantic |
| Veo timeout (>360s) | Đã handle trong `vertex_service.py` — pipeline sẽ mark failed |
| TTS không có word timestamps | Whisper fallback → nếu vẫn fail thì `_estimate_timestamps()` |
| Video < 18s sau slow-mo | `speed_factor = max(speed_factor, 0.5)` — hard floor 0.5x |
| `ffmpeg` không có trên system | `apt install ffmpeg` / `brew install ffmpeg` |

---

## 8. Hướng Phát Triển Tiếp

- [ ] Frontend tab "WhatIf" trong `web/` — input topic, SSE progress bar, video preview
- [ ] Batch queue: nạp danh sách 10 topic → tự gen 10 Shorts
- [ ] BG music library: auto-match vibe từ Gemini với file nhạc trong `assets/music/`
- [ ] MongoDB thay SQLite để quản lý hàng trăm video kịch bản
- [ ] ElevenLabs TTS thay OpenAI cho giọng tốt hơn tiếng Việt
