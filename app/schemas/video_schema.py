from pydantic import BaseModel, Field


class VideoGenerationRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000, description="Text prompt for video generation")
    image_reference_uri: str | None = Field(
        default=None,
        description="Optional GCS URI of a reference image (e.g. gs://bucket/image.jpg)",
    )
    duration: int = Field(..., ge=1, le=60, description="Video duration in seconds")


class VideoGenerationResponse(BaseModel):
    status: str
    file_path: str
    message: str
