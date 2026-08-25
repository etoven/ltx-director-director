from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QEasingCurve, QEvent, QEventLoop, QObject, QRunnable, QRectF, QSettings, QSize, QStandardPaths, Qt, QThreadPool, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QDockWidget, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QInputDialog, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSlider, QSpinBox, QSplitter, QSplitterHandle, QStatusBar, QTextEdit, QToolBar, QVBoxLayout, QWidget,
)

from .ai import GEMINI_MODELS, build_prompts, refine_segment_prompt, refine_timing, retryable_connection_error
from .media import APP_CACHE, data_url, prepare_audio, prepare_media, write_audio_clip, write_data_url
from .models import AudioSegment, Segment

FPS = 24
MAX_SECONDS = 60.0
MAX_SEGMENTS = 16


def project_library_path() -> Path:
    root = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    path = Path(root) / "projects"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_export_name(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", value).strip(" .-")
    return cleaned or "Untitled"


def pixmap_from_data_url(value: str) -> QPixmap:
    pixmap = QPixmap()
    try:
        pixmap.loadFromData(base64.b64decode(value.split(",", 1)[1]))
    except (IndexError, ValueError):
        pass
    return pixmap


def choose_media_files(parent: QWidget, multiple: bool, initial: str) -> list[str]:
    """Prefer the desktop's actual file picker, then fall back to QFileDialog."""
    media_filter = "Supported Media (*.png *.jpg *.jpeg *.webp *.gif *.webm *.wav *.mp3 *.flac *.ogg *.m4a *.aac)"
    if sys.platform.startswith("linux") and shutil.which("kdialog"):
        command = ["kdialog", "--title", "Choose images, video, or audio"]
        if multiple:
            command.extend(["--multiple", "--separate-output"])
        command.extend(["--getopenfilename", initial or ":ltxPromptDirectorMedia", media_filter])
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return []
    if sys.platform.startswith("linux") and shutil.which("zenity"):
        command = ["zenity", "--file-selection", "--title=Choose images, video, or audio", f"--filename={initial.rstrip('/')}/", "--file-filter=Supported media | *.png *.jpg *.jpeg *.webp *.gif *.webm *.wav *.mp3 *.flac *.ogg *.m4a *.aac"]
        if multiple:
            command.extend(["--multiple", "--separator=\n"])
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return [line.strip() for line in result.stdout.splitlines() if line.strip()]
        return []
    if multiple:
        files, _ = QFileDialog.getOpenFileNames(parent, "Add images, video, or audio", initial, media_filter)
        return files
    file, _ = QFileDialog.getOpenFileName(parent, "Choose media", initial, media_filter)
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


def choose_directory(parent: QWidget, title: str, initial: str) -> str:
    if sys.platform.startswith("linux") and shutil.which("kdialog"):
        result = subprocess.run(["kdialog", "--title", title, "--getexistingdirectory", initial], capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    if sys.platform.startswith("linux") and shutil.which("zenity"):
        result = subprocess.run(["zenity", "--file-selection", "--directory", f"--title={title}", f"--filename={initial.rstrip('/')}/"], capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    return QFileDialog.getExistingDirectory(parent, title, initial)


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)


class MagicWorker(QRunnable):
    def __init__(self, operation, args: tuple, retries: int, retry_cooldown: int, activity: str = "Analyzing frames and directing motion…"):
        super().__init__()
        self.operation = operation
        self.args = args
        self.retries = retries
        self.retry_cooldown = max(0, int(retry_cooldown))
        self.activity = activity
        self.signals = WorkerSignals()

    def run(self) -> None:
        attempts = self.retries + 1
        for attempt in range(1, attempts + 1):
            try:
                self.signals.progress.emit(attempt, attempts, self.activity)
                self.signals.finished.emit(self.operation(*self.args))
                return
            except Exception as error:
                if attempt >= attempts or not retryable_connection_error(error):
                    self.signals.failed.emit(str(error))
                    return
                for remaining in range(self.retry_cooldown, 0, -1):
                    self.signals.progress.emit(attempt + 1, attempts, f"Provider response stumbled—retrying in {remaining}s…")
                    time.sleep(1)
                if self.retry_cooldown == 0:
                    self.signals.progress.emit(attempt + 1, attempts, "Provider response stumbled—retrying now…")


class TimelineListWidget(QListWidget):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scroll_animation = None
        self._scroll_target = 0
        self.setAcceptDrops(True)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.horizontalScrollBar().setSingleStep(24)
        self.horizontalScrollBar().sliderPressed.connect(self._cancel_smooth_scroll)

    def wheelEvent(self, event) -> None:
        bar = self.horizontalScrollBar()
        pixel_delta = event.pixelDelta()
        pixels = pixel_delta.x() or pixel_delta.y()
        if pixels:
            self._cancel_smooth_scroll()
            bar.setValue(bar.value() - pixels)
            self._scroll_target = bar.value()
            event.accept()
            return
        angle_delta = event.angleDelta()
        degrees = angle_delta.x() or angle_delta.y()
        if not degrees:
            super().wheelEvent(event)
            return
        base = self._scroll_target if self._scroll_animation else bar.value()
        distance = round(-(degrees / 120) * 120)
        self._scroll_target = max(bar.minimum(), min(bar.maximum(), base + distance))
        if self._scroll_animation:
            self._scroll_animation.stop()
        animation = QVariantAnimation(self)
        animation.setDuration(190)
        animation.setStartValue(bar.value())
        animation.setEndValue(self._scroll_target)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(lambda value: bar.setValue(round(value)))
        animation.finished.connect(self._smooth_scroll_finished)
        self._scroll_animation = animation
        animation.start()
        event.accept()

    def _cancel_smooth_scroll(self) -> None:
        if self._scroll_animation:
            self._scroll_animation.stop()
            self._scroll_animation = None
        self._scroll_target = self.horizontalScrollBar().value()

    def _smooth_scroll_finished(self) -> None:
        self._scroll_animation = None
        self._scroll_target = self.horizontalScrollBar().value()

    def mousePressEvent(self, event) -> None:
        self._cancel_smooth_scroll()
        super().mousePressEvent(event)

    @staticmethod
    def _media_paths(event) -> list[str]:
        if not event.mimeData().hasUrls():
            return []
        supported = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".webm"}
        return [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in supported]

    def dragEnterEvent(self, event) -> None:
        if self._media_paths(event):
            self.setProperty("dropActive", True)
            self.style().unpolish(self)
            self.style().polish(self)
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self._media_paths(event):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:
        self._clear_drop_state()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        paths = self._media_paths(event)
        self._clear_drop_state()
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _clear_drop_state(self) -> None:
        self.setProperty("dropActive", False)
        self.style().unpolish(self)
        self.style().polish(self)


class AudioTrackScroll(QScrollArea):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setProperty("dropActive", False)

    @staticmethod
    def _audio_paths(event) -> list[str]:
        if not event.mimeData().hasUrls():
            return []
        supported = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
        return [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in supported]

    def dragEnterEvent(self, event) -> None:
        if self._audio_paths(event):
            self._set_drop_state(True)
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self._audio_paths(event):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:
        self._set_drop_state(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        paths = self._audio_paths(event)
        self._set_drop_state(False)
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _set_drop_state(self, active: bool) -> None:
        self.setProperty("dropActive", active)
        self.style().unpolish(self)
        self.style().polish(self)


class ProjectListWidget(QListWidget):
    drag_started = Signal()
    order_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._press_position = None
        self._drag_row = -1
        self._reordering = False
        self._scroll_animation = None
        self._scroll_target = 0
        self.setDragDropMode(QListWidget.DragDropMode.NoDragDrop)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)

    def wheelEvent(self, event) -> None:
        bar = self.verticalScrollBar()
        pixels = event.pixelDelta().y()
        if pixels:
            if self._scroll_animation:
                self._scroll_animation.stop()
                self._scroll_animation = None
            bar.setValue(bar.value() - pixels)
            self._scroll_target = bar.value()
            event.accept()
            return
        degrees = event.angleDelta().y()
        if not degrees:
            super().wheelEvent(event)
            return
        base = self._scroll_target if self._scroll_animation else bar.value()
        distance = round(-(degrees / 120) * 96)
        self._scroll_target = max(bar.minimum(), min(bar.maximum(), base + distance))
        if self._scroll_animation:
            self._scroll_animation.stop()
        animation = QVariantAnimation(self)
        animation.setDuration(175)
        animation.setStartValue(bar.value())
        animation.setEndValue(self._scroll_target)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(lambda value: bar.setValue(round(value)))
        animation.finished.connect(self._scroll_finished)
        self._scroll_animation = animation
        animation.start()
        event.accept()

    def _scroll_finished(self) -> None:
        self._scroll_animation = None
        self._scroll_target = self.verticalScrollBar().value()

    def mousePressEvent(self, event) -> None:
        self._press_position = event.position().toPoint()
        self._drag_row = self.indexAt(self._press_position).row()
        self._reordering = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not (event.buttons() & Qt.MouseButton.LeftButton) or self._drag_row < 0 or self._press_position is None:
            super().mouseMoveEvent(event)
            return
        if not self._reordering:
            distance = (event.position().toPoint() - self._press_position).manhattanLength()
            if distance < QApplication.startDragDistance():
                return
            self._reordering = True
            self.drag_started.emit()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
        position = event.position().toPoint()
        edge = 34
        if position.y() < edge:
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - 24)
        elif position.y() > self.viewport().height() - edge:
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() + 24)

        hovered = self.indexAt(position)
        target = hovered.row() if hovered.isValid() else self._drag_row
        if target != self._drag_row:
            item = self.takeItem(self._drag_row)
            self.insertItem(target, item)
            item.setSelected(True)
            self._drag_row = target
            self.order_changed.emit()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        was_reordering = self._reordering
        self._press_position = None
        self._drag_row = -1
        self._reordering = False
        self.viewport().unsetCursor()
        if was_reordering:
            self.order_changed.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class MagicSpinner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.angle = 0
        self.scale_factor = 1.0
        self.set_scale(1.0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance)
        self.timer.start(45)

    def advance(self) -> None:
        self.angle = (self.angle + 8) % 360
        self.update()

    def set_scale(self, scale: float) -> None:
        self.scale_factor = max(.75, min(2.0, scale))
        side = round(150 * self.scale_factor)
        self.setFixedSize(side, side)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = self.scale_factor
        center = self.rect().center()
        painter.translate(center)
        painter.rotate(self.angle)
        for index in range(10):
            painter.save()
            painter.rotate(index * 36)
            alpha = 55 + index * 20
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(90, 176, 235, min(255, alpha)))
            painter.drawEllipse(QRectF(-4 * scale, -61 * scale, 8 * scale, 18 * scale))
            painter.restore()
        painter.rotate(-self.angle)
        painter.setPen(QPen(QColor("#d9efff"), 3 * scale))
        painter.setBrush(QColor("#27343c"))
        painter.drawRoundedRect(QRectF(-31 * scale, -23 * scale, 62 * scale, 46 * scale), 8 * scale, 8 * scale)
        painter.setPen(QPen(QColor("#79c8ff"), 2 * scale))
        for x in (-19, 0, 19):
            painter.drawLine(round(x * scale), round(-16 * scale), round((x + 8) * scale), round(-6 * scale))
            painter.drawLine(round((x + 8) * scale), round(-6 * scale), round(x * scale), round(4 * scale))
        painter.setPen(QColor("#fff2a8"))
        painter.setFont(self.font())
        painter.drawText(QRectF(-30 * scale, 6 * scale, 60 * scale, 15 * scale), Qt.AlignmentFlag.AlignCenter, "DIRECTING")


class MagicBuildOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("magicOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet("#magicOverlay{background:rgba(5,8,10,205)} #magicOverlayPanel{background:#20282c;border:2px solid #4f83ae;border-radius:12px}")
        layout = QVBoxLayout(self)
        self.panel = QFrame(self)
        self.panel.setObjectName("magicOverlayPanel")
        self.panel_layout = QVBoxLayout(self.panel)
        self.panel_layout.addStretch()
        self.spinner = MagicSpinner(self.panel)
        self.spinner.timer.stop()
        self.panel_layout.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignCenter)
        self.title = QLabel("✦ Magic Build is directing your sequence")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setObjectName("magicOverlayTitle")
        self.detail = QLabel("Analyzing frames and directing motion…")
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail.setWordWrap(True)
        self.attempt = QLabel("")
        self.attempt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.attempt.setObjectName("muted")
        self.panel_layout.addWidget(self.title)
        self.panel_layout.addWidget(self.detail)
        self.panel_layout.addWidget(self.attempt)
        self.panel_layout.addStretch()
        layout.addWidget(self.panel, 0, Qt.AlignmentFlag.AlignCenter)
        scale = parent.settings.value("ui_text_scale", 100, int) / 100 if parent and hasattr(parent, "settings") else 1.0
        self.set_scale(scale)
        self.hide()

    def set_scale(self, scale: float) -> None:
        scale = max(.75, min(2.0, scale))
        self.panel.setFixedSize(round(390 * scale), round(290 * scale))
        self.panel_layout.setContentsMargins(round(28 * scale), round(20 * scale), round(28 * scale), round(24 * scale))
        self.spinner.set_scale(scale)

    def update_attempt(self, current: int, total: int, detail: str) -> None:
        self.detail.setText(detail)
        self.attempt.setText(f"Attempt {current} of {total}" if total > 1 else "")

    def show_overlay(self) -> None:
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.spinner.timer.start(45)
        self.show()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.grabKeyboard()

    def hide_overlay(self) -> None:
        self.releaseKeyboard()
        self.spinner.timer.stop()
        self.hide()

    def mousePressEvent(self, event) -> None:
        event.accept()

    def keyPressEvent(self, event) -> None:
        event.accept()


