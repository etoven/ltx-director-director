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
    images = [_segment_input(item) for item in segments]
    rules = _rules(len(images), intent, sfx, spoken_dialog, hdr, reduce_music, sum(item.kind == "text" for item in segments))
    if provider == "openai":
        return _openai(images, api_key, rules, timeout, sfx, spoken_dialog)
    return _gemini(images, api_key, model, rules, timeout, sfx, spoken_dialog)


def refine_timing(segments: list[Segment], selected_index: int, provider: str, model: str, api_key: str, intent: str, requested_total: float, timeout: int = 400) -> dict:
    """Retiming pass that may change only the selected segment's duration."""
    if not 0 <= selected_index < len(segments):
        raise ValueError("Select a segment to refine its timing.")
    maximum = 60.0 if len(segments) == 1 else 12.0
    untouched_total = sum(segment.duration for index, segment in enumerate(segments) if index != selected_index)
    available = min(maximum, MAX_SEQUENCE_SECONDS - untouched_total)
    required = None
    if requested_total > 0:
        required = round((requested_total - untouched_total) * 2) / 2
        if required < 1.0 or required > available:
            raise ValueError(
                f"The requested {requested_total:.1f}s sequence cannot be reached by changing only this segment. "
                f"It would need to be {required:.1f}s, but its available range is 1.0–{available:.1f}s."
            )
    images = _refinement_images(segments, selected_index)
    rules = _timing_rules(segments, selected_index, intent, available, required)
    raw = _provider_raw(images, provider, model, api_key, rules, timeout)
    result = _parse_json(raw)
    if not isinstance(result, dict):
        raise AIResponseFormatError("The AI returned an invalid timing response. The operation will retry.")
    duration = _strict_duration(result.get("duration"), available)
    if required is not None and duration != required:
        raise AIResponseFormatError(f"The AI ignored the required {required:.1f}s selected-segment duration. The operation will retry.")
    return {"duration": duration}


