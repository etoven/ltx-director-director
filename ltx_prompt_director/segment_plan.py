"""Atomic GPT-6+ unified prompt and segment JSON editing.

Retain each workspace's production prompt template. Add only the segment JSON
contract to the existing generation input; never fabricate local media.
"""
from __future__ import annotations
import copy
import json
import math
import re
from uuid import uuid4
from . import ai
from .models import Segment

MAX_SEGMENTS = 96

def supports_structured_edit(provider, model):
    name = str(model).lower()
    # Gemini Flash reasoning models support structured text/JSON output, but
    # Nano Banana image models don't support the segment-planning JSON schema.
    if provider == "gemini":
        return bool(re.match(r"^gemini-(?:[3-9]|[1-9]\d)(?:[.-]|$)", name)) and not (
            "-image" in name or "-tts" in name or "-transcribe" in name
        )
    return provider in {"openai", "chatgpt"} and bool(
        re.match(r"^gpt-(?:[6-9]|[1-9]\d)(?:[.-]|$)", name))

def response_contract(segments, current_prompt, *, refine, focus="all"):
    plan = [{"id": s.id, "name": s.name, "kind": s.kind, "role": s.role,
             "duration": s.duration, "prompt": s.prompt,
             "imagePrompt": s.image_prompt, "hasMedia": bool(s.media_path)}
            for s in segments]
    return (
        "\n\nADDITIVE STRUCTURED SEGMENT JSON CONTRACT. All existing production "
        "instructions above remain authoritative. This extends ONLY the response "
        'transport. Return ONE strict JSON object {"prompt":"complete unified '
        'production prompt", "segments":[{"id":"existing UUID or new:1", '
        '"name":"...", "kind":"image|video|text", "role":"start|end|text", '
        '"duration":2.5, "prompt":"...", "imagePrompt":"...", '
        '"artworkPrompt":""}]}. '
        "The prompt field must retain the established continuous production format. "
        "The segments array is the ordered final timeline. Existing UUIDs are "
        "stable identities. Include every retained segment; omit an ID to delete "
        "only when justified. You may reorder, retime, or modify roles, prompts "
        "and image prompts as warranted by explicit requests. Do not gratuitously "
        "change existing wording, duration, media or order. "
        'New IDs must be new:1, new:2, etc; use kind=text role=text for '
        'text inserts, or kind=image with role=start|end and a nonempty '
        'artworkPrompt to request a new visual frame (a temporary text tile '
        'becomes an image only after real artwork renders). '
        "new visual media cannot be fabricated. If new artwork is requested, "
        "include a detailed imagePrompt and artworkPrompt; the app can render "
        "only through a separately authenticated image provider. "
        "To EDIT OR REGENERATE AN EXISTING IMAGE FRAME, keep its exact existing "
        "ID, kind=image and start/end role, but set artworkPrompt to a detailed "
        "image-editing instruction and imagePrompt to the intended resulting "
        "frame. Do this whenever the director explicitly asks to edit, replace, "
        "fix, render, or regenerate an image already on the timeline. The app "
        "will supply that frame's ORIGINAL pixels to the image-editing API. "
        "Do not create a new ID for an in-place edit. Leave artworkPrompt empty "
        "on existing images unless actual image generation/editing is requested. "
        "Never provide external media paths or URLs, and do not change existing "
        "media kinds. Text items cannot use start/end role. "
        "A text segment ALWAYS has role=text; never label text as start/end. "
        "An existing image or video segment retains its current kind and "
        "start/end role unless a start/end role change was explicitly requested. "
        "NEVER send role=text for an existing image/video, including while "
        "rewriting only its prompt; copy its original role from CURRENT SEGMENTS. "
        "In particular, "
        "Source/Scene are conditioning labels, NOT frame roles. "
        "The unified prompt and segments must describe exactly the same sequence. "
        "ALWAYS include the complete nonempty segments array, including when "
        "the request is only to generate an image; an artwork request is not "
        "a substitute for the segment list. The array is REQUIRED for the "
        "application to render the artwork and must contain every unchanged "
        "existing segment alongside any new image proposal. "
        "Do not output anything outside JSON.\nCURRENT SEGMENTS:\n"
        + json.dumps(plan, ensure_ascii=False) + "\n"
        + "FOCUS: " + focus + "\n"
        + ("CURRENT USER EDITED UNIFIED PROMPT — preserve requested creative edits "
           "and wording as best as possible:\n" + current_prompt if current_prompt
           else "Generate unified prompt from existing source template.")
    )

