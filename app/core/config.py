import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    gcp_project: str
    gcp_location: str = "us-central1"
    # GOOGLE_APPLICATION_CREDENTIALS is read directly by the Google SDK from the
    # environment, but we expose it here so it can be validated at startup.
    google_application_credentials: str

    model_config = {"env_file": ".env", "case_sensitive": False}


settings = Settings()
