# fast-video

AI video generation service powered by **Google Vertex AI (Veo)**.

Three pipeline modes:
- **Single clip** — generate one Veo clip from a prompt (`/generate-one`)
- **WhatIf Factory** — full YouTube Shorts pipeline: topic → AI-scripted + AI-generated video with voiceover (`/whatif/*`)
- **Timeline Civilizations** — location name → historical eras video, 6 clips each showing a different era of that place (`/timeline/*`)

## Project structure

```
fast-video/
  app/
    main.py                      ← FastAPI app init + router registration
    api/
      routes.py                  ← POST /generate-one, history, models
      whatif_routes.py           ← POST /whatif/start, GET /whatif/{id}/events, GET /whatif/{id}/result
      timeline_routes.py         ← POST /timeline/start, GET /timeline/{id}/events, GET /timeline/{id}/result
      dashboard_routes.py        ← GET /dashboard/stats (cost tracking)
    core/
      config.py                  ← Env var validation (pydantic-settings)
      logger.py                  ← Logging setup
    pipeline_whatif/
      orchestrator.py            ← WhatIf job management & pipeline coordination
      stage0_brain.py            ← Gemini: topic → script + 6 Veo prompts
      stage1_veo_gen.py          ← Vertex AI Veo: generate 6 video clips (parallel)
      stage2_tts.py              ← Google Cloud TTS: per-clip voiceover (parallel with stage1)
      stage3_stitch.py           ← ffmpeg: concatenate clips
      stage4_audio_mix.py        ← ffmpeg + pydub: mux voiceover into video
    pipeline_timeline/
      orchestrator.py            ← Timeline job management (reuses stages 1-4 from WhatIf)
      stage0_brain.py            ← Gemini: location → 6 historical era prompts
    services/
      vertex_service.py          ← Vertex AI Veo integration
      gemini_service.py          ← Gemini: generate_brain() + generate_timeline_brain()
      tts_service.py             ← Google Cloud TTS integration
      history_service.py         ← SQLite generation history
      cost_service.py            ← SQLite cost tracking (record_cost, get_stats)
    schemas/
      video_schema.py            ← Single-clip request/response models
      whatif_schema.py           ← WhatIfJob, BrainOutput, WhatIfStatus
      timeline_schema.py         ← TimelineRequest (location, language, voice_model, model)
    utils/
      file_utils.py              ← UUID filename + exports directory management
  exports/                       ← Final videos saved here
  temp/whatif_jobs/              ← WhatIf per-job working directories (auto-created)
  temp/timeline_jobs/            ← Timeline per-job working directories (auto-created)
  data/history.db                ← SQLite DB (generation history + cost log)
  web/                           ← Frontend UI
    js/dashboard.js              ← Overview tab: cost stats
    js/timeline.js               ← Timeline tab: job submission + SSE progress
  requirements.txt
  Makefile
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `VERTEX_AI_CREDENTIALS_FILE` | **Yes** | Path to GCP service account JSON key |
| `GCP_PROJECT` | **Yes** | Your GCP project ID |
| `GCP_LOCATION` | No | Vertex AI region for Veo (default: `us-central1`) |
| `GEMINI_LOCATION` | No | Gemini endpoint location (default: `global`) |
| `GEMINI_MODEL` | No | Gemini model ID (default: `gemini-2.5-flash-preview-04-17`) |

```bash
export VERTEX_AI_CREDENTIALS_FILE="/path/to/service-account.json"
export GCP_PROJECT="your-project-id"
export GCP_LOCATION="us-central1"   # optional
```

> All three services (Veo, Gemini, Google Cloud TTS) reuse the same service account credentials — no extra keys needed.

## Quick start

### macOS / Linux

```bash
make install   # create venv + install dependencies
make run       # start server on port 8000
make debug     # start server with --reload (hot-reload)
```

### Windows

Two batch files are provided — no manual setup needed.

**Step 1 — Install (run once):**
```
install_windows.bat
```
This will:
- Check for Python 3.11+ and install it via `winget` if missing
- Check for `ffmpeg` and install it via `winget` if missing
- Create a `.venv` virtual environment
- Install all Python dependencies from `requirements.txt`
- Create `exports/` and `temp/whatif_jobs/` directories

**Step 2 — Set environment variables** (required before running):
```bat
set VERTEX_AI_CREDENTIALS_FILE=C:\path\to\service-account.json
set GCP_PROJECT=your-gcp-project-id
```
Or set them permanently via **System Properties → Advanced → Environment Variables**.

**Step 3 — Run:**
```
run_windows.bat
```

The server starts on `http://localhost:8000`.

---

## API — Single Clip

### `POST /generate-one`

Generate a single Veo video clip from a text prompt.

```bash
curl -X POST http://localhost:8000/generate-one \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "A lone surfer riding a massive wave at sunset",
    "duration": 5
  }'
```

With optional image reference:

```bash
curl -X POST http://localhost:8000/generate-one \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Slow dramatic lighting over the scene",
    "image_reference_uri": "gs://your-bucket/reference.jpg",
    "duration": 8
  }'
```

**Response:**
```json
{
  "status": "success",
  "file_path": "/abs/path/to/exports/<uuid>.mp4",
  "message": "Video generated successfully"
}
```

**Error codes:**

| HTTP | Cause |
|---|---|
| `400` | Invalid input or safety filter rejection |
| `500` | Missing env var or internal error |
| `502` | Unexpected Vertex AI API error |
| `504` | Request timed out |

---

## API — WhatIf Factory

Full end-to-end pipeline: one topic string → complete YouTube Shorts video (~20s, 9:16, with voiceover).

### Pipeline overview

