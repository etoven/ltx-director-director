"""Native slash command suggestions for every editable prompt field."""
from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QCompleter, QTextEdit

TAGS = ('/refine', '/refine-global', '/keep', '/avoid', '/focus')
HINTS = {
    '/refine': 'Refine this passage or cue',
    '/refine-global': 'Apply this instruction to the whole prompt',
    '/keep': 'Preserve this detail',
    '/avoid': 'Exclude this detail',
    '/focus': 'Emphasize this detail',
}
PARTIAL = re.compile(r'/[\w-]*$')


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
        editor.installEventFilter(self)

    def eventFilter(self, watched, event):
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
        cursor.insertText(tag + ' ')
        self.editor.setTextCursor(cursor)


def install_prompt_tags(editor: QTextEdit):
    if not getattr(editor, '_prompt_tag_suggestions', None):
        editor._prompt_tag_suggestions = PromptTagSuggestions(editor)
