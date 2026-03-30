from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.api.whatif_routes import router as whatif_router
from app.api.timeline_routes import router as timeline_router
from app.api.dashboard_routes import router as dashboard_router
from app.core.logger import setup_logging
from app.pipeline_whatif.orchestrator import cleanup_old_work_dirs
from app.pipeline_timeline.orchestrator import cleanup_old_work_dirs as cleanup_timeline_dirs
from app.services.history_service import init_db as init_history_db
from app.services.cost_service import init_cost_db
from app.services.vertex_service import init_vertex

setup_logging()

_WEB_DIR = Path(__file__).resolve().parents[1] / "web"
_EXPORTS_DIR = Path(__file__).resolve().parents[1] / "exports"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_history_db()
    init_cost_db()
    init_vertex()
    cleanup_old_work_dirs()
    cleanup_timeline_dirs()
    yield


app = FastAPI(
    title="fast-video",
    description="AI video generation service powered by Google Vertex AI (Veo).",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)
app.include_router(whatif_router)
app.include_router(timeline_router)
app.include_router(dashboard_router)

# Static assets — must be mounted AFTER API routes to avoid shadowing them.
_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
_ASSETS_VIDEO_DIR = _WEB_DIR / "assets" / "video"
_ASSETS_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/exports", StaticFiles(directory=str(_EXPORTS_DIR)), name="exports")
app.mount("/assets/video", StaticFiles(directory=str(_EXPORTS_DIR)), name="assets_video")
app.mount("/js",  StaticFiles(directory=str(_WEB_DIR / "js")),  name="js")
app.mount("/css", StaticFiles(directory=str(_WEB_DIR / "css")), name="css")
app.mount("/assets", StaticFiles(directory=str(_WEB_DIR / "assets")), name="assets")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(str(_WEB_DIR / "index.html"))
