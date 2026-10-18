"""Timing-only sequence planning, bounded by the target length and source media."""
from __future__ import annotations

import copy
import json
import math

from . import ai
from .models import Segment
from .prompt_templates import FRAME_MOTION_GUIDANCE

TIMING_FIELDS = ("duration", "trim_start", "shot_trimmed")


def _same_source(left: Segment, right: Segment) -> bool:
    return any(a and a == b for a, b in (
        (left.media_path, right.media_path),
        (getattr(left, '_media_digest', None), getattr(right, '_media_digest', None)),
        (getattr(left, '_archive_source', None), getattr(right, '_archive_source', None))))


def _frame_allocation(weights: list[float], total: int, minimums: list[int],
                      maximums: list[int]) -> list[int]:
    if total < sum(minimums) or total > sum(maximums):
        raise ValueError("The target length does not fit the available source footage and one-frame minimums.")
    low, high = 0.0, max(1.0, total / min(weights))
    for _ in range(80):
        scale = (low + high) / 2
        values = [min(cap, max(floor, weight * scale))
                  for weight, floor, cap in zip(weights, minimums, maximums)]
        if sum(values) < total:
            low = scale
        else:
            high = scale
    values = [min(cap, max(floor, weight * high))
              for weight, floor, cap in zip(weights, minimums, maximums)]
    frames = [min(cap, max(floor, math.floor(value)))
              for value, floor, cap in zip(values, minimums, maximums)]
    remaining = total - sum(frames)
    for index in sorted(range(len(values)), key=lambda i: values[i] - frames[i], reverse=True):
        if remaining and frames[index] < maximums[index]:
            frames[index] += 1
            remaining -= 1
    if remaining:
        raise ValueError("Could not distribute the requested timing within source limits.")
    return frames


def retimed_segments(segments: list[Segment], durations: list[float], target: float,
                     fps: int = 24) -> list[Segment]:
    if (not segments or not isinstance(durations, list) or len(durations) != len(segments)
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) or value <= 0 for value in durations)
            or not math.isfinite(target) or target <= 0 or fps <= 0
            or not math.isfinite(target * fps)):
        raise ValueError("Provide one positive finite duration for every segment and a target length.")
    total_frames = math.floor(target * fps + .000001)
    remainder = round(target - total_frames / fps, 6)
    groups = []
    for index, segment in enumerate(segments):
        if (groups and segment.kind == segments[index - 1].kind == 'video'
                and _same_source(segments[index - 1], segment)
                and (segment.trim_start or 0) == (segments[index - 1].trim_start or 0)
                    + round(segments[index - 1].duration * fps)):
            groups[-1].append(index)
        else:
            groups.append([index])
    caps = []
    for group in groups:
        first = segments[group[0]]
        cap = (first.media_duration_frames - (first.trim_start or 0)
               if first.kind == 'video' and first.media_duration_frames is not None else total_frames + 1)
        caps.append(max(0, cap))
    allocations = _frame_allocation([sum(durations[i] for i in group) for group in groups],
                                    total_frames, [len(group) for group in groups], caps)
    extra_group = next((i for i in range(len(groups) - 1, -1, -1)
                        if allocations[i] < caps[i]), None)
    if remainder > .000001 and extra_group is None:
        raise ValueError("The target length exceeds available video source footage.")
    updated = copy.deepcopy(segments)
    for group_index, (group, frame_count) in enumerate(zip(groups, allocations)):
        frames = _frame_allocation([durations[i] for i in group], frame_count,
                                   [1] * len(group), [frame_count] * len(group))
        trim = segments[group[0]].trim_start or 0
        for position, (index, count) in enumerate(zip(group, frames)):
            item = updated[index]
            item.duration = round(count / fps + (remainder if group_index == extra_group
                                  and position == len(group) - 1 else 0), 6)
            if item.kind == 'video':
                if item.duration != segments[index].duration or trim != (segments[index].trim_start or 0):
                    item.trim_start, item.shot_trimmed = trim, True
                trim += count
    # Absorb per-segment decimal rounding into the same part chosen for the
    # sub-frame remainder, without changing any whole source-frame boundary.
    residue = round(target - sum(item.duration for item in updated), 6)
    if residue:
        destination = groups[extra_group if extra_group is not None else -1][-1]
        updated[destination].duration = round(updated[destination].duration + residue, 6)
    return updated


def plan_sequence_timing(segments: list[Segment], provider: str, model: str, api_key: str,
                         target: float, intent: str, combined_prompt: str,
                         reference_images: list | None = None, timeout: int = 400) -> dict:
    inventory = ai.reference_inventory(segments, reference_images)
    for record in inventory:
        if 'timeline_index' in record:
            segment = segments[record['timeline_index']]
            record['duration'] = segment.duration
            if segment.kind == 'video':
                record['trim_start_frame'] = segment.trim_start or 0
                record['source_duration'] = (segment.media_duration_frames / 24
                                             if segment.media_duration_frames is not None else None)
    rules = (
        "Perform a TIMING-ONLY pass over the entire ordered timeline. All segment "
        "and unified prompt text is immutable. Do not rewrite, summarize, generate "
        "or return any prompt text, image directions, media paths, IDs or new shots. "
        "Return one duration per existing segment in exactly its current order. "
        "Consider exact dialogue length, language and delivery, pauses, breath, lip "
        "sync, action complexity, transformation stages, camera movement, start/end "
        "frame roles, neighboring frames, hard cuts, close-ups, reactions, shot "
        "pacing, music/sound cues and source-video trims/available footage. "
        "Use only actually supplied media observations; never invent unheard audio. "
        "Timeline prompts are authoritative. The combined draft is secondary pacing "
        "context and cannot replace current timeline beats. Preserve all details "
        "by changing their allotted time only. Minimize unnecessary changes. "
        f"Fit exactly {target:.6f} seconds overall, with at least one frame per "
        "segment at 24 fps. The app snaps timing to source-frame boundaries and "
        "enforces video source limits. Do not drop dialogue to fit the budget. "
        '\nReturn only strict JSON: {"durations":[3.0,2.0,...]}. No prompt fields.\n'
        + FRAME_MOTION_GUIDANCE + "\nAUTHORITATIVE TIMELINE AND REFERENCES:\n"
        + json.dumps(inventory, ensure_ascii=False)
        + "\nDIRECTOR GUIDANCE:\n" + intent
        + "\nCOMBINED DRAFT — PACING CONTEXT ONLY:\n" + combined_prompt
    )
    raw = ai._provider_raw(ai._minimax_reference_inputs(segments, provider, reference_images),
                           provider, model, api_key, rules, timeout)
    response = ai._parse_json(raw)
    durations = response.get('durations') if isinstance(response, dict) else None
    try:
        updated = retimed_segments(segments, durations, target)
    except ValueError as error:
        raise ai.AIResponseFormatError(f"Invalid timing-only proposal: {error}") from error
    return {'durations': [item.duration for item in updated]}
