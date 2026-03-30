# fast-video — CLAUDE.md

## Project Overview

AI video generation service powered by **Vertex AI Veo**. Three pipeline modes:

- **Single-clip** (`/generate-one`) — generate one Veo clip from a prompt. Supports 7 task types (text_to_video, image_to_video, reference_subject, reference_style, video_extension, inpaint_insert, inpaint_remove), any aspect ratio (16:9, 9:16, 1:1), 4–8s duration.
- **WhatIf Factory** (`/whatif/*`) — full automated pipeline: one topic string → ~20s video (9:16) with AI script, 6 Veo clips, TTS voiceover, ffmpeg stitch.
- **Timeline Civilizations** (`/timeline/*`) — same pipeline as WhatIf but stage 0 uses a different Gemini prompt: one location name → historical eras video (6 clips, each a different era of the location).

## Tech Stack

- **FastAPI** — REST API + SSE streaming
- **Vertex AI Veo 3.1 / 3.1 Fast** — text-to-video clip generation
- **Vertex AI Gemini 3 Flash Preview** — script/brain generation from topic
- **Google Cloud TTS** — per-clip voiceover
- **FFmpeg + pydub** — video stitching, audio mixing
- **SQLite** — generation history + cost tracking
- **Python 3.11, async/await** — parallel stages via `asyncio.gather()`

## File Structure

```
app/
├── main.py                    # FastAPI init, router registration, static mounts
├── api/
│   ├── routes.py              # Single-clip, history, model endpoints
│   ├── whatif_routes.py       # WhatIf: /whatif/start, /events, /result
│   ├── timeline_routes.py     # Timeline: /timeline/start, /events, /result
│   └── dashboard_routes.py    # Dashboard: /dashboard/stats
├── core/
│   ├── config.py              # Pydantic Settings (GCP_PROJECT, GCP_LOCATION, etc.)
│   ├── logger.py              # Logging setup
│   └── exceptions.py          # VertexTimeoutError, VertexSafetyError, etc.
├── pipeline_whatif/
│   ├── orchestrator.py        # Job management, SSE broadcasting, cleanup
│   ├── stage0_brain.py        # Gemini → BrainOutput (intro, visuals[], vibe)
│   ├── stage1_veo_gen.py      # Veo → clip_00.mp4 … clip_05.mp4 (parallel)
│   ├── stage2_tts.py          # TTS → clip_audio_00.mp3 … (parallel w/ stage1)
│   ├── stage3_stitch.py       # ffmpeg concat → stitched.mp4
│   └── stage4_audio_mix.py    # pydub + ffmpeg → with_audio.mp4 (final)
├── pipeline_timeline/
│   ├── orchestrator.py        # Timeline job management (reuses stages 1-4 from WhatIf)
│   └── stage0_brain.py        # Gemini → historical eras BrainOutput for a location
├── services/
│   ├── vertex_service.py      # Veo: generate_video, list_models, cost estimation
│   ├── gemini_service.py      # Gemini: generate_brain() + generate_timeline_brain()
│   ├── tts_service.py         # Google Cloud TTS, multi-voice support
│   ├── history_service.py     # SQLite CRUD for generation history
│   └── cost_service.py        # SQLite cost tracking (record_cost, get_stats)
├── schemas/
│   ├── video_schema.py        # VideoGenerationRequest/Response, GenerationTask enum
│   ├── whatif_schema.py       # WhatIfJob, BrainOutput, WhatIfStatus enum
│   └── timeline_schema.py     # TimelineRequest (location, language, voice_model, model)
└── utils/
    └── file_utils.py          # UUID filenames, exports dir management

exports/                       # Final output videos (served via /exports/*)
temp/whatif_jobs/              # WhatIf per-job working dirs (auto-deleted after 24h)
temp/timeline_jobs/            # Timeline per-job working dirs (auto-deleted after 24h)
data/history.db                # SQLite DB (generation history + cost log)
web/                           # Frontend UI (index.html, js/, css/, assets/)
  web/js/dashboard.js          # Overview tab: cost stats charts
  web/js/timeline.js           # Timeline tab: job submission + SSE progress
main.py                        # Uvicorn entrypoint
requirements.txt
.env                           # GCP credentials (not committed)
```

