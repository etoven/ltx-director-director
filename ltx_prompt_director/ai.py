from __future__ import annotations

from . import response_schemas

import hashlib
import math
import json
import re
from functools import lru_cache
from .prompt_templates import CONTENT_FIDELITY, FRAME_MOTION_GUIDANCE, WORKSPACE_PROMPTS as WORKSPACE_INSTRUCTIONS, render_workspace_prompt
from pathlib import Path

import requests

from .media import data_url, video_storyboard_data_urls
from .project_archive import materialize_source
from .models import Segment
from .ai_stream import send_stream, streaming_enabled
from .minimax_reference import reference_image_for_provider, reference_inventory, reference_rules, reference_slots, timeline_motion_context, WORKFLOW_NAMES, WORKFLOW_DIRECTIONS, detect_workflow


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
    rules += f'\n\nRequired transport: exactly {len(segments)} segment records in input order. Return JSON with segments (each duration, prompt) and globalPrompt. Each duration must be positive and finite. Preserve all media and segment identities.'
    with response_schemas.using(response_schemas.sequence(len(segments))):
        if provider == "openai":
            return _openai(images, api_key, rules, timeout, sfx, spoken_dialog, model)
        if provider == "chatgpt":
            raw = _openai_raw(images, api_key, rules, timeout, model=model, chatgpt=True)
            return _validate(raw, len(images), sfx, spoken_dialog)
        return _gemini(images, api_key, model, rules, timeout, sfx, spoken_dialog)


def refine_sequence_prompts(segments: list[Segment], provider: str, model: str,
                            api_key: str, intent: str, global_prompt: str,
                            sfx: bool, spoken_dialog: bool, hdr: bool,
                            reduce_music: bool, timeout: int = 400) -> dict:
    """Magic Build refinement across ALL segments without touching media identities.

    The upper Global editor is the director's instruction set. Apply its
    /refine and /refine-global notes across the whole ordered timeline, and
    return exactly the segment count and globalPrompt that Magic Build uses.
    """
    if not segments:
        raise ValueError("Add at least one sequence segment before global refinement.")
    existing = [{
        "id": item.id, "name": item.name, "kind": item.kind, "role": item.role,
        "duration": item.duration, "prompt": item.prompt,
        "imagePrompt": item.image_prompt,
    } for item in segments]
    images = [_segment_input(item) for item in segments]
    rules = _rules(len(images), intent, sfx, spoken_dialog, hdr, reduce_music,
                   sum(item.kind == "text" for item in segments))
    rules += (
        "\n\nSEQUENCE-WIDE MAGIC BUILD REFINEMENT: The Director's Intent "
        "(GLOBAL scope) contains change instructions for the ENTIRE sequence. "
        "Read and apply all /refine and /refine-global annotations as edits, "
        "not narrative text. Consider EVERY supplied segment and its current "
        "prompt, imagePrompt, duration, media role, and previous/next continuity. "
        "Change as little as possible beyond the director's explicitly "
        "requested changes. Retain original media, identities, ordering, and "
        "count; do not invent any files. The returned segments array must have "
        "exactly the same number of entries in the same order. "
        "Refine prompts and timings across segments as appropriate to meet "
        "the GLOBAL creative direction, including spoken-dialog continuity. "
        "Return exactly JSON with globalPrompt and segments containing "
        "prompt, imagePrompt (empty when unchanged) and positive duration. "
        "\nCURRENT GLOBAL PRODUCTION NOTES:\n" + (global_prompt.strip() or "None") +
        "\nAUTHORITATIVE CURRENT TIMELINE (ALL SEGMENTS):\n"
        + json.dumps(existing, ensure_ascii=False)
    )
    result = _validate(
        _structured_raw(images, provider, model, api_key, rules, timeout, schema=response_schemas.sequence(len(segments))),
        len(images), sfx, spoken_dialog)
    return result



UNIFIED_PROMPT_TRANSPORT = '\n\nRESPONSE TRANSPORT: Return strict JSON with a single nonempty "prompt" string containing the complete editable production prompt in the workspace format. Do not return timed_actions, segment records or a timing reconciliation plan. The timeline is authoritative; describe its existing timing without changing segment count, duration, order or identity. Prompt editing never rebuilds the timeline. This response contract supersedes older structured timed-plan instructions.'


def combined_timeline_contract(segments: list[Segment]) -> str:
    """Fresh production synthesis must follow the current plan, not old notes."""
    plan, cursor = [], 0.0
    for index, item in enumerate(segments):
        end = cursor + item.duration
        record = {"index": index + 1, "id": item.id, "name": item.name,
                  "kind": item.kind, "role": item.role, "start": round(cursor, 6),
                  "end": round(end, 6), "duration": item.duration,
                  "prompt": item.prompt, "imagePrompt": item.image_prompt}
        if item.kind == "image":
            record["frameAnchorTime"] = round(end if item.role == "end" else cursor, 6)
        if item.kind == "video":
            start = max(0.0, float(item.trim_start or 0) / 24)
            record.update(sourceStart=start, sourceEnd=start + item.duration)
        plan.append(record)
        cursor = end
    return (
        "\n\nCURRENT TIMELINE — AUTHORITATIVE GENERATION PLAN\n"
        "Generate the complete production brief afresh from EVERY record below, "
        "in this exact order. These are the current edited timeline segments, "
        "including split, inserted, reordered and rewritten beats. Include each "
        "beat's action, performance, dialogue and resulting state at its current "
        "time. Never omit an intermediate or final beat, collapse split segments "
        "because their images match, or revive deleted/replaced actions. A beat "
        "does not require a camera cut: preserve continuous-shot direction when "
        "appropriate while still describing its complete action progression. "
        "AUTHORITY: The current segment prompts, order and durations override "
        "conflicting older global direction, narrative examples and source-media "
        "events. In particular, discard an old stopping point or narrative "
        "restriction when the current later segments explicitly progress beyond it. "
        "Use Director's Intent and global notes only where compatible with this "
        "plan, retaining compatible identity, setting, style and sound guidance. "
        "Images provide visual anchors; they must not replace authored motion "
        "or suppress new segment text. For video, only its sourceStart/sourceEnd "
        "interval belongs to that beat; other source footage must not replace "
        "the subsequent timeline. ONE-FOR-ONE CONTENT TRANSFER: This is a "
        "formatting and integration task, not a summary or creative rewrite. "
        "Carry over ALL substantive segment text, retaining its wording wherever "
        "possible. Change only the section placement, reference/speaker notation, "
        "dialogue markup, timing notation and minimal connective phrasing needed "
        "by the workspace's production format. Preserve every individual action "
        "and intermediate physical change, order and simultaneity, body/object "
        "detail, expression, reaction, camera instruction, lighting/material "
        "qualifier, exact spoken words, language/accent/delivery, synchronized "
        "sound, negative constraint and ending state. Shared context may be "
        "consolidated only if its complete meaning and scope survive; distinct "
        "actions or details must never be merged away. Preserve imagePrompt "
        "appearance details as well as prompt action details, resolving changed "
        "states by the current segment action. COMPLETENESS OVERRIDES LENGTH: "
        "Ignore any earlier word-count target, including 350–500 words, sentence "
        "quota, compactness or brevity instruction that would discard content. "
        "Use as much output as the complete source requires; target video duration "
        "does not impose a prose length limit. Never compress the timeline to fit "
        "an assumed prompt or context budget. Keep all authored details and reduce "
        "optional added commentary instead. Keep the workspace's production "
        "sections and syntax, expanding their content as needed. Before returning, "
        "compare EVERY source sentence/clause with the resulting production text "
        "and restore any omitted detail. Check the resulting prose against ALL records, "
        "especially distinct beats sharing the same media. Return only the "
        "production-prompt response; never change the timeline.\n"
        + json.dumps(plan, ensure_ascii=False)
    )


