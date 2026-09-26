from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

import requests

from .media import data_url, video_storyboard_data_urls
from .models import Segment
from .minimax_reference import reference_image_for_provider, reference_inventory, reference_rules, reference_slots


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


def minimax_interval_detail_standard(duration: float) -> tuple[int, int]:
    """Return duration-scaled sentence and word guidance for one MiniMax interval."""
    duration = max(0.0, float(duration))
    sentences = 2 if duration < 4.0 else (3 if duration < 7.0 else 4)
    words = max(24, min(50, round(duration * 10)))
    return sentences, words


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
    inputs = _minimax_h3_inputs(segments, provider, frame_checkpoints=True)
    rules = _minimax_h3_rules(segments, intent, global_prompt, sfx, spoken_dialog, reduce_music, structured=True)
    raw = _provider_raw(inputs, provider, model, api_key, rules, timeout)
    return _assemble_minimax_h3_frames(raw, segments)


def build_minimax_h3_reference_prompt(segments: list[Segment], provider: str, model: str, api_key: str, intent: str, global_prompt: str, sfx: bool, spoken_dialog: bool, reduce_music: bool, timeout: int = 400, reference_images: list | None = None) -> str:
    """Write a reference brief using the client-detected workflow and asset map."""
    if not segments:
        raise ValueError("Add at least one timeline item before generating a MiniMax H3 reference prompt.")
    inputs = _minimax_reference_inputs(segments, provider, reference_images)
    rules = _minimax_h3_reference_rules(segments, intent, global_prompt, sfx, spoken_dialog, reduce_music, reference_images)
    raw = _provider_raw(inputs, provider, model, api_key, rules, timeout)
    return _extract_minimax_h3_prompt(raw)


def refine_minimax_h3_prompt(segments: list[Segment], provider: str, model: str, api_key: str, intent: str, global_prompt: str, sfx: bool, spoken_dialog: bool, reduce_music: bool, current_prompt: str, refinement_instructions: str, timeout: int = 400) -> str:
    """Refine the user's edited MiniMax prompt without exposing private edit directions."""
    if not segments:
        raise ValueError("Add at least one timeline item before refining a MiniMax H3 prompt.")
    if not current_prompt.strip():
        raise ValueError("Write or generate a MiniMax H3 prompt before refining it.")
    inputs = _minimax_h3_inputs(segments, provider, refinement=True, frame_checkpoints=True)
    rules = _minimax_h3_rules(segments, intent, global_prompt, sfx, spoken_dialog, reduce_music)
    rules += f"""

REFINEMENT MODE — FOLLOW THIS PRIORITY ORDER WHEN ANY INPUTS COMPETE:
1. PRIVATE REFINEMENT INSTRUCTIONS are the highest-priority edit request. Apply them completely and literally wherever they target the production prompt.
2. CURRENT EDITOR PROMPT is the authoritative creative content and current sequence state. Refine it; never rebuild it from the references.
3. Preserve the required timestamps and three-section production-prompt structure.
4. Timeline frames, videos, segment prompts, global prompt, and Director's Intent are SECONDARY CONTINUITY EVIDENCE only. Use them to verify identity, pose, environment, composition, physical plausibility, and boundary continuity without overriding items 1 or 2.

- Make the smallest complete set of edits needed to satisfy the private refinement instructions. Preserve every deliberate user edit and every untouched passage.
- Never replace, ignore, or reinterpret the user's current prompt merely because requested motion is not visible in a still frame or differs from reference-frame guidance.
- Do not introduce a new action, camera path, transformation, setting, or visual fact from secondary evidence unless the refinement instructions request it or it is strictly necessary to repair a physical contradiction.
- Keep existing cue content attached to the same timestamp unless the private instructions explicitly request timing changes.
- The refinement instructions are private editing directions. Never quote, summarize, mention, or append them inside the production prompt.
- Return the same strict transport JSON contract containing only the refined production prompt.

CURRENT EDITOR PROMPT:
{current_prompt.strip()}

PRIVATE REFINEMENT INSTRUCTIONS:
{refinement_instructions.strip() or 'Improve clarity, motion continuity, causal flow, and production readiness without changing the creative intent.'}
"""
    raw = _provider_raw(inputs, provider, model, api_key, rules, timeout)
    return _extract_minimax_h3_prompt(raw)


