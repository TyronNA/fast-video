"""Stage 0: Fetch Pokémon evolution chain then call Gemini to generate Cyberpunk Evolution prompts."""
import httpx

from app.core.logger import get_logger
from app.schemas.whatif_schema import BrainOutput, VisualConfig
from app.services.gemini_service import generate_pokemon_brain

logger = get_logger(__name__)


async def _fetch_evolution_chain(pokemon_name: str) -> list[str]:
    """Return ordered list of species names in the evolution chain via PokéAPI.

    Falls back to [pokemon_name] if the API is unreachable or the Pokémon is unknown.
    """
    name = pokemon_name.lower().replace(" ", "-")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"https://pokeapi.co/api/v2/pokemon-species/{name}")
            if resp.status_code != 200:
                logger.warning("PokéAPI species lookup failed for %r (HTTP %s)", name, resp.status_code)
                return [pokemon_name]
            species = resp.json()
            chain_url = species["evolution_chain"]["url"]

            resp2 = await client.get(chain_url)
            if resp2.status_code != 200:
                logger.warning("PokéAPI evolution chain fetch failed (HTTP %s)", resp2.status_code)
                return [pokemon_name]
            chain_data = resp2.json()

        # Walk depth-first, take first branch at each fork
        names: list[str] = []
        node = chain_data["chain"]
        while node:
            names.append(node["species"]["name"].capitalize())
            node = node["evolves_to"][0] if node["evolves_to"] else None

        logger.info("PokéAPI evolution chain for %r: %s", pokemon_name, names)
        return names if names else [pokemon_name]
    except Exception as exc:  # noqa: BLE001
        logger.warning("PokéAPI lookup error for %r: %s", pokemon_name, exc)
        return [pokemon_name]


async def run(
    pokemon_name: str,
    voice_model: str = "en-US-Neural2-J",
    language: str = "en",
) -> BrainOutput:
    evolution_chain = await _fetch_evolution_chain(pokemon_name)
    raw = await generate_pokemon_brain(pokemon_name, evolution_chain, language)
    return BrainOutput(
        intro_phrase=raw["intro_phrase"],
        voice_model=voice_model,
        visuals=[VisualConfig(**v) for v in raw["visuals"]],
        vibe=raw.get("vibe", "Cyberpunk Phonk"),
    )