```
POST /whatif/start {"topic": "Hà Nội năm 3000"}
          ↓
    create job (returns job_id immediately)
          ↓ (background)
  [Stage 0]  Gemini generates: intro phrase + 6 Veo prompts + landmark names
          ↓
  [Stage 1] ──parallel── [Stage 2]
  Veo generates           Google TTS generates
  6 video clips           per-clip voiceover audio
          ↓
  [Stage 3]  ffmpeg concatenates 6 clips → stitched.mp4
          ↓
  [Stage 4]  pydub + ffmpeg mux voiceover → with_audio.mp4
          ↓
    exports/whatif_{job_id}.mp4
```

**Stages 1 and 2 run in parallel** — Veo clip generation and TTS voiceover synthesis happen simultaneously, reducing total pipeline time.

---

### `POST /whatif/start`

Start a new WhatIf job. Returns immediately with a `job_id`.

**Request:**
```json
{
  "topic": "Hà Nội năm 3000",
  "model": "veo-3.1-fast-generate-preview",
  "voice_model": "en-US-Neural2-J",
  "language": "en"
}
```

| Field | Default | Description |
|---|---|---|
| `topic` | required | Topic or question for the video |
| `model` | `veo-3.1-fast-generate-preview` | Veo model variant |
| `voice_model` | `en-US-Neural2-J` | Google Cloud TTS voice |
| `language` | `en` | Language for Gemini script generation (`en` or `vi`) |

**Response `202`:**
```json
{
  "job_id": "a3f9bc12e4d0",
  "status": "queued"
}
```

---

### `GET /whatif/{job_id}/events`

Stream real-time pipeline progress as Server-Sent Events.

```bash
curl -N http://localhost:8000/whatif/a3f9bc12e4d0/events
```

**Event format:**
```
data: {"message": "Generating video clips...", "stage": "veo_gen", "percent": 30}
data: {"message": "TTS complete", "stage": "tts", "percent": 55}
...
data: {"done": true}
```

| Field | Description |
|---|---|
| `message` | Human-readable status |
| `stage` | Internal stage name |
| `percent` | Overall progress 0–100 |
| `done: true` | Pipeline completed successfully |
| `failed: true` | Pipeline failed; includes `error` field |

Keepalive pings are sent every 25 seconds if there is no activity.

---

### `GET /whatif/{job_id}/result`

Get the current state of a job. Can be polled at any time; non-blocking.

**Response:**
```json
{
  "job_id": "a3f9bc12e4d0",
  "status": "completed",
  "output_video": "/exports/whatif_a3f9bc12e4d0.mp4",
  "duration_sec": 22.4,
  "brain_output": {
    "intro_phrase": "What if Hanoi became a megacity?",
    "voice_model": "en-US-Neural2-J",
    "visuals": [...],
    "vibe": "Cyberpunk Phonk"
  },
  "error": null
}
```

| Status | Meaning |
|---|---|
| `queued` | Job created, not yet started |
| `running` | Pipeline in progress |
| `completed` | Video ready at `output_video` |
| `failed` | Error in `error` field |

---

### Supported voices

| Voice name | Language | Style |
|---|---|---|
| `en-US-Neural2-J` | English (US) | Male, deep |
| `en-US-Neural2-D` | English (US) | Male, neutral |
| `en-US-Neural2-A` | English (US) | Female |
| `vi-VN-Neural2-A` | Vietnamese | Female |
| `vi-VN-Neural2-D` | Vietnamese | Male |

Legacy aliases (`onyx`, `alloy`, `echo`, `fable`, `nova`, `shimmer`) are also accepted and map to English Neural2 voices.

---

### Example: full flow

```bash
# 1. Start job
JOB=$(curl -s -X POST http://localhost:8000/whatif/start \
  -H "Content-Type: application/json" \
  -d '{"topic": "Tokyo in the year 3000", "language": "en"}' \
  | jq -r .job_id)

# 2. Stream progress
curl -N http://localhost:8000/whatif/$JOB/events

# 3. Get result
curl http://localhost:8000/whatif/$JOB/result | jq .output_video
```

---

---

## API — Timeline Civilizations

Location name → historical eras video (~20s, 9:16, with voiceover).

Same pipeline as WhatIf — only the Gemini brain prompt differs. Each of the 6 clips depicts a different historical era of the location.

### `POST /timeline/start`

```bash
curl -X POST http://localhost:8000/timeline/start \
  -H "Content-Type: application/json" \
  -d '{"location": "Rome", "language": "en"}'
```

| Field | Default | Description |
|---|---|---|
| `location` | required | Place name (e.g. `"Rome"`, `"Hà Nội"`) |
| `model` | `veo-3.1-fast-generate-preview` | Veo model variant |
| `voice_model` | `en-US-Neural2-J` | Google Cloud TTS voice |
| `language` | `en` | Language for Gemini script |

**Response `202`:** `{"job_id": "...", "status": "queued"}`

### `GET /timeline/{job_id}/events` / `GET /timeline/{job_id}/result`

Identical format to WhatIf endpoints. Output saved as `exports/timeline_{job_id}.mp4`.

---

## API — Dashboard

### `GET /dashboard/stats`

Returns cost stats aggregated by day, model, and job type.

```bash
curl "http://localhost:8000/dashboard/stats?days=30"
```

| Param | Default | Description |
|---|---|---|
| `days` | `30` | Lookback window (1–365) |

---

## Output

| Type | Location |
|---|---|
| Single clip | `./exports/<uuid>.mp4` |
| WhatIf video | `./exports/whatif_<job_id>.mp4` |
| Timeline video | `./exports/timeline_<job_id>.mp4` |
| WhatIf temp files | `./temp/whatif_jobs/wi_YYYYMMDD_<job_id>/` |
| Timeline temp files | `./temp/timeline_jobs/tl_YYYYMMDD_<job_id>/` |
