"""Portable workspace definitions and their user-editable filesystem store."""
from __future__ import annotations

import json
import re
from importlib.resources import files
from pathlib import Path

from PySide6.QtCore import QStandardPaths

SCHEMA_VERSION = 1
KINDS = {"image", "video", "start", "end", "identity", "style", "scene", "object", "composition", "wardrobe"}


def is_obsolete_stock_definition(value: dict) -> bool:
    """Recognize untouched legacy defaults embedded in portable projects."""
    import hashlib
    hashes = {
        'minimax_frames': {'79aa0f7a834319d465bfb61fe49eb4ce09572b326bc2a31d8e9f5483c2241b9e'},
        'minimax_references': {'76e1e3d0b159bb5a64620f9e5e15ebee3f052a5fcafd7e6f72424caea643015d',
                               '62ca3a9db6b3284289493f88ae031912f3161ddf2a83fb64973d94aba6908599'},
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest() in hashes.get(value.get('id'), set())


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
        marker = self.root / ".stock-single-minimax"
        if not marker.exists():
            import hashlib
            original_hashes = {'minimax_references': '76e1e3d0b159bb5a64620f9e5e15ebee3f052a5fcafd7e6f72424caea643015d', 'minimax_frames': '79aa0f7a834319d465bfb61fe49eb4ce09572b326bc2a31d8e9f5483c2241b9e', 'ltx': 'd84c9c0c4ba1f498d2284dbde26c1b098d400dea4d7d6c75f9a33385be16349a'}
            for workspace_id, original_hash in original_hashes.items():
                path = self.root / f"{workspace_id}.json"
                if not path.exists():
                    continue
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                    unchanged = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest() == original_hash
                    if unchanged:
                        if workspace_id == "minimax_frames":
                            path.unlink()
                        elif workspace_id == "minimax_references":
                            path.unlink()
                        else:
                            stock = files("ltx_prompt_director").joinpath(f"workspace_templates/{workspace_id}.json")
                            self.save(json.loads(stock.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    pass
            marker.touch()
        guide_marker = self.root / ".stock-minimax-guides"
        if not guide_marker.exists():
            import hashlib
            previous = self.root / "minimax_references.json"
            if previous.exists():
                try:
                    value = json.loads(previous.read_text(encoding="utf-8"))
                    if hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest() == "62ca3a9db6b3284289493f88ae031912f3161ddf2a83fb64973d94aba6908599":
                        previous.unlink()
                except (OSError, ValueError):
                    pass
            self.restore_stock(overwrite=False)
            guide_marker.touch()
        skill_marker = self.root / ".stock-minimax-official-skills"
        if not skill_marker.exists():
            for workspace_id in ('minimax_official_skill_base', 'minimax_official_skill_reference'):
                if not (self.root / f"{workspace_id}.json").exists():
                    source = files("ltx_prompt_director").joinpath(f"workspace_templates/{workspace_id}.json")
                    self.save(json.loads(source.read_text(encoding="utf-8")))
            skill_marker.touch()
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
