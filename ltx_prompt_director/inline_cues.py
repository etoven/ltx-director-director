"""Native QTextEdit table cells with stable timeline identities."""
from __future__ import annotations

import re

from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor, QTextLength, QTextTableFormat

from .timed_action import CUE, NEXT, SECTION

CUE_ID = QTextCharFormat.Property.UserProperty
PREFIX = re.compile(r'^\s*\d{2}:\d{2}:\d{2}:\d{2}\s*-\s*\d{2}:\d{2}:\d{2}:\d{2}:\s*')


def cue_cells(editor) -> dict[str, str]:
    """Read in-document cue cells by segment ID, independent of their text position."""
    cells = {}
    block = editor.document().firstBlock()
    while block.isValid():
        cursor = QTextCursor(editor.document())
        cursor.setPosition(block.position())
        table = cursor.currentTable()
        if table:
            segment_id = str(table.format().property(CUE_ID) or '')
            if segment_id and segment_id not in cells:
                cell = table.cellAt(0, 0)
                first, last = cell.firstCursorPosition().block(), cell.lastCursorPosition().block()
                lines = []
                current = first
                while current.isValid():
                    lines.append(current.text())
                    if current == last:
                        break
                    current = current.next()
                cells[segment_id] = PREFIX.sub('', '\n'.join(lines), count=1).strip()
        block = block.next()
    return cells


def insert_cue_cells(editor, prompt: str, segments) -> bool:
    """Render each timed action as one wrapping, editable cell inside the document."""
    section = SECTION.search(prompt)
    if not section:
        return False
    following = NEXT.search(prompt, section.end())
    body_end = following.start() if following else len(prompt)
    cues = list(CUE.finditer(prompt[section.end():body_end]))
    if len(cues) != len(segments):
        return False
    prefix = prompt[:section.end()]
    suffix = prompt[body_end:]
    editor.clear()
    cursor = editor.textCursor()
    cursor.insertText(prefix.rstrip('\n') + '\n')
    for segment, cue in zip(segments, cues):
        table_format = QTextTableFormat()
        table_format.setProperty(CUE_ID, segment.id)
        table_format.setBorder(0)
        table_format.setCellPadding(6)
        table_format.setCellSpacing(0)
        table_format.setWidth(QTextLength(QTextLength.Type.PercentageLength, 98))
        table_format.setBackground(QColor('#28343a'))
        table = cursor.insertTable(1, 1, table_format)
        cell_cursor = table.cellAt(0, 0).firstCursorPosition()
        cell_cursor.insertText(cue.group(0).strip())
        cursor = table.lastCursorPosition()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText('\n')
    cursor.insertText(suffix.lstrip('\n') if suffix else '')
    return True
