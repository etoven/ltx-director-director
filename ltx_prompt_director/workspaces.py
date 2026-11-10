"""Portable workspace definitions and their user-editable filesystem store."""
from __future__ import annotations

import json
import math
import re
from importlib.resources import files
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from .timing_limits import sequence_limit, shot_limit

SCHEMA_VERSION = 1
FACTORY_IDS = frozenset({'ltx', 'minimax_official_skill_base', 'minimax_official_skill_reference', 'minimax_prompt_refinement'})
PREVIOUS_REFINEMENT_DIGEST = 'b2c4c7c9c53dd41c5cf3440742c9effcb7de352b63d945b8d73171807fe09977'
KINDS = {"image", "video", "start", "end", "identity", "style", "scene", "object", "composition", "wardrobe", "voice", "sound", "motion"}


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
    if value.get("prompt_mode") not in {"unified", "segmented", "prompt_only", "sequence"}:
        raise ValueError("Prompt mode must be unified, segmented or prompt_only or sequence.")
    if value.get("engine") not in {"ltx", "minimax_frames", "minimax_references", "minimax_prompt", "generic", "shot_sequence"}:
        raise ValueError("Choose LTX, MiniMax Frames, MiniMax References, MiniMax Prompt Refinement or Generic.")
    if (value["engine"] == "shot_sequence") != (value["prompt_mode"] == "sequence"):
        raise ValueError("Shot Sequence requires sequence mode.")
    if (value["engine"] == "minimax_prompt") != (value["prompt_mode"] == "prompt_only"):
        raise ValueError("MiniMax Prompt Refinement requires prompt_only mode.")
    if value["engine"] not in {"generic", "minimax_prompt", "shot_sequence"} and (value["engine"] == "ltx") != (value["prompt_mode"] == "segmented"):
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
    if type(count) is not int or not 0 <= count <= 4:
        raise ValueError("Untimed reference slots must be between 0 and 4.")
    if value["engine"] == "ltx" and count:
        raise ValueError("LTX references are timeline frames; untimed image slots require a MiniMax engine.")
    if value["prompt_mode"] == "prompt_only" and (refs["enabled"] or count or value["global_prompt"] or value["audio_generation"]):
        raise ValueError("Prompt-only refinement supports annotations, without reference media, global direction or audio controls.")
    prompts = value.get("generation_prompts")
    if not isinstance(prompts, dict) or not isinstance(prompts.get("generate"), str) or not prompts["generate"].strip():
        raise ValueError("A generation instruction is required.")
    if not isinstance(prompts.get("refine", ""), str):
        raise ValueError("Refinement instruction must be text.")
    upgrade_h3_instructions(value)
    if value["prompt_mode"] == "prompt_only" and not prompts.get("refine", "").strip():
        raise ValueError("Prompt-only refinement requires a refinement instruction.")
    maximum = sequence_limit(value)
    if maximum is not None and (isinstance(maximum, bool) or not isinstance(maximum, (int, float))
                                or not math.isfinite(maximum) or maximum < 0.01):
        raise ValueError("Maximum sequence length must be at least 0.01 seconds, or null for no limit.")
    value["max_sequence_duration"] = maximum
    shots = value.get("max_shots", shot_limit(value) or 0)
    if type(shots) is not int or shots < 0:
        raise ValueError("Maximum shots must be a nonnegative integer; use 0 for unlimited.")
    value["max_shots"] = shots
    from .output_templates import default_layout, validate_layout
    if value['prompt_mode'] == 'unified':
        value['output_template'] = validate_layout(value.get('output_template', default_layout(value['engine'])))
    else:
        value.pop('output_template', None)
    return value


