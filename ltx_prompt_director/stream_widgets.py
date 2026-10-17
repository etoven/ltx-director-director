"""Polished live AI summaries and progressive parsed-JSON generation previews."""
from __future__ import annotations

from importlib.resources import files

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
    QSizePolicy, QSplitter, QTextEdit, QVBoxLayout, QWidget,
)

from .stream_json import structured_stream_html

REASONING_HELP = (
    "Waiting for provider-published reasoning summaries…\n"
    "Some models only stream generated text. Internal reasoning is not exposed."
)
OUTPUT_HELP = "Waiting for the first structured field…"


class LiveOutputPane(QFrame):
    """Display provider-published summaries and decoded output, without applying it."""

    def __init__(self, parent=None, *, collapsible=True):
        super().__init__(parent)
        self.setObjectName("liveOutputPanel")
        self.setStyleSheet(
            "#liveOutputPanel{background:#111c2b;border:1px solid #385b88;border-radius:8px}"
            "QPlainTextEdit,QTextEdit{background:#0a1420;color:#e2ecfa;border:1px solid #263a52;"
            "border-radius:5px;padding:6px;font-family:monospace}"
            "QLabel{color:#cddbeb}"
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.received_reasoning = False
        self.raw_output = ""
        self._complete = False
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(65)
        self._render_timer.timeout.connect(self._render_output)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 9)
        layout.setSpacing(6)
        header = QHBoxLayout()
        self.title = QLabel("✦ Live AI Activity")
        self.title.setStyleSheet("font-weight:600")
        header.addWidget(self.title)
        self.status = QLabel("Ready")
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
            "Reasoning summary", REASONING_HELP, "ai-reasoning", reasoning=True,
        )
        self.output, output_section = self._make_column(
            "Generated prompt · parsed live", OUTPUT_HELP, "ai-prompt", reasoning=False,
        )
        self.splitter.addWidget(reasoning_section)
        self.splitter.addWidget(output_section)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        body.addWidget(self.splitter)
        layout.addWidget(self.content, 1)

    def _make_column(self, title: str, placeholder: str, symbol: str, *, reasoning: bool):
        column = QFrame()
        column.setFrameShape(QFrame.Shape.NoFrame)
        vertical = QVBoxLayout(column)
        vertical.setContentsMargins(0, 0, 0, 0)
        vertical.setSpacing(4)
        heading = QHBoxLayout()
        image = QLabel()
        asset = files("ltx_prompt_director").joinpath(f"assets/{symbol}.svg")
        image.setPixmap(QIcon(str(asset)).pixmap(17, 17))
        heading.addWidget(image)
        label = QLabel(title)
        label.setStyleSheet("font-weight:600;color:#9bc8ff")
        heading.addWidget(label)
        heading.addStretch()
        vertical.addLayout(heading)
        view = QPlainTextEdit() if reasoning else QTextEdit()
        view.setReadOnly(True)
        if not reasoning:
            view.setAcceptRichText(True)
        view.setPlaceholderText(placeholder)
        view.setMinimumHeight(80)
        vertical.addWidget(view, 1)
        return view, column

    def toggle_content(self):
        visible = not self.content.isVisible()
        self.content.setVisible(visible)
        self.toggle.setText("▴ Hide" if visible else "▾ Show")

    def begin(self, activity: str, model: str):
        self._render_timer.stop()
        self.raw_output = ""
        self._complete = False
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
            self._render_timer.stop()
            self.raw_output = ""
            self._complete = False
            self.reasoning.clear()
            self.output.clear()
            self.received_reasoning = False
            self.status.setText(text or "Retrying…")
        elif kind == "reasoning":
            self.received_reasoning = True
            self._append_reasoning(text)
        elif kind == "output":
            self.raw_output += text
            if not self._render_timer.isActive():
                self._render_timer.start()
        elif kind == "status":
            self.status.setText(text)

    def _append_reasoning(self, value: str):
        if not value:
            return
        scroll = self.reasoning.verticalScrollBar()
        previous = scroll.value()
        cursor = self.reasoning.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(value)
        if self.auto_scroll.isChecked():
            self.reasoning.setTextCursor(cursor)
            scroll.setValue(scroll.maximum())
        else:
            scroll.setValue(previous)

    def _render_output(self):
        scroll = self.output.verticalScrollBar()
        at = scroll.value()
        self.output.setHtml(structured_stream_html(self.raw_output, complete=self._complete))
        if self.auto_scroll.isChecked():
            scroll.setValue(scroll.maximum())
        else:
            scroll.setValue(at)

    def finish(self, success: bool):
        self._render_timer.stop()
        self._complete = success
        self._render_output()
        self.status.setText("Complete" if success else "Generation failed")
        if not self.received_reasoning:
            self.reasoning.setPlaceholderText("This model did not publish a reasoning summary.")
