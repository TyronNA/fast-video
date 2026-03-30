import json
import re
from json import JSONDecodeError

import httpx

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
    "required": ["script", "visuals", "vibe", "bg_music_suggestion"],
    "properties": {
        "script": {"type": "STRING"},
        "visuals": {
            "type": "ARRAY",
            "minItems": 4,
            "maxItems": 5,
            "items": {
                "type": "OBJECT",
                "required": ["prompt", "duration"],
                "properties": {
                    "prompt": {"type": "STRING"},
                    "duration": {"type": "INTEGER", "enum": [4, 6, 8]},
                },
            },
        },
        "vibe": {"type": "STRING"},
        "bg_music_suggestion": {"type": "STRING"},
    },
}


def _vertex_host(location: str) -> str:
    # Global uses the shared endpoint host, regional locations use {location}-aiplatform.
    if location == "global":
        return "aiplatform.googleapis.com"
    return f"{location}-aiplatform.googleapis.com"

_BRAIN_PROMPT = """You are an expert short-form video content strategist specialising in \
"What If" futuristic and alternative-history scenarios.

Given a topic for an 18-20 second YouTube Shorts video, generate content in {language} language.

Return ONLY a valid JSON object (no markdown fences, no explanation) matching this exact schema:
{{
  "script": "<3-sentence voiceover: Hook sentence. Content sentence. Call-to-action sentence.>",
  "visuals": [
    {{
      "prompt": "<Shot 1 — Veo prompt: sweeping aerial establisher, cinematic wide shot, hyper-realistic, 8k>",
      "duration": 4
    }},
    {{
      "prompt": "<Shot 2 — Veo prompt: iconic cultural landmark reimagined in futuristic style, cinematic, 8k>",
      "duration": 4
    }},
    {{
      "prompt": "<Shot 3 — Veo prompt: blend of historical heritage + advanced technology, cinematic, 8k>",
      "duration": 4
    }},
    {{
      "prompt": "<Shot 4 — Veo prompt: bustling futuristic street life or marketplace, cinematic, 8k>",
      "duration": 4
    }},
    {{
      "prompt": "<Shot 5 (optional) — Veo prompt: dramatic reveal / hero shot, cinematic, 8k>",
      "duration": 4
    }}
  ],
  "vibe": "<Music genre suggestion, e.g. Cyberpunk Phonk / Epic Orchestral / Lo-fi Chill>",
  "bg_music_suggestion": "<Short filename or style description>"
}}

Veo prompt rules (strictly follow):
- Always include: cinematic, hyper-realistic, 8k
- Use: wide shot, drone view OR establishing shot (avoids face distortion artefacts)
- Include: lighting description, mood, gentle camera movement (slow pan / gentle dolly)
- Avoid: close-up of faces, text or numbers in scene, watermarks
- Duration MUST be 4 for every shot (never use other values)
- Generate exactly 4 or 5 shots

Topic: {topic}
Language for script: {language}"""


def _build_payload(prompt_text: str, use_schema: bool = True) -> dict:
    generation_config = {
        "temperature": 0.5,
        "maxOutputTokens": 1536,
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


def _fallback_brain(topic: str, language: str) -> dict:
    if language == "vi":
        script = (
            f"Điều gì xảy ra nếu {topic}? "
            "Tương lai thay đổi mọi thứ quanh chúng ta theo cách không ngờ tới. "
            "Bạn nghĩ kịch bản nào sẽ xảy ra tiếp theo, bình luận ngay nhé."
        )
    else:
        script = (
            f"What if {topic}? "
            "The future could reshape everything around us in unexpected ways. "
            "What do you think happens next? Drop your take in the comments."
        )
    return {
        "script": script,
        "visuals": [
            {
                "prompt": (
                    f"What-if scenario about {topic}, sweeping aerial drone view, "
                    "hyper-realistic, 8k, atmospheric lighting, slow pan, no text, no watermark"
                ),
                "duration": 4,
            },
            {
                "prompt": (
                    f"Iconic cultural landmark in {topic} reimagined as futuristic structure, "
                    "cinematic wide shot, hyper-realistic, 8k, dramatic sky, gentle dolly, no text"
                ),
                "duration": 4,
            },
            {
                "prompt": (
                    f"Heritage bridge or monument in {topic} fused with advanced technology, "
                    "cinematic establishing shot, hyper-realistic, 8k, rim light, no text"
                ),
                "duration": 4,
            },
            {
                "prompt": (
                    f"Futuristic bustling marketplace or street scene in {topic}, "
                    "cinematic, 8k, neon lighting, holographic stalls, slow pan, no text"
                ),
                "duration": 4,
            },
        ],
        "vibe": "Cyberpunk Phonk",
        "bg_music_suggestion": "Phonk-Phonk-pr.mp3",
    }


def _salvage_brain_from_text(raw_text: str, topic: str, language: str) -> dict:
    text = _clean_raw_text(raw_text)
    if not text:
        return _fallback_brain(topic, language)

    script_match = re.search(r'"script"\s*:\s*"([\s\S]*?)"\s*,\s*"visuals"', text)
    prompts = re.findall(r'"prompt"\s*:\s*"([\s\S]*?)"\s*(?:,|})', text)
    durations = re.findall(r'"duration"\s*:\s*(\d+)', text)
    vibe_match = re.search(r'"vibe"\s*:\s*"([\s\S]*?)"\s*(?:,|})', text)
    music_match = re.search(r'"bg_music_suggestion"\s*:\s*"([\s\S]*?)"\s*(?:,|})', text)

    result = _fallback_brain(topic, language)

    if script_match:
        result["script"] = _cleanup_json_string(script_match.group(1))
    if vibe_match:
        result["vibe"] = _cleanup_json_string(vibe_match.group(1))
    if music_match:
        result["bg_music_suggestion"] = _cleanup_json_string(music_match.group(1))

    if prompts:
        visuals = []
        for i in range(min(5, len(prompts))):
            visuals.append(
                {
                    "prompt": _cleanup_json_string(prompts[i]),
                    "duration": _normalize_duration(durations[i] if i < len(durations) else 4),
                }
            )
        while len(visuals) < 4:
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

    if "script" not in result or "visuals" not in result:
        raise ValueError("Gemini response missing required keys: script/visuals")

    if not isinstance(result["visuals"], list) or not result["visuals"]:
        raise ValueError("Gemini visuals must be a non-empty list")

    for visual in result["visuals"]:
        visual["duration"] = _normalize_duration(visual.get("duration"))

    logger.info("Gemini response: vibe=%s, script_len=%d", result.get("vibe"), len(result.get("script", "")))
    return result


async def generate_brain(topic: str, language: str = "vi") -> dict:
    prompt_text = _BRAIN_PROMPT.format(topic=topic, language=language)
    logger.info(
        "Gemini: using Vertex AI, model=%s, location=%s",
        settings.gemini_model,
        settings.gemini_location,
    )

    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2 import service_account as sa

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
        last_raw_text = ""
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
            last_raw_text = _extract_raw_text(body)

            try:
                return _parse_response(body)
            except (JSONDecodeError, ValueError, KeyError, IndexError) as exc:
                last_error = exc
                logger.warning("Gemini JSON parse failed (attempt %d/3): %s", attempt, exc)

        if last_error is not None:
            logger.error("Gemini parse failed after retries, salvaging response: %s", last_error)
            return _salvage_brain_from_text(last_raw_text, topic, language)

    raise RuntimeError("Gemini request loop exited unexpectedly")

