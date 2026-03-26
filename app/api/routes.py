from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.logger import get_logger
from app.schemas.video_schema import VideoGenerationRequest, VideoGenerationResponse
from app.services import vertex_service

router = APIRouter()
logger = get_logger(__name__)


@router.post(
    "/generate-one",
    response_model=VideoGenerationResponse,
    summary="Generate a single video from a text prompt",
)
def generate_one(request: VideoGenerationRequest) -> JSONResponse:
    logger.info(
        "POST /generate-one  prompt=%.80s  duration=%ds",
        request.prompt,
        request.duration,
    )

    output_path = vertex_service.generate_video(
        prompt=request.prompt,
        duration=request.duration,
        image_reference_uri=request.image_reference_uri,
    )

    return JSONResponse(
        content={
            "status": "success",
            "file_path": str(output_path),
            "message": "Video generated successfully",
        }
    )
