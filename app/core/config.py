from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    gcp_project: str
    gcp_location: str = "us-central1"
    vertex_ai_credentials_file: str = "app/config/vertex-ai.json"
    gemini_location: str = "global"
    # Override to switch Gemini model (e.g. gemini-2.5-flash-preview when available)
    gemini_model: str = "gemini-3-flash-preview"

    model_config = {"env_file": ".env", "case_sensitive": False}


settings = Settings()