def refine_minimax_h3_reference_prompt(segments: list[Segment], provider: str, model: str, api_key: str, intent: str, global_prompt: str, sfx: bool, spoken_dialog: bool, reduce_music: bool, current_prompt: str, refinement_instructions: str, timeout: int = 400, reference_images: list | None = None) -> str:
    """Refine the current reference brief using the client-selected workflow."""
    if not segments:
        raise ValueError("Add at least one timeline item before refining a MiniMax H3 reference prompt.")
    if not current_prompt.strip():
        raise ValueError("Write or generate a MiniMax H3 reference prompt before refining it.")
    inputs = _minimax_reference_inputs(segments, provider, reference_images, refinement=True)
    rules = _minimax_h3_reference_rules(segments, intent, global_prompt, sfx, spoken_dialog, reduce_music, reference_images)
    rules += f"""

REFERENCE-MODE REFINEMENT — FOLLOW THIS PRIORITY ORDER:
1. PRIVATE REFINEMENT INSTRUCTIONS are the highest-priority edit request.
2. CURRENT EDITOR PROMPT is authoritative. Refine it instead of rebuilding it from the reference assets.
3. Use the compact production-brief format while preserving the edited content, reference roles, timing, speakers and dialogue. Convert legacy formatting without adding actions or discarding user edits.
4. Timeline assets and prompts are secondary continuity evidence only and must not override items 1 or 2.

- Make the smallest complete edits required by the private instructions.
- Use the current client asset map for Image1/Video1 labels. When converting an older prompt, retain the same source-to-subject relationships; do not mistake a subject label for an asset index.
- Never quote or expose the private refinement instructions in the production prompt.
- Return strict transport JSON containing only the refined prompt.

CURRENT EDITOR PROMPT:
{current_prompt.strip()}

PRIVATE REFINEMENT INSTRUCTIONS:
{refinement_instructions.strip() or 'Improve reference clarity, action continuity, camera direction, and production detail without changing creative intent.'}
"""
    raw = _provider_raw(inputs, provider, model, api_key, rules, timeout)
    return _extract_minimax_h3_prompt(raw)


