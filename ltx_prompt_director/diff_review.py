"""Word-level, non-destructive inline AI diff review in a QTextEdit.

The authoritative proposed text stays in its original QTextDocument; a
temporary read-only projection shows added words in green and removed words
struck through in red. Small per-paragraph approval controls float inside the
same editor. No diff markup leaks into saved prompts or JSON exports.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from html import escape
import re

from .ai_inline_diff import word_diff

from PySide6.QtCore import QEvent, QObject, QTimer, Qt, QSignalBlocker
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import QCheckBox, QToolButton, QTextEdit


def paragraphs(text: str) -> list[str]:
    """Split at blank lines without losing any whitespace or line endings."""
    return [part for part in re.findall(r"[\s\S]*?(?:\n[ \t]*\n|\Z)", text)
            if part]


@dataclass
class ParagraphChange:
    old: str
    new: str
    decision: str = "pending"  # equal, pending, approved, rejected


def paragraph_changes(before: str, after: str) -> list[ParagraphChange]:
    old, new = paragraphs(before), paragraphs(after)
    pieces = []
    for tag, a, b, c, d in SequenceMatcher(
        None, old, new, autojunk=False
    ).get_opcodes():
        if tag == "equal":
            pieces.extend(ParagraphChange(p, p, "equal") for p in new[c:d])
            continue
        # Pair replacements one paragraph at a time, including independent
        # insertions and deletions. This makes each change independently
        # reversible rather than rejecting a large hunk at once.
        count = max(b - a, d - c)
        for offset in range(count):
            pieces.append(ParagraphChange(
                old[a + offset] if a + offset < b else "",
                new[c + offset] if c + offset < d else "",
            ))
    return pieces


def reviewed_text(parts: list[ParagraphChange]) -> str:
    return "".join(piece.old if piece.decision == "rejected" else piece.new
                   for piece in parts)


def removed_paragraph_html(original: str) -> str:
    """Red strikethrough deletion, matching the live red/green inline diff.

    Escaping is required: prompts often contain <Picture 1> and JSON.
    Display markup is NEVER inserted into the saved user prompt.
    """
    safe = escape(original.rstrip("\n")).replace("\n", "<br/>")
    return ('<span style="color:#ed969e;background-color:#41242b;'
            'text-decoration:line-through">− ' + safe + '</span>')


def _utf16_len(text: str) -> int:
    """Qt QTextCursor indexes UTF-16 code units, including non-BMP characters."""
    return len(text.encode("utf-16-le")) // 2


class ParagraphDiffReview(QObject):
    """Inline red/green word changes with individually reversible paragraphs.

    Display-only QTextDocument lets us show both the removed and replacement
    text on the SAME line while keeping the actual editor document untouched.
    The authoritative document is restored before any genuine text change.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.editor = None
        self.parts: list[ParagraphChange] = []
        self.buttons = []
        # Kept as an empty compatibility attribute: red deletions are now
        # inline in the projection, never QLabel blocks above the paragraph.
        self.removed_labels = []
        self.approve_all = None
        self.deny_all = None
        self.source_document = None
        self.preview_document = None
        self._original_read_only = False
        self._applying = False

    @property
    def pending_count(self) -> int:
        return sum(part.decision == "pending" for part in self.parts)

    def begin(self, editor: QTextEdit, before: str, after: str) -> bool:
        self.finish()
        if not before.strip() or before == after or editor.toPlainText() != after:
            return False
        parts = paragraph_changes(before, after)
        if not any(part.decision == "pending" for part in parts):
            return False
        self.editor = editor
        self.parts = parts
        self._original_read_only = editor.isReadOnly()
        # Retain the original document (including its undo stack) while only
        # the transient, colored projection is visible to the user.
        self.source_document = editor.document()
        self.source_document.setParent(self)
        self.preview_document = QTextDocument(self)
        self.preview_document.setDefaultFont(self.source_document.defaultFont())
        self.preview_document.setDocumentMargin(self.source_document.documentMargin())
        with QSignalBlocker(editor):
            editor.setDocument(self.preview_document)
        editor.setReadOnly(True)

        # A tiny 23px strip INSIDE the editor, not another exterior toolbar.
        editor.setViewportMargins(0, 23, 0, 0)
        self.approve_all = QCheckBox("✓ Approve all", editor)
        self.approve_all.setObjectName("diffApproveAll")
        self.approve_all.setAccessibleName("Approve all paragraph changes")
        self.approve_all.setToolTip("Keep every remaining AI paragraph change")
        self.approve_all.setStyleSheet(
            "QCheckBox#diffApproveAll{background:#16372b;border:0;"
            "border-radius:4px;color:#b6eccd;font-size:10px;"
            "padding:1px 5px;spacing:4px}"
            "QCheckBox#diffApproveAll:hover{background:#20523a;color:white}"
        )
        self.approve_all.setFixedSize(110, 20)
        self.approve_all.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.approve_all.toggled.connect(self._approve_all_toggled)
        self.approve_all.show()
        self.deny_all = QCheckBox("× Deny all", editor)
        self.deny_all.setObjectName("diffDenyAll")
        self.deny_all.setAccessibleName("Deny all paragraph changes")
        self.deny_all.setToolTip("Restore the original text for all remaining changes")
        self.deny_all.setStyleSheet(
            "QCheckBox#diffDenyAll{background:#44272a;border:0;"
            "border-radius:4px;color:#f3a6ac;font-size:10px;"
            "padding:1px 5px;spacing:4px}"
            "QCheckBox#diffDenyAll:hover{background:#663239;color:white}"
        )
        self.deny_all.setFixedSize(91, 20)
        self.deny_all.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.deny_all.toggled.connect(self._deny_all_toggled)
        self.deny_all.show()
        editor.viewport().installEventFilter(self)
        editor.installEventFilter(self)
        editor.verticalScrollBar().valueChanged.connect(self.reposition)
        editor.horizontalScrollBar().valueChanged.connect(self.reposition)
        editor.textChanged.connect(self._editor_changed)
        self._refresh()
        QTimer.singleShot(0, self.reposition)
        return True

    def _editor_changed(self):
        if not self._applying:
            # Unexpected external mutation: end the review rather than
            # applying stale paragraph offsets to the wrong document.
            self.finish()

    def _approve_all_toggled(self, checked):
        if checked:
            for part in self.parts:
                if part.decision == "pending":
                    part.decision = "approved"
            self._apply()
            self.finish()

    def _deny_all_toggled(self, checked):
        if checked:
            for part in self.parts:
                if part.decision == "pending":
                    part.decision = "rejected"
            self._apply()
            self.finish()

    def decide(self, index: int, approve: bool):
        if self.editor is None or index < 0 or index >= len(self.parts):
            return
        part = self.parts[index]
        if part.decision != "pending":
            return
        part.decision = "approved" if approve else "rejected"
        self._apply()
        if not self.pending_count:
            self.finish()
        else:
            self._refresh()

    def _apply(self):
        """Commit decisions to the actual document, never the colored view."""
        editor = self.editor
        if editor is None or self.source_document is None:
            return
        updated = reviewed_text(self.parts)
        if self.source_document.toPlainText() == updated:
            return
        scrollbar = editor.verticalScrollBar()
        scroll = scrollbar.value()
        self._applying = True
        try:
            # Temporarily expose the actual document so regular textChanged
            # observers see and persist the correct (markup-free) prompt.
            with QSignalBlocker(editor):
                editor.setDocument(self.source_document)
            editor.setReadOnly(self._original_read_only)
            editor.setPlainText(updated)
            with QSignalBlocker(editor):
                editor.setDocument(self.preview_document)
            editor.setReadOnly(True)
        finally:
            self._applying = False
        scrollbar.setValue(min(scroll, scrollbar.maximum()))

    def _refresh(self):
        editor = self.editor
        if editor is None or self.preview_document is None:
            return
        for approve, reject, _pos in self.buttons:
            approve.hide()
            reject.hide()
            approve.deleteLater()
            reject.deleteLater()
        self.buttons = []
        # Render additions/deletions inline, preserving exact paragraph order.
        # The paragraph's BEFORE wording is not placed above the change.
        with QSignalBlocker(editor):
            self.preview_document.clear()
            cursor = QTextCursor(self.preview_document)
            normal = QTextCharFormat()
            added = QTextCharFormat()
            added.setForeground(QColor("#14251a"))
            added.setBackground(QColor("#80e991"))
            removed = QTextCharFormat()
            removed.setForeground(QColor("#ffe2e5"))
            removed.setBackground(QColor("#9c3f4b"))
            removed.setFontStrikeOut(True)
            for index, part in enumerate(self.parts):
                offset = cursor.position()
                if part.decision == "pending":
                    for state, passage in word_diff(part.old, part.new):
                        cursor.insertText(passage, {
                            "equal": normal, "added": added, "removed": removed
                        }[state])
                else:
                    cursor.insertText(
                        part.old if part.decision == "rejected" else part.new,
                        normal
                    )
                if part.decision != "pending":
                    continue
                approve = QToolButton(editor.viewport())
                reject = QToolButton(editor.viewport())
                for control, label, accessible, tooltip, color in (
                    (approve, "✓", "Approve this paragraph", "Approve AI change", "#238636"),
                    (reject, "×", "Reject this paragraph",
                     "Reject AI change; restore original: " + part.old.strip()[:240],
                     "#b53b43"),
                ):
                    control.setObjectName("diffReviewButton")
                    control.setText(label)
                    control.setAccessibleName(accessible)
                    control.setToolTip(tooltip)
                    control.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                    control.setFixedSize(19, 19)
                    control.setStyleSheet(
                        "QToolButton#diffReviewButton{background:" + color +
                        ";border:0;border-radius:4px;color:white;"
                        "font-size:12px;font-weight:bold;padding:0;min-height:0}"
                        "QToolButton#diffReviewButton:hover{background:#397fa3}"
                    )
                approve.clicked.connect(lambda checked=False, i=index: self.decide(i, True))
                reject.clicked.connect(lambda checked=False, i=index: self.decide(i, False))
                self.buttons.append((approve, reject, offset))
        self.reposition()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            QTimer.singleShot(0, self.reposition)
        if (watched is self.editor and event.type() == QEvent.Type.KeyPress
                and not self._applying):
            # Typing directly in the editor ends review before Qt processes
            # the keystroke, so the real prompt stays immediately editable.
            key = event.key()
            modifiers = event.modifiers()
            ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
            character = bool(event.text()) and not bool(modifiers & (
                Qt.KeyboardModifier.ControlModifier |
                Qt.KeyboardModifier.AltModifier |
                Qt.KeyboardModifier.MetaModifier
            ))
            edit_key = key in (
                Qt.Key.Key_Backspace, Qt.Key.Key_Delete, Qt.Key.Key_Return,
                Qt.Key.Key_Enter
            ) or (ctrl and key in (Qt.Key.Key_V, Qt.Key.Key_X))
            if character or edit_key:
                self.finish()
        return False

    def reposition(self, *_args):
        if not self.editor or self.preview_document is None:
            return
        editor = self.editor
        if self.deny_all:
            self.deny_all.move(max(2, editor.width() - self.deny_all.width() - 9), 2)
            self.deny_all.raise_()
        if self.approve_all:
            right = editor.width() - (self.deny_all.width() + 6 if self.deny_all else 0)
            self.approve_all.move(max(2, right - self.approve_all.width() - 9), 2)
            self.approve_all.raise_()
        viewport = editor.viewport()
        for approve, reject, offset in self.buttons:
            cursor = QTextCursor(self.preview_document)
            cursor.setPosition(min(offset, self.preview_document.characterCount() - 1))
            rect = editor.cursorRect(cursor)
            y = rect.top()
            visible = -1 <= y <= viewport.height() - 19
            for button, x in ((approve, viewport.width() - 43),
                              (reject, viewport.width() - 22)):
                if visible:
                    button.move(max(0, x), y)
                    button.show()
                    button.raise_()
                else:
                    button.hide()

    def finish(self):
        editor = self.editor
        if editor is None:
            self.parts = []
            return
        # Clear state first: restoring the document must not recursively
        # trigger _editor_changed / _refresh.
        self.editor = None
        editor.viewport().removeEventFilter(self)
        editor.removeEventFilter(self)
        try:
            editor.verticalScrollBar().valueChanged.disconnect(self.reposition)
            editor.horizontalScrollBar().valueChanged.disconnect(self.reposition)
            editor.textChanged.disconnect(self._editor_changed)
        except (RuntimeError, TypeError):
            pass
        for approve, reject, _ in self.buttons:
            approve.hide()
            reject.hide()
            approve.deleteLater()
            reject.deleteLater()
        self.buttons = []
        for control in (self.approve_all, self.deny_all):
            if control:
                control.hide()
                control.deleteLater()
        self.approve_all = None
        self.deny_all = None
        if self.source_document is not None:
            with QSignalBlocker(editor):
                if editor.document() is self.preview_document:
                    editor.setDocument(self.source_document)
                self.source_document.setParent(editor)
            self.source_document = None
        editor.setReadOnly(self._original_read_only)
        if self.preview_document is not None:
            self.preview_document.deleteLater()
            self.preview_document = None
        editor.setViewportMargins(0, 0, 0, 0)
        editor.viewport().update()
        self.removed_labels = []
        self.parts = []
