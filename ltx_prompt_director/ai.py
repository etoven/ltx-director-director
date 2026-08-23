from __future__ import annotations

import json
import re

import requests

from .media import data_url
from .models import Segment


GEMINI_MODELS = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]


class AIResponseFormatError(ValueError):
    """The provider returned text that does not satisfy the response contract."""


def build_prompts(segments: list[Segment], provider: str, model: str, api_key: str, intent: str, sfx: bool, spoken_dialog: bool, hdr: bool, reduce_music: bool, timeout: int = 400) -> dict:
    images = [{"name": item.name, "role": item.role, "image": data_url(item.preview_path, max_edge=384)} for item in segments]
    rules = _rules(len(images), intent, sfx, spoken_dialog, hdr, reduce_music)
    if provider == "openai":
        result = _openai(images, api_key, rules, timeout, sfx, spoken_dialog)
    else:
        result = _gemini(images, api_key, model, rules, timeout, sfx, spoken_dialog)
    requested_duration = _requested_single_frame_duration(intent) if len(images) == 1 else None
    if requested_duration is not None:
        result["segments"][0]["duration"] = requested_duration
    return result


def _requested_single_frame_duration(intent: str) -> float | None:
    """Read an explicit one-frame duration so provider drift cannot override user intent."""
    text = str(intent or "")
    patterns = (
        r"\b(?:total|scene|sequence|segment|frame)(?:\s+(?:duration|length))?\s*(?:of|is|:|=)?\s*(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s)\b",
        r"\b(\d+(?:\.\d+)?)\s*[- ]?(?:seconds?|secs?)\s+(?:scene|sequence|segment|frame)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return max(1.0, min(60.0, round(float(match.group(1)) * 2) / 2))
    # In a one-frame sequence, a lone seconds value is unambiguously the requested
    # segment length even when phrased conversationally (for example, "make it 20s").
    seconds = re.findall(r"\b(\d+(?:\.\d+)?)\s*[- ]?(?:seconds?|secs?|s)\b", text, re.I)
    if len(seconds) == 1:
        return max(1.0, min(60.0, round(float(seconds[0]) * 2) / 2))
    return None


def _rules(count: int, intent: str, sfx: bool, spoken_dialog: bool, hdr: bool, reduce_music: bool) -> str:
    audio = (
        ("SFX IS ON AND IS A HARD OUTPUT REQUIREMENT: Every segment prompt must include a concise clause beginning exactly `SFX:` with synchronized sound grounded in visible motion and materials. " if sfx else "Do not include SFX, Foley or ambience directions. ")
        + ("SPOKEN DIALOG IS ON AND IS A HARD OUTPUT REQUIREMENT: Every segment prompt must include a concise clause beginning exactly `Spoken Dialog:` describing vocal intent, delivery or visible vocalization. Do not invent quoted wording unless User intent supplies the exact words. " if spoken_dialog else "Do not include spoken dialog, speech, breathing, cries or other vocal directions. ")
    )
    quality_rule = "Begin globalPrompt with exactly: (4K, HDR, Realistic). " if hdr else "Do not add a parenthesized quality header to globalPrompt. "
    if reduce_music:
        position = "Immediately after the quality header" if hdr else "At the beginning of globalPrompt"
        sound_rule = f"{position}, include a setting-specific line in exactly this format: [SOUND]: Ambient <describe the room or environment ambience only>. This ambience line is required and must not request music. "
    else:
        sound_rule = "Do not add a [SOUND] ambience header unless the user explicitly requests one. "
    global_format = quality_rule + sound_rule
    if count == 1:
        frame_planning_rule = (
            "SINGLE-FRAME MODE: Treat the supplied frame as a strong visual anchor, not as a complete motion description. "
            "If it is labeled START FRAME, begin exactly from it and guide coherent action forward from that starting state; do not invent action before it. "
            "If it is labeled END FRAME, guide plausible preceding action toward that target state, resolve exactly into it and stop there; do not continue beyond it. "
            "Use User intent as the primary source of desired action, with conservative supporting motion inferred from visible pose, expression, environment and physical cause-and-effect. "
            "Describe time-based motion across the segment rather than merely inventorying the still image. Do not require or refer to a missing adjacent frame. "
            "If User intent specifies a total scene or segment duration, return that duration exactly in the single segment, from 1.0 up to 60.0 seconds. "
            "The JSON must still use a segments array containing exactly one object; never return a singular segment object or a bare segment."
        )
        duration_rule = "Normally assign 1.0-12.0 seconds according to motion complexity; an explicit User-intent duration overrides that recommendation up to 60.0 seconds."
    else:
        frame_planning_rule = "Infer transitions only from adjacent frames."
        duration_rule = "Assign 1.0-12.0 seconds in 0.5-second increments according to motion complexity."
    return f"""EXPECTED SEGMENT COUNT: {count}

You are LTXDirector, an expert prompt planner for LTX Video 2.3. Analyze all {count} supplied frames in order.
Return exactly one segment per frame; never add, remove, merge or reorder. A start frame is the exact opening frame and an end frame is the exact target.
Write production-ready natural-language prompts describing visible subject, action, expression, physical change, secondary motion, environment and camera behavior. {frame_planning_rule} Preserve identity, outfit, scene, lighting, angle, composition, aspect ratio and style. Use a stationary camera unless the frames clearly demand otherwise. Require gradual motion, overlapping progression, direct continuity and no cross-fade. Do not invent visual facts.
Treat the creative guidance above as defaults. When User intent explicitly requests something different, follow the user's instruction. User intent overrides conflicting creative defaults, but not the required segment count, frame order, start/end-frame meaning or strict JSON schema.
{duration_rule} Use 0.5-second increments. {audio}
The globalPrompt contains persistent subject, scene, camera, lighting, style, continuity and negative constraints only. {global_format}
User intent: {intent or 'Infer motion only from the ordered frames.'}
Return strict JSON: {{"segments":[{{"duration":5,"prompt":"..."}}],"globalPrompt":"..."}}"""


def _gemini(images: list[dict], key: str, model: str, rules: str, timeout: int, sfx: bool, spoken_dialog: bool) -> dict:
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
    raw = _gemini_response_text(data)
    return _validate(raw, len(images), sfx, spoken_dialog)


def _gemini_response_text(data: object) -> str:
    """Extract Gemini text without leaking response-shape KeyErrors into the UI."""
    if not isinstance(data, dict):
        raise AIResponseFormatError("Gemini returned an invalid response envelope. Magic Build will retry.")
    feedback = data.get("promptFeedback")
    block_reason = feedback.get("blockReason") if isinstance(feedback, dict) else None
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        detail = f" ({block_reason})" if block_reason else ""
        raise AIResponseFormatError(f"Gemini returned no response candidate{detail}. Magic Build will retry.")
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        raise AIResponseFormatError("Gemini returned an invalid response candidate. Magic Build will retry.")
    content = candidate.get("content")
    response_parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(response_parts, list) or not response_parts:
        finish_reason = candidate.get("finishReason")
        detail = f" ({finish_reason})" if finish_reason else ""
        raise AIResponseFormatError(f"Gemini returned no generated text{detail}. Magic Build will retry.")
    raw = "".join(part.get("text", "") for part in response_parts if isinstance(part, dict))
    if not raw.strip():
        finish_reason = candidate.get("finishReason")
        detail = f" ({finish_reason})" if finish_reason else ""
        raise AIResponseFormatError(f"Gemini returned empty generated text{detail}. Magic Build will retry.")
    return raw


def _openai(images: list[dict], key: str, rules: str, timeout: int, sfx: bool, spoken_dialog: bool) -> dict:
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
    return _validate(raw, len(images), sfx, spoken_dialog)


def _validate(raw: str, expected: int, require_sfx: bool = False, require_spoken_dialog: bool = False) -> dict:
    result = _parse_json(raw)
    if expected == 1:
        result = _normalize_single_frame_result(result)
    if not isinstance(result, dict):
        raise AIResponseFormatError("The AI response was not a JSON object. Magic Build will retry.")
    if not isinstance(result.get("segments"), list) or len(result["segments"]) != expected:
        raise AIResponseFormatError(f"The AI returned the wrong segment count; expected {expected}. Magic Build will retry.")
    normalized_segments = []
    for segment in result["segments"]:
        if not isinstance(segment, dict):
            raise AIResponseFormatError("The AI returned an invalid segment object. Magic Build will retry.")
        prompt = segment.get("prompt") or segment.get("segmentPrompt") or segment.get("segment_prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise AIResponseFormatError("The AI returned a segment without a prompt. Magic Build will retry.")
        prompt_lower = prompt.casefold()
        if require_sfx and "sfx:" not in prompt_lower:
            raise AIResponseFormatError("The AI omitted required SFX direction. Magic Build will retry.")
        if require_spoken_dialog and "spoken dialog:" not in prompt_lower:
            raise AIResponseFormatError("The AI omitted required Spoken Dialog direction. Magic Build will retry.")
        duration_value = segment.get("duration", 5)
        match = re.search(r"\d+(?:\.\d+)?", str(duration_value))
        if not match:
            raise AIResponseFormatError("The AI returned an invalid segment duration. Magic Build will retry.")
        maximum_duration = 60.0 if expected == 1 else 12.0
        duration = max(1.0, min(maximum_duration, round(float(match.group()) * 2) / 2))
        normalized_segments.append({"duration": duration, "prompt": prompt.strip()})
    global_prompt = result.get("globalPrompt") or result.get("global_prompt") or result.get("global")
    if not isinstance(global_prompt, str) or not global_prompt.strip():
        raise AIResponseFormatError("The AI returned no global prompt. Magic Build will retry.")
    return {"segments": normalized_segments, "globalPrompt": global_prompt.strip()}


def _parse_json(raw: str) -> object:
    """Parse JSON while tolerating presentation noise commonly emitted by LLMs."""
    cleaned = str(raw or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    candidates = [cleaned]
    object_start, object_end = cleaned.find("{"), cleaned.rfind("}")
    if 0 <= object_start < object_end:
        candidates.append(cleaned[object_start:object_end + 1])
    array_start, array_end = cleaned.find("["), cleaned.rfind("]")
    if 0 <= array_start < array_end:
        candidates.append(cleaned[array_start:array_end + 1])
    for candidate in candidates:
        for value in (candidate, re.sub(r",\s*([}\]])", r"\1", candidate)):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                continue
    raise AIResponseFormatError("The AI returned malformed JSON. Magic Build will retry.")


def _normalize_single_frame_result(result: object) -> object:
    """Normalize common one-frame response shapes without weakening count validation."""
    if isinstance(result, list):
        if len(result) != 1 or not isinstance(result[0], dict):
            return result
        entry = result[0]
        if "segments" in entry:
            return entry
        return {"segments": [entry], "globalPrompt": entry.get("globalPrompt") or entry.get("global_prompt") or ""}
    if not isinstance(result, dict):
        return result
    normalized = dict(result)
    segments = normalized.get("segments")
    if isinstance(segments, dict):
        normalized["segments"] = [segments]
    elif "segments" not in normalized:
        singular = normalized.get("segment")
        if isinstance(singular, dict):
            normalized["segments"] = [singular]
        elif any(key in normalized for key in ("prompt", "segmentPrompt", "segment_prompt")):
            normalized["segments"] = [{key: normalized[key] for key in ("duration", "prompt", "segmentPrompt", "segment_prompt") if key in normalized}]
    return normalized


def retryable_connection_error(error: Exception) -> bool:
    """Retry transient provider failures and malformed model responses."""
    if isinstance(error, AIResponseFormatError):
        return True
    if isinstance(error, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(error, requests.HTTPError):
        status = error.response.status_code if error.response is not None else 0
        return status == 429 or status >= 500
    return isinstance(error, requests.RequestException)
