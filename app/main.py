from fastapi import FastAPI

from app.api.routes import router
from app.core.logger import setup_logging

setup_logging()

app = FastAPI(
    title="fast-video",
    description="AI video generation service powered by Google Vertex AI (Veo).",
    version="0.1.0",
)

app.include_router(router)
