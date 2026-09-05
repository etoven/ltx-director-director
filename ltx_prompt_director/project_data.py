from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from uuid import uuid4


DEFAULT_PROJECT_TAGS = [
    {"name": "Done", "color": "#367d4a"},
    {"name": "Consider", "color": "#8a742f"},
    {"name": "Re-shoot", "color": "#934545"},
]
ARCHIVE_COLOR = "#59636b"


def load_project_tags(value: object) -> list[dict[str, str]]:
    try:
        raw = json.loads(str(value)) if isinstance(value, str) else value
    except (TypeError, ValueError):
        raw = None
    tags: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else DEFAULT_PROJECT_TAGS:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        color = str(item.get("color", "")).strip()
        if not name or not re.fullmatch(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?(?:[0-9a-fA-F]{2})?", color) or name.casefold() in seen or name.casefold() == "archive":
            continue
        seen.add(name.casefold())
        tags.append({"name": name, "color": color})
    return tags or [dict(item) for item in DEFAULT_PROJECT_TAGS]


def tags_to_text(tags: list[dict[str, str]]) -> str:
    return "\n".join(f"{tag['name']} | {tag['color']}" for tag in tags)


def tags_from_text(value: str) -> list[dict[str, str]]:
    records = []
    for line in value.splitlines():
        if not line.strip():
            continue
        name, separator, color = line.partition("|")
        if not separator:
            raise ValueError(f"Tag line needs Name | #color: {line.strip()}")
        records.append({"name": name.strip(), "color": color.strip()})
    parsed = load_project_tags(records)
    if len(parsed) != len(records):
        raise ValueError("Tag names must be unique, colors must be hexadecimal, and Archive is reserved.")
    return parsed


def normalize_notes(value: object) -> list[dict[str, str]]:
    result = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict) or not str(item.get("text", "")).strip():
            continue
        created = str(item.get("createdAt") or datetime.now(timezone.utc).isoformat())
        result.append({
            "id": str(item.get("id") or uuid4().hex),
            "text": str(item["text"]).strip(),
            "createdAt": created,
            "date": str(item.get("date") or created),
        })
    return sorted(result, key=lambda note: (note["date"], note["createdAt"], note["id"]))


def new_note(text: str, date: str | None = None) -> dict[str, str]:
    created = datetime.now(timezone.utc).isoformat()
    return {"id": uuid4().hex, "text": text.strip(), "createdAt": created, "date": date or created}
