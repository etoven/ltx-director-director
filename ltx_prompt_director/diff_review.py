"""Compact, non-destructive per-paragraph AI diff review in a QTextEdit.

Generated draft text stays in the real editor. Green highlights represent
pending/approved additions; red highlights represent explicitly rejected edits.
The buttons float INSIDE the editor viewport, so there is no extra toolbar row.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
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


def _utf16_len(text: str) -> int:
    """Qt QTextCursor indexes UTF-16 code units, including non-BMP characters."""
    return len(text.encode("utf-16-le")) // 2


class ParagraphDiffReview(QObject):
    """Tiny inline approval controls for completed AI edits.

    No actions occur during streaming, and no QTextDocument replacement is
    required: undo history and the actual authoritative project text remain
    managed by the existing editor.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.editor = None
        self.parts: list[ParagraphChange] = []
        self.buttons = []
        self.approve_all = None
        self._applying = False

    @property
    def pending_count(self) -> int:
        return sum(part.decision == "pending" for part in self.parts)

    def begin(self, editor: QTextEdit, before: str, after: str) -> bool:
        self.finish()
        if not before.strip() or before == after or editor.toPlainText() != after:
            return False
        parts = paragraph_changes(before, after)
        if not any(p.decision == "pending" for p in parts):
            return False
        self.editor = editor
        self.parts = parts
        # A 21px internal strip provides the 'Approve all' affordance without
        # inserting any separate toolbar or changing the outer editor layout.
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
        self.approve_all.toggled.connect(self._approve_all_toggled)
        self.approve_all.show()
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
            # A manual edit wins; don't apply stale paragraph offsets.
            self.finish()

    def _approve_all_toggled(self, checked):
        if checked:
            for part in self.parts:
                if part.decision == "pending":
                    part.decision = "approved"
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
        if self.editor is None:
            return
        new_value = reviewed_text(self.parts)
        if self.editor.toPlainText() == new_value:
            return
        scrollbar = self.editor.verticalScrollBar()
        scroll = scrollbar.value()
        self._applying = True
        try:
            # Emits normal editor change signals so the existing project,
            # cache, dirty flag and timeline are kept in sync.
            self.editor.setPlainText(new_value)
        finally:
            self._applying = False
        scrollbar.setValue(min(scroll, scrollbar.maximum()))

    def _refresh(self):
        editor = self.editor
        if editor is None:
            return
        for accept, reject, _pos in self.buttons:
            accept.deleteLater()
            reject.deleteLater()
        self.buttons = []
        selections = []
        offset = 0
        for index, part in enumerate(self.parts):
            text = part.old if part.decision == "rejected" else part.new
            first = offset
            offset += _utf16_len(text)
            if part.decision == "equal":
                continue
            cursor = QTextCursor(editor.document())
            cursor.setPosition(min(first, editor.document().characterCount() - 1))
            if text:
                cursor.setPosition(min(offset, editor.document().characterCount() - 1),
                                   QTextCursor.MoveMode.KeepAnchor)
                highlight = QTextEdit.ExtraSelection()
                highlight.cursor = cursor
                highlight.format = QTextCharFormat()
                highlight.format.setBackground(QColor(
                    "#47272d" if part.decision == "rejected" else
                    "#193b2b" if part.decision == "approved" else "#1b4530"
                ))
                selections.append(highlight)
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
                control.setFixedSize(19, 19)
                control.setStyleSheet(
                    "QToolButton#diffReviewButton{background:" + color +
                    ";border:0;border-radius:4px;color:white;"
                    "font-size:12px;font-weight:bold;padding:0;min-height:0}"
                    "QToolButton#diffReviewButton:hover{background:#397fa3}"
                )
            approve.clicked.connect(lambda checked=False, i=index: self.decide(i, True))
            reject.clicked.connect(lambda checked=False, i=index: self.decide(i, False))
            self.buttons.append((approve, reject, first))
        editor.setExtraSelections(selections)
        self.reposition()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            QTimer.singleShot(0, self.reposition)
        return False

    def reposition(self, *_args):
        if not self.editor:
            return
        editor = self.editor
        if self.approve_all:
            self.approve_all.move(max(2, editor.width() - self.approve_all.width() - 9), 2)
            self.approve_all.raise_()
        viewport = editor.viewport()
        for approve, reject, offset in self.buttons:
            cursor = QTextCursor(editor.document())
            cursor.setPosition(min(offset, editor.document().characterCount() - 1))
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
            reject.deleteLater()
            approve.deleteLater()
        self.buttons = []
        if self.approve_all:
            self.approve_all.hide()
            self.approve_all.deleteLater()
            self.approve_all = None
        editor.setExtraSelections([])
        editor.setViewportMargins(0, 0, 0, 0)
        editor.viewport().update()
        self.parts = []