def build_minimax_h3_prompt(segments: list[Segment], provider: str, model: str, api_key: str, intent: str, global_prompt: str, sfx: bool, spoken_dialog: bool, reduce_music: bool, timeout: int = 400, reference_images: list | None = None) -> str:
    """Synthesize the complete ordered timeline into one MiniMax H3 prompt."""
    if not segments:
        raise ValueError("Add at least one timeline item before exporting a MiniMax H3 prompt.")
    inputs = _minimax_frames_inputs(segments, provider, reference_images)
    rules = _minimax_h3_rules(segments, intent, global_prompt, sfx, spoken_dialog, reduce_music, reference_images)
    rules += "\nCURRENT MEDIA CONTRACT: The asset map may include uploaded reference audio and video soundtracks. Audio-only video samples condition sound only. Follow the supplied labels and observation availability; never invent unheard audio or visual observations. This overrides legacy template text saying audio references are unavailable."
    rules += UNIFIED_PROMPT_TRANSPORT + combined_timeline_contract(segments)
    raw = _structured_raw(inputs, provider, model, api_key, rules, timeout, schema=response_schemas.PROMPT)
    return _extract_minimax_h3_prompt(raw, segments)


def build_minimax_h3_reference_prompt(segments: list[Segment], provider: str, model: str, api_key: str, intent: str, global_prompt: str, sfx: bool, spoken_dialog: bool, reduce_music: bool, timeout: int = 400, reference_images: list | None = None) -> str:
    """Write a reference brief using the client-detected workflow and asset map."""
    if not segments:
        raise ValueError("Add at least one timeline item before generating a MiniMax H3 reference prompt.")
    inputs = _minimax_reference_inputs(segments, provider, reference_images)
    rules = _minimax_h3_reference_rules(segments, intent, global_prompt, sfx, spoken_dialog, reduce_music, reference_images)
    rules += "\nCURRENT MEDIA CONTRACT: The asset map may include uploaded reference audio and video soundtracks. Audio-only video samples condition sound only. Follow the supplied labels and observation availability; never invent unheard audio or visual observations. This overrides legacy template text saying audio references are unavailable."
    rules += UNIFIED_PROMPT_TRANSPORT + combined_timeline_contract(segments)
    raw = _structured_raw(inputs, provider, model, api_key, rules, timeout, schema=response_schemas.PROMPT)
    return _extract_minimax_h3_prompt(raw, segments)


def refine_minimax_text_prompt(provider: str, model: str, api_key: str, current_prompt: str, timeout: int = 400) -> str:
    """Refine annotated text without receiving any timeline or media context."""
    if not current_prompt.strip():
        raise ValueError("Write or paste a MiniMax prompt before refining it.")
    rules = render_workspace_prompt('minimax_prompt_refinement', 'refine', {
        'current_prompt': current_prompt.strip(),
    })
    from .prompt_tags import prompt_annotations
    annotations = prompt_annotations(current_prompt)
    rules += '\n\nEXPLICIT ANNOTATION EDIT REQUESTS (highest priority):\n' + (annotations or 'No annotations. Improve clarity, continuity and production readiness while preserving intent.')
    rules += '\nApply every requested edit in the returned text, rather than merely retaining its instruction. A leading /refine annotation before the scene applies to the whole prompt unless it explicitly identifies a narrower passage. Requests such as "all this" refer to the complete prompt. Preservation rules do not block changes required by those requests. For a continuous-shot request, reconcile camera and action wording throughout the scene into one uninterrupted shot; remove contradictory cuts or shot transitions, and preserve reference alignment and sound unless explicitly asked to change them. Verify the resulting prose satisfies each request before returning.'
    rules += '\nRESPONSE TRANSPORT: Return strict JSON with one nonempty "prompt" string containing the complete refined text. No segment records, timed_actions or commentary.'
    raw = _structured_raw([], provider, model, api_key, rules, timeout, schema=response_schemas.PROMPT)
    return _extract_minimax_h3_prompt(raw)


def refine_minimax_h3_prompt(segments: list[Segment], provider: str, model: str, api_key: str, intent: str, global_prompt: str, sfx: bool, spoken_dialog: bool, reduce_music: bool, current_prompt: str, refinement_instructions: str, timeout: int = 400, reference_images: list | None = None) -> str:
    """Refine the user's edited MiniMax prompt without exposing private edit directions."""
    if not segments:
        raise ValueError("Add at least one timeline item before refining a MiniMax H3 prompt.")
    if not current_prompt.strip():
        raise ValueError("Write or generate a MiniMax H3 prompt before refining it.")
    inputs = _minimax_frames_inputs(segments, provider, reference_images, refinement=True)
    rules = _minimax_h3_rules(segments, intent, global_prompt, sfx, spoken_dialog, reduce_music, reference_images)
    rules += render_workspace_prompt('minimax_frames', 'refine', {
        'current_prompt': current_prompt.strip(),
        'refinement_instructions': refinement_instructions.strip() or 'Improve clarity, motion continuity, causal flow, and production readiness without changing the creative intent.',
    })
    rules += "\nCURRENT MEDIA CONTRACT: The asset map may include uploaded reference audio and video soundtracks. Audio-only video samples condition sound only. Follow the supplied labels and observation availability; never invent unheard audio or visual observations. This overrides legacy template text saying audio references are unavailable."
    rules += UNIFIED_PROMPT_TRANSPORT
    raw = _structured_raw(inputs, provider, model, api_key, rules, timeout, schema=response_schemas.PROMPT)
    return _extract_minimax_h3_prompt(raw, segments)


