"""User-owned refinement presets, shared by the two prompt editors."""
from __future__ import annotations

import copy
import json

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                              QHBoxLayout, QLabel, QLineEdit, QListWidget,
                              QMessageBox, QPushButton, QTextEdit, QVBoxLayout)

TARGETS = {"segment": "Segment", "unified": "Unified", "both": "Both"}
SETTINGS_KEY = "quick_fix_presets"
STARTERS = [
    {"name": "Refine Camera", "target": "both",
     "prompt": "refine camera angle and shot descriptions."},
    {"name": "Format Prompt", "target": "unified",
     "prompt": "Format the spacing between shots to make it more human legable"},
]


def load_presets(settings) -> list[dict]:
    if not settings.contains(SETTINGS_KEY):
        save_presets(settings, STARTERS)
    try:
        entries = json.loads(settings.value(SETTINGS_KEY, "[]"))
    except (ValueError, TypeError):
        entries = []
    if not isinstance(entries, list):
        return []
    return [{"name": e["name"].strip(), "target": e["target"], "prompt": e["prompt"].strip()}
            for e in entries if isinstance(e, dict) and isinstance(e.get("target"), str) and e["target"] in TARGETS
            and isinstance(e.get("name"), str) and e["name"].strip()
            and isinstance(e.get("prompt"), str) and e["prompt"].strip()]


def save_presets(settings, entries: list[dict]) -> None:
    settings.setValue(SETTINGS_KEY, json.dumps(entries, ensure_ascii=False))


class QuickFixEditor(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.entries = copy.deepcopy(load_presets(settings))
        self.setWindowTitle("Quick Fixes")
        self.resize(560, 440)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Choose a preset to edit, or add your own refinement instruction."))
        self.presets = QListWidget()
        layout.addWidget(self.presets)
        controls = QHBoxLayout()
        self.add_button = QPushButton("Add")
        self.remove_button = QPushButton("Remove")
        controls.addWidget(self.add_button)
        controls.addWidget(self.remove_button)
        controls.addStretch()
        layout.addLayout(controls)
        form = QFormLayout()
        self.name = QLineEdit()
        self.target = QComboBox()
        for value, label in TARGETS.items():
            self.target.addItem(label, value)
        self.prompt = QTextEdit()
        self.prompt.setAcceptRichText(False)
        self.prompt.setPlaceholderText("Instruction to inject and refine automatically…")
        form.addRow("Name", self.name)
        form.addRow("Apply to", self.target)
        form.addRow("Refine prompt", self.prompt)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.presets.currentRowChanged.connect(self.select)
        self.name.textChanged.connect(self.update_entry)
        self.target.currentIndexChanged.connect(self.update_entry)
        self.prompt.textChanged.connect(self.update_entry)
        self.add_button.clicked.connect(self.add)
        self.remove_button.clicked.connect(self.remove)
        self.reload()

    def reload(self, row=0):
        self.presets.blockSignals(True)
        self.presets.clear()
        self.presets.addItems([e["name"] or "New Quick Fix" for e in self.entries])
        self.presets.blockSignals(False)
        self.presets.setCurrentRow(min(row, len(self.entries) - 1))
        self.select(self.presets.currentRow())

    def select(self, row):
        entry = self.entries[row] if 0 <= row < len(self.entries) else None
        for field in (self.name, self.target, self.prompt):
            field.blockSignals(True)
            field.setEnabled(entry is not None)
        self.name.setText(entry["name"] if entry else "")
        self.target.setCurrentIndex(self.target.findData(entry["target"] if entry else "both"))
        self.prompt.setPlainText(entry["prompt"] if entry else "")
        for field in (self.name, self.target, self.prompt):
            field.blockSignals(False)
        self.remove_button.setEnabled(entry is not None)

    def update_entry(self, *_):
        row = self.presets.currentRow()
        if 0 <= row < len(self.entries):
            self.entries[row] = {"name": self.name.text(), "target": self.target.currentData(),
                                 "prompt": self.prompt.toPlainText()}
            self.presets.item(row).setText(self.name.text() or "New Quick Fix")

    def add(self):
        self.entries.append({"name": "New Quick Fix", "target": "both", "prompt": ""})
        self.reload(len(self.entries) - 1)
        self.name.selectAll()
        self.name.setFocus()

    def remove(self):
        row = self.presets.currentRow()
        if 0 <= row < len(self.entries):
            self.entries.pop(row)
            self.reload(row)

    def save(self):
        for row, entry in enumerate(self.entries):
            if not entry["name"].strip() or not entry["prompt"].strip():
                self.presets.setCurrentRow(row)
                QMessageBox.information(self, "Quick Fixes", "Give each preset a name and a refine prompt.")
                return
        save_presets(self.settings, self.entries)
        self.accept()
