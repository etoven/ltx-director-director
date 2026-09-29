"""Template-driven prompt generation without model-specific creative defaults."""
from __future__ import annotations

import json

from . import ai
from .minimax_reference import reference_inventory
from .prompt_templates import render_workspace_prompt


def _rules(segments, intent, global_prompt, sfx, spoken_dialog, reduce_music, references,
           current_prompt="", refinement_instructions="", *, refine=False):
    return render_workspace_prompt("generic", "refine" if refine else "generate", {
        "director_intent": intent.strip(), "global_direction": global_prompt.strip(),
        "asset_map": json.dumps(reference_inventory(segments, references), ensure_ascii=False, indent=2),
        "ordered_plan": json.dumps([{"index": index + 1, "prompt": segment.prompt, "duration": segment.duration}
                                    for index, segment in enumerate(segments)], ensure_ascii=False, indent=2),
        "total_duration": sum(segment.duration for segment in segments),
        "current_prompt": current_prompt, "refinement_instructions": refinement_instructions,
        "sound_effects": "Enabled" if sfx else "Disabled",
        "dialogue": "Enabled" if spoken_dialog else "Disabled",
        "music": "No background music" if reduce_music else "Use supplied music direction only",
    })


TIMED_CONTRACT = '''
Required response transport: return JSON with "prompt" containing the production brief and a
[TIMED ACTION] heading without action lines, and "timed_actions" containing objects with
"start", "end" (non-drop-frame SMPTE HH:MM:SS:FF at 24 fps) and "action".
Intervals must be contiguous and ascending from zero. Respect explicit requested timing.
Preserve media order and identities; additional action intervals may be added at the end.
'''


def build_generic_prompt(segments, provider, model, api_key, intent, global_prompt, sfx,
                         spoken_dialog, reduce_music, timeout=400, reference_images=None):
    inputs = ai._minimax_reference_inputs(segments, provider, reference_images)
    rules = _rules(segments, intent, global_prompt, sfx, spoken_dialog, reduce_music, reference_images)
    raw = ai._provider_raw(inputs, provider, model, api_key, rules + TIMED_CONTRACT, timeout)
    return ai._extract_minimax_h3_prompt(raw, segments)


def refine_generic_prompt(segments, provider, model, api_key, intent, global_prompt, sfx,
                          spoken_dialog, reduce_music, current_prompt, refinement_instructions,
                          timeout=400, reference_images=None):
    inputs = ai._minimax_reference_inputs(segments, provider, reference_images, refinement=True)
    rules = _rules(segments, intent, global_prompt, sfx, spoken_dialog, reduce_music, reference_images,
                   current_prompt, refinement_instructions, refine=True)
    raw = ai._provider_raw(inputs, provider, model, api_key, rules + TIMED_CONTRACT, timeout)
    return ai._extract_minimax_h3_prompt(raw, segments)


def build_generic_segments(segments, provider, model, api_key, intent, sfx, spoken_dialog, hdr,
                           reduce_music, timeout=400, reference_images=None):
    inputs = ai._minimax_reference_inputs(segments, provider, reference_images)
    rules = _rules(segments, intent, "", sfx, spoken_dialog, reduce_music, reference_images)
    rules += f'\nRequired transport: return JSON with exactly {len(segments)} "segments" records in input order, each with positive "duration" and "prompt", and a "globalPrompt" string (empty if this workspace has no global prompt). Do not return a unified timed brief.'
    result = ai._parse_json(ai._provider_raw(inputs, provider, model, api_key, rules, timeout))
    if not isinstance(result, dict) or not isinstance(result.get("segments"), list):
        raise ai.AIResponseFormatError("The AI returned no segmented plan.")
    if len(result["segments"]) != len(segments):
        raise ai.AIResponseFormatError("The AI returned the wrong segment count.")
    normalized = []
    for record in result["segments"]:
        if not isinstance(record, dict) or not isinstance(record.get("prompt"), str) or not record["prompt"].strip():
            raise ai.AIResponseFormatError("The AI returned an empty segment prompt.")
        normalized.append({"duration": ai._strict_duration(record.get("duration")),
                           "prompt": record["prompt"].strip(), "imagePrompt": ""})
    global_prompt = result.get("globalPrompt", "")
    if not isinstance(global_prompt, str):
        raise ai.AIResponseFormatError("The AI returned an invalid global prompt.")
    return {"segments": normalized, "globalPrompt": global_prompt.strip()}


def refine_generic_segment(segments, selected_index, provider, model, api_key, intent,
                           requested_total, timeout=400, reference_images=None):
    selected = segments[selected_index]
    inputs = ai._refinement_images(segments, selected_index)
    inputs += [item for item in ai._minimax_reference_inputs([], provider, reference_images)
               if item.get("image")]
    rules = _rules(segments, intent, "", False, False, False, reference_images,
                   selected.prompt, f"Refine only segment {selected_index + 1}; preserve all other segments.", refine=True)
    rules += '\nRequired transport: return only JSON with "prompt" and a positive "duration" for the selected segment.'
    result = ai._parse_json(ai._provider_raw(inputs, provider, model, api_key, rules, timeout))
    if not isinstance(result, dict) or not isinstance(result.get("prompt"), str) or not result["prompt"].strip():
        raise ai.AIResponseFormatError("The AI returned no refined segment prompt.")
    return {"prompt": result["prompt"].strip(), "imagePrompt": "",
            "duration": ai._strict_duration(result.get("duration", selected.duration))}
