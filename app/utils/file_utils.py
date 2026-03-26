import uuid
from pathlib import Path

EXPORTS_DIR = Path(__file__).resolve().parents[2] / "exports"


def ensure_exports_dir() -> None:
    """Create the exports directory if it does not already exist."""
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


def build_output_path() -> Path:
    """Return a unique .mp4 file path inside the exports directory."""
    ensure_exports_dir()
    return EXPORTS_DIR / f"{uuid.uuid4()}.mp4"
