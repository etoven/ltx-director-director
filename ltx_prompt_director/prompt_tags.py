"""Native slash command suggestions for every editable prompt field."""
from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QTextBlockFormat, QTextCharFormat, QTextCursor, QTextLength, QTextTableCellFormat, QTextTableFormat
from PySide6.QtWidgets import QCompleter, QTextEdit
from .inline_cues import NOTE_TAG, PromptTextEdit

TAGS = ('/refine', '/refine-global', '/keep', '/avoid', '/focus')
HINTS = {
    '/refine': 'Refine this passage or cue',
    '/refine-global': 'Apply this instruction to the whole prompt',
    '/keep': 'Preserve this detail',
    '/avoid': 'Exclude this detail',
    '/focus': 'Emphasize this detail',
}
NOTE_LABELS = {'/refine': 'Refinement', '/refine-global': 'Global refinement',
               '/keep': 'Keep', '/avoid': 'Avoid', '/focus': 'Focus'}
PARTIAL = re.compile(r'/[\w-]*$')
EXISTING = re.compile(r'(?<!\w)/(refine-global|refine|keep|avoid|focus)\b[ \t]*([^\n]*)', re.IGNORECASE)
GLOBAL_REFINEMENT_LINE = re.compile(r'(?im)^[ \t]*/refine-global\b[^\n]*(?:\n[ ]{4}[^\n]*)*\n?')


def consume_global_refinements(prompt: str) -> str:
    """Remove completed global edit instructions from a successful response."""
    return re.sub(r'\n{3,}', '\n\n', GLOBAL_REFINEMENT_LINE.sub('', prompt)).strip()

def insert_note(editor: PromptTextEdit, cursor: QTextCursor, tag: str, body: str = '') -> None:
    """Insert a native wrapping note inside the existing document and focus its body."""
    cursor.beginEditBlock()
    cursor.removeSelectedText()
    fmt = QTextTableFormat()
    fmt.setProperty(NOTE_TAG, tag)
    fmt.setBorder(0)
    fmt.setMargin(4)
    fmt.setCellPadding(0)
    fmt.setCellSpacing(0)
    global_note = tag == '/refine-global'
    fmt.setWidth(QTextLength(QTextLength.Type.PercentageLength, 96 if global_note else 62))
    table = cursor.insertTable(2, 1, fmt)
    value, label = table.cellAt(0, 0), table.cellAt(1, 0)
    for cell, top, bottom in ((value, 8, 1), (label, 0, 6)):
        cell_format = QTextTableCellFormat(cell.format())
        cell_format.setTopPadding(top)
        cell_format.setBottomPadding(bottom)
        cell_format.setLeftPadding(10)
        cell_format.setRightPadding(10)
        cell.setFormat(cell_format)
    label_cursor = label.firstCursorPosition()
    label_block = QTextBlockFormat()
    label_block.setAlignment(Qt.AlignmentFlag.AlignRight)
    label_block.setTopMargin(0)
    label_block.setBottomMargin(0)
    label_cursor.setBlockFormat(label_block)
    label_format = QTextCharFormat()
    label_format.setForeground(QColor('#91b6c2'))
    label_format.setFont(QFont('Sans Serif', 8, QFont.Weight.Medium))
    label_cursor.insertText(NOTE_LABELS.get(tag, tag[1:].replace('-', ' ').title()), label_format)
    body_cursor = value.firstCursorPosition()
    if body:
        body_cursor.insertText(body)
    cursor.endEditBlock()
    editor.setTextCursor(body_cursor)


def render_prompt_notes(editor: PromptTextEdit) -> None:
    """Rehydrate saved slash directives into their editable native note boxes."""
    found = []
    block = editor.document().firstBlock()
    while block.isValid():
        probe = QTextCursor(editor.document())
        probe.setPosition(block.position())
        active = probe.currentTable()
        if block.text() and not (active and active.format().property(NOTE_TAG)):
            matches = list(EXISTING.finditer(block.text()))
            if matches:
                match = matches[0]
                body = match.group(2).strip()
                start = block.position() + match.start()
                end = block.position() + match.end()
                continuation = block.next()
                while continuation.isValid() and continuation.text().startswith('    '):
                    body += '\n' + continuation.text()[4:]
                    end = continuation.position() + len(continuation.text())
                    block = continuation
                    continuation = continuation.next()
                found.append((start, end, '/' + match.group(1).lower(), body))
        block = block.next()
    for first, last, tag, body in reversed(found):
        cursor = QTextCursor(editor.document())
        cursor.setPosition(first)
        cursor.setPosition(last, QTextCursor.MoveMode.KeepAnchor)
        insert_note(editor, cursor, tag, body)


class PromptTagSuggestions(QObject):
    def __init__(self, editor: QTextEdit):
        super().__init__(editor)
        self.editor = editor
        self.completer = QCompleter(TAGS, editor)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.setWidget(editor)
        self.completer.activated[str].connect(self.insert_tag)
        self.completer.popup().setStyleSheet('QListView { background: #182a31; color: #d7edf2; border: 1px solid #558497; padding: 5px; } QListView::item { padding: 5px 12px; } QListView::item:selected { background: #275467; }')
        self.completer.popup().installEventFilter(self)
        editor.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched in (self.editor, self.completer.popup()) and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Tab and self.completer.popup().isVisible():
                item = self.completer.popup().currentIndex().data() or self.completer.currentCompletion()
                if item:
                    self.completer.popup().hide()
                    self.insert_tag(str(item))
                    return True
        if watched is self.editor and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Slash or (event.text() and (event.text().isalnum() or event.text() == '-')) or event.key() == Qt.Key.Key_Backspace:
                QTimer.singleShot(0, self.update_suggestions)
            elif event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Space, Qt.Key.Key_Return):
                if event.key() == Qt.Key.Key_Escape:
                    self.completer.popup().hide()
        return super().eventFilter(watched, event)

    def update_suggestions(self):
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            return
        before = cursor.block().text()[:cursor.positionInBlock()]
        match = PARTIAL.search(before)
        if not match or (match.start() and before[match.start() - 1].isalnum()):
            self.completer.popup().hide()
            return
        prefix = match.group()
        self.completer.setCompletionPrefix(prefix)
        if not self.completer.completionCount():
            self.completer.popup().hide()
            return
        rect = self.editor.cursorRect(cursor)
        rect.setWidth(240)
        self.completer.complete(rect)
        self.completer.popup().setToolTip(' /refine affects this cue; /refine-global affects the whole prompt')

    def insert_tag(self, tag: str):
        cursor = self.editor.textCursor()
        before = cursor.block().text()[:cursor.positionInBlock()]
        match = PARTIAL.search(before)
        if not match:
            return
        cursor.setPosition(cursor.position() - len(match.group()), QTextCursor.MoveMode.KeepAnchor)
        if isinstance(self.editor, PromptTextEdit):
            insert_note(self.editor, cursor, tag)
        else:
            cursor.insertText(tag + ' ')
            self.editor.setTextCursor(cursor)


def install_prompt_tags(editor: QTextEdit):
    if not getattr(editor, '_prompt_tag_suggestions', None):
        editor._prompt_tag_suggestions = PromptTagSuggestions(editor)
