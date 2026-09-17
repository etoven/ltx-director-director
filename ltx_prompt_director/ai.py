from __future__ import annotations

import json
import re
from pathlib import Path

import requests

from .media import data_url, video_storyboard_data_urls
from .models import Segment


GEMINI_MODELS = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"]
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
    result = _parse_json(raw)
    if not isinstance(result, dict):
        raise AIResponseFormatError("The AI returned an invalid MiniMax H3 response. The operation will retry.")
    prompt = result.get("prompt") or result.get("minimaxPrompt") or result.get("minimax_prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise AIResponseFormatError("The AI returned no MiniMax H3 prompt. The operation will retry.")
    prompt = prompt.strip()
    required_sections = (
        "subject_definitions", "summary", "retention_analysis", "detailed_description",
        "overall_soundscape", "non_diegetic_music",
    )
    missing = [section for section in required_sections if not re.search(rf"(?im)^\s*{section}\s*:", prompt)]
    if missing:
        raise AIResponseFormatError(
            f"The AI omitted required MiniMax H3 section(s): {', '.join(missing)}. The operation will retry."
        )
    shot_numbers = [int(number) for number in re.findall(r"(?im)^\s*\[Shot\s+(\d+)\]", prompt)]
    expected_shots = list(range(1, len(segments) + 1))
    if shot_numbers != expected_shots:
        raise AIResponseFormatError(
            f"The AI returned {len(shot_numbers)} MiniMax shot(s), but the timeline requires exactly "
            f"{len(segments)} in order. The operation will retry."
        )
    return prompt


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


def _minimax_h3_inputs(segments: list[Segment], provider: str = "gemini") -> list[dict]:
    inputs = []
    cursor = 0.0
    picture_number = 0
    video_number = 0
    for item in segments:
        start = cursor
        end = start + item.duration
        if item.kind == "image":
            picture_number += 1
        elif item.kind == "video":
            video_number += 1
        value = {
            "name": item.name,
            "role": item.role,
            "kind": item.kind,
            "start_time": start,
            "end_time": end,
            "duration": item.duration,
            "prompt": item.prompt.strip() or "[No existing segment prompt; infer conservatively from supplied visual and sequence context.]",
            "prompt_label": "CURRENT TIMELINE PROMPT — SOURCE MATERIAL FOR MINIMAX H3 SYNTHESIS",
            "image_prompt": item.image_prompt.strip(),
            "picture_number": picture_number if item.kind == "image" else None,
            "video_number": video_number if item.kind == "video" else None,
        }
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
    sound_rule = (
        "Write a concise overall_soundscape grounded in visible actions, materials, environments, clearly audible video-reference content, and existing SFX instructions."
        if sfx else
        "Do not invent Foley or ambient sound. Preserve clearly audible video-reference content when the provider exposes it; otherwise, if no source prompt explicitly requests sound, write `None specified.` under overall_soundscape."
    )
    dialog_rule = (
        "Preserve any actual spoken words, delivery, language, accent, and lip-sync requirements in the appropriate shot."
        if spoken_dialog else
        "Do not invent spoken dialogue; preserve it only if it is already explicitly written in the source prompts or Director's Intent."
    )
    music_rule = (
        "Write `None. Use only the described diegetic soundscape.` under non_diegetic_music unless the source explicitly requires music."
        if reduce_music else
        "Summarize explicitly requested music; otherwise infer only a brief, stylistically compatible music direction when it materially supports the sequence."
    )
    return f"""You are a sequence prompt editor for MiniMax H3 video generation. Convert the complete ordered LTX Director timeline below into ONE compact, production-ready MiniMax H3 multi-shot prompt. This is synthesis, not concatenation: boil repeated details down, preserve every important action and continuity constraint, and describe the entire sequence in chronological order.

The supplied example establishes structure only. Never copy its woman, apples, swim caps, colors, props, locations, timing, or fashion-film content. Derive all facts exclusively from the supplied timeline frames, current prompts, global prompt, and Director's Intent. Never invent unsupported identity, anatomy, clothing, setting, dialogue, or transformation facts.

TIMELINE FACTS:
- {len(segments)} ordered timeline items, including {image_count} still-image reference(s) and {video_count} video reference(s)
- the output must contain exactly {len(segments)} shots: timeline Segment N maps directly and exclusively to [Shot N]
- exact total duration: {total:.2f} seconds
- each record supplies exact start/end time, duration, media kind, current video prompt, and when available an audio-free still-image prompt
- still-image START frames establish exact opening states; still-image END frames are exact targets and must not be described as later action
- VIDEO records are temporal references: inspect their complete ordered motion, action progression, camera behavior, transformations, ending state, and clearly audible content when accessible instead of treating their preview or sampled frames as unrelated still pictures
- when a VIDEO is represented by timestamped samples, interpret them as ordered observations from one continuous source clip; never invent motion that is unsupported by their progression or the authoritative current prompt

AUTHORITATIVE DIRECTOR'S INTENT:
{intent.strip() or 'No additional director intent supplied.'}

GLOBAL CONTINUITY PROMPT:
{global_prompt.strip() or 'No global prompt supplied.'}

OUTPUT STRUCTURE — use these six lowercase headings exactly, in this order, with no Markdown fences:

subject_definitions:
Define each distinct recurring visible subject as <Subject N>. Define every supplied still image in timeline order as <Picture N>, stating whether it is a first frame, end frame, or keyframe. Define every supplied video clip in timeline order as <Video N>, summarizing its observed temporal action and camera movement without reducing it to one frame. Connect recurring identities only when supported. Text-only items do not create visual references.

summary:
Begin with `[keyframe completion + reference generation]`. In one compact paragraph, state the complete creative arc across all {len(segments)} ordered shots, principal motion, transitions, and ending state. Do not summarize multiple timeline segments as one shot.

retention_analysis:
Give one line per recurring subject, one per <Picture N>, and one per <Video N>. Include shot appearances and exactly one status—fully_preserved, partially_preserved, or not_preserved—followed by a concise reason. For video references, explicitly state which observed motion, action progression, camera behavior, and ending state are retained. Explain deliberate transformations, outfit changes, scene changes, and end-frame targets as intended progression rather than continuity mistakes.

detailed_description:
Start with one brief sentence defining the overall medium, visual style, and pacing. Then write exactly {len(segments)} chronological shot paragraphs, numbered consecutively `[Shot 1]` through `[Shot {len(segments)}]`, with one and only one shot for each supplied timeline segment. Segment N must map directly to `[Shot N]`, using that segment's exact `At HH:MM:SS.mmm` start timestamp. Never merge, consolidate, omit, or renumber adjacent segments, even when they depict one continuous action or share a scene; express continuity between their separate shot paragraphs instead. Reference <Subject N>, <Picture N>, and <Video N> consistently, and place each visual reference in the shot belonging to its source segment. Use two to five precise sentences per shot covering opening anchor, visible action over time, camera behavior, physical causality, continuity, and resolved ending. A <Video N> contributes its full temporal behavior to its corresponding shot—not merely its first, middle, or last sampled frame. Preserve explicit stationary-camera rules and avoid adding camera moves merely to make prose exciting.

overall_soundscape:
{sound_rule} {dialog_rule}

non_diegetic_music:
{music_rule}

Before returning, count the timeline records and `[Shot N]` headers. They must match exactly, with no skipped or duplicate shot number. Keep the result concise enough to function as one prompt. Do not include analysis, alternatives, warnings, JSON, or commentary inside the prompt. Return strict transport JSON containing only: {{"prompt":"the complete multiline MiniMax H3 prompt"}}"""


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
        analysis_mode = f" — {item['video_analysis_mode']}" if item.get("video_analysis_mode") else ""
        source_duration = f" — source clip {float(item['source_duration']):.3f}s" if item.get("source_duration") else ""
        parts.append({"text": f"SEGMENT {index} OF {len(images)} — {label}{picture}{video} — {item['name']}{timing}{source_duration}{analysis_mode}"})
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
        analysis_mode = f" — {item['video_analysis_mode']}" if item.get("video_analysis_mode") else ""
        source_duration = f" — source clip {float(item['source_duration']):.3f}s" if item.get("source_duration") else ""
        content.append({"type": "input_text", "text": f"SEGMENT {index} OF {len(images)} — {label}{picture}{video} — {item['name']}{timing}{source_duration}{analysis_mode}"})
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