def refine_minimax_h3_reference_prompt(segments: list[Segment], provider: str, model: str, api_key: str, intent: str, global_prompt: str, sfx: bool, spoken_dialog: bool, reduce_music: bool, current_prompt: str, refinement_instructions: str, timeout: int = 400, reference_images: list | None = None) -> str:
    """Refine the current reference brief using the client-selected workflow."""
    if not segments:
        raise ValueError("Add at least one timeline item before refining a MiniMax H3 reference prompt.")
    if not current_prompt.strip():
        raise ValueError("Write or generate a MiniMax H3 reference prompt before refining it.")
    inputs = _minimax_reference_inputs(segments, provider, reference_images, refinement=True)
    rules = _minimax_h3_reference_rules(segments, intent, global_prompt, sfx, spoken_dialog, reduce_music, reference_images)
    rules += render_workspace_prompt('minimax_references', 'refine', {
        'current_prompt': current_prompt.strip(),
        'refinement_instructions': refinement_instructions.strip() or 'Improve reference clarity, action continuity, camera direction, and production detail without changing creative intent.',
    })
    rules += "\nCURRENT MEDIA CONTRACT: The asset map may include uploaded reference audio and video soundtracks. Audio-only video samples condition sound only. Follow the supplied labels and observation availability; never invent unheard audio or visual observations. This overrides legacy template text saying audio references are unavailable."
    rules += UNIFIED_PROMPT_TRANSPORT
    raw = _structured_raw(inputs, provider, model, api_key, rules, timeout, schema=response_schemas.PROMPT)
    return _extract_minimax_h3_prompt(raw, segments)


