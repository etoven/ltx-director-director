"""Local, best-effort timeline timing for an independently editable prompt."""
from __future__ import annotations

import re

from PySide6.QtCore import QSignalBlocker
from PySide6.QtGui import QTextCursor

from .inline_cues import NOTE_TAG


# Consume the whole candidate so a broken code cannot match as a shorter one.
TIME_CODE = re.compile(r"(?<![\w:.])\d{2,}(?::\d+){1,3}(?:\.\d+)?(?![\w.]|:[\w.])")
RANGE_SEPARATOR = re.compile(r"\s*(?:[-–—→]|to)\s*", re.IGNORECASE)


def timecodes(text: str):
    """Find valid MM:SS[.fraction], HH:MM:SS, or 24 fps SMPTE codes."""
    for match in TIME_CODE.finditer(text):
        value = match.group()
        parts = value.split(':')
        if any(len(part) != 2 for part in parts[1:-1]):
            continue
        last = parts[-1].split('.')
        if len(last[0]) != 2 or int(last[0]) >= (24 if len(parts) == 4 else 60):
            continue
        if len(parts) > 2 and ('.' in value or any(int(p) >= 60 for p in parts[1:-1])):
            continue
        yield match


def format_timecode(seconds: float, template: str) -> str:
    """Keep the user's notation and precision, rounding with carry."""
    parts = template.split(':')
    if len(parts) == 2:
        precision = len(parts[-1].split('.')[1]) if '.' in template else 0
        scale = 10 ** precision
        ticks = int(seconds * scale + .5)
        minutes, rest = divmod(ticks, 60 * scale)
        whole, fraction = divmod(rest, scale)
        result = f'{minutes:0{len(parts[0])}d}:{whole:02d}'
        return result + (f'.{fraction:0{precision}d}' if precision else '')
    scale = 24 if len(parts) == 4 else 1
    whole, frames = divmod(int(seconds * scale + .5), scale)
    minutes, sec = divmod(whole, 60)
    hours, minutes = divmod(minutes, 60)
    result = f'{hours:0{len(parts[0])}d}:{minutes:02d}:{sec:02d}'
    return result + (f':{frames:02d}' if len(parts) == 4 else '')


def cue_timecodes(text: str):
    """A range belongs to one segment; a lone code marks a segment start."""
    matches = list(timecodes(text))
    index = 0
    while index < len(matches):
        first = matches[index]
        if index + 1 < len(matches) and RANGE_SEPARATOR.fullmatch(text[first.end():matches[index + 1].start()]):
            yield (first, matches[index + 1])
            index += 2
        else:
            yield (first,)
            index += 1


def utf16_length(text: str) -> int:
    return len(text.encode('utf-16-le')) // 2


def document_cues(editor):
    """Yield native document positions, excluding editable instruction notes."""
    block = editor.document().firstBlock()
    while block.isValid():
        table = QTextCursor(block).currentTable()
        in_note = False
        while table:
            if table.format().property(NOTE_TAG):
                in_note = True
                break
            parent = table.parentFrame()
            table = parent if hasattr(parent, 'columns') else None
        if not in_note:
            text = block.text()
            for cue in cue_timecodes(text):
                yield tuple((block.position() + utf16_length(text[:m.start()]), m.group()) for m in cue)
        block = block.next()


def retime_prompt(editor, durations: list[float], roles: list[str] | None = None) -> bool:
    """Patch only codes in place, keeping native notes, undo, selection and scroll.

    Broken codes are skipped; extra cues remain authored. Matching starts over
    on every timeline edit, so repairing a code reconnects it automatically.
    """
    # Imported here because the section parser shares the timecode tokenizer.
    from .prompt_sections import document_sections

    boundaries = [0.0]
    for duration in durations:
        boundaries.append(boundaries[-1] + duration)
    replacements = []
    for section in document_sections(editor):
        cue_index, cue = section.index, section.codes
        if cue_index >= len(durations):
            continue
        for endpoint, (start, template) in enumerate(cue):
            if len(cue) == 1 and roles is not None and roles[cue_index] == 'end':
                endpoint = 1
            replacement = format_timecode(boundaries[cue_index + endpoint], template)
            if replacement != template:
                replacements.append((start, start + len(template), replacement))
    if not replacements:
        return False

    original = editor.textCursor()
    position, anchor = original.position(), original.anchor()
    scroll_x = editor.horizontalScrollBar().value()
    scroll_y = editor.verticalScrollBar().value()

    def mapped(value):
        shift = 0
        for start, end, replacement in replacements:
            if value < start:
                break
            if value <= end:
                offset = len(replacement) if value == end else min(value - start, len(replacement))
                return start + shift + offset
            shift += len(replacement) - (end - start)
        return value + shift

    with QSignalBlocker(editor):
        cursor = QTextCursor(editor.document())
        cursor.beginEditBlock()
        for start, end, replacement in reversed(replacements):
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(replacement)
        cursor.endEditBlock()
        original.setPosition(mapped(anchor))
        original.setPosition(mapped(position), QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(original)
        editor.horizontalScrollBar().setValue(scroll_x)
        editor.verticalScrollBar().setValue(scroll_y)
    return True
