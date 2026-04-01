"""Pokémon Cyberpunk Evolution API — /pokemon prefix."""
import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.pipeline_pokemon import orchestrator
from app.schemas.pokemon_schema import PokemonRequest
from app.schemas.whatif_schema import WhatIfResultResponse, WhatIfStartResponse

router = APIRouter(prefix="/pokemon", tags=["pokemon"])


@router.post("/start", response_model=WhatIfStartResponse, status_code=202)
async def start_pokemon(req: PokemonRequest):
    """
    Start a Pokémon Cyberpunk Evolution pipeline from a Pokémon name.

        POST /pokemon/start
        {"pokemon_name": "Pikachu"}

    Returns a job_id to track progress via GET /pokemon/{job_id}/events.
    """
    job = orchestrator.create_job(req)
    asyncio.create_task(orchestrator.run_pipeline(job.job_id))
    return WhatIfStartResponse(job_id=job.job_id, status=job.status)


@router.get("/{job_id}/events")
async def stream_events(job_id: str):
    """Real-time SSE stream of Pokémon evolution pipeline progress events."""
    job = orchestrator.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator():
        if job.terminal_event:
            yield f"data: {json.dumps(job.terminal_event, ensure_ascii=False)}\n\n"
            return

        q: asyncio.Queue = asyncio.Queue()
        job.subscribers.append(q)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event.get("done") or event.get("failed"):
                        break
                except asyncio.TimeoutError:
                    yield 'data: {"ping":true}\n\n'
        finally:
            try:
                job.subscribers.remove(q)
            except ValueError:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{job_id}/result", response_model=WhatIfResultResponse)
async def get_result(job_id: str):
    """Fetch the final result of a Pokémon Evolution job."""
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
