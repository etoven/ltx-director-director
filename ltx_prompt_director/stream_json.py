"""Incremental, display-only JSON parsing for live LTX prompt streams.

Tolerates JSON fragments mid-escape/mid-value and never edits the source or
changes the provider's final response validation. Only presentation uses it.
"""
from __future__ import annotations

import json
import re
from html import escape

_FIELD = re.compile(r'(?<!\\)"(prompt|globalPrompt|segmentPrompt|imagePrompt|duration|name)"\s*:\s*', re.I)
_LABEL = {
    "prompt": "Production prompt",
    "globalPrompt": "Global direction",
    "segmentPrompt": "Segment prompt",
    "imagePrompt": "Image prompt",
    "duration": "Duration",
    "name": "Name",
}


def _string_value(source: str, start: int) -> tuple[str, bool, int]:
    escaped = False
    index = start
    while index < len(source):
        ch = source[index]
        if ch == '"' and not escaped:
            return _decode(source[start:index]), True, index + 1
        if ch == "\\" and not escaped:
            escaped = True
        else:
            escaped = False
        index += 1
    return _decode(source[start:]), False, len(source)


def _decode(fragment: str) -> str:
    # The final few characters of a live stream may be an unfinished \u
    # escape, lone slash or surrogate pair. Defer only that incomplete tail.
    for removed in range(min(8, len(fragment)) + 1):
        candidate = fragment[:len(fragment) - removed] if removed else fragment
        try:
            return json.loads('"' + candidate + '"')
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return fragment


def live_fields(source: str) -> list[tuple[str, str, bool]]:
    """(label, decoded value, is_complete) for fields available so far."""
    stripped = source.lstrip()
    if not stripped.startswith(("{", "[")):
        return [("Generated text", source, False)] if source else []
    fields = []
    for match in _FIELD.finditer(source):
        key = match.group(1)
        cursor = match.end()
        if cursor >= len(source):
            continue
        if source[cursor] == '"':
            value, complete, _ = _string_value(source, cursor + 1)
            fields.append((_LABEL.get(key, key), value, complete))
        elif key == "duration":
            number = re.match(r'[-+]?(?:\d+(?:\.\d*)?)', source[cursor:])
            if number:
                fields.append(("Duration", number.group(), True))
    return fields


def structured_stream_html(source: str, *, complete: bool = False) -> str:
    """Render progressive field cards, not raw escaped JSON source."""
    fields = live_fields(source)
    if not fields:
        return ('<p style="color:#8298b2">Waiting for the first structured field…</p>')
    cards = []
    count = 0
    for label, value, field_finished in fields:
        # Empty fields are shown once they start instead of flashing JSON syntax.
        count += 1
        safe = escape(value).replace("\n", "<br/>")
        if not safe.strip():
            safe = '<span style="color:#71869e">Receiving text…</span>'
        color = "#9cc9ff" if label in {"Production prompt", "Global direction", "Segment prompt", "Image prompt"} else "#c9b0ff"
        dot = "●" if complete or field_finished else "◉"
        cards.append(
            '<table cellspacing="0" cellpadding="9" width="100%" bgcolor="#162537" '
            'style="border:1px solid #304968"><tr><td>'
            f'<span style="color:{color};font-weight:600">{dot}  {escape(label)}</span>'
            f'<div style="color:#e5eef9;margin-top:6px;white-space:pre-wrap">{safe}</div>'
            '</td></tr></table><p style="margin:3px"></p>'
        )
    return ('<div style="font-size:12px;font-family:monospace">'
            + "".join(cards) + "</div>")
