"""One plain-text document with template-owned, immutable structure."""
from difflib import SequenceMatcher

from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtGui import QColor, QKeySequence, QTextCursor
from PySide6.QtWidgets import QTextEdit


def length(text):
    return len(text.encode('utf-16-le')) // 2


def passage(text, start, end):
    return text.encode('utf-16-le')[start * 2:end * 2].decode('utf-16-le')


class ContinuousPromptEditor(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setAccessibleName('Unified prompt text')
        self.highlight_timeline_timecodes = True
        self.ranges = []
        self._text = ''
        self._internal = False
        self.review_applying = lambda: False
        self.writable = lambda: True
        self.textChanged.connect(self._validate_change)

    def section_at(self, position=None):
        position = self.textCursor().position() if position is None else position
        return next((r for r in self.ranges if r['start'] <= position <= r['end']), None)

    def section_for_cursor(self):
        position = self.textCursor().position()
        return (self.section_at(position)
                or next((r for r in self.ranges if position < r['start']), None)
                or (self.ranges[-1] if self.ranges else None))

    def can_replace(self, cursor):
        return (not self.isReadOnly() and self.writable() and any(
            r['start'] <= cursor.selectionStart() <= cursor.selectionEnd() <= r['end']
            for r in self.ranges))

    def replace_cursor(self, cursor, text):
        if self.can_replace(cursor):
            cursor.insertText(text)
            self.setTextCursor(cursor)

    def keyPressEvent(self, event):
        cursor = self.textCursor()
        editing = bool(event.text() and not event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        editing |= any(event.matches(k) for k in (QKeySequence.StandardKey.Cut, QKeySequence.StandardKey.Paste))
        if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            editing = True
            if not cursor.hasSelection():
                cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter if event.key() == Qt.Key.Key_Backspace
                                    else QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor)
        if editing and not self.can_replace(cursor):
            event.accept()
            return
        super().keyPressEvent(event)

    def insertFromMimeData(self, source):
        self.replace_cursor(self.textCursor(), source.text())

    def inputMethodEvent(self, event):
        cursor = self.textCursor()
        if event.replacementLength() or event.replacementStart():
            start = max(0, cursor.position() + event.replacementStart())
            cursor.setPosition(start)
            cursor.setPosition(start + event.replacementLength(), QTextCursor.MoveMode.KeepAnchor)
        if self.can_replace(cursor):
            super().inputMethodEvent(event)
        else:
            event.ignore()

    def dropEvent(self, event):
        target = self.cursorForPosition(event.position().toPoint())
        if not self.can_replace(target):
            event.ignore()
            return
        if event.source() in (self, self.viewport()):
            source = self.textCursor()
            origin = self.section_at(source.selectionStart())
            destination = self.section_at(target.position())
            if not self.can_replace(source) or origin is not destination:
                event.ignore()
                return
            super().dropEvent(event)
        else:
            self.replace_cursor(target, event.mimeData().text())
            event.acceptProposedAction()

    def _validate_change(self):
        if self._internal:
            return
        new = self.toPlainText()
        old = self._text
        if new == old:
            return
        if self.review_applying():
            self._text = new
            return
        # Match unchanged text OUTSIDE a field, rather than a minimal diff.
        # A newline inserted at a field's end can match the first character
        # of a protected separator; it still belongs to the editable field.
        field = None
        for candidate in self.ranges:
            prefix = passage(old, 0, candidate['start'])
            suffix = passage(old, candidate['end'], length(old))
            if (len(new) >= len(prefix) + len(suffix)
                    and new.startswith(prefix) and new.endswith(suffix)):
                field = candidate
                break
        if field is None or not self.writable() or self.isReadOnly():
            # Covers native context-menu slots, accessibility and QTextCursor
            # mutations as well as keyboard/paste/input-method entry points.
            with QSignalBlocker(self):
                self._internal = True
                try:
                    self.undo()
                    if self.toPlainText() != old:
                        self.setPlainText(old)
                finally:
                    self._internal = False
            return
        delta = length(new) - length(old)
        passed = False
        for r in self.ranges:
            if r is field:
                r['end'] += delta
                passed = True
            elif passed:
                r['start'] += delta
                r['end'] += delta
        self._text = new

    def set_parts(self, parts):
        text = ''.join(part['text'] for part in parts)
        old = self.toPlainText()
        if old != text:
            scroll = self.verticalScrollBar().value()
            saved = self.textCursor()
            with QSignalBlocker(self):
                self._internal = True
                try:
                    cursor = QTextCursor(self.document())
                    cursor.beginEditBlock()
                    for tag, a, b, c, d in reversed(SequenceMatcher(None, old, text, autojunk=False).get_opcodes()):
                        if tag != 'equal':
                            cursor.setPosition(length(old[:a]))
                            cursor.setPosition(length(old[:b]), QTextCursor.MoveMode.KeepAnchor)
                            cursor.insertText(text[c:d])
                    cursor.endEditBlock()
                    if not old:
                        self.document().clearUndoRedoStacks()
                    self.setTextCursor(saved)
                finally:
                    self._internal = False
            self.verticalScrollBar().setValue(scroll)
        self._text = text
        self.ranges = []
        position = 0
        for part in parts:
            end = position + length(part['text'])
            if part['kind'] == 'field':
                self.ranges.append({**part, 'start': position, 'end': end})
            position = end

    def field_text(self, key):
        r = next(r for r in self.ranges if r['key'] == key)
        return passage(self._text, r['start'], r['end'])

    def view_position(self, position):
        """Map source offsets into the temporary red/green review document."""
        shown = self.document().toPlainText()
        if shown == self._text:
            return position
        index = len(passage(self._text, 0, position))
        for tag, a, b, c, d in SequenceMatcher(None, self._text, shown, autojunk=False).get_opcodes():
            if a <= index <= b:
                return length(shown[:c + min(index - a, d - c)])
        return length(shown)

    def select_section(self, key, reveal=False):
        r = next((r for r in self.ranges if r['key'] == key), None)
        if r is None:
            self.setExtraSelections([])
            return
        first, last = self.view_position(r['start']), self.view_position(r['end'])
        selections = []
        block = self.document().findBlock(first)
        while block.isValid() and block.position() <= last:
            fragments = block.begin()
            while not fragments.atEnd():
                fragment = fragments.fragment()
                a, b = max(first, fragment.position()), min(last, fragment.position() + fragment.length())
                # Keep the review's red/green backgrounds visible.
                if a < b and fragment.charFormat().background().style() == Qt.BrushStyle.NoBrush:
                    selected = QTextEdit.ExtraSelection()
                    selected.cursor = QTextCursor(self.document())
                    selected.cursor.setPosition(a)
                    selected.cursor.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
                    selected.format.setBackground(QColor('#263e50'))
                    selections.append(selected)
                fragments += 1
            block = block.next()
        self.setExtraSelections(selections)
        if reveal:
            cursor = QTextCursor(self.document())
            cursor.setPosition(first)
            self.setTextCursor(cursor)
            self.ensureCursorVisible()


class SectionText:
    """Section-addressed compatibility facade, never another text widget."""
    def __init__(self, panel, key):
        self.panel, self.key = panel, key

    def toPlainText(self):
        return self.panel.editor.field_text(self.key)

    @property
    def _spell_highlighter(self):
        return self.panel.editor._spell_highlighter

    def setPlainText(self, text):
        r = next(r for r in self.panel.editor.ranges if r['key'] == self.key)
        cursor = QTextCursor(self.panel.editor.document())
        cursor.setPosition(r['start'])
        cursor.setPosition(r['end'], QTextCursor.MoveMode.KeepAnchor)
        self.panel.editor.replace_cursor(cursor, text)

    def setFocus(self):
        self.panel.editor.select_section(self.key, reveal=True)
        self.panel.editor.setFocus()
