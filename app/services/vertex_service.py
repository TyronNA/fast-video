from pathlib import Path

import vertexai
from fastapi import HTTPException
from google.api_core.exceptions import DeadlineExceeded, GoogleAPICallError, InvalidArgument
from vertexai.preview.vision_models import VideoGenerationModel

from app.core.config import settings
from app.core.logger import get_logger
from app.utils.file_utils import build_output_path

logger = get_logger(__name__)

_MODEL_ID = "veo-2.0-generate-001"


def _init_vertex() -> None:
    """Initialise the Vertex AI SDK with project + location from settings."""
    vertexai.init(project=settings.gcp_project, location=settings.gcp_location)


def _build_generate_kwargs(
    prompt: str,
    duration: int,
    image_reference_uri: str | None,
) -> dict:
    kwargs: dict = {"prompt": prompt, "duration_seconds": duration}
    if image_reference_uri:
        kwargs["reference_image"] = image_reference_uri
    return kwargs


def generate_video(
    prompt: str,
    duration: int,
    image_reference_uri: str | None = None,
) -> Path:
    """
    Call Vertex AI Veo, wait for the result, save the first video, and return
    its local Path.

    Raises:
        HTTPException 400 — safety filter rejection or invalid argument.
        HTTPException 504 — API deadline exceeded.
        HTTPException 502 — any other Vertex AI API error.
    """
    logger.info(
        "Initialising Vertex AI  project=%s  location=%s",
        settings.gcp_project,
        settings.gcp_location,
    )
    _init_vertex()

    model = VideoGenerationModel.from_pretrained(_MODEL_ID)
    kwargs = _build_generate_kwargs(prompt, duration, image_reference_uri)

    logger.info("Sending generation request  duration=%ds  prompt=%.80s", duration, prompt)

    try:
        response = model.generate_video(**kwargs)
    except DeadlineExceeded as exc:
        logger.error("Vertex AI request timed out: %s", exc)
        raise HTTPException(
            status_code=504,
            detail="Video generation timed out. Try a shorter duration or retry later.",
        ) from exc
    except InvalidArgument as exc:
        logger.error("Vertex AI rejected the request (invalid argument / safety filter): %s", exc)
        raise HTTPException(
            status_code=400,
            detail=f"Request rejected by Vertex AI: {exc.message}",
        ) from exc
    except GoogleAPICallError as exc:
        logger.error("Vertex AI API error: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Vertex AI error: {exc.message}",
        ) from exc

    videos = response.videos
    if not videos:
        raise HTTPException(
            status_code=502,
            detail="Vertex AI returned no videos. The prompt may have been blocked by the safety filter.",
        )

    output_path = build_output_path()
    logger.info("Saving video to %s", output_path)
    videos[0].save(str(output_path))

    return output_path
