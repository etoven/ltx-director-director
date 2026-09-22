from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

import requests

from .media import data_url, video_storyboard_data_urls
from .models import Segment


GEMINI_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-2.5-flash-lite",
]
MAX_INLINE_VIDEO_BYTES = 12 * 1024 * 1024


class AIResponseFormatError(ValueError):
    """The provider returned text that does not satisfy the response contract."""


def build_prompts(segments: list[Segment], provider: str, model: str, api_key: str, intent: str, sfx: bool, spoken_dialog: bool, hdr: bool, reduce_music: bool, timeout: int = 400) -> dict:
    images = [_segment_input(item) for item in segments]
    rules = _rules(len(images), intent, sfx, spoken_dialog, hdr, reduce_music, sum(item.kind == "text" for item in segments))
    if provider == "openai":
        return _openai(images, api_key, rules, timeout, sfx, spoken_dialog)
    return _gemini(images, api_key, model, rules, timeout, sfx, spoken_dialog)


def build_minimax_h3_prompt(segments: list[Segment], provider: str, model: str, api_key: str, intent: str, global_prompt: str, sfx: bool, spoken_dialog: bool, reduce_music: bool, timeout: int = 400) -> str:
    """Synthesize the complete ordered timeline into one MiniMax H3 prompt."""
    if not segments:
        raise ValueError("Add at least one timeline item before exporting a MiniMax H3 prompt.")
    inputs = _minimax_h3_inputs(segments, provider)
    rules = _minimax_h3_rules(segments, intent, global_prompt, sfx, spoken_dialog, reduce_music)
    raw = _provider_raw(inputs, provider, model, api_key, rules, timeout)
    return _validated_minimax_h3_prompt(raw, segments)


def refine_minimax_h3_prompt(segments: list[Segment], provider: str, model: str, api_key: str, intent: str, global_prompt: str, sfx: bool, spoken_dialog: bool, reduce_music: bool, current_prompt: str, refinement_instructions: str, timeout: int = 400) -> str:
    """Refine the user's edited MiniMax prompt without exposing private edit directions."""
    if not segments:
        raise ValueError("Add at least one timeline item before refining a MiniMax H3 prompt.")
    if not current_prompt.strip():
        raise ValueError("Write or generate a MiniMax H3 prompt before refining it.")
    inputs = _minimax_h3_inputs(segments, provider, refinement=True)
    rules = _minimax_h3_rules(segments, intent, global_prompt, sfx, spoken_dialog, reduce_music)
    rules += f"""

REFINEMENT MODE — FOLLOW THIS PRIORITY ORDER WHEN ANY INPUTS COMPETE:
1. PRIVATE REFINEMENT INSTRUCTIONS are the highest-priority edit request. Apply them completely and literally wherever they target the production prompt.
2. CURRENT EDITOR PROMPT is the authoritative creative content and current sequence state. Refine it; never rebuild it from the references.
3. Preserve the required timestamps and three-section transport structure.
4. Timeline frames, videos, segment prompts, global prompt, and Director's Intent are SECONDARY CONTINUITY EVIDENCE only. Use them to verify identity, pose, environment, composition, physical plausibility, and boundary continuity without overriding items 1 or 2.

- Make the smallest complete set of edits needed to satisfy the private refinement instructions. Preserve every deliberate user edit and every untouched passage.
- Never replace, ignore, or reinterpret the user's current prompt merely because requested motion is not visible in a still frame or differs from reference-frame guidance.
- Do not introduce a new action, camera path, transformation, setting, or visual fact from secondary evidence unless the refinement instructions request it or it is strictly necessary to repair a physical contradiction.
- Keep existing cue content attached to the same timestamp unless the private instructions explicitly request timing changes.
- The refinement instructions are private editing directions. Never quote, summarize, mention, or append them inside the production prompt.
- Return the same strict transport JSON contract, including a freshly checked private continuityPlan and the refined production prompt.

CURRENT EDITOR PROMPT:
{current_prompt.strip()}

PRIVATE REFINEMENT INSTRUCTIONS:
{refinement_instructions.strip() or 'Improve clarity, motion continuity, causal flow, and production readiness without changing the creative intent.'}
"""
    raw = _provider_raw(inputs, provider, model, api_key, rules, timeout)
    return _validated_minimax_h3_prompt(raw, segments)


