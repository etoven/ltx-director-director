"""Prompt-aware splits with fixed source intervals and bounded AI output."""
from __future__ import annotations

import json

from . import ai
from .models import Segment, split_segment_parts
from .prompt_templates import CONTENT_FIDELITY, FRAME_MOTION_GUIDANCE


def divide_segment_prompt(segments: list[Segment], selected_index: int, count: int,
                          style: str, provider: str, model: str, api_key: str,
                          timeout: int = 400) -> list[str]:
    if style not in {"divide", "cuts"}:
        raise ValueError("Unknown prompt split style.")
    if not 0 <= selected_index < len(segments):
        raise ValueError("Select a segment to split.")
    target = segments[selected_index]
    if not target.prompt.strip():
        raise ValueError("Write a segment prompt before dividing its action beats.")
    parts = split_segment_parts(target, count)
    editing = (
        "Keep the action continuous across the parts; do not introduce cuts, "
        "new camera angles or close-ups that were not requested in the original prompt."
        if style == "divide" else
        "This is an explicit request to turn the selected action into separate shots. "
        "Place hard shot cuts between action beats. Establish the action in the first "
        "part, then use close-ups of the relevant subject, gesture, object or reaction "
        "in the following parts. Begin each later prompt with 'Hard cut to a close-up'. "
        "Keep action progression and screen direction coherent; no dissolves or fades. "
        "These are target-video shot instructions, not changes to the original media."
    )
    rules = (
        f"Divide ONLY the SELECTED segment's prompt into exactly {count} ordered parts. "
        "Distribute its action beats chronologically instead of repeating its whole "
        "prompt in each part. Preserve every detail, action, resulting state, named "
        "subject, transformation stage, sound cue and exact dialogue across the parts. "
        "Repeat shared identity/setting details only as needed for continuity. Do not "
        "import actions from neighboring segments or write a combined/global prompt. "
        "Each provided duration is fixed; fit the action and spoken lines within it. "
        "Do not invent files, reference labels, media, IDs, new plot or extra dialogue. "
        + editing + CONTENT_FIDELITY + FRAME_MOTION_GUIDANCE +
        '\nReturn strict JSON only: {"prompts":["first part", "second part"' +
        (', "third part"' if count == 3 else '') + ']}. No other fields.\n' +
        "ORDERED TIMELINE — NEIGHBORS ARE CONTINUITY CONTEXT ONLY:\n" +
        ai._segment_context(segments, selected_index) +
        "\nAUTHORITATIVE SELECTED PROMPT:\n" + target.prompt +
        "\nFIXED PART DURATIONS IN SECONDS:\n" + json.dumps([part.duration for part in parts])
    )
    raw = ai._provider_raw(ai._refinement_images(segments, selected_index),
                           provider, model, api_key, rules, timeout)
    result = ai._parse_json(raw)
    prompts = result.get("prompts") if isinstance(result, dict) else None
    if (not isinstance(prompts, list) or len(prompts) != count
            or any(not isinstance(prompt, str) or not prompt.strip() for prompt in prompts)):
        raise ai.AIResponseFormatError(f"The split needs exactly {count} nonempty prompts; retrying.")
    prompts = [prompt.strip() for prompt in prompts]
    if style == "cuts":
        # Keep the explicitly requested cut and shot size visible in every
        # later part, even if the provider forgets its required opening phrase.
        for index in range(1, count):
            if not prompts[index].casefold().startswith("hard cut to a close-up"):
                prompts[index] = "Hard cut to a close-up. " + prompts[index]
    return prompts
