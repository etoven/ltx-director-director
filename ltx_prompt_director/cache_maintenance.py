"""Versioned OS preview cache; project originals remain in their portable archives."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import time
from pathlib import Path

from PIL import Image

from .media import APP_CACHE

CACHE_VERSION = 2
CACHE_MAX_AGE_SECONDS = 90 * 24 * 3600
MARKER = APP_CACHE / "preview-cache.json"


def needs_refresh(now: float | None = None) -> bool:
    try:
        marker = json.loads(MARKER.read_text(encoding="utf-8"))
        return marker["version"] != CACHE_VERSION or (now or time.time()) - marker["builtAt"] >= CACHE_MAX_AGE_SECONDS
    except (OSError, ValueError, KeyError, TypeError):
        return True


def mark_ready() -> None:
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    temporary = MARKER.with_name(f"{MARKER.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps({"version": CACHE_VERSION, "builtAt": time.time()}), encoding="utf-8")
        os.replace(temporary, MARKER)
    finally:
        temporary.unlink(missing_ok=True)


def cover_path(value: str) -> Path:
    return APP_CACHE / "covers" / f"{hashlib.sha256(value.encode('ascii')).hexdigest()[:32]}.jpg"


def cache_cover(value: str) -> Path | None:
    if not value or "," not in value:
        return None
    target = cover_path(value)
    if target.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        raw = base64.b64decode(value.split(",", 1)[1])
        with Image.open(io.BytesIO(raw)) as source:
            image = source.convert("RGB")
            image.thumbnail((230, 230), Image.Resampling.LANCZOS)
            image.save(temporary, "JPEG", quality=82)
        os.replace(temporary, target)
    except (OSError, ValueError):
        return None
    finally:
        temporary.unlink(missing_ok=True)
    return target


def warm_metadata(path: Path) -> None:
    meta = json.loads(path.read_text(encoding="utf-8"))
    cache_cover(str(meta.get("thumbnailData", "")))