## WhatIf Pipeline Stages

| Stage | File | Task |
|-------|------|------|
| 0 — Brain | `pipeline_whatif/stage0_brain.py` | Gemini: topic → intro phrase, 6 visual prompts, vibe, landmark names |
| 1 — Veo Gen | `pipeline_whatif/stage1_veo_gen.py` | Generate 6 video clips (4–8s each), retry up to 3x |
| 2 — TTS | `pipeline_whatif/stage2_tts.py` | Per-clip voiceover audio (runs parallel with Stage 1) |
| 3 — Stitch | `pipeline_whatif/stage3_stitch.py` | ffmpeg concat clips → stitched.mp4 |
| 4 — Audio Mix | `pipeline_whatif/stage4_audio_mix.py` | Overlay TTS at timestamps → final video |

Stages 1 & 2 run concurrently via `asyncio.gather()`. Stages 3 & 4 are sequential.

## Timeline Civilizations Pipeline

Same 4-stage structure as WhatIf. Only stage 0 differs.

| Stage | File | Task |
|-------|------|------|
| 0 — Brain | `pipeline_timeline/stage0_brain.py` | Gemini: location → 6 historical era prompts, vibe |
| 1–4 | shared with WhatIf | identical stages reused from `pipeline_whatif/` |

- Input: `location` (e.g. `"Rome"`) instead of a topic
- Output: `exports/timeline_{job_id}.mp4`
- Work dir: `temp/timeline_jobs/tl_YYYYMMDD_{job_id}/`
- Cost recorded under `job_type="timeline"` in `cost_log` table

## API Endpoints

```
POST /generate-one              # Single-clip generation
GET  /models                    # List Veo models + metadata
GET  /models/check              # Live probe each model endpoint
GET  /estimate                  # Cost estimation
GET  /tasks                     # List task types: text_to_video, image_to_video, reference_subject, reference_style, video_extension, inpaint_insert, inpaint_remove
POST /history                   # Save generation metadata
GET  /history                   # List previous generations

POST /whatif/start              # Start WhatIf pipeline → {job_id} (HTTP 202)
GET  /whatif/{job_id}/events    # SSE real-time progress stream
GET  /whatif/{job_id}/result    # Fetch final result

POST /timeline/start            # Start Timeline pipeline → {job_id} (HTTP 202)
GET  /timeline/{job_id}/events  # SSE real-time progress stream
GET  /timeline/{job_id}/result  # Fetch final result

GET  /dashboard/stats           # Cost stats (by day/model/job_type), accepts ?days=30
```

## Configuration (.env)

```
GCP_PROJECT=<required>
GCP_LOCATION=us-central1
VERTEX_AI_CREDENTIALS_FILE=app/config/vertex-ai.json
GEMINI_MODEL=gemini-2.5-flash-preview-04-17
```

## Key Patterns & Conventions

- **SSE events** in `orchestrator.py`: `asyncio.Queue` per job, events broadcast via `broadcast_event()`
- **Job state** stored in `WhatIfJob` dataclass (in-memory dict `_jobs`)
- **Retry logic**: up to 3 attempts per clip, exponential backoff (2s, 4s)
- **Temp cleanup**: job dirs auto-deleted 24h after completion
- **Schemas**: always use Pydantic models in `app/schemas/`; avoid raw dicts in routes
- **Logging**: use `app.core.logger` — do not use `print()`

## What to AVOID

- Do NOT explore `.venv/` — it's the virtual environment, never modify it
- Do NOT read `exports/`, `temp/`, or `data/` — runtime output, not source code
- Do NOT add `print()` — use the existing logger
- Do NOT add background music/ducking logic — was intentionally removed
- Do NOT mock external APIs in tests — integration tests should hit real endpoints
- Do NOT add subtitle burning — Stage 5 was intentionally removed

## Running Locally

```bash
python -m uvicorn main:app --reload --port 8000
```

Frontend: `http://localhost:8000`
API docs: `http://localhost:8000/docs`
