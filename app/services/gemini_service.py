import json
import re
from json import JSONDecodeError

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account as sa

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)

_VERTEX_URL_TPL = (
    "https://{host}/v1/projects/{project}"
    "/locations/{location}/publishers/google/models/{model}:generateContent"
)

_SUPPORTED_DURATIONS = (4, 6, 8)

_BRAIN_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "required": ["intro_phrase", "visuals", "vibe"],
    "properties": {
        "intro_phrase": {"type": "STRING"},
        "visuals": {
            "type": "ARRAY",
            "minItems": 5,
            "maxItems": 6,
            "items": {
                "type": "OBJECT",
                "required": ["prompt", "duration", "landmark_name"],
                "properties": {
                    "prompt": {"type": "STRING"},
                    "duration": {"type": "INTEGER", "enum": [4, 6, 8]},
                    "landmark_name": {"type": "STRING"},
                },
            },
        },
        "vibe": {"type": "STRING"},
    },
}


_TIMELINE_VEO_FORMULA = """\
Every Veo prompt MUST follow this formula:
  [UNIQUE CAMERA MOVE + LENS] + [ERA-SPECIFIC VISUAL SIGNATURE of the location] + [PERIOD-ACCURATE ARCHITECTURE & MATERIALS] + [DEPTH LAYER] + [ERA-FITTING ATMOSPHERE] + [QUALITY TAGS]

Camera moves + lens (each shot must use a DIFFERENT one):
  sweeping aerial drone shot, anamorphic wide lens | slow cinematic dolly forward, shallow depth of field |
  bird's-eye view slow pan, wide-angle 18mm | low-altitude flyover, telephoto compression |
  extreme low-angle looking up, wide-angle distortion | slow crane up reveal, anamorphic lens |
  tracking shot along skyline, 35mm cinematic

Era atmospheres (pick one that matches the historical period — no repeats):
  ancient: golden morning sun over stone temples, dusty terracotta haze |
  medieval: cold overcast gray light, crumbling arches, sparse settlements |
  renaissance/classical: warm afternoon golden hour, marble domes and plazas |
  industrial: sepia-tinted coal haze, brick towers, steam columns |
  modern: blue-hour ambient glow, glass towers, dense urban grid |
  future: neon city bloom, floating platforms, holographic sky

Depth layer (add ONE per prompt — creates foreground parallax):
  massive stone column in close foreground | ancient arch gate framing the shot |
  floating transport platform passing close | energy pylon tower in foreground |
  ruined wall fragment in near foreground | polished glass building edge in foreground

Quality tags (always append to every prompt):
  cinematic, hyperrealistic, ultra-detailed, 8K, anamorphic, no people, no text, no watermark

CRITICAL — Era Accuracy rule:
  Each prompt MUST describe what the location physically looked like AT THAT SPECIFIC ERA — its actual architecture, building materials, and urban density from that period.
  Example ancient: "the seven hills of Rome lined with terracotta rooftops of the early republic, the Forum Romanum an open plaza of limestone columns and wooden market stalls..."
  Example medieval: "the ruins of the Forum now partially buried under medieval village buildings, a small church rising from crumbled imperial marble..."
  Example future: "the Colosseum's ancient arches now embedded in a mega-tower of glass and plasma conduits, the Forum transformed into an elevated hyperloop transit hub..."
  This era accuracy makes each clip look completely different from every other clip.
"""

