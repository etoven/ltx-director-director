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

