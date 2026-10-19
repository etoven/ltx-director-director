"""Recognize editable shot passages without including their structural prefixes."""
from __future__ import annotations

from dataclasses import dataclass
import re

from PySide6.QtGui import QTextCursor

from .inline_cues import NOTE_TAG
from .prompt_timecodes import cue_timecodes, utf16_length


SHOT = re.compile(r'\[Shot[ \t]*(\d+)\]', re.IGNORECASE)
BODY = re.compile(r'(?im)^[ \t]*(?:integrated_multimodal_description|detailed_description)[ \t]*:?[ \t]*')
SECTION = re.compile(
    r'(?im)^[ \t]*(?:(?:overall_soundscape|non_diegetic_music|subject_definitions|summary|retention_analysis)\b'
    r'|\[(?!shot\b)[A-Z][A-Z _-]+\][ \t]*$)')


@dataclass(frozen=True)
class PromptSection:
    index: int
    start: int
    body_start: int
    end: int
    codes: tuple[tuple[int, str], ...] = ()


def prompt_sections(text: str) -> list[PromptSection]:
    """Offsets are Python indices; shot labels and timestamp punctuation stay fixed.

    Explicit shot numbers take precedence over ordinal timecode counting. This
    also handles MiniMax's untimed first shot and skips image-alignment preambles.
    """
    text = text.replace('\u2029', '\n').replace('\u2028', '\n')
    header = BODY.search(text)
    begin = header.end() if header else 0
    shots = [m for m in SHOT.finditer(text, begin)
             if not re.search(r'\bfrom[ \t]*$', text[max(begin, m.start() - 12):m.start()], re.I)
             and not text[m.end():].lstrip(' \t').startswith(')')]
    if shots:
        limit = SECTION.search(text, shots[0].end())
        stop = limit.start() if limit else len(text)
        shots = [m for m in shots if m.start() < stop]
        boundaries = [(int(m[1]) - 1, m.end(), shots[i + 1].start() if i + 1 < len(shots) else stop)
                      for i, m in enumerate(shots)]
    else:
        cues = list(cue_timecodes(text[begin:]))
        limit = SECTION.search(text, begin + cues[0][0].start()) if cues else None
        stop = limit.start() if limit else len(text)
        cues = [cue for cue in cues if begin + cue[0].start() < stop]
        boundaries = [(i, begin + cue[0].start(), begin + cues[i + 1][0].start() if i + 1 < len(cues) else stop)
                      for i, cue in enumerate(cues)]
    result = []
    for index, start, end in boundaries:
        following = SECTION.search(text, start, end)
        if following:
            end = following.start()
        end = start + len(text[start:end].rstrip(' \t\r\n'))
        prefix = re.match(r'[ \t]*(?:At[ \t]+)?', text[start:end], re.I)
        candidate = start + prefix.end()
        first = next(cue_timecodes(text[candidate:end]), ())
        codes = ()
        if first and first[0].start() == 0:
            codes = tuple((candidate + m.start(), m.group()) for m in first)
            start = candidate
            body = candidate + first[-1].end()
        else:
            start = body = start + re.match(r'[ \t]*', text[start:end]).end()
        punctuation = re.match(r'[ \t,:]*', text[body:end])
        body += punctuation.end()
        if index >= 0 and start < end:
            result.append(PromptSection(index, start, body, end, codes))
    return result


def document_sections(editor) -> list[PromptSection]:
    """Translate to Qt UTF-16 positions, leaving native instruction notes alone."""
    document = editor.document() if hasattr(editor, 'document') else editor
    raw = document.toRawText()
    # Mask native notes before parsing so a clock in an instruction cannot
    # consume a segment ordinal or become a shot delimiter.
    masked = list(raw)
    encoded = raw.encode('utf-16-le')
    notes = set()
    block = document.firstBlock()
    while block.isValid():
        frame = QTextCursor(block).currentTable()
        while frame:
            if frame.format().property(NOTE_TAG):
                notes.add((frame.firstPosition() - 1, frame.lastPosition() + 1))
            parent = frame.parentFrame()
            frame = parent if hasattr(parent, 'columns') else None
        block = block.next()
    for start, end in notes:
        a = len(encoded[:start * 2].decode('utf-16-le'))
        b = len(encoded[:end * 2].decode('utf-16-le'))
        masked[a:b] = ['\n' if ch in '\n\r\u2029\u2028' else ' ' for ch in raw[a:b]]
    result = []
    for section in prompt_sections(''.join(masked)):
        start = utf16_length(raw[:section.start])
        table = QTextCursor(document)
        table.setPosition(start)
        frame = table.currentTable()
        in_note = False
        while frame:
            if frame.format().property(NOTE_TAG):
                in_note = True
                break
            parent = frame.parentFrame()
            frame = parent if hasattr(parent, 'columns') else None
        if not in_note:
            result.append(PromptSection(
                section.index, start, utf16_length(raw[:section.body_start]),
                utf16_length(raw[:section.end]),
                tuple((utf16_length(raw[:position]), code) for position, code in section.codes)))
    return result
