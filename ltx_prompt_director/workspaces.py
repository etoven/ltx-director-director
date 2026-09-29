"""Portable workspace definitions and their user-editable filesystem store."""
from __future__ import annotations

import json
import re
from importlib.resources import files
from pathlib import Path

from PySide6.QtCore import QStandardPaths

SCHEMA_VERSION = 1
KINDS = {"image", "video", "start", "end", "identity", "style", "scene", "object", "composition", "wardrobe"}


def definition_root() -> Path:
    root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)) / "workspaces"
    root.mkdir(parents=True, exist_ok=True)
    return root


def validate_definition(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Expected a workspace definition with schema_version 1.")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", str(value.get("id", ""))):
        raise ValueError("ID must contain only letters, numbers, hyphens and underscores.")
    if not isinstance(value.get("name"), str) or not value["name"].strip():
        raise ValueError("A workspace name is required.")
    if value.get("prompt_mode") not in {"unified", "segmented"}:
        raise ValueError("Prompt mode must be unified or segmented.")
    if value.get("engine") not in {"ltx", "minimax_frames", "minimax_references", "generic"}:
        raise ValueError("Choose LTX, MiniMax Frames, MiniMax References or Generic.")
    if value["engine"] != "generic" and (value["engine"] == "ltx") != (value["prompt_mode"] == "segmented"):
        raise ValueError("The LTX engine requires segmented prompts; MiniMax engines require unified prompts.")
    for key in ("global_prompt", "audio_generation"):
        if not isinstance(value.get(key), bool):
            raise ValueError(f"{key} must be true or false.")
    refs = value.get("references", {})
    if not isinstance(refs, dict) or not isinstance(refs.get("enabled"), bool):
        raise ValueError("References must specify enabled and kinds.")
    if not isinstance(refs.get("kinds"), list) or any(kind not in KINDS for kind in refs["kinds"]):
        raise ValueError("Unknown reference kind.")
    count = refs.get("untimed_slots", 0)
    if type(count) is not int or not 0 <= count <= 2:
        raise ValueError("Untimed reference slots must be 0, 1 or 2.")
    if value["engine"] == "ltx" and count:
        raise ValueError("LTX references are timeline frames; untimed image slots require a MiniMax engine.")
    prompts = value.get("generation_prompts")
    if not isinstance(prompts, dict) or not isinstance(prompts.get("generate"), str) or not prompts["generate"].strip():
        raise ValueError("A generation instruction is required.")
    if not isinstance(prompts.get("refine", ""), str):
        raise ValueError("Refinement instruction must be text.")
    return value


class WorkspaceStore:
    def __init__(self, root: Path | None = None):
        self.root = root or definition_root()
        self.root.mkdir(parents=True, exist_ok=True)
        # A marker means deliberately deleted stock definitions stay deleted.
        if not (self.root / ".initialized").exists():
            self.restore_stock(overwrite=False)
            (self.root / ".initialized").touch()
        self.errors: list[str] = []

    def load(self) -> dict[str, dict]:
        result = {}
        self.errors = []
        for path in sorted(self.root.glob("*.json")):
            try:
                value = validate_definition(json.loads(path.read_text(encoding="utf-8")))
                if value["id"] != path.stem:
                    raise ValueError("Filename must match definition ID.")
                result[value["id"]] = value
            except (OSError, ValueError) as error:
                self.errors.append(f"{path.name}: {error}")
        return result

    def save(self, value: dict, previous_id: str | None = None) -> None:
        validate_definition(value)
        target = self.root / f"{value['id']}.json"
        if previous_id and previous_id != value["id"] and target.exists():
            raise ValueError("Another workspace already uses this ID.")
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(target)
        if previous_id and previous_id != value["id"]:
            self.delete(previous_id)

    def delete(self, workspace_id: str) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", workspace_id):
            raise ValueError("Invalid workspace ID.")
        (self.root / f"{workspace_id}.json").unlink(missing_ok=True)

    def restore_stock(self, overwrite: bool = True) -> None:
        stock = files("ltx_prompt_director").joinpath("workspace_templates")
        for path in stock.iterdir():
            if path.name.endswith(".json"):
                value = validate_definition(json.loads(path.read_text(encoding="utf-8")))
                if overwrite or not (self.root / path.name).exists():
                    self.save(value)
