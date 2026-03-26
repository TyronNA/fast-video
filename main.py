# Entry-point shim — the real application lives in app/main.py
# Run with:  uvicorn app.main:app --reload
from app.main import app  # noqa: F401

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
GCP_PROJECT = os.environ.get("GCP_PROJECT")
GCP_LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
EXPORTS_DIR = Path("./exports")
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="fast-video", version="0.1.0")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class VideoRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    image_reference_uri: str | None = Field(default=None)
    duration: int = Field(..., ge=1, le=60, description="Duration in seconds")


class VideoResponse(BaseModel):
    status: str
    file_path: str
    message: str


# ---------------------------------------------------------------------------
# Vertex AI helper
# ---------------------------------------------------------------------------
def generate_video_from_vertex(
    prompt: str,
    duration: int,
    image_reference_uri: str | None = None,
) -> Path:
    """
    Call the Vertex AI Veo model, wait for the result, and save the first
    generated video to the exports directory.

    Returns the local Path of the saved file.
    Raises HTTPException on API / safety / timeout errors.
    """
    if not GCP_PROJECT:
        raise HTTPException(
            status_code=500,
            detail="GCP_PROJECT environment variable is not set.",
        )

    logger.info(
        "Initialising Vertex AI  project=%s  location=%s", GCP_PROJECT, GCP_LOCATION
    )
    vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION)

    model = VideoGenerationModel.from_pretrained("veo-2.0-generate-001")

    generate_kwargs: dict = {
        "prompt": prompt,
        "duration_seconds": duration,
    }

    if image_reference_uri:
        logger.info("Using image reference: %s", image_reference_uri)
        generate_kwargs["reference_image"] = image_reference_uri

    logger.info("Sending video generation request  duration=%ds", duration)

    try:
        response = model.generate_video(**generate_kwargs)
    except DeadlineExceeded as exc:
        logger.error("Vertex AI request timed out: %s", exc)
        raise HTTPException(
            status_code=504,
            detail="Video generation timed out. Try a shorter duration or retry later.",
        ) from exc
    except GoogleAPICallError as exc:
        logger.error("Vertex AI API error: %s", exc)
        # Safety filter violations surface as INVALID_ARGUMENT / 400
        status_code = 400 if exc.grpc_status_code and exc.grpc_status_code.value[0] == 3 else 502
        raise HTTPException(
            status_code=status_code,
            detail=f"Vertex AI error: {exc.message}",
        ) from exc

    videos = response.videos
    if not videos:
        raise HTTPException(
            status_code=502,
            detail="Vertex AI returned no videos. The prompt may have been blocked by the safety filter.",
        )

    video = videos[0]
    output_filename = EXPORTS_DIR / f"{uuid.uuid4()}.mp4"

    logger.info("Saving video to %s", output_filename)
    video.save(str(output_filename))

    return output_filename


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.post("/generate-one", response_model=VideoResponse)
def generate_one(request: VideoRequest) -> JSONResponse:
    """Generate a single video from a text prompt and optional image reference."""
    logger.info(
        "Received /generate-one  prompt=%.80s  duration=%ds",
        request.prompt,
        request.duration,
    )

    output_path = generate_video_from_vertex(
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
