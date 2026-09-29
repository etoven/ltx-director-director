"""Settings page for portable workspace definitions."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QFormLayout, QListWidget,
                              QLineEdit, QComboBox, QCheckBox, QTextEdit, QPushButton,
                              QFileDialog, QMessageBox, QLabel, QSpinBox)

from .workspaces import WorkspaceStore, KINDS, validate_definition
from .spellcheck import install_spellcheck


class WorkspaceEditor(QWidget):
    def __init__(self, store: WorkspaceStore, owner, parent=None):
        super().__init__(parent)
        self.store, self.owner = store, owner
        self.current_id = None
        outer = QVBoxLayout(self)
        outer.addWidget(QLabel("Workspace definitions control prompt layout, references, audio and AI instructions."))
        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.setMaximumWidth(220)
        self.list.currentRowChanged.connect(self.select)
        body.addWidget(self.list)
        form = QFormLayout()
        self.identifier, self.name = QLineEdit(), QLineEdit()
        self.engine = QComboBox()
        self.engine.addItem("LTX", "ltx")
        self.engine.addItem("MiniMax Frames", "minimax_frames")
        self.engine.addItem("MiniMax References", "minimax_references")
        self.mode = QComboBox()
        self.mode.addItem("One unified prompt", "unified")
        self.mode.addItem("Per-segment prompts", "segmented")
        self.global_prompt, self.audio = QCheckBox("Include global prompt"), QCheckBox("Supports audio generation")
        self.references = QCheckBox("Allow reference media")
        self.kinds = QLineEdit()
        self.kinds.setToolTip("Comma-separated types: " + ", ".join(sorted(KINDS)))
        self.slots = QSpinBox()
        self.slots.setRange(0, 2)
        self.variables = QLabel()
        self.variables.setWordWrap(True)
        form.addRow("Template fields", self.variables)
        self.generate, self.refine = QTextEdit(), QTextEdit()
        install_spellcheck(self.generate)
        install_spellcheck(self.refine)
        self.generate.setAcceptRichText(False)
        self.refine.setAcceptRichText(False)
        for title, control in (("ID", self.identifier), ("Name", self.name), ("Generation engine", self.engine),
                               ("Prompt layout", self.mode), ("", self.global_prompt), ("", self.audio),
                               ("", self.references), ("Reference kinds", self.kinds), ("Untimed image slots", self.slots),
                               ("Generation instructions", self.generate), ("Refinement instructions", self.refine)):
            form.addRow(title, control)
        body.addLayout(form, 1)
        outer.addLayout(body, 1)
        buttons = QHBoxLayout()
        for title, action in (("New", self.new), ("Save / rename", self.save), ("Delete", self.delete),
                              ("Import…", self.import_definition), ("Export", self.export), ("Restore stock", self.restore)):
            button = QPushButton(title)
            button.clicked.connect(action)
            buttons.addWidget(button)
        outer.addLayout(buttons)
        self.status = QLabel()
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.reload()

    def reload(self, selected=None):
        self.values = self.store.load()
        self.list.blockSignals(True)
        self.list.clear()
        for key, value in self.values.items():
            self.list.addItem(value["name"])
            self.list.item(self.list.count() - 1).setData(Qt.ItemDataRole.UserRole, key)
        row = next((i for i in range(self.list.count()) if self.list.item(i).data(Qt.ItemDataRole.UserRole) == selected), 0)
        self.list.setCurrentRow(row)
        self.list.blockSignals(False)
        self.select(row)
        self.status.setText("\n".join(self.store.errors))

    def select(self, row):
        if row < 0 or row >= self.list.count():
            self.current_id = None
            return
        self.current_id = self.list.item(row).data(Qt.ItemDataRole.UserRole)
        self.populate(self.values[self.current_id])

    def populate(self, value):
        self.identifier.setText(value["id"])
        self.name.setText(value["name"])
        self.engine.setCurrentIndex(self.engine.findData(value["engine"]))
        self.mode.setCurrentIndex(self.mode.findData(value["prompt_mode"]))
        self.global_prompt.setChecked(value["global_prompt"])
        self.audio.setChecked(value["audio_generation"])
        self.references.setChecked(value["references"]["enabled"])
        self.kinds.setText(", ".join(value["references"]["kinds"]))
        self.slots.setValue(value["references"].get("untimed_slots", 0))
        fields = value.get("template_variables", {})
        names = sorted(set(fields.get("generate", []) + fields.get("refine", [])))
        self.variables.setText(", ".join("${" + name + "}" for name in names) or "Plain instructions or supported ${field} substitutions")
        self.generate.setPlainText(value["generation_prompts"]["generate"])
        self.refine.setPlainText(value["generation_prompts"].get("refine", ""))

    def value(self):
        existing = self.values.get(self.current_id, {})
        return validate_definition(dict(**{key: value for key, value in existing.items() if key not in {"schema_version", "id", "name", "engine", "prompt_mode", "global_prompt", "audio_generation", "references", "generation_prompts"}}, schema_version=1, id=self.identifier.text().strip(), name=self.name.text().strip(),
            engine=self.engine.currentData(), prompt_mode=self.mode.currentData(), global_prompt=self.global_prompt.isChecked(),
            audio_generation=self.audio.isChecked(), references=dict(enabled=self.references.isChecked(),
            kinds=[kind.strip() for kind in self.kinds.text().split(",") if kind.strip()], untimed_slots=self.slots.value()),
            generation_prompts=dict(generate=self.generate.toPlainText(), refine=self.refine.toPlainText())))

    def new(self):
        value = copy.deepcopy(next(iter(self.values.values()))) if self.values else dict(schema_version=1, engine="ltx", prompt_mode="segmented", global_prompt=True, audio_generation=True, references=dict(enabled=True, kinds=["image", "video"], untimed_slots=0), generation_prompts=dict(generate="Describe the supplied timeline.", refine="Apply the requested changes."))
        value.update(id="workspace_" + uuid4().hex[:8], name="New workspace")
        self.current_id = None
        self.populate(value)
        self.name.setFocus()
        self.name.selectAll()

    def save(self):
        try:
            value = self.value()
            if self.current_id is None and value["id"] in self.values:
                raise ValueError("This ID already exists. Select it to edit or choose a new ID.")
            self.store.save(value, self.current_id)
            self.owner.reload_workspace_definitions(previous_id=self.current_id, new_id=value["id"])
            self.reload(value["id"])
            self.status.setText("Workspace saved.")
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Workspace", str(error))

    def delete(self):
        if self.current_id:
            self.store.delete(self.current_id)
            self.owner.reload_workspace_definitions()
            self.reload()

    def restore(self):
        self.store.restore_stock()
        self.owner.reload_workspace_definitions()
        self.reload()
        self.status.setText("Stock workspaces restored. Other custom definitions were preserved.")

    def import_definition(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import workspace definition", "", "Workspace JSON (*.json)")
        if path:
            try:
                value = validate_definition(json.loads(Path(path).read_text(encoding="utf-8")))
                self.store.save(value)
                self.owner.reload_workspace_definitions()
                self.reload(value["id"])
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, "Import workspace", str(error))

    def export(self):
        try:
            value = self.value()
            path = self.owner.next_export_path(value["name"] + ".workspace.json")
            path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            self.owner.record_export(path)
            self.status.setText(f"Exported {path.name}")
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Export workspace", str(error))
