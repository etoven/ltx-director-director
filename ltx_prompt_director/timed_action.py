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
