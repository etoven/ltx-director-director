"""Timed actions rendered as editable Qt table cells within the prompt editor."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QKeySequence, QTextCharFormat, QTextCursor, QTextLength, QTextTableCellFormat, QTextTableFormat
from PySide6.QtWidgets import QTextEdit

from .ai import _minimax_timestamp
from .timed_action import CUE, NEXT, SECTION

CUE_ID = QTextCharFormat.Property.UserProperty
TIME = re.compile(r'\d{2}:\d{2}:\d{2}:\d{2}')
PREFIX = re.compile(r'^\s*\d{2}:\d{2}:\d{2}:\d{2}\s*-\s*\d{2}:\d{2}:\d{2}:\d{2}:\s*')


def _cell_text(cell) -> str:
    first, last = cell.firstCursorPosition().block(), cell.lastCursorPosition().block()
    lines = []
    block = first
    while block.isValid():
        lines.append(block.text())
        if block == last:
            break
        block = block.next()
    return '\n'.join(lines)


def _inline_table(cursor):
    table = cursor.currentTable()
    return table if table and table.columns() == 2 else None


def cue_cells(editor) -> dict[str, str]:
    """Read in-document cue actions by segment ID, independent of prose position."""
    cells = {}
    block = editor.document().firstBlock()
    while block.isValid():
        cursor = QTextCursor(editor.document())
        cursor.setPosition(block.position())
        table = _inline_table(cursor)
        if table:
            segment_id = str(table.format().property(CUE_ID) or '')
            if segment_id and segment_id not in cells:
                cells[segment_id] = _cell_text(table.cellAt(0, 1)).strip()
        block = block.next()
    return cells


def inline_prompt(editor) -> str:
    """Serialize native cells to the production prompt's normal timestamp lines."""
    document = editor.document()
    lines = []
    after_table = False
    block = document.firstBlock()
    while block.isValid():
        cursor = QTextCursor(document)
        cursor.setPosition(block.position())
        table = _inline_table(cursor)
        if not table:
            # Qt inserts a structural empty paragraph immediately before every table.
            if not block.text() and after_table and lines and lines[-1] == '':
                block = block.next()
                continue
            if not (not block.text() and block.next().isValid()
                    and _inline_table(_cursor_at(document, block.next().position()))
                    and lines and lines[-1] == '[TIMED ACTION]'):
                lines.append(block.text())
            if block.text():
                after_table = False
            block = block.next()
            continue
        times = TIME.findall(_cell_text(table.cellAt(0, 0)))
        action = _cell_text(table.cellAt(0, 1)).strip()
        if len(times) == 2:
            lines.append(f'{times[0]} - {times[1]}: {action}')
        else:
            lines.append(action)
        after_table = True
        while block.isValid() and _inline_table(_cursor_at(document, block.position())) == table:
            block = block.next()
    return '\n'.join(lines).rstrip()


def _cursor_at(document, position):
    cursor = QTextCursor(document)
    cursor.setPosition(position)
    return cursor


