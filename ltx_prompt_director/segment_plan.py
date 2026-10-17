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
    return provider in {"openai", "chatgpt"} and bool(
        re.match(r"^gpt-(?:[6-9]|[1-9]\d)(?:[.-]|$)", str(model).lower()))

def response_contract(segments, current_prompt, *, refine, focus="all"):
    plan = [{"id": s.id, "name": s.name, "kind": s.kind, "role": s.role,
             "duration": s.duration, "prompt": s.prompt,
             "imagePrompt": s.image_prompt, "hasMedia": bool(s.media_path)}
            for s in segments]
    return (
        "\n\nADDITIVE GPT-6+ SEGMENT JSON CONTRACT. All existing production "
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
        'New IDs must be new:1, new:2, etc, with kind=text and role=text; '
        "new visual media cannot be fabricated. If new artwork is requested, "
        "include a detailed imagePrompt and artworkPrompt; the app can render "
        "only through a separately authenticated image provider. "
        "Never provide external media paths or URLs, and do not change existing "
        "media kinds. Text items cannot use start/end role. "
        "The unified prompt and segments must describe exactly the same sequence. "
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
        if data.get("kind", kind) != kind:
            raise ai.AIResponseFormatError("AI cannot replace or invent media kinds.")
        role = data.get("role", before.role if before else "text")
        if role != ("text" if kind == "text" else role) or (
            kind != "text" and role not in ("start", "end")):
            raise ai.AIResponseFormatError("Invalid frame role.")
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
        normalized.append({"id": sid, "name": name.strip(), "kind": kind,
                           "role": role, "duration": duration, "prompt": prose,
                           "imagePrompt": img, "artworkPrompt": art.strip() if not before else ""})
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
            ops.append({"kind": "rewrite", "id": before.id, "name": before.name})
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
        built.append(item)
    return built

def generate_combined(segments, provider, model, api_key, intent, global_prompt,
                      sfx, spoken_dialog, reduce_music, current_prompt,
                      refinement_instructions, timeout, reference_images,
                      workspace_engine, mode, refine, focus="all", image_api_key="", render_artwork=False):
    if not supports_structured_edit(provider, model):
        raise ValueError("Combined editing requires an eligible GPT-6+ model.")
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
    # Preserve the original unified response contract in the provider prompt.
    # The new segment JSON is appended AFTER it and overrides transport only.
    if workspace_engine != "generic":
        # Exact same media-contract suffix as the existing MiniMax generation
        # and refinement functions. No creative prompt text is rewritten.
        rules += "\nCURRENT MEDIA CONTRACT: The asset map may include uploaded reference audio and video soundtracks. Audio-only video samples condition sound only. Follow the supplied labels and observation availability. Never invent unheard audio or visual observations. This overrides legacy template text saying audio references are unavailable."
    rules += ai.UNIFIED_PROMPT_TRANSPORT
    rules += response_contract(segments, current_prompt, refine=refine, focus=focus)
    raw = ai._provider_raw(inputs, provider, model, api_key, rules, timeout)
    result = normalize_plan(raw, segments)
    if render_artwork and image_api_key:
        result["renderedArtwork"] = render_requested_artwork(
            result["segments"], image_api_key
        )
    return result



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
        if item.get("prompt", before.prompt) != before.prompt:
            changes.append({"kind": "rewrite", "id": before.id, "name": before.name})
    return changes



def render_requested_artwork(records: list[dict], api_key: str) -> dict[str, str]:
    """Optional, billed OpenAI image API: no ChatGPT OAuth/image tool shortcut.

    Only synthesize up to four newly requested frames, never alter existing media.
    Produces persistent PNGs after a complete validated segment plan.
    """
    import base64
    import os
    from pathlib import Path
    from uuid import uuid4
    import requests
    from .ai_stream import send_stream

    output = {}
    entries = [row for row in records if row["id"].startswith("new:") and row["artworkPrompt"]]
    if not entries:
        return output
    root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "PromptDirector" / "generated-artwork"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for entry in entries[:4]:
        send_stream("status", f"Rendering frame artwork: {entry['name']}")
        result = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            json={"model": "gpt-image-2.5-flare", "size": "1536x1024",
                  "n": 1, "prompt": entry["artworkPrompt"]},
            timeout=240,
        )
        if not result.ok:
            send_stream("status", f"Artwork not rendered (HTTP {result.status_code}): {entry['name']}")
            continue
        try:
            source = result.json()["data"][0]["b64_json"]
            payload = base64.b64decode(source, validate=True)
            if not payload.startswith(b"\x89PNG\r\n\x1a\n") or len(payload) > 35 * 1024 * 1024:
                raise ValueError("Invalid generated PNG")
        except (ValueError, KeyError, IndexError, TypeError):
            send_stream("status", f"Artwork response incomplete for {entry['name']}")
            continue
        path = root / (str(uuid4()) + ".png")
        path.write_bytes(payload)
        output[entry["id"]] = str(path)
        send_stream("artwork", json.dumps({"id": entry["id"], "name": entry["name"],
                                           "path": str(path)}))
    return output
