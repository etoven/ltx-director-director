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

from .media import (APP_CACHE, MEDIA_SUFFIXES, media_mime_from_header,
                    safe_media_filename, video_source_dimensions)

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
        try:
            stream = archive.open(_resolve_source_member(archive, segment, member))
        except Exception:
            archive.close()
            raise
        return archive, stream
    raise FileNotFoundError(f"Original media unavailable: {segment.name}")


def clear_archived_source(segment) -> None:
    """A replacement must never fall back to the previous image or its digest."""
    for key in ("_archive_source", "_media_digest", "_source_size", "_source_suffix", "_materialized_stat"):
        if hasattr(segment, key):
            delattr(segment, key)


def _resolve_source_member(archive: zipfile.ZipFile, segment, member: str) -> str:
    """Resolve source identity after a saved split or reorder changes ZIP indices."""
    names = set(archive.namelist())
    frames = json.loads(archive.read(MANIFEST)).get("frames", []) if MANIFEST in names else []
    digest = getattr(segment, "_media_digest", "")
    for frame in frames:
        if (frame.get("id") == segment.id and frame.get("archiveSource") in names
                and (not digest or frame.get("mediaDigest") == digest)):
            return frame["archiveSource"]
    if digest:
        for frame in frames:
            if frame.get("mediaDigest") == digest and frame.get("archiveSource") in names:
                return frame["archiveSource"]
    if member in names and (not digest or not frames or any(
            f.get("archiveSource") == member and f.get("mediaDigest") == digest for f in frames)):
        return member
    raise FileNotFoundError(
        f"Original media unavailable for {segment.name}. Reopen the project or replace this segment's media.")


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
                allowed = ({".webm", ".mp4", ".mov", ".mkv", ".avi", ".m4v"} if segment.kind == "video"
                           else {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"})
                suffix = next((value for value in (Path(segment.media_path).suffix.lower(),
                                                   Path(segment.name).suffix.lower()) if value in allowed),
                              ".webm" if segment.kind == "video" else ".png")
                member = f"media/{index:04d}{suffix}"
                original = _source_stream(segment)
                # Older projects may use a narrative as the suffix, or label an
                # MP4 as WebM. Normalize the archive name from the actual stream.
                input_stream = original[1] if isinstance(original, tuple) else original
                header = input_stream.read(4096)
                input_stream.seek(0)
                suffix = MEDIA_SUFFIXES.get(media_mime_from_header(header, suffix), suffix)
                member = f"media/{index:04d}{suffix}"
                frame["name"] = safe_media_filename(segment.name, f"Segment_{index + 1:02d}", suffix=suffix)
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
                if ref and ref.get('kind') in {'video','audio'}:
                    for key, archive_key in [('path','archiveMedia'),('audio_path','archiveAudio')]:
                        if ref.get(key):
                            source = Path(ref.pop(key))
                            member = f'references/{index}-{key}{source.suffix}'
                            with source.open('rb') as stream:
                                _copy_to_archive(archive,member,stream)
                            ref[archive_key] = member
                if not ref or not ref.get("image"):
                    continue
                header, encoded = ref.pop("image").split(",", 1)
                member = f"references/{index}.png"
                archive.writestr(member, base64.b64decode(encoded))
                ref["archiveImage"] = member
                ref["imageMime"] = header.removeprefix("data:").split(";", 1)[0]
            payload["projectVersion"] = 9
            archive.writestr(MANIFEST, json.dumps(payload, separators=(",", ":"), ensure_ascii=False), compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
        os.replace(temporary.name, target)
        for frame, segment in zip(payload["frames"], segments):
            if frame.get("archiveSource"):
                # A portable save remains a recovery source if an outside file disappears.
                segment._archive_source = (str(target), frame["archiveSource"])
                segment._media_digest = frame["mediaDigest"]
    finally:
        Path(temporary.name).unlink(missing_ok=True)


def _member_suffix(archive: zipfile.ZipFile, name: str) -> str:
    with archive.open(name) as stream:
        header = stream.read(4096)
    return MEDIA_SUFFIXES.get(media_mime_from_header(header, Path(name).suffix), ".bin")


def _member_cache_path(name: str, source_path: Path, suffix: str) -> Path:
    stat = source_path.stat()
    cache_key = hashlib.sha256(f"{source_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{name}".encode()).hexdigest()[:24]
    return APP_CACHE / "projects" / f"{cache_key}{suffix}"


def _cached_member(archive: zipfile.ZipFile, name: str, source_path: Path) -> Path:
    path = _member_cache_path(name, source_path, _member_suffix(archive, name))
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
            suffix = _member_suffix(archive, frame["archiveSource"])
            frame["_archiveSourceSuffix"] = suffix
            frame["name"] = safe_media_filename(frame.get("name", ""), "Segment", suffix=suffix)
            frame["_archiveMediaPath"] = str(_member_cache_path(frame["archiveSource"], source, suffix))
            frame["_archiveSourceSize"] = info.file_size
        for ref in payload.get("minimaxH3", {}).get("referenceImages", []):
            if ref:
                for key, archive_key in [('path','archiveMedia'),('audio_path','archiveAudio')]:
                    if ref.get(archive_key):
                        ref[key] = str(_cached_member(archive,ref[archive_key],source))
                        if key == 'path':
                            ref['name'] = safe_media_filename(ref.get('name', ''), 'Reference', suffix=Path(ref[key]).suffix)
            if ref and ref.get("archiveImage"):
                raw = archive.read(ref["archiveImage"])
                mime = media_mime_from_header(raw[:4096], Path(ref["archiveImage"]).suffix)
                ref["imageMime"] = mime
                ref["name"] = safe_media_filename(ref.get("name", ""), "Reference", suffix=MEDIA_SUFFIXES.get(mime, ".png"))
                ref["image"] = f"data:{mime};base64," + base64.b64encode(raw).decode()
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
        member = _resolve_source_member(archive, segment, original[1])
        actual = _cached_member(archive, member, Path(original[0]))
    segment._archive_source = (original[0], member)
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
