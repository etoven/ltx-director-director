"""Portable .LTXD archives with streamed originals and small, eagerly loaded previews."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from .media import APP_CACHE, video_source_dimensions

MANIFEST = "project.json"


def _copy_to_archive(archive: zipfile.ZipFile, name: str, source, digest: bool = False) -> str:
    hasher = hashlib.sha256()
    with archive.open(name, "w", force_zip64=True) as output:
        while chunk := source.read(1024 * 1024):
            output.write(chunk)
            if digest:
                hasher.update(chunk)
    return hasher.hexdigest() if digest else ""


def _source_stream(segment):
    source = Path(segment.media_path)
    if source.is_file():
        return source.open("rb")
    original = getattr(segment, "_archive_source", None)
    if original:
        archive_path, member = original
        archive = zipfile.ZipFile(archive_path)
        stream = archive.open(member)
        return archive, stream
    raise FileNotFoundError(f"Original media unavailable: {segment.name}")


def save_project_archive(path: str | Path, payload: dict, segments: list) -> None:
    """Save atomically; no source file or base64 copy is held in memory."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.NamedTemporaryFile(prefix=".ltxd-", suffix=".tmp", dir=target.parent, delete=False)
    temporary.close()
    try:
        with zipfile.ZipFile(temporary.name, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for index, (frame, segment) in enumerate(zip(payload["frames"], segments)):
                if segment.kind == "text":
                    continue
                preview = Path(segment.preview_path)
                if not preview.is_file():
                    raise FileNotFoundError(f"Preview unavailable: {segment.name}")
                member = f"previews/{index:04d}.jpg"
                with preview.open("rb") as stream:
                    _copy_to_archive(archive, member, stream)
                frame["archivePreview"] = member
                suffix = Path(segment.name).suffix.lower() or Path(segment.media_path).suffix.lower() or ".png"
                member = f"media/{index:04d}{suffix}"
                original = _source_stream(segment)
                if isinstance(original, tuple):
                    input_archive, stream = original
                    try:
                        frame["mediaDigest"] = _copy_to_archive(archive, member, stream, digest=True)
                    finally:
                        stream.close()
                        input_archive.close()
                else:
                    with original:
                        frame["mediaDigest"] = _copy_to_archive(archive, member, original, digest=True)
                frame["archiveSource"] = member
                if segment.kind == "image":
                    # Persist original dimensions without decoding the image at each project load.
                    from PIL import Image
                    if Path(segment.media_path).is_file():
                        with Image.open(segment.media_path) as image:
                            frame["sourceSize"] = list(image.size)
                    elif getattr(segment, "_source_size", None):
                        frame["sourceSize"] = list(segment._source_size)
                elif segment.kind == "video":
                    size = video_source_dimensions(segment.media_path) or getattr(segment, "_source_size", None)
                    if size:
                        frame["sourceSize"] = list(size)
            refs = payload.get("minimaxH3", {}).get("referenceImages", [])
            for index, ref in enumerate(refs):
                if not ref or not ref.get("image"):
                    continue
                header, encoded = ref.pop("image").split(",", 1)
                member = f"references/{index}.png"
                archive.writestr(member, base64.b64decode(encoded))
                ref["archiveImage"] = member
                ref["imageMime"] = header.removeprefix("data:").split(";", 1)[0]
            payload["projectVersion"] = 9
            archive.writestr(MANIFEST, json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
        os.replace(temporary.name, target)
        for frame, segment in zip(payload["frames"], segments):
            if frame.get("archiveSource"):
                # A portable save remains a recovery source if an outside file disappears.
                segment._archive_source = (str(target), frame["archiveSource"])
                segment._media_digest = frame["mediaDigest"]
    finally:
        Path(temporary.name).unlink(missing_ok=True)


def _cached_member(archive: zipfile.ZipFile, name: str, source_path: Path) -> Path:
    stat = source_path.stat()
    cache_key = hashlib.sha256(f"{source_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{name}".encode()).hexdigest()[:24]
    path = APP_CACHE / "projects" / f"{cache_key}{Path(name).suffix}"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            with archive.open(name) as incoming, temporary.open("wb") as output:
                shutil.copyfileobj(incoming, output, length=1024 * 1024)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def load_project_archive(path: str | Path) -> dict:
    source = Path(path)
    with zipfile.ZipFile(source) as archive:
        payload = json.loads(archive.read(MANIFEST))
        for frame in payload.get("frames", []):
            if not frame.get("archiveSource"):
                continue
            frame["_archivePath"] = str(source)
            frame["_archivePreviewPath"] = str(_cached_member(archive, frame["archivePreview"], source))
            # Only the small preview is extracted at project-open time.
            info = archive.getinfo(frame["archiveSource"])
            frame["_archiveMediaPath"] = str(APP_CACHE / "projects" / (
                hashlib.sha256(f"{source.resolve()}:{source.stat().st_size}:{source.stat().st_mtime_ns}:{frame['archiveSource']}".encode()).hexdigest()[:24]
                + Path(frame["archiveSource"]).suffix
            ))
            frame["_archiveSourceSize"] = info.file_size
        for ref in payload.get("minimaxH3", {}).get("referenceImages", []):
            if ref and ref.get("archiveImage"):
                raw = archive.read(ref["archiveImage"])
                ref["image"] = f"data:{ref.get('imageMime', 'image/png')};base64," + base64.b64encode(raw).decode()
    return payload


def materialize_source(segment) -> str:
    """Extract an original only when a provider or export needs it."""
    source = Path(segment.media_path)
    if source.is_file():
        return str(source)
    original = getattr(segment, "_archive_source", None)
    if not original:
        return str(source)
    with zipfile.ZipFile(original[0]) as archive:
        actual = _cached_member(archive, original[1], Path(original[0]))
    segment.media_path = str(actual)
    stat = actual.stat()
    segment._materialized_stat = (stat.st_size, stat.st_mtime_ns)
    return str(actual)


def read_project(path: str | Path) -> dict:
    if zipfile.is_zipfile(path):
        return load_project_archive(path)
    return json.loads(Path(path).read_text(encoding="utf-8"))


def project_thumbnail_data(path: str | Path) -> list[tuple[str, str]]:
    if not zipfile.is_zipfile(path):
        project = read_project(path)
        return [(f"Segment {index + 1}", str(frame.get("previewData", "")))
                for index, frame in enumerate(project.get("frames", [])) if frame.get("previewData")]
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read(MANIFEST))
        return [(f"Segment {index + 1}", "data:image/jpeg;base64," + base64.b64encode(archive.read(frame["archivePreview"])).decode())
                for index, frame in enumerate(manifest.get("frames", [])) if frame.get("archivePreview")]