def _extract_minimax_h3_prompt(raw: str) -> str:
    """Extract the MiniMax prompt without second-guessing its creative content."""
    result = _parse_json(raw)
    if not isinstance(result, dict):
        raise AIResponseFormatError("The AI returned an invalid MiniMax H3 response. The operation will retry.")
    prompt = result.get("prompt") or result.get("minimaxPrompt") or result.get("minimax_prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise AIResponseFormatError("The AI returned no MiniMax H3 prompt. The operation will retry.")
    return prompt.strip()


def _minimax_frame_slots(segments: list[Segment]) -> tuple[list[str], list[tuple[str, str, bool]]]:
    """Return stable transport keys and chronological timestamps for action/bridge prose."""
    starts = []
    cursor = 0.0
    for segment in segments:
        starts.append(cursor)
        cursor += segment.duration
    visual_indices = [index for index, segment in enumerate(segments) if segment.kind in ("image", "video")]
    bridge_after = {}
    for earlier, later in zip(visual_indices, visual_indices[1:]):
        preceding = later - 1
        bridge_after[preceding] = _minimax_timestamp((starts[preceding] + starts[later]) / 2)
    slots = []
    lines = []
    for index, segment in enumerate(segments):
        timestamp = _minimax_timestamp(starts[index])
        key = f"cue_{index + 1}"
        slots.append((key, timestamp, False))
        sentences, words = minimax_interval_detail_standard(segment.duration)
        lines.append(f'{key} at {timestamp}: {sentences}+ complete sentences, {words}+ words of action prose')
        if index in bridge_after:
            bridge_key = f"bridge_after_{index + 1}"
            slots.append((bridge_key, bridge_after[index], True))
            lines.append(f'{bridge_key} at {bridge_after[index]}: bridge prose linking the adjacent visual checkpoints')
    return lines, slots


def _assemble_minimax_h3_frames(raw: str, segments: list[Segment]) -> str:
    """Place model-written prose in fixed slots without parsing or rewriting its language."""
    value = _parse_json(raw)
    if not isinstance(value, dict):
        raise AIResponseFormatError("The AI returned an invalid MiniMax Frames response. The operation will retry.")
    _, slots = _minimax_frame_slots(segments)
    required = ("opening", *(key for key, _, _ in slots), "soundscape", "music")
    missing = [key for key in required if not isinstance(value.get(key), str) or not value[key].strip()]
    if missing:
        raise AIResponseFormatError(f"The AI omitted MiniMax Frames field(s): {', '.join(missing)}. The operation will retry.")
    lines = ["continuous_video:", value["opening"].strip()]
    for key, timestamp, bridge in slots:
        lines.append(f'{timestamp} {"Frame bridge: " if bridge else ""}{value[key].strip()}')
    lines.extend(("", "soundscape:", value["soundscape"].strip(), "", "music:", value["music"].strip()))
    return "\n".join(lines)


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


def minimax_h3_cache_key(segments: list[Segment], provider: str, model: str, intent: str, global_prompt: str, sfx: bool, spoken_dialog: bool, reduce_music: bool, reference_images: list | None = None) -> str:
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
        "schema": 2,
        "referenceImages": reference_slots(reference_images),
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


def _minimax_h3_inputs(segments: list[Segment], provider: str = "gemini", refinement: bool = False, frame_checkpoints: bool = False) -> list[dict]:
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
        elif frame_checkpoints:
            continuity_function = "visual checkpoint already reached at its cue timestamp; develop toward it before that timestamp and carry its state forward afterward"
        elif item.role == "end":
            continuity_function = "visual target to approach progressively and resolve by this interval's end"
        else:
            continuity_function = "continuity checkpoint to reach progressively from the preceding interval"
        value = {
            "name": item.name,
            "role": item.role,
            "frame_mode_checkpoint": frame_checkpoints and item.kind == "image",
            "kind": item.kind,
            "start_time": start,
            "end_time": end,
            "duration": item.duration,
            "recommended_detail": {
                "minimum_complete_sentences": minimax_interval_detail_standard(item.duration)[0],
                "minimum_words": minimax_interval_detail_standard(item.duration)[1],
                "purpose": "duration-scaled MiniMax motion specificity; do not print these counts in the production prompt",
            },
            "camera_continuity_requirement": (
                "Compare this frame's camera angle, position, subject scale, screen placement, background, pose, and action with adjacent frames. "
                "For each pair of successive visual checkpoints, plan exactly one timed Frame bridge before the next visual checkpoint; "
                "keep the bridge concise for near-identical states and describe observed video motion instead of inventing another movement."
            ),
            "action_trajectory_requirement": (
                "Use this frame in context with every earlier and later frame to direct the complete action path through this interval. Describe the "
                "observable intermediate stages that connect adjacent frame states, including onset, progressive change, physical cause, material or "
                "anatomical response, momentum carried across the cue, and the exact state reached at the next frame; never jump directly between states."
            ),
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
            if frame_checkpoints:
                source_start = max(0.0, float(item.trim_start or 0) / 24)
                value["continuity_function"] += (
                    f"; only source time {source_start:.3f}s to {source_start + item.duration:.3f}s "
                    "belongs to this timeline item; preceding source footage is context, not an earlier timeline cue"
                )
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


def _minimax_h3_rules(segments: list[Segment], intent: str, global_prompt: str, sfx: bool, spoken_dialog: bool, reduce_music: bool, structured: bool = False) -> str:
    """Build a short, explicit interleaved cue template for MiniMax Frames mode."""
    timestamps = []
    starts = []
    cursor = 0.0
    for item in segments:
        starts.append(cursor)
        timestamps.append(_minimax_timestamp(cursor))
        cursor += item.duration
    visual_indices = [index for index, item in enumerate(segments) if item.kind in ("image", "video")]
    bridge_after = {}
    for previous, following in zip(visual_indices, visual_indices[1:]):
        # Put the bridge in the last interval before the next visual checkpoint.
        # This keeps text-only action cues in their original chronological order.
        preceding = following - 1
        bridge_time = (starts[preceding] + starts[following]) / 2
        bridge_after[preceding] = _minimax_timestamp(bridge_time)
    cue_lines = []
    for index, item in enumerate(segments):
        sentences, words = minimax_interval_detail_standard(item.duration)
        cue_lines.append(f"{timestamps[index]} {{describe the interval's action in at least {sentences} complete sentences and {words} words}}")
        if index in bridge_after:
            cue_lines.append(f"{bridge_after[index]} Frame bridge: {{describe the supported progression toward the next visual checkpoint}}")
    cue_template = "\n".join(cue_lines)
    field_lines, slots = _minimax_frame_slots(segments)
    if structured:
        example = {key: "your prose for this field" for key in ("opening", *(key for key, _, _ in slots), "soundscape", "music")}
        output_contract = f"""Return ONE JSON object with the following named prose fields. Write text only: no timestamps, section headings, or `Frame bridge:` labels inside field values. The app places each field at its fixed timestamp and assembles the final prompt. Supply every field exactly once.
opening: one concise sentence with stable scene and opening camera view
{chr(10).join(field_lines)}
soundscape: supported sounds or `None specified.`
music: requested music or `None. Use only the described diegetic soundscape.`

JSON shape:
{json.dumps(example, ensure_ascii=False)}"""
    else:
        output_contract = f"""OUTPUT TEMPLATE — return these three lowercase sections in order:
continuous_video:
{{one concise sentence with the stable subject, scene, and opening camera view}}
{cue_template}

soundscape:
{{write only the supported soundscape or the required None statement}}

music:
{{write only the requested music or the required None statement}}

Return strict transport JSON with exactly one top-level field, containing the completed three-section prompt and nothing else:
{{"prompt": "the complete MiniMax H3 production prompt"}}"""
    sound_rule = (
        "Describe supported ambience, physical sounds, and clearly audible source-video audio."
        if sfx else
        "Preserve clearly audible source-video audio or explicitly supplied sounds; otherwise write `None specified.`"
    )
    dialog_rule = (
        "Preserve supplied words, delivery, language, and lip sync."
        if spoken_dialog else
        "Do not invent dialogue; retain explicitly supplied words only."
    )
    music_rule = (
        "Write `None. Use only the described diegetic soundscape.` unless music is explicitly supplied."
        if reduce_music else
        "Describe explicitly requested music; otherwise use music only when the supplied context requires it."
    )
    return f"""You write one detailed, continuous MiniMax H3 video prompt for the complete ordered timeline. Inspect all supplied images, source videos, segment prompts, Director's Intent, and global prompt before writing. Use only supported visual and audio facts. Each timestamp describes a moment in the same evolving scene; never treat a frame as a cut or a reset.

TIMELINE: {len(segments)} action cues; {len(visual_indices)} visual checkpoints; {len(bridge_after)} required Frame bridges; total {cursor:.2f} seconds. Keep every cue and bridge in its assigned slot. Do not omit or merge slots, and do not add cuts or change the timeline.

ACTION: Follow each segment's current LTX prompt and the entire frame sequence. Describe what starts moving, its visible intermediate stages, physical cause, contact, weight, material response, and what carries into the next cue. Give each action cue its requested sentence and word detail without repeating the whole scene.

BRIDGES: There is exactly one Frame bridge between every successive pair of visual checkpoints, including video-to-image and nearly identical image pairs. Fill ALL of them. Compare both checkpoints as a whole: camera, subject, pose, anatomy, expression, objects, clothing, lighting, and setting. Describe how A progresses toward B during the existing time span. Start and carry substantial changes BEFORE the next visual checkpoint; the image at its timestamp already shows its reached state. For a large change, give its camera path or physical action stages; for a small change, briefly describe supported continued motion or stability. If a source video already shows the transition, describe that observed motion at the bridge time. Never invent a zoom, turn, fall, or other event to fill a bridge. Keep simultaneous camera and subject action moving together. Do not move the fixed frame timestamps or add time.

DIRECTOR'S INTENT:
{intent.strip() or 'No additional director intent supplied.'}

GLOBAL CONTINUITY PROMPT:
{global_prompt.strip() or 'No global prompt supplied.'}

AUDIO DIRECTION: {sound_rule} {dialog_rule} {music_rule}

{output_contract}"""

def _minimax_h3_reference_rules(segments: list[Segment], intent: str, global_prompt: str, sfx: bool, spoken_dialog: bool, reduce_music: bool, reference_images: list | None = None) -> str:
    return reference_rules(segments, intent, global_prompt, sfx, spoken_dialog, reduce_music, reference_images)


def _minimax_reference_inputs(segments: list[Segment], provider: str, reference_images: list | None = None, refinement: bool = False) -> list[dict]:
    # Reuse media transport, but none of the Frames-mode instructions or quotas.
    timeline_media = _minimax_h3_inputs(segments, provider)
    references = reference_slots(reference_images)
    inputs = []
    for record in reference_inventory(segments, references):
        item = {**record, "reference_mode_input": True}
        if "timeline_index" in record:
            media = timeline_media[record["timeline_index"]]
            for key in ("image", "video", "video_frames", "video_analysis_mode", "video_number"):
                if key in media:
                    item[key] = media[key]
        else:
            item["image"] = reference_image_for_provider(references[record["reference_slot"]]["image"])
        if refinement:
            item["guidance_priority"] = "SECONDARY CONTINUITY EVIDENCE ONLY"
        item["prompt_label"] = "TIMELINE ACTION DIRECTION"
        inputs.append(item)
    return inputs


def _minimax_reference_header(item: dict) -> str:
    metadata = {key: value for key, value in item.items() if key not in {"image", "video", "video_frames"}}
    return "REFERENCE INPUT: " + json.dumps(metadata, ensure_ascii=False)


def _minimax_reference_timestamp(seconds: float) -> str:
    timestamp = _minimax_timestamp(seconds)
    head, milliseconds = timestamp.rsplit(":", 1)
    return f"{head}.{milliseconds}"


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
            "IMAGE CHECKPOINT AT REQUIRED CUE" if item.get("frame_mode_checkpoint") else
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
        parts.append({"text": _minimax_reference_header(item) if item.get("reference_mode_input") else f"SEGMENT {index} OF {len(images)} — {label}{picture}{video} — {item['name']}{timing}{cue}{next_cue}{continuity}{source_duration}{analysis_mode}"})
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
            "IMAGE CHECKPOINT AT REQUIRED CUE" if item.get("frame_mode_checkpoint") else
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
        content.append({"type": "input_text", "text": _minimax_reference_header(item) if item.get("reference_mode_input") else f"SEGMENT {index} OF {len(images)} — {label}{picture}{video} — {item['name']}{timing}{cue}{next_cue}{continuity}{source_duration}{analysis_mode}"})
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


def provider_error_message(error: Exception) -> str:
    """Translate provider HTTP failures into actionable dialog text."""
    if isinstance(error, AIResponseFormatError):
        return re.sub(r"\s+(?:The operation|Magic Build) will retry\.$", "", str(error)).strip()
    if isinstance(error, requests.HTTPError) and error.response is not None:
        response = error.response
        status = response.status_code
        detail = ""
        try:
            payload = response.json()
            provider_error = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(provider_error, dict):
                detail = " ".join(str(provider_error.get("message") or "").split())
        except (ValueError, requests.JSONDecodeError):
            detail = ""
        if status == 503:
            message = (
                "Google Gemini is temporarily overloaded for the selected model. This is a provider-capacity issue, "
                "not a problem with your timeline or prompt. Wait a few minutes and try again, or choose another "
                "Gemini model in Settings."
            )
            return f"{message}\n\nGoogle response (HTTP {status}): {detail}" if detail else f"{message}\n\nHTTP {status}"
        message = f"Google Gemini returned HTTP {status}."
        if detail:
            message = f"{message}\n\nGoogle response: {detail}"
        return message
    return str(error)
