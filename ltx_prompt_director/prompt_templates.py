"""Data-only AI templates. Substitutions never evaluate user-supplied code."""
from __future__ import annotations

import json
from contextvars import ContextVar
from functools import lru_cache
from importlib.resources import files
from string import Template

WORKSPACE_PROMPTS = ContextVar("workspace_prompts", default=None)
WORKSPACE_ENGINE = ContextVar("workspace_engine", default=None)
WORKSPACE_OPERATION = ContextVar("workspace_operation", default='generate')

MINIMAX_FIRST_SHOT = "[Shot 1]"
MINIMAX_SHOT_TIMING = (
    "\n\nOFFICIAL H3 SHOT TIMING: The first shot has no timestamp. "
    "Later shots use [Shot N] At MM:SS.mmm, with strictly increasing cut times "
    "inside the video duration. Frame anchors are separate from cuts: a last "
    "frame is reached at its endpoint, not treated as a shot beginning there. "
    "Do not create a cut for every timeline planning beat."
)


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


def render_workspace_prompt(engine: str, kind: str, values: dict, *, domain='unified') -> str:
    engine = WORKSPACE_ENGINE.get() or engine
    configured = WORKSPACE_PROMPTS.get()
    source = configured.get(kind, stock_prompt(engine, kind)) if isinstance(configured, dict) else stock_prompt(engine, kind)
    is_h3 = engine in {'minimax_frames', 'minimax_references'}
    scoped = is_h3 and domain != 'unified'
    if scoped and source == stock_prompt(engine, kind):
        # Factory instructions describe a complete production document. They are
        # not the instruction template for a segment or director-notes editor.
        source = f'{kind.capitalize()} the current {domain} target using the supplied edit requests and context.'
    if is_h3:
        defaults = {name: 'Not supplied; use the current operation context.' for name in (
            'workflow_name', 'workflow_direction', 'total_duration', 'director_intent',
            'global_direction', 'asset_map', 'ordered_plan', 'sound_effects', 'dialogue',
            'music', 'current_prompt', 'refinement_instructions')}
        values = {**defaults, **values}
    template = Template(source)
    result = template.safe_substitute(values)
    # Input facts and edit targets remain available even in a completely custom template.
    present = {match.group("named") or match.group("braced") for match in Template.pattern.finditer(source)}
    for key in ("director_intent", "global_direction", "authoritative_intent", "current_prompt", "refinement_instructions", "asset_map", "intervals", "reference_map", "ordered_plan", "selected_segment", "duration_instruction"):
        if key in values and key not in present:
            result += f"\n\n{key.replace('_', ' ').upper()}:\n{values[key]}"
    if scoped:
        result = ('WORKSPACE SETTINGS — apply creative guidance only within the current work domain:\n'
                  + result + scoped_h3_rules(domain))
    elif is_h3:
        result += official_h3_rules(engine)
    return result + CONTENT_FIDELITY + ('' if engine == 'minimax_prompt_refinement' else FRAME_MOTION_GUIDANCE)


def scoped_h3_rules(domain):
    scopes = {
        'segment': 'Write only the selected segment’s action prose. Neighboring segments and Global notes are context, not editing targets. Preserve the selected draft’s unrelated wording. Change its duration only when the current request authorizes it.',
        'global': 'Write only shared director instructions in the Global field. Do not write finished Unified production prose or rewrite segment prompts. Return timing changes only when explicitly requested.',
        'timeline': 'Write the requested timeline records independently, retaining their count, identities, media and order. Each prompt describes only its own beat. Shared director guidance belongs in globalPrompt.',
        'timing': 'Return only the requested duration values. All prompt text, media and identities are immutable.',
    }
    return ('\n\nMINIMAX H3 WORK DOMAIN — ' + domain.upper() + ' (authoritative):\n'
            + scopes[domain]
            + '\nDo not produce a Unified document in any field. Do not add production section headings, '
            'a summary, reference definitions, retention analysis, a whole-video soundtrack section, '
            'or a keyframe-alignment preamble. These belong to Unified. No shot numbering or cut labels '
            'unless explicitly requested for this target. This scope overrides document-format requests '
            'in workspace settings and source context.\n'
            'Relevant official H3 writing conventions: describe concrete visible actions and state changes, '
            'composition, camera motion and synchronized sound in natural prose. Respect start/end-frame '
            'anchors and continuity. Camera motion may specify type, amplitude and speed when meaningful. '
            'Preserve existing reference labels and their roles; do not redefine or renumber them. '
            'For requested speech use stable (S1) speaker IDs and <d>[Language] exact spoken words</d>, '
            'with identity and delivery outside the dialogue tags. Preserve original-language dialogue, '
            'lyrics and quoted visible text. A voiceover uses says in an off-screen voiceover and keeps '
            'the corresponding on-screen lips closed. Use <scenetrans> for speech continuing across '
            'a requested cut and <cutoff> for truncated speech. Follow current sound and dialogue controls.\n')


@lru_cache(maxsize=2)
def official_h3_rules(engine):
    guides = json.loads(files('ltx_prompt_director').joinpath('engine_templates/h3_official_skill.json').read_text())
    return ('\n\nOFFICIAL MINIMAX H3 SKILL — authoritative model conventions; supersedes conflicting legacy format instructions.\n'
            + guides['skill'] + '\n' + guides['base']
            + ('\n' + guides['reference'] if engine == 'minimax_references' else '')
            + '\nAPPLICATION SCOPE: The operation below determines which fields may change and its JSON transport. '
            'For a segment, global-direction or timing-only operation, apply the relevant H3 conventions only to '
            'that target; do not return an entire production document. Timeline beats are not automatically shots. '
            'Preserve media, identities, ordering and all fields outside the explicit operation scope.\n')


def changed_direction_request(current, previous=None):
    changed = {key: value for key, value in current.items()
               if (previous is None and value.strip()) or (previous is not None and value != previous.get(key, ''))}
    if not changed:return ''
    return ('CURRENT GLOBAL EDIT REQUEST — the Global input has changed since the last accepted production result. '
            'Apply these latest directions across the existing production draft, including Summary and all affected '
            'shot, subject, sound and music sections. These are explicit current edit requests, not background '
            'continuity evidence. They supersede conflicting earlier wording only where requested; preserve '
            'unrelated edits and omissions. Do not paste the instructions verbatim into Summary. '
            'An empty updated input removes that source of guidance without erasing unrelated authored prose.\n'
            + '\n\n'.join(key.upper() + ':\n' + (value or '(cleared)') for key, value in changed.items()))
