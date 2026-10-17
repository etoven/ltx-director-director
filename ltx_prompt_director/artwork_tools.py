"""Capability-based artwork tools for automatically requested timeline frames.

ChatGPT-plan OAuth does not include image-generation tool access.
A configured Gemini API key enables Nano Banana; a conventional OpenAI API
key enables GPT Image. This module never accepts OAuth access tokens.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
import os
from pathlib import Path
from uuid import uuid4

import requests
from PIL import Image, UnidentifiedImageError


# Persist these exact IDs in Settings independently of the chosen text model.
# "auto" retains the historical preferred-provider behavior for upgrades.
# Explicit selections never silently switch to a different billed model.
IMAGE_MODEL_CHOICES = (
    ("Automatic (use configured image API)", "auto"),
    ("Gemini · Flash Lite Image (fast)", "gemini:gemini-3.1-flash-lite-image"),
    ("Gemini · Flash Image", "gemini:gemini-3.1-flash-image"),
    ("Gemini · Pro Image", "gemini:gemini-3-pro-image"),
    ("OpenAI · GPT Image 2.5 Flare", "openai:gpt-image-2.5-flare"),
    ("OpenAI · GPT Image 2.5 Sunburst", "openai:gpt-image-2.5-sunburst"),
    ("OpenAI · GPT Image 2", "openai:gpt-image-2"),
    ("OpenAI · GPT Image 1", "openai:gpt-image-1"),
    ("OpenAI · GPT Image 1 mini", "openai:gpt-image-1-mini"),
)
DEFAULT_IMAGE_MODELS = {
    "gemini": "gemini-3.1-flash-lite-image",
    "openai": "gpt-image-2.5-flare",
}
IMAGE_MODEL_IDS = {value for _label, value in IMAGE_MODEL_CHOICES}


@dataclass(frozen=True)
class ArtworkTool:
    provider: str
    model: str
    api_key: str

    @property
    def label(self) -> str:
        return ("Google Gemini / Nano Banana" if self.provider == "gemini"
                else "OpenAI API / GPT Image")


def available_artwork_tools(
    keys: dict[str, str], preferred: str = "", selected: str = "auto"
) -> list[ArtworkTool]:
    """Resolve configured image-capable API tools, never ChatGPT plan OAuth.

    Automatic mode keeps existing provider priority. An explicit model is
    authoritative: absent credentials leave the artwork pending rather than
    silently sending a chargeable request through another provider.
    """
    if selected != "auto":
        if selected not in IMAGE_MODEL_IDS:
            return []
        provider, model = selected.split(":", 1)
        key = str(keys.get(provider) or "").strip()
        return [ArtworkTool(provider, model, key)] if key else []

    # ChatGPT OAuth is intentionally NOT an image-generation credential.
    priority = (preferred, "openai" if preferred == "gemini" else "gemini") if (
        preferred in DEFAULT_IMAGE_MODELS
    ) else ("gemini", "openai")
    return [
        ArtworkTool(provider, DEFAULT_IMAGE_MODELS[provider], str(keys.get(provider)).strip())
        for provider in priority if str(keys.get(provider) or "").strip()
    ]


def _png_payload(payload: bytes) -> bytes:
    if not payload or len(payload) > 35 * 1024 * 1024:
        raise ValueError("Generated image is empty or too large.")
    try:
        with Image.open(BytesIO(payload)) as image:
            image.load()
            if image.format == "PNG":
                return payload
            result = BytesIO()
            image.convert("RGB").save(result, format="PNG")
            return result.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ValueError("Image tool did not return a usable image.") from error


def _decode(value: str) -> bytes:
    return _png_payload(base64.b64decode(value, validate=True))


def _openai_image(prompt: str, tool: ArtworkTool) -> bytes:
    response = requests.post(
        "https://api.openai.com/v1/images/generations",
        headers={"Authorization": "Bearer " + tool.api_key,
                 "Content-Type": "application/json"},
        json={"model": tool.model, "size": "1536x1024", "n": 1, "prompt": prompt},
        timeout=240,
    )
    response.raise_for_status()
    return _decode(response.json()["data"][0]["b64_json"])


def _gemini_image(prompt: str, tool: ArtworkTool) -> bytes:
    """Nano Banana 2 Lite using Google's documented generateContent REST API."""
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1/models/"
        f"{tool.model}:generateContent",
        headers={"x-goog-api-key": tool.api_key,
                 "Content-Type": "application/json"},
        json={
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "responseFormat": {"image": {"aspectRatio": "16:9", "imageSize": "1K"}},
            },
        },
        timeout=240,
    )
    response.raise_for_status()
    for candidate in response.json().get("candidates", []):
        for part in (candidate.get("content") or {}).get("parts", []):
            image = part.get("inlineData") or part.get("inline_data") or {}
            if image.get("data"):
                return _decode(image["data"])
    raise ValueError("Gemini Flash Lite returned no image data.")


def render_artwork(records: list[dict], tool: ArtworkTool) -> dict[str, str]:
    """Render an already approved batch on a background worker.

    No UI confirmation here: approval is always collected BEFORE calling it.
    """
    root = (Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
            / "PromptDirector" / "generated-artwork")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = {}
    for entry in records[:4]:
        if not entry.get("artworkPrompt"):
            continue
        prompt = entry["artworkPrompt"]
        data = (_gemini_image(prompt, tool) if tool.provider == "gemini"
                else _openai_image(prompt, tool))
        path = root / (uuid4().hex + ".png")
        path.write_bytes(data)
        output[entry["id"]] = str(path)
    return output
