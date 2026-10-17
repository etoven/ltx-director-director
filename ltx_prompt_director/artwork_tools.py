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


@dataclass(frozen=True)
class ArtworkTool:
    provider: str
    model: str
    api_key: str

    @property
    def label(self) -> str:
        return ("Google Gemini / Nano Banana" if self.provider == "gemini"
                else "OpenAI API / GPT Image")


def available_artwork_tools(keys: dict[str, str], preferred: str = "") -> list[ArtworkTool]:
    """Return tools the locally configured credentials can actually call.

    Credentials indicate potential access; the server remains authoritative
    for entitlement, billing, quota, and the model's live availability.
    """
    providers = {
        "gemini": ArtworkTool("gemini", "gemini-nano-banana-2.1", str(keys.get("gemini") or "")),
        "openai": ArtworkTool("openai", "gpt-image-2.5-flare", str(keys.get("openai") or "")),
    }
    # ChatGPT OAuth is intentionally NOT a source of image-generation credentials.
    priority = ("gemini", "openai") if preferred not in providers else (
        preferred, "openai" if preferred == "gemini" else "gemini"
    )
    return [providers[provider] for provider in priority if providers[provider].api_key.strip()]


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
    headers = {"x-goog-api-key": tool.api_key, "Content-Type": "application/json"}
    # Google currently recommends Nano Banana 2.1 via the Interactions API.
    response = requests.post(
        "https://generativelanguage.googleapis.com/v1beta/interactions",
        headers=headers,
        json={
            "model": tool.model,
            "input": [{"type": "text", "text": prompt}],
            "response_format": {"type": "image", "aspect_ratio": "16:9",
                                "image_size": "1K"},
        },
        timeout=240,
    )
    if response.status_code in (400, 404):
        # Older accounts/API tiers may support Nano Banana 2 via generateContent
        # even when 2.1's Interactions surface isn't enabled.
        fallback = requests.post(
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-3.1-flash-image:generateContent",
            headers=headers,
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseModalities": ["TEXT", "IMAGE"],
                    "imageConfig": {"aspectRatio": "16:9", "imageSize": "1K"},
                },
            },
            timeout=240,
        )
        fallback.raise_for_status()
        for candidate in fallback.json().get("candidates") or []:
            for part in (candidate.get("content") or {}).get("parts") or []:
                image = part.get("inlineData") or part.get("inline_data") or {}
                if image.get("data"):
                    return _decode(image["data"])
        raise ValueError("Gemini returned no image data.")
    response.raise_for_status()
    document = response.json()
    interaction = document.get("interaction") or document
    image = interaction.get("output_image") or interaction.get("outputImage") or {}
    if image.get("data"):
        return _decode(image["data"])
    for step in interaction.get("steps") or []:
        for content in step.get("output") or []:
            if content.get("type") == "image" and content.get("data"):
                return _decode(content["data"])
    raise ValueError("Nano Banana returned no output image.")


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