def normalize_plan(raw, current):
    response = ai._parse_json(raw)
    if not isinstance(response, dict):
        raise ai.AIResponseFormatError("Expected one combined JSON response.")
    prompt = response.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ai.AIResponseFormatError("No unified prompt in combined response.")
    records = response.get("segments")
    if not isinstance(records, list) or not (1 <= len(records) <= MAX_SEGMENTS):
        raise ai.AIResponseFormatError("Invalid combined segment list.")
    by_id = {s.id: s for s in current}
    used, normalized = set(), []
    for pos, data in enumerate(records):
        if not isinstance(data, dict):
            raise ai.AIResponseFormatError("Invalid segment object.")
        sid = data.get("id")
        if not isinstance(sid, str) or sid in used:
            raise ai.AIResponseFormatError("Missing or duplicate segment ID.")
        used.add(sid)
        before = by_id.get(sid)
        if before is None and not re.fullmatch(r"new:\d+", sid):
            raise ai.AIResponseFormatError("Unknown segment ID.")
        kind = before.kind if before else "text"
        requested_kind = data.get("kind", kind)
        artwork_role = "start"
        if before is None and requested_kind == "image" and data.get("artworkPrompt"):
            # A proposed image is temporarily a text placeholder until the
            # separately authenticated image API returns actual media bytes.
            artwork_role = data.get("role", "start")
            requested_kind = "text"
        if requested_kind != kind:
            raise ai.AIResponseFormatError("AI cannot replace or invent media kinds.")
        role = data.get("role", before.role if before else "text")
        if before is None and data.get("artworkPrompt") and data.get("kind") == "image":
            # A separately-rendered image is a text placeholder until pixels
            # exist. Keep the requested start/end role for the final image.
            role = "text"
            if artwork_role not in ("start", "end"):
                raise ai.AIResponseFormatError(
                    "Generated visual role must be start or end."
                )
        elif kind == "text":
            # A text segment remains text even if the model accidentally
            # mirrors a neighboring frame's start/end or conditioning label.
            # Do not reject an entire otherwise-valid combined refinement.
            role = "text"
        elif role not in ("start", "end"):
            # AI occasionally copies "text" from an adjacent text tile, or
            # "Source"/"Scene" from conditioning metadata, into an EXISTING
            # image/video's role. This is malformed metadata, NOT authority
            # to convert the user's original visual media to a text tile.
            # Preserve the original start/end and keep the rest of the valid
            # combined plan instead of failing the entire refinement.
            role = before.role if before and before.role in ("start", "end") else "start"
        try:
            duration = float(data.get("duration", before.duration if before else 5.0))
        except (TypeError, ValueError) as error:
            raise ai.AIResponseFormatError("Invalid duration.") from error
        if not math.isfinite(duration) or not 0.01 <= duration <= 3600:
            raise ai.AIResponseFormatError("Segment duration out of bounds.")
        name = data.get("name", before.name if before else f"AI Segment {pos + 1}")
        prose = data.get("prompt", before.prompt if before else "")
        img = data.get("imagePrompt", before.image_prompt if before else "")
        art = data.get("artworkPrompt", "")
        if not all(isinstance(v, str) for v in (name, prose, img, art)):
            raise ai.AIResponseFormatError("Invalid text field.")
        if not name.strip() or not (prose.strip() or art.strip()):
            raise ai.AIResponseFormatError("Segment requires a name and direction.")
        if before and art.strip() and (before.kind != "image" or not before.media_path):
            raise ai.AIResponseFormatError(
                "Only existing image frames with real media can request an in-place artwork edit."
            )
        normalized.append({"id": sid, "name": name.strip(), "kind": kind,
                           "role": role, "duration": duration, "prompt": prose,
                           "imagePrompt": img,
                           "artworkPrompt": art.strip() if (not before or before.kind == "image") else "",
                           "artworkRole": artwork_role})
    return {"prompt": prompt.strip(), "segments": normalized}

