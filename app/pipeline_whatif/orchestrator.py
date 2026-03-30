"""
WhatIf pipeline orchestrator.
Manages in-memory job state and runs the multi-stage pipeline as a background task.
"""
import asyncio
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

from app.schemas.whatif_schema import WhatIfJob, WhatIfRequest, WhatIfStatus
from app.core.logger import get_logger

logger = get_logger(__name__)

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
        topic_type=req.topic_type,
    )
    _JOBS[job_id] = job
    logger.info("Created WhatIf job %s for topic=%r", job_id, req.topic)
    return job


def get_job(job_id: str) -> WhatIfJob | None:
    return _JOBS.get(job_id)


def _work_dir(job_id: str) -> Path:
    matches = list(_WORK_BASE.glob(f"wi_*_{job_id}"))
    if matches:
        return matches[0]
    d = _WORK_BASE / f"wi_{datetime.now().strftime('%Y%m%d')}_{job_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _broadcast(job: WhatIfJob, event: dict) -> None:
    for q in job.subscribers:
        await q.put(event)


async def _push(job: WhatIfJob, message: str, stage: str, percent: int) -> None:
    job.current_stage = stage
    job.stage_percent = percent
    event = {"message": message, "stage": stage, "percent": percent}
    job.logs.append(event)
    await _broadcast(job, event)
    logger.info("[%s] [%s] %s", job.job_id, stage, message)


def cleanup_old_work_dirs(max_age_hours: int = 24) -> None:
    """Remove work directories older than max_age_hours on server startup."""
    if not _WORK_BASE.exists():
        return
    cutoff = datetime.now().timestamp() - max_age_hours * 3600
    for d in _WORK_BASE.iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            logger.info("Cleaned up stale work dir: %s", d)


async def run_pipeline(job_id: str) -> None:
    from app.pipeline_whatif import (
        stage0_brain,
        stage1_veo_gen,
        stage2_tts,
        stage3_stitch,
        stage4_audio_mix,
    )

    job = _JOBS[job_id]
    work_dir = _work_dir(job_id)
    job.status = WhatIfStatus.running

    try:
        # ── Stage 0: Brain ──────────────────────────────────────────────────
        await _push(job, f"🧠 Generating script & prompts for '{job.topic}'...", "brain", 5)
        job.brain_output = await stage0_brain.run(job.topic, job.voice_model, language="en", topic_type=job.topic_type)
        await _push(job, f"✅ Script ready. Vibe: {job.brain_output.vibe}", "brain", 15)

        # ── Stage 1 + 2: Veo clips & TTS in parallel ────────────────────────
        await _push(job, "🎬 Generating video clips & voiceover in parallel...", "media_gen", 20)
        await asyncio.gather(
            stage1_veo_gen.run(job, work_dir),
            stage2_tts.run(job, work_dir),
        )
        await _push(job, f"✅ {len(job.clip_paths)} clip(s) + voiceover ready", "media_gen", 60)

        # ── Stage 3: Stitch ──────────────────────────────────────────────────
        await _push(job, "✂️ Stitching clips to 18-20s...", "stitch", 62)
        stitched = await asyncio.to_thread(stage3_stitch.run, job, work_dir)
        await _push(job, "✅ Stitch complete", "stitch", 72)

        # ── Stage 4: Audio mix ───────────────────────────────────────────────
        await _push(job, "🎵 Mixing voiceover + BG music...", "audio_mix", 74)
        final = await asyncio.to_thread(stage4_audio_mix.run, job, stitched, work_dir)
        await _push(job, "✅ Audio mix complete", "audio_mix", 90)

        # ── Move to exports/ ─────────────────────────────────────────────────
        export_dir = Path("exports")
        export_dir.mkdir(parents=True, exist_ok=True)
        export_path = export_dir / f"whatif_{job_id}.mp4"
        Path(final).rename(export_path)

        probe = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1",
             str(export_path)],
            capture_output=True, text=True,
        )
        duration = float(probe.stdout.strip()) if probe.stdout.strip() else 0.0

        job.output_video = f"/exports/{export_path.name}"
        job.output_duration_sec = duration
        job.status = WhatIfStatus.completed

        await _push(job, f"🎉 Done! {duration:.1f}s → {job.output_video}", "done", 100)
        terminal = {"done": True}
        job.terminal_event = terminal
        await _broadcast(job, terminal)

        shutil.rmtree(work_dir, ignore_errors=True)
        logger.info("[%s] Cleaned up work dir %s", job_id, work_dir)

    except Exception as exc:
        job.status = WhatIfStatus.failed
        job.error = str(exc)
        logger.exception("[%s] Pipeline failed: %s", job_id, exc)
        terminal = {"failed": True, "error": str(exc)}
        job.terminal_event = terminal
        await _broadcast(job, terminal)