def upgrade_h3_instructions(value):
    """Repair known factory instructions, including LTX copies, without replacing authored text."""
    engine = value.get('engine')
    if engine not in {'minimax_frames', 'minimax_references'}:
        return
    import hashlib
    from .prompt_templates import stock_prompt
    from .output_templates import default_layout
    legacy = json.loads(files('ltx_prompt_director').joinpath('engine_templates/h3_legacy_instructions.json').read_text())
    for kind, source in value.get('generation_prompts', {}).items():
        if hashlib.sha256(source.encode()).hexdigest() in legacy[engine].get(kind, []):
            value['generation_prompts'][kind] = stock_prompt(engine, kind)
    if value.get('output_template') == legacy['layouts'][engine]:
        value['output_template'] = default_layout(engine)


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
        prompt_marker = self.root / ".stock-minimax-prompt-only"
        if not prompt_marker.exists():
            if not (self.root / "minimax_prompt_refinement.json").exists():
                source = files("ltx_prompt_director").joinpath("workspace_templates/minimax_prompt_refinement.json")
                self.save(json.loads(source.read_text(encoding="utf-8")))
            prompt_marker.touch()
        mixed_marker = self.root / ".stock-minimax-four-media"
        if not mixed_marker.exists():
            import hashlib
            previous = {'minimax_base_guide': '238f498a7d088ecaeaa6d81516508c2e503bba53594cdd82ba4ffd03b13f4e53', 'minimax_full_reference_guide': '76b84e540da50439b14a2e559ed761ccc7a1c1b46a3c0238e126fbd059b68831', 'minimax_official_skill_reference': '5fe899c6c1d97a3bd7a8358f79e0c273d365eb69b5724fbf650d043d4e64e7ab', 'minimax_official_skill_base': '5ca56ee84f89fd5a64640e0f1af654b9918d902671bc460413fe3cfaf537cdb5'}
            for workspace_id, digest in previous.items():
                path = self.root / f"{workspace_id}.json"
                if path.exists():
                    try:
                        value = json.loads(path.read_text())
                        legacy = {key:item for key,item in value.items() if key not in {"max_sequence_duration", "max_shots"}}
                        if hashlib.sha256(json.dumps(legacy,sort_keys=True).encode()).hexdigest() == digest:
                            source = files("ltx_prompt_director").joinpath(f"workspace_templates/{workspace_id}.json")
                            upgraded = json.loads(source.read_text())
                            if "max_sequence_duration" in value:
                                upgraded["max_sequence_duration"] = value["max_sequence_duration"]
                            if "max_shots" in value:
                                upgraded["max_shots"] = value["max_shots"]
                            self.save(upgraded)
                    except (OSError,ValueError):
                        pass
            mixed_marker.touch()
        sequence_marker = self.root / ".stock-shot-sequence"
        if not sequence_marker.exists():
            source = files("ltx_prompt_director").joinpath("workspace_templates/shot_sequence.json")
            if not (self.root / "shot_sequence.json").exists():
                self.save(json.loads(source.read_text()))
            sequence_marker.touch()
        compact_marker = self.root / '.stock-official-compact-v1'
        if not compact_marker.exists():
            self.retire_redundant_defaults()
            import hashlib
            refinement = self.root / 'minimax_prompt_refinement.json'
            if refinement.is_file():
                try:
                    previous = json.loads(refinement.read_text(encoding='utf-8'))
                    previous.pop('output_template', None)
                    digest = hashlib.sha256(json.dumps(previous, sort_keys=True).encode()).hexdigest()
                    if digest == PREVIOUS_REFINEMENT_DIGEST:
                        stock = files('ltx_prompt_director').joinpath('workspace_templates/minimax_prompt_refinement.json')
                        self.save(json.loads(stock.read_text(encoding='utf-8')))
                except (OSError, ValueError):
                    pass
            compact_marker.touch()
        self.errors: list[str] = []

    def load(self) -> dict[str, dict]:
        result = {}
        self.errors = []
        for path in sorted(self.root.glob("*.json")):
            try:
                original = json.loads(path.read_text(encoding="utf-8"))
                import copy
                value = validate_definition(copy.deepcopy(original))
                if value["id"] != path.stem:
                    raise ValueError("Filename must match definition ID.")
                result[value["id"]] = value
                if value.get('engine') in {'minimax_frames', 'minimax_references'} and value != original:
                    self.save(value)
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
            if path.name.endswith(".json") and path.name.removesuffix('.json') in FACTORY_IDS:
                value = validate_definition(json.loads(path.read_text(encoding="utf-8")))
                if overwrite or not (self.root / path.name).exists():
                    self.save(value)

    def retire_redundant_defaults(self):
        """Remove only unchanged retired defaults; authored variants remain usable."""
        from .output_templates import default_layout
        for key in ('minimax_base_guide', 'minimax_full_reference_guide', 'shot_sequence'):
            path = self.root / (key + '.json')
            if not path.is_file():
                continue
            try:
                actual = json.loads(path.read_text(encoding='utf-8'))
                stock = json.loads(files('ltx_prompt_director').joinpath('workspace_templates/' + key + '.json').read_text(encoding='utf-8'))
                layout = actual.pop('output_template', None)
                if layout is not None and layout != default_layout(actual.get('engine', 'ltx')):
                    continue
                if actual == stock:
                    path.unlink()
            except (OSError, ValueError):
                pass