def preview_operations(original, records):
    by_id = {s.id: s for s in original}
    indices = {s.id: i for i, s in enumerate(original)}
    retained = {s["id"] for s in records}
    ops = [{"kind": "delete", "id": s.id, "name": s.name}
           for s in original if s.id not in retained]
    for pos, entry in enumerate(records):
        before = by_id.get(entry["id"])
        if before is None:
            ops.append({"kind": "add", "id": entry["id"], "name": entry["name"], "position": pos})
            if entry["artworkPrompt"]:
                ops.append({"kind": "artwork", "id": entry["id"], "name": entry["name"]})
            continue
        if indices[before.id] != pos:
            ops.append({"kind": "move", "id": before.id, "name": before.name, "position": pos})
        if abs(before.duration - entry["duration"]) > 0.005:
            ops.append({"kind": "timing", "id": before.id, "name": before.name, "duration": entry["duration"]})
        if before.role != entry["role"]:
            ops.append({"kind": "role", "id": before.id, "name": before.name, "role": entry["role"]})
        if before.prompt != entry["prompt"] or before.image_prompt != entry["imagePrompt"]:
            ops.append({"kind": "rewrite", "id": before.id, "name": before.name,
                        "prompt": entry["prompt"],
                        "imagePrompt": entry["imagePrompt"]})
        if entry["artworkPrompt"] and before.kind == "image":
            ops.append({"kind": "artwork", "id": before.id, "name": before.name})
    return ops

def build_segment_objects(original, records):
    by_id = {s.id: s for s in original}
    built = []
    for row in records:
        before = by_id.get(row["id"])
        item = copy.copy(before) if before else Segment(
            row["name"], "", "", "text", "text", "", row["duration"], id=str(uuid4()))
        item.name, item.duration = row["name"], row["duration"]
        item.role, item.prompt, item.image_prompt = row["role"], row["prompt"], row["imagePrompt"]
        if before is None:
            item.artwork_target_role = row.get("artworkRole", "start")
        built.append(item)
    return built


def explicit_existing_image_edit(text: str) -> str:
    """Recover an unambiguous /refine image request when a model omits artworkPrompt.

    Never turn ordinary story prose mentioning images into billable image work.
    This reads ONLY explicit inline edit instructions from the user.
    """
    for match in re.finditer(r"(?im)^\s*/refine(?:-global)?\s*:?[ \t]+([^\n]+)", text or ""):
        instruction = match.group(1).strip()
        if (re.search(r"\b(image|frame|picture|photo|still|artwork)\b", instruction, re.I)
                and re.search(r"\b(edit|generate|regenerate|render|create|change|modify|"
                              r"replace|remove|add|fix|alter|make|update)\b", instruction, re.I)):
            return instruction
    return ""