def refine_segment_prompt(segments: list[Segment], selected_index: int, provider: str, model: str, api_key: str, intent: str, requested_total: float, timeout: int = 400) -> dict:
    """Refine only the selected prompt, with an optional selected-duration change."""
    if not 0 <= selected_index < len(segments):
        raise ValueError("Select a segment to refine its prompt.")
    selected = segments[selected_index]
    if not selected.prompt.strip():
        raise ValueError("The selected segment needs an existing prompt before it can be refined.")
    maximum = 60.0 if len(segments) == 1 else 12.0
    untouched_total = sum(segment.duration for index, segment in enumerate(segments) if index != selected_index)
    available = min(maximum, MAX_SEQUENCE_SECONDS - untouched_total)
    required = None
    if requested_total > 0:
        required = round((requested_total - untouched_total) * 2) / 2
        if required < 1.0 or required > available:
            raise ValueError(
                f"The requested {requested_total:.1f}s sequence cannot be reached while changing only this segment. "
                f"It would need to be {required:.1f}s, but its available range is 1.0–{available:.1f}s."
            )
    images = _refinement_images(segments, selected_index)
    rules = _prompt_refinement_rules(segments, selected_index, intent, available, required)
    raw = _provider_raw(images, provider, model, api_key, rules, timeout)
    result = _parse_json(raw)
    if not isinstance(result, dict):
        raise AIResponseFormatError("The AI returned an invalid prompt-refinement response. The operation will retry.")
    prompt = result.get("prompt") or result.get("segmentPrompt") or result.get("segment_prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise AIResponseFormatError("The AI returned no refined prompt. The operation will retry.")
    duration = _strict_duration(result.get("duration"), available)
    if required is not None and duration != required:
        raise AIResponseFormatError(f"The AI ignored the required {required:.1f}s selected-segment duration. The operation will retry.")
    return {"prompt": prompt.strip(), "duration": duration}


MAX_SEQUENCE_SECONDS = 60.0


def _segment_input(item: Segment) -> dict:
    value = {"name": item.name, "role": item.role, "kind": item.kind}
    if item.kind != "text" and item.preview_path:
        value["image"] = data_url(item.preview_path, max_edge=384)
    return value


def _refinement_images(segments: list[Segment], selected_index: int) -> list[dict]:
    start = max(0, selected_index - 1)
    end = min(len(segments), selected_index + 2)
    return [
        {
            "name": f"Segment {index + 1} {'SELECTED' if index == selected_index else ('PREVIOUS' if index < selected_index else 'NEXT')} — {item.name}",
            "role": item.role,
            **({"image": data_url(item.preview_path, max_edge=384)} if item.kind != "text" and item.preview_path else {}),
            "kind": item.kind,
        }
        for index, item in enumerate(segments[start:end], start)
    ]


def _segment_context(segments: list[Segment], selected_index: int) -> str:
    records = []
    for index, segment in enumerate(segments):
        relation = "SELECTED" if index == selected_index else ("PREVIOUS" if index == selected_index - 1 else ("NEXT" if index == selected_index + 1 else "SEQUENCE CONTEXT"))
        anchor = "TEXT-ONLY SEGMENT" if segment.kind == "text" else f"{segment.role.upper()} FRAME"
        records.append(
            f"Segment {index + 1} [{relation}; {anchor}; current duration {segment.duration:.1f}s]\n"
            f"Existing prompt (immutable unless SELECTED prompt refinement): {segment.prompt or '[empty]'}"
        )
    return "\n\n".join(records)


def _timing_rules(segments: list[Segment], selected_index: int, intent: str, available: float, required: float | None) -> str:
    duration_instruction = (
        f"Return exactly {required:.1f} seconds because this is the only duration that satisfies the requested total sequence length."
        if required is not None else
        f"Choose 1.0–{available:.1f} seconds in 0.5-second increments."
    )
    return f"""You are performing a TIMING-ONLY refinement for LTX Video 2.3.
Analyze the complete ordered segment plan below so the selected segment still fits the sequence. Use the immediately previous and next prompts and supplied adjacent frames as the primary motion and continuity context.
Change ONLY the duration of selected segment {selected_index + 1}. Every prompt is immutable: do not rewrite, summarize or return any prompt text. Do not change any other duration.
Estimate the time genuinely needed for the selected prompt's action, physical progression, camera motion, Spoken Dialog and lip sync. Respect its start/end-frame role, surrounding continuity, the 60-second sequence ceiling and the selected segment's available maximum.
{duration_instruction}
Director's intent and planning controls:
{intent.strip() or 'No additional intent supplied.'}

ORDERED EXISTING PLAN:
{_segment_context(segments, selected_index)}

Return strict JSON containing only: {{"duration": 5.0}}"""


def _prompt_refinement_rules(segments: list[Segment], selected_index: int, intent: str, available: float, required: float | None) -> str:
    duration_instruction = (
        f"Return exactly {required:.1f} seconds because this is the only duration that satisfies the requested total sequence length."
        if required is not None else
        f"You may retime the selected segment from 1.0–{available:.1f} seconds in 0.5-second increments when the refined action or dialog needs it."
    )
    return f"""You are refining ONE existing segment prompt for LTX Video 2.3.
Refine ONLY segment {selected_index + 1}. Preserve its visible facts and intended action while improving clarity, temporal progression, physical causality, camera direction, secondary motion, Spoken Dialog delivery and lip-sync direction where present.
Use the immediately previous and next prompts and supplied adjacent frames for continuity, but do not rewrite or return any other prompt. Do not change the global prompt.
{duration_instruction}
Director's intent and planning controls:
{intent.strip() or 'No additional intent supplied.'}

ORDERED EXISTING PLAN:
{_segment_context(segments, selected_index)}

Return strict JSON containing only: {{"prompt":"refined selected prompt","duration":5.0}}"""


def _strict_duration(value: object, maximum: float) -> float:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", str(value or ""))
    if not match:
        raise AIResponseFormatError("The AI returned an invalid refined duration. The operation will retry.")
    duration = float(match.group(1))
    if duration < 1.0 or duration > maximum or abs(duration * 2 - round(duration * 2)) > 1e-6:
        raise AIResponseFormatError(f"The AI returned a duration outside 1.0–{maximum:.1f}s in 0.5s increments. The operation will retry.")
    return round(duration * 2) / 2


def _rules(count: int, intent: str, sfx: bool, spoken_dialog: bool, hdr: bool, reduce_music: bool, text_count: int = 0) -> str:
    if spoken_dialog and count == 1:
        spoken_rule = (
            "SPOKEN DIALOG IS ON: The single segment must include an appropriate clause beginning exactly `Spoken Dialog:` and containing actual audible words spoken by a visible or explicitly described character. "
            "Format it as `Spoken Dialog: \"<brief spoken line>\" spoken in <language> with a natural <specific regional accent>, delivered <tone or performance>.` Preserve exact wording from Director's Intent when supplied; otherwise write a brief natural line consistent with the requested scene. "
            "State the language and accent directly beside the spoken line; never expect LTX Video to infer an accent merely from a nationality mentioned elsewhere. "
            "When the speaker's mouth is visible, explicitly require accurate lip sync, natural phoneme-shaped mouth articulation and facial performance synchronized to the spoken words; do not animate speech on a closed or non-speaking mouth. "
            "Breathing, cries, gasps, growls and other wordless vocalizations are not spoken dialog and belong under SFX. "
        )
    elif spoken_dialog:
        spoken_rule = (
            "SPOKEN DIALOG IS ON: Include spoken dialog in at least one narratively appropriate segment, but do not force it into every segment. Only segments in which a visible character actually speaks should contain a clause beginning exactly `Spoken Dialog:`. "
            "Each such clause must contain actual audible words, formatted as `Spoken Dialog: \"<brief spoken line>\" spoken in <language> with a natural <specific regional accent>, delivered <tone or performance>.` Preserve exact wording from Director's Intent when supplied; otherwise write brief natural dialogue consistent with the requested scene and maintain conversational continuity across speaking segments. "
            "State the language and accent directly beside every spoken line; never expect LTX Video to infer an accent merely from a nationality mentioned elsewhere. "
            "In each speaking segment where the speaker's mouth is visible, explicitly require accurate lip sync, natural phoneme-shaped mouth articulation and facial performance synchronized to the spoken words. Non-speaking segments must not add speech-like mouth movement. "
            "Breathing, cries, gasps, growls and other wordless vocalizations are not spoken dialog and belong under SFX. "
        )
    else:
        spoken_rule = "Do not include spoken dialog or audible speech. Wordless breathing, cries or other vocal sounds may appear only when SFX is enabled. "
    audio = (
        ("SFX IS ON AND IS A HARD OUTPUT REQUIREMENT: Every segment prompt must include a concise clause beginning exactly `SFX:` with synchronized sound grounded in visible motion and materials. " if sfx else "Do not include SFX, Foley or ambience directions. ")
        + spoken_rule
    )
    quality_rule = "Begin globalPrompt with exactly: (4K, HDR, Realistic). " if hdr else "Do not add a parenthesized quality header to globalPrompt. "
    if reduce_music:
        position = "Immediately after the quality header" if hdr else "At the beginning of globalPrompt"
        sound_rule = f"{position}, include a setting-specific line in exactly this format: [SOUND]: Ambient <describe the room or environment ambience only>. This ambience line is required and must not request music. "
    else:
        sound_rule = "Do not add a [SOUND] ambience header unless the user explicitly requests one. "
    global_format = quality_rule + sound_rule
    if count == 1 and text_count == 0:
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
    elif count == 1:
        frame_planning_rule = (
            "TEXT-ONLY MODE: No visual frame is supplied for this segment. Treat Director's Intent and the existing text-segment position as authoritative, "
            "write a complete time-based LTX prompt, and do not claim to see visual facts that were not provided."
        )
        duration_rule = "Assign 1.0-60.0 seconds in 0.5-second increments; obey an explicit requested total duration exactly."
    else:
        frame_planning_rule = "Infer transitions only from adjacent frames."
        duration_rule = "Assign 1.0-12.0 seconds in 0.5-second increments according to motion complexity."
    authoritative_intent = intent.strip() or "Infer motion only from the ordered frames."
    return f"""EXPECTED SEGMENT COUNT: {count}

AUTHORITATIVE DIRECTOR'S INTENT:
{authoritative_intent}

Before planning, identify every explicit constraint in Director's Intent—including requested duration, timing, action, pacing, camera, audio and ending state—and obey all of them. These constraints are mandatory, not suggestions. For a single-item sequence, an explicitly requested total or scene duration is the duration of that one segment and must be returned exactly.

You are LTXDirector, an expert prompt planner for LTX Video 2.3. Analyze all {count} supplied timeline items in order.
Return exactly one segment per timeline item; never add, remove, merge or reorder. Visual items supply a frame; text-only items deliberately supply no image and must still receive a prompt and duration. A start frame is the exact opening frame and an end frame is the exact target.
Write production-ready natural-language prompts describing visible subject, action, expression, physical change, secondary motion, environment and camera behavior. {frame_planning_rule} Preserve identity, outfit, scene, lighting, angle, composition, aspect ratio and style. Use a stationary camera unless the frames clearly demand otherwise. Require gradual motion, overlapping progression, direct continuity and no cross-fade. Do not invent visual facts.
Treat the creative guidance above as defaults. When User intent explicitly requests something different, follow the user's instruction. User intent overrides conflicting creative defaults, but not the required segment count, frame order, start/end-frame meaning or strict JSON schema.
{duration_rule} Use 0.5-second increments. {audio}
The globalPrompt contains persistent subject, scene, camera, lighting, style, continuity and negative constraints only. {global_format}
Recheck the JSON against AUTHORITATIVE DIRECTOR'S INTENT before returning it. Correct any duration or prompt that fails an explicit constraint.
Return strict JSON: {{"segments":[{{"duration":5,"prompt":"..."}}],"globalPrompt":"..."}}"""


def _gemini(images: list[dict], key: str, model: str, rules: str, timeout: int, sfx: bool, spoken_dialog: bool) -> dict:
    raw = _gemini_raw(images, key, model, rules, timeout)
    return _validate(raw, len(images), sfx, spoken_dialog)


def _provider_raw(images: list[dict], provider: str, model: str, key: str, rules: str, timeout: int) -> str:
    if provider == "openai":
        return _openai_raw(images, key, rules, timeout)
    return _gemini_raw(images, key, model, rules, timeout)


def _gemini_raw(images: list[dict], key: str, model: str, rules: str, timeout: int) -> str:
    parts: list[dict] = [{"text": rules}]
    for index, item in enumerate(images, 1):
        label = "TEXT-ONLY SEGMENT" if item.get("kind") == "text" else f"{item['role'].upper()} FRAME"
        parts.append({"text": f"SEGMENT {index} OF {len(images)} — {label} — {item['name']}"})
        if item.get("image"):
            mime, encoded = re.match(r"^data:([^;]+);base64,(.+)$", item["image"], re.S).groups()
            parts.append({"inline_data": {"mime_type": mime, "data": encoded}})
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key, "content-type": "application/json"},
        json={"contents": [{"role": "user", "parts": parts}], "generationConfig": {"temperature": 0.25, "responseMimeType": "application/json"}},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    return _gemini_response_text(data)


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
    raw = _openai_raw(images, key, rules, timeout)
    return _validate(raw, len(images), sfx, spoken_dialog)


