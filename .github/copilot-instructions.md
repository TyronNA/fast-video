# fast-video — Copilot Instructions

AI video generation service (FastAPI + Vertex AI Veo). Full reference: [CLAUDE.md](../CLAUDE.md) | Pipeline detail: [WHATIF_REFERENCE.md](../WHATIF_REFERENCE.md)

## Four Pipelines

| Pipeline | Entry | Input | Output |
|----------|-------|-------|--------|
| Single-clip | `POST /generate-one` | prompt + task type | one Veo clip |
| WhatIf Factory | `POST /whatif/start` | topic string | ~20s 9:16 video |
| Timeline Civilizations | `POST /timeline/start` | location name | ~20s historical video |
| Pokémon Cyberpunk | `POST /pokemon/start` | Pokémon name | evolution video via PokéAPI |

Pipelines share stages 1–4 from `pipeline_whatif/`. Only stage 0 (brain/prompt generation) differs per pipeline. Results streamed via SSE at `GET /{pipeline}/{job_id}/events`.

## Build & Run

```bash
make install   # create .venv + install requirements.txt (Python 3.11)
make run       # uvicorn on :8000
make debug     # uvicorn --reload on :8000
```

No test suite — validate with live API at `http://localhost:8000/docs`.

## Architecture Conventions

- **SSE jobs**: `asyncio.Queue` per job in `orchestrator.py`, broadcast via `broadcast_event()`. Job state lives in in-memory `_jobs` dict.
- **Stages 1 & 2 run concurrently**: `asyncio.gather(stage1, stage2)` in both WhatIf and Timeline orchestrators.
- **Schemas in `app/schemas/`**: always use Pydantic models; no raw dicts in routes.
- **Logging**: `from app.core.logger import get_logger` — never `print()`.
- **Cost tracking**: call `record_cost()` from `cost_service.py` for every generation; accessible at `GET /dashboard/stats`.

## Critical Gotchas

- **Do not touch `pipeline_whatif/stage3_stitch.py` and `stage4_audio_mix.py` casually** — ffmpeg pipeline is fragile; test with a real job before changing.
- **No background music/ducking** — intentionally removed; don't add it back.
- **No subtitle burning** — Stage 5 was intentionally removed.
- **Retry logic** in `stage1_veo_gen.py`: up to 3 attempts per clip, exponential backoff (2 s, 4 s). Don't bypass this.
- **Temp cleanup**: job dirs under `temp/whatif_jobs/` and `temp/timeline_jobs/` auto-delete 24 h after completion.
- **Never modify** `.venv/`, `exports/`, `temp/`, or `data/` — runtime output only.
- **Credentials**: all three services (Veo, Gemini, TTS) reuse the same service account key at `VERTEX_AI_CREDENTIALS_FILE`.

## Adding a New Pipeline

1. Create `app/pipeline_<name>/` with `__init__.py`, `orchestrator.py`, `stage0_brain.py`.
2. Add a schema in `app/schemas/<name>_schema.py`.
3. Add routes in `app/api/<name>_routes.py` (prefix `/<name>`).
4. Register the router in `app/main.py`.
5. Reuse `pipeline_whatif` stages 1–4 unchanged.