def _validated_minimax_h3_prompt(raw: str, segments: list[Segment]) -> str:
    """Validate both newly generated and editor-refined MiniMax prompts."""
    result = _parse_json(raw)
    if not isinstance(result, dict):
        raise AIResponseFormatError("The AI returned an invalid MiniMax H3 response. The operation will retry.")
    prompt = result.get("prompt") or result.get("minimaxPrompt") or result.get("minimax_prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise AIResponseFormatError("The AI returned no MiniMax H3 prompt. The operation will retry.")
    continuity_plan = result.get("continuityPlan") or result.get("continuity_plan")
    if not isinstance(continuity_plan, dict):
        raise AIResponseFormatError("The AI returned no MiniMax continuity plan. The operation will retry.")
    persistent_anchors = continuity_plan.get("persistentAnchors") or continuity_plan.get("persistent_anchors")
    if not isinstance(persistent_anchors, list) or not any(str(anchor).strip() for anchor in persistent_anchors):
        raise AIResponseFormatError("The AI returned no persistent MiniMax continuity anchors. The operation will retry.")
    bridges = continuity_plan.get("bridges")
    expected_bridge_count = max(0, len(segments) - 1)
    if not isinstance(bridges, list) or len(bridges) != expected_bridge_count:
        raise AIResponseFormatError(
            f"The AI returned an invalid MiniMax bridge plan; expected {expected_bridge_count} adjacent transition bridge(s). "
            "The operation will retry."
        )
    for index, bridge in enumerate(bridges, 1):
        if not isinstance(bridge, dict) or bridge.get("fromSegment") != index or bridge.get("toSegment") != index + 1:
            raise AIResponseFormatError("The AI returned an out-of-order MiniMax bridge plan. The operation will retry.")
        if bridge.get("divergence") not in {"low", "medium", "high"}:
            raise AIResponseFormatError("The AI omitted a valid MiniMax bridge divergence level. The operation will retry.")
        if bridge.get("resolution") not in {"reach", "carry_forward"}:
            raise AIResponseFormatError("The AI omitted a valid MiniMax bridge resolution strategy. The operation will retry.")
        if not str(bridge.get("progressiveChange") or "").strip():
            raise AIResponseFormatError("The AI omitted a progressive MiniMax bridge action. The operation will retry.")
        if not str(bridge.get("boundaryState") or "").strip():
            raise AIResponseFormatError("The AI omitted the plausible state at a MiniMax cue boundary. The operation will retry.")
        if not str(bridge.get("carriedMotion") or "").strip():
            raise AIResponseFormatError("The AI omitted the motion carried through a MiniMax cue boundary. The operation will retry.")
    prompt = prompt.strip()
    required_sections = ("continuous_video", "soundscape", "music")
    missing = [section for section in required_sections if not re.search(rf"(?im)^\s*{section}\s*:", prompt)]
    if missing:
        raise AIResponseFormatError(
            f"The AI omitted required MiniMax H3 section(s): {', '.join(missing)}. The operation will retry."
        )
    detailed_match = re.search(
        r"(?ims)^\s*continuous_video\s*:\s*(.*?)(?=^\s*soundscape\s*:)",
        prompt,
    )
    detailed = detailed_match.group(1) if detailed_match else ""
    cue_timestamps = re.findall(r"(?m)^\s*(\d{2,}:\d{2}:\d{3})\b", detailed)
    expected_timestamps = []
    cursor = 0.0
    for segment in segments:
        expected_timestamps.append(_minimax_timestamp(cursor))
        cursor += segment.duration
    if cue_timestamps != expected_timestamps:
        raise AIResponseFormatError(
            f"The AI returned {len(cue_timestamps)} valid MiniMax timeline cue(s), but the timeline requires "
            f"exactly {len(segments)} at the prescribed start times. The operation will retry."
        )
    if re.search(r"(?im)^\s*\[Shot\s+\d+\]", detailed):
        raise AIResponseFormatError("The AI added structural shot headers that can force hard cuts. The operation will retry.")
    if re.search(r"(?i)<(?:Picture|Video)\s+\d+>", detailed):
        raise AIResponseFormatError("The AI exposed reference labels in the motion description. The operation will retry.")
    if re.search(r"(?m)^\s*\d{2,}:\d{2}:\d{3}\s+\[[^\]\n]+\]", detailed):
        raise AIResponseFormatError("The AI added a bracketed camera or shot command after a timeline cue. The operation will retry.")
    if re.search(r"(?i)\b(?:hard|jump)\s+cut\b|\bcuts?\s+to\b", detailed):
        raise AIResponseFormatError("The AI added an editorial cut instruction. The operation will retry.")
    if re.search(r"(?im)^\s*(?:subject_definitions|summary|retention_analysis|detailed_description)\s*:", prompt):
        raise AIResponseFormatError("The AI exposed internal reference analysis in the MiniMax production prompt. The operation will retry.")
    cue_starts = list(re.finditer(r"(?m)^\s*(\d{2,}:\d{2}:\d{3})\s+", detailed))
    for index, cue_start in enumerate(cue_starts):
        block_end = cue_starts[index + 1].start() if index + 1 < len(cue_starts) else len(detailed)
        interval_text = detailed[cue_start.end():block_end]
        word_count = len(re.findall(r"\b[\w'-]+\b", interval_text))
        required_words = max(12, min(32, round(float(segments[index].duration) * 6)))
        if word_count < required_words:
            raise AIResponseFormatError(
                f"The AI returned a skimpy MiniMax interval at {cue_start.group(1)} ({word_count} words); "
                f"at least {required_words} words are required for production-ready motion detail. The operation will retry."
            )
    return prompt


@lru_cache(maxsize=256)
def _file_content_digest(path_text: str, size: int, modified_ns: int) -> str:
    """Return a stable media digest while avoiding repeat reads during one session."""
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))
    with Path(path_text).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _segment_media_digest(segment: Segment) -> str:
    source = Path(segment.media_path or segment.preview_path) if (segment.media_path or segment.preview_path) else None
    if not source or not source.is_file():
        return ""
    stat = source.stat()
    return _file_content_digest(str(source.resolve()), stat.st_size, stat.st_mtime_ns)


