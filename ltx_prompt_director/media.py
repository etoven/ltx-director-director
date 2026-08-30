from __future__ import annotations

import base64
import io
import subprocess
import tempfile
import wave
from pathlib import Path

import imageio.v2 as imageio
import imageio_ffmpeg
from PIL import Image


APP_CACHE = Path(tempfile.gettempdir()) / "ltx-director-director"
APP_CACHE.mkdir(parents=True, exist_ok=True)


def data_url(path: str, max_edge: int | None = None, quality: int = 82) -> str:
    source = Path(path)
    if max_edge:
        with Image.open(source) as image:
            image = image.convert("RGB")
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, "WEBP", quality=quality, method=6)
        return "data:image/webp;base64," + base64.b64encode(output.getvalue()).decode()
    mime = "video/webm" if source.suffix.lower() == ".webm" else _image_mime(source)
    return f"data:{mime};base64," + base64.b64encode(source.read_bytes()).decode()


def write_data_url(value: str, destination: Path) -> None:
    _, encoded = value.split(",", 1)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(base64.b64decode(encoded))


def capture_webm_preview(path: str, fps_out: int = 24) -> tuple[str, int, int]:
    reader = imageio.get_reader(path, "ffmpeg")
    try:
        meta = reader.get_meta_data()
        fps = float(meta.get("fps") or fps_out)
        duration = float(meta.get("duration") or 1.0)
        source_frames = max(1, round(duration * fps))
        source_index = max(0, min(source_frames - 1, round(max(0.0, duration - 1.0) * fps)))
        try:
            frame = reader.get_data(source_index)
        except Exception:
            frame = reader.get_data(max(0, source_frames - 1))
        preview = APP_CACHE / f"{Path(path).stem}-{Path(path).stat().st_mtime_ns}.jpg"
        Image.fromarray(frame).convert("RGB").save(preview, "JPEG", quality=90)
        duration_frames = max(1, round(duration * fps_out))
        trim_start = max(0, duration_frames - fps_out)
        return str(preview), duration_frames, trim_start
    finally:
        reader.close()


def prepare_media(path: str) -> tuple[str, str, int | None, int | None]:
    source = Path(path)
    if source.suffix.lower() == ".webm":
        preview, frames, trim = capture_webm_preview(path)
        return "video", preview, frames, trim
    with Image.open(source) as image:
        image.verify()
    return "image", path, None, None


def extract_audio_for_export(source_path: str, destination: str | Path, fps: int = 24) -> tuple[int, list[float]]:
    """Extract a video's complete audio stream and return its frame duration and peaks."""
    source = Path(source_path)
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", str(source), "-vn",
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output),
    ]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode or not output.is_file() or output.stat().st_size <= 44:
        output.unlink(missing_ok=True)
        raise ValueError("No decodable audio track was found.")
    with wave.open(str(output), "rb") as audio:
        frame_count = audio.getnframes()
        sample_rate = audio.getframerate()
        sample_width = audio.getsampwidth()
        channels = audio.getnchannels()
        raw = audio.readframes(frame_count)
    duration_frames = max(1, round(frame_count / max(1, sample_rate) * fps))
    return duration_frames, waveform_peaks(raw, sample_width, channels)


def waveform_peaks(raw: bytes, sample_width: int, channels: int, count: int = 200) -> list[float]:
    """Return normalized peak amplitudes in evenly sized waveform buckets."""
    if sample_width != 2 or not raw:
        return [0.0] * count
    samples = memoryview(raw).cast("h")
    if channels > 1:
        samples = samples[::channels]
    bucket = max(1, len(samples) // count)
    peaks = []
    for index in range(count):
        chunk = samples[index * bucket:min(len(samples), (index + 1) * bucket)]
        peaks.append(max((abs(value) for value in chunk), default=0) / 32768.0)
    return peaks


def _image_mime(path: Path) -> str:
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}.get(path.suffix.lower(), "image/png")