def _extract_minimax_h3_prompt(raw: str, segments: list[Segment] | None = None) -> str:
    """Read production text without converting it into a timeline plan."""
    result = _parse_json(raw)
    if not isinstance(result, dict):
        raise AIResponseFormatError("The AI returned an invalid production-prompt response. The operation will retry.")
    for key in ("prompt", "minimaxPrompt", "minimax_prompt", "productionPrompt", "production_prompt", "productionBrief", "production_brief"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    # Accept providers returning named prose sections; never interpret their
    # contents or extra metadata as instructions to mutate the timeline.
    sections = []
    for key, heading in (
        ("subject_definitions", "subject_definitions:"), ("summary", "summary:"),
        ("retention_analysis", "retention_analysis:"), ("detailed_description", "detailed_description:"),
        ("integrated_multimodal_description", "integrated_multimodal_description:"),
        ("frame_use", "[FRAME USE]"), ("reference_use", "[REFERENCE USE]"),
        ("continuity", "[CONTINUITY]"), ("scene", "[SCENE]"),
        ("timed_action", "[TIMED ACTION]"), ("sound", "[SOUND]"), ("avoid", "[AVOID]"),
        ("overall_soundscape", "overall_soundscape:"), ("non_diegetic_music", "non_diegetic_music:"),
    ):
        value = result.get(key)
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            value = "\n".join(value)
        if isinstance(value, str) and value.strip():
            sections.append(f"{heading}\n{value.strip()}")
    if sections:
        return "\n\n".join(sections)
    raise AIResponseFormatError("The AI response did not include a production prompt. The operation will retry.")


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
        return str(getattr(segment, "_media_digest", ""))
    stat = source.stat()
    if getattr(segment, "_materialized_stat", None) == (stat.st_size, stat.st_mtime_ns):
        return str(getattr(segment, "_media_digest", ""))
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
    images = _refinement_images(segments, selected_index)
    rules = _timing_rules(segments, selected_index, intent, requested_total)
    raw = _structured_raw(images, provider, model, api_key, rules, timeout, schema=response_schemas.TIMING)
    result = _parse_json(raw)
    if not isinstance(result, dict):
        raise AIResponseFormatError("The AI returned an invalid timing response. The operation will retry.")
    duration = _strict_duration(result.get("duration"))
    return {"duration": duration}


def generate_segment_prompt(segments: list[Segment], selected_index: int, provider: str,
                            model: str, api_key: str, intent: str, requested_total: float,
                            timeout: int = 400) -> dict:
    """Generate one selected timeline beat, not the full unified prompt."""
    if not 0 <= selected_index < len(segments):
        raise ValueError("Select a timeline segment to generate its prompt.")
    target = segments[selected_index]
    rules = (
        "Generate the motion/action prompt for ONLY the SELECTED timeline segment. "
        "Use its media and already-authored text as authoritative starting context; "
        "use adjacent images and written neighboring prompts to bridge the action "
        "without copying the entire sequence. Keep its subject, stage of transformation, "
        "environment, composition, and camera continuity grounded in supplied media. "
        "Respect any inline /refine directives in selected text as edit instructions. "
        "Never modify or return the combined/unified prompt, any other segment, or "
        "global direction. Do not change segment IDs or its media role. "
        "Return strict JSON with exactly the selected prompt, imagePrompt (empty when unchanged) "
        "and a positive finite duration: "
        '{"prompt":"generated action direction","imagePrompt":"visual description","duration":3.0}.'
        "\\nDIRECTOR / GLOBAL NOTES:\\n" + (intent.strip() or "None supplied.") +
        "\\nORDERED TIMELINE:\\n" + _segment_context(segments, selected_index) +
        f"\\nSELECTED SEGMENT INDEX: {selected_index + 1}." +
        f"\\nSELECTED PROMPT AS PROVIDED: {target.prompt or '[generate from adjacent context]'}" +
        f"\\nCURRENT DURATION: {target.duration:.2f} seconds." +
        "\\n" + _refinement_duration_instruction(requested_total)
    )
    raw = _structured_raw(_refinement_images(segments, selected_index),
                        provider, model, api_key, rules + CONTENT_FIDELITY + FRAME_MOTION_GUIDANCE, timeout, schema=response_schemas.SEGMENT)
    result = _parse_json(raw)
    if not isinstance(result, dict):
        raise AIResponseFormatError("Segment generation returned invalid JSON; retrying.")
    prompt = result.get("prompt") or result.get("segmentPrompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise AIResponseFormatError("Segment generation returned no prompt; retrying.")
    return {"prompt": prompt.strip(), "imagePrompt": _image_prompt_value(result),
            "duration": _strict_duration(result.get("duration"))}


def refine_segment_prompt(segments: list[Segment], selected_index: int, provider: str, model: str, api_key: str, intent: str, requested_total: float, timeout: int = 400) -> dict:
    """Refine only the selected prompt, with an optional selected-duration change."""
    if not 0 <= selected_index < len(segments):
        raise ValueError("Select a segment to refine its prompt.")
    selected = segments[selected_index]
    if not selected.prompt.strip():
        raise ValueError("The selected segment needs an existing prompt before it can be refined.")
    images = _refinement_images(segments, selected_index)
    rules = _prompt_refinement_rules(segments, selected_index, intent, requested_total)
    rules += '\n\nRequired response schema: {"prompt":"refined selected prompt","imagePrompt":"","duration":5.0}. Return only the selected segment, with a positive finite duration. Use an empty imagePrompt when no visual update is needed.'
    raw = _structured_raw(images, provider, model, api_key, rules, timeout, schema=response_schemas.SEGMENT)
    result = _parse_json(raw)
    if not isinstance(result, dict):
        raise AIResponseFormatError("The AI returned an invalid prompt-refinement response. The operation will retry.")
    prompt = result.get("prompt") or result.get("segmentPrompt") or result.get("segment_prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise AIResponseFormatError("The AI returned no refined prompt. The operation will retry.")
    image_prompt = _image_prompt_value(result)
    duration = _strict_duration(result.get("duration"))
    return {"prompt": prompt.strip(), "imagePrompt": image_prompt, "duration": duration}



def generate_global_direction(segments: list[Segment], provider: str, model: str,
                              api_key: str, director_notes: str, timeout: int = 400) -> dict:
    """Generate ONLY the global/intent editor's creative direction."""
    if not segments:
        raise ValueError("Add timeline segments before generating global direction.")
    rules = (
        "Generate one globally applicable Director's Intent / global prompt "
        "from the existing ordered segment sequence and any notes supplied. "
        "Preserve named subjects, intended actions, setting, chronology, "
        "continuity and image-established facts. Summarize the creative "
        "direction for use across segments, rather than rewriting each "
        "segment or producing a completed combined production prompt. "
        "No timeline changes, durations or invented media. Return strict "
        'JSON only: {"globalPrompt":"complete global direction"}.'
        "\nDIRECTOR NOTES:\n" + (director_notes.strip() or "None supplied.") +
        "\nSEGMENTS:\n" + _segment_context(segments, -1)
    )
    raw = _structured_raw([_segment_input(item) for item in segments],
                        provider, model, api_key, rules, timeout, schema=response_schemas.GLOBAL)
    result = _parse_json(raw)
    if not isinstance(result, dict) or not isinstance(result.get("globalPrompt"), str) or not result["globalPrompt"].strip():
        raise AIResponseFormatError("Global direction generation returned no prompt; retrying.")
    return {"globalPrompt": result["globalPrompt"].strip()}


def refine_global_prompt(segments: list[Segment], provider: str, model: str, api_key: str,
                         intent: str, current_prompt: str, timeout: int = 400) -> dict:
    """Refine shared direction and explicitly requested timeline timing only."""
    if not segments or not current_prompt.strip():
        raise ValueError("Write a global prompt and add segments before refining.")
    plan = [{"name": s.name, "duration": s.duration, "prompt": s.prompt, "role": s.role} for s in segments]
    rules = (
        'Refine the CURRENT GLOBAL PROMPT. Inline /refine and /refine-global requests take priority; '
        'preserve /keep, /avoid and /focus constraints. Keep all other authored content. '
        'Remove a completed /refine-global directive from the returned globalPrompt. '
        'Apply explicit timing instructions in the global prompt to the timeline. '
        'For a request to resize or conform the whole sequence proportionally to a total, '
        'return that total in proportionalTotal; the client calculates each duration from the current ratios. '
        'In that case set durations to null. Otherwise set proportionalTotal to null. '
        'For other explicit retiming requests return one positive duration per segment in input order. '
        'Without a timing request, preserve every duration. Never rewrite segment prompts, reorder, add or delete segments. '
        'The requested-length UI preference must not override the edit request. '
        'Return strict JSON: {"globalPrompt":"refined global direction", "durations":[2,3], "proportionalTotal":null}. '
        '\nDIRECTOR CONTEXT:\n' + intent + '\nCURRENT GLOBAL PROMPT:\n' + current_prompt +
        '\nCURRENT TIMELINE:\n' + json.dumps(plan, ensure_ascii=False)
    )
    result = _parse_json(_structured_raw([_segment_input(s) for s in segments], provider, model, api_key, rules, timeout, schema=response_schemas.global_refinement(len(segments))))
    if not isinstance(result, dict) or not isinstance(result.get('globalPrompt'), str) or not result['globalPrompt'].strip():
        raise AIResponseFormatError("The AI returned no global prompt. The operation will retry.")
    total = result.get('proportionalTotal')
    if total is not None:
        total = _strict_duration(total)
        units = round(total * 100)
        if units < len(segments):
            raise AIResponseFormatError("The requested total is too short for the supplied segments.")
        weights = [s.duration for s in segments]
        allocated = [max(1, int(units * weight / sum(weights))) for weight in weights]
        while sum(allocated) < units:
            index = max(range(len(weights)), key=lambda i: units * weights[i] / sum(weights) - allocated[i])
            allocated[index] += 1
        while sum(allocated) > units:
            candidates = [i for i in range(len(weights)) if allocated[i] > 1]
            index = min(candidates, key=lambda i: units * weights[i] / sum(weights) - allocated[i])
            allocated[index] -= 1
        durations = [n / 100 for n in allocated]
    else:
        values = result.get('durations')
        if not isinstance(values, list) or len(values) != len(segments):
            raise AIResponseFormatError("Global refinement must preserve the segment count. The operation will retry.")
        durations = [_strict_duration(value) for value in values]
    return {'globalPrompt': result['globalPrompt'].strip(), 'durations': durations}

def _segment_input(item: Segment) -> dict:
    value = {"name": item.name, "role": item.role, "kind": item.kind}
    if item.kind != "text" and item.preview_path:
        value["image"] = data_url(materialize_source(item) if item.kind == "image" else item.preview_path, max_edge=384)
    return value


def _minimax_timestamp(seconds: float) -> str:
    """24 fps non-drop-frame SMPTE HH:MM:SS:FF, rounded to the nearest frame."""
    total_frames = max(0, round(float(seconds) * 24))
    total_seconds, frame = divmod(total_frames, 24)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, whole_seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}:{frame:02d}"


def _minimax_h3_inputs(segments: list[Segment], provider: str = "gemini", refinement: bool = False, frame_checkpoints: bool = False) -> list[dict]:
    inputs = []
    motion_contexts = timeline_motion_context(segments)
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
        elif not seen_visual and not (item.kind == "image" and item.role == "end"):
            continuity_function = "opening visual anchor establishing the initial continuous state"
            seen_visual = True
        elif item.kind == "video":
            continuity_function = "temporal motion evidence spanning this complete timeline interval"
        elif item.kind == "image":
            continuity_function = ("end-frame target reached by this interval's end, then carried forward" if item.role == "end"
                                   else "start-frame checkpoint present at this interval's beginning; develop onward from it")
        else:
            continuity_function = "continuity checkpoint to reach progressively from the preceding interval"
        if item.kind in ("image", "video"):
            seen_visual = True
        value = {
            "segment_id": item.id,
            "timeline_index": index,
            "name": item.name,
            "role": item.role,
            "frame_mode_checkpoint": frame_checkpoints and item.kind == "image",
            "kind": item.kind,
            "motion_context": motion_contexts[index],
            "start_time": start,
            "end_time": end,
            "duration": item.duration,
            "camera_continuity_requirement": (
                "Compare this frame's camera angle, position, subject scale, screen placement, background, pose, and action with adjacent frames. "
                "Describe supported movement in the existing timed ranges; keep near-identical states stable and do not invent camera movement."
            ),
            "action_trajectory_requirement": (
                "Use this frame in context with every earlier and later frame to direct the complete action path through this interval. Describe the "
                "observable intermediate stages that connect adjacent frame states, including onset, progressive change, physical cause, material or "
                "anatomical response, momentum carried across the cue, and the exact state reached at the next frame; never jump directly between states."
            ),
            "cue_timestamp": _minimax_timestamp(end if item.kind == "image" and item.role == "end" else start),
            "next_cue_timestamp": _minimax_timestamp(end) if index + 1 < len(segments) else None,
            "continuity_function": continuity_function,
            "prompt": item.prompt.strip() or "[No existing segment prompt; infer conservatively from supplied visual and sequence context.]",
            "prompt_label": "CURRENT TIMELINE SEGMENT PROMPT — AUTHORITATIVE ACTION AND PROGRESSION",
            "image_prompt": item.image_prompt.strip(),
            "picture_number": picture_number if item.kind == "image" else None,
            "video_number": video_number if item.kind == "video" else None,
        }
        if refinement:
            value["guidance_priority"] = (
                "SECONDARY CONTINUITY EVIDENCE ONLY — do not override the private refinement instructions "
                "or rebuild the authoritative current editor prompt from this record."
            )
        source = Path(materialize_source(item)) if item.media_path and item.kind == "video" else None
        if item.kind == "video" and source and source.is_file():
            value["source_duration"] = round((item.media_duration_frames or 0) / 24, 3) or None
            value["trim_start_frame"] = item.trim_start
            if frame_checkpoints:
                source_start = max(0.0, float(item.trim_start or 0) / 24)
                value["continuity_function"] += (
                    f"; only source time {source_start:.3f}s to {source_start + item.duration:.3f}s "
                    "belongs to this timeline item; preceding source footage is context, not an earlier timeline cue"
                )
            if provider not in {"openai", "chatgpt"} and source.stat().st_size <= MAX_INLINE_VIDEO_BYTES:
                value["video"] = data_url(str(source))
                value["video_analysis_mode"] = "full source video"
            else:
                value["video_frames"] = video_storyboard_data_urls(str(source))
                value["video_analysis_mode"] = "timestamped samples across the full source video"
        if item.kind == "image" and item.media_path:
            value["image"] = data_url(materialize_source(item), max_edge=512)
        elif item.kind == "video" and not value.get("video") and not value.get("video_frames") and item.preview_path:
            value["image"] = data_url(item.preview_path, max_edge=512)
            value["video_analysis_mode"] = "single preview fallback; infer motion only from the authoritative video prompt"
        inputs.append(value)
        cursor = end
    return inputs


def _mixed_reference_input(record, source, provider):
    item = {**record, 'reference_mode_input':True}
    kind = record['kind']
    if kind == 'image':
        item['image'] = reference_image_for_provider(source['image'])
    elif kind == 'video':
        item['video_number'] = int(record['label'].removeprefix('Video'))
        if provider == 'gemini':
            item['video'] = data_url(source['path'])
            item['video_analysis_mode'] = 'complete uploaded reference video including its soundtrack'
        else:
            item['video_frames'] = video_storyboard_data_urls(source['path'])
            item['video_analysis_mode'] = 'sampled visuals; soundtrack not auditioned by this provider; use explicit audio notes only'
    else:
        audio = source.get('audio_path')
        if not audio:
            raise ValueError('The audio-only video reference has no usable soundtrack. Choose a sample with audio.')
        if provider == 'gemini':
            item['audio'] = data_url(audio)
            item['audio_analysis_mode'] = 'uploaded audio only; no visual conditioning'
        else:
            item['audio_analysis_mode'] = 'source audio is available to ComfyUI export, but this provider has not auditioned it; use explicit notes only'
    return item


def _minimax_frames_inputs(segments: list[Segment], provider: str, reference_images: list | None = None, refinement: bool = False) -> list[dict]:
    """Keep timed conditioning media separate from untimed, role-limited images."""
    inputs = _minimax_h3_inputs(segments, provider, refinement=refinement, frame_checkpoints=True)
    slots = reference_slots(reference_images)
    for record in reference_inventory(segments, slots):
        if "reference_slot" not in record:
            continue
        entry = _mixed_reference_input(record, slots[record["reference_slot"]], provider)
        if refinement:
            entry["guidance_priority"] = "SECONDARY CONTINUITY EVIDENCE ONLY"
        inputs.append(entry)
    return inputs


def _minimax_h3_rules(segments: list[Segment], intent: str, global_prompt: str, sfx: bool, spoken_dialog: bool, reduce_music: bool, reference_images: list | None = None) -> str:
    """Build a reference-style brief with authoritative frame and interval timing."""
    inventory = reference_inventory(segments, reference_images)
    intervals = "\n".join(
        f"{_minimax_timestamp(record['start_time'])} - {_minimax_timestamp(record['end_time'])}: "
        f"{record['label'] or 'Text direction'} ({record['name']}); "
        f"{('end frame reached at ' + _minimax_timestamp(record['end_time'])) if record['kind'] == 'image' and record['role'] == 'end' else ('start frame at ' + _minimax_timestamp(record['start_time'])) if record['kind'] == 'image' else 'timed source video' if record['kind'] == 'video' else 'text action'}; "
        f"segment direction: {record['prompt'] or 'none supplied'}"
        for record in inventory if "timeline_index" in record
    )
    references = [record for record in inventory if "reference_slot" in record]
    reference_map = "\n".join(
        f"{record['label']} ({record['name']}): untimed {record['role']} reference"
        f"; notes: {record['notes'] or 'none'}"
        for record in references
    ) or "No untimed reference images supplied."
    return render_workspace_prompt('minimax_frames', 'generate', {
        'asset_map': json.dumps(inventory, ensure_ascii=False, indent=2),
        'workflow_name': WORKFLOW_NAMES[detect_workflow(segments, reference_images)],
        'workflow_direction': WORKFLOW_DIRECTIONS[detect_workflow(segments, reference_images)],
        'intervals': intervals,
        'reference_map': reference_map,
        'total_duration': format(sum((item.duration for item in segments)), '.3f'),
        'director_intent': intent.strip() or 'Not supplied.',
        'global_direction': global_prompt.strip() or 'Not supplied.',
        'sound_effects': 'Describe supported physical sounds and ambience.' if sfx else 'Only explicitly supplied or clearly audible source sounds; otherwise none specified.',
        'dialogue': 'Spoken dialogue is requested: preserve supplied exact lines; if none are supplied, write suitable original spoken lines with speakers, language and delivery.' if spoken_dialog else 'Do not add dialogue; retain explicitly supplied words only.',
        'music': 'No background music unless explicitly requested.' if reduce_music else 'Use music only if supplied direction calls for it.',
    })

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
            for key in ("segment_id", "motion_context", "image", "video", "video_frames", "video_analysis_mode", "video_number"):
                if key in media:
                    item[key] = media[key]
        else:
            item.update(_mixed_reference_input(record, references[record["reference_slot"]], provider))
        if refinement:
            item["guidance_priority"] = "SECONDARY CONTINUITY EVIDENCE ONLY"
        item["prompt_label"] = "TIMELINE ACTION DIRECTION"
        inputs.append(item)
    return inputs


def _minimax_reference_header(item: dict) -> str:
    metadata = {key: value for key, value in item.items() if key not in {"image", "video", "video_frames", "audio"}}
    return "REFERENCE INPUT: " + json.dumps(metadata, ensure_ascii=False)


def _minimax_reference_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(float(seconds) * 1000))
    minutes, remainder = divmod(milliseconds, 60_000)
    whole_seconds, fraction = divmod(remainder, 1000)
    return f"{minutes:02d}:{whole_seconds:02d}.{fraction:03d}"


def _refinement_images(segments: list[Segment], selected_index: int) -> list[dict]:
    start = max(0, selected_index - 1)
    end = min(len(segments), selected_index + 2)
    motion_contexts = timeline_motion_context(segments)
    indices = set(range(start, end))
    # Text directions between checkpoints must not hide the actual visual pair.
    for side in ("previous_visual", "next_visual"):
        anchor = motion_contexts[selected_index][side]
        if anchor is not None:
            indices.add(anchor["timeline_index"])
    return [
        {
            "name": f"Segment {index + 1} {'SELECTED' if index == selected_index else ('PREVIOUS' if index < selected_index else 'NEXT')} — {item.name}",
            "role": item.role,
            "kind": item.kind,
            "selected": index == selected_index,
            "segment_id": item.id,
            "timeline_index": index,
            "motion_context": motion_contexts[index],
            "prompt": item.prompt,
            **({"image": data_url(materialize_source(item) if item.kind == "image" else item.preview_path, max_edge=384)} if item.kind != "text" and item.preview_path else {}),
        }
        for index in sorted(indices) for item in [segments[index]]
    ]


def _segment_context(segments: list[Segment], selected_index: int) -> str:
    records = []
    motion_contexts = timeline_motion_context(segments)
    for index, segment in enumerate(segments):
        relation = "SELECTED" if index == selected_index else ("PREVIOUS" if index == selected_index - 1 else ("NEXT" if index == selected_index + 1 else "SEQUENCE CONTEXT"))
        context = motion_contexts[index]
        anchor = "TEXT-ONLY SEGMENT" if segment.kind == "text" else "SOURCE VIDEO" if segment.kind == "video" else f"{segment.role.upper()} FRAME"
        if segment.kind == "image":
            anchor += f" anchored at {context['current_visual']['anchor_time']:.3f}s"
        records.append(
            f"Segment {index + 1} [{relation}; {anchor}; current duration {segment.duration:.2f}s; "
            f"interval {context['interval_start']:.3f}–{context['interval_end']:.3f}s]\n"
            f"Existing prompt (immutable unless SELECTED prompt refinement): {segment.prompt or '[empty]'}"
        )
    return "\n\n".join(records)


def _refinement_duration_instruction(requested_total: float) -> str:
    guidance = "Choose any positive selected-segment duration needed for the action, with two decimal places of precision."
    if requested_total > 0:
        guidance += (f" The existing {requested_total:.2f}s requested total is a planning preference, not a hard limit on this refinement. "
                     "Preserve all other segment durations; let the sequence total grow or shrink when this action needs it.")
    return guidance


def _timing_rules(segments: list[Segment], selected_index: int, intent: str, requested_total: float) -> str:
    duration_instruction = _refinement_duration_instruction(requested_total)
    return f"""You are performing a TIMING-ONLY refinement for LTX Video 2.3.
Analyze the complete ordered segment plan below so the selected segment still fits the sequence. Use the immediately previous and next prompts and supplied adjacent frames as the primary motion and continuity context.
Change ONLY the duration of selected segment {selected_index + 1}. Every prompt is immutable: do not rewrite, summarize or return any prompt text. Do not change any other duration.
Estimate the time genuinely needed for the selected prompt's action, physical progression, camera motion, Spoken Dialog and lip sync. Respect its start/end-frame role and surrounding continuity. There is no segment-duration or total sequence-length ceiling.
{duration_instruction}
Director's intent and planning controls:
{intent.strip() or 'No additional intent supplied.'}

ORDERED EXISTING PLAN:
{_segment_context(segments, selected_index)}

Return strict JSON containing only: {{"duration": 5.0}}""" + FRAME_MOTION_GUIDANCE


def _prompt_refinement_rules(segments: list[Segment], selected_index: int, intent: str, requested_total: float) -> str:
    duration_instruction = _refinement_duration_instruction(requested_total)
    return render_workspace_prompt('ltx', 'refine', {
        'selected_segment': selected_index + 1,
        'duration_instruction': duration_instruction,
        'director_intent': intent.strip() or 'No additional intent supplied.',
        'ordered_plan': _segment_context(segments, selected_index),
    })


def _strict_duration(value: object) -> float:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", str(value or ""))
    if not match:
        raise AIResponseFormatError("The AI returned an invalid refined duration. The operation will retry.")
    duration = float(match.group(1))
    if duration <= 0:
        raise AIResponseFormatError("The AI returned a non-positive duration. The operation will retry.")
    return round(duration, 2)


def _image_prompt_value(value: dict) -> str:
    prompt = value.get("imagePrompt") or value.get("image_prompt")
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
    return render_workspace_prompt('ltx', 'generate', {
        'count': count,
        'authoritative_intent': authoritative_intent,
        'frame_planning_rule': frame_planning_rule,
        'duration_rule': duration_rule,
        'audio': audio,
        'global_format': global_format,
    })


def _gemini(images: list[dict], key: str, model: str, rules: str, timeout: int, sfx: bool, spoken_dialog: bool) -> dict:
    raw = _gemini_raw(images, key, model, rules, timeout)
    return _validate(raw, len(images), sfx, spoken_dialog)


def _provider_raw(images: list[dict], provider: str, model: str, key: str, rules: str, timeout: int) -> str:
    if provider == "openai":
        return _openai_raw(images, key, rules, timeout, model=model)
    if provider == "chatgpt":
        return _openai_raw(images, key, rules, timeout, model=model, chatgpt=True)
    return _gemini_raw(images, key, model, rules, timeout)


def _structured_raw(images, provider, model, key, rules, timeout, *, schema):
    """Bind an explicit operation contract for this worker's entire request."""
    with response_schemas.using(schema):
        return _provider_raw(images, provider, model, key, rules, timeout)


def _checked_response(raw: str) -> str:
    try:
        response_schemas.validate(raw)
    except ValueError as error:
        raise AIResponseFormatError(
            f"The AI response did not match the {response_schemas.current()['title']} schema "
            f"({error}). The result was not applied; the operation will retry.") from error
    return raw


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
        next_cue = f" — INTERVAL END BOUNDARY {item['next_cue_timestamp']}" if item.get("next_cue_timestamp") else " — FINAL INTERVAL"
        continuity = f" — CONTINUITY ROLE: {item['continuity_function']}" if item.get("continuity_function") else ""
        analysis_mode = f" — {item['video_analysis_mode']}" if item.get("video_analysis_mode") else ""
        source_duration = f" — source clip {float(item['source_duration']):.3f}s" if item.get("source_duration") else ""
        parts.append({"text": _minimax_reference_header(item) if item.get("reference_mode_input") else f"SEGMENT {index} OF {len(images)} — {label}{picture}{video} — {item['name']}{timing}{cue}{next_cue}{continuity}{source_duration}{analysis_mode}"})
        if item.get("motion_context") and not item.get("reference_mode_input"):
            parts.append({"text": "TIMED FRAME MOTION CONTEXT: " + json.dumps(item["motion_context"], ensure_ascii=False)})
        if item.get("audio"):
            mime, encoded = re.match(r"^data:([^;]+);base64,(.+)$", item["audio"], re.S).groups()
            parts.append({"inline_data": {"mime_type": mime, "data": encoded}})
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
    if streaming_enabled():
        return _checked_response(_gemini_stream(parts, key, model, timeout))
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key, "content-type": "application/json"},
        json={"contents": [{"role": "user", "parts": parts}], "generationConfig": response_schemas.gemini_config()},
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    return _checked_response(_gemini_response_text(data))