def generate_combined(segments, provider, model, api_key, intent, global_prompt,
                      sfx, spoken_dialog, reduce_music, current_prompt,
                      refinement_instructions, timeout, reference_images,
                      workspace_engine, mode, refine, focus="all"):
    if not supports_structured_edit(provider, model):
        raise ValueError("Combined editing requires a structured-capable GPT-6+ or Gemini Flash model.")
    if not segments:
        raise ValueError("Combined editing requires at least one timeline item.")
    if workspace_engine == "generic":
        from .generic_ai import _rules
        inputs = ai._minimax_reference_inputs(segments, provider, reference_images, refinement=refine)
        rules = _rules(segments, intent, global_prompt, sfx, spoken_dialog, reduce_music,
                       reference_images, current_prompt, refinement_instructions, refine=refine)
    elif mode == "references":
        inputs = ai._minimax_reference_inputs(segments, provider, reference_images, refinement=refine)
        rules = ai._minimax_h3_reference_rules(
            segments, intent, global_prompt, sfx, spoken_dialog, reduce_music, reference_images)
        if refine:
            from .prompt_templates import render_workspace_prompt
            rules += render_workspace_prompt("minimax_references", "refine", {
                "current_prompt": current_prompt, "refinement_instructions": refinement_instructions})
    else:
        inputs = ai._minimax_frames_inputs(segments, provider, reference_images, refinement=refine)
        rules = ai._minimax_h3_rules(
            segments, intent, global_prompt, sfx, spoken_dialog, reduce_music, reference_images)
        if refine:
            from .prompt_templates import render_workspace_prompt
            rules += render_workspace_prompt("minimax_frames", "refine", {
                "current_prompt": current_prompt, "refinement_instructions": refinement_instructions})
    # Retain the workspace production rules verbatim, but use ONE consistent
    # response schema. The legacy UNIFIED_PROMPT_TRANSPORT explicitly forbids
    # segment records and therefore cannot precede this combined contract.
    if workspace_engine != "generic":
        # Exact same media-contract suffix as the existing MiniMax generation
        # and refinement functions. No creative prompt text is rewritten.
        rules += "\nCURRENT MEDIA CONTRACT: The asset map may include uploaded reference audio and video soundtracks. Audio-only video samples condition sound only. Follow the supplied labels and observation availability. Never invent unheard audio or visual observations. This overrides legacy template text saying audio references are unavailable."
    rules += response_contract(segments, current_prompt, refine=refine, focus=focus)
    raw = ai._provider_raw(inputs, provider, model, api_key, rules, timeout)
    # Return the validated plan immediately. Image rendering runs AFTER the
    # proposed segments have slid into the UI so the user sees progress.
    try:
        return normalize_plan(raw, segments)
    except ai.AIResponseFormatError as error:
        if str(error) != "Invalid combined segment list.":
            # Do not conceal invalid IDs, unsafe invented media or other
            # genuinely malformed timeline mutations with a silent fallback.
            raise
        initial = ai._parse_json(raw)
        original_prompt = initial.get("prompt") if isinstance(initial, dict) else None
        if not isinstance(original_prompt, str) or not original_prompt.strip():
            raise

        # A model may obey its historical prompt-only JSON response format and
        # omit the timeline array. Give it ONE schema-only repair opportunity,
        # not another user-visible action, and preserve its original prose.
        from .ai_stream import send_stream
        send_stream("reset", "Completing missing combined segment JSON")
        repair_rules = (
            rules
            + "\n\nSCHEMA-ONLY REPAIR: The previous output provided a unified "
              "production prompt but omitted the REQUIRED nonempty ordered "
              "segments array. Preserve the previous unified prompt VERBATIM; "
              "do not rewrite it or reinterpret the creative request. Return "
              "one JSON object with the unchanged prompt and a complete "
              "segments array containing all retained current IDs, in order, "
              "plus any requested new artwork tiles. To request artwork, add "
              "a new:1/new:2 kind=image row with a detailed artworkPrompt and "
              "imagePrompt; use the correct start/end role. Do not invent media "
              "paths. This is a response format repair, not a second creative "
              "refinement.\nPREVIOUS PROMPT-ONLY RESPONSE:\n"
            + json.dumps(initial, ensure_ascii=False)
        )
        repaired_raw = ai._provider_raw(inputs, provider, model, api_key, repair_rules, timeout)
        repaired = ai._parse_json(repaired_raw)
        if not isinstance(repaired, dict) or not isinstance(repaired.get("segments"), list):
            raise ai.AIResponseFormatError(
                "Combined response was missing its segment plan even after one repair. "
                "The existing prompt and timeline were left unchanged."
            ) from error
        # Never let the format-repair request silently replace the production
        # text authored by the first response.
        return normalize_plan(json.dumps({
            "prompt": original_prompt, "segments": repaired["segments"]
        }, ensure_ascii=False), segments)



def completed_stream_records(raw: str) -> list[dict]:
    """Return only fully received segment objects from an unfinished JSON stream.

    Never treats an incomplete field as an authoritative mutation.
    """
    matched = re.search(r'"segments"\s*:\s*\[', raw)
    if not matched:
        return []
    decoder = json.JSONDecoder()
    cursor = matched.end()
    records = []
    while len(records) < MAX_SEGMENTS:
        while cursor < len(raw) and raw[cursor] in " \t\r\n,":
            cursor += 1
        if cursor >= len(raw) or raw[cursor] == "]":
            break
        try:
            obj, amount = decoder.raw_decode(raw[cursor:])
        except json.JSONDecodeError:
            break
        if not isinstance(obj, dict) or not isinstance(obj.get("id"), str):
            break
        records.append(obj)
        cursor += amount
    return records


