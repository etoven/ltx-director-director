"""Keep MiniMax action cues addressable while retaining the editable production brief."""
from __future__ import annotations

import re

from .ai import _minimax_timestamp

SECTION = re.compile(r'(?m)^\[TIMED ACTION\][ \t]*\n?')
NEXT = re.compile(r'(?m)^\[[A-Z][A-Z ]+\][ \t]*$')
CUE = re.compile(r'(?m)^\s*(\d{2}:\d{2}:\d{2}:\d{2})\s*-\s*(\d{2}:\d{2}:\d{2}:\d{2})\s*:\s*(.*?)(?=\n\s*\d{2}:\d{2}:\d{2}:\d{2}\s*-|\Z)', re.S)


def split_actions(prompt: str) -> tuple[list[str], str]:
    """Return cue descriptions and the untouched non-action prose."""
    section = SECTION.search(prompt)
    if not section:
        return [], prompt
    following = NEXT.search(prompt, section.end())
    body = prompt[section.end():following.start() if following else len(prompt)]
    descriptions = [match.group(3).strip() for match in CUE.finditer(body)]
    return descriptions, prompt[:section.end()] + (prompt[following.start():] if following else '')


def compose_actions(prompt: str, segments, descriptions: list[str]) -> str:
    section = SECTION.search(prompt)
    if not section:
        prompt = prompt.rstrip() + '\n\n[TIMED ACTION]\n'
        section = SECTION.search(prompt)
    following = NEXT.search(prompt, section.end())
    before = prompt[:section.end()].rstrip() + '\n'
    after = prompt[following.start():].lstrip('\n') if following else ''
    cursor = 0.0
    cues = []
    for index, segment in enumerate(segments):
        end = cursor + segment.duration
        description = descriptions[index] if index < len(descriptions) else segment.prompt.strip()
        cues.append(f'{_minimax_timestamp(cursor)} - {_minimax_timestamp(end)}: {description}')
        cursor = end
    return before + '\n\n'.join(cues) + ('\n\n' + after if after else '')


RANGE_LINE = re.compile(r"(?m)^[ \t]*(\d{2}:\d{2}:\d{2}:\d{2})[ \t]*-[ \t]*(\d{2}:\d{2}:\d{2}:\d{2})[ \t]*:[ \t]*(.*)$")


def timecode_seconds(value: str, fps: int = 24) -> float:
    hours, minutes, seconds, frames = map(int, value.split(":"))
    if minutes >= 60 or seconds >= 60 or frames >= fps:
        raise ValueError("Invalid SMPTE timecode")
    return hours * 3600 + minutes * 60 + seconds + frames / fps


def parse_timed_plan(prompt: str, fps: int = 24) -> list[dict]:
    """Keep invalid cue positions so partial matches never shift later segment identities."""
    section = SECTION.search(prompt)
    begin = section.end() if section else 0
    following = NEXT.search(prompt, begin)
    end = following.start() if following else len(prompt)
    body = prompt[begin:end]
    # A malformed timecode-looking line is a cue, too; it must detach its segment.
    starts = list(re.finditer(r"(?m)^[ \t]*\d{2}:\d{2}:[^\n]*", body))
    plan = []
    expected = 0.0
    blocked = False
    for index, start in enumerate(starts):
        stop = starts[index + 1].start() if index + 1 < len(starts) else len(body)
        chunk = body[start.start():stop].strip()
        match = RANGE_LINE.match(chunk)
        value = {"valid": False, "description": chunk, "start": None, "end": None}
        if match:
            value["description"] = chunk[match.start(3):].strip()
            try:
                a, b = timecode_seconds(match[1], fps), timecode_seconds(match[2], fps)
                value.update(start=a, end=b)
                value["valid"] = not blocked and b > a and abs(a - expected) < 0.5 / fps and bool(value["description"])
                expected = b
            except ValueError:
                pass
        if not value["valid"]:
            blocked = True
        plan.append(value)
    return plan
