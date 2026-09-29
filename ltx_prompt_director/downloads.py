"""Native popup export history with file-URL drag and drop."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QMimeData, QUrl, QSize
from PySide6.QtGui import QDesktopServices, QDrag, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                              QListWidget, QListWidgetItem, QAbstractItemView, QMenu)


class DownloadList(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setSpacing(4)
        self.setWordWrap(True)
        self.itemDoubleClicked.connect(lambda item: QDesktopServices.openUrl(QUrl.fromLocalFile(item.data(Qt.ItemDataRole.UserRole))))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.context_menu)

    def startDrag(self, supportedActions):
        urls = [QUrl.fromLocalFile(item.data(Qt.ItemDataRole.UserRole)) for item in self.selectedItems()
                if Path(item.data(Qt.ItemDataRole.UserRole)).is_file()]
        if not urls:
            return
        mime = QMimeData()
        mime.setUrls(urls)
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def context_menu(self, position):
        item = self.itemAt(position)
        if not item:
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        menu = QMenu(self)
        menu.addAction("Open", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
        menu.addAction("Open folder", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent))))
        menu.exec(self.mapToGlobal(position))


class DownloadTray(QFrame):
    def __init__(self, settings, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.settings = settings
        self.setObjectName("downloadTray")
        self.setFixedSize(430, 470)
        self.setStyleSheet("#downloadTray {background:#20272b; border:1px solid #52636d; border-radius:10px;} QListWidget {border:0; background:transparent;} QListWidget::item {padding:12px; border-radius:6px;} QListWidget::item:selected {background:#354954;}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        header = QHBoxLayout()
        header.addWidget(QLabel("Recent exports"), 1)
        close = QPushButton("×")
        close.setFixedSize(28, 28)
        close.clicked.connect(self.hide)
        header.addWidget(close)
        outer.addLayout(header)
        self.list = DownloadList(self)
        self.list.setIconSize(QSize(30, 30))
        self.list.setStyleSheet("QListWidget {border:0; background:transparent; color:#e7edf0; font-size:12px;} QListWidget::item {border:0; padding:12px 8px; border-radius:6px; background:transparent;} QListWidget::item:selected {background:#354954; border:1px solid #5b8a9b;} QListWidget::item:hover {background:#2b383f;}")
        outer.addWidget(self.list, 1)
        self.empty = QLabel("Your exports will appear here.\nDrag a file into another application, or double-click to open it.")
        self.empty.setWordWrap(True)
        outer.addWidget(self.empty)
        self.folder_button = QPushButton("Open download folder ↗")
        outer.addWidget(self.folder_button)
        try:
            values = json.loads(settings.value("download_history", "[]"))
            self.history = [value for value in values if isinstance(value, dict) and isinstance(value.get("path"), str)][:100]
        except (TypeError, ValueError):
            self.history = []
        self.refresh()

    def add(self, path: Path):
        path = path.resolve()
        self.history = [{"path": str(path), "created": datetime.now(timezone.utc).isoformat()}] + [item for item in self.history if item["path"] != str(path)]
        self.history = self.history[:100]
        self.settings.setValue("download_history", json.dumps(self.history))
        self.refresh()

    def refresh(self):
        self.list.clear()
        for value in self.history:
            path = Path(value["path"])
            exists = path.is_file()
            size = path.stat().st_size if exists else 0
            detail = f"{size / 1024:.0f} KB" if exists else "File moved or removed"
            created = value.get("created", "")[:16].replace("T", " · ")
            suffix = path.suffix.lower()
            label = "IMG" if suffix in {".png", ".jpg", ".jpeg", ".webp"} else "VID" if suffix in {".mp4", ".webm", ".mov"} else "{}" if suffix == ".json" else "PRJ" if suffix == ".ltxd" else "FILE"
            color = "#50b992" if label == "IMG" else "#bd90dc" if label == "VID" else "#6dbada"
            pixmap = QPixmap(30, 30)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(1, 1, 28, 28, 5, 5)
            painter.setPen(QColor("#10252c"))
            painter.setFont(QFont("Sans Serif", 7, QFont.Weight.Bold))
            painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, label)
            painter.end()
            item = QListWidgetItem(QIcon(pixmap), f"{path.name}\n{detail} · {created}")
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            if not exists:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(item)
        self.empty.setVisible(not self.history)
        if self.list.count():
            self.list.setCurrentRow(0)
