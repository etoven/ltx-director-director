"""Client-side workflow and asset roles for MiniMax References generation."""
from __future__ import annotations

import base64
import io

from PIL import Image

from .models import Segment
from .prompt_templates import render_workspace_prompt


REFERENCE_ROLES = {
    "identity": "Identity",
    "wardrobe": "Wardrobe",
    "scene": "Setting",
    "style": "Visual style",
    "object": "Object / prop",
    "composition": "Composition",
}
WORKFLOW_NAMES = {
    "t2v": "Text to video (T2V)",
    "i2v": "Image to video (I2V)",
    "fl2v": "First / last frame (FL2V)",
    "keyframes": "Multiple keyframes",
    "v2v": "Video to video (V2V)",
    "r2v": "Mixed references (R2V)",
}
WORKFLOW_DIRECTIONS = {
    "t2v": "Stage the requested action from text. There are no visual assets to cite.",
    "i2v": "Animate the timeline image according to its start/end role. Describe movement away from a start image or toward an end image.",
    "fl2v": "Connect the supplied opening and ending states through visible intermediate movement, reaching the ending image at its assigned time.",
    "keyframes": "Connect the ordered image checkpoints. Preserve the assigned image times while motion spans their boundaries.",
    "v2v": "Use the video as the source for the requested sequence. For an edit, identify the editing master and requested changes; for continuation, start from its ending state. Do not invent an editing or continuation request.",
    "r2v": "Combine the supplied assets using their individual roles. Timeline media controls chronology; untimed reference images control only their selected attributes.",
}


def reference_slots(value: object) -> list[dict | None]:
    """Copy the two portable image slots; old projects default to empty slots."""
    result: list[dict | None] = [None, None]
    for index, item in enumerate(value[:2] if isinstance(value, list) else []):
        if not isinstance(item, dict) or not str(item.get("image", "")).startswith("data:image/"):
            continue
        result[index] = {
            "name": str(item.get("name") or f"Reference {index + 1}"),
            "image": item["image"],
            "role": item.get("role") if item.get("role") in REFERENCE_ROLES else "identity",
            "notes": str(item.get("notes", "")),
        }
    return result


def detect_workflow(segments: list[Segment], references: list | None = None) -> str:
    images = [item for item in segments if item.kind == "image"]
    videos = [item for item in segments if item.kind == "video"]
    if any(reference_slots(references)) or (images and videos):
        return "r2v"
    if videos:
        return "v2v"
    if len(images) == 1:
        return "i2v"
    if len(images) == 2 and images[0].role == "start" and images[1].role == "end":
        return "fl2v"
    return "keyframes" if images else "t2v"


def reference_image_for_provider(encoded: str) -> str:
    """Keep originals in the project; cap only the image sent for analysis."""
    with Image.open(io.BytesIO(base64.b64decode(encoded.split(",", 1)[1]))) as image:
        if max(image.size) <= 1024:
            return encoded
        image = image.convert("RGB")
        image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, "WEBP", quality=90)
    return "data:image/webp;base64," + base64.b64encode(output.getvalue()).decode()


def reference_inventory(segments: list[Segment], references: list | None = None) -> list[dict]:
    """Assign labels once, shared by UI, prompt instructions and provider media."""
    records = []
    image_count = video_count = 0
    cursor = 0.0
    for index, item in enumerate(segments):
        start, end = round(cursor, 3), round(cursor + item.duration, 3)
        label = None
        if item.kind == "image":
            image_count += 1
            label = f"Image{image_count}"
        elif item.kind == "video":
            video_count += 1
            label = f"Video{video_count}"
        record = {"label": label, "name": item.name, "kind": item.kind,
                  "timeline_index": index, "start_time": start, "end_time": end,
                  "role": item.role, "prompt": item.prompt, "image_prompt": item.image_prompt}
        if item.kind == "image":
            record["checkpoint_time"] = end if item.role == "end" else start
        elif item.kind == "video":
            record["source_start"] = max(0, item.trim_start or 0) / 24
            record["source_end"] = record["source_start"] + item.duration
        records.append(record)
        cursor = end
    for slot, item in enumerate(reference_slots(references)):
        if item:
            image_count += 1
            records.append({"label": f"Image{image_count}", "name": item["name"],
                            "kind": "image", "reference_slot": slot, "role": item["role"],
                            "notes": item["notes"]})
    return records


def reference_rules(segments: list[Segment], intent: str, global_prompt: str,
                    sfx: bool, spoken_dialog: bool, reduce_music: bool,
                    references: list | None = None) -> str:
    import json
    workflow = detect_workflow(segments, references)
    inventory = reference_inventory(segments, references)
    # A concise, independently worded production brief based on the supplied guide.
    return render_workspace_prompt('minimax_references', 'generate', {
        'workflow_name': WORKFLOW_NAMES[workflow],
        'workflow_direction': WORKFLOW_DIRECTIONS[workflow],
        'asset_map': json.dumps(inventory, ensure_ascii=False, indent=2),
        'total_duration': format(sum((item.duration for item in segments)), '.3f'),
        'director_intent': intent.strip() or 'Not supplied.',
        'global_direction': global_prompt.strip() or 'Not supplied.',
        'sound_effects': 'Describe supported physical sounds and ambience.' if sfx else 'Only explicitly supplied or clearly audible source sounds; otherwise none specified.',
        'dialogue': 'Use supplied exact words, speakers and delivery.' if spoken_dialog else 'Do not add dialogue; retain explicitly supplied words only.',
        'music': 'No background music unless explicitly requested.' if reduce_music else 'Use music only if the supplied direction calls for it.',
    })
