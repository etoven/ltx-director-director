"""Independent, frame-aligned conditioning audio and bounded waveform decoding."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from pathlib import Path
import shutil
from uuid import uuid4

from .media import APP_CACHE, safe_media_filename

FPS = 24
AUDIO_SUFFIXES = {'.wav', '.mp3', '.flac', '.ogg', '.opus', '.m4a', '.aac', '.aif', '.aiff', '.webm', '.mp4'}


@dataclass
class AudioClip:
    name: str
    media_path: str
    source_duration: float
    start_frame: int = 0
    trim_start: int = 0
    duration_frames: int = 0
    lane: int = 0
    id: str = ''
    peaks: list[float] = field(default_factory=list)
    muted: bool = False

    def __post_init__(self):
        if not math.isfinite(self.source_duration) or self.source_duration < 1 / FPS:
            raise ValueError('Audio must contain at least one frame of decodable sound.')
        self.id = self.id or uuid4().hex
        self.start_frame = max(0, int(self.start_frame))
        self.lane = max(0, int(self.lane))
        self.trim_start = max(0, min(self.source_frames - 1, int(self.trim_start)))
        self.duration_frames = max(1, min(int(self.duration_frames) or self.source_frames,
                                          self.source_frames - self.trim_start))

    @property
    def source_frames(self):
        return max(1, math.floor(self.source_duration * FPS + 1e-6))

    @property
    def end_frame(self):
        return self.start_frame + self.duration_frames

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(**{key: value[key] for key in cls.__dataclass_fields__ if key in value})

    def move(self, frame, lane=None):
        self.start_frame = max(0, int(frame))
        if lane is not None:
            self.lane = max(0, int(lane))

    def trim_left(self, frame):
        delta = max(-self.trim_start, min(self.duration_frames - 1,
                    max(0, int(frame)) - self.start_frame))
        self.start_frame += delta
        self.trim_start += delta
        self.duration_frames -= delta

    def trim_right(self, frame):
        self.duration_frames = max(1, min(int(frame) - self.start_frame,
                                          self.source_frames - self.trim_start))


def prepare_audio(paths):
    """Copy originals and decode peaks in a worker, retaining bounded sample memory."""
    import av
    import numpy as np
    prepared, errors = [], []
    directory = APP_CACHE / 'conditioning-audio'
    directory.mkdir(parents=True, exist_ok=True)
    for path in paths:
        destination = None
        try:
            source = Path(path)
            if source.suffix.lower() not in AUDIO_SUFFIXES:
                raise ValueError('Unsupported audio format')
            destination = directory / (uuid4().hex + '-' + safe_media_filename(source.name, 'Audio'))
            shutil.copy2(source, destination)
            # A first bounded pass counts exact decoded samples; metadata can
            # include encoder padding or be absent for variable-rate formats.
            def blocks():
                with av.open(str(destination)) as container:
                    if not container.streams.audio:
                        raise ValueError('No decodable audio stream')
                    resampler = av.AudioResampler(format='fltp', layout='mono', rate=8000)
                    for frame in container.decode(audio=0):
                        for converted in resampler.resample(frame):
                            yield converted.to_ndarray().reshape(-1)
                    for converted in resampler.resample(None):
                        yield converted.to_ndarray().reshape(-1)
            samples = sum(len(block) for block in blocks())
            duration = samples / 8000
            count = 1024
            peaks = np.zeros(count, dtype=np.float32)
            offset = 0
            for block in blocks():
                indices = np.minimum(count - 1, (np.arange(len(block)) + offset) * count // max(1, samples))
                np.maximum.at(peaks, indices, np.abs(block))
                offset += len(block)
            prepared.append(AudioClip(source.name, str(destination), duration,
                                      peaks=np.clip(peaks, 0, 1).round(5).tolist()))
        except Exception as error:
            if destination:
                destination.unlink(missing_ok=True)
            errors.append(f'{Path(path).name}: {error}')
    return prepared, errors


def audio_export_records(clips, media_directory, used_names):
    """Copy original audio to LTX input, preserving gaps, overlap and source in/out."""
    from .media import unique_media_filename, comfy_input_references
    records = []
    for clip in clips:
        if clip.muted:
            continue
        source = Path(clip.media_path)
        name = unique_media_filename(safe_media_filename(clip.name, 'Audio', suffix=source.suffix), used_names)
        destination = Path(media_directory) / name
        shutil.copy2(source, destination)
        reference, _ = comfy_input_references(name)
        records.append({'id': clip.id, 'type': 'audio', 'start': clip.start_frame,
                        'length': clip.duration_frames, 'trimStart': clip.trim_start,
                        'audioDurationFrames': clip.source_frames, 'audioFile': reference,
                        'fileName': name, 'fileSize': destination.stat().st_size,
                        'waveformPeaks': clip.peaks, 'lane': clip.lane})
    return records
