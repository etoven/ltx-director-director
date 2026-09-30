"""Two portable, untimed reference-image drop targets for the MiniMax editor."""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from PySide6.QtCore import QBuffer, QEvent, QIODevice, QMimeData, QStandardPaths, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QDrag, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QVBoxLayout

from .minimax_reference import REFERENCE_ROLES


class MiniMaxReferenceSlot(QFrame):
    changed = Signal(object)
    error = Signal(str)
    export_requested = Signal()

    def __init__(self, number: int):
        super().__init__()
        self.number = number
        self.value = None
        self.setAcceptDrops(True)
        self.setObjectName("referenceImageSlot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("dropActive", False)
        self._drag_start = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 6, 9, 6)
        row = QHBoxLayout()
        self.title = QLabel(f"REFERENCE {number} · untimed")
        self.title.setObjectName("sectionLabel")
        row.addWidget(self.title, 1)
        for text, callback in (("Browse", self.browse), ("Paste", self.paste)):
            button = QPushButton(text)
            button.setObjectName("copyButton")
            button.clicked.connect(callback)
            row.addWidget(button)
        self.export_action = QAction("Export Image", self)
        self.export_action.setEnabled(False)
        self.export_action.triggered.connect(lambda: self.export_requested.emit())
        self.clear_action = QAction("Clear", self)
        self.clear_action.setEnabled(False)
        self.clear_action.triggered.connect(self.clear)
        layout.addLayout(row)
        body = QHBoxLayout()
        self.preview = QLabel("Drop image here")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedSize(116, 72)
        self.preview.setObjectName("referenceImagePreview")
        self.preview.installEventFilter(self)
        self.preview.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.preview.customContextMenuRequested.connect(lambda point: self.show_image_menu(self.preview.mapToGlobal(point)))
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
        self.export_action.setEnabled(bool(value and value.get("image")))
        self.clear_action.setEnabled(bool(value))
        self.preview.setCursor(Qt.CursorShape.OpenHandCursor if value else Qt.CursorShape.ArrowCursor)
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
        if self.accepts_mime(event.mimeData()):
            self.set_drop_active(True)
            event.acceptProposedAction()
        else:
            self.set_drop_active(False)
            event.ignore()

    @staticmethod
    def accepts_mime(mime) -> bool:
        return mime.hasImage() or any(url.isLocalFile() and Path(url.toLocalFile()).is_file()
            and Path(url.toLocalFile()).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
            for url in mime.urls())

    def dragMoveEvent(self, event) -> None:
        self.dragEnterEvent(event)

    def dragLeaveEvent(self, event) -> None:
        self.set_drop_active(False)
        super().dragLeaveEvent(event)

    def set_drop_active(self, active) -> None:
        self.setProperty("dropActive", bool(active))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def show_image_menu(self, point) -> None:
        menu = QMenu(self)
        menu.addAction(self.export_action)
        menu.addAction(self.clear_action)
        menu.exec(point)

    def contextMenuEvent(self, event) -> None:
        self.show_image_menu(event.globalPos())
        event.accept()

    def drag_mime(self):
        if not self.value or not self.value.get("image"):
            return None
        data = base64.b64decode(self.value["image"].split(",", 1)[1])
        # Keep URL targets alive after the drag returns: external file managers
        # may read them asynchronously. The content hash avoids stale copies.
        root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)) / "reference-drags"
        root = root / hashlib.sha256(data).hexdigest()
        root.mkdir(parents=True, exist_ok=True)
        name = Path(self.value.get("name") or "Reference.png").stem
        mime_type = self.value["image"].split(";", 1)[0].removeprefix("data:").lower()
        suffix = {"image/jpeg": ".jpg", "image/webp": ".webp", "image/bmp": ".bmp",
                  "image/tiff": ".tiff"}.get(mime_type, ".png")
        path = root / (name + suffix)
        if not path.exists():
            path.write_bytes(data)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        mime.setImageData(QImage.fromData(data))
        return mime

    def eventFilter(self, watched, event):
        if watched is self.preview:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_start = event.position().toPoint()
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self._drag_start = None
            elif event.type() == QEvent.Type.MouseMove and self._drag_start is not None and event.buttons() & Qt.MouseButton.LeftButton:
                if (event.position().toPoint() - self._drag_start).manhattanLength() >= QApplication.startDragDistance():
                    self._drag_start = None
                    mime = self.drag_mime()
                    if mime:
                        drag = QDrag(self.preview)
                        drag.setMimeData(mime)
                        if self.preview.pixmap() and not self.preview.pixmap().isNull():
                            drag.setPixmap(self.preview.pixmap())
                        drag.exec(Qt.DropAction.CopyAction)
                        return True
        return super().eventFilter(watched, event)

    def dropEvent(self, event) -> None:
        self.set_drop_active(False)
        if self.load_mime(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()
