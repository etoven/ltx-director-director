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
    start: float | None = None

    def __post_init__(self) -> None:
        self.id = self.id or str(uuid4())
        self.duration = max(0.01, round(float(self.duration), 2))
        if self.start is not None:
            self.start = max(0.0, round(float(self.start), 2))

    @property
    def exists(self) -> bool:
        return Path(self.media_path).exists()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "Segment":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value[key] for key in allowed if key in value})


@dataclass
class AudioSegment:
    name: str
    media_path: str
    start: float = 0.0
    duration: float = 1.0
    trim_start: int = 0
    audio_duration_frames: int = 0
    waveform_peaks: list[float] | None = None
    coupled_to: str | None = None
    id: str = ""

    def __post_init__(self) -> None:
        self.id = self.id or str(uuid4())
        self.start = max(0.0, round(float(self.start) * 24) / 24)
        self.duration = max(1 / 24, round(float(self.duration) * 24) / 24)
        self.trim_start = max(0, int(self.trim_start or 0))
        self.audio_duration_frames = max(1, int(self.audio_duration_frames or round(self.duration * 24)))
        self.waveform_peaks = [max(0.0, min(1.0, float(value))) for value in (self.waveform_peaks or [])]

    @property
    def exists(self) -> bool:
        return Path(self.media_path).exists()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> "AudioSegment":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value[key] for key in allowed if key in value})
