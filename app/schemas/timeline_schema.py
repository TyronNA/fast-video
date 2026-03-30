from pydantic import BaseModel


class TimelineRequest(BaseModel):
    location: str
    language: str = "en"
    voice_model: str = "en-US-Neural2-J"
    model: str = "veo-3.1-fast-generate-preview"