class TileLoadingSpinner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.phase = 0
        self.scale_factor = 1.0
        self.set_scale(1.0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance)

    def set_scale(self, scale: float) -> None:
        self.scale_factor = max(.75, min(2.0, scale))
        self.setFixedSize(round(104 * self.scale_factor), round(58 * self.scale_factor))
        self.update()

    def advance(self) -> None:
        self.phase = (self.phase + 1) % 12
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = self.scale_factor
        active = (self.phase // 4) % 3
        for index, x in enumerate((4, 38, 72)):
            lift = -4 if index == active else 0
            rect = QRectF(x * scale, (10 + lift) * scale, 28 * scale, 38 * scale)
            painter.setPen(QPen(QColor("#9edaff") if index == active else QColor("#56646c"), 2 * scale))
            painter.setBrush(QColor("#315f7b") if index == active else QColor("#252d31"))
            painter.drawRoundedRect(rect, 4 * scale, 4 * scale)
            painter.setPen(QPen(QColor("#d8f1ff") if index == active else QColor("#78868d"), 2 * scale))
            painter.drawLine(round((x + 6) * scale), round((37 + lift) * scale), round((x + 13) * scale), round((29 + lift) * scale))
            painter.drawLine(round((x + 13) * scale), round((29 + lift) * scale), round((x + 22) * scale), round((39 + lift) * scale))
            painter.drawEllipse(QRectF((x + 18) * scale, (17 + lift) * scale, 4 * scale, 4 * scale))


class TimelineLoadingOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background:rgba(9,13,15,220);color:#d9efff")
        layout = QVBoxLayout(self)
        layout.addStretch()
        self.spinner = TileLoadingSpinner(self)
        layout.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignCenter)
        label = QLabel("Loading timeline tiles…")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("font-weight:bold;color:#d9efff")
        layout.addWidget(label)
        layout.addStretch()
        self.hide()

    def set_scale(self, scale: float) -> None:
        self.spinner.set_scale(scale)

    def show_loading(self) -> None:
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.spinner.timer.start(80)
        self.show()

    def hide_loading(self) -> None:
        self.spinner.timer.stop()
        self.hide()


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
        label_width = painter.fontMetrics().horizontalAdvance("60.00") + 8
        label_interval = max(1, (label_width + self.scale - 1) // self.scale)
        for second in range(61):
            x = second * self.scale - self.offset
            if x < -30 or x > self.width() + 30:
                continue
            painter.setPen(QPen(QColor("#303538")))
            painter.drawLine(x, 12, x, 24)
            if second == 0 or second % label_interval == 0:
                painter.setPen(QColor("#ff5757") if second == 0 else QColor("#788186"))
                painter.drawText(x + 4, 16, "0" if second == 0 else f"{second}.00")


class ResizeHandle(QFrame):
    preview = Signal(float)
    finished = Signal()

    def __init__(self, duration: float, pixels_per_second: int, maximum_duration: float = 12.0):
        super().__init__()
        self.duration = duration
        self.start_duration = duration
        self.start_x: float | None = None
        self.pixels_per_second = pixels_per_second
        self.maximum_duration = maximum_duration
        self.setObjectName("resizeHandle")
        # Keep a comfortable hit target while drawing only a slim dotted grip.
        self.setFixedWidth(12)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setToolTip("Drag to resize segment (1 second minimum)")

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        active = self.start_x is not None
        color = QColor("#b9e7ff") if active else QColor("#84aabd") if self.underMouse() else QColor("#52636c")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        diameter = 3.0
        spacing = 7.0
        center_x = self.width() / 2
        center_y = self.height() / 2
        for index in range(-3, 4):
            painter.drawEllipse(QRectF(center_x - diameter / 2, center_y + index * spacing - diameter / 2, diameter, diameter))

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_x = event.globalPosition().x()
            self.start_duration = self.duration
            self.grabMouse()
            self.update()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.start_x is not None:
            value = max(1, min(self.maximum_duration, round((self.start_duration + (event.globalPosition().x() - self.start_x) / self.pixels_per_second) * 2) / 2))
            if value != self.duration:
                self.duration = value
                self.preview.emit(value)
            event.accept()

    def mouseReleaseEvent(self, event):
        if self.start_x is not None:
            self.start_x = None
            self.releaseMouse()
            self.update()
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
        self.scale_factor = 1.0
        self.setObjectName("timelineHeightHandle")
        self.set_scale(1.0)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip("Drag down to enlarge timeline previews")

    def set_scale(self, scale: float) -> None:
        self.scale_factor = max(.75, min(2.0, scale))
        self.setFixedHeight(round(14 * self.scale_factor))
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        active = self.start_y is not None
        color = QColor("#9fd8fa") if active else QColor("#71838d") if self.underMouse() else QColor("#4f5c63")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        diameter = max(3.0, 3.2 * self.scale_factor)
        spacing = 8 * self.scale_factor
        center_x = self.width() / 2
        center_y = self.height() / 2
        for index in range(-3, 4):
            painter.drawEllipse(QRectF(center_x + index * spacing - diameter / 2, center_y - diameter / 2, diameter, diameter))

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_y = event.globalPosition().y()
            self.start_height = self.current_height
            self.grabMouse()
            self.update()
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
            self.update()
            self.finished.emit()
            event.accept()


class DottedPromptSplitterHandle(QSplitterHandle):
    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = getattr(self.splitter(), "scale_factor", 1.0)
        color = QColor("#71838d") if self.underMouse() else QColor("#4f5c63")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        diameter = max(3.0, 3.2 * scale)
        spacing = 8 * scale
        center_x = self.width() / 2
        center_y = self.height() / 2
        for index in range(-3, 4):
            painter.drawEllipse(QRectF(center_x + index * spacing - diameter / 2, center_y - diameter / 2, diameter, diameter))

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class DottedPromptSplitter(QSplitter):
    def __init__(self, parent=None):
        super().__init__(Qt.Orientation.Vertical, parent)
        self.scale_factor = 1.0
        self.setObjectName("promptSplitter")
        self.setChildrenCollapsible(False)
        self.set_scale(1.0)

    def createHandle(self) -> QSplitterHandle:
        return DottedPromptSplitterHandle(self.orientation(), self)

    def set_scale(self, scale: float) -> None:
        self.scale_factor = max(.75, min(2.0, scale))
        self.setHandleWidth(round(14 * self.scale_factor))
        for index in range(1, self.count()):
            self.handle(index).update()


class SegmentCard(QFrame):
    duration_changed = Signal(float)
    delete_requested = Signal()
    resize_finished = Signal()

    def __init__(self, segment: Segment, preview_height: int, pixels_per_second: int, maximum_duration: float = 12.0):
        super().__init__()
        self.segment = segment
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setObjectName("segmentCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.outer_layout = QHBoxLayout(self)
        self.outer_layout.setContentsMargins(0, 0, 0, 0)
        self.outer_layout.setSpacing(0)
        self.content = QWidget()
        self.content.setObjectName("segmentCardBody")
        self.content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.content.setCursor(Qt.CursorShape.OpenHandCursor)
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(1)
        badges = QHBoxLayout()
        badges.setContentsMargins(0, 0, 0, 0)
        kind = QLabel("TEXT" if segment.kind == "text" else ("WEBM" if segment.kind == "video" else "IMAGE"))
        kind.setObjectName("mediaBadge")
        self.role_badge = QLabel("PROMPT" if segment.kind == "text" else segment.role.upper())
        self.role_badge.setObjectName("roleBadge")
        close = QPushButton("×")
        close.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        close.setObjectName("tileDelete")
        close.setFixedSize(20, 20)
        close.setCursor(Qt.CursorShape.ArrowCursor)
        close.clicked.connect(self.delete_requested)
        badges.addWidget(kind)
        badges.addStretch()
        badges.addWidget(self.role_badge)
        badges.addWidget(close)
        layout.addLayout(badges)
        self.preview = QLabel()
        self.preview.setObjectName("segmentPreview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.source_pixmap = QPixmap(segment.preview_path) if segment.preview_path else QPixmap()
        if segment.kind == "text":
            self.preview.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            self.preview.setMargin(8)
            self.preview.setWordWrap(True)
            self.set_text_preview(segment.prompt)
        layout.addWidget(self.preview, 1)
        title = QLabel(segment.name if segment.kind == "text" else (segment.prompt or segment.name))
        title.setToolTip(segment.name)
        title.setWordWrap(False)
        title.setObjectName("tileTitle")
        layout.addWidget(title)
        self.duration_label = QLabel(f"{segment.duration:.1f}s")
        self.duration_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.duration_label.setObjectName("tileDuration")
        layout.addWidget(self.duration_label)
        self.outer_layout.addWidget(self.content, 1)
        self.resize_handle = ResizeHandle(segment.duration, pixels_per_second, maximum_duration)
        self.resize_handle.preview.connect(self._preview_duration)
        self.resize_handle.finished.connect(self.resize_finished)
        self.outer_layout.addWidget(self.resize_handle)
        self.update_layout(preview_height, pixels_per_second)

    def set_timeline_edges(self, first: bool, last: bool) -> None:
        self.outer_layout.setContentsMargins(0 if first else 1, 0, 0 if last else 1, 0)

    def set_role(self, role: str) -> None:
        """Update the visible role without recreating the timeline card."""
        if self.segment.kind == "text":
            return
        self.segment.role = role
        self.role_badge.setText(role.upper())

    def set_text_preview(self, text: str) -> None:
        if self.segment.kind == "text":
            self.preview.setText(text or "Enter text prompt…")

    def update_layout(self, preview_height: int, pixels_per_second: int) -> None:
        height = max(80, preview_height)
        width = max(36, int(self.segment.duration * pixels_per_second) - 14)
        self.preview.setMinimumHeight(height)
        if not self.source_pixmap.isNull():
            scaled = self.source_pixmap.scaled(
                width, height, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            left = max(0, (scaled.width() - width) // 2)
            top = max(0, (scaled.height() - height) // 2)
            self.preview.setPixmap(scaled.copy(left, top, width, height))
        self.resize_handle.pixels_per_second = pixels_per_second
        self.resize_handle.duration = self.segment.duration
        self.duration_label.setText(f"{self.segment.duration:.1f}s")

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


class AudioClipCard(QFrame):
    moved = Signal(str, float)
    trimmed = Signal(str, float, float, int)
    menu_requested = Signal(str, object)
    HANDLE_WIDTH = 9

    def __init__(self, segment: AudioSegment, pixels_per_second: int, parent=None):
        super().__init__(parent)
        self.segment = segment
        self.pixels_per_second = pixels_per_second
        self.drag_origin = None
        self.start_origin = 0.0
        self.duration_origin = 0.0
        self.trim_origin = 0
        self.resize_edge: str | None = None
        self.preview_start: float | None = None
        self.preview_duration: float | None = None
        self.preview_trim: int | None = None
        self.setObjectName("audioClip")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self.setToolTip(self.tooltip_text())
        self.update_cursor()
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda point: self.menu_requested.emit(segment.id, self.mapToGlobal(point)))

    def tooltip_text(self) -> str:
        if self.segment.coupled_to:
            state = "Coupled to video — right-click to decouple before trimming or moving"
        else:
            state = "Drag the waveform to move; drag either edge to trim"
        end = self.segment.start + self.segment.duration
        return (
            f"{self.segment.name}\n{state}\n"
            f"Start {self.segment.start:.2f}s · End {end:.2f}s · Length {self.segment.duration:.2f}s"
        )

    def edge_at(self, x: float) -> str | None:
        if self.segment.coupled_to:
            return None
        if x <= self.HANDLE_WIDTH:
            return "left"
        if x >= self.width() - self.HANDLE_WIDTH:
            return "right"
        return None

    def update_cursor(self, x: float | None = None) -> None:
        if self.segment.coupled_to:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif self.resize_edge or (x is not None and self.edge_at(x)):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif self.drag_origin is not None:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        mid = self.height() // 2 + 5
        all_peaks = self.segment.waveform_peaks or [0.0]
        source_frames = max(1, self.segment.audio_duration_frames)
        trim_start = self.segment.trim_start if self.preview_trim is None else self.preview_trim
        duration = self.segment.duration if self.preview_duration is None else self.preview_duration
        start = self.segment.start if self.preview_start is None else self.preview_start
        first_peak = min(len(all_peaks) - 1, int(trim_start / source_frames * len(all_peaks)))
        last_frame = trim_start + round(duration * FPS)
        last_peak = max(first_peak + 1, min(len(all_peaks), int(last_frame / source_frames * len(all_peaks)) + 1))
        peaks = all_peaks[first_peak:last_peak] or [0.0]
        painter.setPen(QPen(QColor("#65b7d8"), 1))
        usable = max(1, self.width() - 8)
        for x in range(4, self.width() - 4, 2):
            index = min(len(peaks) - 1, int((x - 4) / usable * len(peaks)))
            height = max(1, int(peaks[index] * max(3, self.height() * .34)))
            painter.drawLine(x, mid - height, x, mid + height)
        painter.setPen(QColor("#e5f4fb"))
        painter.drawText(12, 16, self.segment.name)
        painter.setPen(QColor("#8da4ae"))
        marker = "LINKED" if self.segment.coupled_to else f"{start:.2f}s–{start + duration:.2f}s"
        painter.drawText(12, self.height() - 7, marker)
        if not self.segment.coupled_to:
            painter.fillRect(0, 0, self.HANDLE_WIDTH, self.height(), QColor("#2185a8"))
            painter.fillRect(self.width() - self.HANDLE_WIDTH, 0, self.HANDLE_WIDTH, self.height(), QColor("#2185a8"))
            painter.setPen(QPen(QColor("#d9f5ff"), 1))
            for edge_x in (3, self.width() - 6):
                painter.drawLine(edge_x, 20, edge_x, self.height() - 20)
                painter.drawLine(edge_x + 3, 20, edge_x + 3, self.height() - 20)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and not self.segment.coupled_to:
            self.resize_edge = self.edge_at(event.position().x())
            self.drag_origin = event.globalPosition().toPoint()
            self.start_origin = self.segment.start
            self.duration_origin = self.segment.duration
            self.trim_origin = self.segment.trim_start
            self.preview_start = self.start_origin
            self.preview_duration = self.duration_origin
            self.preview_trim = self.trim_origin
            self.update_cursor(event.position().x())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.drag_origin is not None:
            delta_pixels = event.globalPosition().toPoint().x() - self.drag_origin.x()
            delta_frames = round(delta_pixels / max(1, self.pixels_per_second) * FPS)
            delta_seconds = delta_frames / FPS
            minimum = 1 / FPS
            if self.resize_edge == "left":
                maximum_delta = min(
                    self.duration_origin - minimum,
                    max(0, self.segment.audio_duration_frames - self.trim_origin - 1) / FPS,
                )
                minimum_delta = max(-self.start_origin, -self.trim_origin / FPS)
                delta_seconds = max(minimum_delta, min(maximum_delta, delta_seconds))
                delta_frames = round(delta_seconds * FPS)
                start = self.start_origin + delta_frames / FPS
                duration = self.duration_origin - delta_frames / FPS
                self.preview_start = start
                self.preview_duration = duration
                self.preview_trim = self.trim_origin + delta_frames
                self.move(round(start * self.pixels_per_second), self.y())
                self.resize(max(1, round(duration * self.pixels_per_second)), self.height())
                self.update()
            elif self.resize_edge == "right":
                available = max(1, self.segment.audio_duration_frames - self.trim_origin) / FPS
                duration = max(minimum, min(available, MAX_SECONDS - self.start_origin, self.duration_origin + delta_seconds))
                self.preview_duration = duration
                self.resize(max(1, round(duration * self.pixels_per_second)), self.height())
                self.update()
            else:
                start = max(0.0, min(MAX_SECONDS - self.duration_origin, self.start_origin + delta_seconds))
                self.move(round(start * self.pixels_per_second), self.y())
            event.accept()
            return
        self.update_cursor(event.position().x())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self.drag_origin is None:
            self.update_cursor()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.drag_origin is not None:
            if self.resize_edge == "left":
                start = self.preview_start if self.preview_start is not None else self.start_origin
                duration = self.preview_duration if self.preview_duration is not None else self.duration_origin
                trim_start = self.preview_trim if self.preview_trim is not None else self.trim_origin
                self.trimmed.emit(self.segment.id, start, duration, trim_start)
            elif self.resize_edge == "right":
                duration = self.preview_duration if self.preview_duration is not None else self.duration_origin
                available = max(1, self.segment.audio_duration_frames - self.trim_origin) / FPS
                duration = min(duration, available, MAX_SECONDS - self.start_origin)
                self.trimmed.emit(self.segment.id, self.start_origin, duration, self.trim_origin)
            else:
                start = max(0.0, min(MAX_SECONDS - self.segment.duration, self.x() / max(1, self.pixels_per_second)))
                self.moved.emit(self.segment.id, round(start * FPS) / FPS)
            self.drag_origin = None
            self.resize_edge = None
            self.preview_start = None
            self.preview_duration = None
            self.preview_trim = None
            self.update_cursor(event.position().x())
            event.accept()
            return
        super().mouseReleaseEvent(event)


class SettingsDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Application Settings")
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
        self.timeout = QSpinBox()
        self.timeout.setRange(30, 900)
        self.timeout.setSingleStep(10)
        self.timeout.setSuffix(" seconds")
        self.timeout.setValue(settings.value("api_timeout", 400, int))
        self.retries = QSpinBox()
        self.retries.setRange(0, 10)
        self.retries.setValue(settings.value("api_retries", 2, int))
        self.retries.setToolTip("Additional attempts after the initial API request")
        self.retry_cooldown = QSpinBox()
        self.retry_cooldown.setRange(0, 300)
        self.retry_cooldown.setSingleStep(5)
        self.retry_cooldown.setSuffix(" seconds")
        self.retry_cooldown.setValue(settings.value("api_retry_cooldown", 10, int))
        self.retry_cooldown.setToolTip("Time to wait before each retry; set to 0 to retry immediately")
        text_scale = settings.value("ui_text_scale", 100, int)
        self.ui_text_scale = QSlider(Qt.Orientation.Horizontal)
        self.ui_text_scale.setRange(75, 200)
        self.ui_text_scale.setSingleStep(5)
        self.ui_text_scale.setPageStep(25)
        self.ui_text_scale.setTickInterval(25)
        self.ui_text_scale.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.ui_text_scale.setValue(text_scale)
        self.ui_text_scale.setToolTip("Scale text throughout the main application window")
        self.ui_text_scale_value = QLabel(f"{text_scale}%")
        self.ui_text_scale_value.setMinimumWidth(44)
        self.ui_text_scale_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.ui_text_scale.valueChanged.connect(lambda value: self.ui_text_scale_value.setText(f"{value}%"))
        text_scale_row = QWidget()
        text_scale_layout = QHBoxLayout(text_scale_row)
        text_scale_layout.setContentsMargins(0, 0, 0, 0)
        text_scale_layout.addWidget(self.ui_text_scale, 1)
        text_scale_layout.addWidget(self.ui_text_scale_value)
        downloads = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or str(Path.home())
        self.segment_save_dir = QLineEdit(str(settings.value("segment_export_dir", settings.value("segment_save_dir", downloads))))
        self.segment_save_browse = QPushButton("Browse…")
        self.segment_save_browse.clicked.connect(self.choose_segment_save_directory)
        save_directory_row = QWidget()
        save_directory_layout = QHBoxLayout(save_directory_row)
        save_directory_layout.setContentsMargins(0, 0, 0, 0)
        save_directory_layout.addWidget(self.segment_save_dir, 1)
        save_directory_layout.addWidget(self.segment_save_browse)
        form.addRow("Provider", self.provider)
        form.addRow("Gemini model", self.model)
        form.addRow("Gemini API key", self.gemini)
        form.addRow("OpenAI API key", self.openai)
        form.addRow("API timeout", self.timeout)
        form.addRow("Connection retries", self.retries)
        form.addRow("Retry cooldown", self.retry_cooldown)
        form.addRow("UI text scale (DPI)", text_scale_row)
        form.addRow("Default segment export folder", save_directory_row)
        form.addRow("", self.remember)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        geometry = settings.value("dialogs/settings_geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def accept(self) -> None:
        self.settings.setValue("provider", self.provider.currentText())
        self.settings.setValue("gemini_model", self.model.currentText())
        self.settings.setValue("remember_keys", self.remember.isChecked())
        self.settings.setValue("api_timeout", self.timeout.value())
        self.settings.setValue("api_retries", self.retries.value())
        self.settings.setValue("api_retry_cooldown", self.retry_cooldown.value())
        self.settings.setValue("ui_text_scale", self.ui_text_scale.value())
        self.settings.setValue("segment_export_dir", self.segment_save_dir.text().strip())
        self.settings.setValue("dialogs/settings_geometry", self.saveGeometry())
        if self.remember.isChecked():
            self.settings.setValue("gemini_key", self.gemini.text().strip())
            self.settings.setValue("openai_key", self.openai.text().strip())
        else:
            self.settings.remove("gemini_key")
            self.settings.remove("openai_key")
        self.parent().session_keys = {"gemini": self.gemini.text().strip(), "openai": self.openai.text().strip()}
        super().accept()

    def choose_segment_save_directory(self) -> None:
        selected = choose_directory(self, "Choose default segment export folder", self.segment_save_dir.text().strip() or str(Path.home()))
        if selected:
            self.segment_save_dir.setText(selected)

    def reject(self) -> None:
        self.settings.setValue("dialogs/settings_geometry", self.saveGeometry())
        super().reject()


class ProjectDetailsDialog(QDialog):
    def __init__(self, suggested_description: str, parent=None, name: str = "", collection: str = "", collections: list[str] | None = None,
                 thumbnail_options: list[tuple[str, str]] | None = None, thumbnail_data: str = "", thumbnail_source: str = ""):
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
        self.thumbnail_data = thumbnail_data
        self.thumbnail_source = thumbnail_source
        self.thumbnail_options = thumbnail_options or []
        thumbnail_box = QWidget()
        thumbnail_layout = QVBoxLayout(thumbnail_box)
        thumbnail_layout.setContentsMargins(0, 0, 0, 0)
        self.thumbnail_list = QListWidget()
        self.thumbnail_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.thumbnail_list.setFlow(QListWidget.Flow.LeftToRight)
        self.thumbnail_list.setWrapping(False)
        self.thumbnail_list.setIconSize(QSize(104, 104))
        self.thumbnail_list.setFixedHeight(148)
        self.thumbnail_list.setSpacing(6)
        for index, (label, value) in enumerate(self.thumbnail_options):
            item = QListWidgetItem(QIcon(self._thumbnail_pixmap(value)), label)
            item.setData(Qt.ItemDataRole.UserRole, (f"segment:{index}", value))
            item.setSizeHint(QSize(116, 132))
            self.thumbnail_list.addItem(item)
            if self.thumbnail_source == f"segment:{index}" or (not self.thumbnail_source and index == 0):
                self.thumbnail_list.setCurrentItem(item)
        if self.thumbnail_source == "custom" and self.thumbnail_data:
            item = QListWidgetItem(QIcon(self._thumbnail_pixmap(self.thumbnail_data)), "Custom")
            item.setData(Qt.ItemDataRole.UserRole, ("custom", self.thumbnail_data))
            item.setSizeHint(QSize(116, 132))
            self.thumbnail_list.addItem(item)
            self.thumbnail_list.setCurrentItem(item)
        self.custom_thumbnail_button = QPushButton("Upload custom thumbnail…")
        self.custom_thumbnail_button.clicked.connect(self.choose_custom_thumbnail)
        thumbnail_layout.addWidget(self.thumbnail_list)
        thumbnail_layout.addWidget(self.custom_thumbnail_button)
        form.addRow("Name", self.name)
        form.addRow("Description", self.description)
        form.addRow("Collection", self.collection)
        form.addRow("Thumbnail", thumbnail_box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        geometry = parent.settings.value("dialogs/project_details_geometry") if parent else None
        if geometry:
            self.restoreGeometry(geometry)

    def accept(self) -> None:
        if not self.name.text().strip():
            QMessageBox.warning(self, "Project name required", "Enter a name for this project.")
            return
        self.parent().settings.setValue("dialogs/project_details_geometry", self.saveGeometry())
        super().accept()

    def reject(self) -> None:
        self.parent().settings.setValue("dialogs/project_details_geometry", self.saveGeometry())
        super().reject()

    def collection_name(self) -> str:
        value = self.collection.currentText().strip()
        return "" if value == "No collection" else value

    @staticmethod
    def _thumbnail_pixmap(value: str) -> QPixmap:
        pixmap = pixmap_from_data_url(value)
        if pixmap.isNull():
            return pixmap
        scaled = pixmap.scaled(104, 104, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        return scaled.copy(max(0, (scaled.width() - 104) // 2), max(0, (scaled.height() - 104) // 2), 104, 104)

    def choose_custom_thumbnail(self) -> None:
        initial = self.parent().settings.value("last_media_dir", str(Path.home()))
        paths = choose_media_files(self, False, str(initial))
        if not paths:
            return
        path = paths[0]
        self.parent().settings.setValue("last_media_dir", str(Path(path).parent))
        custom_data = data_url(path, max_edge=720, quality=90)
        if not custom_data:
            QMessageBox.warning(self, "Thumbnail unavailable", "That image could not be used as a thumbnail.")
            return
        for row in range(self.thumbnail_list.count()):
            item = self.thumbnail_list.item(row)
            if (item.data(Qt.ItemDataRole.UserRole) or ("", ""))[0] == "custom":
                self.thumbnail_list.takeItem(row)
                break
        item = QListWidgetItem(QIcon(self._thumbnail_pixmap(custom_data)), "Custom")
        item.setData(Qt.ItemDataRole.UserRole, ("custom", custom_data))
        item.setSizeHint(QSize(116, 132))
        self.thumbnail_list.addItem(item)
        self.thumbnail_list.setCurrentItem(item)

    def selected_thumbnail(self) -> tuple[str, str]:
        item = self.thumbnail_list.currentItem()
        if item:
            return item.data(Qt.ItemDataRole.UserRole)
        if self.thumbnail_options:
            return "segment:0", self.thumbnail_options[0][1]
        return self.thumbnail_source, self.thumbnail_data


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LTX Director - Director")
        self.setWindowIcon(QIcon(str(files("ltx_prompt_director").joinpath("assets/icon.png"))))
        self.resize(1450, 900)
        self.settings = QSettings()
        self._settings_sync_timer = QTimer(self)
        self._settings_sync_timer.setSingleShot(True)
        self._settings_sync_timer.setInterval(250)
        self._settings_sync_timer.timeout.connect(self.settings.sync)
        self.session_keys = {"gemini": self.settings.value("gemini_key", ""), "openai": self.settings.value("openai_key", "")}
        self.segments: list[Segment] = []
        self.audio_segments: list[AudioSegment] = []
        self._syncing_audio_scroll = False
        self.current_project_id: str | None = None
        self.current_project_name = "Untitled"
        self.project_dirty = False
        self.project_sessions: dict[str, dict] = {}
        self.current_collection: str | None = None
        self.autofit_tail_extension = 0
        self._restoring_layout = True
        self.project_panel_width = max(280, min(900, self.settings.value("project_panel_width", 330, int)))
        saved_icon_size = self.settings.value("project_icon_size", 230, int)
        self.project_icon_size = min((96, 156, 230), key=lambda size: abs(size - saved_icon_size))
        self.pixels_per_second = 65
        self.timeline_height = max(184, min(430, self.settings.value("timeline_panel_height", 184, int)))
        self.thread_pool = QThreadPool.globalInstance()
        self._loading = False
        self._build_ui()
        self._apply_theme()
        self.magic_overlay = MagicBuildOverlay(self)
        geometry = self.settings.value("window/geometry")
        state = self.settings.value("window/state")
        if geometry:
            self.restoreGeometry(geometry)
        if state:
            self.restoreState(state)
        QTimer.singleShot(0, self.restore_project_panel_width)
        QTimer.singleShot(0, self.restore_timeline_panel_height)
        QTimer.singleShot(0, self.restore_last_project)
        self.update_window_title()

    def _build_ui(self) -> None:
        self._build_project_dock()
        toolbar = QToolBar("Project")
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        projects_action = self.project_dock.toggleViewAction()
        projects_action.setText("Projects")
        toolbar.addAction(projects_action)
        toolbar.addSeparator()
        action_groups = [
            (("New Project", self.new_project),),
            (("Open", self.import_ltx), ("Import", self.import_project)),
            (("Delete selected", self.delete_selected),),
        ]
        for group_index, group in enumerate(action_groups):
            for label, callback in group:
                action = QAction(label, self)
                action.triggered.connect(callback)
                toolbar.addAction(action)
            if group_index < len(action_groups) - 1:
                toolbar.addSeparator()
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        self.provider_button = QPushButton()
        self.provider_button.setObjectName("toolbarButton")
        self.provider_button.clicked.connect(self.open_settings)
        self.update_provider_button()
        toolbar.addWidget(self.provider_button)
        toolbar.addSeparator()
        for label, callback in [("LTX Director Export", self.export_ltx), ("Project Export", self.export_project)]:
            action = QAction(label, self)
            action.triggered.connect(callback)
            toolbar.addAction(action)
        toolbar.addSeparator()
        current_text_scale = self.settings.value("ui_text_scale", 100, int)
        self.ui_scale_label = QLabel("TEXT")
        self.ui_scale_label.setObjectName("timelineControlLabel")
        self.ui_scale_label.setToolTip("Interface text scale")
        toolbar.addWidget(self.ui_scale_label)
        self.ui_scale_control = QFrame()
        self.ui_scale_control.setObjectName("outputSizeControl")
        ui_scale_layout = QHBoxLayout(self.ui_scale_control)
        ui_scale_layout.setContentsMargins(0, 0, 0, 0)
        self.ui_scale_spin = QSpinBox()
        self.ui_scale_spin.setObjectName("timelineSpin")
        self.ui_scale_spin.setRange(75, 200)
        self.ui_scale_spin.setSingleStep(5)
        self.ui_scale_spin.setSuffix("%")
        self.ui_scale_spin.setValue(current_text_scale)
        self.ui_scale_spin.setToolTip("Adjust interface text size (75–200%)")
        self.ui_scale_spin.valueChanged.connect(self.set_ui_text_scale)
        ui_scale_layout.addWidget(self.ui_scale_spin)
        toolbar.addWidget(self.ui_scale_control)
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
        self.timeline_controls = QHBoxLayout()
        self.timeline_controls.setContentsMargins(10, 6, 10, 5)
        self.timeline_controls.setSpacing(8)
        timeline_title = QLabel("TIMELINE")
        timeline_title.setObjectName("timelineTitle")
        self.timeline_controls.addWidget(timeline_title)
        self.timeline_controls.addStretch()
        output_label = QLabel("OUTPUT")
        output_label.setObjectName("timelineControlLabel")
        self.timeline_controls.addWidget(output_label)
        self.output_size_control = QFrame()
        self.output_size_control.setObjectName("outputSizeControl")
        output_size_layout = QHBoxLayout(self.output_size_control)
        output_size_layout.setContentsMargins(0, 0, 0, 0)
        output_size_layout.setSpacing(0)
        self.output_width = QSpinBox()
        self.output_width.setObjectName("timelineSpin")
        self.output_width.setRange(256, 4096)
        self.output_width.setSingleStep(32)
        self.output_width.setValue(1280)
        self.output_width.setSuffix(" px")
        self.output_width.setToolTip("LTX Director custom output width")
        self.output_width.valueChanged.connect(self.mark_dirty)
        self.output_width.editingFinished.connect(self.normalize_output_dimensions)
        self.output_height = QSpinBox()
        self.output_height.setObjectName("timelineSpin")
        self.output_height.setRange(256, 4096)
        self.output_height.setSingleStep(32)
        self.output_height.setValue(704)
        self.output_height.setSuffix(" px")
        self.output_height.setToolTip("LTX Director custom output height")
        self.output_height.valueChanged.connect(self.mark_dirty)
        self.output_height.editingFinished.connect(self.normalize_output_dimensions)
        resolution_separator = QLabel("×")
        resolution_separator.setObjectName("resolutionSeparator")
        resolution_separator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        output_size_layout.addWidget(self.output_width)
        output_size_layout.addWidget(resolution_separator)
        output_size_layout.addWidget(self.output_height)
        self.timeline_controls.addWidget(self.output_size_control)
        scale_label = QLabel("SCALE")
        scale_label.setObjectName("timelineControlLabel")
        self.timeline_controls.addWidget(scale_label)
        self.timeline_scale = QSlider(Qt.Orientation.Horizontal)
        self.timeline_scale.setRange(20, 160)
        self.timeline_scale.setValue(self.pixels_per_second)
        self.timeline_scale.setFixedWidth(150)
        self.timeline_scale.setToolTip("Timeline pixels per second")
        self.timeline_scale.valueChanged.connect(self.set_timeline_scale)
        self.timeline_controls.addWidget(self.timeline_scale)
        autofit = QPushButton("↔  Auto fit")
        autofit.setObjectName("timelineButton")
        autofit.clicked.connect(self.autofit_timeline)
        self.timeline_controls.addWidget(autofit)
        timeline_layout.addLayout(self.timeline_controls)
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
        self.timeline = TimelineListWidget()
        self.timeline.setObjectName("timeline")
        self.timeline.setProperty("dropActive", False)
        self.timeline.setViewMode(QListWidget.ViewMode.ListMode)
        self.timeline.setFlow(QListWidget.Flow.LeftToRight)
        self.timeline.setWrapping(False)
        self.timeline.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.timeline.setSpacing(0)
        self.timeline.setFixedHeight(self.timeline_height)
        # SOUND owns the one visible timeline scrollbar; MAIN follows the same
        # range/value programmatically and still accepts wheel/trackpad input.
        self.timeline.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.timeline.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.timeline.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.timeline.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.timeline.customContextMenuRequested.connect(self.timeline_menu)
        self.timeline.currentRowChanged.connect(self.load_editor)
        self.timeline.currentRowChanged.connect(self.update_timeline_selection_style)
        self.timeline.itemClicked.connect(self.reload_clicked_segment)
        self.timeline.model().rowsMoved.connect(lambda *_: self.sync_order())
        self.timeline.files_dropped.connect(self.add_media_paths)
        self.timeline.horizontalScrollBar().valueChanged.connect(self.ruler.set_offset)
        self.timeline.horizontalScrollBar().valueChanged.connect(self.sync_audio_scroll)
        self.timeline_loading = TimelineLoadingOverlay(self.timeline.viewport())
        track_row.addWidget(self.timeline, 1)
        self.add_tile = QPushButton("＋\nAdd media\n60.0s available")
        self.add_tile.setObjectName("addTile")
        self.add_tile.clicked.connect(self.add_media)
        self.add_text_tile = QPushButton("＋ Add text")
        self.add_text_tile.setObjectName("addTextTile")
        self.add_text_tile.clicked.connect(self.add_text_segment)
        self.add_tile_wrap = QFrame()
        self.add_tile_wrap.setObjectName("addTileBox")
        self.add_tile_layout = QVBoxLayout(self.add_tile_wrap)
        self.add_tile_layout.setContentsMargins(0, 0, 0, 0)
        self.add_tile_layout.setSpacing(0)
        self.add_tile_layout.addWidget(self.add_tile, 1)
        self.add_tile_layout.addWidget(self.add_text_tile)
        self.add_tile_wrap.setFixedSize(112, max(152, self.timeline_height - 32))
        track_row.addWidget(self.add_tile_wrap, 0, Qt.AlignmentFlag.AlignVCenter)
        timeline_layout.addLayout(track_row)
        audio_row = QHBoxLayout()
        audio_row.setContentsMargins(0, 0, 0, 0)
        audio_row.setSpacing(0)
        audio_label = QLabel("SOUND ◉")
        audio_label.setObjectName("audioTrackLabel")
        audio_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        audio_label.setFixedWidth(145)
        audio_row.addWidget(audio_label)
        self.audio_scroll = AudioTrackScroll()
        self.audio_scroll.setObjectName("audioTrack")
        self.audio_scroll.setWidgetResizable(False)
        self.audio_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.audio_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.audio_scroll.setFixedHeight(88)
        self.audio_canvas = QWidget()
        self.audio_canvas.setObjectName("audioCanvas")
        self.audio_canvas.setFixedHeight(76)
        self.audio_scroll.setWidget(self.audio_canvas)
        self.audio_scroll.files_dropped.connect(self.add_audio_paths)
        self.audio_scroll.horizontalScrollBar().valueChanged.connect(self.audio_scroll_changed)
        audio_row.addWidget(self.audio_scroll, 1)
        audio_end_spacer = QWidget()
        audio_end_spacer.setFixedWidth(128)
        audio_row.addWidget(audio_end_spacer)
        timeline_layout.addLayout(audio_row)
        self.timeline_height_handle = TimelineHeightHandle(self.timeline_height)
        self.timeline_height_handle.height_changed.connect(self.set_timeline_height)
        self.timeline_height_handle.finished.connect(self.finish_timeline_height_resize)
        timeline_layout.addWidget(self.timeline_height_handle)
        outer.addWidget(timeline_shell)

        self.sequence_bar = QLabel()
        self.sequence_bar.setObjectName("sequenceBar")
        outer.addWidget(self.sequence_bar)

        director_panel = QFrame()
        director_panel.setObjectName("directorPanel")
        self.director_controls = QVBoxLayout(director_panel)
        self.director_controls.setContentsMargins(10, 8, 10, 8)
        self.director_controls.setSpacing(7)
        intent_row = QHBoxLayout()
        intent_row.setContentsMargins(0, 0, 0, 0)
        intent_row.setSpacing(8)
        intent_label = QLabel("DIRECTOR'S INTENT")
        intent_label.setObjectName("panelTitle")
        intent_row.addWidget(intent_label)
        self.intent = QTextEdit()
        self.intent.setAcceptRichText(False)
        intent_example = (
            "Example: A lost courier discovers a glowing map, crosses the storm, and reaches the beacon at sunrise. "
            "Describe the narrative, action, pacing, camera, dialogue wording, and ending you want."
        )
        self.intent.setPlaceholderText(intent_example)
        self.intent.setToolTip(intent_example)
        self.intent.textChanged.connect(self.mark_dirty)
        self.sfx = QPushButton("SFX")
        self.sfx.setCheckable(True)
        self.sfx.setObjectName("audioToggle")
        self.sfx.toggled.connect(self.mark_dirty)
        self.spoken_dialog = QPushButton("Spoken Dialog")
        self.spoken_dialog.setCheckable(True)
        self.spoken_dialog.setObjectName("audioToggle")
        self.spoken_dialog.setToolTip("Allow Magic Build to include spoken-dialog direction when supported by the scene")
        self.spoken_dialog.toggled.connect(self.update_spoken_dialog_controls)
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
        for button in (self.sfx, self.spoken_dialog, self.hdr, self.reduce_music, self.magic_button):
            button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        intent_row.addWidget(self.intent, 1)
        intent_row.addWidget(self.magic_button, 0, Qt.AlignmentFlag.AlignTop)
        self.director_controls.addLayout(intent_row)
        planning_row = QHBoxLayout()
        planning_row.setContentsMargins(0, 0, 0, 0)
        planning_row.setSpacing(6)
        planning_label = QLabel("DIRECTION SETTINGS")
        planning_label.setObjectName("groupLabel")
        planning_row.addWidget(planning_label)
        length_label = QLabel("TOTAL LENGTH")
        length_label.setObjectName("groupLabel")
        self.requested_length = QDoubleSpinBox()
        self.requested_length.setObjectName("timelineSpin")
        self.requested_length.setRange(0, MAX_SECONDS)
        self.requested_length.setSingleStep(.5)
        self.requested_length.setDecimals(1)
        self.requested_length.setSuffix(" s")
        self.requested_length.setSpecialValueText("Auto")
        self.requested_length.setToolTip("Requested total sequence length; Auto lets Magic Build choose")
        self.requested_length.valueChanged.connect(self.mark_dirty)
        language_label = QLabel("LANGUAGE")
        language_label.setObjectName("groupLabel")
        self.speaker_language = QComboBox()
        self.speaker_language.setEditable(True)
        self.speaker_language.addItems([
            "(Image/context provided)", "English", "Spanish", "French", "German", "Italian",
            "Portuguese", "Japanese", "Korean", "Mandarin Chinese", "Hindi", "Arabic", "Russian",
        ])
        self.speaker_language.setCurrentIndex(0)
        self.speaker_language.setToolTip("Spoken language used when Spoken Dialog is enabled; this field is editable")
        self.speaker_language.currentTextChanged.connect(self.mark_dirty)
        self.speaker_language.setEnabled(False)
        accent_label = QLabel("ACCENT")
        accent_label.setObjectName("groupLabel")
        self.speaker_accent = QComboBox()
        self.speaker_accent.setEditable(True)
        self.speaker_accent.addItems([
            "(Image/context provided)", "Neutral", "General American", "British RP", "Yorkshire",
            "Australian", "Canadian", "Irish", "Scottish", "Indian English", "Asian (Chinese)", "Mexican Spanish",
            "Castilian Spanish", "Brazilian Portuguese", "European Portuguese",
        ])
        self.speaker_accent.setCurrentIndex(0)
        self.speaker_accent.setToolTip("Exact regional accent used when Spoken Dialog is enabled; this field is editable")
        self.speaker_accent.currentTextChanged.connect(self.mark_dirty)
        self.speaker_accent.setEnabled(False)
        planning_row.addWidget(length_label)
        planning_row.addWidget(self.requested_length)
        planning_row.addWidget(language_label)
        planning_row.addWidget(self.speaker_language)
        planning_row.addWidget(accent_label)
        planning_row.addWidget(self.speaker_accent)
        planning_row.addStretch()
        self.director_controls.addLayout(planning_row)
        options_row = QHBoxLayout()
        options_row.setContentsMargins(0, 0, 0, 0)
        options_row.setSpacing(6)
        options_label = QLabel("PROMPT OPTIONS")
        options_label.setObjectName("groupLabel")
        options_row.addWidget(options_label)
        options_row.addWidget(self.sfx)
        options_row.addWidget(self.spoken_dialog)
        options_row.addWidget(self.hdr)
        options_row.addWidget(self.reduce_music)
        options_row.addStretch()
        self.director_controls.addLayout(options_row)
        outer.addWidget(director_panel)

        segment_panel = QFrame()
        segment_panel.setObjectName("promptPanel")
        segment_layout = QVBoxLayout(segment_panel)
        segment_layout.setContentsMargins(9, 6, 9, 5)
        self.segment_header = QHBoxLayout()
        self.segment_header.setSpacing(8)
        segment_label = QLabel("SEGMENT PROMPT")
        segment_label.setObjectName("sectionLabel")
        self.segment_header.addWidget(segment_label)
        self.refine_timing_button = QPushButton("⏱ Refine Timing")
        self.refine_timing_button.setObjectName("refineButton")
        self.refine_timing_button.setToolTip("Analyze the existing sequence and retime only the selected segment; prompt wording is never changed")
        self.refine_timing_button.clicked.connect(self.refine_selected_timing)
        self.refine_timing_button.setEnabled(False)
        self.refine_prompt_button = QPushButton("✎ Refine Prompt")
        self.refine_prompt_button.setObjectName("refineButton")
        self.refine_prompt_button.setToolTip("Refine only the selected segment prompt using adjacent frames for continuity; may also adjust its duration")
        self.refine_prompt_button.clicked.connect(self.refine_selected_prompt)
        self.refine_prompt_button.setEnabled(False)
        self.segment_header.addWidget(self.refine_timing_button)
        self.segment_header.addWidget(self.refine_prompt_button)
        self.frame_number = QLabel("Frame —")
        self.frame_number.setObjectName("muted")
        self.start_button = QPushButton("Start frame")
        self.end_button = QPushButton("End frame")
        self.start_button.setObjectName("frameToggle")
        self.end_button.setObjectName("frameToggle")
        self.start_button.setCheckable(True)
        self.end_button.setCheckable(True)
        self.duration_control = QFrame()
        self.duration_control.setObjectName("outputSizeControl")
        duration_control_layout = QHBoxLayout(self.duration_control)
        duration_control_layout.setContentsMargins(0, 0, 0, 0)
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setObjectName("timelineSpin")
        self.duration_spin.setRange(1, 12)
        self.duration_spin.setSingleStep(.5)
        self.duration_spin.setDecimals(1)
        self.duration_spin.setSuffix(" s")
        self.duration_spin.setToolTip("Segment duration in seconds (1–12)")
        self.duration_spin.valueChanged.connect(self.editor_duration_changed)
        duration_control_layout.addWidget(self.duration_spin)
        self.start_button.clicked.connect(lambda: self.set_role("start"))
        self.end_button.clicked.connect(lambda: self.set_role("end"))
        self.segment_header.addStretch()
        segment_meta = QFrame()
        segment_meta.setObjectName("segmentMetaBar")
        segment_meta_layout = QHBoxLayout(segment_meta)
        segment_meta_layout.setContentsMargins(5, 3, 5, 3)
        segment_meta_layout.setSpacing(6)
        segment_meta_layout.addWidget(self.frame_number)
        segment_meta_layout.addWidget(self.start_button)
        segment_meta_layout.addWidget(self.end_button)
        duration_label = QLabel("DURATION")
        duration_label.setObjectName("timelineControlLabel")
        segment_meta_layout.addWidget(duration_label)
        segment_meta_layout.addWidget(self.duration_control)
        self.segment_header.addWidget(segment_meta)
        segment_layout.addLayout(self.segment_header)
        self.segment_prompt = QTextEdit()
        self.segment_prompt.setObjectName("promptEditor")
        self.segment_prompt.textChanged.connect(self.save_prompt)
        segment_layout.addWidget(self.segment_prompt)
        segment_footer = QHBoxLayout()
        segment_footer.setContentsMargins(0, 0, 0, 0)
        segment_footer.setSpacing(0)
        self.segment_count = QLabel("0 characters")
        self.segment_count.setObjectName("muted")
        self.copy_segment = QPushButton("□ Copy")
        self.copy_segment.setObjectName("copyButton")
        self.copy_segment.setFlat(True)
        self.copy_segment.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.copy_segment.clicked.connect(lambda: QApplication.clipboard().setText(self.segment_prompt.toPlainText()))
        segment_footer.addWidget(self.segment_count)
        segment_footer.addStretch()
        segment_footer.addWidget(self.copy_segment, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        segment_layout.addLayout(segment_footer)
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
        self.global_prompt.setObjectName("promptEditor")
        self.global_prompt.textChanged.connect(self.update_counts)
        self.global_prompt.textChanged.connect(self.mark_dirty)
        global_layout.addWidget(self.global_prompt)
        global_footer = QHBoxLayout()
        global_footer.setContentsMargins(0, 0, 0, 0)
        global_footer.setSpacing(0)
        self.global_count = QLabel("0 characters")
        self.global_count.setObjectName("muted")
        self.copy_global = QPushButton("□ Copy")
        self.copy_global.setObjectName("copyButton")
        self.copy_global.setFlat(True)
        self.copy_global.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.copy_global.clicked.connect(lambda: QApplication.clipboard().setText(self.global_prompt.toPlainText()))
        global_footer.addWidget(self.global_count)
        global_footer.addStretch()
        global_footer.addWidget(self.copy_global, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        global_layout.addLayout(global_footer)
        segment_panel.setMinimumHeight(90)
        global_panel.setMinimumHeight(90)
        self.prompt_splitter = DottedPromptSplitter()
        self.prompt_splitter.addWidget(segment_panel)
        self.prompt_splitter.addWidget(global_panel)
        self.prompt_splitter.setStretchFactor(0, 3)
        self.prompt_splitter.setStretchFactor(1, 2)
        self.prompt_splitter.splitterMoved.connect(self.save_prompt_splitter_sizes)
        outer.addWidget(self.prompt_splitter, 1)
        QTimer.singleShot(0, self.restore_prompt_splitter_sizes)
        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())
        self.update_summary()

    def _build_project_dock(self) -> None:
        self.project_dock = QDockWidget("PROJECT LIBRARY", self)
        self.project_dock.setObjectName("projectDock")
        self.project_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.project_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.project_dock.setMinimumWidth(280)
        self.project_dock.setMaximumWidth(900)

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(9, 9, 9, 9)
        layout.setSpacing(8)
        library_controls = QFrame()
        library_controls.setObjectName("libraryControls")
        header = QHBoxLayout(library_controls)
        header.setContentsMargins(6, 5, 6, 5)
        header.setSpacing(6)
        self.collection_up = QPushButton("↑ UP")
        self.collection_up.setVisible(False)
        self.collection_up.clicked.connect(self.leave_collection)
        self.project_library_title = QLabel("Saved projects")
        self.project_library_title.setObjectName("projectLibraryTitle")
        self.project_library_title.setWordWrap(True)
        self.project_library_title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.project_library_title.setMinimumWidth(0)
        self.project_library_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.project_library_title)
        self.project_sort = QComboBox()
        self.project_sort.addItem("Title A–Z", "title_asc")
        self.project_sort.addItem("Title Z–A", "title_desc")
        self.project_sort.addItem("Custom", "custom")
        saved_sort = self.settings.value("project_sort_mode", "title_asc")
        self.project_sort.setCurrentIndex(max(0, self.project_sort.findData(saved_sort)))
        self.project_sort.setToolTip("Sort projects by title or drag tiles in Custom mode")
        self.project_sort.currentIndexChanged.connect(self.project_sort_changed)
        header.addWidget(self.project_sort)
        self.project_icon_button = QPushButton()
        self.project_icon_button.setObjectName("projectIconButton")
        self.project_icon_button.setToolTip("Set project and collection thumbnail size")
        icon_menu = QMenu(self.project_icon_button)
        self.project_icon_actions: dict[int, QAction] = {}
        for label, size in (("Small", 96), ("Medium", 156), ("Large", 230)):
            action = icon_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda checked=False, value=size: self.set_project_icon_size(value))
            self.project_icon_actions[size] = action
        self.project_icon_button.setMenu(icon_menu)
        header.addWidget(self.project_icon_button)
        header.addWidget(self.collection_up)
        header.addStretch()
        layout.addWidget(library_controls)
        self.project_search = QLineEdit()
        self.project_search.setObjectName("projectSearch")
        self.project_search.setPlaceholderText("Search projects…")
        self.project_search.setClearButtonEnabled(True)
        self.project_search.textChanged.connect(self.filter_projects)
        layout.addWidget(self.project_search)

        self.project_list = ProjectListWidget()
        self.project_list.setObjectName("projectList")
        self.project_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.project_list.setFlow(QListWidget.Flow.LeftToRight)
        self.project_list.setWrapping(True)
        self.project_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.project_list.setMovement(QListWidget.Movement.Static)
        self.project_list.setSpacing(4)
        self.project_list.itemClicked.connect(self.activate_clicked_project)
        self.project_list.itemDoubleClicked.connect(lambda *_: self.open_library_project())
        self.project_list.drag_started.connect(self.activate_custom_sort_for_drag)
        self.project_list.order_changed.connect(self.save_custom_project_order)
        layout.addWidget(self.project_list, 1)

        buttons = QHBoxLayout()
        self.save_library_button = QPushButton("Save Current")
        self.save_library_button.setObjectName("librarySave")
        self.save_library_button.clicked.connect(self.save_library_project)
        open_button = QPushButton("Open")
        open_button.setObjectName("librarySecondary")
        open_button.clicked.connect(self.open_library_project)
        edit_button = QPushButton("Edit")
        edit_button.setObjectName("librarySecondary")
        edit_button.clicked.connect(self.edit_library_project)
        delete_button = QPushButton("Delete")
        delete_button.setObjectName("libraryDelete")
        delete_button.clicked.connect(self.delete_library_project)
        buttons.addWidget(self.save_library_button, 1)
        buttons.addWidget(open_button)
        buttons.addWidget(edit_button)
        buttons.addWidget(delete_button)
        layout.addLayout(buttons)
        self.update_project_icon_controls()
        self.project_dock.setWidget(panel)
        self.project_dock.installEventFilter(self)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.project_dock)
        self.project_dock.visibilityChanged.connect(self.project_dock_visibility_changed)
        self.project_dock.dockLocationChanged.connect(lambda *_: self.save_window_panel_state())
        self.project_dock.topLevelChanged.connect(lambda *_: self.save_window_panel_state())
        self.project_dock.hide()
        self.refresh_project_library()

    def project_dock_visibility_changed(self, visible: bool) -> None:
        if visible:
            QTimer.singleShot(0, lambda: self.set_project_panel_width(self.project_panel_width, persist=False))

    def restore_project_panel_width(self) -> None:
        """Restore the saved dock width without startup resize events overwriting it."""
        self.set_project_panel_width(self.project_panel_width, persist=False)
        QTimer.singleShot(0, self.finish_layout_restore)

    def finish_layout_restore(self) -> None:
        self._restoring_layout = False

    def set_project_panel_width(self, width: int, persist: bool = True) -> None:
        width = max(280, min(900, int(width)))
        self.project_panel_width = width
        if self.project_dock.isFloating():
            self.project_dock.resize(width, self.project_dock.height())
        else:
            self.resizeDocks([self.project_dock], [width], Qt.Orientation.Horizontal)
        if persist:
            self.settings.setValue("project_panel_width", width)
            self.queue_settings_sync()

    def update_project_icon_controls(self) -> None:
        labels = {96: "Small", 156: "Medium", 230: "Large"}
        self.project_icon_button.setText(f"Icons: {labels[self.project_icon_size]}")
        for size, action in self.project_icon_actions.items():
            action.setChecked(size == self.project_icon_size)
        card_width = self.project_icon_size + 28
        card_height = self.project_icon_size + 76
        self.project_list.setIconSize(QSize(self.project_icon_size, self.project_icon_size))
        self.project_list.setGridSize(QSize(card_width, card_height))

    def set_project_icon_size(self, size: int) -> None:
        size = min((96, 156, 230), key=lambda value: abs(value - int(size)))
        if size == self.project_icon_size:
            return
        self.project_icon_size = size
        self.settings.setValue("project_icon_size", size)
        self.update_project_icon_controls()
        self.refresh_project_library(preserve_scroll=False)

    def restore_prompt_splitter_sizes(self) -> None:
        try:
            sizes = json.loads(str(self.settings.value("prompt_splitter_sizes", "[]")))
        except (ValueError, TypeError):
            sizes = []
        if isinstance(sizes, list) and len(sizes) == 2 and all(int(value) > 0 for value in sizes):
            self.prompt_splitter.setSizes([int(value) for value in sizes])

    def save_prompt_splitter_sizes(self, *_args) -> None:
        self.settings.setValue("prompt_splitter_sizes", json.dumps(self.prompt_splitter.sizes()))
        self.queue_settings_sync()

    def queue_settings_sync(self) -> None:
        self._settings_sync_timer.start()

    def save_window_panel_state(self) -> None:
        self.settings.setValue("window/state", self.saveState())
        self.queue_settings_sync()

    def eventFilter(self, watched, event) -> bool:
        if watched is getattr(self, "project_dock", None) and event.type() == QEvent.Type.Resize and not self._restoring_layout:
            width = self.project_dock.width()
            if 280 <= width <= 900:
                self.project_panel_width = width
                self.settings.setValue("project_panel_width", width)
                self.settings.setValue("window/state", self.saveState())
                self.queue_settings_sync()
        return super().eventFilter(watched, event)

    def _apply_theme(self) -> None:
        scale = max(75, min(200, self.settings.value("ui_text_scale", 100, int))) / 100

        def scaled(size: int) -> int:
            return max(7, round(size * scale))

        def metric(size: int) -> int:
            return max(1, round(size * scale))

        theme = """
        QMainWindow,QWidget{background:#24292c;color:#d9dcde;font:11px Arial} QMainWindow::separator{width:__DOCK_GRIP_WIDTH__px;height:__DOCK_GRIP_WIDTH__px;background:transparent;background-image:url("__DOCK_GRIP_IMAGE__");background-repeat:no-repeat;background-position:center} QMainWindow::separator:hover{background-color:rgba(88,118,134,35)} QToolBar{background:#1b2023;border:0;border-bottom:1px solid #111517;spacing:3px;padding:5px} QToolBar::separator{background:#394247;width:1px;margin:7px 5px}
        QToolButton,QPushButton,QComboBox,QSpinBox,QDoubleSpinBox,QLineEdit{background:#303436;border:1px solid #101213;border-radius:3px;padding:3px 7px;min-height:19px}
        #mainToolbar QToolButton{background:transparent;border:1px solid transparent;border-radius:4px;padding:5px 9px;color:#c5cdd1} #mainToolbar QToolButton:hover{background:#2b3438;border-color:#3a464c;color:#f3f7f9} #mainToolbar QToolButton:pressed{background:#17232a;border-color:#477d99;color:#bde6fb} #toolbarButton{background:#23343d;border:1px solid #385667;border-radius:5px;color:#c4e8fb;font-weight:bold}
        QToolButton:hover,QPushButton:hover{background:#41474a} QToolButton:pressed,QPushButton:pressed{background:#202729;border-color:#79a8c5} QLineEdit{background:#1e2122}
        QSpinBox,QDoubleSpinBox{padding-right:__SPIN_PAD__px} QSpinBox::up-button,QDoubleSpinBox::up-button{subcontrol-origin:border;subcontrol-position:top right;width:__SPIN_BUTTON__px;background:#3b4347;border:0;border-left:1px solid #171a1c;border-bottom:1px solid #202527;border-top-right-radius:3px} QSpinBox::down-button,QDoubleSpinBox::down-button{subcontrol-origin:border;subcontrol-position:bottom right;width:__SPIN_BUTTON__px;background:#343b3f;border:0;border-left:1px solid #171a1c;border-top:1px solid #202527;border-bottom-right-radius:3px}
        QSpinBox::up-button:hover,QDoubleSpinBox::up-button:hover,QSpinBox::down-button:hover,QDoubleSpinBox::down-button:hover{background:#506471} QSpinBox::up-button:pressed,QDoubleSpinBox::up-button:pressed,QSpinBox::down-button:pressed,QDoubleSpinBox::down-button:pressed{background:#274e66} QSpinBox::up-arrow,QDoubleSpinBox::up-arrow,QSpinBox::down-arrow,QDoubleSpinBox::down-arrow{width:__ARROW_SIZE__px;height:__ARROW_SIZE__px}
        QComboBox{padding-right:__COMBO_PAD__px} QComboBox::drop-down{subcontrol-origin:padding;subcontrol-position:top right;width:__COMBO_BUTTON__px;background:#394145;border:0;border-left:1px solid #171a1c;border-top-right-radius:3px;border-bottom-right-radius:3px} QComboBox::drop-down:hover{background:#506471} QComboBox::drop-down:pressed{background:#274e66} QComboBox::down-arrow{width:__ARROW_SIZE__px;height:__ARROW_SIZE__px} QComboBox QAbstractItemView{background:#202527;color:#e1e5e7;border:1px solid #52616a;outline:0;padding:4px;selection-background-color:#3b6f9c;selection-color:#fff}
        QSlider::groove:horizontal{height:__SLIDER_GROOVE__px;background:#161b1d;border:1px solid #0d1011;border-radius:__SLIDER_RADIUS__px} QSlider::sub-page:horizontal{background:#367da6;border:1px solid #4b9ac6;border-radius:__SLIDER_RADIUS__px} QSlider::add-page:horizontal{background:#161b1d;border-radius:__SLIDER_RADIUS__px} QSlider::handle:horizontal{width:__SLIDER_HANDLE__px;margin:-__SLIDER_MARGIN__px 0;background:#607883;border:2px solid #8fb9cd;border-radius:__SLIDER_HANDLE_RADIUS__px} QSlider::handle:horizontal:hover{background:#78a8be;border-color:#c5ebff} QSlider::handle:horizontal:pressed{background:#4aa3d2;border-color:#e1f6ff}
        #timelineShell{background:#0d0f10;border:1px solid #323638;border-radius:3px} #mainTrackLabel,#audioTrackLabel{background:#191c1d;border-right:1px solid #34383a;font-weight:bold} #audioTrack{background:#101516;border:0;border-top:1px solid #30383c} #audioTrack QWidget#qt_scrollarea_viewport{background:#101516} #audioCanvas{background:#101516} #audioClip{background:#1d343d;border:1px solid #4f849a;border-radius:4px} #audioClip:hover{background:#244653;border-color:#75b8d3}
        #timelineTitle{background:#19262d;color:#b9def2;border:1px solid #304955;border-radius:4px;padding:4px 8px;font-weight:bold;letter-spacing:1px} #timelineControlLabel{background:transparent;color:#7f9099;border:0;font-size:9px;font-weight:bold;letter-spacing:1px}
        #outputSizeControl{background:#1a2023;border:1px solid #354047;border-radius:5px} #timelineSpin{background:transparent;border:0;border-radius:0;padding-left:9px;padding-right:__TIMELINE_SPIN_PAD__px;color:#e6edf1;font-weight:bold;selection-background-color:#3b6f9c} #timelineSpin:hover{background:#20292d} #timelineSpin:focus{background:#202b30;color:#fff}
        #timelineSpin::up-button{width:__TIMELINE_SPIN_BUTTON__px;background:transparent;border:0;border-radius:2px;subcontrol-origin:border;subcontrol-position:top right} #timelineSpin::down-button{width:__TIMELINE_SPIN_BUTTON__px;background:transparent;border:0;border-radius:2px;subcontrol-origin:border;subcontrol-position:bottom right} #timelineSpin::up-button:hover,#timelineSpin::down-button:hover{background:#344b57} #timelineSpin::up-button:pressed,#timelineSpin::down-button:pressed{background:#1f668b} #timelineSpin::up-arrow{image:url("__SPIN_UP_IMAGE__");width:__TIMELINE_ARROW__px;height:__TIMELINE_ARROW__px} #timelineSpin::down-arrow{image:url("__SPIN_DOWN_IMAGE__");width:__TIMELINE_ARROW__px;height:__TIMELINE_ARROW__px}
        #resolutionSeparator{background:transparent;color:#60717a;border:0;padding:0 3px;font-weight:bold} #timelineButton{background:transparent;color:#acd8ef;border:1px solid #3f6679;border-radius:5px;padding:4px 10px;font-weight:bold} #timelineButton:hover{background:#243b46;color:#e0f5ff;border-color:#65a7c7} #timelineButton:pressed{background:#172b35;color:#85c9eb;border-color:#347898}
        QListWidget{background:#0d0f10;border:0;padding:0} QListWidget::item{border:1px solid #696b6c;background:#252728;margin:0} QListWidget::item:selected{border:2px solid #f1f1f1;background:#293034}
        #timeline{padding:3px;background:#0d0f10;outline:0} #timeline::item,#timeline::item:selected,#timeline::item:focus{background:#0d0f10;border:0;outline:0} #timeline[dropActive="true"]{border:3px solid #68b9ee;background:#13232c} #timeline[dropActive="false"]{border:1px solid #323638}
        #segmentCard{background:transparent;border:1px solid transparent} #segmentCard[selected="true"]{background:#17262e;border:1px solid #73a9c4} #segmentCardBody{background:#252728;border:0} #mediaBadge{background:#e5e5e5;color:#262626;font-weight:bold;padding:2px} #roleBadge{background:#36393a;color:#eee;padding:2px}
        #tileDelete{padding:0;min-height:0;max-height:20px;background:#454849;color:#ddd;border:0} #tileDelete:hover{background:#a94444;color:#fff;border:1px solid #e07878} #tileTitle{background:#242627;padding:3px;font-size:9px} #tileDuration{color:#a2a7a9;font-size:8px}
        #resizeHandle{background:transparent;border:0} #resizeHandle:hover{background:rgba(88,118,134,35);border:0}
        #timelineHeightHandle{background:transparent;border:0} #timelineHeightHandle:hover{background:rgba(88,118,134,35);border:0}
        #promptSplitter::handle{background:transparent;border:0} #promptSplitter::handle:hover{background:rgba(88,118,134,35);border:0}
        #segmentPreview{background:#17191a;border-top:1px solid #34383a;color:#7494a3;font-weight:bold}
        #addTileBox{border:1px dashed #596065;background:#111415} #addTile,#addTextTile{border:0;background:transparent;color:#828b90;font-size:10px} #addTile:hover,#addTextTile:hover{background:#1c2326;color:#b7d7e6} #addTextTile{border-top:1px solid #343d41;padding:7px 4px} #audioTrack[dropActive=true]{border:2px dashed #65a9cd;background:#17252c} #sequenceBar{background:#181e21;border:1px solid #303a3f;border-left:3px solid #4e91b4;border-radius:5px;padding:10px 12px;color:#cbd7dd;font-weight:bold}
        #directorPanel{background:#1d2326;border:1px solid #354047;border-radius:6px} #panelTitle{background:transparent;color:#b8d9e9;border:0;font-size:10px;font-weight:bold;letter-spacing:1px} #groupLabel{background:transparent;color:#71838c;border:0;font-size:8px;font-weight:bold;letter-spacing:1px;padding-right:4px}
        #sectionLabel{color:#8ebbd1;font-size:8px;font-weight:bold;letter-spacing:1px} #muted{color:#879095;font-size:9px} #promptPanel{background:#202527;border:1px solid #374044;border-radius:6px} #segmentMetaBar{background:#1a2023;border:1px solid #303a3f;border-radius:5px} #refineButton{background:#252d31;border:1px solid #45545b;color:#c9dce5;padding-left:9px;padding-right:9px} #refineButton:hover{background:#31414a;border-color:#6590a7;color:#f2fbff} #refineButton:pressed{background:#1d2b32;border-color:#7ca9bf} #refineButton:disabled{background:#202527;border-color:#31393d;color:#606a6f}
        QTextEdit{background:#252728;border:0;color:#e1e4e5;font:11px 'Courier New';padding:4px} #promptEditor{background:#202527;border:0;color:#e1e4e5;padding:7px} #magicButton{background:#3b78a5;border:1px solid #5b9bc6;border-radius:5px;color:#f4fbff;font-weight:bold;padding-left:12px;padding-right:12px} #magicButton:hover{background:#4b8dbd;border-color:#8bc6ea} #magicButton:pressed{background:#285b7c}
        #audioToggle,#qualityToggle,#frameToggle{background:transparent;border:1px solid #455057;color:#b8c0c4} #audioToggle:hover,#qualityToggle:hover,#frameToggle:hover{background:#293236;border-color:#65747c;color:#eef3f5} #audioToggle:checked,#qualityToggle:checked,#frameToggle:checked{background:#244d37;border-color:#4c9b6a;color:#c9f4d6} #copyButton{min-height:0;padding:1px 4px;margin:0;border:0;background:transparent;color:#aeb5b8} #copyButton:hover{background:#303a3f;color:#e5f4fc;border:0} #copyButton:pressed{background:#1b2429;color:#8fd3f7;border:0} QStatusBar{background:#171c1e;color:#7f898d;border-top:1px solid #30383c}
        QDockWidget{background:#191d1f;color:#d9dcde;font-weight:bold} QDockWidget::title{background:#1b2022;border-bottom:1px solid #0e1011;padding:8px;text-align:left}
        #projectLibraryTitle,#magicOverlayTitle{font-size:15px;font-weight:bold;color:#f0f2f3} #libraryControls{background:#1c2225;border:1px solid #343e43;border-radius:5px} #projectSearch{background:#171c1e;border:1px solid #343d41;border-radius:5px;padding-left:10px} #projectSearch:focus{border-color:#4d829d;background:#1b2225} #projectList{background:#15191b;border:1px solid #30383c;border-radius:5px;padding:10px}
        #projectList::item{background:#22282b;border:1px solid #363f43;border-radius:6px;margin:4px;padding:7px;color:#dce0e2} #projectList::item:hover{border-color:#6488a1;background:#293136} #projectList::item:selected{border:2px solid #69a5d0;background:#27343b}
        #librarySave{background:#3b78a5;border-color:#5994bd;font-weight:bold} #librarySave:hover{background:#5596ca;border-color:#8bc8f5;color:#fff} #librarySave:pressed{background:#214865;border:1px solid #b9e1ff;color:#fff} #librarySecondary{background:transparent;border-color:#3d484e;color:#bfc7cb} #librarySecondary:hover{background:#30393d;border-color:#596a73;color:#fff} #libraryDelete{background:transparent;border-color:#4b3b3b;color:#c8b7b7} #libraryDelete:hover{background:#713d3d;border-color:#9b5656;color:#fff}
        QMenu{background:#252a2c;border:1px solid #596267;padding:4px} QMenu::item{padding:7px 28px 7px 12px;border-radius:3px} QMenu::item:selected{background:#3b6f9c;color:#fff} QMenu::separator{height:1px;background:#4b5255;margin:4px 7px}
        QScrollBar:vertical{background:#171b1d;width:12px;margin:0;border:0;border-radius:6px} QScrollBar::handle:vertical{background:#46545c;min-height:28px;margin:2px;border-radius:4px} QScrollBar::handle:vertical:hover{background:#63869b} QScrollBar::handle:vertical:pressed{background:#74a8c6}
        QScrollBar:horizontal{background:#171b1d;height:12px;margin:0;border:0;border-radius:6px} QScrollBar::handle:horizontal{background:#46545c;min-width:28px;margin:2px;border-radius:4px} QScrollBar::handle:horizontal:hover{background:#63869b} QScrollBar::handle:horizontal:pressed{background:#74a8c6}
        QScrollBar::add-line,QScrollBar::sub-line{width:0;height:0;background:transparent;border:0} QScrollBar::up-arrow,QScrollBar::down-arrow,QScrollBar::left-arrow,QScrollBar::right-arrow{width:0;height:0;background:transparent} QScrollBar::add-page,QScrollBar::sub-page{background:transparent} QAbstractScrollArea::corner{background:#171b1d;border:0}
        """
        theme = theme.replace("font:11px Arial", f"font:{scaled(11)}px Arial")
        theme = theme.replace("font:11px 'Courier New'", f"font:{scaled(11)}px 'Courier New'")
        theme = theme.replace("spacing:6px;padding:5px", f"spacing:{metric(6)}px;padding:{metric(5)}px")
        theme = theme.replace("padding:3px 7px;min-height:19px", f"padding:{metric(3)}px {metric(7)}px;min-height:{metric(19)}px")
        theme = theme.replace("width:12px;margin:0", f"width:{metric(12)}px;margin:0")
        theme = theme.replace("height:12px;margin:0", f"height:{metric(12)}px;margin:0")
        theme = theme.replace("min-height:28px;margin:2px", f"min-height:{metric(28)}px;margin:{metric(2)}px")
        theme = theme.replace("min-width:28px;margin:2px", f"min-width:{metric(28)}px;margin:{metric(2)}px")
        theme = theme.replace("__SPIN_PAD__", str(metric(30))).replace("__SPIN_BUTTON__", str(metric(23)))
        theme = theme.replace("__COMBO_PAD__", str(metric(30))).replace("__COMBO_BUTTON__", str(metric(25)))
        theme = theme.replace("__ARROW_SIZE__", str(metric(8)))
        theme = theme.replace("__SLIDER_GROOVE__", str(metric(6))).replace("__SLIDER_RADIUS__", str(metric(3)))
        theme = theme.replace("__SLIDER_HANDLE__", str(metric(16))).replace("__SLIDER_MARGIN__", str(metric(6))).replace("__SLIDER_HANDLE_RADIUS__", str(metric(8)))
        dock_grip = str(files("ltx_prompt_director").joinpath("assets/dock-grip.png")).replace("\\", "/")
        theme = theme.replace("__DOCK_GRIP_WIDTH__", str(metric(14))).replace("__DOCK_GRIP_IMAGE__", dock_grip)
        spin_up = str(files("ltx_prompt_director").joinpath("assets/spin-up.svg")).replace("\\", "/")
        spin_down = str(files("ltx_prompt_director").joinpath("assets/spin-down.svg")).replace("\\", "/")
        theme = theme.replace("__SPIN_UP_IMAGE__", spin_up).replace("__SPIN_DOWN_IMAGE__", spin_down)
        theme = theme.replace("__TIMELINE_SPIN_PAD__", str(metric(20))).replace("__TIMELINE_SPIN_BUTTON__", str(metric(17))).replace("__TIMELINE_ARROW__", str(metric(8)))
        for size in (15, 10, 9, 8):
            theme = theme.replace(f"font-size:{size}px", f"font-size:{scaled(size)}px")
        self.setStyleSheet(theme)
        self.timeline_controls.setSpacing(metric(8))
        self.output_width.setFixedWidth(metric(104))
        self.output_height.setFixedWidth(metric(104))
        self.ui_scale_spin.setFixedWidth(metric(82))
        self.intent.setFixedHeight(metric(72))
        self.requested_length.setFixedWidth(metric(82))
        self.speaker_language.setMinimumWidth(metric(170))
        self.speaker_accent.setMinimumWidth(metric(170))
        self.director_controls.setSpacing(metric(8))
        self.segment_header.setSpacing(metric(8))
        self.ruler.setFixedHeight(metric(28))
        self.timeline_height_handle.set_scale(scale)
        self.prompt_splitter.set_scale(scale)
        self.project_list.setSpacing(metric(4))
        for button in (self.provider_button, self.sfx, self.spoken_dialog, self.hdr, self.reduce_music, self.magic_button, self.refine_timing_button, self.refine_prompt_button, self.start_button, self.end_button):
            button.setMinimumWidth(button.fontMetrics().horizontalAdvance(button.text()) + metric(18))
        self.output_width.setMinimumWidth(metric(92))
        self.output_height.setMinimumWidth(metric(92))
        self.duration_spin.setFixedWidth(metric(82))
        self.add_tile_wrap.setFixedWidth(metric(112))
        self.add_tile_layout.setContentsMargins(0, 0, 0, 0)
        for button in (self.copy_segment, self.copy_global):
            button.setFixedHeight(button.fontMetrics().height() + metric(4))
            button.setMinimumWidth(button.fontMetrics().horizontalAdvance(button.text()) + metric(10))
        if hasattr(self, "timeline_loading"):
            self.timeline_loading.set_scale(scale)
        if hasattr(self, "magic_overlay"):
            self.magic_overlay.set_scale(scale)

    def refresh_project_library(self, select_id: str | None = None, preserve_scroll: bool = True) -> None:
        selected = select_id or self.current_project_id
        previous_scroll = self.project_list.verticalScrollBar().value() if preserve_scroll else 0
        if self.project_list._scroll_animation:
            self.project_list._scroll_animation.stop()
            self.project_list._scroll_animation = None
        self.project_list._scroll_target = previous_scroll
        self.project_list.clear()
        records = self.library_records()
        self.collection_up.setVisible(bool(self.current_collection))
        self.project_library_title.setText(self.current_collection or "Saved projects")
        visible_records = [record for record in records if record.get("collection", "") == (self.current_collection or "")]
        entries: list[dict] = [{**record, "kind": "project"} for record in visible_records]
        if not self.current_collection:
            collections: dict[str, list[dict]] = {}
            for record in records:
                if record.get("collection"):
                    collections.setdefault(str(record["collection"]), []).append(record)
            for name, members in collections.items():
                members.sort(key=lambda value: value.get("savedAt", ""), reverse=True)
                entries.append({"kind": "collection", "name": name, "description": f"{len(members)} projects", "members": members})
        mode = self.project_sort.currentData() if hasattr(self, "project_sort") else "title_asc"
        reverse = mode == "title_desc"
        if mode in {"title_asc", "title_desc"}:
            entries.sort(key=lambda value: str(value.get("name", "")).casefold(), reverse=reverse)
        else:
            order = self.custom_project_order()
            positions = {key: index for index, key in enumerate(order)}
            entries.sort(key=lambda value: (positions.get(self.project_entry_key(value), len(positions)), str(value.get("name", "")).casefold()))
        self.project_list.setDragDropMode(QListWidget.DragDropMode.NoDragDrop)
        self.project_list.setMovement(QListWidget.Movement.Static)
        selected_item = None
        for meta in entries:
            if meta.get("kind") == "collection":
                name = str(meta["name"])
                members = meta["members"]
                item = QListWidgetItem(f"{name}\n{len(members)} project{'s' if len(members) != 1 else ''}")
                item.setData(Qt.ItemDataRole.UserRole, {"kind": "collection", "name": name, "description": meta["description"]})
                item.setToolTip(f"Collection: {name}")
                item.setSizeHint(QSize(self.project_icon_size + 28, self.project_icon_size + 76))
                collection_cover = self.collection_pixmap(members[:4], self.project_icon_size)
                if any(self.project_is_dirty(str(member.get("id", ""))) for member in members):
                    painter = QPainter(collection_cover)
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                    dot_size = max(10, round(self.project_icon_size * .078))
                    dot_margin = max(5, round(self.project_icon_size * .039))
                    painter.setPen(QPen(QColor("#5a4300"), max(1, round(self.project_icon_size * .013))))
                    painter.setBrush(QColor("#ffd83d"))
                    painter.drawEllipse(dot_margin, dot_margin, dot_size, dot_size)
                    painter.end()
                item.setIcon(QIcon(collection_cover))
                self.project_list.addItem(item)
                continue
            name = str(meta.get("name") or "Untitled project")
            description = " ".join(str(meta.get("description") or "No description").split())
            if len(description) > 105:
                description = description[:102] + "…"
            item = QListWidgetItem(f"{name}\n{description}")
            item.setData(Qt.ItemDataRole.UserRole, {**meta, "kind": "project"})
            item.setToolTip(f"{name}\n\n{meta.get('description', '')}")
            item.setSizeHint(QSize(self.project_icon_size + 28, self.project_icon_size + 76))
            pixmap = pixmap_from_data_url(str(meta.get("thumbnailData", "")))
            if pixmap.isNull():
                pixmap = QPixmap(self.project_icon_size, self.project_icon_size)
                pixmap.fill(QColor("#171a1b"))
            else:
                pixmap = self.square_pixmap(pixmap, self.project_icon_size)
            if self.project_is_dirty(str(meta.get("id", ""))):
                pixmap = pixmap.copy()
                painter = QPainter(pixmap)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                dot_size = max(10, round(self.project_icon_size * .078))
                dot_margin = max(5, round(self.project_icon_size * .039))
                painter.setPen(QPen(QColor("#5a4300"), max(1, round(self.project_icon_size * .013))))
                painter.setBrush(QColor("#ffd83d"))
                painter.drawEllipse(dot_margin, dot_margin, dot_size, dot_size)
                painter.end()
            item.setIcon(QIcon(pixmap))
            self.project_list.addItem(item)
            if meta.get("id") == selected:
                selected_item = item
        if selected_item:
            self.project_list.setCurrentItem(selected_item)
        self.filter_projects(self.project_search.text())
        QTimer.singleShot(0, lambda value=previous_scroll: self.project_list.verticalScrollBar().setValue(value))

    @staticmethod
    def project_entry_key(meta: dict) -> str:
        return f"collection:{meta.get('name', '')}" if meta.get("kind") == "collection" else f"project:{meta.get('id', '')}"

    def custom_order_settings_key(self) -> str:
        return f"project_custom_order/{self.current_collection or '__root__'}"

    def custom_project_order(self) -> list[str]:
        try:
            value = json.loads(str(self.settings.value(self.custom_order_settings_key(), "[]")))
            return value if isinstance(value, list) else []
        except (ValueError, TypeError):
            return []

    def save_custom_project_order(self) -> None:
        if not hasattr(self, "project_sort") or self.project_sort.currentData() != "custom":
            return
        order = []
        for row in range(self.project_list.count()):
            order.append(self.project_entry_key(self.project_list.item(row).data(Qt.ItemDataRole.UserRole) or {}))
        self.settings.setValue(self.custom_order_settings_key(), json.dumps(order))

    def project_sort_changed(self) -> None:
        self.settings.setValue("project_sort_mode", self.project_sort.currentData())
        self.refresh_project_library()

    def activate_custom_sort_for_drag(self) -> None:
        if self.project_search.text():
            self.project_search.clear()
        if self.project_sort.currentData() == "custom":
            return
        current_order = []
        for row in range(self.project_list.count()):
            current_order.append(self.project_entry_key(self.project_list.item(row).data(Qt.ItemDataRole.UserRole) or {}))
        self.settings.setValue(self.custom_order_settings_key(), json.dumps(current_order))
        self.project_sort.blockSignals(True)
        self.project_sort.setCurrentIndex(self.project_sort.findData("custom"))
        self.project_sort.blockSignals(False)
        self.settings.setValue("project_sort_mode", "custom")

    def activate_clicked_project(self, item: QListWidgetItem) -> None:
        meta = item.data(Qt.ItemDataRole.UserRole) or {}
        if meta.get("kind") == "project":
            self.open_library_project()

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

    def collection_pixmap(self, members: list[dict], size: int) -> QPixmap:
        result = QPixmap(size, size)
        result.fill(QColor("#101314"))
        painter = QPainter(result)
        gap = max(2, round(size * .017))
        cell = (size - gap) // 2
        second = cell + gap
        cells = ((0, 0), (second, 0), (0, second), (second, second))
        for member, (x, y) in zip(members, cells):
            pixmap = pixmap_from_data_url(str(member.get("thumbnailData", "")))
            if not pixmap.isNull():
                painter.drawPixmap(x, y, self.square_pixmap(pixmap, cell))
        painter.setPen(QPen(QColor("#657078"), gap))
        divider = cell + gap // 2
        painter.drawLine(divider, 0, divider, size)
        painter.drawLine(0, divider, size, divider)
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

    def project_is_dirty(self, project_id: str) -> bool:
        if project_id == self.current_project_id:
            return self.project_dirty
        return bool(self.project_sessions.get(project_id, {}).get("dirty"))

    def capture_workspace_state(self) -> dict:
        return {
            "segments": self.segments,
            "audioSegments": self.audio_segments,
            "globalPrompt": self.global_prompt.toPlainText(),
            "directorIntent": self.intent.toPlainText(),
            "requestedLength": self.requested_length.value(),
            "speakerLanguage": self.speaker_language.currentText(),
            "speakerAccent": self.speaker_accent.currentText(),
            "sfx": self.sfx.isChecked(),
            "spokenDialog": self.spoken_dialog.isChecked(),
            "hdr": self.hdr.isChecked(),
            "reduceMusic": self.reduce_music.isChecked(),
            "outputWidth": self.output_width.value(),
            "outputHeight": self.output_height.value(),
            "timelineScale": self.pixels_per_second,
            "timelineHeight": self.timeline_height,
        }

    def cache_current_workspace(self) -> None:
        if not self.current_project_id:
            return
        self.project_sessions[self.current_project_id] = {
            "name": self.current_project_name,
            "dirty": self.project_dirty,
            "state": self.capture_workspace_state(),
        }

    def restore_workspace_state(self, state: dict) -> None:
        self._loading = True
        self.segments = state.get("segments", [])
        self.audio_segments = state.get("audioSegments", [])
        self.global_prompt.setPlainText(str(state.get("globalPrompt", "")))
        self.intent.setPlainText(str(state.get("directorIntent", "")))
        self.requested_length.setValue(float(state.get("requestedLength", 0)))
        self.speaker_language.setCurrentText(str(state.get("speakerLanguage", "(Image/context provided)")))
        self.speaker_accent.setCurrentText(str(state.get("speakerAccent", state.get("speakerNationality", "(Image/context provided)"))))
        self.sfx.setChecked(bool(state.get("sfx")))
        self.spoken_dialog.setChecked(bool(state.get("spokenDialog")))
        self.hdr.setChecked(bool(state.get("hdr")))
        self.reduce_music.setChecked(bool(state.get("reduceMusic")))
        self.output_width.setValue(int(state.get("outputWidth", 1280)))
        self.output_height.setValue(int(state.get("outputHeight", 704)))
        self.timeline_height = max(184, min(430, int(state.get("timelineHeight", 184))))
        self.timeline_height_handle.current_height = self.timeline_height
        self.set_timeline_height(self.timeline_height)
        scale = max(20, min(160, int(state.get("timelineScale", 65))))
        self.timeline_scale.blockSignals(True)
        self.timeline_scale.setValue(scale)
        self.timeline_scale.blockSignals(False)
        self.pixels_per_second = scale
        self.ruler.set_scale(scale)
        self._loading = False
        self.refresh_timeline()

    def leave_collection(self) -> None:
        self.current_collection = None
        self.refresh_project_library(preserve_scroll=False)

    def save_library_project(self, automatic: bool = False) -> None:
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
            project_id = uuid4().hex
            if automatic:
                intent = " ".join(self.intent.toPlainText().split())
                source_name = Path(self.segments[0].name).stem if self.segments else "Sequence"
                suggested = re.split(r"[.!?]", intent, maxsplit=1)[0].strip() if intent else source_name
                suggested = suggested[:52].rstrip(" -—,:;") or "Magic Build"
                existing_names = {str(record.get("name", "")).casefold() for record in self.library_records()}
                name = suggested
                suffix = 2
                while name.casefold() in existing_names:
                    name = f"{suggested} ({suffix})"
                    suffix += 1
                meta = {
                    "id": project_id,
                    "name": name,
                    "description": intent or f"Magic Build sequence generated from {source_name}.",
                    "collection": self.current_collection or "",
                    "projectPath": str(root / f"{project_id}.LTXD"),
                }
            else:
                collections = [str(record.get("collection")) for record in self.library_records() if record.get("collection")]
                dialog = ProjectDetailsDialog(self.intent.toPlainText(), self, collection=self.current_collection or "", collections=collections)
                if not dialog.exec():
                    return
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
        meta["duration"] = self.timeline_end_time()
        meta["segmentCount"] = len(self.segments)
        if not meta.get("thumbnailSource"):
            visual = self.first_visual_segment()
            meta["thumbnailData"] = data_url(visual.preview_path, max_edge=360, quality=84) if visual else ""
        payload = self.project_payload()
        payload["library"] = {key: meta.get(key, "") for key in ("id", "name", "description", "collection", "savedAt")}
        Path(meta["projectPath"]).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        (root / f"{meta['id']}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        self.project_dirty = False
        self.project_sessions[str(meta["id"])] = {
            "name": self.current_project_name,
            "dirty": False,
            "state": self.capture_workspace_state(),
        }
        self.refresh_project_library(meta["id"])
        self.project_dock.show()
        self.settings.setValue("last_project_id", str(meta["id"]))
        self.queue_settings_sync()
        self.statusBar().showMessage(f"Project saved to library: {meta['name']}")

    def open_library_project(self) -> None:
        meta = self.selected_library_project()
        if not meta:
            return
        if meta.get("kind") == "collection":
            self.current_collection = str(meta["name"])
            self.project_search.clear()
            self.refresh_project_library(preserve_scroll=False)
            return
        project_id = str(meta["id"])
        if project_id == self.current_project_id:
            return
        self.cache_current_workspace()
        try:
            session = self.project_sessions.get(project_id)
            if session and session.get("state"):
                self.restore_workspace_state(session["state"])
                dirty = bool(session.get("dirty"))
            else:
                payload = json.loads(Path(meta["projectPath"]).read_text(encoding="utf-8"))
                self.load_project_payload(payload)
                dirty = False
            self.current_project_id = project_id
            self.current_project_name = str(meta["name"])
            self.project_dirty = dirty
            self.project_sessions[project_id] = {
                "name": self.current_project_name,
                "dirty": dirty,
                "state": self.capture_workspace_state(),
            }
            self.update_window_title()
            self.refresh_project_library(project_id)
            self.settings.setValue("last_project_id", project_id)
            self.queue_settings_sync()
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
        self.project_sessions.pop(str(meta.get("id", "")), None)
        if self.current_project_id == meta.get("id"):
            self.current_project_id = None
            self.project_dirty = True
        self.refresh_project_library()
        self.statusBar().showMessage(f"Project deleted: {meta.get('name', 'Untitled project')}")

    def restore_last_project(self) -> None:
        """Open the saved library project that was active at the previous shutdown."""
        project_id = str(self.settings.value("last_project_id", "") or "")
        if not project_id:
            return
        meta = next((record for record in self.library_records() if str(record.get("id", "")) == project_id), None)
        if not meta:
            self.settings.remove("last_project_id")
            return
        self.current_collection = str(meta.get("collection", "")) or None
        self.refresh_project_library(project_id, preserve_scroll=False)
        for row in range(self.project_list.count()):
            item = self.project_list.item(row)
            value = item.data(Qt.ItemDataRole.UserRole) or {}
            if value.get("kind") == "project" and str(value.get("id", "")) == project_id:
                self.project_list.setCurrentItem(item)
                self.open_library_project()
                return

    def edit_library_project(self) -> None:
        meta = self.selected_library_project()
        if not meta or meta.get("kind") != "project":
            return
        collections = [str(record.get("collection")) for record in self.library_records() if record.get("collection")]
        try:
            stored_payload = json.loads(Path(meta["projectPath"]).read_text(encoding="utf-8"))
            thumbnail_options = [
                (f"Segment {index + 1}", str(frame.get("previewData", "")))
                for index, frame in enumerate(stored_payload.get("frames", [])) if frame.get("previewData")
            ]
        except (OSError, ValueError, TypeError):
            thumbnail_options = []
        dialog = ProjectDetailsDialog(
            str(meta.get("description", "")), self, name=str(meta.get("name", "")),
            collection=str(meta.get("collection", "")), collections=collections,
            thumbnail_options=thumbnail_options, thumbnail_data=str(meta.get("thumbnailData", "")),
            thumbnail_source=str(meta.get("thumbnailSource", "")),
        )
        if not dialog.exec():
            return
        thumbnail_source, thumbnail_data = dialog.selected_thumbnail()
        meta.update({
            "name": dialog.name.text().strip(),
            "description": dialog.description.toPlainText().strip(),
            "collection": dialog.collection_name(),
            "thumbnailSource": thumbnail_source,
            "thumbnailData": thumbnail_data,
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
        if str(meta["id"]) in self.project_sessions:
            self.project_sessions[str(meta["id"])]["name"] = meta["name"]
        self.refresh_project_library(meta["id"])
        self.statusBar().showMessage(f"Project details updated: {meta['name']}")

    def update_window_title(self) -> None:
        self.setWindowTitle(f"LTX Director - Director :: {self.current_project_name}")

    def closeEvent(self, event) -> None:
        if hasattr(self, "project_dock") and 280 <= self.project_dock.width() <= 900:
            self.project_panel_width = self.project_dock.width()
            self.settings.setValue("project_panel_width", self.project_panel_width)
        if self.current_project_id:
            self.settings.setValue("last_project_id", self.current_project_id)
        else:
            self.settings.remove("last_project_id")
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())
        self.settings.sync()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "magic_overlay") and self.magic_overlay.isVisible():
            self.magic_overlay.setGeometry(self.rect())
        if hasattr(self, "audio_canvas"):
            self.update_audio_timeline()

    def mark_dirty(self, *_args) -> None:
        if not self._loading:
            was_dirty = self.project_dirty
            self.project_dirty = True
            if self.current_project_id:
                session = self.project_sessions.setdefault(self.current_project_id, {"name": self.current_project_name})
                session["dirty"] = True
                if not was_dirty and hasattr(self, "project_list"):
                    self.refresh_project_library(self.current_project_id)

    def new_project(self) -> None:
        if self.current_project_id:
            self.cache_current_workspace()
        elif self.segments and self.project_dirty:
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
        self.audio_segments = []
        self.current_project_id = None
        self.current_project_name = "Untitled"
        self.intent.clear()
        self.requested_length.setValue(0)
        self.speaker_language.setCurrentText("(Image/context provided)")
        self.speaker_accent.setCurrentText("(Image/context provided)")
        self.segment_prompt.clear()
        self.global_prompt.clear()
        self.sfx.setChecked(False)
        self.spoken_dialog.setChecked(False)
        self.hdr.setChecked(False)
        self.reduce_music.setChecked(False)
        self.output_width.setValue(1280)
        self.output_height.setValue(704)
        self._loading = False
        self.project_dirty = False
        self.refresh_timeline()
        self.update_window_title()
        self.statusBar().showMessage("New project ready")

    def set_timeline_scale(self, value: int) -> None:
        self.autofit_tail_extension = 0
        self.pixels_per_second = value
        self.ruler.set_scale(value)
        self.update_timeline_layout()
        self.mark_dirty()

    def autofit_timeline(self) -> None:
        total = self.timeline_end_time()
        if total <= 0:
            return
        available = max(200, self.timeline.viewport().width() - 6)
        fitted = max(20, min(160, int(available / total)))
        self.timeline_scale.blockSignals(True)
        self.timeline_scale.setValue(fitted)
        self.timeline_scale.blockSignals(False)
        self.pixels_per_second = fitted
        self.ruler.set_scale(fitted)
        base_width = sum(max(48, int(segment.duration * fitted)) for segment in self.segments)
        self.autofit_tail_extension = max(0, available - base_width) if self.total_duration() >= self.audio_end_time() else 0
        self.update_timeline_layout()
        self.timeline.horizontalScrollBar().setValue(0)
        self.mark_dirty()

    def maximum_safe_timeline_height(self) -> int:
        """Keep timeline growth inside the current desktop-sized client area."""
        if not hasattr(self, "timeline") or not self.centralWidget():
            return 430
        central = self.centralWidget()
        current = max(1, self.timeline.height())
        other_minimum = max(0, central.minimumSizeHint().height() - current)
        available_client_height = central.height()
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            window_overhead = max(0, self.frameGeometry().height() - central.height())
            desktop_client_height = max(0, screen.availableGeometry().height() - window_overhead)
            available_client_height = min(available_client_height, desktop_client_height)
        return max(184, min(430, available_client_height - other_minimum))

    def set_timeline_height(self, value: int) -> None:
        value = max(184, min(int(value), self.maximum_safe_timeline_height()))
        self.timeline_height = value
        self.timeline_height_handle.current_height = value
        self.timeline.setFixedHeight(value)
        self.add_tile_wrap.setFixedHeight(max(152, value - 32))
        self.update_timeline_layout()
        self.mark_dirty()

    def restore_timeline_panel_height(self) -> None:
        previous_loading = self._loading
        self._loading = True
        try:
            self.set_timeline_height(self.settings.value("timeline_panel_height", self.timeline_height, int))
        finally:
            self._loading = previous_loading

    def finish_timeline_height_resize(self) -> None:
        self.settings.setValue("timeline_panel_height", self.timeline_height)
        self.queue_settings_sync()
        self.update_timeline_layout()

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

    def audio_end_time(self) -> float:
        return max((item.start + item.duration for item in self.audio_segments), default=0.0)

    def timeline_end_time(self) -> float:
        """Visible/export-style extent shared by MAIN, SOUND, ruler, and summary."""
        return max(self.total_duration(), self.audio_end_time())

    def timeline_content_width(self) -> int:
        """Return the widest real track extent, including minimum-width MAIN cards."""
        time_width = round(self.timeline_end_time() * self.pixels_per_second) + 6
        card_width = sum(
            max(48, int(segment.duration * self.pixels_per_second))
            for segment in self.segments
        ) + self.autofit_tail_extension
        return max(0, time_width, card_width)

    def first_visual_segment(self) -> Segment | None:
        return next((segment for segment in self.segments if segment.kind != "text" and segment.preview_path), None)

    def normalize_output_dimensions(self) -> None:
        for field in (self.output_width, self.output_height):
            normalized = max(256, min(4096, round(field.value() / 32) * 32))
            if field.value() != normalized:
                field.setValue(normalized)

    def add_media(self) -> None:
        paths = choose_media_files(self, True, self.settings.value("last_media_dir", str(Path.home())))
        if not paths:
            return
        self.add_media_paths(paths)

    def add_text_segment(self) -> None:
        if len(self.segments) >= MAX_SEGMENTS or self.total_duration() > MAX_SECONDS - 1:
            return
        number = sum(segment.kind == "text" for segment in self.segments) + 1
        duration = min(5.0, MAX_SECONDS - self.total_duration())
        self.segments.append(Segment(f"Text {number}", "", "", "text", "text", "", duration))
        self.mark_dirty()
        self.refresh_timeline(len(self.segments) - 1)
        self.segment_prompt.setFocus()
        self.statusBar().showMessage("Text-only segment added; enter its prompt or use Magic Build")

    def add_audio_paths(self, paths: list[str]) -> None:
        paths = [path for path in paths if Path(path).is_file()]
        if not paths:
            return
        self.settings.setValue("last_audio_dir", str(Path(paths[0]).parent))
        cursor = max((audio.start + audio.duration for audio in self.audio_segments), default=0.0)
        added = 0
        for path in paths:
            try:
                wav_path, frames, peaks = prepare_audio(path)
                duration = min(frames / FPS, MAX_SECONDS)
                start = min(cursor, max(0.0, MAX_SECONDS - duration))
                self.audio_segments.append(AudioSegment(Path(path).name, wav_path, start, duration, 0, frames, peaks))
                cursor = min(MAX_SECONDS, start + duration)
                added += 1
            except Exception as error:
                QMessageBox.warning(self, "Audio error", f"{Path(path).name}: {error}")
        if added:
            self.mark_dirty()
            self.update_audio_timeline()
            self.statusBar().showMessage(f"Added {added} soundtrack clip{'s' if added != 1 else ''}")

    def add_video_audio(self, segment: Segment, timeline_start: float) -> None:
        if segment.kind != "video" or not Path(segment.media_path).is_file():
            return
        try:
            wav_path, frames, peaks = prepare_audio(segment.media_path)
        except Exception:
            return
        available = max(1, frames - int(segment.trim_start or 0))
        duration = min(segment.duration, available / FPS)
        self.audio_segments.append(AudioSegment(
            f"{Path(segment.name).stem} — audio.wav", wav_path, timeline_start, duration,
            int(segment.trim_start or 0), frames, peaks, segment.id,
        ))

    def add_media_paths(self, paths: list[str]) -> None:
        paths = [path for path in paths if Path(path).is_file()]
        if not paths:
            return
        audio_suffixes = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
        audio_paths = [path for path in paths if Path(path).suffix.lower() in audio_suffixes]
        paths = [path for path in paths if Path(path).suffix.lower() not in audio_suffixes]
        if audio_paths:
            self.add_audio_paths(audio_paths)
        if not paths:
            return
        self.settings.setValue("last_media_dir", str(Path(paths[0]).parent))
        room = MAX_SECONDS - self.total_duration()
        added = 0
        for path in paths[: max(0, MAX_SEGMENTS - len(self.segments))]:
            if room < 1:
                break
            try:
                kind, preview, frames, trim = prepare_media(path)
                duration = 1.0 if kind == "video" else min(5.0, room)
                timeline_start = self.total_duration()
                segment = Segment(Path(path).name, path, preview, kind, "end" if len(self.segments) % 2 else "start", duration=duration, media_duration_frames=frames, trim_start=trim)
                self.segments.append(segment)
                if kind == "video":
                    self.add_video_audio(segment, timeline_start)
                room -= duration
                added += 1
            except Exception as error:
                QMessageBox.warning(self, "Media error", f"{Path(path).name}: {error}")
        self.mark_dirty()
        self.refresh_timeline(len(self.segments) - 1)
        self.statusBar().showMessage(f"Added {added} media file{'s' if added != 1 else ''}")

    def refresh_timeline(self, selected: int = 0) -> None:
        self.autofit_tail_extension = 0
        indicator = getattr(self, "timeline_loading", None) if self.segments else None
        if indicator:
            indicator.show_loading()
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        self._loading = True
        try:
            self.timeline.clear()
            card_height = max(152, self.timeline_height - 32)
            preview_height = max(80, card_height - 55)
            for index, segment in enumerate(self.segments):
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, segment.id)
                item.setSizeHint(QSize(max(48, int(segment.duration * self.pixels_per_second)), card_height))
                self.timeline.addItem(item)
                card = SegmentCard(segment, preview_height, self.pixels_per_second, MAX_SECONDS if len(self.segments) == 1 else 12.0)
                card.duration_changed.connect(lambda value, sid=segment.id: self.change_duration(sid, value))
                card.delete_requested.connect(lambda sid=segment.id: self.delete_by_id(sid))
                card.resize_finished.connect(lambda sid=segment.id: self.finish_resize(sid))
                card.set_timeline_edges(index == 0, index == len(self.segments) - 1)
                self.timeline.setItemWidget(item, card)
                if indicator:
                    indicator.raise_()
                    QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            self._loading = False
            if self.segments:
                self.timeline.setCurrentRow(max(0, min(selected, len(self.segments) - 1)))
            self.update_summary()
            self.update_audio_timeline()
        finally:
            self._loading = False
            if indicator:
                indicator.hide_loading()

    def update_timeline_layout(self) -> None:
        """Resize existing cards in place without rebuilding media-backed widgets."""
        if not hasattr(self, "timeline"):
            return
        self.timeline._cancel_smooth_scroll()
        previous_loading = self._loading
        self._loading = True
        try:
            card_height = max(152, self.timeline_height - 32)
            preview_height = max(80, card_height - 55)
            by_id = {segment.id: segment for segment in self.segments}
            for row in range(self.timeline.count()):
                item = self.timeline.item(row)
                segment = by_id.get(item.data(Qt.ItemDataRole.UserRole))
                if not segment:
                    continue
                width = max(48, int(segment.duration * self.pixels_per_second))
                if row == self.timeline.count() - 1:
                    width += self.autofit_tail_extension
                item.setSizeHint(QSize(width, card_height))
                card = self.timeline.itemWidget(item)
                if isinstance(card, SegmentCard):
                    card.set_timeline_edges(row == 0, row == self.timeline.count() - 1)
                    card.update_layout(preview_height, self.pixels_per_second)
            self.timeline.doItemsLayout()
            self.update_summary()
            self.update_audio_timeline()
        finally:
            self._loading = previous_loading

    def sync_audio_scroll(self, value: int) -> None:
        if hasattr(self, "audio_scroll") and not self._syncing_audio_scroll:
            self._syncing_audio_scroll = True
            self.audio_scroll.horizontalScrollBar().setValue(value)
            self._syncing_audio_scroll = False

    def audio_scroll_changed(self, value: int) -> None:
        if self._syncing_audio_scroll:
            return
        self._syncing_audio_scroll = True
        main_bar = self.timeline.horizontalScrollBar()
        main_bar.setValue(value)
        self.ruler.set_offset(value)
        self._syncing_audio_scroll = False

    def update_timeline_scroll_ranges(self) -> None:
        """Keep both tracks on one dynamic range and hide bars when everything fits."""
        if not hasattr(self, "audio_canvas"):
            return
        main_view = max(1, self.timeline.viewport().width())
        audio_view = max(1, self.audio_scroll.viewport().width())
        visible_width = min(main_view, audio_view)
        content_width = max(visible_width, self.timeline_content_width())
        scroll_maximum = max(0, content_width - visible_width)
        current = min(scroll_maximum, max(
            self.timeline.horizontalScrollBar().value(),
            self.audio_scroll.horizontalScrollBar().value(),
        ))
        self.timeline._cancel_smooth_scroll()
        self._syncing_audio_scroll = True
        try:
            self.audio_canvas.setFixedSize(audio_view + scroll_maximum, 76)
            main_bar = self.timeline.horizontalScrollBar()
            audio_bar = self.audio_scroll.horizontalScrollBar()
            main_bar.setRange(0, scroll_maximum)
            audio_bar.setRange(0, scroll_maximum)
            main_bar.setValue(current)
            audio_bar.setValue(current)
            self.ruler.set_offset(current)
            self.timeline._scroll_target = current
        finally:
            self._syncing_audio_scroll = False

    def segment_start_time(self, segment_id: str) -> float | None:
        cursor = 0.0
        for segment in self.segments:
            if segment.id == segment_id:
                return cursor
            cursor += segment.duration
        return None

    def sync_coupled_audio(self) -> None:
        for audio in self.audio_segments:
            if not audio.coupled_to:
                continue
            start = self.segment_start_time(audio.coupled_to)
            video = next((segment for segment in self.segments if segment.id == audio.coupled_to), None)
            if start is None or video is None:
                audio.coupled_to = None
                continue
            audio.start = start
            available = max(1, audio.audio_duration_frames - audio.trim_start) / FPS
            audio.duration = min(video.duration, available)

    def update_audio_timeline(self) -> None:
        if not hasattr(self, "audio_canvas"):
            return
        self.sync_coupled_audio()
        existing = {child.segment.id: child for child in self.audio_canvas.findChildren(AudioClipCard)}
        for audio in self.audio_segments:
            card = existing.pop(audio.id, None)
            if card is None:
                card = AudioClipCard(audio, self.pixels_per_second, self.audio_canvas)
                card.moved.connect(self.move_audio_segment)
                card.trimmed.connect(self.trim_audio_segment)
                card.menu_requested.connect(self.audio_menu)
            card.segment = audio
            card.pixels_per_second = self.pixels_per_second
            card.setCursor(Qt.CursorShape.ArrowCursor if audio.coupled_to else Qt.CursorShape.SizeAllCursor)
            card.setToolTip(card.tooltip_text())
            card.setGeometry(round(audio.start * self.pixels_per_second), 5, max(28, round(audio.duration * self.pixels_per_second)), 66)
            card.update()
            card.show()
        for card in existing.values():
            card.deleteLater()
        self.update_timeline_scroll_ranges()
        self.update_summary()

    def audio_move_region(self, audio: AudioSegment) -> tuple[float, float, list[AudioSegment]]:
        """Return the independent lane bounded by coupled video-audio clips."""
        left = 0.0
        right = MAX_SECONDS
        center = audio.start + audio.duration / 2
        for fixed in sorted((item for item in self.audio_segments if item.coupled_to), key=lambda item: item.start):
            fixed_end = fixed.start + fixed.duration
            if fixed_end <= center:
                left = max(left, fixed_end)
            elif fixed.start >= center:
                right = min(right, fixed.start)
                break
        clips = sorted(
            (
                item for item in self.audio_segments
                if not item.coupled_to and item.start >= left - 1 / FPS
                and item.start + item.duration <= right + 1 / FPS
            ),
            key=lambda item: (item.start, item.id),
        )
        return left, right, clips

    def audio_neighbor_bounds(self, audio: AudioSegment) -> tuple[float, float]:
        others = sorted((item for item in self.audio_segments if item.id != audio.id), key=lambda item: item.start)
        left = max((item.start + item.duration for item in others if item.start + item.duration <= audio.start + 1 / FPS), default=0.0)
        right = min((item.start for item in others if item.start >= audio.start + audio.duration - 1 / FPS), default=MAX_SECONDS)
        return left, right

    def move_audio_segment(self, audio_id: str, start: float) -> None:
        audio = next((item for item in self.audio_segments if item.id == audio_id), None)
        if not audio or audio.coupled_to:
            return
        desired = max(0.0, min(round(start * FPS) / FPS, MAX_SECONDS - audio.duration))
        region_start, region_end, clips = self.audio_move_region(audio)
        if audio not in clips:
            clips.append(audio)
            clips.sort(key=lambda item: (item.start, item.id))
        current_index = clips.index(audio)
        others = [item for item in clips if item.id != audio.id]
        target_index = sum(desired >= item.start for item in others)
        if target_index != current_index:
            gaps = [max(0.0, clips[0].start - region_start)]
            gaps.extend(max(0.0, clips[index].start - (clips[index - 1].start + clips[index - 1].duration)) for index in range(1, len(clips)))
            reordered = others.copy()
            reordered.insert(target_index, audio)
            cursor = region_start + gaps[0]
            for index, item in enumerate(reordered):
                item.start = round(cursor * FPS) / FPS
                cursor = item.start + item.duration
                if index + 1 < len(reordered):
                    cursor += gaps[index + 1]
            action = "Audio clips reordered"
        else:
            previous_end = region_start if current_index == 0 else clips[current_index - 1].start + clips[current_index - 1].duration
            next_start = region_end if current_index == len(clips) - 1 else clips[current_index + 1].start
            audio.start = max(previous_end, min(desired, next_start - audio.duration))
            audio.start = round(audio.start * FPS) / FPS
            action = f"Audio moved to {audio.start:.2f}s"
        self.mark_dirty()
        self.update_audio_timeline()
        self.statusBar().showMessage(action)

    def trim_audio_segment(self, audio_id: str, start: float, duration: float, trim_start: int) -> None:
        audio = next((item for item in self.audio_segments if item.id == audio_id), None)
        if not audio or audio.coupled_to:
            return
        requested_start = max(0.0, min(round(start * FPS) / FPS, MAX_SECONDS - 1 / FPS))
        requested_end = min(MAX_SECONDS, requested_start + max(1 / FPS, round(duration * FPS) / FPS))
        left, right = self.audio_neighbor_bounds(audio)
        audio.start = max(left, requested_start)
        trim_start += round((audio.start - requested_start) * FPS)
        audio.trim_start = max(0, min(int(trim_start), audio.audio_duration_frames - 1))
        available = max(1, audio.audio_duration_frames - audio.trim_start) / FPS
        audio.duration = max(1 / FPS, min(requested_end - audio.start, right - audio.start, available))
        self.mark_dirty()
        self.update_audio_timeline()
        self.statusBar().showMessage(
            f"Audio trimmed: {audio.start:.2f}s–{audio.start + audio.duration:.2f}s"
        )

    def update_timeline_selection_style(self, selected_row: int) -> None:
        """Render selection on the card, not the QListWidget item beneath it."""
        for row in range(self.timeline.count()):
            card = self.timeline.itemWidget(self.timeline.item(row))
            if not isinstance(card, SegmentCard):
                continue
            selected = row == selected_row
            if card.property("selected") == selected:
                continue
            card.setProperty("selected", selected)
            card.style().unpolish(card)
            card.style().polish(card)
            card.update()

    def sync_order(self) -> None:
        if self._loading:
            return
        by_id = {segment.id: segment for segment in self.segments}
        self.segments = [by_id[self.timeline.item(row).data(Qt.ItemDataRole.UserRole)] for row in range(self.timeline.count())]
        self.update_timeline_layout()
        self.mark_dirty()

    def current_segment(self) -> Segment | None:
        row = self.timeline.currentRow()
        return self.segments[row] if 0 <= row < len(self.segments) else None

    def load_editor(self, row: int) -> None:
        self._loading = True
        segment = self.current_segment()
        self.duration_spin.setMaximum(MAX_SECONDS if len(self.segments) == 1 else 12.0)
        self.duration_spin.setToolTip(f"Segment duration in seconds (1–{int(self.duration_spin.maximum())})")
        self.segment_prompt.setPlainText(segment.prompt if segment else "")
        visual = bool(segment and segment.kind != "text")
        self.start_button.setEnabled(visual)
        self.end_button.setEnabled(visual)
        self.refine_timing_button.setEnabled(bool(segment))
        self.refine_prompt_button.setEnabled(bool(segment and segment.prompt.strip()))
        if segment:
            self.start_button.setChecked(segment.role == "start" if visual else False)
            self.end_button.setChecked(segment.role == "end" if visual else False)
            self.duration_spin.setValue(segment.duration)
            self.frame_number.setText(f"{'Text' if segment.kind == 'text' else 'Frame'} {row + 1}")
        else:
            self.frame_number.setText("Frame —")
        self._loading = False
        self.update_counts()

    def reload_clicked_segment(self, item: QListWidgetItem) -> None:
        """Refresh prompt text when the clicked segment is already selected."""
        self.refresh_segment_prompt_box(self.timeline.row(item))

    def refresh_segment_prompt_box(self, row: int) -> None:
        """Update only the segment prompt box, preserving all other editor state."""
        if not 0 <= row < len(self.segments):
            return
        previous_loading = self._loading
        self._loading = True
        try:
            self.segment_prompt.setPlainText(self.segments[row].prompt)
        finally:
            self._loading = previous_loading
        self.update_counts()

    def sync_selected_duration_control(self) -> None:
        """Keep the selected duration control synchronized with AI timing changes."""
        segment = self.current_segment()
        if not segment:
            return
        self.duration_spin.blockSignals(True)
        self.duration_spin.setMaximum(MAX_SECONDS if len(self.segments) == 1 else 12.0)
        self.duration_spin.setToolTip(f"Segment duration in seconds (1–{int(self.duration_spin.maximum())})")
        self.duration_spin.setValue(segment.duration)
        self.duration_spin.blockSignals(False)

    def save_prompt(self) -> None:
        if not self._loading and self.current_segment():
            self.current_segment().prompt = self.segment_prompt.toPlainText()
            self.refine_prompt_button.setEnabled(bool(self.current_segment().prompt.strip()))
            if self.current_segment().kind == "text":
                item = self.timeline.currentItem()
                card = self.timeline.itemWidget(item) if item else None
                if isinstance(card, SegmentCard):
                    card.set_text_preview(self.current_segment().prompt)
            self.mark_dirty()
            self.update_counts()

    def editor_duration_changed(self, value: float) -> None:
        if not self._loading and self.current_segment():
            self.change_duration(self.current_segment().id, value)
            self.update_timeline_layout()

    def set_role(self, role: str) -> None:
        segment = self.current_segment()
        if not segment or segment.kind == "text":
            return
        segment.role = role
        row = self.timeline.currentRow()
        item = self.timeline.item(row)
        card = self.timeline.itemWidget(item) if item else None
        if isinstance(card, SegmentCard):
            card.set_role(role)
        self.start_button.blockSignals(True)
        self.end_button.blockSignals(True)
        self.start_button.setChecked(role == "start")
        self.end_button.setChecked(role == "end")
        self.start_button.blockSignals(False)
        self.end_button.blockSignals(False)
        self.mark_dirty()

    def change_duration(self, segment_id: str, value: float) -> None:
        self.autofit_tail_extension = 0
        segment = next(item for item in self.segments if item.id == segment_id)
        allowed = min(value, MAX_SECONDS - (self.total_duration() - segment.duration))
        segment.duration = max(1, round(allowed * 2) / 2)
        self.mark_dirty()
        for row in range(self.timeline.count()):
            item = self.timeline.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == segment_id:
                self.timeline.setUpdatesEnabled(False)
                try:
                    item.setSizeHint(QSize(max(48, int(segment.duration * self.pixels_per_second)), max(152, self.timeline_height - 32)))
                    self.timeline.doItemsLayout()
                finally:
                    self.timeline.setUpdatesEnabled(True)
                card = self.timeline.itemWidget(item)
                if isinstance(card, SegmentCard):
                    card.update()
                    card.content.update()
                break
        self.timeline.viewport().update()
        self.update_audio_timeline()

    def finish_resize(self, segment_id: str) -> None:
        self.update_timeline_layout()

    def update_summary(self) -> None:
        main_total = self.total_duration()
        total = self.timeline_end_time()
        self.sequence_bar.setText(f"Sequence     Start: 0.00s  |  End: {total:.2f}s  |  Length: {total:.2f}s  |  Remaining: {MAX_SECONDS - total:.2f}s")
        self.add_tile.setText(f"＋\nAdd media\n{MAX_SECONDS - main_total:.1f}s available")
        self.add_tile.setEnabled(True)
        self.add_text_tile.setEnabled(len(self.segments) < MAX_SEGMENTS and main_total <= MAX_SECONDS - 1)
        self.applied_label.setText(f"Applied across all {len(self.segments)} segments")
        visual_count = sum(segment.kind != "text" for segment in self.segments)
        text_count = len(self.segments) - visual_count
        self.statusBar().showMessage(f"{visual_count} visual · {text_count} text · {len(self.audio_segments)} audio · {total:.1f}s")

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
        segment = self.current_segment()
        menu = QMenu(self)
        if segment and segment.kind != "text":
            menu.addAction("Replace media", self.replace_selected)
            menu.addAction("Export video" if segment.kind == "video" else "Export image", self.export_selected_segment)
            menu.addAction("Set as start frame", lambda: self.set_role("start"))
            menu.addAction("Set as end frame", lambda: self.set_role("end"))
        else:
            menu.addAction("Edit text prompt", self.segment_prompt.setFocus)
        menu.addSeparator()
        menu.addAction("Delete segment", self.delete_selected)
        menu.exec(self.timeline.mapToGlobal(point))

    def audio_menu(self, audio_id: str, global_point) -> None:
        audio = next((item for item in self.audio_segments if item.id == audio_id), None)
        if not audio:
            return
        menu = QMenu(self)
        if audio.coupled_to:
            menu.addAction("Decouple from video", lambda: self.decouple_audio(audio_id))
        else:
            menu.addAction("Set start time…", lambda: self.set_audio_start(audio_id))
        menu.addAction("Export audio", lambda: self.export_audio_segment(audio_id))
        menu.addSeparator()
        menu.addAction("Delete audio", lambda: self.delete_audio_segment(audio_id))
        menu.exec(global_point)

    def decouple_audio(self, audio_id: str) -> None:
        audio = next((item for item in self.audio_segments if item.id == audio_id), None)
        if not audio:
            return
        audio.coupled_to = None
        self.mark_dirty()
        self.update_audio_timeline()
        self.statusBar().showMessage("Audio decoupled; drag the waveform to move it independently")

    def set_audio_start(self, audio_id: str) -> None:
        audio = next((item for item in self.audio_segments if item.id == audio_id), None)
        if not audio or audio.coupled_to:
            return
        value, accepted = QInputDialog.getDouble(
            self, "Audio start time", "Start time in seconds:", audio.start,
            0.0, max(0.0, MAX_SECONDS - audio.duration), 2,
        )
        if accepted:
            self.move_audio_segment(audio_id, round(value * FPS) / FPS)

    def delete_audio_segment(self, audio_id: str) -> None:
        self.audio_segments = [item for item in self.audio_segments if item.id != audio_id]
        self.mark_dirty()
        self.update_audio_timeline()

    def export_audio_segment(self, audio_id: str) -> None:
        audio = next((item for item in self.audio_segments if item.id == audio_id), None)
        if not audio or not Path(audio.media_path).is_file():
            QMessageBox.warning(self, "Audio unavailable", "The source audio for this waveform is unavailable.")
            return
        downloads = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or str(Path.home())
        directory = Path(str(self.settings.value("segment_export_dir", downloads)))
        try:
            directory.mkdir(parents=True, exist_ok=True)
            base = f"{safe_export_name(self.current_project_name)} - {safe_export_name(Path(audio.name).stem)}"
            destination = directory / f"{base}.wav"
            counter = 2
            while destination.exists() and Path(audio.media_path).resolve() != destination.resolve():
                destination = directory / f"{base} ({counter}).wav"
                counter += 1
            if Path(audio.media_path).resolve() != destination.resolve():
                write_audio_clip(audio.media_path, destination, audio.trim_start, audio.duration, FPS)
            self.settings.setValue("segment_export_dir", str(destination.parent))
            self.statusBar().showMessage(f"Audio exported: {destination}")
        except OSError as error:
            QMessageBox.critical(self, "Audio export failed", str(error))

    def export_selected_segment(self) -> None:
        segment = self.current_segment()
        if not segment or segment.kind == "text":
            return
        downloads = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or str(Path.home())
        directory = Path(str(self.settings.value("segment_export_dir", self.settings.value("segment_save_dir", downloads))))
        suffix = ".webm" if segment.kind == "video" else (Path(segment.name).suffix or Path(segment.media_path).suffix or ".png")
        source = Path(segment.media_path)
        if not source.is_file():
            QMessageBox.warning(self, "Media unavailable", "The complete source media for this segment is not available.")
            return
        try:
            directory.mkdir(parents=True, exist_ok=True)
            row = max(0, self.timeline.currentRow()) + 1
            base = f"{safe_export_name(self.current_project_name)} - Segment {row:02d}"
            destination = directory / f"{base}{suffix}"
            counter = 2
            while destination.exists() and source.resolve() != destination.resolve():
                destination = directory / f"{base} ({counter}){suffix}"
                counter += 1
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
            self.settings.setValue("segment_export_dir", str(destination.parent))
            self.statusBar().showMessage(f"Segment exported: {destination}")
        except OSError as error:
            QMessageBox.critical(self, "Export failed", str(error))

    def replace_selected(self) -> None:
        segment = self.current_segment()
        if not segment or segment.kind == "text":
            return
        paths = choose_media_files(self, False, self.settings.value("last_media_dir", str(Path.home())))
        if not paths:
            return
        path = paths[0]
        self.settings.setValue("last_media_dir", str(Path(path).parent))
        try:
            kind, preview, frames, trim = prepare_media(path)
            self.audio_segments = [audio for audio in self.audio_segments if audio.coupled_to != segment.id]
            segment.name, segment.media_path, segment.preview_path, segment.kind = Path(path).name, path, preview, kind
            segment.media_duration_frames, segment.trim_start = frames, trim
            if kind == "video":
                self.add_video_audio(segment, self.segment_start_time(segment.id) or 0.0)
            self.mark_dirty()
            self.refresh_timeline(self.timeline.currentRow())
            self.statusBar().showMessage("Media replaced; timing, role and prompt preserved")
        except Exception as error:
            QMessageBox.critical(self, "Replace failed", str(error))

    def delete_selected(self) -> None:
        row = self.timeline.currentRow()
        if 0 <= row < len(self.segments):
            removed = self.segments.pop(row)
            self.audio_segments = [audio for audio in self.audio_segments if audio.coupled_to != removed.id]
            self.mark_dirty()
            self.refresh_timeline(max(0, row - 1))

    def delete_by_id(self, segment_id: str) -> None:
        row = next((index for index, segment in enumerate(self.segments) if segment.id == segment_id), -1)
        if row >= 0:
            removed = self.segments.pop(row)
            self.audio_segments = [audio for audio in self.audio_segments if audio.coupled_to != removed.id]
            self.mark_dirty()
            self.refresh_timeline(max(0, row - 1))

    def open_settings(self) -> None:
        if SettingsDialog(self.settings, self).exec():
            value = self.settings.value("ui_text_scale", 100, int)
            self.ui_scale_spin.blockSignals(True)
            self.ui_scale_spin.setValue(value)
            self.ui_scale_spin.blockSignals(False)
            self._apply_theme()
            self.update_provider_button()

    def set_ui_text_scale(self, value: int) -> None:
        value = max(75, min(200, value))
        self.settings.setValue("ui_text_scale", value)
        self._apply_theme()

    def update_spoken_dialog_controls(self, checked: bool) -> None:
        self.speaker_language.setEnabled(checked)
        self.speaker_accent.setEnabled(checked)
        self.mark_dirty()

    def build_director_request(self) -> str:
        """Compose focused planning controls into the authoritative model request."""
        lines = []
        creative_intent = self.intent.toPlainText().strip()
        if creative_intent:
            lines.append(creative_intent)
        requested_length = self.requested_length.value()
        if requested_length > 0:
            if len(self.segments) == 1:
                lines.append(
                    f"Requested total sequence length: {requested_length:.1f} seconds. "
                    "Because this is a single-frame sequence, return this exact duration for its one segment."
                )
            else:
                lines.append(
                    f"Requested total sequence length: {requested_length:.1f} seconds. "
                    "Distribute this duration across the supplied segments according to action complexity; the returned segment durations must add up to exactly this total."
                )
        if self.spoken_dialog.isChecked():
            context_default = "(Image/context provided)"
            language = self.speaker_language.currentText().strip() or context_default
            accent = self.speaker_accent.currentText().strip() or context_default
            lines.append(f"Speaker language selection: {language}. Speaker accent selection: {accent}.")
            if language.casefold() == context_default.casefold():
                lines.append(
                    "Determine the spoken language only from reliable context explicitly visible in the image or stated in Director's Intent, such as readable language or an unambiguous setting; otherwise use a culturally neutral language appropriate to the request."
                )
            if accent.casefold() == context_default.casefold():
                lines.append(
                    "Determine the accent only from reliable explicit context, never from physical appearance alone; when context is insufficient, use a neutral natural accent for the selected language."
                )
            lines.append(
                "In every Spoken Dialog clause, state the selected or context-supported language and accent directly beside the quoted words. Do not leave language or accent inference to LTX Video, and avoid stereotypes or caricature."
            )
        return "\n\n".join(lines)

    def build_refinement_request(self) -> str:
        lines = [self.build_director_request()]
        lines.append(
            "SFX option is ON: preserve and improve the selected prompt's required `SFX:` clause."
            if self.sfx.isChecked() else
            "SFX option is OFF: do not invent a new SFX clause, but do not delete deliberate existing prompt content."
        )
        lines.append(
            "Spoken Dialog option is ON: preserve or improve appropriate `Spoken Dialog:` wording, explicit language and accent, performance, and accurate lip-sync direction."
            if self.spoken_dialog.isChecked() else
            "Spoken Dialog option is OFF: do not invent new spoken dialog, but do not delete deliberate existing prompt content."
        )
        return "\n\n".join(line for line in lines if line)

    def ai_credentials(self) -> tuple[str, str, str] | None:
        provider = self.settings.value("provider", "gemini")
        key = self.session_keys.get(provider) or self.settings.value(f"{provider}_key", "")
        if not key:
            self.open_settings()
            key = self.session_keys.get(provider) or self.settings.value(f"{provider}_key", "")
        if not key:
            return None
        return provider, self.settings.value("gemini_model", GEMINI_MODELS[0]), key

    def set_ai_controls_enabled(self, enabled: bool) -> None:
        self.magic_button.setEnabled(enabled)
        segment = self.current_segment()
        self.refine_timing_button.setEnabled(enabled and bool(segment))
        self.refine_prompt_button.setEnabled(enabled and bool(segment and segment.prompt.strip()))

    def start_ai_worker(self, operation, args: tuple, activity: str, finished) -> None:
        retries = self.settings.value("api_retries", 2, int)
        retry_cooldown = self.settings.value("api_retry_cooldown", 10, int)
        self.set_ai_controls_enabled(False)
        self.ai_activity_title = "Magic Build" if operation is build_prompts else ("Refine Timing" if operation is refine_timing else "Refine Prompt")
        self.magic_overlay.update_attempt(1, retries + 1, activity)
        self.magic_overlay.show_overlay()
        worker = MagicWorker(operation, args, retries, retry_cooldown, activity)
        worker.signals.progress.connect(self.magic_progress)
        worker.signals.finished.connect(finished)
        worker.signals.failed.connect(self.magic_failed)
        self.thread_pool.start(worker)

    def refine_selected_timing(self) -> None:
        segment = self.current_segment()
        if not segment:
            return
        credentials = self.ai_credentials()
        if not credentials:
            return
        provider, model, key = credentials
        row = self.timeline.currentRow()
        self.refinement_segment_id = segment.id
        timeout = self.settings.value("api_timeout", 400, int)
        self.statusBar().showMessage(f"Refining timing for segment {row + 1}…")
        self.start_ai_worker(
            refine_timing,
            (self.segments.copy(), row, provider, model, key, self.build_refinement_request(), self.requested_length.value(), timeout),
            "Analyzing sequence context and refining selected timing…",
            self.refine_timing_finished,
        )

    def refine_selected_prompt(self) -> None:
        segment = self.current_segment()
        if not segment:
            return
        if not segment.prompt.strip():
            QMessageBox.information(self, "Refine Prompt", "Write or generate a segment prompt before refining it.")
            return
        credentials = self.ai_credentials()
        if not credentials:
            return
        provider, model, key = credentials
        row = self.timeline.currentRow()
        self.refinement_segment_id = segment.id
        timeout = self.settings.value("api_timeout", 400, int)
        self.statusBar().showMessage(f"Refining prompt for segment {row + 1}…")
        self.start_ai_worker(
            refine_segment_prompt,
            (self.segments.copy(), row, provider, model, key, self.build_refinement_request(), self.requested_length.value(), timeout),
            "Refining the selected prompt with adjacent-frame context…",
            self.refine_prompt_finished,
        )

    def refinement_target(self) -> tuple[int, Segment] | None:
        segment_id = getattr(self, "refinement_segment_id", "")
        for index, segment in enumerate(self.segments):
            if segment.id == segment_id:
                return index, segment
        return None

    def refine_timing_finished(self, result: dict) -> None:
        target = self.refinement_target()
        if target:
            index, segment = target
            durations = [item.duration for item in self.segments]
            durations[index] = float(result["duration"])
            self.mark_dirty()
            self.animate_timeline_durations(durations)
            self.save_library_project(automatic=True)
            self.statusBar().showMessage(f"Segment {index + 1} timing refined to {durations[index]:.1f}s; prompt text unchanged")
        self.set_ai_controls_enabled(True)
        self.magic_overlay.hide_overlay()

    def refine_prompt_finished(self, result: dict) -> None:
        target = self.refinement_target()
        if target:
            index, segment = target
            segment.prompt = str(result["prompt"]).strip()
            durations = [item.duration for item in self.segments]
            durations[index] = float(result["duration"])
            if self.timeline.currentRow() == index:
                self.refresh_segment_prompt_box(index)
            self.mark_dirty()
            self.animate_timeline_durations(durations)
            self.save_library_project(automatic=True)
            self.statusBar().showMessage(f"Segment {index + 1} prompt refined")
        self.set_ai_controls_enabled(True)
        self.magic_overlay.hide_overlay()

    def magic_build(self) -> None:
        if not self.segments:
            self.add_media()
            return
        credentials = self.ai_credentials()
        if not credentials:
            return
        provider, model, key = credentials
        self.statusBar().showMessage("Magic Build is analyzing optimized preview frames…")
        timeout = self.settings.value("api_timeout", 400, int)
        self.start_ai_worker(build_prompts, (
            self.segments.copy(), provider, model, key,
            self.build_director_request(), self.sfx.isChecked(), self.spoken_dialog.isChecked(), self.hdr.isChecked(), self.reduce_music.isChecked(), timeout,
        ), "Analyzing frames and directing motion…", self.magic_finished)

    def magic_progress(self, attempt: int, total: int, detail: str) -> None:
        self.magic_overlay.update_attempt(attempt, total, detail)

    def magic_finished(self, result: dict) -> None:
        target_durations = []
        remaining = MAX_SECONDS
        generated_segments = result["segments"]
        for index, (segment, generated) in enumerate(zip(self.segments, generated_segments)):
            segment.prompt = str(generated.get("prompt", segment.prompt))
            maximum_duration = MAX_SECONDS if len(generated_segments) == 1 else 12.0
            recommended = max(1, min(maximum_duration, round(float(generated.get("duration", segment.duration)) * 2) / 2))
            reserve = max(0, len(generated_segments) - index - 1)
            target = max(1, min(recommended, remaining - reserve))
            target_durations.append(target)
            remaining -= target
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
        self.refresh_segment_prompt_box(self.timeline.currentRow())
        self.mark_dirty()
        self.set_ai_controls_enabled(True)
        self.magic_overlay.hide_overlay()
        self.animate_timeline_durations(target_durations)
        self.save_library_project(automatic=True)
        self.statusBar().showMessage("Magic Build complete")

    def animate_timeline_durations(self, target_durations: list[float]) -> None:
        self.autofit_tail_extension = 0
        if not self.segments or self.timeline.count() != len(self.segments):
            for segment, target in zip(self.segments, target_durations):
                segment.duration = target
            self.refresh_timeline(self.timeline.currentRow())
            return
        start_widths = [self.timeline.item(row).sizeHint().width() for row in range(self.timeline.count())]
        target_widths = [max(48, int(duration * self.pixels_per_second)) for duration in target_durations]
        for segment, target in zip(self.segments, target_durations):
            segment.duration = target
        self.sync_coupled_audio()
        self.update_timeline_scroll_ranges()
        self.update_summary()
        self.sync_selected_duration_control()
        animation = QVariantAnimation(self)
        animation.setDuration(950)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def resize_tiles(progress: float) -> None:
            for row, (start, target) in enumerate(zip(start_widths, target_widths)):
                item = self.timeline.item(row)
                item.setSizeHint(QSize(round(start + (target - start) * progress), max(152, self.timeline_height - 32)))
                card = self.timeline.itemWidget(item)
                if card and hasattr(card, "duration_label"):
                    card.duration_label.setText(f"{target_durations[row]:.1f}s")
            self.timeline.doItemsLayout()
            self.timeline.viewport().update()

        animation.valueChanged.connect(resize_tiles)
        def finish_resize_animation() -> None:
            self.update_timeline_layout()
            self.sync_selected_duration_control()

        animation.finished.connect(finish_resize_animation)
        self.duration_animation = animation
        animation.start()

    def magic_failed(self, message: str) -> None:
        self.set_ai_controls_enabled(True)
        self.magic_overlay.hide_overlay()
        title = getattr(self, "ai_activity_title", "AI operation")
        QMessageBox.critical(self, f"{title} failed", message)
        self.statusBar().showMessage(f"{title} failed")

    def export_ltx(self) -> None:
        if not self.segments:
            return
        directory = Path(self.settings.value("last_document_dir", str(Path.home())))
        path = choose_document_save(self, "LTX Director Export", str(directory / f"{safe_export_name(self.current_project_name)}.json"), "LTX Director JSON (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        self.settings.setValue("last_document_dir", str(Path(path).parent))
        cursor = 0
        timeline = []
        for segment in self.segments:
            length = max(FPS, round(segment.duration * FPS))
            record = {"id": segment.id, "type": segment.kind, "start": cursor, "length": length, "prompt": segment.prompt}
            if segment.kind != "text":
                record.update({"imageFile": segment.media_path, "fileName": segment.name, "fileSize": Path(segment.media_path).stat().st_size if Path(segment.media_path).exists() else 0, "imageB64": data_url(segment.preview_path), "isEndFrame": segment.role == "end"})
                if segment.kind == "video":
                    record.update({"trimStart": segment.trim_start or 0, "videoDurationFrames": segment.media_duration_frames or length})
                    if Path(segment.media_path).exists():
                        record["videoB64"] = data_url(segment.media_path)
            timeline.append(record)
            cursor += length
        audio_timeline = []
        for audio in self.audio_segments:
            record = {
                "id": audio.id, "type": "audio", "start": round(audio.start * FPS),
                "length": max(1, round(audio.duration * FPS)), "trimStart": audio.trim_start,
                "audioDurationFrames": audio.audio_duration_frames, "audioFile": audio.media_path,
                "fileName": audio.name, "waveformPeaks": audio.waveform_peaks or [], "coupledTo": audio.coupled_to,
            }
            if Path(audio.media_path).is_file():
                record["audioB64"] = data_url(audio.media_path)
            audio_timeline.append(record)
        export_frames = max(cursor, max((item["start"] + item["length"] for item in audio_timeline), default=0))
        global_prompt = self.global_prompt.toPlainText()
        self.normalize_output_dimensions()
        payload = {"version": 1, "settings": {"start_second": 0, "end_second": export_frames / FPS, "duration_seconds": export_frames / FPS, "start_frame": 0, "end_frame": export_frames - 1, "duration_frames": export_frames, "epsilon": .99, "use_custom_audio": bool(audio_timeline), "use_custom_motion": False, "inpaint_audio": False, "frame_rate": FPS, "display_mode": "seconds", "custom_width": self.output_width.value(), "custom_height": self.output_height.value(), "resize_method": "maintain aspect ratio", "divisible_by": 32, "img_compression": 0, "override_audio": False}, "global_prompt": global_prompt, "retake_global_prompt": "", "timeline": {"mainTrackEnabled": True, "audioTrackEnabled": bool(audio_timeline), "motionTrackEnabled": False, "showFilenames": True, "overrideAudio": False, "inpaint_audio": False, "propHeight": 163, "globalPropHeight": 124, "global_prompt": global_prompt, "retake_global_prompt": "", "retakeMode": False, "retakeStart": 0, "retakeLength": 0, "retakePrompt": "", "retakeStrength": 1, "retakeVideo": None, "normalStartFrame": 0, "normalDurationFrames": cursor, "segments": timeline, "motionSegments": [], "audioSegments": audio_timeline}}
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
            video_starts: dict[str, float] = {}
            for index, raw in enumerate(payload["timeline"]["segments"][:MAX_SEGMENTS]):
                if raw.get("type") not in ("image", "video", "text"):
                    continue
                if raw.get("type") == "text":
                    loaded.append(Segment(raw.get("fileName", f"Text {index + 1}"), "", "", "text", "text", raw.get("prompt", ""), max(1, float(raw.get("length", FPS)) / fps), id=raw.get("id", "")))
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
                segment = Segment(raw.get("fileName", f"Segment {index + 1}"), str(media_path), str(cache), raw.get("type", "image"), "end" if raw.get("isEndFrame") else "start", raw.get("prompt", ""), max(1, float(raw.get("length", FPS)) / fps), raw.get("videoDurationFrames"), raw.get("trimStart"), raw.get("id", ""))
                loaded.append(segment)
                if segment.kind == "video":
                    video_starts[segment.id] = float(raw.get("start", 0)) / fps
            if not loaded:
                raise ValueError("No supported image, WebM, or text segments were found.")
            self.segments = loaded
            self.audio_segments = []
            for index, raw in enumerate(payload.get("timeline", {}).get("audioSegments", [])):
                audio_path = str(raw.get("audioFile") or raw.get("fileName") or "")
                embedded = str(raw.get("audioB64", ""))
                if embedded.startswith("data:audio/"):
                    cached_audio = APP_CACHE / f"import-audio-{index}-{Path(raw.get('fileName', 'sound.wav')).stem}.wav"
                    write_data_url(embedded, cached_audio)
                    audio_path = str(cached_audio)
                elif audio_path and not Path(audio_path).is_absolute():
                    candidate = Path(path).parent / audio_path
                    if candidate.is_file():
                        audio_path = str(candidate)
                self.audio_segments.append(AudioSegment(
                    raw.get("fileName", f"Audio {index + 1}.wav"), audio_path,
                    float(raw.get("start", 0)) / fps, max(1, int(raw.get("length", 1))) / fps,
                    int(raw.get("trimStart", 0)), int(raw.get("audioDurationFrames", raw.get("length", 1))),
                    raw.get("waveformPeaks", []), raw.get("coupledTo"), raw.get("id", ""),
                ))
            for segment in self.segments:
                if segment.kind == "video" and Path(segment.media_path).is_file() and not any(audio.coupled_to == segment.id for audio in self.audio_segments):
                    self.add_video_audio(segment, video_starts.get(segment.id, self.segment_start_time(segment.id) or 0.0))
            settings = payload.get("settings", {})
            self.output_width.setValue(int(settings.get("custom_width", 1280)))
            self.output_height.setValue(int(settings.get("custom_height", 704)))
            self.normalize_output_dimensions()
            self.global_prompt.setPlainText(payload.get("global_prompt") or payload.get("timeline", {}).get("global_prompt", ""))
            self.intent.clear()
            self.requested_length.setValue(0)
            self.speaker_language.setCurrentText("(Image/context provided)")
            self.speaker_accent.setCurrentText("(Image/context provided)")
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
            value["previewData"] = data_url(segment.preview_path) if segment.preview_path and Path(segment.preview_path).exists() else None
            value["sourceData"] = data_url(segment.media_path) if segment.media_path and Path(segment.media_path).exists() else None
            frames.append(value)
        audio_tracks = []
        for audio in self.audio_segments:
            value = audio.to_dict()
            value["sourceData"] = data_url(audio.media_path) if Path(audio.media_path).is_file() else None
            audio_tracks.append(value)
        return {"app": "ltx-director-director", "projectVersion": 6, "globalPrompt": self.global_prompt.toPlainText(), "directorIntent": self.intent.toPlainText(), "directionOptions": {"requestedLength": self.requested_length.value(), "speakerLanguage": self.speaker_language.currentText(), "speakerAccent": self.speaker_accent.currentText()}, "magicBuild": {"sfx": self.sfx.isChecked(), "spokenDialog": self.spoken_dialog.isChecked(), "hdr": self.hdr.isChecked(), "reduceMusic": self.reduce_music.isChecked()}, "output": {"width": self.output_width.value(), "height": self.output_height.value()}, "timelineView": {"scale": self.pixels_per_second, "height": self.timeline_height}, "frames": frames, "audioTracks": audio_tracks}

    def load_project_payload(self, payload: dict) -> None:
        if payload.get("app") not in {"ltx-director-director", "ltx-prompt-director-python"}:
            raise ValueError("This is not an LTX Director - Director project file.")
        self._loading = True
        loaded = []
        cache_key = uuid4().hex[:10]
        for index, original in enumerate(payload.get("frames", [])[:MAX_SEGMENTS]):
            raw = dict(original)
            is_text = raw.get("kind") == "text"
            preview_path = APP_CACHE / f"project-{cache_key}-{index}.jpg"
            if raw.get("previewData"):
                write_data_url(raw["previewData"], preview_path)
            media_path = "" if is_text else preview_path
            if raw.get("sourceData"):
                suffix = ".webm" if raw.get("kind") == "video" else Path(raw.get("name", "image.png")).suffix or ".png"
                media_path = APP_CACHE / f"project-source-{cache_key}-{index}{suffix}"
                write_data_url(raw["sourceData"], media_path)
            raw.update({"preview_path": "" if is_text else str(preview_path), "media_path": str(media_path)})
            for key in ("previewData", "sourceData"):
                raw.pop(key, None)
            loaded.append(Segment.from_dict(raw))
        if not loaded:
            raise ValueError("Project contains no supported main-track segments.")
        self.segments = loaded
        self.audio_segments = []
        for index, original in enumerate(payload.get("audioTracks", [])):
            raw = dict(original)
            media_path = str(raw.get("media_path", ""))
            if raw.get("sourceData"):
                destination = APP_CACHE / f"project-audio-{cache_key}-{index}.wav"
                write_data_url(raw["sourceData"], destination)
                media_path = str(destination)
            raw["media_path"] = media_path
            raw.pop("sourceData", None)
            self.audio_segments.append(AudioSegment.from_dict(raw))
        self.global_prompt.setPlainText(payload.get("globalPrompt", ""))
        self.intent.setPlainText(payload.get("directorIntent", ""))
        direction_options = payload.get("directionOptions", {})
        self.requested_length.setValue(float(direction_options.get("requestedLength", 0)))
        self.speaker_language.setCurrentText(str(direction_options.get("speakerLanguage", "(Image/context provided)")))
        self.speaker_accent.setCurrentText(str(direction_options.get("speakerAccent", direction_options.get("speakerNationality", "(Image/context provided)"))))
        self.sfx.setChecked(bool(payload.get("magicBuild", {}).get("sfx")))
        magic = payload.get("magicBuild", {})
        self.spoken_dialog.setChecked(bool(magic.get("spokenDialog", magic.get("vocals", False))))
        self.hdr.setChecked(bool(payload.get("magicBuild", {}).get("hdr")))
        self.reduce_music.setChecked(bool(payload.get("magicBuild", {}).get("reduceMusic")))
        output = payload.get("output", {})
        self.output_width.setValue(int(output.get("width", 1280)))
        self.output_height.setValue(int(output.get("height", 704)))
        self.normalize_output_dimensions()
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
        path = choose_document_save(self, "Project Export", str(directory / f"{safe_export_name(self.current_project_name)}.LTXD"), "LTX Director - Director Project (*.LTXD *.ltxd)")
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
