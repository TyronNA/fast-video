"""Stage 0: Call Gemini to generate historical era prompts for Timeline Civilizations."""
from app.schemas.whatif_schema import BrainOutput, VisualConfig
from app.services.gemini_service import generate_timeline_brain


async def run(location: str, voice_model: str = "en-US-Neural2-J", language: str = "en") -> BrainOutput:
    raw = await generate_timeline_brain(location, language)
    return BrainOutput(
        intro_phrase=raw["intro_phrase"],
        voice_model=voice_model,
        visuals=[VisualConfig(**v) for v in raw["visuals"]],
        vibe=raw.get("vibe", "Orchestral Epic"),
    )
