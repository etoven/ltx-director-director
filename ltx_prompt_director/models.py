from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
import math
from pathlib import Path
from uuid import uuid4


@dataclass
class Segment:
    name: str
    media_path: str
    preview_path: str
    kind: str = "image"
    role: str = "start"
    prompt: str = ""
    duration: float = 5.0
    media_duration_frames: int | None = None
    trim_start: int | None = None
    id: str = ""
    image_prompt: str = ""
    prompt_detached: bool = False
    shot_trimmed: bool = False
    artwork_prompt: str = ""
    artwork_status: str = ""  # awaiting, generating, failed, complete
    artwork_target_role: str = "start"
    render_group: int = 0
    source_project_id: str = ""
    source_prompt_type: str = ""

    def __post_init__(self) -> None:
        self.id = self.id or str(uuid4())
        self.duration = max(0.01, round(float(self.duration), 6))

    @property
    def exists(self) -> bool:
        return Path(self.media_path).exists()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "Segment":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value[key] for key in allowed if key in value})


def order_segments_by_ids(segments: list[Segment], ordered_ids: list[str]) -> list[Segment]:
    """Return mixed media/text segments in the timeline widget's ID order."""
    by_id = {segment.id: segment for segment in segments}
    ordered = [by_id[segment_id] for segment_id in ordered_ids if segment_id in by_id]
    seen = set(ordered_ids)
    ordered.extend(segment for segment in segments if segment.id not in seen)
    return ordered


def split_segment(segment: Segment, offset: float, fps: int = 24) -> tuple[Segment, Segment]:
    """Split at a local frame boundary, retaining source metadata and total time."""
    if not math.isfinite(offset) or fps <= 0:
        raise ValueError("Choose a split point inside the segment.")
    frames = round(offset * fps)
    left_duration = round(frames / fps, 6)
    right_duration = round(segment.duration - left_duration, 6)
    if frames < 1 or right_duration < 1 / fps - 0.000001:
        raise ValueError("Each side of the split needs at least one frame.")
    left, right = copy.deepcopy(segment), copy.deepcopy(segment)
    left.duration, right.duration = left_duration, right_duration
    right.id = str(uuid4())
    if segment.kind == "video":
        left.trim_start = segment.trim_start or 0
        right.trim_start = left.trim_start + frames
        left.shot_trimmed = right.shot_trimmed = True
    return left, right


def split_segment_parts(segment: Segment, count: int, fps: int = 24) -> list[Segment]:
    """Make equal frame-aligned parts without changing the original interval."""
    if type(count) is not int or count not in (2, 3) or fps <= 0:
        raise ValueError("Choose a two-way or three-way split.")
    parts, remaining, elapsed = [], segment, 0.0
    for index in range(1, count):
        boundary = round(segment.duration * index / count * fps) / fps
        left, remaining = split_segment(remaining, boundary - elapsed, fps)
        parts.append(left)
        elapsed += left.duration
    return parts + [remaining]


def roll_segment_boundary(left: Segment, right: Segment, offset: float,
                          fps: int = 24) -> tuple[Segment, Segment]:
    """Transfer frame-aligned time across a boundary, preserving the pair's end."""
    if not math.isfinite(offset) or fps <= 0:
        raise ValueError("Choose a finite boundary offset.")
    minimum = math.ceil(1 - left.duration * fps - 0.000001)
    maximum = math.floor(right.duration * fps - 1 + 0.000001)
    if left.kind == "video" and left.media_duration_frames is not None:
        maximum = min(maximum, math.floor(left.media_duration_frames -
                      (left.trim_start or 0) - left.duration * fps + 0.000001))
    if right.kind == "video":
        minimum = max(minimum, -(right.trim_start or 0))
    if minimum > maximum:
        raise ValueError("This boundary has no room to move within its source footage.")
    frames = max(minimum, min(maximum, round(offset * fps)))
    updated_left, updated_right = copy.deepcopy(left), copy.deepcopy(right)
    if frames:
        updated_left.duration = round(left.duration + frames / fps, 6)
        updated_right.duration = round(left.duration + right.duration - updated_left.duration, 6)
        if left.kind == "video":
            updated_left.trim_start = left.trim_start or 0
            updated_left.shot_trimmed = True
        if right.kind == "video":
            updated_right.trim_start = (right.trim_start or 0) + frames
            updated_right.shot_trimmed = True
    return updated_left, updated_right


def text_segment_from_ltx(value: dict, index: int, fps: float) -> Segment:
    """Create an editable text-only segment from an LTX timeline record."""
    if value.get("type") != "text":
        raise ValueError("The LTX timeline record is not a text segment.")
    return Segment(
        str(value.get("fileName") or f"Text {index + 1}"),
        "",
        "",
        "text",
        "text",
        str(value.get("prompt") or ""),
        max(0.01, round(float(value.get("length", fps)) / fps, 2)),
        id=str(value.get("id") or ""),
        image_prompt=str(value.get("imagePrompt") or value.get("image_prompt") or ""),
    )
