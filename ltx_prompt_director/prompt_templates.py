"""Data-only AI templates. Substitutions never evaluate user-supplied code."""
from __future__ import annotations

import json
from contextvars import ContextVar
from functools import lru_cache
from importlib.resources import files
from string import Template

WORKSPACE_PROMPTS = ContextVar("workspace_prompts", default=None)

CONTENT_FIDELITY = (
    "\n\nDETAIL FIDELITY TAKES PRIORITY OVER LENGTH GUIDANCE: Treat workspace "
    "word counts, sentence counts, compactness advice and assumed prompt/context "
    "budgets as soft guidance. Preserve every authored action, intermediate "
    "change, visual qualifier, camera instruction, dialogue word and delivery, "
    "sound cue, constraint and resulting state. Transfer content one for one, "
    "retaining wording wherever possible while adapting the required output "
    "structure, references, timing and dialogue notation. Never drop details "
    "merely to meet a length target; expand the production text instead. "
    "During refinement, preserve all content outside the explicitly requested "
    "changes. An explicit user request to remove, replace or shorten content "
    "authorizes that edit; template brevity advice does not."
)

FRAME_MOTION_GUIDANCE = (
    "\n\nROLE-AWARE CAMERA AND MOTION: When timed frames are supplied, interpret "
    "them at their actual anchor times. A START frame is the state at its segment's "
    "beginning: develop motion away from it toward the following anchor. An END "
    "frame is the target at its segment's end: use preceding anchors to approach "
    "and reach it, never treat it as the opening state or begin its arrival "
    "motion only after it has been reached. Compare the nearest preceding and "
    "following timeline visuals, even across text-only beats, using their labels, "
    "roles and anchor times. Respect intervening authored actions. Compare "
    "background landmarks/parallax, viewpoint, subject scale and position, pose "
    "and facing direction; distinguish camera pan/track/zoom from subject or "
    "object movement. Screen-left subject or background displacement alone does "
    "not establish a left camera pan. A still does not establish motion direction. "
    "Do not reverse the temporal comparison for an end frame, infer movement "
    "between identical states, or invent a pan/cut to explain ambiguous framing. "
    "Coincident anchors allow no travel time: maintain continuity rather than "
    "inventing an instantaneous camera move. Untimed references supply their "
    "chosen attributes, not chronological frame endpoints. Preserve explicitly "
    "authored camera/action directions unless the user asks to correct them; "
    "when evidence is ambiguous, do not silently reverse the requested direction."
)


@lru_cache(maxsize=12)
def stock_prompt(engine: str, kind: str) -> str:
    value = json.loads(files("ltx_prompt_director").joinpath(f"{'engine_templates' if engine in {'generic', 'minimax_frames', 'minimax_references'} else 'workspace_templates'}/{engine}.json").read_text(encoding="utf-8"))
    return value["generation_prompts"][kind]


def render_workspace_prompt(engine: str, kind: str, values: dict) -> str:
    configured = WORKSPACE_PROMPTS.get()
    source = configured.get(kind, stock_prompt(engine, kind)) if isinstance(configured, dict) else stock_prompt(engine, kind)
    template = Template(source)
    result = template.safe_substitute(values)
    # Input facts and edit targets remain available even in a completely custom template.
    present = {match.group("named") or match.group("braced") for match in Template.pattern.finditer(source)}
    for key in ("director_intent", "authoritative_intent", "current_prompt", "refinement_instructions", "asset_map", "intervals", "reference_map", "ordered_plan"):
        if key in values and key not in present:
            result += f"\n\n{key.replace('_', ' ').upper()}:\n{values[key]}"
    return result + CONTENT_FIDELITY + FRAME_MOTION_GUIDANCE