def _openai_raw(images: list[dict], key: str, rules: str, timeout: int) -> str:
    content: list[dict] = [{"type": "input_text", "text": rules}]
    for index, item in enumerate(images, 1):
        label = "TEXT-ONLY SEGMENT" if item.get("kind") == "text" else f"{item['role'].upper()} FRAME"
        content.append({"type": "input_text", "text": f"SEGMENT {index} OF {len(images)} — {label} — {item['name']}"})
        if item.get("image"):
            content.append({"type": "input_image", "image_url": item["image"], "detail": "high"})
    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
        json={"model": "gpt-5.4-mini", "input": [{"role": "user", "content": content}], "text": {"format": {"type": "json_object"}}},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    raw = data.get("output_text") or "".join(c.get("text", "") for item in data.get("output", []) for c in item.get("content", []) if c.get("type") == "output_text")
    if not str(raw).strip():
        raise AIResponseFormatError("OpenAI returned no generated text. The operation will retry.")
    return raw


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
        duration_value = segment.get("duration", 5)
        match = re.search(r"\d+(?:\.\d+)?", str(duration_value))
        if not match:
            raise AIResponseFormatError("The AI returned an invalid segment duration. Magic Build will retry.")
        maximum_duration = 60.0 if expected == 1 else 12.0
        duration = max(1.0, min(maximum_duration, round(float(match.group()) * 2) / 2))
        normalized_segments.append({"duration": duration, "prompt": prompt.strip()})
    if require_spoken_dialog:
        dialog_segments = [segment for segment in normalized_segments if "spoken dialog:" in segment["prompt"].casefold()]
        if not dialog_segments:
            raise AIResponseFormatError("The AI omitted requested spoken dialog from the sequence. Magic Build will retry.")
        if any("accent" not in segment["prompt"].casefold() for segment in dialog_segments):
            raise AIResponseFormatError("The AI omitted an explicit accent from a Spoken Dialog clause. Magic Build will retry.")
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
