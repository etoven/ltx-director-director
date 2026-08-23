from __future__ import annotations

import json
import re

import requests

from .media import data_url
from .models import Segment


GEMINI_MODELS = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]


def build_prompts(segments: list[Segment], provider: str, model: str, api_key: str, intent: str, sfx: bool, spoken_dialog: bool, hdr: bool, reduce_music: bool, timeout: int = 400) -> dict:
    images = [{"name": item.name, "role": item.role, "image": data_url(item.preview_path, max_edge=384)} for item in segments]
    rules = _rules(len(images), intent, sfx, spoken_dialog, hdr, reduce_music)
    if provider == "openai":
        return _openai(images, api_key, rules, timeout)
    return _gemini(images, api_key, model, rules, timeout)


def _rules(count: int, intent: str, sfx: bool, spoken_dialog: bool, hdr: bool, reduce_music: bool) -> str:
    audio = (
        ("Include concise synchronized SFX grounded in visible motion, materials and ambience. " if sfx else "Do not include SFX, Foley or ambience directions. ")
        + ("Include concise Spoken Dialog direction only when visible action or user intent supports it; never invent exact dialogue wording. " if spoken_dialog else "Do not include spoken dialog, speech, breathing, cries or other vocal directions. ")
    )
    quality_rule = "Begin globalPrompt with exactly: (4K, HDR, Realistic). " if hdr else "Do not add a parenthesized quality header to globalPrompt. "
    if reduce_music:
        position = "Immediately after the quality header" if hdr else "At the beginning of globalPrompt"
        sound_rule = f"{position}, include a setting-specific line in exactly this format: [SOUND]: Ambient <describe the room or environment ambience only>. This ambience line is required and must not request music. "
    else:
        sound_rule = "Do not add a [SOUND] ambience header unless the user explicitly requests one. "
    global_format = quality_rule + sound_rule
    return f"""EXPECTED SEGMENT COUNT: {count}

You are LTXDirector, an expert prompt planner for LTX Video 2.3. Analyze all {count} supplied frames in order.
Return exactly one segment per frame; never add, remove, merge or reorder. A start frame is the exact opening frame and an end frame is the exact target.
Write production-ready natural-language prompts describing visible subject, action, expression, physical change, secondary motion, environment and camera behavior. Infer transitions only from adjacent frames. Preserve identity, outfit, scene, lighting, angle, composition, aspect ratio and style. Use a stationary camera unless the frames clearly demand otherwise. Require gradual motion, overlapping progression, direct continuity and no cross-fade. Do not invent visual facts.
Assign 1.0-12.0 seconds in 0.5-second increments according to motion complexity. {audio}
The globalPrompt contains persistent subject, scene, camera, lighting, style, continuity and negative constraints only. {global_format}
User intent: {intent or 'Infer motion only from the ordered frames.'}
Return strict JSON: {{"segments":[{{"duration":5,"prompt":"..."}}],"globalPrompt":"..."}}"""


def _gemini(images: list[dict], key: str, model: str, rules: str, timeout: int) -> dict:
    parts: list[dict] = [{"text": rules}]
    for index, item in enumerate(images, 1):
        mime, encoded = re.match(r"^data:([^;]+);base64,(.+)$", item["image"], re.S).groups()
        parts.extend([{"text": f"IMAGE {index} OF {len(images)} — {item['role'].upper()} FRAME — {item['name']}"}, {"inline_data": {"mime_type": mime, "data": encoded}}])
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key, "content-type": "application/json"},
        json={"contents": [{"role": "user", "parts": parts}], "generationConfig": {"temperature": 0.25, "responseMimeType": "application/json"}},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    raw = "".join(part.get("text", "") for part in data["candidates"][0]["content"]["parts"])
    return _validate(raw, len(images))


def _openai(images: list[dict], key: str, rules: str, timeout: int) -> dict:
    content: list[dict] = [{"type": "input_text", "text": rules}]
    for index, item in enumerate(images, 1):
        content.extend([{"type": "input_text", "text": f"IMAGE {index} OF {len(images)} — {item['role'].upper()} FRAME — {item['name']}"}, {"type": "input_image", "image_url": item["image"], "detail": "high"}])
    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
        json={"model": "gpt-5.4-mini", "input": [{"role": "user", "content": content}], "text": {"format": {"type": "json_object"}}},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    raw = data.get("output_text") or "".join(c.get("text", "") for item in data.get("output", []) for c in item.get("content", []) if c.get("type") == "output_text")
    return _validate(raw, len(images))


def _validate(raw: str, expected: int) -> dict:
    result = json.loads(raw)
    if not isinstance(result.get("segments"), list) or len(result["segments"]) != expected:
        raise ValueError("The AI returned the wrong segment count. Run Magic Build again.")
    return result


def retryable_connection_error(error: Exception) -> bool:
    """Retry transient network, throttling, and upstream service failures only."""
    if isinstance(error, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(error, requests.HTTPError):
        status = error.response.status_code if error.response is not None else 0
        return status == 429 or status >= 500
    return isinstance(error, requests.RequestException)
