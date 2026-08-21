from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, QRunnable, QSettings, QSize, QStandardPaths, Qt, QThreadPool, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QDockWidget, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton,
    QSizePolicy, QSlider, QStatusBar, QTextEdit, QToolBar, QVBoxLayout, QWidget,
)

from .ai import GEMINI_MODELS, build_prompts
from .media import APP_CACHE, data_url, prepare_media, write_data_url
from .models import Segment

FPS = 24
MAX_SECONDS = 60.0
MAX_SEGMENTS = 16


def project_library_path() -> Path:
    root = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    path = Path(root) / "projects"
    path.mkdir(parents=True, exist_ok=True)
    return path


def pixmap_from_data_url(value: str) -> QPixmap:
    pixmap = QPixmap()
    try:
        pixmap.loadFromData(base64.b64decode(value.split(",", 1)[1]))
    except (IndexError, ValueError):
        pass
    return pixmap


def choose_media_files(parent: QWidget, multiple: bool, initial: str) -> list[str]:
    """Prefer the desktop's actual file picker, then fall back to QFileDialog."""
    media_filter = "Supported Media (*.png *.jpg *.jpeg *.webp *.gif *.webm)"
    if sys.platform.startswith("linux") and shutil.which("kdialog"):
        command = ["kdialog", "--title", "Choose images or WebM files"]
        if multiple:
            command.extend(["--multiple", "--separate-output"])
        command.extend(["--getopenfilename", initial or ":ltxPromptDirectorMedia", media_filter])
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return []
    if sys.platform.startswith("linux") and shutil.which("zenity"):
        command = ["zenity", "--file-selection", "--title=Choose images or WebM files", f"--filename={initial.rstrip('/')}/", "--file-filter=Supported media | *.png *.jpg *.jpeg *.webp *.gif *.webm"]
        if multiple:
            command.extend(["--multiple", "--separator=\n"])
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return []
    if multiple:
        files, _ = QFileDialog.getOpenFileNames(parent, "Add images or WebM", initial, media_filter)
        return files
    file, _ = QFileDialog.getOpenFileName(parent, "Replace media", initial, media_filter)
    return [file] if file else []


