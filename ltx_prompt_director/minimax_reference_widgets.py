"""Two portable, untimed reference-image drop targets for the MiniMax editor."""
from __future__ import annotations

import base64
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from .minimax_reference import REFERENCE_ROLES


class MiniMaxReferenceSlot(QFrame):
    changed = Signal(object)
    error = Signal(str)

    def __init__(self, number: int):
        super().__init__()
        self.number = number
        self.value = None
        self.setAcceptDrops(True)
        self.setObjectName("promptPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 6, 9, 6)
        row = QHBoxLayout()
        self.title = QLabel(f"REFERENCE {number} · untimed")
        self.title.setObjectName("sectionLabel")
        row.addWidget(self.title, 1)
        for text, callback in (("Browse", self.browse), ("Paste", self.paste), ("Clear", self.clear)):
            button = QPushButton(text)
            button.setObjectName("copyButton")
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        body = QHBoxLayout()
        self.preview = QLabel("Drop image here")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedSize(116, 72)
        body.addWidget(self.preview)
        fields = QVBoxLayout()
        self.filename = QLabel("No image")
        self.filename.setTextFormat(Qt.TextFormat.PlainText)
        self.filename.setMaximumWidth(300)
        fields.addWidget(self.filename)
        self.role = QComboBox()
        for key, label in REFERENCE_ROLES.items():
            self.role.addItem(label, key)
        self.role.setToolTip("What this reference image should control")
        self.role.currentIndexChanged.connect(self.metadata_changed)
        fields.addWidget(self.role)
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Optional: subject or reference instructions")
        self.notes.textChanged.connect(self.metadata_changed)
        fields.addWidget(self.notes)
        body.addLayout(fields, 1)
        layout.addLayout(body)
        self.setToolTip("Reference appearance, setting or style. This image adds no timeline frame or duration.")

    def set_value(self, value: dict | None, label: str | None = None) -> None:
        self.title.setText(f"REFERENCE {self.number}" + (f" · {label}" if label else " · untimed"))
        if value == self.value:
            return
        self.value = dict(value) if value else None
        self.role.blockSignals(True)
        self.notes.blockSignals(True)
        self.role.setCurrentIndex(max(0, self.role.findData(value.get("role", "identity") if value else "identity")))
        self.notes.setText(value.get("notes", "") if value else "")
        self.role.blockSignals(False)
        self.notes.blockSignals(False)
        self.filename.setText(value["name"] if value else "No image")
        self.filename.setToolTip(value["name"] if value else "")
        self.preview.clear()
        pixmap = QPixmap()
        if value:
            try:
                pixmap.loadFromData(base64.b64decode(value["image"].split(",", 1)[1]))
            except (ValueError, IndexError):
                pass
        if pixmap.isNull():
            self.preview.setText("Image unavailable" if value else "Drop image here")
        else:
            self.preview.setPixmap(pixmap.scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def metadata_changed(self, *_args) -> None:
        if self.value:
            self.value = {**self.value, "role": self.role.currentData(), "notes": self.notes.text()}
            self.changed.emit(dict(self.value))

    def load_image(self, image: QImage, name: str = "Pasted reference.png") -> bool:
        if image.isNull():
            self.error.emit("That file could not be read as an image.")
            return False
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            self.error.emit("Could not store the reference image.")
            return False
        value = {"name": name, "image": "data:image/png;base64," + base64.b64encode(bytes(buffer.data())).decode(),
                 "role": self.role.currentData(), "notes": self.notes.text()}
        self.set_value(value)
        self.changed.emit(value)
        return True

    def load_path(self, path: str) -> bool:
        from PySide6.QtGui import QImageReader
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        if reader.supportsAnimation():
            self.error.emit("Use a still image for this reference slot.")
            return False
        return self.load_image(reader.read(), Path(path).name)

    def browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose reference image", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)")
        if path:
            self.load_path(path)

    def paste(self) -> None:
        if not self.load_mime(QApplication.clipboard().mimeData()):
            self.error.emit("Copy an image or a local image file, then paste it here.")

    def clear(self) -> None:
        self.set_value(None)
        self.changed.emit(None)

    def load_mime(self, mime) -> bool:
        if mime.hasUrls():
            paths = [url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
            if paths:
                return self.load_path(paths[0])
        if mime.hasImage():
            image = mime.imageData()
            if isinstance(image, QPixmap):
                image = image.toImage()
            return self.load_image(image) if isinstance(image, QImage) else False
        return False

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasImage() or any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        if self.load_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()
