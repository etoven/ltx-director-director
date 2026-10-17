"""Non-destructive in-editor AI typing preview.

The authoritative saved draft is never changed while the provider is streaming.
A temporary QTextDocument shows line-level inline red insertions and muted
strikethrough replacements. Before any user edit, restore the actual draft and
suspend the preview so manual changes always win. The caller must call finish()
before running the normal completion validator or rollback handling.
"""
from __future__ import annotations

from difflib import SequenceMatcher

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, QTimer
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor, QTextDocument

from .stream_json import live_fields


def select_streamed_text(raw: str, target: str = "unified", index: int = 0) -> str:
    fields = live_fields(raw)
    if not fields:
        return ""
    label = "Global direction" if target == "global" else "Production prompt"
    if target == "image":
        label = "Image prompt"
    matched = [value for name, value, _done in fields if name == label]
    if not matched and target in {"segment", "unified"}:
        matched = [value for name, value, _done in fields if name in {"Segment prompt", "Generated text"}]
    if not matched and target == "unified":
        matched = [value for name, value, _done in fields if name == "Global direction"]
    if not matched:
        return ""
    position = min(max(0, index), len(matched) - 1) if target == "segment" else 0
    return matched[position]


def line_diff(base: str, candidate: str) -> list[tuple[str, str]]:
    """Yield equal/removed/added spans, including unchanged anchors."""
    previous = base.splitlines(keepends=True)
    proposed = candidate.splitlines(keepends=True)
    spans = []
    for tag, i, j, m, n in SequenceMatcher(
        None, previous, proposed, autojunk=False
    ).get_opcodes():
        if tag in ("equal", "delete", "replace"):
            text = "".join(previous[i:j])
            if text:
                spans.append(("equal" if tag == "equal" else "removed", text))
        if tag in ("insert", "replace"):
            text = "".join(proposed[m:n])
            if text:
                spans.append(("added", text))
    return spans


class LiveTypingPreview(QObject):
    """An editor-local projection of streamed text, not a background mutation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.editor = None
        self.baseline = ""
        self.target = "unified"
        self.index = 0
        self.raw = ""
        self.active = False
        self.suspended = False
        self.showing = False
        self._busy_render = False
        self.timer = QTimer(self)
        self.timer.setInterval(80)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.render)
        self.last_candidate = ""
        self.rendered_candidate = ""
        self.original_document = None
        self.preview_document = None

    def start(self, editor, original: str, target: str = "unified", index: int = 0):
        self.finish()
        self.editor = editor
        self.baseline = original
        self.target = target
        self.index = index
        self.raw = ""
        self.active = True
        self.suspended = False
        self.showing = False
        self.last_candidate = ""
        self.rendered_candidate = ""
        editor.installEventFilter(self)
        editor.viewport().installEventFilter(self)

    def eventFilter(self, source, event):
        if self.active and not self._busy_render:
            if event.type() in (
                QEvent.Type.KeyPress, QEvent.Type.InputMethod,
                QEvent.Type.MouseButtonPress, QEvent.Type.Drop,
                QEvent.Type.ContextMenu,
            ):
                # Restore BEFORE Qt processes the user input. This way the user
                # edits their actual draft rather than an ephemeral AI diff.
                self.suspend()
        return super().eventFilter(source, event)

    def receive(self, kind: str, value: str):
        if not self.active or self.suspended:
            return
        if kind == "reset":
            self.raw = ""
            self.last_candidate = ""
            self.rendered_candidate = ""
            self.restore_baseline()
            return
        if kind != "output":
            return
        self.raw += value
        candidate = select_streamed_text(self.raw, self.target, self.index)
        if candidate and candidate != self.last_candidate:
            self.last_candidate = candidate
            if not self.timer.isActive():
                self.timer.start()

    def render(self):
        if not self.active or self.suspended or self.editor is None:
            return
        available = self.last_candidate
        if not available or available == self.rendered_candidate:
            return
        # Animate newly arrived text in measured character batches instead of
        # instantly pasting a large provider SSE chunk. Catch up adaptively.
        if not available.startswith(self.rendered_candidate):
            self.rendered_candidate = ""
        remaining = len(available) - len(self.rendered_candidate)
        step = max(24, min(160, remaining // 7))
        candidate = available[:len(self.rendered_candidate) + step]
        self.rendered_candidate = candidate
        text = self.editor
        pos = text.verticalScrollBar().value()
        self._busy_render = True
        if not self.showing:
            self.original_document = text.document()
            # Ownership must leave the editor before swapping documents; Qt
            # otherwise destroys its old child document and the undo history.
            self.original_document.setParent(self)
            self.preview_document = QTextDocument(self)
            with QSignalBlocker(text):
                text.setDocument(self.preview_document)
            self.showing = True
        # Block textChanged; otherwise the preview would be committed as an
        # actual user edit into the project/cache/timeline.
        try:
            with QSignalBlocker(text):
                document = text.document()
                document.clear()
                cursor = QTextCursor(document)
                keep = QTextCharFormat()
                keep.setForeground(QColor("#dbe7f5"))
                incoming = QTextCharFormat()
                incoming.setForeground(QColor("#ff696e"))
                incoming.setBackground(QColor("#3b1d29"))
                old = QTextCharFormat()
                old.setForeground(QColor("#ad8191"))
                old.setFontStrikeOut(True)
                for state, passage in line_diff(self.baseline, candidate):
                    cursor.insertText(passage, {"equal": keep, "added": incoming, "removed": old}[state])
                text.verticalScrollBar().setValue(pos)
        finally:
            self._busy_render = False
        if self.active and not self.suspended and self.rendered_candidate != self.last_candidate:
            self.timer.start()

    def restore_baseline(self):
        self.timer.stop()
        if self.editor is not None and self.showing:
            self._busy_render = True
            try:
                with QSignalBlocker(self.editor):
                    self.editor.setDocument(self.original_document)
                    self.original_document.setParent(self.editor)
            finally:
                self._busy_render = False
            if self.preview_document is not None:
                self.preview_document.deleteLater()
            self.preview_document = None
            self.original_document = None
        self.showing = False

    def suspend(self):
        if not self.active:
            return
        self.restore_baseline()
        self.suspended = True

    def finish(self):
        if self.editor is None:
            self.active = False
            return
        self.restore_baseline()
        self.editor.removeEventFilter(self)
        self.editor.viewport().removeEventFilter(self)
        self.active = False
        self.suspended = False
        self.editor = None