def choose_document_open(parent: QWidget, title: str, initial: str, name_filter: str) -> str:
    if sys.platform.startswith("linux") and shutil.which("kdialog"):
        result = subprocess.run(["kdialog", "--title", title, "--getopenfilename", initial, name_filter], capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    if sys.platform.startswith("linux") and shutil.which("zenity"):
        result = subprocess.run(["zenity", "--file-selection", f"--title={title}", f"--filename={initial.rstrip('/')}/", f"--file-filter={name_filter}"], capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    file, _ = QFileDialog.getOpenFileName(parent, title, initial, name_filter)
    return file


def choose_document_save(parent: QWidget, title: str, suggested: str, name_filter: str) -> str:
    if sys.platform.startswith("linux") and shutil.which("kdialog"):
        result = subprocess.run(["kdialog", "--title", title, "--getsavefilename", suggested, name_filter], capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    if sys.platform.startswith("linux") and shutil.which("zenity"):
        result = subprocess.run(["zenity", "--file-selection", "--save", "--confirm-overwrite", f"--title={title}", f"--filename={suggested}", f"--file-filter={name_filter}"], capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    file, _ = QFileDialog.getSaveFileName(parent, title, suggested, name_filter)
    return file


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class MagicWorker(QRunnable):
    def __init__(self, args: tuple):
        super().__init__()
        self.args = args
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            self.signals.finished.emit(build_prompts(*self.args))
        except Exception as error:
            self.signals.failed.emit(str(error))


class TimelineRuler(QWidget):
    def __init__(self):
        super().__init__()
        self.offset = 0
        self.scale = 65
        self.setFixedHeight(28)

    def set_offset(self, value: int) -> None:
        self.offset = value
        self.update()

    def set_scale(self, value: int) -> None:
        self.scale = value
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#17191a"))
        painter.setFont(self.font())
        for second in range(61):
            x = second * self.scale - self.offset
            if x < -30 or x > self.width() + 30:
                continue
            painter.setPen(QPen(QColor("#303538")))
            painter.drawLine(x, 12, x, 24)
            painter.setPen(QColor("#ff5757") if second == 0 else QColor("#788186"))
            painter.drawText(x + 4, 16, "0" if second == 0 else f"{second}.00")


class ResizeHandle(QFrame):
    preview = Signal(float)
    finished = Signal()

    def __init__(self, duration: float, pixels_per_second: int):
        super().__init__()
        self.duration = duration
        self.start_duration = duration
        self.start_x: float | None = None
        self.pixels_per_second = pixels_per_second
        self.setObjectName("resizeHandle")
        self.setFixedWidth(9)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setToolTip("Drag to resize segment (1 second minimum)")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_x = event.globalPosition().x()
            self.start_duration = self.duration
            self.grabMouse()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.start_x is not None:
            value = max(1, min(12, round((self.start_duration + (event.globalPosition().x() - self.start_x) / self.pixels_per_second) * 2) / 2))
            if value != self.duration:
                self.duration = value
                self.preview.emit(value)
            event.accept()

    def mouseReleaseEvent(self, event):
        if self.start_x is not None:
            self.start_x = None
            self.releaseMouse()
            self.finished.emit()
            event.accept()


class TimelineHeightHandle(QFrame):
    height_changed = Signal(int)
    finished = Signal()

    def __init__(self, current_height: int):
        super().__init__()
        self.current_height = current_height
        self.start_height = current_height
        self.start_y: float | None = None
        self.setObjectName("timelineHeightHandle")
        self.setFixedHeight(9)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip("Drag down to enlarge timeline previews")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_y = event.globalPosition().y()
            self.start_height = self.current_height
            self.grabMouse()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.start_y is not None:
            value = max(184, min(430, int(self.start_height + event.globalPosition().y() - self.start_y)))
            self.current_height = value
            self.height_changed.emit(value)
            event.accept()

    def mouseReleaseEvent(self, event):
        if self.start_y is not None:
            self.start_y = None
            self.releaseMouse()
            self.finished.emit()
            event.accept()


class SegmentCard(QFrame):
    duration_changed = Signal(float)
    delete_requested = Signal()
    resize_finished = Signal()

    def __init__(self, segment: Segment, preview_height: int, pixels_per_second: int):
        super().__init__()
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setObjectName("segmentCard")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        content = QWidget()
        content.setCursor(Qt.CursorShape.OpenHandCursor)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(1)
        badges = QHBoxLayout()
        badges.setContentsMargins(0, 0, 0, 0)
        kind = QLabel("WEBM" if segment.kind == "video" else "IMAGE")
        kind.setObjectName("mediaBadge")
        role = QLabel(segment.role.upper())
        role.setObjectName("roleBadge")
        close = QPushButton("×")
        close.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        close.setObjectName("tileDelete")
        close.setFixedSize(20, 20)
        close.clicked.connect(self.delete_requested)
        badges.addWidget(kind)
        badges.addStretch()
        badges.addWidget(role)
        badges.addWidget(close)
        layout.addLayout(badges)
        image = QLabel()
        image.setObjectName("segmentPreview")
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image.setMinimumHeight(max(80, preview_height))
        preview_width = max(36, int(segment.duration * pixels_per_second) - 14)
        image.setPixmap(QPixmap(segment.preview_path).scaled(preview_width, max(80, preview_height), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(image, 1)
        title = QLabel(segment.prompt or segment.name)
        title.setToolTip(segment.name)
        title.setWordWrap(False)
        title.setObjectName("tileTitle")
        layout.addWidget(title)
        self.duration_label = QLabel(f"{segment.duration:.1f}s")
        self.duration_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.duration_label.setObjectName("tileDuration")
        layout.addWidget(self.duration_label)
        outer.addWidget(content, 1)
        handle = ResizeHandle(segment.duration, pixels_per_second)
        handle.preview.connect(self._preview_duration)
        handle.finished.connect(self.resize_finished)
        outer.addWidget(handle)

    def _preview_duration(self, value: float) -> None:
        self.duration_label.setText(f"{value:.1f}s")
        self.duration_changed.emit(value)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.ignore()

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        event.ignore()


class SettingsDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("AI Provider Settings")
        form = QFormLayout(self)
        self.provider = QComboBox()
        self.provider.addItems(["gemini", "openai"])
        self.provider.setCurrentText(settings.value("provider", "gemini"))
        self.model = QComboBox()
        self.model.addItems(GEMINI_MODELS)
        self.model.setCurrentText(settings.value("gemini_model", GEMINI_MODELS[0]))
        self.gemini = QLineEdit(settings.value("gemini_key", ""))
        self.openai = QLineEdit(settings.value("openai_key", ""))
        self.gemini.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember = QCheckBox("Store API keys persistently on this computer")
        self.remember.setChecked(settings.value("remember_keys", False, bool))
        form.addRow("Provider", self.provider)
        form.addRow("Gemini model", self.model)
        form.addRow("Gemini API key", self.gemini)
        form.addRow("OpenAI API key", self.openai)
        form.addRow("", self.remember)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def accept(self) -> None:
        self.settings.setValue("provider", self.provider.currentText())
        self.settings.setValue("gemini_model", self.model.currentText())
        self.settings.setValue("remember_keys", self.remember.isChecked())
        if self.remember.isChecked():
            self.settings.setValue("gemini_key", self.gemini.text().strip())
            self.settings.setValue("openai_key", self.openai.text().strip())
        else:
            self.settings.remove("gemini_key")
            self.settings.remove("openai_key")
        self.parent().session_keys = {"gemini": self.gemini.text().strip(), "openai": self.openai.text().strip()}
        super().accept()


class ProjectDetailsDialog(QDialog):
    def __init__(self, suggested_description: str, parent=None, name: str = "", collection: str = "", collections: list[str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Save project to library")
        self.setMinimumWidth(430)
        form = QFormLayout(self)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Project name")
        self.name.setText(name)
        self.description = QTextEdit()
        self.description.setPlaceholderText("Short description shown in the project library")
        self.description.setPlainText(suggested_description[:240])
        self.description.setFixedHeight(90)
        self.collection = QComboBox()
        self.collection.setEditable(True)
        self.collection.addItem("No collection", "")
        for value in sorted(set(collections or []), key=str.casefold):
            self.collection.addItem(value, value)
        if collection:
            self.collection.setCurrentText(collection)
        form.addRow("Name", self.name)
        form.addRow("Description", self.description)
        form.addRow("Collection", self.collection)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def accept(self) -> None:
        if not self.name.text().strip():
            QMessageBox.warning(self, "Project name required", "Enter a name for this project.")
            return
        super().accept()

    def collection_name(self) -> str:
        value = self.collection.currentText().strip()
        return "" if value == "No collection" else value


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LTX Director - Director")
        self.setWindowIcon(QIcon(str(files("ltx_prompt_director").joinpath("assets/icon.png"))))
        self.resize(1450, 900)
        self.settings = QSettings()
        self.session_keys = {"gemini": self.settings.value("gemini_key", ""), "openai": self.settings.value("openai_key", "")}
        self.segments: list[Segment] = []
        self.current_project_id: str | None = None
        self.current_project_name = "Untitled"
        self.project_dirty = False
        self.current_collection: str | None = None
        self.pixels_per_second = 65
        self.timeline_height = 184
        self.thread_pool = QThreadPool.globalInstance()
        self._loading = False
        self._build_ui()
        self._apply_theme()
        self.update_window_title()

    def _build_ui(self) -> None:
        self._build_project_dock()
        toolbar = QToolBar("Project")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        projects_action = self.project_dock.toggleViewAction()
        projects_action.setText("Projects")
        toolbar.addAction(projects_action)
        for label, callback in [
            ("New Project", self.new_project), ("Add Media", self.add_media), ("Open", self.import_ltx),
            ("Import", self.import_project), ("Delete selected", self.delete_selected),
        ]:
            action = QAction(label, self)
            action.triggered.connect(callback)
            toolbar.addAction(action)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        self.provider_button = QPushButton()
        self.provider_button.clicked.connect(self.open_settings)
        self.update_provider_button()
        toolbar.addWidget(self.provider_button)
        for label, callback in [("LTX Director Export", self.export_ltx), ("Project Export", self.export_project)]:
            action = QAction(label, self)
            action.triggered.connect(callback)
            toolbar.addAction(action)
        settings_action = QAction("⚙", self)
        settings_action.triggered.connect(self.open_settings)
        toolbar.addAction(settings_action)

        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(10, 5, 10, 8)
        outer.setSpacing(8)
        timeline_shell = QFrame()
        timeline_shell.setObjectName("timelineShell")
        timeline_layout = QVBoxLayout(timeline_shell)
        timeline_layout.setContentsMargins(0, 0, 0, 0)
        timeline_layout.setSpacing(0)
        timeline_controls = QHBoxLayout()
        timeline_controls.setContentsMargins(8, 5, 8, 3)
        timeline_controls.addWidget(QLabel("TIMELINE"))
        timeline_controls.addStretch()
        timeline_controls.addWidget(QLabel("Scale"))
        self.timeline_scale = QSlider(Qt.Orientation.Horizontal)
        self.timeline_scale.setRange(20, 160)
        self.timeline_scale.setValue(self.pixels_per_second)
        self.timeline_scale.setFixedWidth(150)
        self.timeline_scale.setToolTip("Timeline pixels per second")
        self.timeline_scale.valueChanged.connect(self.set_timeline_scale)
        timeline_controls.addWidget(self.timeline_scale)
        autofit = QPushButton("Auto fit")
        autofit.clicked.connect(self.autofit_timeline)
        timeline_controls.addWidget(autofit)
        timeline_layout.addLayout(timeline_controls)
        ruler_row = QHBoxLayout()
        ruler_row.setContentsMargins(0, 0, 0, 0)
        ruler_spacer = QWidget()
        ruler_spacer.setFixedWidth(145)
        self.ruler = TimelineRuler()
        ruler_row.addWidget(ruler_spacer)
        ruler_row.addWidget(self.ruler, 1)
        timeline_layout.addLayout(ruler_row)
        track_row = QHBoxLayout()
        track_row.setContentsMargins(0, 0, 0, 0)
        track_row.setSpacing(0)
        main_label = QLabel("MAIN ◉")
        main_label.setObjectName("mainTrackLabel")
        main_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main_label.setFixedWidth(145)
        track_row.addWidget(main_label)
        self.timeline = QListWidget()
        self.timeline.setViewMode(QListWidget.ViewMode.ListMode)
        self.timeline.setFlow(QListWidget.Flow.LeftToRight)
        self.timeline.setWrapping(False)
        self.timeline.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.timeline.setSpacing(0)
        self.timeline.setFixedHeight(self.timeline_height)
        self.timeline.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.timeline.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.timeline.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.timeline.customContextMenuRequested.connect(self.timeline_menu)
        self.timeline.currentRowChanged.connect(self.load_editor)
        self.timeline.model().rowsMoved.connect(lambda *_: self.sync_order())
        self.timeline.horizontalScrollBar().valueChanged.connect(self.ruler.set_offset)
        track_row.addWidget(self.timeline, 1)
        self.add_tile = QPushButton("＋\nAdd media\n60.0s available")
        self.add_tile.setObjectName("addTile")
        self.add_tile.setFixedSize(112, 126)
        self.add_tile.clicked.connect(self.add_media)
        add_tile_wrap = QWidget()
        add_tile_layout = QVBoxLayout(add_tile_wrap)
        add_tile_layout.setContentsMargins(8, 26, 8, 0)
        add_tile_layout.addWidget(self.add_tile)
        add_tile_layout.addStretch()
        track_row.addWidget(add_tile_wrap)
        timeline_layout.addLayout(track_row)
        self.timeline_height_handle = TimelineHeightHandle(self.timeline_height)
        self.timeline_height_handle.height_changed.connect(self.set_timeline_height)
        self.timeline_height_handle.finished.connect(lambda: self.refresh_timeline(self.timeline.currentRow()))
        timeline_layout.addWidget(self.timeline_height_handle)
        outer.addWidget(timeline_shell)

        self.sequence_bar = QLabel()
        self.sequence_bar.setObjectName("sequenceBar")
        outer.addWidget(self.sequence_bar)

        controls = QHBoxLayout()
        intent_label = QLabel("DIRECTOR'S INTENT")
        intent_label.setObjectName("sectionLabel")
        controls.addWidget(intent_label)
        self.intent = QLineEdit()
        self.intent.setPlaceholderText("Optional motion, story, or dialogue direction")
        self.intent.textChanged.connect(self.mark_dirty)
        self.sfx = QPushButton("SFX")
        self.sfx.setCheckable(True)
        self.sfx.setObjectName("audioToggle")
        self.sfx.toggled.connect(self.mark_dirty)
        self.vocals = QPushButton("Vocals")
        self.vocals.setCheckable(True)
        self.vocals.setObjectName("audioToggle")
        self.vocals.toggled.connect(self.mark_dirty)
        self.hdr = QPushButton("HDR")
        self.hdr.setCheckable(True)
        self.hdr.setObjectName("qualityToggle")
        self.hdr.setToolTip("Prepend (4K, HDR, Realistic) to the global prompt")
        self.hdr.clicked.connect(self.apply_global_prefixes)
        self.hdr.toggled.connect(self.mark_dirty)
        self.reduce_music = QPushButton("Reduce Music")
        self.reduce_music.setCheckable(True)
        self.reduce_music.setObjectName("qualityToggle")
        self.reduce_music.setToolTip("Ask Magic Build for setting-specific ambient sound to discourage generated music")
        self.reduce_music.toggled.connect(self.mark_dirty)
        self.magic_button = QPushButton("✦ Magic Build")
        self.magic_button.setObjectName("magicButton")
        self.magic_button.clicked.connect(self.magic_build)
        controls.addWidget(self.intent, 1)
        controls.addWidget(self.sfx)
        controls.addWidget(self.vocals)
        controls.addWidget(self.hdr)
        controls.addWidget(self.reduce_music)
        controls.addWidget(self.magic_button)
        outer.addLayout(controls)

        segment_panel = QFrame()
        segment_panel.setObjectName("promptPanel")
        segment_layout = QVBoxLayout(segment_panel)
        segment_layout.setContentsMargins(9, 6, 9, 5)
        segment_header = QHBoxLayout()
        segment_label = QLabel("SEGMENT PROMPT")
        segment_label.setObjectName("sectionLabel")
        segment_header.addWidget(segment_label)
        self.frame_number = QLabel("Frame —")
        self.frame_number.setObjectName("muted")
        self.start_button = QPushButton("Start frame")
        self.end_button = QPushButton("End frame")
        self.start_button.setCheckable(True)
        self.end_button.setCheckable(True)
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(1, 12)
        self.duration_spin.setSingleStep(.5)
        self.duration_spin.setDecimals(1)
        self.duration_spin.valueChanged.connect(self.editor_duration_changed)
        self.start_button.clicked.connect(lambda: self.set_role("start"))
        self.end_button.clicked.connect(lambda: self.set_role("end"))
        segment_header.addStretch()
        segment_header.addWidget(self.frame_number)
        segment_header.addWidget(self.start_button)
        segment_header.addWidget(self.end_button)
        segment_header.addWidget(QLabel("Duration"))
        segment_header.addWidget(self.duration_spin)
        segment_layout.addLayout(segment_header)
        self.segment_prompt = QTextEdit()
        self.segment_prompt.textChanged.connect(self.save_prompt)
        segment_layout.addWidget(self.segment_prompt)
        segment_footer = QHBoxLayout()
        self.segment_count = QLabel("0 characters")
        self.segment_count.setObjectName("muted")
        copy_segment = QPushButton("□ Copy")
        copy_segment.setObjectName("copyButton")
        copy_segment.clicked.connect(lambda: QApplication.clipboard().setText(self.segment_prompt.toPlainText()))
        segment_footer.addWidget(self.segment_count)
        segment_footer.addStretch()
        segment_footer.addWidget(copy_segment)
        segment_layout.addLayout(segment_footer)
        outer.addWidget(segment_panel, 3)
        global_panel = QFrame()
        global_panel.setObjectName("promptPanel")
        global_layout = QVBoxLayout(global_panel)
        global_layout.setContentsMargins(9, 6, 9, 5)
        global_header = QHBoxLayout()
        global_label = QLabel("GLOBAL PROMPT")
        global_label.setObjectName("sectionLabel")
        self.applied_label = QLabel("Applied across all 0 segments")
        self.applied_label.setObjectName("muted")
        global_header.addWidget(global_label)
        global_header.addStretch()
        global_header.addWidget(self.applied_label)
        global_layout.addLayout(global_header)
        self.global_prompt = QTextEdit()
        self.global_prompt.textChanged.connect(self.update_counts)
        self.global_prompt.textChanged.connect(self.mark_dirty)
        global_layout.addWidget(self.global_prompt)
        global_footer = QHBoxLayout()
        self.global_count = QLabel("0 characters")
        self.global_count.setObjectName("muted")
        copy_global = QPushButton("□ Copy")
        copy_global.setObjectName("copyButton")
        copy_global.clicked.connect(lambda: QApplication.clipboard().setText(self.global_prompt.toPlainText()))
        global_footer.addWidget(self.global_count)
        global_footer.addStretch()
        global_footer.addWidget(copy_global)
        global_layout.addLayout(global_footer)
        outer.addWidget(global_panel, 2)
        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())
        self.update_summary()

    def _build_project_dock(self) -> None:
        self.project_dock = QDockWidget("PROJECT LIBRARY", self)
        self.project_dock.setObjectName("projectDock")
        self.project_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea)
        self.project_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.project_dock.setMinimumWidth(290)
        self.project_dock.setMaximumWidth(350)

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(9, 9, 9, 9)
        layout.setSpacing(8)
        header = QHBoxLayout()
        self.collection_up = QPushButton("↑ UP")
        self.collection_up.setVisible(False)
        self.collection_up.clicked.connect(self.leave_collection)
        self.project_library_title = QLabel("Saved projects")
        self.project_library_title.setObjectName("projectLibraryTitle")
        header.addWidget(self.collection_up)
        header.addWidget(self.project_library_title, 1)
        layout.addLayout(header)
        self.project_search = QLineEdit()
        self.project_search.setPlaceholderText("Search projects…")
        self.project_search.setClearButtonEnabled(True)
        self.project_search.textChanged.connect(self.filter_projects)
        layout.addWidget(self.project_search)

        self.project_list = QListWidget()
        self.project_list.setObjectName("projectList")
        self.project_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.project_list.setFlow(QListWidget.Flow.TopToBottom)
        self.project_list.setWrapping(False)
        self.project_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.project_list.setMovement(QListWidget.Movement.Static)
        self.project_list.setIconSize(QSize(230, 230))
        self.project_list.setSpacing(8)
        self.project_list.itemDoubleClicked.connect(lambda *_: self.open_library_project())
        layout.addWidget(self.project_list, 1)

        buttons = QHBoxLayout()
        self.save_library_button = QPushButton("Save Current")
        self.save_library_button.setObjectName("librarySave")
        self.save_library_button.clicked.connect(self.save_library_project)
        open_button = QPushButton("Open")
        open_button.clicked.connect(self.open_library_project)
        edit_button = QPushButton("Edit")
        edit_button.clicked.connect(self.edit_library_project)
        delete_button = QPushButton("Delete")
        delete_button.setObjectName("libraryDelete")
        delete_button.clicked.connect(self.delete_library_project)
        buttons.addWidget(self.save_library_button, 1)
        buttons.addWidget(open_button)
        buttons.addWidget(edit_button)
        buttons.addWidget(delete_button)
        layout.addLayout(buttons)
        self.project_dock.setWidget(panel)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.project_dock)
        self.project_dock.hide()
        self.refresh_project_library()

    def _apply_theme(self) -> None:
        self.setStyleSheet("""
        QMainWindow,QWidget{background:#24292c;color:#d9dcde;font:11px Arial} QToolBar{background:#303537;border:0;spacing:6px;padding:5px}
        QToolButton,QPushButton,QComboBox,QDoubleSpinBox,QLineEdit{background:#303436;border:1px solid #101213;border-radius:3px;padding:5px 8px}
        QToolButton:hover,QPushButton:hover{background:#41474a} QToolButton{min-height:20px} QLineEdit{background:#1e2122}
        #timelineShell{background:#0d0f10;border:1px solid #323638;border-radius:3px} #mainTrackLabel{background:#191c1d;border-right:1px solid #34383a;font-weight:bold}
        QListWidget{background:#0d0f10;border:0;padding-top:26px} QListWidget::item{border:1px solid #696b6c;background:#252728;margin:0} QListWidget::item:selected{border:2px solid #f1f1f1;background:#293034}
        #segmentCard{background:#252728;border:0} #mediaBadge{background:#e5e5e5;color:#262626;font-weight:bold;padding:2px} #roleBadge{background:#36393a;color:#eee;padding:2px}
        #tileDelete{padding:0;background:#454849;color:#ddd;border:0} #tileTitle{background:#242627;padding:3px;font-size:9px} #tileDuration{color:#a2a7a9;font-size:8px}
        #resizeHandle{background:#606669;border-left:1px solid #9ca2a4} #resizeHandle:hover{background:#8aa6b7;border-left:2px solid #d8edf8}
        #timelineHeightHandle{background:#3d4447;border-top:1px solid #737d82} #timelineHeightHandle:hover{background:#6d8795;border-top:2px solid #d8edf8}
        #segmentPreview{background:#17191a;border-top:1px solid #34383a}
        #addTile{border:1px dashed #596065;background:#111415;color:#828b90;font-size:10px} #sequenceBar{background:#1c1f20;border:1px solid #0e1011;border-radius:3px;padding:12px;font-weight:bold}
        #sectionLabel{color:#939ca1;font-size:8px;letter-spacing:1px} #muted{color:#879095;font-size:9px} #promptPanel{background:#252728;border:1px solid #101213;border-radius:3px}
        QTextEdit{background:#252728;border:0;color:#e1e4e5;font:11px 'Courier New';padding:4px} #magicButton{background:#3b6f9c;border-color:#4f83ae;font-weight:bold}
        #audioToggle:checked,#qualityToggle:checked{background:#285c3d;border-color:#4c9b6a;color:#c9f4d6} #copyButton{border:0;background:transparent;color:#aeb5b8} QStatusBar{background:#1b1e1f;color:#7f898d}
        QDockWidget{background:#191d1f;color:#d9dcde;font-weight:bold} QDockWidget::title{background:#1b2022;border-bottom:1px solid #0e1011;padding:8px;text-align:left}
        #projectLibraryTitle{font-size:15px;font-weight:bold;color:#f0f2f3} #projectList{background:#151819;border:1px solid #0e1011;padding:5px}
        #projectList::item{background:#24282a;border:1px solid #3b4144;border-radius:4px;padding:7px;color:#dce0e2} #projectList::item:hover{border-color:#6488a1;background:#2b3134} #projectList::item:selected{border:2px solid #69a5d0;background:#29343a}
        #librarySave{background:#3b6f9c;border-color:#4f83ae;font-weight:bold} #libraryDelete:hover{background:#713d3d;border-color:#9b5656}
        """)

    def refresh_project_library(self, select_id: str | None = None) -> None:
        selected = select_id or self.current_project_id
        self.project_list.clear()
        records = self.library_records()
        self.collection_up.setVisible(bool(self.current_collection))
        self.project_library_title.setText(self.current_collection or "Saved projects")
        visible_records = [record for record in records if record.get("collection", "") == (self.current_collection or "")]
        if not self.current_collection:
            collections: dict[str, list[dict]] = {}
            for record in records:
                if record.get("collection"):
                    collections.setdefault(str(record["collection"]), []).append(record)
            for name, members in sorted(collections.items(), key=lambda pair: pair[0].casefold()):
                members.sort(key=lambda value: value.get("savedAt", ""), reverse=True)
                item = QListWidgetItem(f"{name}\n{len(members)} project{'s' if len(members) != 1 else ''}")
                item.setData(Qt.ItemDataRole.UserRole, {"kind": "collection", "name": name, "description": f"{len(members)} projects"})
                item.setToolTip(f"Collection: {name}")
                item.setSizeHint(QSize(255, 292))
                item.setIcon(QIcon(self.collection_pixmap(members[:4])))
                self.project_list.addItem(item)
        selected_item = None
        for meta in visible_records:
            name = str(meta.get("name") or "Untitled project")
            description = " ".join(str(meta.get("description") or "No description").split())
            if len(description) > 105:
                description = description[:102] + "…"
            item = QListWidgetItem(f"{name}\n{description}")
            item.setData(Qt.ItemDataRole.UserRole, {**meta, "kind": "project"})
            item.setToolTip(f"{name}\n\n{meta.get('description', '')}")
            item.setSizeHint(QSize(255, 292))
            pixmap = pixmap_from_data_url(str(meta.get("thumbnailData", "")))
            if not pixmap.isNull():
                item.setIcon(QIcon(self.square_pixmap(pixmap, 230)))
            self.project_list.addItem(item)
            if meta.get("id") == selected:
                selected_item = item
        if selected_item:
            self.project_list.setCurrentItem(selected_item)
        self.filter_projects(self.project_search.text())

    def library_records(self) -> list[dict]:
        records = []
        for path in project_library_path().glob("*.meta.json"):
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
                if Path(meta.get("projectPath", "")).is_file():
                    records.append(meta)
            except (OSError, ValueError, TypeError):
                continue
        records.sort(key=lambda value: value.get("savedAt", ""), reverse=True)
        return records

    @staticmethod
    def square_pixmap(pixmap: QPixmap, size: int) -> QPixmap:
        scaled = pixmap.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        return scaled.copy(max(0, (scaled.width() - size) // 2), max(0, (scaled.height() - size) // 2), size, size)

    def collection_pixmap(self, members: list[dict]) -> QPixmap:
        result = QPixmap(230, 230)
        result.fill(QColor("#101314"))
        painter = QPainter(result)
        cells = ((0, 0), (117, 0), (0, 117), (117, 117))
        for member, (x, y) in zip(members, cells):
            pixmap = pixmap_from_data_url(str(member.get("thumbnailData", "")))
            if not pixmap.isNull():
                painter.drawPixmap(x, y, self.square_pixmap(pixmap, 113))
        painter.setPen(QPen(QColor("#657078"), 2))
        painter.drawLine(115, 0, 115, 230)
        painter.drawLine(0, 115, 230, 115)
        painter.end()
        return result

    def filter_projects(self, query: str) -> None:
        terms = query.casefold().split()
        for row in range(self.project_list.count()):
            item = self.project_list.item(row)
            meta = item.data(Qt.ItemDataRole.UserRole) or {}
            haystack = f"{meta.get('name', '')} {meta.get('description', '')}".casefold()
            item.setHidden(not all(term in haystack for term in terms))

    def selected_library_project(self) -> dict | None:
        item = self.project_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def leave_collection(self) -> None:
        self.current_collection = None
        self.refresh_project_library()

    def save_library_project(self) -> None:
        if not self.segments:
            QMessageBox.information(self, "Nothing to save", "Add at least one image or WebM segment first.")
            return
        root = project_library_path()
        meta = None
        if self.current_project_id:
            meta_path = root / f"{self.current_project_id}.meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    meta = None
        if not meta:
            collections = [str(record.get("collection")) for record in self.library_records() if record.get("collection")]
            dialog = ProjectDetailsDialog(self.intent.text(), self, collection=self.current_collection or "", collections=collections)
            if not dialog.exec():
                return
            project_id = uuid4().hex
            meta = {
                "id": project_id,
                "name": dialog.name.text().strip(),
                "description": dialog.description.toPlainText().strip(),
                "collection": dialog.collection_name(),
                "projectPath": str(root / f"{project_id}.LTXD"),
            }
            self.current_project_id = project_id
            self.current_project_name = meta["name"]
            self.update_window_title()
        meta["savedAt"] = datetime.now(timezone.utc).isoformat()
        meta["duration"] = self.total_duration()
        meta["segmentCount"] = len(self.segments)
        meta["thumbnailData"] = data_url(self.segments[0].preview_path, max_edge=360, quality=84)
        payload = self.project_payload()
        payload["library"] = {key: meta.get(key, "") for key in ("id", "name", "description", "collection", "savedAt")}
        Path(meta["projectPath"]).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        (root / f"{meta['id']}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        self.project_dirty = False
        self.refresh_project_library(meta["id"])
        self.project_dock.show()
        self.statusBar().showMessage(f"Project saved to library: {meta['name']}")

    def open_library_project(self) -> None:
        meta = self.selected_library_project()
        if not meta:
            return
        if meta.get("kind") == "collection":
            self.current_collection = str(meta["name"])
            self.project_search.clear()
            self.refresh_project_library()
            return
        try:
            payload = json.loads(Path(meta["projectPath"]).read_text(encoding="utf-8"))
            self.load_project_payload(payload)
            self.current_project_id = str(meta["id"])
            self.current_project_name = str(meta["name"])
            self.project_dirty = False
            self.update_window_title()
            self.statusBar().showMessage(f"Project opened: {meta['name']}")
        except Exception as error:
            self._loading = False
            QMessageBox.critical(self, "Project open failed", str(error))

    def delete_library_project(self) -> None:
        meta = self.selected_library_project()
        if not meta:
            return
        if meta.get("kind") == "collection":
            QMessageBox.information(self, "Collection contains projects", "Open the collection and move or delete its projects individually.")
            return
        answer = QMessageBox.question(
            self,
            "Delete project",
            f"Delete ‘{meta.get('name', 'this project')}’ from the local project library?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        project_path = Path(meta.get("projectPath", ""))
        meta_path = project_library_path() / f"{meta.get('id')}.meta.json"
        if project_path.is_file():
            project_path.unlink()
        if meta_path.is_file():
            meta_path.unlink()
        if self.current_project_id == meta.get("id"):
            self.current_project_id = None
            self.project_dirty = True
        self.refresh_project_library()
        self.statusBar().showMessage(f"Project deleted: {meta.get('name', 'Untitled project')}")

    def edit_library_project(self) -> None:
        meta = self.selected_library_project()
        if not meta or meta.get("kind") != "project":
            return
        collections = [str(record.get("collection")) for record in self.library_records() if record.get("collection")]
        dialog = ProjectDetailsDialog(
            str(meta.get("description", "")), self, name=str(meta.get("name", "")),
            collection=str(meta.get("collection", "")), collections=collections,
        )
        if not dialog.exec():
            return
        meta.update({
            "name": dialog.name.text().strip(),
            "description": dialog.description.toPlainText().strip(),
            "collection": dialog.collection_name(),
            "savedAt": datetime.now(timezone.utc).isoformat(),
        })
        meta.pop("kind", None)
        root = project_library_path()
        (root / f"{meta['id']}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        project_path = Path(meta["projectPath"])
        payload = json.loads(project_path.read_text(encoding="utf-8"))
        payload["library"] = {key: meta.get(key, "") for key in ("id", "name", "description", "collection", "savedAt")}
        project_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if self.current_project_id == meta["id"]:
            self.current_project_name = meta["name"]
            self.update_window_title()
        self.refresh_project_library(meta["id"])
        self.statusBar().showMessage(f"Project details updated: {meta['name']}")

    def update_window_title(self) -> None:
        self.setWindowTitle(f"LTX Director - Director :: {self.current_project_name}")

    def mark_dirty(self, *_args) -> None:
        if not self._loading:
            self.project_dirty = True

    def new_project(self) -> None:
        if self.segments and self.project_dirty:
            answer = QMessageBox.question(
                self, "Start a new project",
                "Start a new project? Any changes not saved to the project library or an exported project file will be lost.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._loading = True
        self.segments = []
        self.current_project_id = None
        self.current_project_name = "Untitled"
        self.intent.clear()
        self.segment_prompt.clear()
        self.global_prompt.clear()
        self.sfx.setChecked(False)
        self.vocals.setChecked(False)
        self.hdr.setChecked(False)
        self.reduce_music.setChecked(False)
        self._loading = False
        self.project_dirty = False
        self.refresh_timeline()
        self.update_window_title()
        self.statusBar().showMessage("New project ready")

    def set_timeline_scale(self, value: int) -> None:
        self.pixels_per_second = value
        self.ruler.set_scale(value)
        self.refresh_timeline(self.timeline.currentRow())
        self.mark_dirty()

    def autofit_timeline(self) -> None:
        total = self.total_duration()
        if total <= 0:
            return
        available = max(200, self.timeline.viewport().width() - 6)
        fitted = max(20, min(160, int(available / total)))
        self.timeline_scale.setValue(fitted)
        self.timeline.horizontalScrollBar().setValue(0)

    def set_timeline_height(self, value: int) -> None:
        self.timeline_height = value
        self.timeline.setFixedHeight(value)
        self.add_tile.setFixedHeight(max(126, value - 58))
        self.mark_dirty()

    def apply_global_prefixes(self) -> None:
        text = self.global_prompt.toPlainText().strip()
        quality = "(4K, HDR, Realistic)"
        if self.hdr.isChecked() and not text.startswith(quality):
            text = f"{quality}\n{text}".strip()
        elif not self.hdr.isChecked() and text.startswith(quality):
            text = text[len(quality):].lstrip("\n ")
        self.global_prompt.setPlainText(text)

    def total_duration(self) -> float:
        return sum(item.duration for item in self.segments)

    def add_media(self) -> None:
        paths = choose_media_files(self, True, self.settings.value("last_media_dir", str(Path.home())))
        if not paths:
            return
        self.settings.setValue("last_media_dir", str(Path(paths[0]).parent))
        room = MAX_SECONDS - self.total_duration()
        for path in paths[: max(0, MAX_SEGMENTS - len(self.segments))]:
            if room < 1:
                break
            try:
                kind, preview, frames, trim = prepare_media(path)
                duration = 1.0 if kind == "video" else min(5.0, room)
                self.segments.append(Segment(Path(path).name, path, preview, kind, "end" if len(self.segments) % 2 else "start", duration=duration, media_duration_frames=frames, trim_start=trim))
                room -= duration
            except Exception as error:
                QMessageBox.warning(self, "Media error", f"{Path(path).name}: {error}")
        self.mark_dirty()
        self.refresh_timeline(len(self.segments) - 1)

    def refresh_timeline(self, selected: int = 0) -> None:
        self._loading = True
        self.timeline.clear()
        card_height = max(152, self.timeline_height - 32)
        preview_height = max(80, card_height - 55)
        for index, segment in enumerate(self.segments):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, segment.id)
            item.setSizeHint(QSize(max(48, int(segment.duration * self.pixels_per_second)), card_height))
            self.timeline.addItem(item)
            card = SegmentCard(segment, preview_height, self.pixels_per_second)
            card.duration_changed.connect(lambda value, sid=segment.id: self.change_duration(sid, value))
            card.delete_requested.connect(lambda sid=segment.id: self.delete_by_id(sid))
            card.resize_finished.connect(lambda sid=segment.id: self.finish_resize(sid))
            self.timeline.setItemWidget(item, card)
        self._loading = False
        if self.segments:
            self.timeline.setCurrentRow(max(0, min(selected, len(self.segments) - 1)))
        self.update_summary()

    def sync_order(self) -> None:
        if self._loading:
            return
        by_id = {segment.id: segment for segment in self.segments}
        self.segments = [by_id[self.timeline.item(row).data(Qt.ItemDataRole.UserRole)] for row in range(self.timeline.count())]
        self.mark_dirty()

    def current_segment(self) -> Segment | None:
        row = self.timeline.currentRow()
        return self.segments[row] if 0 <= row < len(self.segments) else None

    def load_editor(self, row: int) -> None:
        self._loading = True
        segment = self.current_segment()
        self.segment_prompt.setPlainText(segment.prompt if segment else "")
        self.start_button.setEnabled(bool(segment))
        self.end_button.setEnabled(bool(segment))
        if segment:
            self.start_button.setChecked(segment.role == "start")
            self.end_button.setChecked(segment.role == "end")
            self.duration_spin.setValue(segment.duration)
            self.frame_number.setText(f"Frame {row + 1}")
        else:
            self.frame_number.setText("Frame —")
        self._loading = False
        self.update_counts()

    def save_prompt(self) -> None:
        if not self._loading and self.current_segment():
            self.current_segment().prompt = self.segment_prompt.toPlainText()
            self.mark_dirty()
            self.update_counts()

    def editor_duration_changed(self, value: float) -> None:
        if not self._loading and self.current_segment():
            self.change_duration(self.current_segment().id, value)
            self.refresh_timeline(self.timeline.currentRow())

    def set_role(self, role: str) -> None:
        if self.current_segment():
            self.current_segment().role = role
            self.mark_dirty()
            self.refresh_timeline(self.timeline.currentRow())

    def change_duration(self, segment_id: str, value: float) -> None:
        segment = next(item for item in self.segments if item.id == segment_id)
        allowed = min(value, MAX_SECONDS - (self.total_duration() - segment.duration))
        segment.duration = max(1, round(allowed * 2) / 2)
        self.mark_dirty()
        for row in range(self.timeline.count()):
            item = self.timeline.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == segment_id:
                item.setSizeHint(QSize(max(48, int(segment.duration * self.pixels_per_second)), max(152, self.timeline_height - 32)))
                break
        self.timeline.doItemsLayout()
        self.update_summary()

    def finish_resize(self, segment_id: str) -> None:
        row = next((index for index, segment in enumerate(self.segments) if segment.id == segment_id), 0)
        self.refresh_timeline(row)

    def update_summary(self) -> None:
        total = self.total_duration()
        self.sequence_bar.setText(f"Sequence     Start: 0.00s  |  End: {total:.2f}s  |  Length: {total:.2f}s  |  Remaining: {MAX_SECONDS - total:.2f}s")
        self.add_tile.setText(f"＋\nAdd media\n{MAX_SECONDS - total:.1f}s available")
        self.add_tile.setEnabled(len(self.segments) < MAX_SEGMENTS and total <= MAX_SECONDS - 1)
        self.applied_label.setText(f"Applied across all {len(self.segments)} segments")
        self.statusBar().showMessage(f"{len(self.segments)} media segments · {total:.1f}s")

    def update_counts(self) -> None:
        self.segment_count.setText(f"{len(self.segment_prompt.toPlainText())} characters")
        self.global_count.setText(f"{len(self.global_prompt.toPlainText())} characters")

    def update_provider_button(self) -> None:
        provider = self.settings.value("provider", "gemini")
        label = self.settings.value("gemini_model", GEMINI_MODELS[0]) if provider == "gemini" else "OpenAI"
        self.provider_button.setText(f"✦ {label.replace('gemini-', 'Gemini ').replace('-', ' ').title() if provider == 'gemini' else label}")

    def timeline_menu(self, point) -> None:
        item = self.timeline.itemAt(point)
        if not item:
            return
        self.timeline.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction("Replace media", self.replace_selected)
        menu.addAction("Set as start frame", lambda: self.set_role("start"))
        menu.addAction("Set as end frame", lambda: self.set_role("end"))
        menu.addSeparator()
        menu.addAction("Delete segment", self.delete_selected)
        menu.exec(self.timeline.mapToGlobal(point))

    def replace_selected(self) -> None:
        segment = self.current_segment()
        if not segment:
            return
        paths = choose_media_files(self, False, self.settings.value("last_media_dir", str(Path.home())))
        if not paths:
            return
        path = paths[0]
        self.settings.setValue("last_media_dir", str(Path(path).parent))
        try:
            kind, preview, frames, trim = prepare_media(path)
            segment.name, segment.media_path, segment.preview_path, segment.kind = Path(path).name, path, preview, kind
            segment.media_duration_frames, segment.trim_start = frames, trim
            self.mark_dirty()
            self.refresh_timeline(self.timeline.currentRow())
            self.statusBar().showMessage("Media replaced; timing, role and prompt preserved")
        except Exception as error:
            QMessageBox.critical(self, "Replace failed", str(error))

    def delete_selected(self) -> None:
        row = self.timeline.currentRow()
        if 0 <= row < len(self.segments):
            self.segments.pop(row)
            self.mark_dirty()
            self.refresh_timeline(max(0, row - 1))

    def delete_by_id(self, segment_id: str) -> None:
        row = next((index for index, segment in enumerate(self.segments) if segment.id == segment_id), -1)
        if row >= 0:
            self.segments.pop(row)
            self.mark_dirty()
            self.refresh_timeline(max(0, row - 1))

    def open_settings(self) -> None:
        if SettingsDialog(self.settings, self).exec():
            self.update_provider_button()

    def magic_build(self) -> None:
        if not self.segments:
            self.add_media()
            return
        provider = self.settings.value("provider", "gemini")
        key = self.session_keys.get(provider) or self.settings.value(f"{provider}_key", "")
        if not key:
            self.open_settings()
            key = self.session_keys.get(provider) or self.settings.value(f"{provider}_key", "")
        if not key:
            return
        self.magic_button.setEnabled(False)
        self.statusBar().showMessage("Magic Build is analyzing optimized preview frames…")
        worker = MagicWorker((
            self.segments.copy(), provider, self.settings.value("gemini_model", GEMINI_MODELS[0]), key,
            self.intent.text(), self.sfx.isChecked(), self.vocals.isChecked(), self.hdr.isChecked(), self.reduce_music.isChecked(),
        ))
        worker.signals.finished.connect(self.magic_finished)
        worker.signals.failed.connect(self.magic_failed)
        self.thread_pool.start(worker)

    def magic_finished(self, result: dict) -> None:
        for segment, generated in zip(self.segments, result["segments"]):
            segment.prompt = str(generated.get("prompt", segment.prompt))
            segment.duration = max(1, round(float(generated.get("duration", segment.duration)) * 2) / 2)
        global_prompt = str(result.get("globalPrompt", "")).strip()
        quality = "(4K, HDR, Realistic)"
        if self.hdr.isChecked() and not global_prompt.startswith(quality):
            global_prompt = f"{quality}\n{global_prompt}"
        if self.reduce_music.isChecked() and "[SOUND]:" not in global_prompt:
            lines = global_prompt.splitlines()
            insertion = 1 if lines and lines[0].strip() == quality else 0
            lines.insert(insertion, "[SOUND]: Ambient environmental room tone matching the visible setting.")
            global_prompt = "\n".join(lines)
        self.global_prompt.setPlainText(global_prompt)
        self.mark_dirty()
        self.magic_button.setEnabled(True)
        self.refresh_timeline(self.timeline.currentRow())
        self.statusBar().showMessage("Magic Build complete")

    def magic_failed(self, message: str) -> None:
        self.magic_button.setEnabled(True)
        QMessageBox.critical(self, "Magic Build failed", message)
        self.statusBar().showMessage("Magic Build failed")

    def export_ltx(self) -> None:
        if not self.segments:
            return
        directory = Path(self.settings.value("last_document_dir", str(Path.home())))
        path = choose_document_save(self, "LTX Director Export", str(directory / "ltx_director_export.json"), "LTX Director JSON (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        self.settings.setValue("last_document_dir", str(Path(path).parent))
        cursor = 0
        timeline = []
        for segment in self.segments:
            length = max(FPS, round(segment.duration * FPS))
            record = {"id": segment.id, "type": segment.kind, "start": cursor, "length": length, "prompt": segment.prompt, "imageFile": segment.media_path, "fileName": segment.name, "fileSize": Path(segment.media_path).stat().st_size if Path(segment.media_path).exists() else 0, "imageB64": data_url(segment.preview_path), "isEndFrame": segment.role == "end"}
            if segment.kind == "video":
                record.update({"trimStart": segment.trim_start or 0, "videoDurationFrames": segment.media_duration_frames or length})
                if Path(segment.media_path).exists():
                    record["videoB64"] = data_url(segment.media_path)
            timeline.append(record)
            cursor += length
        global_prompt = self.global_prompt.toPlainText()
        payload = {"version": 1, "settings": {"start_second": 0, "end_second": cursor / FPS, "duration_seconds": cursor / FPS, "start_frame": 0, "end_frame": cursor, "duration_frames": cursor, "epsilon": .99, "use_custom_audio": False, "use_custom_motion": False, "inpaint_audio": False, "frame_rate": FPS, "display_mode": "seconds", "custom_width": 1280, "custom_height": 704, "resize_method": "maintain aspect ratio", "divisible_by": 32, "img_compression": 0, "override_audio": False}, "global_prompt": global_prompt, "retake_global_prompt": "", "timeline": {"mainTrackEnabled": True, "audioTrackEnabled": False, "motionTrackEnabled": False, "showFilenames": True, "overrideAudio": False, "inpaint_audio": False, "propHeight": 163, "globalPropHeight": 124, "global_prompt": global_prompt, "retake_global_prompt": "", "retakeMode": False, "retakeStart": 0, "retakeLength": 0, "retakePrompt": "", "retakeStrength": 1, "retakeVideo": None, "normalStartFrame": 0, "normalDurationFrames": cursor, "segments": timeline, "motionSegments": [], "audioSegments": []}}
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.statusBar().showMessage(f"LTX Director export saved: {path}")

    def import_ltx(self) -> None:
        path = choose_document_open(self, "Open LTX Director JSON", self.settings.value("last_document_dir", str(Path.home())), "LTX Director JSON (*.json)")
        if not path:
            return
        self.settings.setValue("last_document_dir", str(Path(path).parent))
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            fps = float(payload.get("settings", {}).get("frame_rate", FPS))
            loaded = []
            for index, raw in enumerate(payload["timeline"]["segments"][:MAX_SEGMENTS]):
                if raw.get("type") not in ("image", "video"):
                    continue
                cache = APP_CACHE / f"import-{index}-{Path(path).stem}.jpg"
                preview = raw.get("imageB64")
                if preview and preview.startswith("data:image/"):
                    write_data_url(preview, cache)
                else:
                    continue
                media_path = raw.get("imageFile") or raw.get("fileName") or str(cache)
                if raw.get("type") == "video" and str(raw.get("videoB64", "")).startswith("data:video/webm"):
                    video_path = APP_CACHE / f"import-{index}-{Path(raw.get('fileName', 'clip.webm')).name}"
                    write_data_url(raw["videoB64"], video_path)
                    media_path = str(video_path)
                loaded.append(Segment(raw.get("fileName", f"Segment {index + 1}"), str(media_path), str(cache), raw.get("type", "image"), "end" if raw.get("isEndFrame") else "start", raw.get("prompt", ""), max(1, float(raw.get("length", FPS)) / fps), raw.get("videoDurationFrames"), raw.get("trimStart"), raw.get("id", "")))
            if not loaded:
                raise ValueError("No supported embedded image or WebM segments were found.")
            self.segments = loaded
            self.global_prompt.setPlainText(payload.get("global_prompt") or payload.get("timeline", {}).get("global_prompt", ""))
            self.current_project_id = None
            self.current_project_name = Path(path).stem
            self.update_window_title()
            self.refresh_timeline()
            self.project_dirty = False
        except Exception as error:
            QMessageBox.critical(self, "Import failed", str(error))

    def project_payload(self) -> dict:
        frames = []
        for segment in self.segments:
            value = segment.to_dict()
            value["previewData"] = data_url(segment.preview_path)
            value["sourceData"] = data_url(segment.media_path) if Path(segment.media_path).exists() else None
            frames.append(value)
        return {"app": "ltx-director-director", "projectVersion": 2, "globalPrompt": self.global_prompt.toPlainText(), "directorIntent": self.intent.text(), "magicBuild": {"sfx": self.sfx.isChecked(), "vocals": self.vocals.isChecked(), "hdr": self.hdr.isChecked(), "reduceMusic": self.reduce_music.isChecked()}, "timelineView": {"scale": self.pixels_per_second, "height": self.timeline_height}, "frames": frames}

    def load_project_payload(self, payload: dict) -> None:
        if payload.get("app") not in {"ltx-director-director", "ltx-prompt-director-python"}:
            raise ValueError("This is not an LTX Director - Director project file.")
        self._loading = True
        loaded = []
        cache_key = uuid4().hex[:10]
        for index, original in enumerate(payload.get("frames", [])[:MAX_SEGMENTS]):
            raw = dict(original)
            preview_path = APP_CACHE / f"project-{cache_key}-{index}.jpg"
            write_data_url(raw["previewData"], preview_path)
            media_path = preview_path
            if raw.get("sourceData"):
                suffix = ".webm" if raw.get("kind") == "video" else Path(raw.get("name", "image.png")).suffix or ".png"
                media_path = APP_CACHE / f"project-source-{cache_key}-{index}{suffix}"
                write_data_url(raw["sourceData"], media_path)
            raw.update({"preview_path": str(preview_path), "media_path": str(media_path)})
            for key in ("previewData", "sourceData"):
                raw.pop(key, None)
            loaded.append(Segment.from_dict(raw))
        if not loaded:
            raise ValueError("Project contains no supported media.")
        self.segments = loaded
        self.global_prompt.setPlainText(payload.get("globalPrompt", ""))
        self.intent.setText(payload.get("directorIntent", ""))
        self.sfx.setChecked(bool(payload.get("magicBuild", {}).get("sfx")))
        self.vocals.setChecked(bool(payload.get("magicBuild", {}).get("vocals")))
        self.hdr.setChecked(bool(payload.get("magicBuild", {}).get("hdr")))
        self.reduce_music.setChecked(bool(payload.get("magicBuild", {}).get("reduceMusic")))
        view = payload.get("timelineView", {})
        self.timeline_height = max(184, min(430, int(view.get("height", self.timeline_height))))
        self.timeline_height_handle.current_height = self.timeline_height
        self.set_timeline_height(self.timeline_height)
        self.timeline_scale.setValue(max(20, min(160, int(view.get("scale", self.pixels_per_second)))))
        self._loading = False
        self.refresh_timeline()
        self.project_dirty = False

    def export_project(self) -> None:
        if not self.segments:
            return
        directory = Path(self.settings.value("last_document_dir", str(Path.home())))
        path = choose_document_save(self, "Project Export", str(directory / "ltx_director_director_project.LTXD"), "LTX Director - Director Project (*.LTXD *.ltxd)")
        if not path:
            return
        if not path.lower().endswith(".ltxd"):
            path += ".LTXD"
        self.settings.setValue("last_document_dir", str(Path(path).parent))
        payload = self.project_payload()
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.project_dirty = False
        self.statusBar().showMessage(f"Project saved: {path}")

    def import_project(self) -> None:
        path = choose_document_open(self, "Import Project", self.settings.value("last_document_dir", str(Path.home())), "LTX Director - Director Project (*.LTXD *.ltxd *.ltxproject.json *.json)")
        if not path:
            return
        self.settings.setValue("last_document_dir", str(Path(path).parent))
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            self.load_project_payload(payload)
            self.current_project_id = None
            self.current_project_name = str(payload.get("library", {}).get("name") or Path(path).stem)
            self.update_window_title()
            self.project_dirty = False
            self.statusBar().showMessage(f"Project imported: {path}")
        except Exception as error:
            self._loading = False
            QMessageBox.critical(self, "Project import failed", str(error))
