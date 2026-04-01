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
            "minItems": 4,
            "maxItems": 5,
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


_TIMELINE_BRAIN_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "required": ["intro_phrase", "visuals", "vibe"],
    "properties": {
        "intro_phrase": {"type": "STRING"},
        "visuals": {
            "type": "ARRAY",
            "minItems": 5,
            "maxItems": 5,
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

Camera moves + lens (each shot must use a DIFFERENT one — ALL must have visible, dynamic motion — NO static or locked-off shots):
  fast sweeping aerial drone shot, ultra-wide anamorphic 14mm | rapid cinematic push-in, shallow depth of field, wide angle |
  fast bird's-eye pan across the horizon, ultra wide-angle 14mm | dynamic low-altitude flyover, telephoto compression |
  extreme low-angle sweeping upward pan, wide-angle distortion | fast crane up reveal, anamorphic wide lens |
  fast tracking shot along skyline, 35mm cinematic

CRITICAL — Camera Motion rule: Every shot MUST have pronounced, fast camera movement — sweeping pans, rapid fly-throughs, fast cranes. NO still or stationary camera.

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
    "Task: Generate exactly 5 cinematic shots showing __LOCATION__ across radically different historical eras — "
    "each era must be separated by AT LEAST 300 years from the previous one, so viewers see dramatic visual transformation between every clip.\n\n"
    "__VEO_GUIDE__\n"
    "Shot structure — use EXACTLY these 5 era slots in chronological order:\n"
    "- Shot 1 (opening hook): __LOCATION__ TODAY (present day) — fast sweeping wide aerial overhead shot. duration=4, landmark_name=\"\"\n"
    "- Shot 2 (deep ancient): __LOCATION__ in its EARLIEST historical era — pick the most ancient period relevant to this location (e.g. 3000 BC, 500 BC, 100 AD — choose one specific year). "
    "Show raw ancient architecture: stone, mud-brick, primitive settlements, open wilderness. landmark_name = specific year in __LANG__ (e.g. '500 TCN', '100 AD'). duration=4\n"
    "- Shot 3 (medieval/classical): __LOCATION__ roughly 300–600 years AFTER shot 2 — pick the medieval, classical, or early imperial era of this specific location. "
    "Architecture transitions: wooden towers, walled fortifications, early market squares. landmark_name = specific century in __LANG__ (e.g. 'Thế kỷ 8', 'Century 12'). duration=4\n"
    "- Shot 4 (industrial/modern): __LOCATION__ roughly 300–600 years AFTER shot 3 — pick the colonial, industrial, or 20th-century modern era. "
    "Architecture: iron and brick buildings, tram roads, early urban grid, or mid-century concrete. This era must be between 1800 AD and 1990 AD. landmark_name = specific decade in __LANG__ (e.g. '1920s', 'Thập niên 1960'). duration=4\n"
    "- Shot 5 (far future): __LOCATION__ at least 500 years FROM NOW — minimum year 2500 AD. "
    "Architecture: mega-towers, floating platforms, holographic structures, plasma conduits. landmark_name = specific far future year in __LANG__ (e.g. 'Năm 2500', 'Year 2800'). duration=4\n\n"
    "CRITICAL ERA SPACING RULE: Calculate the year gaps between shots 2→3→4→5. Each gap MUST be at least 300 years. "
    "If a gap is under 300 years, pick a different year. Write the chosen years in the landmark_name so they are clearly visible.\n\n"
    "Return ONLY a valid JSON object (no markdown, no explanation):\n"
    '{\n'
    '  "intro_phrase": "<punchy 7-9 word hook in __LANG__, e.g. \'Rome — 2500 năm trong 20 giây\'>",\n'
    '  "visuals": [\n'
    '    {"prompt": "<shot 1 — fast sweeping wide aerial reveal of __LOCATION__ today>", "duration": 4, "landmark_name": ""},\n'
    '    {"prompt": "<shot 2 — __LOCATION__ in deep ancient era, exact year chosen>", "duration": 4, "landmark_name": "<specific year in __LANG__>"},\n'
    '    {"prompt": "<shot 3 — __LOCATION__ in medieval/classical era, at least 300 years after shot 2>", "duration": 4, "landmark_name": "<specific century in __LANG__>"},\n'
            {"prompt": "<shot 4 — __LOCATION__ in industrial or modern era, between 1800-1990 AD, at least 300 years after shot 3>", "duration": 4, "landmark_name": "<specific decade in __LANG__>"},\n"
    '    {"prompt": "<shot 5 — __LOCATION__ in far future, minimum year 2500>", "duration": 4, "landmark_name": "<far future year in __LANG__>"}\n'
    '  ],\n'
    '  "vibe": "<music genre fitting this historical journey — e.g. Orchestral Epic, Cinematic Score, Taiko Drums>"\n'
    '}\n\n'
    "Hard rules:\n"
    "- Exactly 5 shots: all duration=4, in order (today → ancient → medieval → industrial/modern → far future)\n"
    "- Each shot MUST use a DIFFERENT camera move+lens AND a DIFFERENT era atmosphere — no repeats across all 5\n"
    "- Every camera move MUST be fast and dynamic — sweeping pans, rapid fly-throughs, fast crane reveals — NO slow or static shots\n"
    "- Each Veo prompt MUST open by describing what __LOCATION__ actually looked like AT THAT ERA (architecture, materials, density) before any artistic description\n"
    "- Each prompt MUST include one depth layer element (foreground parallax)\n"
    "- MINIMUM 300-year gap between shots 2, 3, and 4 — absolutely no two adjacent historical shots within the same century\n"
    "- Shot 4 must be between 1800 and 1990 AD; shot 5 must be 2500 AD or later\n"
    "- landmark_name for shots 2-5 = specific year/decade/century in __LANG__ — NO vague labels like 'Ancient Era' or 'Medieval'\n"
    "- NO people, no faces, no text in scene, no watermarks\n"
    "- intro_phrase and landmark_name values in __LANG__"
)


def _fallback_timeline(location: str) -> dict:
    return {
        "intro_phrase": f"{location} — from ancient to future",
        "visuals": [
            {
                "prompt": (
                    f"Fast sweeping aerial drone shot, ultra-wide anamorphic 14mm, {location} iconic skyline today, "
                    "modern architecture and urban grid, rapid pan across the horizon, blue-hour ambient glow, glass towers, "
                    "massive structural beam in foreground, cinematic, hyperrealistic, ultra-detailed, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": "",
            },
            {
                "prompt": (
                    f"Fast bird's-eye pan across the horizon, ultra wide-angle 14mm, {location} in ancient times, "
                    "stone temples and terracotta rooftops, dusty limestone plaza with market stalls, "
                    "golden morning sun over stone temples, ancient arch gate in close foreground, "
                    "cinematic, hyperrealistic, ultra-detailed, 8K, no text, no watermark"
                ),
                "duration": 4,
            "landmark_name": "500 BC",
            },
            {
                "prompt": (
                    f"Rapid cinematic push-in, shallow depth of field, wide angle, {location} in medieval times, "
                    "crumbling stone walls and sparse wooden settlements, overgrown ancient ruins, "
                    "cold overcast gray light, ruined wall fragment in near foreground, "
                    "cinematic, hyperrealistic, ultra-detailed, 8K, no text, no watermark"
                ),
                "duration": 4,
            "landmark_name": "1200s",
            },
            {
                "prompt": (
                    f"Fast tracking shot along skyline, 35mm cinematic, {location} in the early 1900s, "
                    "brick and iron industrial buildings, cobblestone streets and tram lines, "
                    "sepia-tinted coal haze with steam columns, energy pylon tower in foreground, "
                    "cinematic, hyperrealistic, ultra-detailed, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": "1920s",
            },
            {
                "prompt": (
                    f"Extreme low-angle sweeping upward pan, wide-angle distortion, {location} in year 2500, "
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

Camera moves + lens (each shot must use a DIFFERENT one — ALL must have fast, visible motion — NO static or locked-off shots):
  fast sweeping aerial drone shot, ultra-wide anamorphic 14mm | rapid cinematic push-in, shallow depth of field, wide angle |
  fast bird's-eye pan across the horizon, ultra wide-angle 14mm | dynamic low-altitude flyover, telephoto compression |
  extreme low-angle sweeping upward pan, wide-angle distortion | fast crane up reveal, anamorphic wide lens |
  fast tracking shot along skyline, 35mm cinematic

CRITICAL — Camera Motion rule: Every shot MUST have fast, dynamic camera movement — sweeping pans, rapid fly-throughs, fast crane reveals. NO still or stationary camera.

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

Camera moves + lens (each shot must use a DIFFERENT one — ALL must have fast, visible motion — NO static or locked-off shots):
  fast sweeping aerial drone shot, ultra-wide anamorphic 14mm | rapid cinematic push-in, shallow depth of field, wide angle |
  fast bird's-eye pan across the horizon, ultra wide-angle 14mm | dynamic low-altitude flyover, telephoto compression |
  extreme low-angle sweeping upward pan, wide-angle distortion | fast crane up reveal, anamorphic wide lens |
  fast tracking shot, 35mm cinematic

CRITICAL — Camera Motion rule: Every shot MUST have fast, dynamic camera movement — sweeping pans, rapid fly-throughs, fast crane reveals. NO still or stationary camera.

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
    "Task: Generate exactly 5 cinematic shots for a 18-20 second vertical Shorts video imagining __TOPIC__ transformed far into the future.\n\n"
    "__VEO_GUIDE__\n"
    "Shot structure:\n"
    '- Shot 1 (opening hero): Awe-inspiring fast sweeping wide aerial reveal of the entire futuristic city — jaw-dropping scale, impossible megastructures filling the frame. duration=4, landmark_name=""\n'
    "- Shots 2-5: Pick 4 of the most ICONIC and RECOGNIZABLE real-world landmarks/areas of __TOPIC__ and reimagine each one. Must be places that actually exist and are famous — specific to THIS city, not generic labels. Each prompt MUST start by describing the real-world visual signature of that landmark (its shape, structure, or what makes it recognizable), then transform it into a futuristic version with specific sci-fi tech (plasma conduits, anti-gravity platforms, bioluminescent crystal, neural interface towers, etc.). Each clip must look VISUALLY DIFFERENT from every other clip. duration=4 each.\n\n"
    "Return ONLY a valid JSON object (no markdown, no explanation):\n"
    '{\n'
    '  "intro_phrase": "<punchy 6-8 word question in __LANG__, e.g. What would Tokyo look like in 3000?>",\n'
    '  "visuals": [\n'
    '    {"prompt": "<shot 1 — awe-inspiring fast sweeping wide aerial hero shot>", "duration": 4, "landmark_name": ""},\n'
    '    {"prompt": "<shot 2 — iconic landmark of __TOPIC__ reimagined with specific sci-fi transformation>", "duration": 4, "landmark_name": "<real area name in __LANG__, 2-4 words>"},\n'
    '    {"prompt": "<shot 3 — different iconic landmark of __TOPIC__ reimagined>", "duration": 4, "landmark_name": "<real area name in __LANG__, 2-4 words>"},\n'
    '    {"prompt": "<shot 4 — different iconic landmark of __TOPIC__ reimagined>", "duration": 4, "landmark_name": "<real area name in __LANG__, 2-4 words>"},\n'
    '    {"prompt": "<shot 5 — cinematic closing wide shot, different landmark>", "duration": 4, "landmark_name": "<real area name in __LANG__, 2-4 words>"}\n'
    '  ],\n'
    '  "vibe": "<music genre that fits this city\'s futuristic vibe>"\n'
    '}\n\n'
    "Hard rules:\n"
    "- Exactly 5 shots: all duration=4\n"
    "- Each shot MUST use a DIFFERENT camera move+lens AND a DIFFERENT atmosphere — no repeats across all 5\n"
    "- Every camera move MUST be fast and dynamic — sweeping pans, rapid fly-throughs, fast crane reveals — NO slow or static shots\n"
    "- Each Veo prompt MUST open with the real-world visual signature of that landmark before any futuristic description — this is what makes each clip look unique\n"
    "- Each prompt MUST include one depth layer element (foreground parallax)\n"
    "- landmark_name for shots 2-5 MUST be real, recognizable place names that exist in __TOPIC__ — NOT generic labels like 'City Center', 'Old Quarter', 'Central District', 'Business District', 'Waterfront', 'Transit Hub'; MAX 4 words\n"
    "- Prefer bridges, beaches, hills, specific roads, monuments, stadiums over vague districts\n"
    "- NO faces, no text in scene, no watermarks\n"
    "- intro_phrase and landmark_name values in __LANG__"
)

_FICTIONAL_BRAIN_PROMPT = (
    'You are a Veo video director creating a mythological/fantastical "What If" YouTube Shorts video.\n\n'
    "Topic: __TOPIC__\n\n"
    "Task: Generate exactly 5 cinematic shots for a 18-20 second vertical Shorts video rendering __TOPIC__ as a jaw-dropping photorealistic world.\n\n"
    "__VEO_GUIDE__\n"
    "Realm visual vocabulary — use the appropriate style based on the topic:\n"
    "- Planets (Mars/Sao Hỏa, Moon/Mặt Trăng, Mercury/Sao Thủy, etc.): alien terrain textures, terraforming dome habitats, space-age architecture, alien sky colors, twin moons/gas giant backdrops\n"
    "- Chinese Mythology (Thiên Đình/Celestial Court, Tiên Giới/Immortal Realm, Địa Phủ/Underworld): jade and gold palatial towers, celestial dragon motifs, cloud sea terraces, ornate gate pillars, red-and-gold lanterns, black iron underworld courts\n"
    "- Western Mythology (Heaven/Thiên Đàng, Hell/Địa Ngục, Asgard): divine white marble, towering golden gates, volcanic obsidian hellscapes, Norse stone halls with runes, rainbow bridge Bifrost\n"
    "- Cosmic/Abstract: impossible non-Euclidean geometry, living crystalline light, fractal landscapes\n\n"
    "Shot structure:\n"
    '- Shot 1 (opening hero): Awe-inspiring fast sweeping wide aerial reveal of the entire realm — overwhelming divine/alien scale, impossible beauty or terror. duration=4, landmark_name=""\n'
    "- Shots 2-5: Pick 4 of the most ICONIC zones, structures, or features of __TOPIC__ from its mythology or cultural lore and render each one. Each prompt MUST start by describing the canonical visual signature of that area (its mythological iconography — what makes it recognizable from stories/art), then visualize it as a stunning photorealistic scene. Each clip must look VISUALLY DIFFERENT. duration=4 each.\n\n"
    "Return ONLY a valid JSON object (no markdown, no explanation):\n"
    '{\n'
    '  "intro_phrase": "<punchy 6-8 word question or statement in __LANG__, e.g. What does Heaven really look like?>",\n'
    '  "visuals": [\n'
    '    {"prompt": "<shot 1 — awe-inspiring fast sweeping wide hero shot of the entire realm>", "duration": 4, "landmark_name": ""},\n'
    '    {"prompt": "<shot 2 — iconic zone/structure of __TOPIC__ rendered photorealistically>", "duration": 4, "landmark_name": "<area name from mythology in __LANG__, 2-4 words>"},\n'
    '    {"prompt": "<shot 3 — different iconic zone of __TOPIC__>", "duration": 4, "landmark_name": "<area name in __LANG__, 2-4 words>"},\n'
    '    {"prompt": "<shot 4 — different iconic zone of __TOPIC__>", "duration": 4, "landmark_name": "<area name in __LANG__, 2-4 words>"},\n'
    '    {"prompt": "<shot 5 — cinematic closing wide shot of __TOPIC__>", "duration": 4, "landmark_name": "<area name in __LANG__, 2-4 words>"}\n'
    '  ],\n'
    '  "vibe": "<music genre that fits this realm — e.g. Orchestral Epic, Dark Ambient, Celestial Ambient, Gregorian Chant, Taiko Drums>"\n'
    '}\n\n'
    "Hard rules:\n"
    "- Exactly 5 shots: all duration=4\n"
    "- Each shot MUST use a DIFFERENT camera move+lens AND a DIFFERENT otherworldly atmosphere — no repeats across all 5\n"
    "- Every camera move MUST be fast and dynamic — sweeping pans, rapid fly-throughs, fast crane reveals — NO slow or static shots\n"
    "- Each Veo prompt MUST open with the canonical visual signature of that mythological area before any photorealistic description\n"
    "- Each prompt MUST include one depth layer element (foreground parallax)\n"
    "- landmark_name for shots 2-5 must be iconic named zones from this realm's mythology — NOT generic labels like 'Area 1', 'Zone A'; MAX 4 words\n"
    "- NO faces, no text in scene, no watermarks\n"
    "- intro_phrase and landmark_name values in __LANG__"
)


def _build_payload(prompt_text: str, use_schema: bool = True, response_schema: dict | None = None) -> dict:
    generation_config = {
        "temperature": 0.5,
        "maxOutputTokens": 8000,
        "responseMimeType": "application/json",
    }
    if use_schema:
        generation_config["responseSchema"] = response_schema if response_schema is not None else _BRAIN_RESPONSE_SCHEMA

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
                    f"Fast sweeping aerial drone shot, ultra-wide anamorphic 14mm, futuristic {topic} megacity skyline, "
                    "glass mega-towers, flying vehicles, rapid pan across the horizon, blue-hour ambient glow, "
                    "cinematic, photorealistic, ultra-detailed, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": "",
            },
            {
                "prompt": (
                    f"Rapid cinematic push-in, wide angle, futuristic commercial district of {topic}, "
                    "holographic billboards, elevated sky-bridges, golden-hour cinematic lighting, "
                    "cinematic, photorealistic, ultra-detailed, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": "Business District",
            },
            {
                "prompt": (
                    f"Fast bird's-eye pan across the horizon, ultra wide-angle 14mm, futuristic waterfront of {topic}, "
                    "glowing skyline reflections, flying taxis, night city neon bloom, "
                    "cinematic, photorealistic, ultra-detailed, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": "Waterfront",
            },
            {
                "prompt": (
                    f"Dynamic low-altitude flyover, telephoto compression, futuristic transit hub of {topic}, "
                    "autonomous pods, vertical gardens on towers, dramatic overcast storm light, "
                    "cinematic, photorealistic, ultra-detailed, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": "Transit Hub",
            },
            {
                "prompt": (
                    f"Fast crane up reveal, anamorphic wide lens, dramatic wide shot of futuristic {topic} megacity, "
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
        for i in range(min(5, len(prompts))):
            visuals.append(
                {
                    "prompt": _cleanup_json_string(prompts[i]),
                    "duration": _normalize_duration(durations[i] if i < len(durations) else 4),
                    "landmark_name": _cleanup_json_string(landmarks[i]) if i < len(landmarks) else "",
                }
            )
        while len(visuals) < 5:
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

_POKEMON_VEO_FORMULA = """\
Every Veo prompt MUST follow this formula:
  [UNIQUE CAMERA MOVE + LENS] + [STYLIZED BIOLOGICAL DESCRIPTION — color + animal base + signature features + animation style] + [CYBERPUNK ARMOR STAGE] + [ACTIVE COMBAT ACTION — skill being fired] + [ENERGY FX + IMPACT EFFECTS] + [ENVIRONMENT] + [ATMOSPHERE] + [QUALITY TAGS]

Camera moves + lens (each shot MUST use a DIFFERENT one — ALL must have fast, aggressive motion — NO static or locked-off shots):
  fast ramping low-angle push-in toward creature's face, ultra-wide 14mm fisheye |
  rapid orbiting 180° tracking shot circling the creature mid-action, 35mm |
  extreme low-angle upward sweep, wide-angle distortion, creature looming over camera |
  fast lateral whip-pan tracking shot keeping creature center-frame, anamorphic 35mm |
  extreme low-angle crane boom upward, anamorphic wide lens, creature rising into frame |
  rapid cinematic push-in from behind, creature launching attack toward camera, 24mm |
  fast bird's-eye spinning overhead shot, creature surrounded by energy rings, 14mm |
  dynamic handheld-style fast zoom-out reveal, creature detonating outward explosion

CRITICAL — Camera Motion rule: Every shot MUST have violent, kinetic camera movement — fast push-ins, whip-pans, rapid cranes. NO slow drifts, NO hovering, NO stationary camera.

CRITICAL — Character Name Ban:
  NEVER use any character name, franchise name (Pokémon, Nintendo), or game title inside a Veo prompt.
  Describe the creature ONLY by these 5 pillars:
    1. Primary color: (e.g. "vibrant-orange", "electric-yellow", "deep-sea blue")
    2. Animal base & Body: (e.g. "bipedal dinosaur-like creature", "sturdy quadrupedal turtle", "agile rodent-like drone")
    3. Signature features: (e.g. "blunt rounded snout and large blue eyes", "long pointed antennas", "thick heavy hexagonal shell")
    4. Style: ALWAYS include "high-end 3D animation style", "smooth stylized surfaces", and "expressive large eyes" to avoid realistic animal results.
    5. Action state (REQUIRED): the creature MUST be mid-action — NEVER standing still. Use: "charging a blinding energy orb", "unleashing a wide plasma beam from its jaws", "launching explosive neon projectiles", "spin-dashing through energy shockwaves", "detonating a full-body radial energy burst"

  Example instead of "Charmander" -> "Fast ramping low-angle push-in, ultra-wide 14mm, a stylized vibrant-orange bipedal dinosaur-like creature, high-end 3D animation style, smooth skin, large expressive eyes, blunt rounded snout, tail flame blazing white-hot, leaping forward and launching a spiraling fire vortex at the camera, ember particles exploding outward, shockwave ring expanding across wet neon asphalt."

Action FX vocabulary (use different ones per shot — creates visual variety):
  charging energy orb between paws, crackling with lightning arcs |
  firing a wide sustained plasma beam from mouth, melting the ground ahead |
  launching rapid neon projectile volley in arc formation |
  spin-dashing through the air leaving a spiral energy trail |
  detonating a radial full-body shockwave that cracks the ground |
  slamming two energy-charged fists into the ground creating a crater shockwave |
  rising into the air surrounded by rotating energy ring satellites |
  unleashing a cascading lightning storm from shoulder cannon |
  blasting twin parallel beam cannons from open palms |
  erupting in a core-overload explosion of plasma wings and fire
"""

_POKEMON_BRAIN_PROMPT = (
    'You are a Veo video director creating a "Cyberpunk Evolution" YouTube Shorts video.\n\n'
    "Creature concept: __POKEMON__\n"
    "Evolution stages: __EVOLUTION_CHAIN__\n\n"
    "TASK: Generate 5 cinematic shots. You MUST describe the creatures visually. NEVER mention their names in the 'prompt' field.\n\n"
    "__VEO_GUIDE__\n"
    "Shot structure:\n"
    "- Shot 1 (Intro): Camera: FAST RAMPING LOW-ANGLE PUSH-IN, ultra-wide 14mm fisheye — camera rushes toward the creature's glowing face from ground level. Original tiny unarmored form, describe by color+animal+style ONLY. The creature CHARGING UP a blinding energy orb, body crackling with lightning arcs, eyes blazing, power aura violently expanding, dramatic wind-blur, neon megacity at night. duration=4, landmark_name=''\n"
    "- Shot 2 (Light Armor / __EVO_1__): Camera: RAPID ORBITING 180° TRACKING SHOT circling the creature mid-combat, 35mm — camera sweeps aggressively around. Creature in light cyberpunk armor (neon energy line engravings, chrome shoulder plates), FIRING a rapid neon projectile volley in arc formation, each projectile blazing trail, shockwave ring slamming into ground, underground cyberpunk forge, molten sparks. landmark_name=__EVO_1__. duration=4\n"
    "- Shot 3 (Medium Armor / __EVO_2__): Camera: EXTREME LOW-ANGLE UPWARD SWEEP, wide-angle distortion — starts at ground level and swings upward as explosion erupts. Creature in medium battle armor (reinforced chest, energy-conduit gauntlets), SLAMMING both charged fists into the ground, cratering impact, radial shockwave of debris and plasma rippling outward, drone spotlights, rooftop arena. landmark_name=__EVO_2__. duration=4\n"
    "- Shot 4 (Heavy Armor / __EVO_3__ Battle Mode): Camera: FAST LATERAL WHIP-PAN TRACKING SHOT, anamorphic 35mm — camera races alongside the creature. Full heavy exosuit (integrated shoulder plasma cannon, full plating), FIRING a massive sustained beam cannon blast, beam tearing a glowing trench through battlefield rubble, screen-filling energy trail, heat shimmer, ember clouds, post-apocalyptic ruins. landmark_name=__EVO_3__ Battle Mode. duration=4\n"
    "- Shot 5 (Titan / Ultimate Form): Camera: EXTREME LOW-ANGLE CRANE BOOM UPWARD, anamorphic wide lens — camera starts at ground, booms up as titan rises. Colossal city-scale mecha titan, CORE OVERLOADING — reactor chest splitting open into plasma wings, twin beam arrays firing skyward from open palms, cascading lightning erupting from shoulder joints, entire body radiating blinding white-gold energy, towering over neon skyline. landmark_name=Ultimate Form. duration=4\n\n"
    "Return ONLY valid JSON:\n"
    '{\n'
    '  "intro_phrase": "<punchy 6-8 word hype hook in __LANG__>",\n'
    '  "visuals": [\n'
    '    {"prompt": "<shot 1 — charging up, power aura, intense close-up — NO name>", "duration": 4, "landmark_name": ""},\n'
    '    {"prompt": "<shot 2 — light armor, firing projectiles or spin-dash — NO name>", "duration": 4, "landmark_name": "<__EVO_1__ in __LANG__>"},\n'
    '    {"prompt": "<shot 3 — medium armor, ground-slam shockwave — NO name>", "duration": 4, "landmark_name": "<__EVO_2__ in __LANG__>"},\n'
    '    {"prompt": "<shot 4 — heavy armor, beam cannon firing — NO name>", "duration": 4, "landmark_name": "<__EVO_3__ in __LANG__>"},\n'
    '    {"prompt": "<shot 5 — titan, full core overload, energy wings, beam arrays — NO name>", "duration": 4, "landmark_name": "Ultimate Form"}\n'
    '  ],\n'
    '  "vibe": "Cyberpunk Phonk"\n'
    '}\n\n'
    "HARD RULES:\n"
    "- ZERO Pokemon/franchise names in any 'prompt' value. Describe ONLY by color + animal body + features + action.\n"
    "- Every creature MUST be in active motion or firing a skill — NO idle standing poses.\n"
    "- Each shot MUST use a DIFFERENT camera move, a DIFFERENT energy FX action, and a DIFFERENT atmosphere.\n"
    "- Each prompt MUST include particle/energy FX details: shockwave rings, beam trails, ember particles, plasma bursts, crackling arcs.\n"
    "- landmark_name and intro_phrase MUST use the names in __LANG__ (this is safe).\n"
    "- No people, no faces, no text in scene."
)

def _fallback_pokemon(pokemon_name: str, evolution_chain: list[str]) -> dict:
    """Fallback: no Pokemon names in Veo prompts — pure visual description with active combat actions."""
    evo1 = evolution_chain[0]
    evo2 = evolution_chain[min(1, len(evolution_chain)-1)]
    evo3 = evolution_chain[-1]

    return {
        "intro_phrase": f"{pokemon_name} tiến hóa Cyberpunk cực đỉnh!",
        "visuals": [
            {
                "prompt": (
                    "Fast ramping low-angle push-in, ultra-wide 14mm fisheye, camera rushing toward creature's glowing face from ground level, "
                    "a tiny stylized vibrant-orange bipedal dinosaur-like creature, "
                    "high-end 3D animation style, smooth skin, large expressive glowing eyes, blunt rounded snout, "
                    "tail tip blazing white-hot, CHARGING UP a blinding spherical energy orb between its small claws, "
                    "body crackling with electric arcs, intense power aura violently expanding outward, wind-blur, "
                    "neon rain-soaked cyberpunk megacity at night, reflective wet asphalt, "
                    "cinematic, hyperrealistic, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": "",
            },
            {
                "prompt": (
                    "Rapid orbiting 180-degree tracking shot circling the creature mid-combat, 35mm, "
                    "a stylized vibrant-orange bipedal dinosaur-like creature "
                    "in light cyberpunk armor — neon energy line engravings, minimal chrome shoulder plates, "
                    "FIRING a rapid volley of neon-orange projectiles in arc formation, "
                    "each projectile leaving a blazing trail, shockwave ring slamming into ground, "
                    "underground cyberpunk forge, molten sparks flying, dramatic rim lighting, "
                    "cinematic, hyperrealistic, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": evo1,
            },
            {
                "prompt": (
                    "Extreme low-angle upward sweep, wide-angle distortion, camera starting at ground level swinging upward as explosion erupts, "
                    "a larger stylized orange creature "
                    "in medium battle armor — reinforced chest plate, glowing energy-conduit gauntlets, "
                    "SLAMMING both charged fists into the rooftop, ground cracking and cratering, "
                    "explosive radial shockwave of debris and orange plasma rippling outward from impact point, "
                    "rooftop arena, drone spotlights, neon city bloom, "
                    "cinematic, hyperrealistic, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": evo2,
            },
            {
                "prompt": (
                    "Fast lateral whip-pan tracking shot, anamorphic 35mm, camera racing alongside creature, "
                    "a powerful stylized orange creature "
                    "in full heavy exosuit armor — integrated shoulder plasma cannon, plated limbs, "
                    "FIRING a massive sustained plasma beam cannon blast from shoulder-mount arm, "
                    "beam tearing a glowing trench through smoldering battlefield rubble, "
                    "screen-filling energy trail, heat distortion shimmer, ember clouds, "
                    "post-apocalyptic cyberpunk ruins, red-orange inferno light, "
                    "cinematic, hyperrealistic, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": f"{evo3} Battle Mode",
            },
            {
                "prompt": (
                    "Extreme low-angle crane boom upward, anamorphic wide lens, camera booming up from ground as titan rises, "
                    "a colossal city-scale mecha titan inspired by a bipedal dinosaur silhouette, "
                    "CORE OVERLOADING — reactor chest splitting open into brilliant plasma wings, "
                    "twin parallel beam arrays firing skyward from open palms, "
                    "cascading lightning storm erupting from shoulder joints, "
                    "entire body radiating blinding white-gold energy, "
                    "towering over neon cyberpunk skyline, holographic ads dissolving in the energy surge, "
                    "cinematic, hyperrealistic, 8K, no text, no watermark"
                ),
                "duration": 4,
                "landmark_name": "Ultimate Form",
            },
        ],
        "vibe": "Cyberpunk Phonk",
    }

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
                    json=_build_payload(attempt_prompt, use_schema=use_schema, response_schema=_TIMELINE_BRAIN_RESPONSE_SCHEMA),
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


async def generate_pokemon_brain(
    pokemon_name: str,
    evolution_chain: list[str],
    language: str = "en",
) -> dict:
    chain = evolution_chain + [f"{evolution_chain[-1]} Battle"] * max(0, 3 - len(evolution_chain))
    evo1 = chain[0]
    evo2 = chain[min(1, len(chain) - 1)]
    evo3 = chain[min(2, len(chain) - 1)]

    prompt_text = (
        _POKEMON_BRAIN_PROMPT
        .replace("__POKEMON__", pokemon_name)
        .replace("__EVOLUTION_CHAIN__", ", ".join(evolution_chain))
        .replace("__EVO_1__", evo1)
        .replace("__EVO_2__", evo2)
        .replace("__EVO_3__", evo3)
        .replace("__LANG__", language)
        .replace("__VEO_GUIDE__", _POKEMON_VEO_FORMULA)
    )
    logger.info(
        "Pokémon brain: pokemon=%r, chain=%s, model=%s",
        pokemon_name,
        evolution_chain,
        settings.gemini_model,
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
                    logger.warning("Gemini rejected responseSchema (pokemon); retrying without schema")
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
                logger.warning("Pokémon brain JSON parse failed (attempt %d/3): %s", attempt, exc)

        if last_error is not None:
            logger.error(
                "Pokémon brain parse failed after retries, using fallback: %s | raw_text_preview=%r",
                last_error,
                best_raw_text[:500],
            )
            salvaged = _salvage_brain_from_text(best_raw_text, pokemon_name)
            if salvaged and salvaged.get("visuals"):
                return salvaged
            return _fallback_pokemon(pokemon_name, evolution_chain)

    raise RuntimeError("Pokémon brain request loop exited unexpectedly")