_TIMELINE_BRAIN_PROMPT = (
    'You are a Veo video director creating a "Timeline Civilizations" YouTube Shorts video.\n\n'
    "Location: __LOCATION__\n\n"
    "Task: Generate exactly 6 cinematic shots showing __LOCATION__ across different historical eras — "
    "from ancient past to far future. Each clip is a window into what this place looked (or will look) like at a specific point in time.\n\n"
    "__VEO_GUIDE__\n"
    "Shot structure:\n"
    "- Shot 1 (opening hook): Dramatic wide cinematic reveal of __LOCATION__ TODAY — an iconic overhead view that makes viewers instantly recognize the place. duration=6, landmark_name=\"\"\n"
    "- Shots 2-6: Choose 5 distinct time periods that span the history of __LOCATION__ (include: 1 deep ancient past, 1 medieval/classical, 1 early modern/renaissance, 1 recent past, 1 far future). "
    "For each era, show __LOCATION__ as it appeared/will appear at that exact time — accurate to the period's architecture, materials, density, and technology. "
    "Each clip must look and feel completely different from the others due to era accuracy. duration=4 each.\n\n"
    "Return ONLY a valid JSON object (no markdown, no explanation):\n"
    '{\n'
    '  "intro_phrase": "<punchy 7-9 word hook in __LANG__, e.g. \'Rome — 2500 năm trong 30 giây\'>",\n'
    '  "visuals": [\n'
    '    {"prompt": "<shot 1 — cinematic wide reveal of __LOCATION__ today>", "duration": 6, "landmark_name": ""},\n'
    '    {"prompt": "<shot 2 — __LOCATION__ in deep ancient past>", "duration": 4, "landmark_name": "<era label in __LANG__, e.g. \'500 TCN\' or \'500 BC\'>"},\n'
    '    {"prompt": "<shot 3 — __LOCATION__ in medieval/classical era>", "duration": 4, "landmark_name": "<era label>"},\n'
    '    {"prompt": "<shot 4 — __LOCATION__ in early modern era>", "duration": 4, "landmark_name": "<era label>"},\n'
    '    {"prompt": "<shot 5 — __LOCATION__ in recent past>", "duration": 4, "landmark_name": "<era label>"},\n'
    '    {"prompt": "<shot 6 — __LOCATION__ far in the future>", "duration": 4, "landmark_name": "<era label, e.g. \'Năm 2500\' or \'Year 2500\'>"}\n'
    '  ],\n'
    '  "vibe": "<music genre fitting this historical journey — e.g. Orchestral Epic, Cinematic Score, Taiko Drums>"\n'
    '}\n\n'
    "Hard rules:\n"
    "- Exactly 6 shots: shot 1 duration=6, shots 2-6 duration=4\n"
    "- Each shot MUST use a DIFFERENT camera move+lens AND a DIFFERENT era atmosphere — no repeats across all 6\n"
    "- Each Veo prompt MUST open by describing what __LOCATION__ actually looked like AT THAT ERA before any artistic description\n"
    "- Each prompt MUST include one depth layer element (foreground parallax)\n"
    "- Eras must span at least 2000 years total — do NOT cluster periods in the same century\n"
    "- landmark_name for shots 2-6 = concise era label in __LANG__ (e.g. '500 TCN', 'Thế kỷ 11', 'Thế kỷ 15', '1900s', 'Năm 2500')\n"
    "- NO people, no faces, no text in scene, no watermarks\n"
    "- intro_phrase and landmark_name values in __LANG__"
)