def stream_operations(original: list[Segment], raw: str) -> list[dict]:
    """Progressive preview for fully decoded records, before final validation."""
    by_id = {item.id: item for item in original}
    indices = {item.id: i for i, item in enumerate(original)}
    changes = []
    for pos, item in enumerate(completed_stream_records(raw)):
        before = by_id.get(item["id"])
        if before is None:
            if re.fullmatch(r"new:\d+", item["id"]):
                changes.append({"kind": "add", "id": item["id"], "name": str(item.get("name", "New segment")), "position": pos})
            continue
        if indices[before.id] != pos:
            changes.append({"kind": "move", "id": before.id, "name": before.name, "position": pos})
        try:
            duration = float(item.get("duration", before.duration))
        except (TypeError, ValueError):
            duration = before.duration
        if math.isfinite(duration) and abs(duration-before.duration) > 0.005:
            changes.append({"kind": "timing", "id": before.id, "name": before.name, "duration": duration})
        if item.get("role", before.role) != before.role:
            changes.append({"kind": "role", "id": before.id, "name": before.name, "role": item["role"]})
        if (item.get("prompt", before.prompt) != before.prompt
                or item.get("imagePrompt", before.image_prompt) != before.image_prompt):
            changes.append({"kind": "rewrite", "id": before.id, "name": before.name,
                            "prompt": item.get("prompt", before.prompt),
                            "imagePrompt": item.get("imagePrompt", before.image_prompt)})
        if before.kind == "image" and str(item.get("artworkPrompt") or "").strip():
            changes.append({"kind": "artwork", "id": before.id, "name": before.name})
    return changes



def normalize_timeline_sync(raw, current, authoritative_prompt):
    """Validate replacement timeline JSON; preserve real image/video assets.

    Existing checkpoints can be retimed and rewritten, but never deleted or
    replaced with an invented file. Unmatched beats can add text or proposed
    image placeholders through the existing artwork rendering workflow.
    """
    response = ai._parse_json(raw)
    if not isinstance(response, dict) or not isinstance(response.get("segments"), list):
        raise ai.AIResponseFormatError("Timeline sync requires an ordered segments array.")
    entries = response["segments"]
    existing = {segment.id: segment for segment in current}
    received = [entry.get("id") for entry in entries if isinstance(entry, dict)]
    if len(received) != len(entries):
        raise ai.AIResponseFormatError("Timeline sync contains an invalid segment entry.")
    for entry in entries:
        sid = entry.get("id")
        original = existing.get(sid)
        artwork = str(entry.get("artworkPrompt") or "").strip()
        if original and artwork:
            raise ai.AIResponseFormatError(
                "Timeline sync cannot regenerate an existing image checkpoint.")
        if original:
            if entry.get("kind", original.kind) != original.kind:
                raise ai.AIResponseFormatError("Timeline sync cannot change existing media types.")
            if entry.get("role", original.role) != original.role:
                raise ai.AIResponseFormatError("Timeline sync cannot change frame roles.")
        elif entry.get("kind") == "image":
            if entry.get("role") not in ("start", "end") or not artwork:
                raise ai.AIResponseFormatError(
                    "A new image checkpoint requires a start/end role and an artwork prompt.")
        elif entry.get("kind") != "text" or entry.get("role") != "text":
            raise ai.AIResponseFormatError(
                "New synchronized intervals must be text or renderable image checkpoints.")
    # Deleting a superseded text beat is fine. Silently deleting/reordering a
    # user's actual frames or video, however, is not a text synchronization.
    visual_before = [item.id for item in current if item.kind != "text"]
    visual_after = [sid for sid in received if sid in existing and existing[sid].kind != "text"]
    if visual_after != visual_before:
        raise ai.AIResponseFormatError("Timeline sync must retain media checkpoints in order.")
    result = normalize_plan(
        json.dumps({"prompt": authoritative_prompt, "segments": entries}, ensure_ascii=False),
        current,
    )
    return {"segments": result["segments"]}