class PromptTextEdit(QTextEdit):
    """One shared editor; protect native MiniMax timecode cells from accidental edits."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.inline_cue_mode = False

    def toPlainText(self):
        return inline_prompt(self) if self.inline_cue_mode else super().toPlainText()

    def _protect_timecode(self) -> bool:
        cursor = self.textCursor()
        table = _inline_table(cursor)
        if table and table.cellAt(cursor).column() == 0:
            self.setTextCursor(table.cellAt(0, 1).firstCursorPosition())
            return True
        if cursor.hasSelection():
            block = self.document().findBlock(cursor.selectionStart())
            while block.isValid() and block.position() <= cursor.selectionEnd():
                probe = _cursor_at(self.document(), block.position())
                selected_table = _inline_table(probe)
                if selected_table and selected_table.cellAt(probe).column() == 0:
                    self.setTextCursor(selected_table.cellAt(0, 1).firstCursorPosition())
                    return True
                block = block.next()
        return False

    def keyPressEvent(self, event):
        editing = bool(event.text() and not event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier))
        editing = editing or event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete, Qt.Key.Key_Return, Qt.Key.Key_Enter)
        editing = editing or event.matches(QKeySequence.StandardKey.Paste) or event.matches(QKeySequence.StandardKey.Cut)
        if self.inline_cue_mode and editing:
            if self._protect_timecode():
                event.accept()
                return
            cursor = self.textCursor()
            table = _inline_table(cursor)
            if table and table.cellAt(cursor).column() == 1:
                cell = table.cellAt(0, 1)
                if ((event.key() == Qt.Key.Key_Backspace and cursor.position() <= cell.firstCursorPosition().position())
                        or (event.key() == Qt.Key.Key_Delete and cursor.position() >= cell.lastCursorPosition().position())):
                    event.accept()
                    return
            if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
                adjacent = cursor.position() + (-1 if event.key() == Qt.Key.Key_Backspace else 1)
                if 0 <= adjacent < self.document().characterCount():
                    probe = _cursor_at(self.document(), adjacent)
                    if _inline_table(probe) and not table:
                        event.accept()
                        return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source):
        if self.inline_cue_mode and self._protect_timecode():
            return
        super().insertFromMimeData(source)

    def cut(self):
        if self.inline_cue_mode and self._protect_timecode():
            return
        super().cut()

    def dropEvent(self, event):
        if self.inline_cue_mode and event.source() is self and self._protect_timecode():
            event.ignore()
            return
        super().dropEvent(event)


def insert_cue_cells(editor, prompt: str, segments) -> bool:
    """Render all timed ranges as wrapping cells; link only verified timeline ranges."""
    section = SECTION.search(prompt)
    if not section:
        return False
    following = NEXT.search(prompt, section.end())
    body_end = following.start() if following else len(prompt)
    cues = list(CUE.finditer(prompt[section.end():body_end]))
    if not cues:
        return False
    prefix, suffix = prompt[:section.end()], prompt[body_end:]
    editor.clear()
    cursor = editor.textCursor()
    cursor.insertText(prefix.rstrip('\n') + '\n')
    boundaries = {}
    elapsed = 0.0
    for segment in segments:
        end = elapsed + segment.duration
        boundaries[(_minimax_timestamp(elapsed), _minimax_timestamp(end))] = segment.id
        elapsed = end
    used = set()
    for index, cue in enumerate(cues):
        segment_id = boundaries.get((cue.group(1), cue.group(2)))
        if not segment_id and len(cues) == len(segments):
            segment_id = segments[index].id
        if segment_id in used:
            segment_id = None
        if segment_id:
            used.add(segment_id)
        table_format = QTextTableFormat()
        if segment_id:
            table_format.setProperty(CUE_ID, segment_id)
        table_format.setBorder(0)
        table_format.setCellPadding(6)
        table_format.setCellSpacing(0)
        table_format.setWidth(QTextLength(QTextLength.Type.PercentageLength, 98))
        table_format.setColumnWidthConstraints([
            QTextLength(QTextLength.Type.FixedLength, 130),
            QTextLength(QTextLength.Type.PercentageLength, 80),
        ])
        table = cursor.insertTable(1, 2, table_format)
        time_cell, action_cell = table.cellAt(0, 0), table.cellAt(0, 1)
        for cell, color in ((time_cell, '#172930'), (action_cell, '#28343a')):
            cell_format = QTextTableCellFormat(cell.format())
            cell_format.setBackground(QColor(color))
            cell.setFormat(cell_format)
        time_cursor = time_cell.firstCursorPosition()
        time_style = QTextCharFormat()
        time_style.setForeground(QColor('#9dcfdf'))
        time_style.setFontWeight(600)
        time_cursor.insertText(f'{cue.group(1)}\n→ {cue.group(2)}', time_style)
        action_cell.firstCursorPosition().insertText(PREFIX.sub('', cue.group(0).strip(), count=1))
        cursor = table.lastCursorPosition()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText('\n')
    cursor.insertText(suffix.lstrip('\n') if suffix else '')
    return True