def _fallback_timeline(location: str) -> dict:
    return {
        "intro_phrase": f"{location} — from ancient to future",
        "visuals": [
            {
                "prompt": (
                    f"Sweeping aerial drone shot, anamorphic wide lens, {location} iconic skyline today, "
                    "modern architecture and urban grid, blue-hour ambient glow, glass towers, "
                    "massive structural beam in foreground, cinematic, hyperrealistic, ultra-detailed, 8K, no text, no watermark"
                ),
                "duration": 6,
                "landmark_name": "",
            },
            {
                "prompt": (
                    f"Bird's-eye view slow pan, wide-angle 18mm, {location} in ancient times, "
                    "stone temples and terracotta rooftops, dusty limestone plaza with market stalls, "
                    "golden morning sun over stone temples, ancient arch gate in close foreground, "
                    "cinematic, hyperrealistic, ultra-detailed, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": "Ancient Era",
            },
            {
                "prompt": (
                    f"Slow cinematic dolly forward, shallow depth of field, {location} in medieval times, "
                    "crumbling stone walls and sparse wooden settlements, overgrown ancient ruins, "
                    "cold overcast gray light, ruined wall fragment in near foreground, "
                    "cinematic, hyperrealistic, ultra-detailed, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": "Medieval Era",
            },
            {
                "prompt": (
                    f"Slow crane up reveal, anamorphic lens, {location} in the renaissance, "
                    "marble domes and baroque plazas, ornate facades lining broad stone avenues, "
                    "warm afternoon golden hour, polished marble column in foreground, "
                    "cinematic, hyperrealistic, ultra-detailed, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": "Renaissance",
            },
            {
                "prompt": (
                    f"Tracking shot along skyline, 35mm cinematic, {location} in the early 1900s, "
                    "brick and iron industrial buildings, cobblestone streets and tram lines, "
                    "sepia-tinted coal haze with steam columns, energy pylon tower in foreground, "
                    "cinematic, hyperrealistic, ultra-detailed, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": "1900s",
            },
            {
                "prompt": (
                    f"Extreme low-angle looking up, wide-angle distortion, {location} in year 2500, "
                    "mega-towers of glass and plasma conduits rising from ancient foundations, "
                    "hyperloop transit arches spanning ancient landmarks, neon city bloom and holographic sky, "
                    "floating transport platform passing close, "
                    "cinematic, hyperrealistic, ultra-detailed, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": "Year 2500",
            },
        ],
        "vibe": "Orchestral Epic",
    }


def _vertex_host(location: str) -> str:
    # Global uses the shared endpoint host, regional locations use {location}-aiplatform.
    if location == "global":
        return "aiplatform.googleapis.com"
    return f"{location}-aiplatform.googleapis.com"


_VEO_PROMPT_FORMULA = """\
Every Veo prompt MUST follow this formula:
  [UNIQUE CAMERA MOVE + LENS] + [VISUAL ANCHOR of the real landmark] + [FUTURISTIC TRANSFORMATION] + [DEPTH LAYER] + [ATMOSPHERE] + [QUALITY TAGS]

Camera moves + lens (each shot must use a DIFFERENT one):
  sweeping aerial drone shot, anamorphic wide lens | slow cinematic dolly forward, shallow depth of field |
  bird's-eye view slow pan, wide-angle 18mm | low-altitude flyover, telephoto compression |
  extreme low-angle looking up, wide-angle distortion | slow crane up reveal, anamorphic lens |
  tracking shot along skyline, 35mm cinematic

Atmosphere (each shot must use a DIFFERENT one):
  blue-hour ambient glow, volumetric light shafts | golden-hour cinematic lighting, lens flare |
  dramatic overcast storm light, god rays breaking through clouds | night city neon bloom, reflective wet surfaces |
  sunrise warm diffused haze, bioluminescent particles floating | twilight purple sky, holographic data streams

Depth layer (add ONE per prompt — creates foreground parallax):
  massive structural beam in foreground | hovering transport pod passing close | foreground glass panel reflection |
  energy conduit tower in foreground | cascading waterfall edge in near foreground

Quality tags (always append to every prompt):
  cinematic, hyperrealistic, ultra-detailed, 8K, anamorphic, no people, no text, no watermark

CRITICAL — Visual Anchor rule:
  Each prompt MUST describe the real-world visual signature of that specific landmark FIRST, then transform it.
  Example: instead of "futuristic Shibuya" → write "the iconic X-shaped pedestrian crossing of Shibuya, now a floating platform of glowing androids and plasma conduit networks..."
  Example: instead of "futuristic Dragon Bridge Da Nang" → write "the dragon-shaped suspension bridge spanning the Han River, now a colossal bio-mechanical dragon of living metal with plasma breath arching across a neon waterway..."
  The visual anchor makes each clip look DIFFERENT from each other.
"""

_FICTIONAL_VEO_PROMPT_FORMULA = """\
Every Veo prompt MUST follow this formula:
  [UNIQUE CAMERA MOVE + LENS] + [REALM AREA SIGNATURE] + [SUPERNATURAL/ALIEN SPECTACLE] + [DEPTH LAYER] + [OTHERWORLDLY ATMOSPHERE] + [QUALITY TAGS]

Camera moves + lens (each shot must use a DIFFERENT one):
  sweeping aerial drone shot, anamorphic wide lens | slow cinematic dolly forward, shallow depth of field |
  bird's-eye view slow pan, wide-angle 18mm | low-altitude flyover, telephoto compression |
  extreme low-angle looking up, wide-angle distortion | slow crane up reveal, anamorphic lens |
  tracking shot, 35mm cinematic

Otherworldly atmosphere (each shot must use a DIFFERENT one — must feel alien/divine/supernatural):
  iridescent aurora-filled sky with twin moons | ethereal golden divine radiance, floating mist |
  volcanic crimson hellfire glow, ember particles | deep cosmic nebula backdrop, star clusters |
  crystalline bioluminescent mist, soft blue glow | silver moonlit ethereal haze, translucent veils |
  spectral ghost-green phosphorescence, ancient energy

Depth layer (add ONE per prompt):
  massive gate pillar in foreground | ancient stone column close-up | energy pillar in near foreground |
  ancient lantern chain in foreground | crystal formation in close foreground

Quality tags (always append to every prompt):
  cinematic, hyperrealistic, ultra-detailed, 8K, anamorphic, no people, no text, no watermark

CRITICAL — Realm Signature rule:
  Each prompt MUST describe the canonical visual signature of that specific area/zone from its mythology or lore FIRST, then render it as a photorealistic scene.
  Example for Thiên Đình: "the towering jade-white Nantian Gate of Heaven, twin dragon pillars flanking an ornate celestial archway, clouds of golden mist swirling below..."
  Example for Địa Phủ: "the black iron gates of the Ten Courts of Hell, massive ox-headed guards flanking crimson lantern-lit corridors of obsidian stone..."
  Example for Mars colony: "the vast rust-red plains of Valles Marineris canyon system, now lined with terraforming glass dome habitats stretching to the horizon..."
  The realm signature makes each clip look VISUALLY DIFFERENT from every other clip.
"""

_BRAIN_PROMPT = (
    'You are a Veo video director creating a futuristic "What If" YouTube Shorts video.\n\n'
    "Topic: __TOPIC__\n\n"
    "Task: Generate exactly 6 cinematic shots for a 24-28 second vertical Shorts video imagining __TOPIC__ transformed far into the future.\n\n"
    "__VEO_GUIDE__\n"
    "Shot structure:\n"
    '- Shot 1 (opening hero): Awe-inspiring wide aerial reveal of the entire futuristic city — jaw-dropping scale, impossible megastructures filling the frame. duration=6, landmark_name=""\n'
    "- Shots 2-6: Pick 5 of the most ICONIC and RECOGNIZABLE real-world landmarks/areas of __TOPIC__ and reimagine each one. Must be places that actually exist and are famous — specific to THIS city, not generic labels. Each prompt MUST start by describing the real-world visual signature of that landmark (its shape, structure, or what makes it recognizable), then transform it into a futuristic version with specific sci-fi tech (plasma conduits, anti-gravity platforms, bioluminescent crystal, neural interface towers, etc.). Each clip must look VISUALLY DIFFERENT from every other clip. duration=4 each.\n\n"
    "Return ONLY a valid JSON object (no markdown, no explanation):\n"
    '{\n'
    '  "intro_phrase": "<punchy 6-8 word question in __LANG__, e.g. What would Tokyo look like in 3000?>",\n'
    '  "visuals": [\n'
    '    {"prompt": "<shot 1 — awe-inspiring wide aerial hero shot>", "duration": 6, "landmark_name": ""},\n'
    '    {"prompt": "<shot 2 — iconic landmark of __TOPIC__ reimagined with specific sci-fi transformation>", "duration": 4, "landmark_name": "<real area name in __LANG__, 2-4 words>"},\n'
    '    {"prompt": "<shot 3 — different iconic landmark of __TOPIC__ reimagined>", "duration": 4, "landmark_name": "<real area name in __LANG__, 2-4 words>"},\n'
    '    {"prompt": "<shot 4 — different iconic landmark of __TOPIC__ reimagined>", "duration": 4, "landmark_name": "<real area name in __LANG__, 2-4 words>"},\n'
    '    {"prompt": "<shot 5 — different iconic landmark of __TOPIC__ reimagined>", "duration": 4, "landmark_name": "<real area name in __LANG__, 2-4 words>"},\n'
    '    {"prompt": "<shot 6 — cinematic closing wide shot, different landmark>", "duration": 4, "landmark_name": "<real area name in __LANG__, 2-4 words>"}\n'
    '  ],\n'
    '  "vibe": "<music genre that fits this city\'s futuristic vibe>"\n'
    '}\n\n'
    "Hard rules:\n"
    "- Exactly 6 shots: shot 1 duration=6, shots 2-6 duration=4\n"
    "- Each shot MUST use a DIFFERENT camera move+lens AND a DIFFERENT atmosphere — no repeats across all 6\n"
    "- Each Veo prompt MUST open with the real-world visual signature of that landmark before any futuristic description — this is what makes each clip look unique\n"
    "- Each prompt MUST include one depth layer element (foreground parallax)\n"
    "- landmark_name for shots 2-6 MUST be real, recognizable place names that exist in __TOPIC__ — NOT generic labels like 'City Center', 'Old Quarter', 'Central District', 'Business District', 'Waterfront', 'Transit Hub'; MAX 4 words\n"
    "- Prefer bridges, beaches, hills, specific roads, monuments, stadiums over vague districts\n"
    "- NO faces, no text in scene, no watermarks\n"
    "- intro_phrase and landmark_name values in __LANG__"
)

_FICTIONAL_BRAIN_PROMPT = (
    'You are a Veo video director creating a mythological/fantastical "What If" YouTube Shorts video.\n\n'
    "Topic: __TOPIC__\n\n"
    "Task: Generate exactly 6 cinematic shots for a 24-28 second vertical Shorts video rendering __TOPIC__ as a jaw-dropping photorealistic world.\n\n"
    "__VEO_GUIDE__\n"
    "Realm visual vocabulary — use the appropriate style based on the topic:\n"
    "- Planets (Mars/Sao Hỏa, Moon/Mặt Trăng, Mercury/Sao Thủy, etc.): alien terrain textures, terraforming dome habitats, space-age architecture, alien sky colors, twin moons/gas giant backdrops\n"
    "- Chinese Mythology (Thiên Đình/Celestial Court, Tiên Giới/Immortal Realm, Địa Phủ/Underworld): jade and gold palatial towers, celestial dragon motifs, cloud sea terraces, ornate gate pillars, red-and-gold lanterns, black iron underworld courts\n"
    "- Western Mythology (Heaven/Thiên Đàng, Hell/Địa Ngục, Asgard): divine white marble, towering golden gates, volcanic obsidian hellscapes, Norse stone halls with runes, rainbow bridge Bifrost\n"
    "- Cosmic/Abstract: impossible non-Euclidean geometry, living crystalline light, fractal landscapes\n\n"
    "Shot structure:\n"
    '- Shot 1 (opening hero): Awe-inspiring wide aerial reveal of the entire realm — overwhelming divine/alien scale, impossible beauty or terror. duration=6, landmark_name=""\n'
    "- Shots 2-6: Pick 5 of the most ICONIC zones, structures, or features of __TOPIC__ from its mythology or cultural lore and render each one. Each prompt MUST start by describing the canonical visual signature of that area (its mythological iconography — what makes it recognizable from stories/art), then visualize it as a stunning photorealistic scene. Each clip must look VISUALLY DIFFERENT. duration=4 each.\n\n"
    "Return ONLY a valid JSON object (no markdown, no explanation):\n"
    '{\n'
    '  "intro_phrase": "<punchy 6-8 word question or statement in __LANG__, e.g. What does Heaven really look like?>",\n'
    '  "visuals": [\n'
    '    {"prompt": "<shot 1 — awe-inspiring wide hero shot of the entire realm>", "duration": 6, "landmark_name": ""},\n'
    '    {"prompt": "<shot 2 — iconic zone/structure of __TOPIC__ rendered photorealistically>", "duration": 4, "landmark_name": "<area name from mythology in __LANG__, 2-4 words>"},\n'
    '    {"prompt": "<shot 3 — different iconic zone of __TOPIC__>", "duration": 4, "landmark_name": "<area name in __LANG__, 2-4 words>"},\n'
    '    {"prompt": "<shot 4 — different iconic zone of __TOPIC__>", "duration": 4, "landmark_name": "<area name in __LANG__, 2-4 words>"},\n'
    '    {"prompt": "<shot 5 — different iconic zone of __TOPIC__>", "duration": 4, "landmark_name": "<area name in __LANG__, 2-4 words>"},\n'
    '    {"prompt": "<shot 6 — cinematic closing wide shot of __TOPIC__>", "duration": 4, "landmark_name": "<area name in __LANG__, 2-4 words>"}\n'
    '  ],\n'
    '  "vibe": "<music genre that fits this realm — e.g. Orchestral Epic, Dark Ambient, Celestial Ambient, Gregorian Chant, Taiko Drums>"\n'
    '}\n\n'
    "Hard rules:\n"
    "- Exactly 6 shots: shot 1 duration=6, shots 2-6 duration=4\n"
    "- Each shot MUST use a DIFFERENT camera move+lens AND a DIFFERENT otherworldly atmosphere — no repeats across all 6\n"
    "- Each Veo prompt MUST open with the canonical visual signature of that mythological area before any photorealistic description\n"
    "- Each prompt MUST include one depth layer element (foreground parallax)\n"
    "- landmark_name for shots 2-6 must be iconic named zones from this realm's mythology — NOT generic labels like 'Area 1', 'Zone A'; MAX 4 words\n"
    "- NO faces, no text in scene, no watermarks\n"
    "- intro_phrase and landmark_name values in __LANG__"
)


def _build_payload(prompt_text: str, use_schema: bool = True) -> dict:
    generation_config = {
        "temperature": 0.5,
        "maxOutputTokens": 8000,
        "responseMimeType": "application/json",
    }
    if use_schema:
        generation_config["responseSchema"] = _BRAIN_RESPONSE_SCHEMA

    return {
        "contents": [{"role": "user", "parts": [{"text": prompt_text}]}],
        "generationConfig": generation_config,
    }


def _normalize_duration(value: int | str | None) -> int:
    try:
        d = int(value) if value is not None else 6
    except (TypeError, ValueError):
        d = 6
    return min(_SUPPORTED_DURATIONS, key=lambda s: abs(s - d))


def _clean_raw_text(raw_text: str) -> str:
    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text.strip())
    raw_text = re.sub(r"\s*```$", "", raw_text.strip())
    return raw_text.strip()


def _extract_json_object(raw_text: str) -> str:
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return raw_text
    return raw_text[start : end + 1]


def _extract_raw_text(data: dict) -> str:
    try:
        return data["candidates"][0]["content"]["parts"][0].get("text", "")
    except Exception:  # noqa: BLE001
        return ""


def _cleanup_json_string(value: str) -> str:
    v = value.strip()
    v = v.replace('\\"', '"').replace("\\n", " ")
    return re.sub(r"\s+", " ", v).strip()


def _fallback_brain(topic: str) -> dict:
    return {
        "intro_phrase": f"What would {topic} look like in the future?",
        "visuals": [
            {
                "prompt": (
                    f"Sweeping aerial drone shot, futuristic {topic} megacity skyline, glass mega-towers, "
                    "flying vehicles, blue-hour ambient glow, cinematic, photorealistic, ultra-detailed, 8K, no text, no watermark"
                ),
                "duration": 6,
                "landmark_name": "",
            },
            {
                "prompt": (
                    f"Slow cinematic dolly forward, futuristic commercial district of {topic}, "
                    "holographic billboards, elevated sky-bridges, golden-hour cinematic lighting, "
                    "cinematic, photorealistic, ultra-detailed, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": "Business District",
            },
            {
                "prompt": (
                    f"Bird's-eye view slow pan, futuristic waterfront of {topic}, "
                    "glowing skyline reflections, flying taxis, night city neon bloom, "
                    "cinematic, photorealistic, ultra-detailed, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": "Waterfront",
            },
            {
                "prompt": (
                    f"Low-altitude flyover, futuristic transit hub of {topic}, "
                    "autonomous pods, vertical gardens on towers, dramatic overcast storm light, "
                    "cinematic, photorealistic, ultra-detailed, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": "Transit Hub",
            },
            {
                "prompt": (
                    f"Wide establishing shot, futuristic residential towers of {topic}, "
                    "rooftop sky-parks, neon-lit walkways, twilight purple sky, "
                    "cinematic, photorealistic, ultra-detailed, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": "Sky District",
            },
            {
                "prompt": (
                    f"Slow crane up reveal, dramatic wide shot of futuristic {topic} megacity, "
                    "entire skyline, sunrise warm diffused haze, "
                    "cinematic, photorealistic, ultra-detailed, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": "Skyline",
            },
        ],
        "vibe": "Cyberpunk Phonk",
    }


def _salvage_brain_from_text(raw_text: str, topic: str) -> dict:
    text = _clean_raw_text(raw_text)
    if not text:
        return _fallback_brain(topic)

    intro_match = re.search(r'"intro_phrase"\s*:\s*"([\s\S]*?)"\s*(?:,|})', text)
    prompts = re.findall(r'"prompt"\s*:\s*"([\s\S]*?)"\s*(?:,|})', text)
    durations = re.findall(r'"duration"\s*:\s*(\d+)', text)
    landmarks = re.findall(r'"landmark_name"\s*:\s*"([\s\S]*?)"\s*(?:,|})', text)
    vibe_match = re.search(r'"vibe"\s*:\s*"([\s\S]*?)"\s*(?:,|})', text)

    result = _fallback_brain(topic)

    if intro_match:
        result["intro_phrase"] = _cleanup_json_string(intro_match.group(1))
    if vibe_match:
        result["vibe"] = _cleanup_json_string(vibe_match.group(1))

    if prompts:
        visuals = []
        for i in range(min(6, len(prompts))):
            visuals.append(
                {
                    "prompt": _cleanup_json_string(prompts[i]),
                    "duration": _normalize_duration(durations[i] if i < len(durations) else 4),
                    "landmark_name": _cleanup_json_string(landmarks[i]) if i < len(landmarks) else "",
                }
            )
        while len(visuals) < 6:
            visuals.append(result["visuals"][len(visuals)])
        result["visuals"] = visuals

    return result


def _parse_response(data: dict) -> dict:
    raw_text = _extract_raw_text(data)
    raw_text = _clean_raw_text(raw_text)

    parse_errors: list[Exception] = []
    for candidate in (raw_text, _extract_json_object(raw_text)):
        if not candidate:
            continue
        try:
            result = json.loads(candidate)
            break
        except JSONDecodeError as exc:
            parse_errors.append(exc)
    else:
        preview = raw_text[:240].replace("\n", " ")
        logger.warning("Gemini returned invalid JSON: %s", preview)
        raise parse_errors[-1] if parse_errors else ValueError("Gemini response had no parseable JSON")

    if "intro_phrase" not in result or "visuals" not in result:
        raise ValueError("Gemini response missing required keys: intro_phrase/visuals")

    if not isinstance(result["visuals"], list) or not result["visuals"]:
        raise ValueError("Gemini visuals must be a non-empty list")

    for visual in result["visuals"]:
        visual["duration"] = _normalize_duration(visual.get("duration"))

    logger.info("Gemini response: vibe=%s, intro_phrase=%r", result.get("vibe"), result.get("intro_phrase", ""))
    return result


async def generate_brain(topic: str, language: str = "en", topic_type: str = "city_future") -> dict:
    if topic_type == "fictional_realm":
        base_prompt = _FICTIONAL_BRAIN_PROMPT
        veo_guide = _FICTIONAL_VEO_PROMPT_FORMULA
    else:
        base_prompt = _BRAIN_PROMPT
        veo_guide = _VEO_PROMPT_FORMULA

    prompt_text = (
        base_prompt
        .replace("__TOPIC__", topic)
        .replace("__LANG__", language)
        .replace("__VEO_GUIDE__", veo_guide)
    )
    logger.info(
        "Gemini: using Vertex AI, model=%s, location=%s",
        settings.gemini_model,
        settings.gemini_location,
    )

    creds = sa.Credentials.from_service_account_file(
        settings.vertex_ai_credentials_file,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    creds.refresh(GoogleAuthRequest())
    url = _VERTEX_URL_TPL.format(
        host=_vertex_host(settings.gemini_location),
        location=settings.gemini_location,
        project=settings.gcp_project,
        model=settings.gemini_model,
    )
    headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        last_error: Exception | None = None
        best_raw_text = ""  # keep the longest non-empty raw text across all attempts
        use_schema = True
        for attempt in range(1, 4):
            attempt_prompt = prompt_text
            if attempt > 1:
                attempt_prompt += (
                    "\n\nIMPORTANT: Return strict minified JSON only. "
                    "Do not include markdown, comments, or trailing text."
                )

            try:
                resp = await client.post(
                    url,
                    json=_build_payload(attempt_prompt, use_schema=use_schema),
                    headers=headers,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 400 and use_schema:
                    logger.warning("Gemini rejected responseSchema; retrying without schema")
                    use_schema = False
                    last_error = exc
                    continue
                raise

            body = resp.json()
            raw_text = _extract_raw_text(body)
            # Keep the longest non-empty raw text — later attempts may return empty
            if len(raw_text) > len(best_raw_text):
                best_raw_text = raw_text

            try:
                return _parse_response(body)
            except (JSONDecodeError, ValueError, KeyError, IndexError) as exc:
                last_error = exc
                logger.warning("Gemini JSON parse failed (attempt %d/3): %s", attempt, exc)

        if last_error is not None:
            logger.error(
                "Gemini parse failed after retries, salvaging response: %s | raw_text_preview=%r",
                last_error,
                best_raw_text[:500],
            )
            return _salvage_brain_from_text(best_raw_text, topic)

    raise RuntimeError("Gemini request loop exited unexpectedly")


async def generate_timeline_brain(location: str, language: str = "en") -> dict:
    prompt_text = (
        _TIMELINE_BRAIN_PROMPT
        .replace("__LOCATION__", location)
        .replace("__LANG__", language)
        .replace("__VEO_GUIDE__", _TIMELINE_VEO_FORMULA)
    )
    logger.info(
        "Timeline brain: model=%s, location=%s",
        settings.gemini_model,
        settings.gemini_location,
    )

    creds = sa.Credentials.from_service_account_file(
        settings.vertex_ai_credentials_file,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    creds.refresh(GoogleAuthRequest())
    url = _VERTEX_URL_TPL.format(
        host=_vertex_host(settings.gemini_location),
        location=settings.gemini_location,
        project=settings.gcp_project,
        model=settings.gemini_model,
    )
    headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        last_error: Exception | None = None
        best_raw_text = ""
        use_schema = True
        for attempt in range(1, 4):
            attempt_prompt = prompt_text
            if attempt > 1:
                attempt_prompt += (
                    "\n\nIMPORTANT: Return strict minified JSON only. "
                    "Do not include markdown, comments, or trailing text."
                )
            try:
                resp = await client.post(
                    url,
                    json=_build_payload(attempt_prompt, use_schema=use_schema),
                    headers=headers,
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 400 and use_schema:
                    logger.warning("Gemini rejected responseSchema (timeline); retrying without schema")
                    use_schema = False
                    last_error = exc
                    continue
                raise

            body = resp.json()
            raw_text = _extract_raw_text(body)
            if len(raw_text) > len(best_raw_text):
                best_raw_text = raw_text

            try:
                return _parse_response(body)
            except (JSONDecodeError, ValueError, KeyError, IndexError) as exc:
                last_error = exc
                logger.warning("Timeline brain JSON parse failed (attempt %d/3): %s", attempt, exc)

        if last_error is not None:
            logger.error(
                "Timeline brain parse failed after retries, using fallback: %s | raw_text_preview=%r",
                last_error,
                best_raw_text[:500],
            )
            salvaged = _salvage_brain_from_text(best_raw_text, location)
            if salvaged and salvaged.get("visuals"):
                return salvaged
            return _fallback_timeline(location)

    raise RuntimeError("Timeline brain request loop exited unexpectedly")

