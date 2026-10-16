"""Live AI activity panels for both overlay and inline prompt workspaces."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

REASONING_HELP = (
    "Waiting for provider-published reasoning summaries…\n"
    "Some models only stream their generated output. Internal reasoning is not exposed."
)
OUTPUT_HELP = "Waiting for the first generated text…"


class LiveOutputPane(QFrame):
    """Streaming display only; never writes to the production prompt editor."""

    def __init__(self, parent=None, *, collapsible=True):
        super().__init__(parent)
        self.setObjectName("liveOutputPanel")
        self.setStyleSheet(
            "#liveOutputPanel{background:#111c2b;border:1px solid #385b88;border-radius:8px}"
            "QPlainTextEdit{background:#0a1420;color:#e2ecfa;border:1px solid #263a52;"
            "border-radius:5px;padding:6px;font-family:monospace}"
            "QLabel{color:#cddbeb}"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.received_reasoning = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 9)
        layout.setSpacing(6)
        header = QHBoxLayout()
        self.title = QLabel("✦ Live AI Activity")
        self.title.setStyleSheet("font-weight:600")
        header.addWidget(self.title)
        self.status = QLabel("Ready")
        self.status.setObjectName("muted")
        header.addWidget(self.status)
        header.addStretch()
        self.auto_scroll = QCheckBox("Auto-scroll")
        self.auto_scroll.setChecked(True)
        header.addWidget(self.auto_scroll)
        self.toggle = QPushButton("▴ Hide" if collapsible else "")
        self.toggle.setFixedWidth(70)
        self.toggle.setVisible(collapsible)
        self.toggle.clicked.connect(self.toggle_content)
        header.addWidget(self.toggle)
        layout.addLayout(header)
        self.content = QWidget()
        body = QHBoxLayout(self.content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(4)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.reasoning, reasoning_section = self._make_column(
            "Reasoning summary · when provided", REASONING_HELP
        )
        self.output, output_section = self._make_column(
            "Generated prompt · streaming", OUTPUT_HELP
        )
        self.splitter.addWidget(reasoning_section)
        self.splitter.addWidget(output_section)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        body.addWidget(self.splitter)
        layout.addWidget(self.content, 1)

    def _make_column(self, title: str, placeholder: str):
        column = QFrame()
        column.setFrameShape(QFrame.Shape.NoFrame)
        vertical = QVBoxLayout(column)
        vertical.setContentsMargins(0, 0, 0, 0)
        vertical.setSpacing(4)
        label = QLabel(title)
        label.setStyleSheet("font-weight:600;color:#9bc8ff")
        vertical.addWidget(label)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlaceholderText(placeholder)
        view.setMinimumHeight(80)
        vertical.addWidget(view, 1)
        return view, column

    def toggle_content(self):
        visible = not self.content.isVisible()
        self.content.setVisible(visible)
        self.toggle.setText("▴ Hide" if visible else "▾ Show")

    def begin(self, activity: str, model: str):
        self.received_reasoning = False
        self.reasoning.clear()
        self.output.clear()
        self.reasoning.setPlaceholderText(REASONING_HELP)
        self.output.setPlaceholderText(OUTPUT_HELP)
        self.status.setText(f"{model} · {activity}")
        if not self.content.isVisible():
            self.content.show()
            self.toggle.setText("▴ Hide")
        self.show()

    def receive_stream(self, kind: str, text: str):
        if kind == "reset":
            self.reasoning.clear()
            self.output.clear()
            self.received_reasoning = False
            self.status.setText(text or "Retrying…")
        elif kind == "reasoning":
            self.received_reasoning = True
            self._append(self.reasoning, text)
        elif kind == "output":
            self._append(self.output, text)
        elif kind == "status":
            self.status.setText(text)

    def _append(self, editor: QPlainTextEdit, value: str):
        if not value:
            return
        scroll = editor.verticalScrollBar()
        previous = scroll.value()
        cursor = editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(value)
        if self.auto_scroll.isChecked():
            editor.setTextCursor(cursor)
            scroll.setValue(scroll.maximum())
        else:
            scroll.setValue(previous)

    def finish(self, success: bool):
        self.status.setText("Complete" if success else "Generation failed")
        if not self.received_reasoning:
            self.reasoning.setPlaceholderText(
                "This model did not publish a reasoning summary."
            )