def minimax_h3_cache_key(segments: list[Segment], provider: str, model: str, intent: str, global_prompt: str, sfx: bool, spoken_dialog: bool, reduce_music: bool) -> str:
    """Fingerprint every input that can materially change a MiniMax prompt."""
    records = []
    for segment in segments:
        records.append({
            "id": segment.id,
            "name": segment.name,
            "kind": segment.kind,
            "role": segment.role,
            "prompt": segment.prompt,
            "imagePrompt": segment.image_prompt,
            "duration": segment.duration,
            "mediaDurationFrames": segment.media_duration_frames,
            "trimStart": segment.trim_start,
            "mediaDigest": _segment_media_digest(segment),
        })
    value = {
        "schema": 1,
        "provider": provider,
        "model": model,
        "intent": intent,
        "globalPrompt": global_prompt,
        "sfx": bool(sfx),
        "spokenDialog": bool(spoken_dialog),
        "reduceMusic": bool(reduce_music),
        "segments": records,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def refine_timing(segments: list[Segment], selected_index: int, provider: str, model: str, api_key: str, intent: str, requested_total: float, timeout: int = 400) -> dict:
    """Retiming pass that may change only the selected segment's duration."""
    if not 0 <= selected_index < len(segments):
        raise ValueError("Select a segment to refine its timing.")
    untouched_total = sum(segment.duration for index, segment in enumerate(segments) if index != selected_index)
    required = None
    if requested_total > 0:
        required = round(requested_total - untouched_total, 2)
        if required < 0.01:
            raise ValueError(
                f"The requested {requested_total:.2f}s sequence cannot be reached by changing only this segment. "
                f"It would need a non-positive duration of {required:.2f}s."
            )
    images = _refinement_images(segments, selected_index)
    rules = _timing_rules(segments, selected_index, intent, required)
    raw = _provider_raw(images, provider, model, api_key, rules, timeout)
    result = _parse_json(raw)
    if not isinstance(result, dict):
        raise AIResponseFormatError("The AI returned an invalid timing response. The operation will retry.")
    duration = _strict_duration(result.get("duration"))
    if required is not None and duration != required:
        raise AIResponseFormatError(f"The AI ignored the required {required:.2f}s selected-segment duration. The operation will retry.")
    return {"duration": duration}


def refine_segment_prompt(segments: list[Segment], selected_index: int, provider: str, model: str, api_key: str, intent: str, requested_total: float, timeout: int = 400) -> dict:
    """Refine only the selected prompt, with an optional selected-duration change."""
    if not 0 <= selected_index < len(segments):
        raise ValueError("Select a segment to refine its prompt.")
    selected = segments[selected_index]
    if not selected.prompt.strip():
        raise ValueError("The selected segment needs an existing prompt before it can be refined.")
    untouched_total = sum(segment.duration for index, segment in enumerate(segments) if index != selected_index)
    required = None
    if requested_total > 0:
        required = round(requested_total - untouched_total, 2)
        if required < 0.01:
            raise ValueError(
                f"The requested {requested_total:.2f}s sequence cannot be reached while changing only this segment. "
                f"It would need a non-positive duration of {required:.2f}s."
            )
    images = _refinement_images(segments, selected_index)
    rules = _prompt_refinement_rules(segments, selected_index, intent, required)
    raw = _provider_raw(images, provider, model, api_key, rules, timeout)
    result = _parse_json(raw)
    if not isinstance(result, dict):
        raise AIResponseFormatError("The AI returned an invalid prompt-refinement response. The operation will retry.")
    prompt = result.get("prompt") or result.get("segmentPrompt") or result.get("segment_prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise AIResponseFormatError("The AI returned no refined prompt. The operation will retry.")
    image_prompt = _image_prompt_value(result)
    if not image_prompt:
        raise AIResponseFormatError("The AI returned no Gemini image-generation prompt. The operation will retry.")
    duration = _strict_duration(result.get("duration"))
    if required is not None and duration != required:
        raise AIResponseFormatError(f"The AI ignored the required {required:.2f}s selected-segment duration. The operation will retry.")
    return {"prompt": prompt.strip(), "imagePrompt": image_prompt, "duration": duration}


def _segment_input(item: Segment) -> dict:
    value = {"name": item.name, "role": item.role, "kind": item.kind}
    if item.kind != "text" and item.preview_path:
        value["image"] = data_url(item.preview_path, max_edge=384)
    return value


def _minimax_timestamp(seconds: float) -> str:
    """Format a MiniMax motion cue as MM:SS:mmm without implying an edit point."""
    total_milliseconds = max(0, round(float(seconds) * 1000))
    minutes, remainder = divmod(total_milliseconds, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{minutes:02d}:{whole_seconds:02d}:{milliseconds:03d}"


def _minimax_h3_inputs(segments: list[Segment], provider: str = "gemini", refinement: bool = False) -> list[dict]:
    inputs = []
    cursor = 0.0
    picture_number = 0
    video_number = 0
    seen_visual = False
    for index, item in enumerate(segments):
        start = cursor
        end = start + item.duration
        if item.kind == "image":
            picture_number += 1
        elif item.kind == "video":
            video_number += 1
        if item.kind == "text":
            continuity_function = "action instruction spanning this complete timeline interval"
        elif not seen_visual:
            continuity_function = "opening visual anchor establishing the initial continuous state"
            seen_visual = True
        elif item.kind == "video":
            continuity_function = "temporal motion evidence spanning this complete timeline interval"
        elif item.role == "end":
            continuity_function = "visual target to approach progressively and resolve by this interval's end"
        else:
            continuity_function = "continuity checkpoint to reach progressively from the preceding interval"
        value = {
            "name": item.name,
            "role": item.role,
            "kind": item.kind,
            "start_time": start,
            "end_time": end,
            "duration": item.duration,
            "cue_timestamp": _minimax_timestamp(start),
            "next_cue_timestamp": _minimax_timestamp(end) if index + 1 < len(segments) else None,
            "continuity_function": continuity_function,
            "prompt": item.prompt.strip() or "[No existing segment prompt; infer conservatively from supplied visual and sequence context.]",
            "prompt_label": "CURRENT TIMELINE PROMPT — SOURCE MATERIAL FOR MINIMAX H3 SYNTHESIS",
            "image_prompt": item.image_prompt.strip(),
            "picture_number": picture_number if item.kind == "image" else None,
            "video_number": video_number if item.kind == "video" else None,
        }
        if refinement:
            value["guidance_priority"] = (
                "SECONDARY CONTINUITY EVIDENCE ONLY — do not override the private refinement instructions "
                "or rebuild the authoritative current editor prompt from this record."
            )
        source = Path(item.media_path) if item.media_path else None
        if item.kind == "video" and source and source.is_file():
            value["source_duration"] = round((item.media_duration_frames or 0) / 24, 3) or None
            value["trim_start_frame"] = item.trim_start
            if provider != "openai" and source.stat().st_size <= MAX_INLINE_VIDEO_BYTES:
                value["video"] = data_url(str(source))
                value["video_analysis_mode"] = "full source video"
            else:
                value["video_frames"] = video_storyboard_data_urls(str(source))
                value["video_analysis_mode"] = "timestamped samples across the full source video"
        if item.kind == "image" and item.preview_path:
            value["image"] = data_url(item.preview_path, max_edge=512)
        elif item.kind == "video" and not value.get("video") and not value.get("video_frames") and item.preview_path:
            value["image"] = data_url(item.preview_path, max_edge=512)
            value["video_analysis_mode"] = "single preview fallback; infer motion only from the authoritative video prompt"
        inputs.append(value)
        cursor = end
    return inputs


def _minimax_h3_rules(segments: list[Segment], intent: str, global_prompt: str, sfx: bool, spoken_dialog: bool, reduce_music: bool) -> str:
    total = sum(item.duration for item in segments)
    image_count = sum(item.kind == "image" for item in segments)
    video_count = sum(item.kind == "video" for item in segments)
    cue_timestamps = []
    cursor = 0.0
    for item in segments:
        cue_timestamps.append(_minimax_timestamp(cursor))
        cursor += item.duration
    cue_list = ", ".join(cue_timestamps)
    cue_template = "\n".join(
        f"{timestamp} {{what changes continuously during interval {index}; preserve the carried-forward state and describe only the new motion delta}}"
        for index, timestamp in enumerate(cue_timestamps, 1)
    )
    bridge_template = [
        {
            "fromSegment": index,
            "toSegment": index + 1,
            "divergence": "low|medium|high",
            "progressiveChange": "specific physically continuous motion that links the adjacent states",
            "boundaryState": "plausible visible state reached exactly at the next cue without a reset",
            "carriedMotion": "direction, velocity, action, or settled stillness that continues through the cue",
            "resolution": "reach|carry_forward",
        }
        for index in range(1, len(segments))
    ]
    sound_rule = (
        "Write a concise soundscape grounded in visible actions, materials, environments, clearly audible video-reference content, and existing SFX instructions."
        if sfx else
        "Do not invent Foley or ambient sound. Preserve clearly audible video-reference content when the provider exposes it; otherwise, if no source prompt explicitly requests sound, write `None specified.` under soundscape."
    )
    dialog_rule = (
        "Preserve any actual spoken words, delivery, language, accent, and lip-sync requirements in the appropriate timed motion beat."
        if spoken_dialog else
        "Do not invent spoken dialogue; preserve it only if it is already explicitly written in the source prompts or Director's Intent."
    )
    music_rule = (
        "Write `None. Use only the described diegetic soundscape.` under music unless the source explicitly requires music."
        if reduce_music else
        "Summarize explicitly requested music; otherwise infer only a brief, stylistically compatible music direction when it materially supports the sequence."
    )
    return f"""You are a sequence prompt editor for MiniMax H3 video generation. Convert the complete ordered LTX Director timeline below into ONE detailed, production-ready continuous-flow video prompt. This is synthesis, not concatenation: preserve important actions and continuity while describing one uninterrupted chronological progression whose visual state evolves naturally across every cue boundary.

Derive facts exclusively from the supplied timeline frames, videos, current prompts, global prompt, and Director's Intent. Never invent unsupported identity, anatomy, clothing, setting, dialogue, or transformation facts.

TIMELINE FACTS:
- {len(segments)} ordered timeline items, including {image_count} still-image reference(s) and {video_count} video reference(s)
- the production prompt must contain exactly {len(segments)} bare timestamped motion cues, one for each timeline interval, while still describing a single continuous video
- required cue timestamps, in order: {cue_list}
- exact total duration: {total:.2f} seconds
- each record supplies exact start/end time, duration, media kind, current video prompt, and when available an audio-free still-image prompt
- the first visual establishes the opening state
- a later still-image START frame is a continuity checkpoint whose approach must begin during the preceding interval; it is not a new scene or edit instruction
- a still-image END frame is a target to approach progressively and resolve by that interval's end; it is not a new action beginning at its cue
- VIDEO records are temporal references: inspect their complete ordered motion, action progression, camera behavior, transformations, ending state, and clearly audible content when accessible instead of treating their preview or sampled frames as unrelated still pictures
- when a VIDEO is represented by timestamped samples, interpret them as ordered observations from one continuous source clip; never invent motion that is unsupported by their progression or the authoritative current prompt

PRIVATE CONTINUITY-PLANNING PASS:
- distinguish the opening state from a persistent anchor. Before listing persistent anchors, compare each candidate against the entire timeline and exclude any pose, anatomy, clothing, prop state, lighting state, framing, camera position, or environment that a later source intentionally changes
- when camera behavior evolves, preserve one coherent camera path and spatial geography rather than incorrectly declaring the opening framing or camera position fixed throughout
- internally compare the visible/temporal state at the end of every interval with the opening demand of the next interval: subject position and scale, pose, anatomy or transformation progress, clothing, props, environment, lighting, camera framing, camera motion, screen direction, and momentum
- classify each adjacent pair as low, medium, or high divergence
- plan one concrete progressive bridge for every adjacent pair; begin preparation before the next timestamp through motivated camera or subject motion, overlapping action, occlusion, reframing, object interaction, environmental motion, or a gradual transformation supported by the source
- for every boundary, state both the plausible visible state reached exactly at the timestamp and the subject/camera/environmental momentum carried through it; a timestamp marks time passing, never a reset of pose, velocity, framing, or scene state
- use `reach` only when the next checkpoint can be achieved physically within the available interval
- use `carry_forward` when a high-divergence target cannot be reached believably in time: preserve motion, identity, spatial logic, and momentum, complete only the plausible portion by the cue, then continue the remaining evolution after it instead of snapping to the reference
- prioritize temporal continuity over literal instantaneous reconstruction of a divergent still; references are state evidence, never edit commands
- keep this plan private. Never expose segment numbers, picture/video labels, divergence ratings, bridge strategies, reference inventories, or analysis headings in the production prompt

MINIMAX H3 PRODUCTION-PROMPT PRINCIPLES:
- treat all supplied images, videos, prompts, and audio context as one unified creative context; references guide identity, motion, framing, atmosphere, and continuity but are not edit points
- front-load only the subject identity, environment, visual style, lighting, screen direction, camera behavior, and transformation state that truly remain stable across the complete timeline; never promote an opening-only condition into a global claim
- because visual references already establish appearance and setting, spend the timestamped prose on motion: what changes, how it progresses, its physical cause, contact and weight, secondary motion, and how existing momentum flows through the cue boundary
- describe later intervals as deltas from the carried-forward state; do not reintroduce or re-inventory the subject, outfit, location, composition, or props at every timestamp
- positively describe continuous state and motion. Avoid editorial vocabulary, transition labels, reference labels, and negative prompting in the production prompt
- state a stationary or persistent camera baseline once. If source material requires camera movement, describe one coherent evolving camera path rather than resetting framing at each cue
- express essential camera movement as ordinary prose within the motion interval. The bare timestamp is the only structural prefix
- use short causal sentences and overlapping motion. Start preparatory movement before a substantially different checkpoint, preserve velocity across its timestamp, and settle only after the new state has been physically reached
- qualify any opening-only condition with `initially` when it later changes. Never call the camera fixed or stationary in the same interval where it starts moving; instead describe it as initially still, then beginning one smooth path

AUTHORITATIVE DIRECTOR'S INTENT:
{intent.strip() or 'No additional director intent supplied.'}

GLOBAL CONTINUITY PROMPT:
{global_prompt.strip() or 'No global prompt supplied.'}

REQUIRED PRODUCTION-PROMPT SECTIONS — use these three lowercase headings exactly, in order, with no Markdown fences:

continuous_video:
Start with one precise persistent-anchor sentence. Then write exactly {len(segments)} chronological interval lines. Start each with its exact bare `MM:SS:mmm` timestamp followed immediately by natural motion prose; use exactly these timestamps in order: {cue_list}. The timestamp is the line's only prefix. Use 2 to 4 detailed sentences per interval, scaled to its duration. Cover the primary action and progression, visible pose or state delta, physical cause/contact/weight and secondary motion, plus camera or environmental response when it changes. Describe only new action and progressive state change while carrying prior state and momentum forward. Avoid padding and repeated inventories, but provide at least enough concrete motion detail to make every interval production-ready. A video reference contributes its full temporal behavior, not merely sampled frames.

soundscape:
{sound_rule} {dialog_rule}

music:
{music_rule}

PRODUCTION-PROMPT TEMPLATE FOR THIS {len(segments)}-INTERVAL TIMELINE — the number of cue lines is generated from the input timeline and is never a fixed example count:

continuous_video:
{{one precise sentence stating only whole-timeline visual anchors and either a truly fixed camera baseline or one coherent evolving camera path}}
{cue_template}

soundscape:
{{timeline-specific soundscape or the required None statement}}

music:
{{timeline-specific music direction or the required None statement}}

Before returning, verify that the production prompt has exactly {len(segments)} timestamp lines in prescribed order, no structural shot labels, no picture/video labels, no bracketed camera commands, no explicit edit or cut instructions, and no repeated full-scene inventories.

Return strict transport JSON with exactly these two top-level fields. `continuityPlan` is private validation data and must not be copied into `prompt`:
{{
  "continuityPlan": {{
    "persistentAnchors": ["specific visual/camera anchor that truly persists"],
    "bridges": {json.dumps(bridge_template)}
  }},
  "prompt": "the complete three-section MiniMax H3 production prompt"
}}"""


def _refinement_images(segments: list[Segment], selected_index: int) -> list[dict]:
    start = max(0, selected_index - 1)
    end = min(len(segments), selected_index + 2)
    return [
        {
            "name": f"Segment {index + 1} {'SELECTED' if index == selected_index else ('PREVIOUS' if index < selected_index else 'NEXT')} — {item.name}",
            "role": item.role,
            "kind": item.kind,
            "selected": index == selected_index,
            "prompt": item.prompt,
            **({"image": data_url(item.preview_path, max_edge=384)} if item.kind != "text" and item.preview_path else {}),
        }
        for index, item in enumerate(segments[start:end], start)
    ]


def _segment_context(segments: list[Segment], selected_index: int) -> str:
    records = []
    for index, segment in enumerate(segments):
        relation = "SELECTED" if index == selected_index else ("PREVIOUS" if index == selected_index - 1 else ("NEXT" if index == selected_index + 1 else "SEQUENCE CONTEXT"))
        anchor = "TEXT-ONLY SEGMENT" if segment.kind == "text" else f"{segment.role.upper()} FRAME"
        records.append(
            f"Segment {index + 1} [{relation}; {anchor}; current duration {segment.duration:.2f}s]\n"
            f"Existing prompt (immutable unless SELECTED prompt refinement): {segment.prompt or '[empty]'}"
        )
    return "\n\n".join(records)


def _timing_rules(segments: list[Segment], selected_index: int, intent: str, required: float | None) -> str:
    duration_instruction = (
        f"Return exactly {required:.2f} seconds because this is the only duration that satisfies the requested total sequence length."
        if required is not None else
        "Choose any positive duration needed for the action, with two decimal places of precision."
    )
    return f"""You are performing a TIMING-ONLY refinement for LTX Video 2.3.
Analyze the complete ordered segment plan below so the selected segment still fits the sequence. Use the immediately previous and next prompts and supplied adjacent frames as the primary motion and continuity context.
Change ONLY the duration of selected segment {selected_index + 1}. Every prompt is immutable: do not rewrite, summarize or return any prompt text. Do not change any other duration.
Estimate the time genuinely needed for the selected prompt's action, physical progression, camera motion, Spoken Dialog and lip sync. Respect its start/end-frame role and surrounding continuity. There is no segment-duration or total sequence-length ceiling.
{duration_instruction}
Director's intent and planning controls:
{intent.strip() or 'No additional intent supplied.'}

ORDERED EXISTING PLAN:
{_segment_context(segments, selected_index)}

Return strict JSON containing only: {{"duration": 5.0}}"""


def _prompt_refinement_rules(segments: list[Segment], selected_index: int, intent: str, required: float | None) -> str:
    duration_instruction = (
        f"Return exactly {required:.2f} seconds because this is the only duration that satisfies the requested total sequence length."
        if required is not None else
        "You may assign any positive duration needed by the refined action or dialog, with two decimal places of precision."
    )
    return f"""You are refining ONE existing segment prompt for LTX Video 2.3.
Refine ONLY segment {selected_index + 1}. The SELECTED CURRENT EDITOR PROMPT is the authoritative creative instruction and is the text the user explicitly asked you to refine. Preserve every requested action, change, camera instruction, timing cue and constraint from that prompt while improving clarity, temporal progression, physical causality, secondary motion, Spoken Dialog delivery and lip-sync direction where present.
For an image segment, use the supplied image only to ground visible identity, pose, environment and composition. Never replace, ignore or reinterpret the user's selected prompt merely because its requested motion is not visible in the still frame.
Use the immediately previous and next prompts and supplied adjacent frames for continuity, but do not rewrite or return any other prompt. Do not change the global prompt.
Also return `imagePrompt` as a separate Gemini still-image generation prompt derived from the refined selected prompt. Do not simplify, replace, or remove audio/vocal language from `prompt`; the established production-ready video prompt remains authoritative and complete. In `imagePrompt` only, preserve its visible subject, transformation state or action moment, pose, expression, environment, composition, camera, lighting and style, but remove every audio-only instruction including SFX, Foley, ambience, music, Spoken Dialog, voice, accent, vocalization, lip-sync and sound cues. Describe one representative still frame rather than a timed video sequence.
{duration_instruction}
Director's intent and planning controls:
{intent.strip() or 'No additional intent supplied.'}

ORDERED EXISTING PLAN:
{_segment_context(segments, selected_index)}

Return strict JSON containing only: {{"prompt":"refined selected prompt","imagePrompt":"Gemini still-image prompt with no audio or vocal directions","duration":5.0}}"""


def _strict_duration(value: object) -> float:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", str(value or ""))
    if not match:
        raise AIResponseFormatError("The AI returned an invalid refined duration. The operation will retry.")
    duration = float(match.group(1))
    if duration <= 0:
        raise AIResponseFormatError("The AI returned a non-positive duration. The operation will retry.")
    return round(duration, 2)


def _image_prompt_value(value: dict) -> str:
    prompt = value.get("imagePrompt") or value.get("image_prompt") or value.get("geminiImagePrompt") or value.get("gemini_image_prompt")
    return prompt.strip() if isinstance(prompt, str) else ""


def _rules(count: int, intent: str, sfx: bool, spoken_dialog: bool, hdr: bool, reduce_music: bool, text_count: int = 0) -> str:
    if spoken_dialog and count == 1:
        spoken_rule = (
            "SPOKEN DIALOG IS ON: The single segment must include an appropriate clause beginning exactly `Spoken Dialog:` and containing actual audible words spoken by a visible character. "
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
            "If User intent specifies a total scene or segment duration, return that duration exactly in the single segment. "
            "The JSON must still use a segments array containing exactly one object; never return a singular segment object or a bare segment."
        )
        duration_rule = "Assign any positive duration genuinely required by the action; an explicit User-intent duration is authoritative."
    elif count == 1:
        frame_planning_rule = (
            "TEXT-ONLY MODE: No visual frame is supplied. Treat Director's Intent and the text segment's existing prompt as authoritative context. "
            "Write a complete time-based LTX prompt and do not claim to see visual facts that were not provided."
        )
        duration_rule = "Assign any positive duration genuinely required by the action."
    else:
        frame_planning_rule = "Infer visual transitions only from adjacent supplied frames. Text-only segments deliberately have no image; use their ordered prompt context without inventing unseen visual facts."
        duration_rule = "Assign any positive duration genuinely required by the action."
    authoritative_intent = intent.strip() or "Infer motion only from the ordered frames."
    return f"""EXPECTED SEGMENT COUNT: {count}

AUTHORITATIVE DIRECTOR'S INTENT:
{authoritative_intent}

Before planning, identify every explicit constraint in Director's Intent—including requested duration, timing, action, pacing, camera, audio and ending state—and obey all of them. These constraints are mandatory, not suggestions. For a single-frame sequence, an explicitly requested total or scene duration is the duration of that one segment and must be returned exactly.

You are LTXDirector, an expert prompt planner for LTX Video 2.3. Analyze all {count} supplied timeline items in order.
Return exactly one segment per timeline item; never add, remove, merge or reorder. Visual items supply a frame; text-only items deliberately supply no image and must still receive a prompt and duration. A start frame is the exact opening frame and an end frame is the exact target.
Write production-ready natural-language prompts describing visible subject, action, expression, physical change, secondary motion, environment and camera behavior. {frame_planning_rule} Preserve identity, outfit, scene, lighting, angle, composition, aspect ratio and style. Use a stationary camera unless the frames clearly demand otherwise. Require gradual motion, overlapping progression, direct continuity and no cross-fade. Do not invent visual facts.
For every segment, also return `imagePrompt` as a separate Gemini still-image generation prompt derived from that segment's video prompt. Do not simplify, replace, or remove audio/vocal language from `prompt`; the established production-ready video prompt remains authoritative and complete. In `imagePrompt` only, preserve the visible subject, transformation state or action moment, pose, expression, environment, composition, camera, lighting and style. Remove every audio-only instruction, including SFX, Foley, ambience, music, Spoken Dialog, voice, accent, vocalization, lip-sync and sound cues. Describe one representative still frame rather than a timed video sequence.
Treat the creative guidance above as defaults. When User intent explicitly requests something different, follow the user's instruction. User intent overrides conflicting creative defaults, but not the required segment count, frame order, start/end-frame meaning or strict JSON schema.
{duration_rule} Use no more than two decimal places of precision. There is no segment-duration or total sequence-duration ceiling. {audio}
The globalPrompt contains persistent subject, scene, camera, lighting, style, continuity and negative constraints only. {global_format}
Recheck the JSON against AUTHORITATIVE DIRECTOR'S INTENT before returning it. Correct any duration or prompt that fails an explicit constraint.
Return strict JSON: {{"segments":[{{"duration":5,"prompt":"...","imagePrompt":"Gemini still-image prompt with no audio or vocal directions"}}],"globalPrompt":"..."}}"""


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
        label = (
            "TEXT-ONLY SEGMENT" if item.get("kind") == "text" else
            "VIDEO SEGMENT — ANALYZE COMPLETE TEMPORAL CONTENT" if item.get("kind") == "video" else
            f"{item['role'].upper()} FRAME"
        )
        picture = f" — PICTURE {item['picture_number']}" if item.get("picture_number") else ""
        video = f" — VIDEO {item['video_number']}" if item.get("video_number") else ""
        timing = ""
        if "start_time" in item and "end_time" in item:
            timing = f" — {float(item['start_time']):.3f}s to {float(item['end_time']):.3f}s ({float(item.get('duration', 0)):.3f}s)"
        cue = f" — REQUIRED OUTPUT CUE {item['cue_timestamp']}" if item.get("cue_timestamp") else ""
        next_cue = f" — NEXT CUE BOUNDARY {item['next_cue_timestamp']}" if item.get("next_cue_timestamp") else " — FINAL INTERVAL"
        continuity = f" — CONTINUITY ROLE: {item['continuity_function']}" if item.get("continuity_function") else ""
        analysis_mode = f" — {item['video_analysis_mode']}" if item.get("video_analysis_mode") else ""
        source_duration = f" — source clip {float(item['source_duration']):.3f}s" if item.get("source_duration") else ""
        parts.append({"text": f"SEGMENT {index} OF {len(images)} — {label}{picture}{video} — {item['name']}{timing}{cue}{next_cue}{continuity}{source_duration}{analysis_mode}"})
        if item.get("video"):
            mime, encoded = re.match(r"^data:([^;]+);base64,(.+)$", item["video"], re.S).groups()
            parts.append({"inline_data": {"mime_type": mime, "data": encoded}})
        for frame in item.get("video_frames") or []:
            parts.append({"text": f"VIDEO {item['video_number']} OBSERVATION AT SOURCE {float(frame['timestamp']):.3f}s"})
            mime, encoded = re.match(r"^data:([^;]+);base64,(.+)$", frame["image"], re.S).groups()
            parts.append({"inline_data": {"mime_type": mime, "data": encoded}})
        if item.get("image"):
            mime, encoded = re.match(r"^data:([^;]+);base64,(.+)$", item["image"], re.S).groups()
            parts.append({"inline_data": {"mime_type": mime, "data": encoded}})
        if "prompt" in item:
            authority = item.get("prompt_label") or ("SELECTED CURRENT EDITOR PROMPT — AUTHORITATIVE; REFINE THIS EXACT INPUT" if item.get("selected") else "CONTEXT PROMPT — DO NOT REWRITE")
            parts.append({"text": f"{authority}:\n{item.get('prompt') or '[empty]'}"})
        if item.get("image_prompt"):
            parts.append({"text": f"AUDIO-FREE VISUAL CONTEXT FOR THIS SEGMENT:\n{item['image_prompt']}"})
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
        label = (
            "TEXT-ONLY SEGMENT" if item.get("kind") == "text" else
            "VIDEO SEGMENT — ANALYZE ORDERED TEMPORAL OBSERVATIONS" if item.get("kind") == "video" else
            f"{item['role'].upper()} FRAME"
        )
        picture = f" — PICTURE {item['picture_number']}" if item.get("picture_number") else ""
        video = f" — VIDEO {item['video_number']}" if item.get("video_number") else ""
        timing = ""
        if "start_time" in item and "end_time" in item:
            timing = f" — {float(item['start_time']):.3f}s to {float(item['end_time']):.3f}s ({float(item.get('duration', 0)):.3f}s)"
        cue = f" — REQUIRED OUTPUT CUE {item['cue_timestamp']}" if item.get("cue_timestamp") else ""
        next_cue = f" — NEXT CUE BOUNDARY {item['next_cue_timestamp']}" if item.get("next_cue_timestamp") else " — FINAL INTERVAL"
        continuity = f" — CONTINUITY ROLE: {item['continuity_function']}" if item.get("continuity_function") else ""
        analysis_mode = f" — {item['video_analysis_mode']}" if item.get("video_analysis_mode") else ""
        source_duration = f" — source clip {float(item['source_duration']):.3f}s" if item.get("source_duration") else ""
        content.append({"type": "input_text", "text": f"SEGMENT {index} OF {len(images)} — {label}{picture}{video} — {item['name']}{timing}{cue}{next_cue}{continuity}{source_duration}{analysis_mode}"})
        for frame in item.get("video_frames") or []:
            content.append({"type": "input_text", "text": f"VIDEO {item['video_number']} OBSERVATION AT SOURCE {float(frame['timestamp']):.3f}s"})
            content.append({"type": "input_image", "image_url": frame["image"], "detail": "high"})
        if item.get("image"):
            content.append({"type": "input_image", "image_url": item["image"], "detail": "high"})
        if "prompt" in item:
            authority = item.get("prompt_label") or ("SELECTED CURRENT EDITOR PROMPT — AUTHORITATIVE; REFINE THIS EXACT INPUT" if item.get("selected") else "CONTEXT PROMPT — DO NOT REWRITE")
            content.append({"type": "input_text", "text": f"{authority}:\n{item.get('prompt') or '[empty]'}"})
        if item.get("image_prompt"):
            content.append({"type": "input_text", "text": f"AUDIO-FREE VISUAL CONTEXT FOR THIS SEGMENT:\n{item['image_prompt']}"})
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
        image_prompt = _image_prompt_value(segment)
        if not image_prompt:
            raise AIResponseFormatError("The AI returned a segment without a Gemini image-generation prompt. Magic Build will retry.")
        prompt_lower = prompt.casefold()
        if require_sfx and "sfx:" not in prompt_lower:
            raise AIResponseFormatError("The AI omitted required SFX direction. Magic Build will retry.")
        duration_value = segment.get("duration", 5)
        match = re.search(r"\d+(?:\.\d+)?", str(duration_value))
        if not match:
            raise AIResponseFormatError("The AI returned an invalid segment duration. Magic Build will retry.")
        duration = max(0.01, round(float(match.group()), 2))
        normalized_segments.append({"duration": duration, "prompt": prompt.strip(), "imagePrompt": image_prompt})
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
            normalized["segments"] = [{key: normalized[key] for key in ("duration", "prompt", "segmentPrompt", "segment_prompt", "imagePrompt", "image_prompt", "geminiImagePrompt", "gemini_image_prompt") if key in normalized}]
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
