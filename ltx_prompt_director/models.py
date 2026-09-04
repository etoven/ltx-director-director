from __future__ import annotations

from dataclasses import asdict, dataclass
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

    def __post_init__(self) -> None:
        self.id = self.id or str(uuid4())
        self.duration = max(1.0, round(float(self.duration) * 2) / 2)

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


def text_segment_from_ltx(value: dict, index: int, fps: float) -> Segment:
    """Create an editable text-only segment from an LTX Director record."""
    if value.get("type") != "text":
        raise ValueError("The LTX Director record is not a text segment.")
    return Segment(
        str(value.get("fileName") or f"Text {index + 1}"),
        "",
        "",
        "text",
        "text",
        str(value.get("prompt") or ""),
        max(1.0, float(value.get("length", fps)) / fps),
        id=str(value.get("id") or ""),
    )
