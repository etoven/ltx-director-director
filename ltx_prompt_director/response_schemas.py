"""Operation-specific JSON contracts shared by the provider transports.

Use only the common OpenAI/Gemini JSON Schema subset. Provider constraints
guide decoding; local validation still rejects incomplete or wrong-scope data.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import copy
import json
import math


def text(description: str, *, empty: bool = False) -> dict:
    schema = {"type": "string", "description": description}
    if not empty:
        schema["minLength"] = 1
    return schema


def duration(description="Duration in seconds, positive and finite.") -> dict:
    return {"type": "number", "minimum": 0.01, "description": description}


def array(items: dict, count: int | None = None) -> dict:
    result = {"type": "array", "items": items, "minItems": 1 if count is None else count}
    if count is not None:
        result["maxItems"] = count
    return result


def record(**properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def named(name: str, schema: dict) -> dict:
    return {"title": name, **schema}


PROMPT = named("production_prompt", record(prompt=text("Complete editable production prompt; preserve all details.")))
GLOBAL = named("global_direction", record(globalPrompt=text("Complete global direction.")))
TIMING = named("selected_timing", record(duration=duration()))
SEGMENT_TEXT = named("selected_segment_text", record(prompt=text("Only the selected segment's refined prompt."),
                                                     duration=duration()))
SEGMENT = named("selected_segment", record(
    prompt=text("Only the selected segment's complete action prompt."),
    imagePrompt=text("Audio-free visual description; empty string when no visual update is needed.", empty=True),
    duration=duration()))


def sequence(count: int, *, generic: bool = False) -> dict:
    fields = dict(prompt=text("Complete prompt for this segment only."), duration=duration())
    if not generic:
        fields["imagePrompt"] = SEGMENT["properties"]["imagePrompt"]
    return named("ordered_sequence", record(
        segments=array(record(**fields), count),
        globalPrompt=text("Shared global direction; empty only for workspaces without global direction.", empty=generic)))


def global_refinement(count: int) -> dict:
    return named("global_refinement", record(
        globalPrompt=text("Refined global direction; preserve authored details."),
        durations={"anyOf": [array(duration(), count), {"type": "null"}],
                   "description": "One duration per segment in order, or null when proportionalTotal is set."},
        proportionalTotal={"anyOf": [duration(), {"type": "null"}],
                           "description": "New proportional total only for an explicit resize request; otherwise null."}))


def split(count: int) -> dict:
    return named("split_prompts", record(prompts=array(text("Complete action prompt for this split part."), count)))


def timings(count: int) -> dict:
    return named("sequence_timing_only", record(durations=array(duration(), count)))


def timeline(*, combined: bool) -> dict:
    item = record(
        id=text("Retained existing UUID, or new:1, new:2, etc. for a new segment."),
        name=text("Short descriptive segment name."),
        kind={"type": "string", "enum": ["image", "video", "text"]},
        role={"type": "string", "enum": ["start", "end", "text"]},
        duration={**duration(), "maximum": 3600},
        prompt=text("Complete direction for this segment; preserve every relevant detail.", empty=True),
        imagePrompt=text("Audio-free visual description, or empty string.", empty=True),
        artworkPrompt=text("Explicitly requested artwork instruction, otherwise empty string.", empty=True))
    fields = {"prompt": PROMPT["properties"]["prompt"]} if combined else {}
    fields["segments"] = array(item)
    return named("combined_timeline" if combined else "timeline_sync", record(**fields))


# Requests run on several worker threads. Never share a mutable global schema
# or infer a response type by searching the director's editable prompt text.
_ACTIVE: ContextVar[dict | None] = ContextVar("ai_response_schema", default=None)


@contextmanager
def using(schema: dict):
    token = _ACTIVE.set(schema)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def current() -> dict:
    # Private direct transport calls are prompt-only. Application operations
    # always bind their own explicit contract through ai._structured_raw.
    return _ACTIVE.get() or PROMPT


def openai_format() -> dict:
    schema = copy.deepcopy(current())
    return {"type": "json_schema", "name": schema.pop("title"), "strict": True, "schema": schema}


def gemini_config() -> dict:
    schema = copy.deepcopy(current())
    # GenerateContent's JSON Schema subset does not constrain string length;
    # keep that check locally and retain the field's descriptive instruction.
    def supported(node):
        if isinstance(node, dict):
            return {key: supported(value) for key, value in node.items() if key != "minLength"}
        if isinstance(node, list):
            return [supported(value) for value in node]
        return node
    return {"temperature": 0.25, "responseMimeType": "application/json",
            "responseJsonSchema": supported(schema)}


def validate(raw: str) -> None:
    """Validate precisely the schema we sent, before any editor gets a result."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    def check(value, schema, path):
        def fail(reason):
            raise ValueError(f"{path}: {reason}")
        if "anyOf" in schema:
            for option in schema["anyOf"]:
                try:
                    check(value, option, path)
                    return
                except ValueError:
                    pass
            fail("value does not match an allowed type")
        kind = schema["type"]
        if kind == "null":
            if value is not None:
                fail("expected null")
        elif kind == "object":
            if not isinstance(value, dict):
                fail("expected an object")
            properties = schema["properties"]
            if set(schema["required"]) - value.keys():
                fail("missing required fields")
            if value.keys() - properties.keys():
                fail("unexpected fields for this operation")
            for key, field in properties.items():
                check(value[key], field, f"{path}.{key}")
        elif kind == "array":
            if not isinstance(value, list):
                fail("expected an array")
            if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
                fail("wrong number of entries")
            for index, item in enumerate(value):
                check(item, schema["items"], f"{path}[{index}]")
        elif kind == "string":
            if not isinstance(value, str):
                fail("expected text")
            if schema.get("minLength") and not value.strip():
                fail("empty text")
        elif kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                fail("expected a finite number")
            if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
                fail("number out of range")
        else:
            raise RuntimeError(f"Unsupported response schema type: {kind}")
        if "enum" in schema and value not in schema["enum"]:
            fail("invalid choice")

    try:
        value = json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, TypeError) as error:
        raise ValueError("malformed or incomplete JSON") from error
    check(value, current(), "response")
