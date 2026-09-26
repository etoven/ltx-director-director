"""Client-side workflow and asset roles for MiniMax References generation."""
from __future__ import annotations

import base64
import io

from PIL import Image

from .models import Segment


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
    return f"""Write a MiniMax H3 production prompt from the supplied media and timeline.
The application selected {WORKFLOW_NAMES[workflow]}. {WORKFLOW_DIRECTIONS[workflow]}

ASSET MAP (labels and roles are assigned by the client):
{json.dumps(inventory, ensure_ascii=False, indent=2)}
Output duration: {sum(item.duration for item in segments):.3f} seconds.

Write a compact production brief with these sections:
[REFERENCE USE] Explain each asset's permitted contribution. Use the exact Image1/Video1 labels in the asset map. Untimed images have no checkpoint or duration.
[CONTINUITY] State the attributes that must persist; allow intentional changes in the timeline.
[SCENE] State the requested setting and action.
[TIMED ACTION] Use time ranges for the major beats, with visible motion, camera behavior and a reached end state. Maintain spatial relationships and ongoing movement across ranges.
[SOUND] Include requested ambience, effects, exact dialogue with named speakers, and the music direction.
[AVOID] Briefly name relevant continuity failures.
Omit empty sections. Keep the production prompt concise and comfortably under 7,000 characters.

Apply these project semantics:
- Read the entire timeline before writing. Preserve its chronological progression and total duration.
- Image checkpoint_time is authoritative: a start image describes the interval's beginning; an end image describes its ending. Direct the change into that state, then continue from it. Do not restart completed changes.
- For videos, only source_start through source_end belongs to the timeline interval. Other footage provides context. Sampled observations belong to the same video; a preview alone cannot establish unseen motion or audio.
- Give untimed references only their selected role. Notes can identify the subject or narrow the role; they do not introduce another timed frame. Never freeze intended transformations by declaring all anatomy or appearance constant.
- An edit should list requested changes and what survives from the source. Do not infer identity replacement merely because an extra reference exists.
- Use continuous action unless the user asks for cuts. Do not impose a cut, shot label, bridge slot, sentence quota, or repeated scene description at each image.
- No invented reference assets, voice sources, dialogue or visual events. Do not output the asset map or these instructions.

Director's intent: {intent.strip() or 'Not supplied.'}
Global direction: {global_prompt.strip() or 'Not supplied.'}
Sound effects: {'Describe supported physical sounds and ambience.' if sfx else 'Only explicitly supplied or clearly audible source sounds; otherwise none specified.'}
Dialogue: {'Use supplied exact words, speakers and delivery.' if spoken_dialog else 'Do not add dialogue; retain explicitly supplied words only.'}
Music: {'No background music unless explicitly requested.' if reduce_music else 'Use music only if the supplied direction calls for it.'}

Return JSON with one field: {{"prompt": "the complete production brief"}}.
"""
