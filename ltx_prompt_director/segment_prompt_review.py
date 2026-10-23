"""A temporary, segment-bounded unified preview for ordinary local typing."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, QTimer, Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QApplication, QToolButton, QWidget

from .ai_inline_diff import word_diff
from .prompt_sections import document_sections
from .spellcheck import refresh_spellcheck


class SegmentPromptReview(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.editor = window.unified_prompt
        self.source_editor = None
        self.source_document = None
        self.preview_document = None
        self.buttons = []
        self._serial = 0

    @property
    def active(self):
        return self.source_document is not None

    def identity(self):
        segment = self.window.current_segment()
        return (self.window.workspace_revision, self.window.current_project_id,
                self.window.project_type, segment.id if segment else None,
                self.window.minimax_prompt_text)

    def preview(self, source, replacement: str):
        if not source.hasFocus():
            return
        identity = self.identity()
        if self.active and (self.signature != identity or self.source_editor is not source):
            self.reject()
        if not self.active:
            row = self.window.timeline.currentRow()
            section = next((s for s in document_sections(self.editor) if s.index == row), None)
            if section is None:
                return
            cursor = QTextCursor(self.editor.document())
            cursor.setPosition(section.body_start)
            cursor.setPosition(section.end, QTextCursor.MoveMode.KeepAnchor)
            self.original = cursor.selectedText().replace('\u2029', '\n').replace('\u2028', '\n')
            if self.original == replacement:
                return
            self.section = section
            self.signature = identity
            self.source_editor = source
            self.saved_cursor = self.editor.textCursor()
            self.source_document = self.editor.document()
            self.source_document.setParent(self)
            self.was_read_only = self.editor.isReadOnly()
            self._serial += 1
            QApplication.instance().installEventFilter(self)
            self._make_buttons()
        self.replacement = replacement
        if replacement == self.original:
            self.reject()
            return
        self._render()

    def _make_buttons(self):
        for text, name, color, action in (
            ('✓ Approve', 'Approve unified segment edit', '#238636', self.approve),
            ('× Reject', 'Reject unified segment edit', '#b53b43', self.reject),
        ):
            button = QToolButton(self.editor)
            button.setText(text)
            button.setAccessibleName(name)
            button.setToolTip(name)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setFixedSize(75, 20)
            button.setStyleSheet(f'QToolButton{{background:{color};border:0;border-radius:4px;'
                                'color:white;font-size:10px;padding:0}QToolButton:hover{border:1px solid white}')
            button.clicked.connect(action)
            self.buttons.append(button)
        self.editor.setViewportMargins(0, 23, 0, 0)
        self.reposition()

    def _render(self):
        previous = self.preview_document
        self.preview_document = self.source_document.clone(self)
        for section in document_sections(self.preview_document):
            cursor = QTextCursor(self.preview_document)
            cursor.setPosition(section.start)
            cursor.setPosition(section.end, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            color = QColor('#6f9faf')
            color.setAlpha(25 if section.index == self.section.index else 18)
            fmt.setBackground(color)
            cursor.mergeCharFormat(fmt)
            for start, code in section.codes:
                cursor.setPosition(start)
                cursor.setPosition(start + len(code), QTextCursor.MoveMode.KeepAnchor)
                fmt = QTextCharFormat()
                fmt.setBackground(QColor('#80612a' if section.index == self.section.index else '#245163'))
                fmt.setForeground(QColor('#fff0bf' if section.index == self.section.index else '#d0f3ff'))
                cursor.mergeCharFormat(fmt)
        cursor = QTextCursor(self.preview_document)
        cursor.setPosition(self.section.body_start)
        cursor.setPosition(self.section.end, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        for state, text in word_diff(self.original, self.replacement):
            fmt = QTextCharFormat()
            color = QColor('#6f9faf')
            color.setAlpha(25)
            fmt.setBackground(color)
            if state == 'added':
                fmt.setForeground(QColor('#14251a'))
                fmt.setBackground(QColor('#80e991'))
            elif state == 'removed':
                fmt.setForeground(QColor('#ffe2e5'))
                fmt.setBackground(QColor('#9c3f4b'))
                fmt.setFontStrikeOut(True)
            cursor.insertText(text, fmt)
        with QSignalBlocker(self.editor):
            self.editor.setExtraSelections([])
            self.editor.setDocument(self.preview_document)
            self.editor.setReadOnly(True)
            anchor = QTextCursor(self.preview_document)
            anchor.setPosition(self.section.body_start)
            self.editor.setTextCursor(anchor)
            self.editor.ensureCursorVisible()
        if previous is not None:
            previous.deleteLater()
        self.reposition()

    def reposition(self):
        for index, button in enumerate(self.buttons):
            button.move(max(2, self.editor.width() - (len(self.buttons) - index) * 79 - 5), 2)
            button.show()
            button.raise_()

    def approve(self):
        if not self.active:
            return
        if self.identity() != self.signature:
            self.reject()
            return
        section, replacement = self.section, self.replacement
        self._restore()
        cursor = QTextCursor(self.editor.document())
        cursor.beginEditBlock()
        cursor.setPosition(section.body_start)
        cursor.setPosition(section.end, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(replacement)
        cursor.endEditBlock()
        self.window.highlight_selected_prompt_timecode()

    def reject(self):
        if self.active:
            self._restore()
            self.window.highlight_selected_prompt_timecode()

    def _restore(self):
        document, preview = self.source_document, self.preview_document
        self.source_document = self.preview_document = self.source_editor = None
        self._serial += 1
        QApplication.instance().removeEventFilter(self)
        scroll = self.editor.verticalScrollBar().value()
        with QSignalBlocker(self.editor):
            self.editor.setExtraSelections([])
            self.editor.setDocument(document)
            document.setParent(self.editor)
            self.editor.setReadOnly(self.was_read_only)
            self.editor.setTextCursor(self.saved_cursor)
            self.editor.verticalScrollBar().setValue(min(scroll, self.editor.verticalScrollBar().maximum()))
            refresh_spellcheck(self.editor)
        self.editor.setViewportMargins(0, 0, 0, 0)
        for button in self.buttons:
            button.hide()
            button.deleteLater()
        self.buttons = []
        if preview is not None:
            preview.deleteLater()

    def eventFilter(self, watched, event):
        if watched is self.editor and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.reposition()
        if self.active:
            if watched is self.source_editor and event.type() == QEvent.Type.FocusOut:
                serial = self._serial
                QTimer.singleShot(0, lambda: self._reject_after_focus_out(serial))
            elif event.type() == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget):
                allowed = [self.source_editor, *self.buttons]
                if not any(watched is item or item.isAncestorOf(watched) for item in allowed):
                    self.reject()
            elif event.type() == QEvent.Type.MouseButtonRelease:
                serial = self._serial
                QTimer.singleShot(0, lambda: self._reject_after_focus_out(serial))
        return False

    def _reject_after_focus_out(self, serial):
        if (self.active and serial == self._serial and not self.source_editor.hasFocus()
                and not any(button.isDown() for button in self.buttons)):
            self.reject()