def _gemini_stream(parts: list[dict], key: str, model: str, timeout: int) -> str:
    """Stream public Gemini text and opt-in thought summaries when supported."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"
    base = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": response_schemas.gemini_config()}
    payload = json.loads(json.dumps(base))
    payload["generationConfig"]["thinkingConfig"] = {"includeThoughts": True}
    chunks: list[str] = []
    # A non-thinking model may reject includeThoughts; retry transparently.
    for pass_number in range(2):
        with requests.post(url, headers={"x-goog-api-key": key, "content-type": "application/json"},
                           json=payload, timeout=timeout, stream=True) as response:
            if getattr(response, "status_code", 200) == 400 and pass_number == 0:
                payload = base
                send_stream("status", "This model does not expose thinking summaries; streaming generated text.")
                continue
            if getattr(response, "status_code", 200) >= 400:
                # Cache the provider's error before the streaming context closes
                # its socket, so the UI can explain the rejected request.
                _ = response.content
            response.raise_for_status()
            saw_payload = False
            completed = False
            for line in response.iter_lines(decode_unicode=True):
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                if not line or not line.startswith("data:"):
                    continue
                body = line[5:].strip()
                if body == "[DONE]":
                    break
                try:
                    data = json.loads(body)
                except ValueError:
                    continue
                if isinstance(data.get("error"), dict):
                    raise AIResponseFormatError("Gemini stream failed: " + str(data["error"].get("message", "unknown error")))
                feedback = data.get("promptFeedback") or {}
                if isinstance(feedback, dict) and feedback.get("blockReason"):
                    raise AIResponseFormatError("Gemini blocked this request: " + str(feedback["blockReason"]))
                for candidate in data.get("candidates", []):
                    _check_gemini_completion(candidate)
                    completed = completed or candidate.get("finishReason") == "STOP"
                    for part in candidate.get("content", {}).get("parts", []):
                        content = part.get("text", "")
                        if not content:
                            continue
                        saw_payload = True
                        if part.get("thought") is True:
                            send_stream("reasoning", content)
                        else:
                            chunks.append(content)
                            send_stream("output", content)
            if not saw_payload or not "".join(chunks).strip():
                raise AIResponseFormatError("Gemini streamed no final generated text.")
            if not completed:
                raise AIResponseFormatError("Gemini response ended before completion. The operation will retry.")
            return "".join(chunks)
    raise AIResponseFormatError("Gemini did not accept the streaming request.")


def _check_gemini_completion(candidate: dict) -> None:
    if candidate.get("finishReason") == "MAX_TOKENS":
        raise AIResponseFormatError(
            "Gemini reached its output limit before completing the prompt. "
            "The incomplete result was not accepted. Retry with a model that "
            "supports a longer response; preserve all segment details.")
    reason = candidate.get("finishReason")
    if reason and reason not in {"STOP", "FINISH_REASON_UNSPECIFIED"}:
        raise AIResponseFormatError(f"Gemini stopped without completing the response ({reason}). The result was not accepted.")


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
    _check_gemini_completion(candidate)
    content = candidate.get("content")
    response_parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(response_parts, list) or not response_parts:
        finish_reason = candidate.get("finishReason")
        detail = f" ({finish_reason})" if finish_reason else ""
        raise AIResponseFormatError(f"Gemini returned no generated text{detail}. Magic Build will retry.")
    raw = "".join(part.get("text", "") for part in response_parts
                  if isinstance(part, dict) and part.get("thought") is not True)
    if not raw.strip():
        finish_reason = candidate.get("finishReason")
        detail = f" ({finish_reason})" if finish_reason else ""
        raise AIResponseFormatError(f"Gemini returned empty generated text{detail}. Magic Build will retry.")
    return raw


def _openai(images: list[dict], key: str, rules: str, timeout: int, sfx: bool, spoken_dialog: bool, model: str = "gpt-5.4-mini") -> dict:
    raw = _openai_raw(images, key, rules, timeout, model=model)
    return _validate(raw, len(images), sfx, spoken_dialog)


def _openai_raw(images: list[dict], key: str, rules: str, timeout: int, *,
                model: str = "gpt-5.4-mini", chatgpt: bool = False) -> str:
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
        next_cue = f" — INTERVAL END BOUNDARY {item['next_cue_timestamp']}" if item.get("next_cue_timestamp") else " — FINAL INTERVAL"
        continuity = f" — CONTINUITY ROLE: {item['continuity_function']}" if item.get("continuity_function") else ""
        analysis_mode = f" — {item['video_analysis_mode']}" if item.get("video_analysis_mode") else ""
        source_duration = f" — source clip {float(item['source_duration']):.3f}s" if item.get("source_duration") else ""
        content.append({"type": "input_text", "text": _minimax_reference_header(item) if item.get("reference_mode_input") else f"SEGMENT {index} OF {len(images)} — {label}{picture}{video} — {item['name']}{timing}{cue}{next_cue}{continuity}{source_duration}{analysis_mode}"})
        if item.get("motion_context") and not item.get("reference_mode_input"):
            content.append({"type": "input_text", "text": "TIMED FRAME MOTION CONTEXT: " + json.dumps(item["motion_context"], ensure_ascii=False)})
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
    payload = {"model": model, "input": [{"role": "user", "content": content}],
               "text": {"format": response_schemas.openai_format()}}
    if chatgpt or streaming_enabled():
        # Plan OAuth requires streaming and store=false; API-key mode streams too.
        payload.update({"stream": True, "reasoning": {"summary": "auto"}})
        if chatgpt:
            payload["store"] = False
        token = key.access_token() if hasattr(key, "access_token") else key
        return _checked_response(_chatgpt_stream(token, payload, timeout))
    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("status") == "incomplete":
        raise AIResponseFormatError(
            "OpenAI returned an incomplete response. The partial prompt was not accepted. "
            "Retry with a model that supports a longer response.")
    _check_openai_refusal(data)
    raw = data.get("output_text") or "".join(c.get("text", "") for item in data.get("output", []) for c in item.get("content", []) if c.get("type") == "output_text")
    if not str(raw).strip():
        raise AIResponseFormatError("OpenAI returned no generated text. The operation will retry.")
    return _checked_response(raw)


def _check_openai_refusal(data: dict) -> None:
    for item in data.get("output", []):
        for part in item.get("content", []):
            if part.get("type") == "refusal":
                raise RuntimeError("OpenAI declined this request: " + str(part.get("refusal") or "No reason supplied."))


def _chatgpt_stream(token: str, payload: dict, timeout: int) -> str:
    """Consume the public Responses SSE stream to the terminal completed event."""
    chunks: list[str] = []
    completed = False
    received_summary_delta = False
    headers = {"authorization": f"Bearer {token}", "content-type": "application/json"}
    response = requests.post("https://api.openai.com/v1/responses",
                             headers=headers, json=payload, timeout=timeout, stream=True)
    # Some listed models don't implement summaries. Keep streaming their text.
    if getattr(response, "status_code", 200) == 400 and payload.get("reasoning"):
        response.close()
        payload = {k: v for k, v in payload.items() if k != "reasoning"}
        send_stream("status", "Thinking summaries unavailable for this model; streaming generated output.")
        response = requests.post("https://api.openai.com/v1/responses",
                                 headers=headers, json=payload, timeout=timeout, stream=True)
    with response:
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True):
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            if not line or not line.startswith("data: "):
                continue
            body = line[6:]
            if body == "[DONE]":
                continue
            try:
                event = json.loads(body)
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "response.output_text.delta":
                delta = event.get("delta", "")
                chunks.append(delta)
                send_stream("output", delta)
            elif kind == "response.reasoning_summary_text.delta":
                received_summary_delta = True
                send_stream("reasoning", event.get("delta", ""))
            elif kind == "response.reasoning_summary_text.done" and not received_summary_delta:
                send_stream("reasoning", event.get("text", ""))
            elif kind == "response.completed":
                _check_openai_refusal(event.get("response", {}))
                completed = True
                # Some clients send a complete output object without text deltas.
                if not chunks:
                    for item in event.get("response", {}).get("output", []):
                        for part in item.get("content", []):
                            if part.get("type") == "output_text":
                                text = part.get("text", "")
                                chunks.append(text)
                                send_stream("output", text)
                break
            elif kind in ("response.failed", "response.incomplete", "error"):
                err = event.get("response", {}).get("error") or event.get("error") or {}
                detail = err.get("message") or err.get("code") or kind if isinstance(err, dict) else kind
                raise RuntimeError(f"ChatGPT inference failed: {detail}")
    if not completed:
        raise AIResponseFormatError("ChatGPT response ended before completion. The operation will retry.")
    raw = "".join(chunks).strip()
    if not raw:
        raise AIResponseFormatError("ChatGPT returned empty generated text. The operation will retry.")
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
            normalized["segments"] = [{key: normalized[key] for key in ("duration", "prompt", "segmentPrompt", "segment_prompt", "imagePrompt", "image_prompt") if key in normalized}]
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
        if status == 503 and "generativelanguage.googleapis.com" in str(response.url):
            message = (
                "Google Gemini is temporarily overloaded for the selected model. This is a provider-capacity issue, "
                "not a problem with your timeline or prompt. Wait a few minutes and try again, or choose another "
                "Gemini model in Settings."
            )
            return f"{message}\n\nGoogle response (HTTP {status}): {detail}" if detail else f"{message}\n\nHTTP {status}"
        message = f"AI provider returned HTTP {status}."
        if detail:
            message = f"{message}\n\nProvider response: {detail}"
        return message
    return str(error)
