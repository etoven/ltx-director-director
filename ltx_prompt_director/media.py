from __future__ import annotations

import base64
import io
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from urllib.parse import quote

import imageio_ffmpeg
from PIL import Image


APP_CACHE = Path(tempfile.gettempdir()) / "ltx-director-director"
APP_CACHE.mkdir(parents=True, exist_ok=True)
TIMELINE_VIDEO_SUFFIXES = {".webm", ".mp4"}


def safe_media_filename(value: str, fallback_stem: str = "media") -> str:
    """Return a filesystem-safe media name containing no whitespace."""
    source = Path(value)
    stem = re.sub(r"\s+", "_", source.stem.strip())
    stem = re.sub(r'[^\w.-]+', "_", stem, flags=re.UNICODE)
    stem = re.sub(r"_+", "_", stem).strip("._-") or fallback_stem
    suffix = re.sub(r'[^A-Za-z0-9.]', "", source.suffix)
    return f"{stem}{suffix}"


def unique_media_filename(filename: str, used_names: set[str]) -> str:
    """Keep colliding media names unique within one export."""
    source = Path(filename)
    candidate = filename
    number = 2
    while candidate.casefold() in used_names:
        candidate = f"{source.stem}_{number}{source.suffix}"
        number += 1
    used_names.add(candidate.casefold())
    return candidate


def copy_media_for_export(source_path: str, display_name: str, destination: Path, used_names: set[str], fallback_stem: str) -> Path:
    """Copy media into ComfyUI with a safe, collision-free filename."""
    source = Path(source_path)
    name = unique_media_filename(safe_media_filename(display_name or source.name, fallback_stem), used_names)
    target = destination / name
    if source.resolve() != target.resolve():
        shutil.copy2(source, target)
    return target


def comfy_input_references(filename: str, subfolder: str = "whatdreamscost") -> tuple[str, str]:
    """Return LTX Director's Comfy-relative file and browser preview references."""
    relative = f"{subfolder}/{filename}"
    preview = f"/api/view?filename={quote(filename, safe='')}&type=input&subfolder={quote(subfolder, safe='')}"
    return relative, preview


def data_url(path: str, max_edge: int | None = None, quality: int = 82) -> str:
    source = Path(path)
    if max_edge:
        with Image.open(source) as image:
            image = image.convert("RGB")
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, "WEBP", quality=quality, method=6)
        return "data:image/webp;base64," + base64.b64encode(output.getvalue()).decode()
    video_mimes = {".webm": "video/webm", ".mp4": "video/mp4"}
    mime = video_mimes.get(source.suffix.lower(), _image_mime(source))
    return f"data:{mime};base64," + base64.b64encode(source.read_bytes()).decode()


def probe_video_duration(path: str) -> float:
    """Return a video's duration in seconds using the bundled FFmpeg binary."""
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    probe = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True, check=False)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", probe.stderr)
    if not match:
        return 0.0
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))


def video_storyboard_data_urls(path: str, count: int = 6, max_edge: int = 512) -> list[dict[str, float | str]]:
    """Sample timestamped frames across a video for providers without native video input."""
    source = Path(path)
    if not source.is_file():
        return []
    duration = probe_video_duration(str(source))
    if duration <= 0:
        return []
    count = max(2, min(10, int(count)))
    final_timestamp = max(0.0, duration - min(.1, duration / (count * 2)))
    timestamps = [final_timestamp * index / (count - 1) for index in range(count)]
    frames: list[dict[str, float | str]] = []
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory(prefix="ltx-video-analysis-") as directory:
        for index, timestamp in enumerate(timestamps):
            output = Path(directory) / f"frame-{index:02d}.jpg"
            result = subprocess.run(
                [ffmpeg, "-y", "-ss", f"{timestamp:.3f}", "-i", str(source), "-frames:v", "1", "-q:v", "2", str(output)],
                capture_output=True,
                check=False,
            )
            if not result.returncode and output.is_file():
                frames.append({"timestamp": round(timestamp, 3), "image": data_url(str(output), max_edge=max_edge)})
    return frames


def write_data_url(value: str, destination: Path) -> None:
    _, encoded = value.split(",", 1)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(base64.b64decode(encoded))


def capture_video_preview(path: str, fps_out: int = 24) -> tuple[str, int, int]:
    source = Path(path)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    duration = probe_video_duration(str(source)) or 1.0
    preview = APP_CACHE / f"{source.stem}-{source.stat().st_mtime_ns}.jpg"
    seek = max(0.0, duration - 1.0)
    command = [ffmpeg, "-y", "-ss", f"{seek:.3f}", "-i", str(source), "-frames:v", "1", "-q:v", "2", str(preview)]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode or not preview.is_file():
        fallback = [ffmpeg, "-y", "-i", str(source), "-frames:v", "1", "-q:v", "2", str(preview)]
        result = subprocess.run(fallback, capture_output=True, check=False)
    if result.returncode or not preview.is_file():
        raise ValueError("Could not decode a preview frame from the video file.")
    duration_frames = max(1, round(duration * fps_out))
    trim_start = max(0, duration_frames - fps_out)
    return str(preview), duration_frames, trim_start


def prepare_media(path: str) -> tuple[str, str, int | None, int | None]:
    source = Path(path)
    if source.suffix.lower() in TIMELINE_VIDEO_SUFFIXES:
        preview, frames, trim = capture_video_preview(path)
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