def sync_timeline_to_prompt(segments, provider, model, api_key, combined_prompt,
                            timeout, reference_images, workspace_engine, mode):
    """One-way sync: replace the timeline JSON using unchanged combined prose."""
    if not supports_structured_edit(provider, model):
        raise ValueError("Timeline sync requires a structured-capable GPT-6+ or Gemini Flash model.")
    if not str(combined_prompt).strip():
        raise ValueError("Timeline sync requires a combined prompt.")
    if mode == "references" or workspace_engine == "generic":
        inputs = ai._minimax_reference_inputs(segments, provider, reference_images, refinement=True)
    else:
        inputs = ai._minimax_frames_inputs(segments, provider, reference_images, refinement=True)
    # The provider already receives each actual image (or sampled video)
    # through the established multimodal reference pipeline. Bind that visual
    # evidence to its exact persistent segment UUID so the model can match
    # a frame to a timecode without inventing or swapping attachments.
    for index, asset in enumerate(inputs):
        slot = asset.get("timeline_index")
        if slot is None and "reference_slot" not in asset and workspace_engine != "generic" and mode != "references":
            slot = index if index < len(segments) else None
        if isinstance(slot, int) and 0 <= slot < len(segments):
            original = segments[slot]
            asset["name"] = (
                f"CURRENT SEGMENT {slot + 1}, ID={original.id}, "
                f"ORIGINAL ROLE={original.role} — {asset.get('name', original.name)}"
            )
            asset["segment_id"] = original.id
            asset["prompt_label"] = (
                "OLD TIMELINE DIRECTION; master combined prompt overrides old timing"
            )
    current = [
        {"id": item.id, "name": item.name, "kind": item.kind,
         "role": item.role, "duration": item.duration, "prompt": item.prompt,
         "imagePrompt": item.image_prompt, "hasMedia": bool(item.media_path)}
        for item in segments
    ]
    rules = (
        "ONE-WAY TIMELINE SYNC. The combined prompt below is final and IMMUTABLE. "
        "Do NOT rewrite, summarize, extend, correct or return it. "
        "You are provided the CURRENT TIMELINE JSON and the ACTUAL CURRENT "
        "IMAGE AND VIDEO CHECKPOINTS as multimodal inputs, each labeled with "
        "its original segment position, ID, current timing and start/end role. "
        "Look at the supplied pictures to match them to visual stages and "
        "timestamps in the combined prompt; don't guess frame appearances "
        "from text or invent unseen media. The old timeline times are "
        "SUGGESTIONS ONLY. FIRST match each EXISTING image/video checkpoint "
        "to the correct source prompt beat, retaining its original UUID and "
        "actual media. Retime its duration and revise its segment prompt to "
        "match the timecoded beat precisely. This INCLUDES existing image "
        "segments: they may shorten or lengthen substantially. Their image "
        "files and roles are unchanged. Produce an entirely NEW ordered "
        "timeline JSON with exact times and matched wording. "
        "Preserve all explicitly requested dialogue, identity and motion "
        "details in their matching intervals. Add missing segments ONLY "
        "when the source prompt contains an uncovered beat "
        "(IDs new:1, new:2, ...). "
        "Use text segments for timed motion beats. ONLY if a genuinely "
        "missing image checkpoint cannot be represented by retiming ANY "
        "existing image segment and is required by the master prompt, propose "
        "kind=image, role=start or end, with a detailed, nonempty artworkPrompt "
        "and imagePrompt describing the desired image. The application "
        "renders real pixels separately, and until then the new image is "
        "a temporary text placeholder. Do not propose unnecessary frames. "
        "You may retime, rename, reorder, rewrite or remove obsolete TEXT beats "
        "to create a clean match. Preserve the original IDs, media kinds, roles "
        "and relative order of all image/video checkpoints: real attached media "
        "cannot be regenerated or deleted by sync. Keep its corresponding "
        "interval action aligned to the immutable prompt. New motion beats "
        "use kind=text role=text; ONLY genuinely new visual checkpoints may "
        "request kind=image role=start|end and a detailed artworkPrompt. "
        "Never invent audio attachments, video files, external media paths "
        "or source observations. Existing image/video assets are never edited. "
        "Include every retained existing item only once. Preserve existing "
        "imagePrompt wording unless an adjustment is essential to reflect "
        "the authoritative combined prompt, and never return an artworkPrompt "
        "for an existing media ID. Give every segment a meaningful action "
        "prompt and duration in seconds (0.01 to 3600). "
        "An explicit time range controls duration; split into extra segments "
        "when required instead of losing timed beats. Preserve continuity "
        "and total time from the master combined prompt, with no gaps. "
        'Return exactly one strict JSON object {"segments":[{"id":"new:1",'
        '"name":"Action","kind":"text","role":"text","duration":2.5,'
        '"prompt":"timed action wording","imagePrompt":"","artworkPrompt":""}]}. '
        "For requested images use kind=image, role=start|end, nonempty "
        "artworkPrompt and imagePrompt; no media path. "
        "NO combined prompt field and NO commentary.\n"
        "EXISTING TIMELINE AND MEDIA IDs (actual associated images are included "
        "as separate labeled multimodal parts):\n"
        + json.dumps(current, ensure_ascii=False)
        + "\n\nAUTHORITATIVE COMBINED PROMPT (READ ONLY):\n"
        + combined_prompt
    )
    raw = ai._provider_raw(inputs, provider, model, api_key, rules, timeout)
    return normalize_timeline_sync(raw, segments, combined_prompt)
