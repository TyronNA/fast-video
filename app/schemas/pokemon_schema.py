from pydantic import BaseModel


class PokemonRequest(BaseModel):
    pokemon_name: str
    language: str = "en"
    voice_model: str = "en-US-Neural2-J"
    model: str = "veo-3.1-fast-generate-preview"
