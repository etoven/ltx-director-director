from __future__ import annotations

import base64
import copy
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import warnings
import zipfile
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QBuffer, QIODevice, QDateTime, QEasingCurve, QEvent, QEventLoop, QObject, QRunnable, QPoint, QRectF, QSettings, QSize, QStandardPaths, Qt, QThreadPool, QTimer, QUrl, QVariantAnimation, Signal, Slot
from PySide6.QtGui import QAction, QActionGroup, QBrush, QColor, QCursor, QIcon, QImageReader, QPainter, QPen, QPixmap, QTextCharFormat, QTextCursor, QTextOption
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QColorDialog, QDateTimeEdit, QDockWidget, QGraphicsOpacityEffect, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLayout, QLineEdit,
    QListWidget, QListWidgetItem, QSplitter, QStackedWidget, QTabBar, QTabWidget, QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea,
    QSizePolicy, QSlider, QSpinBox, QProgressBar, QStatusBar, QStyle, QStyledItemDelegate, QStyleOptionSlider, QStyleOptionViewItem, QTextEdit, QToolBar, QToolButton, QToolTip, QVBoxLayout, QWidget, QWidgetAction,
)

from . import __version__
from .chatgpt_auth import ChatGPTAuth
from .chatgpt_widgets import ChatGPTSettingsPanel
from .ai_stream import STREAM_SINK
from .stream_widgets import ReasoningActivityDock, LiveOutputPane
from .ai_inline_diff import LiveTypingPreview, rich_diff_html, typing_batch_size
from .diff_review import ParagraphDiffReview, reset_plain_review_text
from .segment_plan import (supports_structured_edit, generate_combined, sync_timeline_to_prompt, sync_combined_from_timeline, preview_operations,
                           build_segment_objects, stream_operations, completed_stream_records,
                           explicit_existing_image_edit)
from .artwork_tools import IMAGE_MODEL_CHOICES, available_artwork_tools, render_artwork
from .cache_maintenance import cover_path, mark_ready, needs_refresh, warm_metadata
from .ai import AIResponseFormatError, GEMINI_MODELS, build_minimax_h3_prompt, build_minimax_h3_reference_prompt, build_prompts, minimax_h3_cache_key, provider_error_message, WORKSPACE_INSTRUCTIONS, refine_minimax_text_prompt, refine_minimax_h3_prompt, refine_minimax_h3_reference_prompt, refine_segment_prompt, generate_segment_prompt, refine_global_prompt, generate_global_direction, refine_sequence_prompts, refine_timing, retryable_connection_error
from .media import APP_CACHE, MEDIA_SUFFIXES, TIMELINE_VIDEO_SUFFIXES, comfy_input_references, copy_media_for_export, data_url, extract_audio_for_export, media_mime_from_header, media_suffix, prepare_media, safe_media_filename, thumbnail_for_image, unique_media_filename, video_source_dimensions, write_data_url
from .project_archive import clear_archived_source, materialize_source, project_thumbnail_data, read_project, save_project_archive
from .minimax_reference import WORKFLOW_NAMES, detect_workflow, reference_inventory, reference_slots
from .minimax_reference_widgets import MiniMaxReferenceSlot
from .generic_ai import build_generic_prompt, refine_generic_prompt, build_generic_segments, refine_generic_segment
from .downloads import DownloadTray
from .responsive_layout import ControlRow
from .catalog import MediaCatalog
from .comfy_export import export_minimax_workflow
from .media_labels import add_thumbnail_labels
from .workspaces import is_obsolete_stock_definition, WorkspaceStore
from .timing_limits import sequence_limit, fit_sequence, shot_limit, SHOT_COUNT_LIMIT
from .workspace_editor import WorkspaceEditor
from .shot_sequence import DEFAULTS as SHOT_DEFAULTS, settings as shot_settings, ensure_collection_sequences, shot_layout
from .shot_sequence_widgets import ShotSequencePanel
from .models import Segment, order_segments_by_ids, split_segment, split_segment_parts, roll_segment_boundary, text_segment_from_ltx
from .segment_split import divide_segment_prompt
from .timeline_timing import TIMING_FIELDS, plan_sequence_timing, retimed_segments
from .project_data import ARCHIVE_COLOR, load_other_tags, load_project_tags, new_note, normalize_notes, normalize_project_labels
from .spellcheck import install_spellcheck
from .prompt_tags import prompt_content, consume_global_refinements, install_prompt_tags, render_prompt_notes, insert_note
from .quick_fixes import QuickFixEditor, load_presets, preset_groups
from .effects_library import MIME as EFFECT_MIME, load_effects, refinement
from .effects_widgets import EffectsPanel
from .file_dialogs import choose_media_files, choose_document_open, choose_document_save, choose_directory
from .local_generation import LOCAL_PROGRESS, LOCAL_CANCEL, load_profile, save_profile, run_request, validate_profile
from .local_widgets import LocalModelsPanel, ImageGenerationPanel, ProgressPill, GenerationLog
from .inline_cues import PromptTextEdit
from .prompt_timecodes import retime_prompt
from .prompt_sections import document_sections
from .segment_prompt_review import SegmentPromptReview

FPS = 24
MIN_DURATION = 0.01
PROJECT_CARD_COLOR_ROLE = int(Qt.ItemDataRole.UserRole) + 1


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


def timeline_preview_pixmap(path: str) -> QPixmap:
    """Decode a timeline-sized preview instead of retaining an unbounded source image."""
    if not path:
        return QPixmap()
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and (size.width() > 1024 or size.height() > 1024):
        size.scale(QSize(1024, 1024), Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(size)
    image = reader.read()
    return QPixmap.fromImage(image) if not image.isNull() else QPixmap()


def requested_length_value(value: object) -> float:
    """Map legacy Auto/null project values to the spin box's zero=Auto value."""
    if value is None or (isinstance(value, str) and value.strip().casefold() in {"", "auto", "none", "null"}):
        return 0.0
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return 0.0
    return duration if math.isfinite(duration) and duration > 0 else 0.0


def application_window_title(project_name: str = "") -> str:
    title = f"Prompt Director v{__version__}"
    return f"{title} :: {project_name}" if project_name else title


def toolbar_icon(name: str) -> QIcon:
    """Return one of the bundled, theme-independent toolbar icons."""
    return QIcon(str(files("ltx_prompt_director").joinpath(f"assets/toolbar-{name}.svg")))


def prompt_action_icon(name: str) -> QIcon:
    """Small, high-contrast SVG icons for prompt generation and synchronization."""
    return QIcon(str(files("ltx_prompt_director").joinpath(f"assets/ai-{name}.svg")))


def _export_segment_source(segment: Segment, target: Path):
    source = Path(materialize_source(segment))
    if not source.is_file():
        raise FileNotFoundError("The complete source media for this segment is not available.")
    if segment.kind == 'video' and segment.shot_trimmed:
        from .segment_export import copy_video_interval
        return copy_video_interval(source,target,(segment.trim_start or 0)/24,segment.duration)
    return shutil.copy2(source, target)


def segment_media_suffix(segment: Segment) -> str:
    """Preserve a segment's real container instead of renaming video bytes."""
    source = Path(segment.media_path)
    if source.is_file():
        return media_suffix(source, ".webm" if segment.kind == "video" else ".png")
    if getattr(segment, "_source_suffix", ""):
        return segment._source_suffix
    return next((suffix for suffix in (source.suffix.lower(), Path(segment.name).suffix.lower())
                 if suffix in MEDIA_SUFFIXES.values()), ".webm" if segment.kind == "video" else ".png")


def segment_kind_label(segment: Segment) -> str:
    if segment.kind == "text":
        return "TEXT"
    if segment.kind == "image":
        return "IMAGE"
    container = segment_media_suffix(segment).lstrip(".").upper()
    return container if container in {"WEBM", "MP4"} else "VIDEO"




class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)
    stream = Signal(str, str)
    local_event = Signal(object)


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
        attempts = max(0, int(self.retries)) + 1
        for attempt in range(1, attempts + 1):
            try:
                self.signals.stream.emit("reset", f"{self.activity} · attempt {attempt}/{attempts}")
                self.signals.progress.emit(attempt, attempts, self.activity)
                self.signals.finished.emit(self.operation(*self.args))
                return
            except Exception as error:
                if attempt >= attempts or not retryable_connection_error(error):
                    message = provider_error_message(error)
                    if retryable_connection_error(error) and attempts > 1:
                        message = f"{message}\n\nStopped after {attempt} attempts."
                    self.signals.failed.emit(message)
                    return
                retry_subject = "AI response failed validation" if isinstance(error, AIResponseFormatError) else "Provider response stumbled"
                for remaining in range(self.retry_cooldown, 0, -1):
                    self.signals.progress.emit(attempt + 1, attempts, f"{retry_subject}—retrying in {remaining}s…")
                    time.sleep(1)
                if self.retry_cooldown == 0:
                    self.signals.progress.emit(attempt + 1, attempts, f"{retry_subject}—retrying now…")


class BackgroundWorker(QRunnable):
    """Run disk work off the GUI thread; deliver results via Qt queued signals."""

    def __init__(self, operation, args: tuple, cancel=None):
        super().__init__()
        self.operation = operation
        self.args = args
        self.cancel = cancel
        self.signals = WorkerSignals()

    def run(self) -> None:
        cancel_token = LOCAL_CANCEL.set(self.cancel)
        token = LOCAL_PROGRESS.set(self.signals.local_event.emit)
        try:
            result = self.operation(*self.args)
        except Exception as error:
            try:
                self.signals.failed.emit(str(error))
            except RuntimeError:
                pass  # The window closed while its queued disk job was finishing.
            return
        finally:
            LOCAL_PROGRESS.reset(token)
            LOCAL_CANCEL.reset(cancel_token)
        try:
            self.signals.finished.emit(result)
        except RuntimeError:
            pass  # A test or shutdown released the receiver before delivery.


class BackgroundCallbacks(QObject):
    def __init__(self, owner, finished, failed, progress=None):
        super().__init__(owner)
        self.owner = owner
        self.finished = finished
        self.failed = failed
        self.progress = progress

    @Slot(object)
    def on_local_event(self, event):
        if self.progress: self.progress(event)

    @Slot(object)
    def on_finished(self, result):
        self._complete(self.finished, result)

    @Slot(str)
    def on_failed(self, message):
        self.owner._close_after_jobs = False
        self._complete(self.failed, message)

    def _complete(self, callback, result):
        self.owner._pending_disk_jobs -= 1
        try:
            callback(result)
        finally:
            self.owner.update_closing_progress()
            if self.owner._close_after_jobs and not self.owner._pending_disk_jobs:
                # Let the completed progress state reach the screen before dismissing it.
                QTimer.singleShot(180, self.owner.close)
            self.deleteLater()


def _write_library_archive(path: str, payload: dict, segments: list[Segment], metadata_path: Path, meta: dict) -> None:
    save_project_archive(path, payload, segments)
    metadata_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")



def _duplicate_library_project_files(source_meta: dict, root: Path, new_id: str) -> dict:
    """Clone a saved project and its attachments without sharing deletable files."""
    source_id = str(source_meta["id"])
    source_metadata = root / f"{source_id}.meta.json"
    if source_metadata.is_file():
        source_meta = json.loads(source_metadata.read_text(encoding="utf-8"))
    source_path = Path(str(source_meta.get("projectPath", "")))
    if not source_path.is_file():
        raise FileNotFoundError(f"Saved project not found: {source_path}")

    original_name = str(source_meta.get("name") or "Untitled project").strip()
    existing = {str(item.get("name", "")).casefold()
                for path in root.glob("*.meta.json")
                for item in _read_duplicate_name(path)}
    new_name = f"{original_name} (Copy)"
    index = 2
    while new_name.casefold() in existing:
        new_name = f"{original_name} (Copy {index})"
        index += 1

    archive_path = root / f"{new_id}.LTXD"
    metadata_path = root / f"{new_id}.meta.json"
    if archive_path.exists() or metadata_path.exists():
        raise FileExistsError("The duplicate project ID already exists.")
    clone = copy.deepcopy(source_meta)
    clone.pop("kind", None)
    clone.pop("members", None)
    clone.update({"id": new_id, "name": new_name, "projectPath": str(archive_path),
                  "savedAt": datetime.now(timezone.utc).isoformat(), "archived": False})
    normalize_project_labels(clone)

    created: list[Path] = []
    temporary = root / f".{new_id}.copying"
    try:
        shutil.copy2(source_path, temporary)
        os.replace(temporary, archive_path)
        created.append(archive_path)

        def copy_attachment(path_value: str, folder: str, file_name: str) -> str:
            source = Path(str(path_value)) if path_value else None
            if source is None or not source.is_file():
                return ""
            destination_dir = root / folder
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination = destination_dir / (
                f"{new_id}_{uuid4().hex[:8]}_{safe_media_filename(file_name, 'attachment', suffix=media_suffix(source, source.suffix))}"
            )
            created.append(destination)
            shutil.copy2(source, destination)
            return str(destination)

        if clone.get("previewVideoPath"):
            source = Path(clone["previewVideoPath"])
            clone["previewVideoName"] = safe_media_filename(
                clone.get("previewVideoName") or source.name, "preview",
                suffix=media_suffix(source, source.suffix) if source.is_file() else source.suffix)
            clone["previewVideoPath"] = copy_attachment(
                clone["previewVideoPath"], "previews",
                str(clone.get("previewVideoName") or "preview.webm")
            )
        attached = clone.get("projectFiles", clone.get("workflowFiles", []))
        clone["projectFiles"] = [
            {**item, "path": duplicated,
             "name": safe_media_filename(item.get("name") or "project_file", suffix=Path(duplicated).suffix)}
            for item in attached if isinstance(item, dict)
            if (duplicated := copy_attachment(
                item.get("path", ""), "project_files",
                str(item.get("name") or "project_file")
            ))
        ]
        clone.pop("workflowFiles", None)

        if zipfile.is_zipfile(archive_path):
            _persist_library_archive(clone, metadata_path)
        else:
            payload = json.loads(archive_path.read_text(encoding="utf-8"))
            payload["library"] = {
                key: clone.get(key, "") for key in
                ("id", "name", "description", "collection", "savedAt", "status", "tags",
                 "tag", "archived", "notes", "previewVideoPath", "previewVideoName",
                 "projectFiles", "workflowFiles", "downloadDirectory")
            }
            archive_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        metadata_temp = root / f".{new_id}.meta.tmp"
        metadata_temp.write_text(json.dumps(clone, indent=2), encoding="utf-8")
        os.replace(metadata_temp, metadata_path)
        created.append(metadata_path)
        return clone
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
        (root / f".{new_id}.meta.tmp").unlink(missing_ok=True)


def _read_duplicate_name(path: Path) -> list[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return [value] if isinstance(value, dict) else []
    except (OSError, ValueError, TypeError):
        return []

def _prepare_media_batch(paths: list[str]) -> tuple[list[tuple[str, str, str, int | None, int | None]], list[str]]:
    prepared, errors = [], []
    for path in paths:
        try:
            kind, preview, frames, trim = prepare_media(path)
            prepared.append((path, kind, preview, frames, trim))
        except Exception as error:
            errors.append(f"{Path(path).name}: {error}")
    return prepared, errors


def _persist_library_archive(meta: dict, metadata_path: Path) -> None:
    """Update the archive in order; the UI already saved current gallery metadata."""
    project_path = Path(meta.get("projectPath", ""))
    if project_path.is_file() and zipfile.is_zipfile(project_path):
        with zipfile.ZipFile(project_path, "a") as archive:
            payload = json.loads(archive.read("project.json"))
            payload["library"] = {key: meta.get(key, "") for key in ("id", "name", "description", "collection", "savedAt", "status", "tags", "tag", "archived", "notes", "previewVideoPath", "previewVideoName", "projectFiles", "workflowFiles", "downloadDirectory")}
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr("project.json", json.dumps(payload, separators=(",", ":")), compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
    elif project_path.is_file() and project_path.stat().st_size < 1024 * 1024:
        payload = json.loads(project_path.read_text(encoding="utf-8"))
        payload["library"] = {key: meta.get(key, "") for key in ("id", "name", "description", "collection", "savedAt", "status", "tags", "tag", "archived", "notes", "previewVideoPath", "previewVideoName", "projectFiles", "workflowFiles", "downloadDirectory")}
        project_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _export_ltx_snapshot(segments: list[Segment], global_prompt: str, output_width: int, output_height: int,
                         media_directory: Path, path: str) -> None:
    """Prepare full-resolution ComfyUI media and JSON away from the GUI thread."""
    media_directory.mkdir(parents=True, exist_ok=True)
    cursor = 0
    timeline = []
    audio_timeline = []
    used_media_names: set[str] = set()
    for segment in segments:
        start = cursor
        length = max(1, round(segment.duration * FPS))
        record = {"id": segment.id, "type": segment.kind, "start": start, "length": length, "prompt": segment.prompt, "isEndFrame": False if segment.kind == "text" else segment.role == "end"}
        if segment.kind != "text":
            source = Path(materialize_source(segment))
            record.update({"imageFile": str(source), "fileName": segment.name,
                           "fileSize": source.stat().st_size if source.exists() else 0,
                           "imageB64": data_url(segment.preview_path) if segment.preview_path else ""})
            if segment.kind == "image" and source.exists():
                image_path = copy_media_for_export(str(source), segment.name, media_directory, used_media_names, "image")
                image_file, image_preview = comfy_input_references(image_path.name)
                record.update({"imageFile": image_file, "imageB64": image_preview})
                record.pop("fileName", None)
                record.pop("fileSize", None)
            elif segment.kind == "video":
                record.update({"trimStart": segment.trim_start or 0, "videoDurationFrames": segment.media_duration_frames or length})
                if source.exists():
                    video_path = copy_media_for_export(str(source), segment.name, media_directory, used_media_names, "video")
                    video_name = video_path.name
                    video_file, _ = comfy_input_references(video_name)
                    record.update({"imageFile": video_file, "fileName": video_name, "fileSize": video_path.stat().st_size})
                    record["videoB64"] = data_url(str(video_path))
                    audio_name = unique_media_filename(f"{video_path.stem}_extracted_audio.wav", used_media_names)
                    audio_path = media_directory / audio_name
                    try:
                        audio_duration_frames, peaks = extract_audio_for_export(str(video_path), audio_path, FPS)
                    except ValueError:
                        pass
                    else:
                        audio_file, _ = comfy_input_references(audio_name)
                        audio_timeline.append({
                            "id": f"{segment.id}_a", "type": "audio", "start": start,
                            "length": length, "trimStart": segment.trim_start or 0,
                            "audioDurationFrames": audio_duration_frames,
                            "audioFile": audio_file, "fileName": video_name,
                            "waveformPeaks": peaks, "fileSize": audio_path.stat().st_size,
                        })
        timeline.append(record)
        cursor += length
    payload = {"version": 1, "settings": {"start_second": 0, "end_second": cursor / FPS, "duration_seconds": cursor / FPS, "start_frame": 0, "end_frame": cursor, "duration_frames": cursor, "epsilon": .99, "use_custom_audio": bool(audio_timeline), "use_custom_motion": False, "inpaint_audio": True, "frame_rate": FPS, "display_mode": "seconds", "custom_width": output_width, "custom_height": output_height, "resize_method": "crop", "divisible_by": 32, "img_compression": 0, "override_audio": False}, "global_prompt": global_prompt, "retake_global_prompt": "", "timeline": {"mainTrackEnabled": True, "audioTrackEnabled": bool(audio_timeline), "motionTrackEnabled": False, "showFilenames": True, "overrideAudio": False, "inpaint_audio": True, "propHeight": 163, "globalPropHeight": 124, "global_prompt": global_prompt, "retake_global_prompt": "", "retakeMode": False, "retakeStart": 0, "retakeLength": 0, "retakePrompt": "", "retakeStrength": 1, "retakeVideo": None, "normalStartFrame": 0, "normalDurationFrames": cursor, "segments": timeline, "motionSegments": [], "audioSegments": audio_timeline}}
    temporary_fd, temporary_name = tempfile.mkstemp(prefix=f".{Path(path).name}.", suffix=".tmp", dir=Path(path).parent)
    try:
        with os.fdopen(temporary_fd, "w", encoding="utf-8") as output:
            json.dump(payload, output, indent=2)
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


class TimelineListWidget(QListWidget):
    files_dropped = Signal(list, int)
    files_replaced = Signal(list, int)
    projects_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scroll_animation = None
        self._scroll_target = 0
        self._drop_index = -1
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
        supported = {".png", ".jpg", ".jpeg", ".webp", ".gif", *TIMELINE_VIDEO_SUFFIXES}
        return [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() in supported]

    @staticmethod
    def _project_paths(event) -> list[str]:
        if not event.mimeData().hasUrls():
            return []
        supported = {".ltxd", ".json"}
        return [
            url.toLocalFile() for url in event.mimeData().urls()
            if url.isLocalFile() and Path(url.toLocalFile()).is_file() and Path(url.toLocalFile()).suffix.casefold() in supported
        ]

    def dragEnterEvent(self, event) -> None:
        if self._media_paths(event) or self._project_paths(event):
            self.setProperty("dropActive", True)
            self.style().unpolish(self)
            self.style().polish(self)
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        media_paths = self._media_paths(event)
        project_paths = self._project_paths(event)
        if media_paths or project_paths:
            replacing = bool(getattr(event, "modifiers", lambda: Qt.KeyboardModifier.NoModifier)() & Qt.KeyboardModifier.ControlModifier)
            target = self.itemAt(event.position().toPoint()) if replacing else None
            self._drop_index = self.row(target) if target else self.insertion_index(event.position().toPoint()) if media_paths else -1
            self.viewport().update()
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:
        self._clear_drop_state()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        paths = self._media_paths(event)
        project_paths = self._project_paths(event)
        insertion_index = self.insertion_index(event.position().toPoint())
        self._clear_drop_state()
        if project_paths:
            self.projects_dropped.emit(project_paths)
            event.acceptProposedAction()
            return
        if paths:
            target = self.itemAt(event.position().toPoint()) if getattr(event, "modifiers", lambda: Qt.KeyboardModifier.NoModifier)() & Qt.KeyboardModifier.ControlModifier else None
            if target:
                self.files_replaced.emit(paths, self.row(target))
            else:
                self.files_dropped.emit(paths, insertion_index)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def _clear_drop_state(self) -> None:
        self._drop_index = -1
        self.setProperty("dropActive", False)
        self.style().unpolish(self)
        self.style().polish(self)
        self.viewport().update()

    def insertion_index(self, point) -> int:
        item = self.itemAt(point)
        if item is None:
            return self.count()
        row = self.row(item)
        rect = self.visualItemRect(item)
        return row + (1 if point.x() >= rect.center().x() else 0)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._drop_index < 0:
            return
        if self._drop_index < self.count():
            x = self.visualItemRect(self.item(self._drop_index)).left()
        elif self.count():
            x = self.visualItemRect(self.item(self.count() - 1)).right()
        else:
            x = 4
        painter = QPainter(self.viewport())
        painter.setPen(QPen(QColor("#68b9ee"), 4))
        painter.drawLine(x, 4, x, self.viewport().height() - 4)


class ProjectListWidget(QListWidget):
    drag_started = Signal()
    order_changed = Signal()
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._press_position = None
        self._drag_row = -1
        self._reordering = False
        self._scroll_animation = None
        self._scroll_target = 0
        self.setDragDropMode(QListWidget.DragDropMode.NoDragDrop)
        self.setAcceptDrops(True)
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

    @staticmethod
    def _project_paths(event) -> list[str]:
        if not event.mimeData().hasUrls():
            return []
        supported = {".ltxd", ".json"}
        return [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile() and Path(url.toLocalFile()).is_file() and Path(url.toLocalFile()).suffix.casefold() in supported]

    def _set_drop_active(self, active: bool) -> None:
        self.setProperty("dropActive", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event) -> None:
        if self._project_paths(event):
            self._set_drop_active(True)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._project_paths(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_drop_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        paths = self._project_paths(event)
        self._set_drop_active(False)
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        event.ignore()


class ProjectTileDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        color_value = str(index.data(PROJECT_CARD_COLOR_ROLE) or "")
        border = QColor(color_value).lighter(125) if color_value else QColor("#3b464b")
        card = option.rect.adjusted(4, 4, -4, -4)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        fill = QColor(color_value).darker(150) if color_value else QColor("#22282b")
        if option.state & QStyle.StateFlag.State_MouseOver:
            fill = fill.lighter(130)
            border = QColor("#68b9ee")
        painter.setBrush(fill)
        painter.setPen(QPen(border, 2 if color_value else 1))
        painter.drawRoundedRect(card, 7, 7)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#d9eefb"), 1))
            painter.drawRoundedRect(card.adjusted(3, 3, -3, -3), 5, 5)
        painter.restore()
        clean = QStyleOptionViewItem(option)
        clean.state &= ~QStyle.StateFlag.State_Selected
        clean.state &= ~QStyle.StateFlag.State_MouseOver
        clean.state &= ~QStyle.StateFlag.State_HasFocus
        clean.backgroundBrush = QBrush(Qt.BrushStyle.NoBrush)
        super().paint(painter, clean, index)


class SeekSlider(QSlider):
    """A normal slider that also seeks directly to a clicked groove position."""

    seek_requested = Signal(int)
    scrub_started = Signal()
    scrub_finished = Signal()

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._groove_drag=False
        self.sliderMoved.connect(self.seek_requested.emit)
        self.sliderReleased.connect(lambda: self.seek_requested.emit(self.value()) if not self._groove_drag else None)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self.orientation() != Qt.Orientation.Horizontal:
            super().mousePressEvent(event)
            return

        self.scrub_started.emit()

        option = QStyleOptionSlider()
        self.initStyleOption(option)
        handle = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            option,
            QStyle.SubControl.SC_SliderHandle,
            self,
        )
        if handle.contains(event.position().toPoint()):
            super().mousePressEvent(event)
            return

        self._groove_drag=True;self.setSliderDown(True)
        self.seek_at(event.position().x(),option,handle)
        event.accept()

    def seek_at(self,x,option=None,handle=None):
        if option is None:
            option=QStyleOptionSlider();self.initStyleOption(option)
            handle=self.style().subControlRect(QStyle.ComplexControl.CC_Slider,option,QStyle.SubControl.SC_SliderHandle,self)
        span = max(1, self.width() - handle.width())
        click_position = round(x - handle.width() / 2)
        value = QStyle.sliderValueFromPosition(
            self.minimum(),
            self.maximum(),
            max(0, min(span, click_position)),
            span,
            option.upsideDown,
        )
        blocked=self.blockSignals(True)
        self.setValue(value)
        self.blockSignals(blocked)
        self.seek_requested.emit(value)

    def mouseMoveEvent(self,event) -> None:
        if self._groove_drag:self.seek_at(event.position().x());event.accept()
        else:super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event) -> None:
        super().mouseReleaseEvent(event)
        self.setSliderDown(False);self._groove_drag=False
        if event.button()==Qt.MouseButton.LeftButton:self.scrub_finished.emit()


class ProjectVideoWidget(QVideoWidget):
    video_dropped = Signal(str)
    drag_active_changed = Signal(bool)
    play_requested = Signal()
    fullscreen_requested = Signal()
    export_frame_requested = Signal()
    copy_frame_requested = Signal()

    VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumSize(160, 90)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Ignored)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.fullscreen_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        export_frame = menu.addAction("Export Current Frame…")
        copy_frame = menu.addAction("Copy Current Frame to Clipboard")
        selected = menu.exec(event.globalPos())
        if selected == export_frame:
            self.export_frame_requested.emit()
        elif selected == copy_frame:
            self.copy_frame_requested.emit()
        event.accept()

    def dragEnterEvent(self, event) -> None:
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if len(urls) == 1 and urls[0].isLocalFile() and Path(urls[0].toLocalFile()).suffix.lower() in self.VIDEO_SUFFIXES:
            self.drag_active_changed.emit(True)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        self.dragEnterEvent(event)

    def dragLeaveEvent(self, event) -> None:
        self.drag_active_changed.emit(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self.drag_active_changed.emit(False)
        path = event.mimeData().urls()[0].toLocalFile()
        self.video_dropped.emit(path)
        event.acceptProposedAction()


class WorkflowDropList(QListWidget):
    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    @staticmethod
    def _json_paths(event) -> list[str]:
        if not event.mimeData().hasUrls():
            return []
        return [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile() and Path(url.toLocalFile()).is_file()]

    def _set_drop_active(self, active: bool) -> None:
        self.setProperty("dropActive", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event) -> None:
        if self._json_paths(event):
            self._set_drop_active(True)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._json_paths(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_drop_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        paths = self._json_paths(event)
        self._set_drop_active(False)
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        event.ignore()


class ProjectFilesPanel(QWidget):
    add_requested = Signal()
    file_dropped = Signal(str)
    export_requested = Signal()
    remove_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("projectFilesPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)
        self.title = QLabel("No saved project selected")
        self.title.setObjectName("previewTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.files = WorkflowDropList()
        self.files.setObjectName("projectWorkflowList")
        self.files.files_dropped.connect(self.files_dropped)
        controls = ControlRow()
        self.add_button = QPushButton("Add Files…")
        self.add_button.clicked.connect(self.add_requested)
        self.export_button = QPushButton("Export")
        self.export_button.clicked.connect(self.export_requested)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_requested)
        controls.addWidget(self.add_button)
        controls.addWidget(self.export_button)
        controls.addWidget(self.remove_button)
        layout.addWidget(self.title)
        layout.addWidget(self.files, 1)
        layout.addLayout(controls)
        self.files.currentRowChanged.connect(self.selection_changed)
        self.clear_project()

    def files_dropped(self, paths: list[str]) -> None:
        for path in paths:
            self.file_dropped.emit(path)

    def selection_changed(self, row: int) -> None:
        enabled = row >= 0
        self.export_button.setEnabled(enabled)
        self.remove_button.setEnabled(enabled)

    def selected_file(self) -> dict | None:
        item = self.files.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def set_project(self, name: str, project_files: list[dict] | None = None) -> None:
        self.title.setText(name)
        self.add_button.setEnabled(True)
        self.files.clear()
        for project_file in project_files or []:
            item = QListWidgetItem(str(project_file.get("name") or Path(str(project_file.get("path", ""))).name))
            item.setData(Qt.ItemDataRole.UserRole, project_file)
            self.files.addItem(item)
        self.files.setCurrentRow(0 if self.files.count() else -1)
        self.selection_changed(self.files.currentRow())

    def clear_project(self) -> None:
        self.title.setText("No saved project selected")
        self.add_button.setEnabled(False)
        self.files.clear()
        self.selection_changed(-1)


class FullscreenVideoDialog(QDialog):
    def __init__(self, player: QMediaPlayer, parent=None):
        super().__init__(parent)
        self.player = player
        self.setWindowTitle("Project Video Preview")
        self.setModal(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.video = ProjectVideoWidget(self)
        layout.addWidget(self.video, 1)
        controls = QFrame()
        controls.setObjectName("fullscreenPlayerControls")
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(12, 8, 12, 8)
        self.play = QPushButton("▶ Play")
        self.play.clicked.connect(self.toggle_playback)
        self.seek = SeekSlider(Qt.Orientation.Horizontal)
        self.seek.seek_requested.connect(self.seek_to)
        self.position = QLabel("00:00 / 00:00")
        exit_button = QPushButton("✕ Exit Fullscreen")
        exit_button.clicked.connect(self.reject)
        controls_layout.addWidget(self.play)
        controls_layout.addWidget(self.seek, 1)
        controls_layout.addWidget(self.position)
        controls_layout.addWidget(exit_button)
        layout.addWidget(controls)

    def toggle_playback(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def seek_to(self, position: int) -> None:
        self.player.setPosition(max(0, min(position, self.player.duration())))

    def update_position(self, position: int, duration: int) -> None:
        if not self.seek.isSliderDown():
            self.seek.setValue(position)
        self.position.setText(f"{ProjectPreviewPanel.time_text(position)} / {ProjectPreviewPanel.time_text(duration)}")


class ProjectPreviewPanel(QWidget):
    video_dropped = Signal(str)
    choose_requested = Signal()
    export_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("projectPreviewPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setProperty("dropActive", False)
        self.setAcceptDrops(True)
        self.duration = 0
        self._source_generation = 0
        self.current_frame = None
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setMuted(False)
        self.audio.setVolume(1.0)
        self.player.setAudioOutput(self.audio)
        self.video = ProjectVideoWidget(self)
        self.fullscreen_window = FullscreenVideoDialog(self.player, self)
        self.fullscreen_window.finished.connect(self.restore_embedded_video)
        self.player.setVideoOutput(self.video)
        self.video.video_dropped.connect(self.video_dropped)
        self.video.drag_active_changed.connect(self.set_drop_active)
        self.video.play_requested.connect(self.toggle_playback)
        self.video.fullscreen_requested.connect(self.open_fullscreen)
        for video_output in (self.video, self.fullscreen_window.video):
            video_output.export_frame_requested.connect(self.export_current_frame)
            video_output.copy_frame_requested.connect(self.copy_current_frame)
            video_output.videoSink().videoFrameChanged.connect(self.capture_frame)
        self.fullscreen_window.video.fullscreen_requested.connect(self.fullscreen_window.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)
        self.title = QLabel("No saved project selected")
        self.title.setObjectName("previewTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty = QLabel("Save or open a project, then drop a rendered video here")
        self.empty.setObjectName("previewEmpty")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        video_stack = QVBoxLayout()
        video_stack.setContentsMargins(0, 0, 0, 0)
        video_stack.addWidget(self.video, 1)
        video_stack.addWidget(self.empty)
        layout.addWidget(self.title)
        layout.addLayout(video_stack, 1)
        seek_row = QHBoxLayout()
        self.position = QLabel("00:00 / 00:00")
        self.seek = SeekSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.seek_requested.connect(self.seek_to)
        seek_row.addWidget(self.seek, 1)
        seek_row.addWidget(self.position)
        layout.addLayout(seek_row)
        controls = ControlRow()
        self.play = QPushButton("▶ Play")
        self.play.clicked.connect(self.toggle_playback)
        self.choose = QPushButton("Add Video…")
        self.choose.clicked.connect(self.choose_requested)
        self.export = QPushButton("Export Video…")
        self.export.clicked.connect(self.export_requested)
        self.fullscreen = QPushButton("⛶ Fullscreen")
        self.fullscreen.clicked.connect(self.open_fullscreen)
        controls.addWidget(self.play)
        controls.addWidget(self.choose)
        controls.addWidget(self.export)
        controls.addWidget(self.fullscreen)
        layout.addLayout(controls)
        self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(self.duration_changed)
        self.player.playbackStateChanged.connect(self.playback_changed)
        self.player.mediaStatusChanged.connect(self.media_status_changed)
        self.player.errorOccurred.connect(self.media_error)
        self.clear_project()

    def capture_frame(self, frame) -> None:
        image = frame.toImage()
        if not image.isNull():
            self.current_frame = image.copy()

    def frame_available(self) -> bool:
        if self.current_frame is not None and not self.current_frame.isNull():
            return True
        QMessageBox.information(self, "Video Frame", "No video frame is available yet.")
        return False

    def export_current_frame(self) -> None:
        if not self.frame_available():
            return
        source = Path(self.player.source().toLocalFile())
        stem = source.stem or "video"
        suggested = f"{stem}_{self.player.position():010d}ms.png"
        owner = self.window()
        while owner is not None and not hasattr(owner, "next_export_path"):
            owner = owner.parent()
        if owner is None:
            return
        path = owner.next_export_path(suggested)
        if not self.current_frame.save(str(path)):
            owner._reserved_exports.discard(str(path))
            QMessageBox.warning(self, "Export Current Frame", "The current frame could not be saved.")
            return
        owner.record_export(path)
        self.show_player_message(f"Exported frame to {path.name}")

    def copy_current_frame(self) -> None:
        if not self.frame_available():
            return
        QApplication.clipboard().setImage(self.current_frame)
        self.show_player_message("Copied current frame to the clipboard")

    def show_player_message(self, message: str) -> None:
        window = self.window()
        if isinstance(window, QMainWindow):
            window.statusBar().showMessage(message, 3500)

    def dragEnterEvent(self, event) -> None:
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if self.choose.isEnabled() and len(urls) == 1 and urls[0].isLocalFile() and Path(urls[0].toLocalFile()).suffix.lower() in ProjectVideoWidget.VIDEO_SUFFIXES:
            self.set_drop_active(True)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        self.dragEnterEvent(event)

    def dragLeaveEvent(self, event) -> None:
        self.set_drop_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self.set_drop_active(False)
        self.video_dropped.emit(event.mimeData().urls()[0].toLocalFile())
        event.acceptProposedAction()

    def set_drop_active(self, active: bool) -> None:
        self.setProperty("dropActive", active)
        self.style().unpolish(self)
        self.style().polish(self)

    @staticmethod
    def time_text(milliseconds: int) -> str:
        total = max(0, int(milliseconds) // 1000)
        return f"{total // 60:02d}:{total % 60:02d}"

    def release_memory_video(self):
        if getattr(self, "_memory_buffer", None) is not None:
            self.player.stop()
            self.player.setSource(QUrl())
            self._memory_buffer.close()
            self._memory_buffer.deleteLater()
            self._memory_buffer = None

    def set_memory_video(self, name: str, data: bytes, position: int = 0, autoplay: bool = True) -> None:
        self.clear_project()
        self.title.setText(name)
        self._memory_preview = True
        self._initial_media_position = max(0,position)
        self._initial_media_autoplay = autoplay
        self._initial_media_pending = True
        self.pending_preview_path = ""
        self._memory_buffer = QBuffer(self)
        self._memory_buffer.setData(data)
        self._memory_buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.player.setAudioOutput(self.audio)
        self.player.setSourceDevice(self._memory_buffer, QUrl("ram-preview.webm"))
        self.choose.setEnabled(True);self.export.setEnabled(True)
        self.empty.setText("Loading continuous RAM preview…")

    def set_project(self, name: str, video_path: str = "") -> None:
        self.release_memory_video()
        self._generation_preview_active = False
        self._memory_preview = False
        self._initial_media_pending = False
        self.title.setText(name)
        self.choose.setEnabled(True)
        self.pending_preview_path = video_path
        path = Path(video_path)
        try:
            valid_preview = path.is_file() and path.suffix.casefold() in ProjectVideoWidget.VIDEO_SUFFIXES and path.stat().st_size > 0
        except OSError:
            valid_preview = False
        current_path = Path(self.player.source().toLocalFile()) if self.player.source().isLocalFile() else None
        same_preview = False
        if valid_preview and current_path:
            try:
                same_preview = current_path.resolve() == path.resolve()
            except OSError:
                same_preview = False
        if same_preview:
            self.player.setAudioOutput(self.audio)
            self.export.setEnabled(True)
            self.fullscreen.setEnabled(self.duration > 0)
            if self.duration > 0:
                self.empty.hide()
                self.video.show()
            return

        self._source_generation += 1
        generation = self._source_generation
        self.player.stop()
        self.current_frame = None
        self.player.setSource(QUrl())
        if valid_preview:
            self.video.hide()
            self.empty.setText("Loading saved video preview…")
            self.empty.show()
            self.export.setEnabled(True)
            self.fullscreen.setEnabled(False)
            if self.isVisible():
                QTimer.singleShot(0, lambda p=str(path), token=generation: self.load_project_video(p, token))
            else:
                self.empty.setText("Saved video preview loads when this panel opens")
        else:
            self.video.hide()
            self.empty.setText("Drop a rendered video here\nor use Add Video")
            self.empty.show()
            self.export.setEnabled(False)
            self.fullscreen.setEnabled(False)

    def load_project_video(self, path: str, generation: int) -> None:
        """Activate saved preview media only after project/timeline loading has returned."""
        if generation != self._source_generation or not self.isVisible():
            return
        source = Path(path)
        if not source.is_file() or source.suffix.casefold() not in ProjectVideoWidget.VIDEO_SUFFIXES:
            self.media_error()
            return
        self._initial_media_pending = True
        self.player.setAudioOutput(self.audio)
        self.player.setSource(QUrl.fromLocalFile(str(source)))

    def set_preview_visible(self, visible: bool) -> None:
        if visible:
            pending = getattr(self, "pending_preview_path", "")
            if pending and Path(pending).is_file() and not self.player.source().isLocalFile():
                QTimer.singleShot(0, lambda p=pending, token=self._source_generation: self.load_project_video(p, token))
        else:
            self._source_generation += 1
            self.player.stop()
            self.player.setSource(QUrl())
            self.current_frame = None

    def media_error(self, *_args) -> None:
        self.player.stop()
        self.current_frame = None
        self.player.setSource(QUrl())
        self.video.hide()
        self.empty.setText("Saved video preview could not be loaded\nDrop a replacement video here")
        self.empty.show()
        self.export.setEnabled(False)
        self.fullscreen.setEnabled(False)

    def clear_project(self) -> None:
        self.release_memory_video()
        self._generation_preview_active = False
        self._memory_preview = False
        self._initial_media_pending = False
        self._source_generation += 1
        self.player.stop()
        self.current_frame = None
        self.player.setSource(QUrl())
        self.title.setText("No saved project selected")
        self.video.hide()
        self.empty.setText("Save or open a project, then drop a rendered video here")
        self.empty.show()
        self.choose.setEnabled(False)
        self.export.setEnabled(False)
        self.fullscreen.setEnabled(False)
        self.seek.setRange(0, 0)
        self.position.setText("00:00 / 00:00")

    def toggle_playback(self) -> None:
        # Only the first load may initialize playback; buffered/seek events must
        # never undo the user's pause or stop a replay at position zero.
        self._initial_media_pending = False
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            if getattr(self,'_generation_preview_active',False):self._generation_autoplay=False
            self.player.pause()
        else:
            if getattr(self,'_generation_preview_active',False):self._generation_autoplay=True
            if self.player.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia or (self.duration and self.player.position() >= self.duration):
                self.player.setPosition(0)
            self.player.play()

    def seek_to(self, position: int) -> None:
        if self.duration > 0:
            self.player.setPosition(max(0, min(position, self.duration)))

    def duration_changed(self, duration: int) -> None:
        self.duration = max(0, duration)
        self.seek.setRange(0, self.duration)
        self.fullscreen_window.seek.setRange(0, self.duration)
        self.position_changed(self.player.position())

    def media_status_changed(self, status) -> None:
        if status in {QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia}:
            self.empty.hide()
            self.video.show()
            self.fullscreen.setEnabled(True)
            if not getattr(self, "_initial_media_pending", False):return
            self._initial_media_pending = False
            if getattr(self, "_memory_preview", False) or getattr(self,'_generation_preview_active',False):
                self.player.setPosition(min(self.duration,self._initial_media_position) if self._initial_media_autoplay else min(self.duration,max(1,self._initial_media_position)))
                if self._initial_media_autoplay:self.player.play()
                else:self.player.pause()
            else:
                self.player.setPosition(1)
                self.player.pause()

    def position_changed(self, position: int) -> None:
        if not self.seek.isSliderDown():
            self.seek.setValue(position)
        self.position.setText(f"{self.time_text(position)} / {self.time_text(self.duration)}")
        self.fullscreen_window.update_position(position, self.duration)

    def playback_changed(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play.setText("❚❚ Pause" if playing else "▶ Play")
        self.fullscreen_window.play.setText("❚❚ Pause" if playing else "▶ Play")

    def fullscreen_changed(self, fullscreen: bool) -> None:
        self.fullscreen.setText("Exit Fullscreen" if fullscreen else "⛶ Fullscreen")

    def open_fullscreen(self) -> None:
        if self.fullscreen_window.isVisible():
            self.fullscreen_window.reject()
            return
        self.player.setVideoOutput(self.fullscreen_window.video)
        self.fullscreen_window.showFullScreen()
        self.fullscreen_changed(True)

    def restore_embedded_video(self, *_args) -> None:
        position = self.player.position()
        self.player.setVideoOutput(self.video)
        self.player.setPosition(position)
        self.fullscreen_changed(False)

class CatalogMediaViewer(QDialog):
    """Lightbox for originals with the same player as the project preview."""

    def __init__(self, owner, entries, index=0):
        super().__init__(owner)
        self.owner, self.entries, self.index = owner, entries, index
        self.setObjectName("catalogLightbox")
        self.setWindowTitle("Media Catalog Viewer")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.resize(1000, 720)
        layout = QVBoxLayout(self)
        tools = QHBoxLayout()
        previous = QPushButton("← Previous")
        previous.clicked.connect(lambda: self.navigate(-1))
        following = QPushButton("Next →")
        following.clicked.connect(lambda: self.navigate(1))
        self.caption = QLabel()
        self.caption.setTextFormat(Qt.TextFormat.PlainText)
        tools.addWidget(previous)
        tools.addWidget(self.caption, 1)
        tools.addWidget(following)
        close = QPushButton("✕ Close")
        close.clicked.connect(self.close)
        tools.addWidget(close)
        layout.addLayout(tools)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(1, 1)
        self.image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.image.installEventFilter(self)
        self.image.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.image.customContextMenuRequested.connect(self.image_menu)
        layout.addWidget(self.image, 1)
        self.video = ProjectPreviewPanel(self)
        self.video.choose.hide()
        self.video.setAcceptDrops(False)
        for output in (self.video.video, self.video.fullscreen_window.video):
            output.setAcceptDrops(False)
        self.video.export_requested.connect(self.export_original)
        layout.addWidget(self.video, 1)
        self.details = QLabel()
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        self.original = QPixmap()
        self.show_entry()

    def navigate(self, direction):
        self.index = (self.index + direction) % len(self.entries)
        self.show_entry()

    def keyPressEvent(self, event):
        if event.key() in {Qt.Key.Key_Left, Qt.Key.Key_Right}:
            self.navigate(-1 if event.key() == Qt.Key.Key_Left else 1)
            event.accept()
        else:
            super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        if watched is self.image and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self.update_image)
        return super().eventFilter(watched, event)

    def show_entry(self):
        self.video.fullscreen_window.reject()
        self.video.clear_project()
        entry = self.entries[self.index]
        self.caption.setText(f"{entry['name']} · {self.index + 1} / {len(self.entries)}")
        self.details.setText(" · ".join(entry.get("tags", [])) + "\n" + entry.get("description", ""))
        path = Path(entry['path'])
        is_video = path.suffix.lower() in ProjectVideoWidget.VIDEO_SUFFIXES
        self.image.setVisible(not is_video)
        self.video.setVisible(is_video)
        self.original = QPixmap()
        if is_video:
            self.video.set_project(entry['name'], str(path))
            self.video.choose.setEnabled(False)
            self.video.set_preview_visible(True)
        else:
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            self.original = QPixmap.fromImage(reader.read())
            self.update_image()

    def update_image(self):
        if self.original.isNull():
            self.image.setText("Image unavailable")
        else:
            self.image.setPixmap(self.original.scaled(self.image.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "original"):
            QTimer.singleShot(0, self.update_image)

    def showEvent(self, event):
        super().showEvent(event)
        self.video.set_preview_visible(self.video.isVisible())
        QTimer.singleShot(0, self.update_image)

    def image_menu(self, point):
        menu = QMenu(self)
        menu.addAction("Export Image", self.export_original)
        action = menu.addAction("Copy Image to Clipboard", lambda: QApplication.clipboard().setPixmap(self.original))
        action.setEnabled(not self.original.isNull())
        menu.exec(self.image.mapToGlobal(point))

    def export_original(self):
        source = Path(self.entries[self.index]['path'])
        if not source.is_file():
            QMessageBox.warning(self, "Export Media", "The original media file is unavailable.")
            return
        target = self.owner.next_export_path(source.name)
        owner = self.owner
        owner.start_disk_job(shutil.copy2, (source, target), lambda _result: owner.record_export(target),
                             lambda message: owner.export_failed(target, message), serialized=True)

    def done(self, result):
        self.video.fullscreen_window.reject()
        self.video.clear_project()
        super().done(result)

    def closeEvent(self, event):
        self.video.fullscreen_window.reject()
        self.video.clear_project()
        super().closeEvent(event)


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
        self.live_stream = LiveOutputPane(self.panel, collapsible=False)
        self.panel_layout.addWidget(self.live_stream, 1)
        layout.addWidget(self.panel, 1, Qt.AlignmentFlag.AlignCenter)
        scale = parent.settings.value("ui_text_scale", 100, int) / 100 if parent and hasattr(parent, "settings") else 1.0
        self.set_scale(scale)
        self.hide()

    def set_scale(self, scale: float) -> None:
        scale = max(.75, min(2.0, scale))
        self.panel.setMinimumSize(0, 0)
        self.panel.setMaximumSize(round(1100 * scale), round(720 * scale))
        self.panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.layout().setContentsMargins(round(12 * scale), round(12 * scale), round(12 * scale), round(12 * scale))
        self.panel_layout.setContentsMargins(round(18 * scale), round(12 * scale), round(18 * scale), round(14 * scale))
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

    def set_scale(self, value: float) -> None:
        self.scale = max(0.000001, float(value))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#17191a"))
        painter.setFont(self.font())
        scale = max(0.000001, float(self.scale))
        visible_start = max(0.0, (self.offset - 30) / scale)
        visible_end = max(0.0, (self.offset + self.width() + 30) / scale)
        label_width = painter.fontMetrics().horizontalAdvance(f"{math.ceil(visible_end)}.00") + 8
        minimum_interval = max(1, math.ceil(label_width / scale))
        magnitude = 10 ** math.floor(math.log10(minimum_interval))
        label_interval = next(step * magnitude for step in (1, 2, 5, 10) if step * magnitude >= minimum_interval)
        first_second = max(0, math.floor(visible_start / label_interval) * label_interval)
        for second in range(first_second, math.ceil(visible_end) + label_interval, label_interval):
            x = round(second * scale - self.offset)
            painter.setPen(QPen(QColor("#303538")))
            painter.drawLine(x, 12, x, 24)
            painter.setPen(QColor("#ff5757") if second == 0 else QColor("#788186"))
            painter.drawText(x + 4, 16, "0" if second == 0 else f"{second}.00")


class ResizeHandle(QFrame):
    preview = Signal(float)
    started = Signal()
    finished = Signal()

    def __init__(self, duration: float, pixels_per_second: int):
        super().__init__()
        self.duration = duration
        self.start_duration = duration
        self.start_x: float | None = None
        self.pixels_per_second = pixels_per_second
        self.start_scale = pixels_per_second
        self.setObjectName("resizeHandle")
        # Keep a comfortable hit target while drawing only a slim dotted grip.
        self.setFixedWidth(12)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setToolTip("Drag to resize segment")

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
            self.start_scale = max(0.000001, self.pixels_per_second)
            self.started.emit()
            self.grabMouse()
            self.update()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.start_x is not None:
            value = max(MIN_DURATION, self.start_duration + (event.globalPosition().x() - self.start_x) / self.start_scale)
            if value != self.duration:
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


class ConditioningPreview(QLabel):
    """Fit the original preview into the space left by a tile's header/footer."""

    def __init__(self, pixmap: QPixmap):
        super().__init__()
        self.source_pixmap = pixmap
        self.setObjectName("conditioningPreview")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(1, 1)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setPixmap(self.source_pixmap.scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))


class ConditioningHeightHandle(TimelineHeightHandle):
    """Resize the conditioning tiles, including a fully collapsed state."""

    def __init__(self, current_height):
        super().__init__(current_height)
        self.setObjectName("conditioningHeightHandle")
        self.setAccessibleName("Resize or hide conditioning tiles")
        self.setToolTip("Drag down to enlarge conditioning tiles; drag up to hide. Double-click to hide or reopen.")
        self.last_height = current_height or 144

    def mouseMoveEvent(self, event):
        if self.start_y is not None:
            raw = int(self.start_height + event.globalPosition().y() - self.start_y)
            value = 0 if raw < 48 else max(96, min(260, raw))
            self.current_height = value
            if value:
                self.last_height = value
            self.height_changed.emit(value)
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.current_height = 0 if self.current_height else self.last_height
            self.height_changed.emit(self.current_height)
            self.finished.emit()
            event.accept()


class SegmentCard(QFrame):
    duration_changed = Signal(float)
    delete_requested = Signal()
    resize_finished = Signal()
    resize_started = Signal()
    review_decision = Signal(str, bool)

    def __init__(self, segment: Segment, start_time: float, preview_height: int, pixels_per_second: int):
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
        kind = QLabel(segment_kind_label(segment))
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
        self.review_accept = QToolButton()
        self.review_reject = QToolButton()
        for button, glyph, description, color in (
            (self.review_accept, "✓", "Approve this segment change", "#217744"),
            (self.review_reject, "×", "Deny this segment change", "#973943"),
        ):
            button.setText(glyph)
            button.setAccessibleName(description)
            button.setToolTip(description)
            button.setFixedSize(19, 19)
            button.setCursor(Qt.CursorShape.ArrowCursor)
            button.setStyleSheet(
                "QToolButton{background:" + color +
                ";color:#fff;border:0;border-radius:4px;"
                "font-size:13px;font-weight:bold;padding:0;min-height:0}"
                "QToolButton:hover{background:#46809e}"
            )
            button.hide()
            badges.addWidget(button)
        self.review_accept.clicked.connect(
            lambda: self.review_decision.emit(self.segment.id, True))
        self.review_reject.clicked.connect(
            lambda: self.review_decision.emit(self.segment.id, False))
        badges.addWidget(close)
        layout.addLayout(badges)
        self.preview = QLabel()
        self.preview.setObjectName("segmentPreview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.source_pixmap = timeline_preview_pixmap(segment.preview_path)
        self.resolution_badge = QLabel(self.preview)
        self.resolution_badge.setObjectName("resolutionPill")
        self.resolution_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.resolution_badge.setStyleSheet(
            "background:rgba(20,25,28,220);color:#e3f4ff;border:1px solid #668594;"
            "border-radius:8px;padding:2px 6px;font-size:9px;font-weight:bold"
        )
        resolution_source = segment.media_path if segment.kind == "image" else segment.preview_path
        resolution = QImageReader(resolution_source).size() if segment.kind in {"image", "video"} and resolution_source and Path(resolution_source).is_file() else QSize()
        if segment.kind == "video":
            original_size = video_source_dimensions(segment.media_path) or getattr(segment, "_source_size", None)
            if original_size:
                resolution = QSize(*original_size)
        elif segment.kind == "image" and not resolution.isValid() and getattr(segment, "_source_size", None):
            resolution = QSize(*segment._source_size)
        if resolution.isValid():
            self.resolution_badge.setText(f"{resolution.width()} × {resolution.height()}")
            self.resolution_badge.adjustSize()
            self.resolution_badge.show()
        else:
            self.resolution_badge.hide()
        if segment.kind == "text":
            self.preview.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            self.preview.setMargin(8)
            self.preview.setWordWrap(True)
            self.set_text_preview(segment.prompt)
        self.artwork_frame = 0
        self.artwork_timer = QTimer(self)
        self.artwork_timer.setInterval(95)
        self.artwork_timer.timeout.connect(self._advance_artwork_shimmer)
        self.artwork_overlay = QLabel(self.preview)
        self.artwork_overlay.setObjectName("artworkOverlay")
        self.artwork_overlay.move(7, 26)
        self.artwork_overlay.hide()
        # A proposed timeline rewrite uses the same 80-ms, batched red/green
        # typing/diff treatment as the unified prompt editor. It is ONLY a
        # preview; project segment text is never mutated by the animation.
        self.rewrite_overlay = QLabel(self.preview)
        self.rewrite_overlay.setObjectName("timelineRewriteOverlay")
        self.rewrite_overlay.setWordWrap(True)
        self.rewrite_overlay.setTextFormat(Qt.TextFormat.RichText)
        self.rewrite_overlay.setStyleSheet(
            "QLabel#timelineRewriteOverlay{background:rgba(10,25,37,235);"
            "border:0;border-radius:4px;color:#e8f5ff;padding:3px 5px}"
        )
        self.rewrite_overlay.hide()
        self.rewrite_timer = QTimer(self)
        self.rewrite_timer.setInterval(80)
        self.rewrite_timer.timeout.connect(self._advance_rewrite_preview)
        self.rewrite_marquee = AIWorkingMarquee(self.preview)
        self.rewrite_marquee.setFixedHeight(18)
        self.rewrite_marquee.hide()
        self.review_pending = False
        self.rewrite_base = ""
        self.rewrite_target = ""
        self.rewrite_shown = ""
        self.rewrite_active = False
        if segment.artwork_status == "generating":
            self.artwork_timer.start()
            self._advance_artwork_shimmer()
        elif segment.artwork_status in {"awaiting", "failed"}:
            self._show_artwork_placeholder()
        layout.addWidget(self.preview, 1)
        title = QLabel(segment.name if segment.kind == "text" else (segment.prompt or segment.name))
        title.setToolTip(segment.name)
        title.setWordWrap(False)
        title.setObjectName("tileTitle")
        layout.addWidget(title)
        timing = QHBoxLayout()
        timing.setContentsMargins(0, 0, 0, 0)
        self.start_time_label = QLabel()
        self.start_time_label.setObjectName("tileStartTime")
        self.set_start_time(start_time)
        timing.addWidget(self.start_time_label)
        timing.addStretch()
        self.duration_label = QLabel(f"{segment.duration:.2f}s")
        self.duration_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.duration_label.setObjectName("tileDuration")
        timing.addWidget(self.duration_label)
        layout.addLayout(timing)
        self.outer_layout.addWidget(self.content, 1)
        self.resize_handle = ResizeHandle(segment.duration, pixels_per_second)
        self.resize_handle.preview.connect(self._preview_duration)
        self.resize_handle.started.connect(self.resize_started)
        self.resize_handle.finished.connect(self.resize_finished)
        self.outer_layout.addWidget(self.resize_handle)
        self.update_layout(preview_height, pixels_per_second)

    def _show_artwork_placeholder(self):
        if not self.segment.artwork_prompt:
            return
        state = self.segment.artwork_status
        label = ("✦  Generating artwork" if state == "generating" else
                 "◌  Artwork failed · right-click to retry" if state == "failed" else
                 "✦  Artwork requested · ready to render")
        if self.segment.kind == "image":
            # Keep the existing reference visible while an edit is underway.
            self.artwork_overlay.setText(label + ("." * (self.artwork_frame % 4)
                                                  if state == "generating" else ""))
            self.artwork_overlay.setStyleSheet(
                "QLabel#artworkOverlay{background:rgba(12,30,46,224);"
                "border:1px solid #5382aa;border-radius:5px;color:#dff1ff;"
                "font-size:10px;padding:4px 6px}"
            )
            self.artwork_overlay.adjustSize()
            self.artwork_overlay.show()
            self.artwork_overlay.raise_()
            return
        if state == "generating":
            dots = "." * (self.artwork_frame % 4)
            self.preview.setText(label + dots + "\n\n" + self.segment.prompt[:130])
        else:
            self.preview.setText(label + "\n\n" + self.segment.prompt[:130])
        glow = (self.artwork_frame % 12) / 12
        light = round(32 + glow * 35)
        self.preview.setStyleSheet(
            "QLabel#segmentPreview{border:1px solid #517ac0;border-radius:7px;"
            f"background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #101b31,"
            f"stop:0.40 rgb({light},{light+13},{light+49}),stop:0.65 #294c83,"
            "stop:1 #111f37);color:#e4f0ff;padding:7px}"
        )

    def _advance_artwork_shimmer(self):
        if self.segment.artwork_status != "generating":
            self.artwork_timer.stop()
            return
        self.artwork_frame += 1
        self._show_artwork_placeholder()

    def begin_rewrite_preview(self, before: str, after: str) -> None:
        """Stream a read-only proposed rewrite over the unchanged timeline tile."""
        if not after or before == after:
            self.stop_rewrite_preview()
            return
        if self.rewrite_active and (before, after) == (self.rewrite_base, self.rewrite_target):
            return  # Do not restart on every completed JSON record.
        self.stop_rewrite_preview()
        self.rewrite_base = before
        self.rewrite_target = after
        self.rewrite_shown = ""
        self.rewrite_active = True
        self.rewrite_marquee.begin("✦ Reviewing timeline rewrite…")
        self._position_rewrite_marquee()
        self._advance_rewrite_preview()

    def _advance_rewrite_preview(self) -> None:
        if not self.rewrite_active:
            return
        available = self.rewrite_target
        remaining = len(available) - len(self.rewrite_shown)
        if remaining <= 0:
            self.rewrite_timer.stop()
            self.rewrite_marquee.finish()
            return
        self.rewrite_shown = available[:len(self.rewrite_shown) +
                                       typing_batch_size(remaining)]
        # For media, leave the source image uncovered except a small caption.
        before, current = self.rewrite_base, self.rewrite_shown
        if self.segment.kind == "text":
            self.preview.setTextFormat(Qt.TextFormat.RichText)
            self.preview.setText(rich_diff_html(before, current))
        else:
            self.rewrite_overlay.setText(rich_diff_html(before[-260:], current[-260:]))
            self._position_rewrite_overlay()
            self.rewrite_overlay.show()
            self.rewrite_overlay.raise_()
        if self.rewrite_shown != available:
            self.rewrite_timer.start()
        else:
            self.rewrite_timer.stop()
            self.rewrite_marquee.finish()

    def _position_rewrite_marquee(self) -> None:
        self.rewrite_marquee.setFixedWidth(max(45, self.preview.width() - 12))
        self.rewrite_marquee.move(6, 2)
        if self.rewrite_marquee.isVisible():
            self.rewrite_marquee.raise_()

    def set_review_pending(self, before: Segment | None) -> None:
        self.review_pending = True
        self.review_accept.show()
        self.review_reject.show()
        if before is None:
            old_text = ""
            new_text = self.segment.prompt or self.segment.image_prompt
        else:
            old_text = before.prompt
            new_text = self.segment.prompt
            if old_text == new_text and before.image_prompt != self.segment.image_prompt:
                old_text, new_text = before.image_prompt, self.segment.image_prompt
        if old_text != new_text and new_text:
            self.begin_rewrite_preview(old_text, new_text)
        else:
            self.setStyleSheet(
                "QFrame#segmentCard{border:1px solid #5b9b75;border-radius:6px}"
            )

    def clear_review_pending(self) -> None:
        self.review_pending = False
        self.review_accept.hide()
        self.review_reject.hide()
        self.stop_rewrite_preview()
        self.setStyleSheet("")

    def _position_rewrite_overlay(self) -> None:
        if not self.rewrite_active or self.segment.kind == "text":
            return
        width = max(40, self.preview.width() - 12)
        self.rewrite_overlay.setFixedWidth(width)
        self.rewrite_overlay.setMaximumHeight(58)
        self.rewrite_overlay.adjustSize()
        self.rewrite_overlay.move(6, max(3, self.preview.height() -
                                          self.rewrite_overlay.height() - 4))

    def stop_rewrite_preview(self) -> None:
        if not self.rewrite_active:
            return
        self.rewrite_timer.stop()
        self.rewrite_marquee.finish()
        self.rewrite_active = False
        self.rewrite_target = self.rewrite_base = self.rewrite_shown = ""
        self.rewrite_overlay.hide()
        if self.segment.kind == "text":
            self.preview.setTextFormat(Qt.TextFormat.PlainText)
            self.preview.setText(self.segment.prompt or "Enter text prompt…")

    def set_timeline_edges(self, first: bool, last: bool) -> None:
        self.outer_layout.setContentsMargins(0 if first else 1, 0, 0 if last else 1, 0)
        self.resize_handle.setAccessibleName("Resize final segment" if last else "Move segment boundary")
        self.resize_handle.setToolTip(
            "Drag to resize the final segment and change total length." if last else
            "Drag the divider to grow one segment and shrink the next; their total length stays fixed.")

    def set_role(self, role: str) -> None:
        """Update the visible role without recreating the timeline card."""
        if self.segment.kind == "text":
            return
        self.segment.role = role
        self.role_badge.setText(role.upper())

    def set_start_time(self, value: float) -> None:
        self.start_time_label.setText(f"START {max(0.0, value):.2f}s")

    def set_text_preview(self, text: str) -> None:
        if self.segment.kind == "text":
            self.preview.setText(text or "Enter text prompt…")

    def update_layout(self, preview_height: int, pixels_per_second: int) -> None:
        height = max(24, preview_height)
        width = max(36, int(self.segment.duration * pixels_per_second) - 14)
        self.preview.setMinimumHeight(height)
        if not self.source_pixmap.isNull():
            scaled = self.source_pixmap.scaled(
                width, height, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.preview.setPixmap(scaled)
        self.resize_handle.pixels_per_second = pixels_per_second
        self.resize_handle.duration = self.segment.duration
        self.duration_label.setText(f"{self.segment.duration:.2f}s")
        self.position_resolution_badge()

    def position_resolution_badge(self) -> None:
        if self.resolution_badge.isHidden():
            return
        self.resolution_badge.adjustSize()
        self.resolution_badge.move(
            max(4, self.preview.width() - self.resolution_badge.width() - 6),
            max(4, self.preview.height() - self.resolution_badge.height() - 6),
        )
        self.resolution_badge.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.position_resolution_badge()
        self._position_rewrite_overlay()
        self._position_rewrite_marquee()

    def _preview_duration(self, value: float) -> None:
        self.duration_changed.emit(value)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.ignore()

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        event.ignore()


class TagEditor(QWidget):
    def __init__(self, tags: list[dict[str, str]], parent=None, allow_empty: bool = False):
        super().__init__(parent)
        self.allow_empty = allow_empty
        self.rows: list[tuple[QWidget, QLineEdit, QPushButton]] = []
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(4)
        for tag in tags:
            self.add_tag(tag["name"], tag["color"])
        self.add_button = QPushButton("+ Add tag")
        self.add_button.clicked.connect(lambda: self.add_tag("New tag", "#56616a"))
        self.layout.addWidget(self.add_button)

    @staticmethod
    def style_color_button(button: QPushButton, color: str) -> None:
        button.setProperty("tagColor", color)
        button.setStyleSheet(f"background: {color}; border: 1px solid {QColor(color).lighter(150).name()}; border-radius: 4px;")

    def add_tag(self, name: str, color: str) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        name_input = QLineEdit(name)
        color_button = QPushButton()
        color_button.setFixedWidth(42)
        color_button.setToolTip("Choose tag color")
        self.style_color_button(color_button, color)
        color_button.clicked.connect(lambda: self.choose_color(color_button))
        remove = QPushButton("×")
        remove.setFixedWidth(28)
        remove.setToolTip("Remove tag")
        remove.clicked.connect(lambda: self.remove_tag(row))
        layout.addWidget(name_input, 1)
        layout.addWidget(color_button)
        layout.addWidget(remove)
        self.rows.append((row, name_input, color_button))
        index = self.layout.count() - 1 if hasattr(self, "add_button") else self.layout.count()
        self.layout.insertWidget(max(0, index), row)

    def choose_color(self, button: QPushButton) -> None:
        selected = QColorDialog.getColor(QColor(str(button.property("tagColor"))), self, "Choose tag color")
        if selected.isValid():
            self.style_color_button(button, selected.name())

    def remove_tag(self, row: QWidget) -> None:
        self.rows = [value for value in self.rows if value[0] is not row]
        row.deleteLater()

    def tags(self) -> list[dict[str, str]]:
        result = []
        names = set()
        for _row, name_input, color_button in self.rows:
            name = name_input.text().strip()
            if not name:
                raise ValueError("Every project tag needs a name.")
            if name.casefold() in names:
                raise ValueError(f"Duplicate project tag: {name}")
            names.add(name.casefold())
            result.append({"name": name, "color": str(color_button.property("tagColor"))})
        if not result and not self.allow_empty:
            raise ValueError("Add at least one project tag.")
        return result


class SettingsDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Application Settings")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        tabs = QTabWidget(); self.tabs = tabs
        general = QWidget()
        form = QFormLayout(general)
        general_scroll = QScrollArea(); general_scroll.setWidgetResizable(True); general_scroll.setWidget(general)
        tabs.addTab(general_scroll, "Application")
        self.local_models = LocalModelsPanel(parent)
        local_scroll = QScrollArea(); local_scroll.setWidgetResizable(True); local_scroll.setWidget(self.local_models)
        tabs.addTab(local_scroll, "Local models")
        def scroll_page(widget, title):
            scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(widget); tabs.addTab(scroll, title)
        scroll_page(ChatGPTSettingsPanel(parent.chatgpt_auth, settings, tabs), "ChatGPT plan")
        scroll_page(WorkspaceEditor(parent.workspace_store, parent, tabs), "Workspaces")
        from .storage_widgets import StorageSettingsPanel
        self.storage_panel=StorageSettingsPanel(parent,tabs)
        scroll_page(self.storage_panel,"Storage and backups")
        self.resize(1280, 956); self.setMinimumSize(1040, 680)
        self.provider = QComboBox()
        self.provider.addItems(["gemini", "openai", "chatgpt", "local"])
        self.provider.setCurrentText(settings.value("provider", "gemini"))
        self.model = QComboBox()
        self.model.addItems(GEMINI_MODELS)
        self.model.setCurrentText(settings.value("gemini_model", GEMINI_MODELS[0]))
        # Session-only keys remain available when reopening Settings even if
        # the user declined persistent on-disk credential storage.
        self.gemini = QLineEdit(str(parent.session_keys.get("gemini") or
                                     settings.value("gemini_key", "") or ""))
        self.openai = QLineEdit(str(parent.session_keys.get("openai") or
                                     settings.value("openai_key", "") or ""))
        self.gemini.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai_model = QComboBox()
        self.openai_model.setEditable(True)
        self.openai_model.addItems(["gpt-6", "gpt-6-mini", "gpt-5.4-mini"])
        self.openai_model.setCurrentText(str(settings.value("openai_model", "gpt-5.4-mini")))
        self.openai_model.setToolTip("OpenAI API-key model; enter any model available to your API account")
        self.image_gen_model = QComboBox()
        for label, model_id in IMAGE_MODEL_CHOICES:
            self.image_gen_model.addItem(label, model_id)
        saved_image_model = str(settings.value("artwork/default_model", "auto") or "auto")
        selected_index = self.image_gen_model.findData(saved_image_model)
        self.image_gen_model.setCurrentIndex(max(0, selected_index))
        self.image_gen_model.setToolTip(
            "Default model for timeline artwork requested by segment JSON. "
            "Automatic retains the previous provider preference. Specific "
            "cloud models require that provider's API key. Local Flux uses the files "
            "configured in Local models. ChatGPT plan OAuth "
            "cannot generate image files. Image API usage is billed separately."
        )
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
        self.comfy_root_dir = QLineEdit(str(settings.value("comfy_root_dir", "")))
        self.comfy_root_dir.setPlaceholderText("Choose the ComfyUI root directory")
        self.comfy_root_browse = QPushButton("Browse…")
        self.comfy_root_browse.clicked.connect(self.choose_comfy_root_directory)
        comfy_directory_row = QWidget()
        comfy_directory_layout = QHBoxLayout(comfy_directory_row)
        comfy_directory_layout.setContentsMargins(0, 0, 0, 0)
        comfy_directory_layout.addWidget(self.comfy_root_dir, 1)
        comfy_directory_layout.addWidget(self.comfy_root_browse)
        status_value = settings.value("project_status_tags", settings.value("project_tags", ""))
        self.project_status_tags = TagEditor(load_project_tags(status_value))
        self.project_status_tags.setToolTip("A project can have one status. Its status controls the project card color.")
        self.project_other_tags = TagEditor(load_other_tags(settings.value("project_other_tags", "")), allow_empty=True)
        self.project_other_tags.setToolTip("A project can have any number of these labels. They appear as thumbnail pills.")
        form.addRow("Provider", self.provider)
        form.addRow("Gemini model", self.model)
        form.addRow("Gemini API key", self.gemini)
        form.addRow("OpenAI API key", self.openai)
        form.addRow("OpenAI model", self.openai_model)
        form.addRow("Default image generation model", self.image_gen_model)
        form.addRow("API timeout", self.timeout)
        form.addRow("Connection retries", self.retries)
        form.addRow("Retry cooldown", self.retry_cooldown)
        form.addRow("UI text scale (DPI)", text_scale_row)
        form.addRow("Default download folder", save_directory_row)
        form.addRow("ComfyUI working directory", comfy_directory_row)
        form.addRow("Status tags (choose one)", self.project_status_tags)
        form.addRow("Other tags (choose many)", self.project_other_tags)
        form.addRow("", self.remember)
        from .settings_design import SettingsSurface
        scale = max(.75, min(2, text_scale / 100))
        self.surface = SettingsSurface(self, tabs, self.local_models, scale)
        outer.addWidget(self.surface)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons = buttons
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.surface.attach_buttons(buttons)
        geometry = settings.value("dialogs/settings_geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def accept(self) -> None:
        try:
            project_status_tags = self.project_status_tags.tags()
            project_other_tags = self.project_other_tags.tags()
            names = [tag["name"].casefold() for tag in project_status_tags + project_other_tags]
            if len(names) != len(set(names)) or "archive" in names:
                raise ValueError("Status and other tag names must be unique, and Archive is reserved.")
        except ValueError as error:
            QMessageBox.warning(self, "Invalid project tags", str(error))
            return
        save_profile(self.settings, self.local_models.value())
        self.settings.setValue("provider", self.provider.currentText())
        self.settings.setValue("gemini_model", self.model.currentText())
        self.settings.setValue("openai_model", self.openai_model.currentText())
        self.settings.setValue("artwork/default_model", self.image_gen_model.currentData())
        self.settings.setValue("remember_keys", self.remember.isChecked())
        self.settings.setValue("api_timeout", self.timeout.value())
        self.settings.setValue("api_retries", self.retries.value())
        self.settings.setValue("api_retry_cooldown", self.retry_cooldown.value())
        self.settings.setValue("ui_text_scale", self.ui_text_scale.value())
        self.settings.setValue("segment_export_dir", self.segment_save_dir.text().strip())
        self.settings.setValue("comfy_root_dir", self.comfy_root_dir.text().strip())
        self.settings.setValue("project_status_tags", json.dumps(project_status_tags))
        self.settings.setValue("project_other_tags", json.dumps(project_other_tags))
        self.settings.setValue("project_tags", json.dumps(project_status_tags))
        self.settings.setValue("dialogs/settings_geometry", self.saveGeometry())
        if self.remember.isChecked():
            self.settings.setValue("gemini_key", self.gemini.text().strip())
            self.settings.setValue("openai_key", self.openai.text().strip())
        else:
            self.settings.remove("gemini_key")
            self.settings.remove("openai_key")
        self.parent().session_keys = {"gemini": self.gemini.text().strip(), "openai": self.openai.text().strip()}
        super().accept()

    def done(self, result):
        self.surface.dispose()
        self.local_models.dispose()
        super().done(result)

    def choose_segment_save_directory(self) -> None:
        selected = choose_directory(self, "Choose default download folder", self.segment_save_dir.text().strip() or str(Path.home()))
        if selected:
            self.segment_save_dir.setText(selected)

    def choose_comfy_root_directory(self) -> None:
        selected = choose_directory(self, "Choose ComfyUI root directory", self.comfy_root_dir.text().strip() or str(Path.home()))
        if selected:
            self.comfy_root_dir.setText(selected)

    def reject(self) -> None:
        self.settings.setValue("dialogs/settings_geometry", self.saveGeometry())
        super().reject()


class ProjectNoteRow(QFrame):
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    checked_changed = Signal(str, bool)

    def __init__(self, note: dict, parent=None):
        super().__init__(parent)
        self.note_id = str(note["id"])
        self.setObjectName("projectNoteRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 5, 5, 5)
        layout.setSpacing(7)
        completed = QCheckBox()
        completed.setChecked(bool(note.get("checked", False)))
        completed.setToolTip("Mark note complete")
        completed.toggled.connect(lambda checked: self.checked_changed.emit(self.note_id, checked))
        note_time = QDateTime.fromString(str(note["date"]), Qt.DateFormat.ISODate)
        stamp = QLabel(note_time.toLocalTime().toString("yyyy-MM-dd HH:mm") if note_time.isValid() else str(note["date"]).replace("T", " ")[:16])
        stamp.setObjectName("projectNoteDate")
        stamp.setFixedWidth(118)
        stamp.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        text = QLabel(str(note["text"]))
        text.setWordWrap(True)
        if note.get("checked"):
            font = text.font()
            font.setStrikeOut(True)
            text.setFont(font)
            text.setStyleSheet("color:#839097")
        text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        remove = QPushButton("×")
        remove.setObjectName("projectNoteDelete")
        remove.setToolTip("Delete note")
        remove.setFixedSize(24, 24)
        remove.clicked.connect(lambda: self.delete_requested.emit(self.note_id))
        layout.addWidget(completed)
        layout.addWidget(stamp)
        layout.addWidget(text, 1)
        layout.addWidget(remove)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.edit_requested.emit(self.note_id)
        super().mousePressEvent(event)


class PreviewCacheDialog(QDialog):
    """On first run or after cache expiry, prepare project covers without freezing UI."""

    def __init__(self, metadata_files: list[Path], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preparing project previews")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumWidth(370)
        self.files = metadata_files
        self.position = 0
        layout = QVBoxLayout(self)
        self.description = QLabel("Updating the preview cache for saved projects…")
        self.description.setWordWrap(True)
        layout.addWidget(self.description)
        self.progress = QProgressBar()
        self.progress.setRange(0, max(1, len(metadata_files)))
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance)

    def start(self) -> None:
        self.open()
        self.timer.start(0)

    def advance(self) -> None:
        if self.position < len(self.files):
            path = self.files[self.position]
            try:
                warm_metadata(path)
            except (OSError, ValueError, TypeError):
                pass  # A broken thumbnail should not block access to the project.
            self.position += 1
            self.progress.setValue(self.position)
            self.description.setText(f"Preparing project previews: {self.position} of {len(self.files)}")
        else:
            self.timer.stop()
            mark_ready()
            self.accept()

    def reject(self) -> None:
        self.timer.stop()
        super().reject()


class ProjectDetailsDialog(QDialog):
    def __init__(self, suggested_description: str, parent=None, name: str = "", collection: str = "", collections: list[str] | None = None,
                 thumbnail_options: list[tuple[str, str]] | None = None, thumbnail_data: str = "", thumbnail_source: str = "",
                 notes: list[dict] | None = None, download_directory: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Save project to library")
        self.download_folder = QLineEdit(download_directory)
        self.download_folder.setPlaceholderText("Use the application download folder")
        self.setMinimumWidth(430)
        form = QFormLayout(self)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Project name")
        self.name.setText(name)
        self.description = QTextEdit()
        install_spellcheck(self.description)
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
        self.notes = normalize_notes(notes)
        self.editing_note_id = ""
        notes_box = QWidget()
        notes_layout = QVBoxLayout(notes_box)
        notes_layout.setContentsMargins(0, 0, 0, 0)
        notes_layout.setSpacing(5)
        note_entry = QHBoxLayout()
        self.note_date = QDateTimeEdit(QDateTime.currentDateTime())
        self.note_date.setCalendarPopup(True)
        self.note_date.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.note_date.setToolTip("Note date and time; editing does not change it unless you do")
        self.note_input = QLineEdit()
        self.note_input.setPlaceholderText("Type a note and press Enter…")
        self.note_input.returnPressed.connect(self.commit_note)
        add_note = QPushButton("Add")
        add_note.clicked.connect(self.commit_note)
        note_entry.addWidget(self.note_date)
        note_entry.addWidget(self.note_input, 1)
        note_entry.addWidget(add_note)
        self.notes_list = QListWidget()
        self.notes_list.setObjectName("projectNotesList")
        self.notes_list.setMinimumHeight(170)
        notes_layout.addLayout(note_entry)
        notes_layout.addWidget(self.notes_list)
        self.refresh_notes()
        form.addRow("Name", self.name)
        form.addRow("Description", self.description)
        form.addRow("Collection", self.collection)
        form.addRow("Download folder", self.download_folder)
        form.addRow("Thumbnail", thumbnail_box)
        form.addRow("Notes", notes_box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        geometry = parent.settings.value("dialogs/project_details_geometry") if parent else None
        if geometry:
            self.restoreGeometry(geometry)

    def accept(self) -> None:
        if self.note_input.text().strip():
            self.commit_note()
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

    def refresh_notes(self) -> None:
        self.notes = normalize_notes(self.notes)
        self.notes_list.clear()
        for note in self.notes:
            item = QListWidgetItem()
            row = ProjectNoteRow(note, self.notes_list)
            row.edit_requested.connect(self.edit_note)
            row.delete_requested.connect(self.delete_note)
            row.checked_changed.connect(self.set_note_checked)
            item.setSizeHint(row.sizeHint())
            item.setData(Qt.ItemDataRole.UserRole, note["id"])
            self.notes_list.addItem(item)
            self.notes_list.setItemWidget(item, row)
        self.notes_list.scrollToBottom()

    def commit_note(self) -> None:
        text = self.note_input.text().strip()
        if not text:
            return
        date = self.note_date.dateTime().toUTC().toString(Qt.DateFormat.ISODate)
        existing = next((note for note in self.notes if note["id"] == self.editing_note_id), None)
        if existing:
            existing["text"] = text
            existing["date"] = date
        else:
            self.notes.append(new_note(text, date))
        self.editing_note_id = ""
        self.note_input.clear()
        self.note_date.setDateTime(QDateTime.currentDateTime())
        self.refresh_notes()

    def edit_note(self, note_id: str) -> None:
        note = next((value for value in self.notes if value["id"] == note_id), None)
        if not note:
            return
        self.editing_note_id = note_id
        self.note_input.setText(note["text"])
        value = QDateTime.fromString(note["date"], Qt.DateFormat.ISODate)
        if value.isValid():
            self.note_date.setDateTime(value.toLocalTime())
        self.note_input.setFocus()
        self.note_input.selectAll()

    def delete_note(self, note_id: str) -> None:
        self.notes = [note for note in self.notes if note["id"] != note_id]
        if self.editing_note_id == note_id:
            self.editing_note_id = ""
            self.note_input.clear()
        self.refresh_notes()

    def set_note_checked(self, note_id: str, checked: bool) -> None:
        note = next((value for value in self.notes if value["id"] == note_id), None)
        if note:
            note["checked"] = checked
            self.refresh_notes()

    def notes_value(self) -> list[dict]:
        return normalize_notes(self.notes)


class ProjectPropertiesPanel(QWidget):
    """Editable project details, statuses, tags, and checklist notes in a dock."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.project_id = ""
        self.notes: list[dict] = []
        self._loading = False
        self.editing_note_id = ""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        self.message = QLabel("Select a project in the library to edit its properties.")
        self.message.setWordWrap(True)
        outer.addWidget(self.message)
        form = QFormLayout()
        self.name = QLineEdit()
        self.description = QTextEdit()
        install_spellcheck(self.description)
        self.description.setMaximumHeight(80)
        self.collection = QLineEdit()
        self.status = QComboBox()
        self.status.addItem("No status", "")
        self.status.currentIndexChanged.connect(self.commit_fields)
        self.archived = QCheckBox("Archived")
        self.archived.toggled.connect(self.commit_fields)
        form.addRow("Name", self.name)
        form.addRow("Description", self.description)
        form.addRow("Collection", self.collection)
        self.download_folder = QLineEdit()
        folder_row = QWidget()
        folder_layout = QHBoxLayout(folder_row)
        folder_layout.setContentsMargins(0, 0, 0, 0)
        folder_layout.addWidget(self.download_folder, 1)
        browse = QPushButton("…")
        browse.clicked.connect(self.choose_download_folder)
        folder_layout.addWidget(browse)
        self.download_folder.textChanged.connect(self.queue_fields)
        form.addRow("Download folder", folder_row)
        form.addRow("Status", self.status)
        form.addRow("", self.archived)
        outer.addLayout(form)
        outer.addWidget(QLabel("TAGS"))
        self.tags_container = QWidget()
        self.tags_layout = QVBoxLayout(self.tags_container)
        self.tags_layout.setContentsMargins(0, 0, 0, 0)
        self.tags_layout.setSpacing(2)
        outer.addWidget(self.tags_container)
        self.tag_boxes: list[QCheckBox] = []
        tag_entry = QHBoxLayout()
        self.new_tag = QLineEdit()
        self.new_tag.setPlaceholderText("Add a tag to this project…")
        self.new_tag.returnPressed.connect(self.create_tag)
        tag_add = QPushButton("Add tag")
        tag_add.clicked.connect(self.create_tag)
        tag_entry.addWidget(self.new_tag, 1)
        tag_entry.addWidget(tag_add)
        outer.addLayout(tag_entry)
        outer.addWidget(QLabel("TASKS & NOTES"))
        note_input = QHBoxLayout()
        self.note_date = QDateTimeEdit(QDateTime.currentDateTime())
        self.note_date.setCalendarPopup(True)
        self.note_date.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.note_text = QLineEdit()
        self.note_text.setPlaceholderText("New task or note…")
        self.note_text.returnPressed.connect(self.commit_note)
        add = QPushButton("Add")
        add.clicked.connect(self.commit_note)
        note_input.addWidget(self.note_date)
        note_input.addWidget(self.note_text, 1)
        note_input.addWidget(add)
        outer.addLayout(note_input)
        self.notes_list = QListWidget()
        self.notes_list.setMinimumHeight(130)
        outer.addWidget(self.notes_list, 1)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(500)
        self.save_timer.timeout.connect(self.commit_fields)
        self.name.textChanged.connect(self.queue_fields)
        self.description.textChanged.connect(self.queue_fields)
        self.collection.textChanged.connect(self.queue_fields)
        self.set_project(None)

    def choose_download_folder(self) -> None:
        directory = choose_directory(self, "Project download folder", self.download_folder.text() or str(self.owner.export_directory()))
        if directory:
            self.download_folder.setText(directory)
            self.commit_fields()

    def queue_fields(self, *_args) -> None:
        if not self._loading and self.project_id:
            self.save_timer.start()

    def set_project(self, meta: dict | None) -> None:
        project_id = str(meta.get("id", "")) if meta and meta.get("kind") != "collection" else ""
        if project_id != self.project_id and self.save_timer.isActive():
            self.save_timer.stop()
            self.commit_fields()
        self._loading = True
        self.project_id = project_id
        self.setEnabled(bool(project_id))
        self.message.setText("Project properties save as you edit." if project_id else "Select a project in the library to edit its properties.")
        self.name.setText(str(meta.get("name", "")) if meta else "")
        self.description.setPlainText(str(meta.get("description", "")) if meta else "")
        self.collection.setText(str(meta.get("collection", "")) if meta else "")
        directory = (self.owner.download_directory if project_id == self.owner.current_project_id else str(meta.get("downloadDirectory", ""))) if meta else ""
        self.download_folder.setText(directory)
        self.download_folder.setPlaceholderText(str(self.owner.export_directory()))
        self.status.blockSignals(True)
        self.status.clear()
        self.status.addItem("No status", "")
        for tag in self.owner.project_status_tags():
            self.status.addItem(tag["name"], tag["name"])
        current = str(meta.get("status", "")) if meta else ""
        if current and self.status.findData(current) < 0:
            self.status.addItem(current, current)
        self.status.setCurrentIndex(max(0, self.status.findData(current)))
        self.status.blockSignals(False)
        self.archived.blockSignals(True)
        self.archived.setChecked(bool(meta.get("archived")) if meta else False)
        self.archived.blockSignals(False)
        for box in self.tag_boxes:
            self.tags_layout.removeWidget(box)
            box.deleteLater()
        self.tag_boxes = []
        selected = {str(value).casefold() for value in meta.get("tags", [])} if meta else set()
        definitions = self.owner.project_other_tags()
        for value in selected:
            if value not in {tag["name"].casefold() for tag in definitions}:
                definitions.append({"name": next((tag for tag in meta.get("tags", []) if str(tag).casefold() == value), value)})
        for tag in definitions:
            box = QCheckBox(tag["name"])
            box.setChecked(tag["name"].casefold() in selected)
            box.toggled.connect(self.commit_fields)
            self.tags_layout.addWidget(box)
            self.tag_boxes.append(box)
        self.notes = normalize_notes(meta.get("notes")) if meta else []
        self.editing_note_id = ""
        self.note_text.clear()
        self.refresh_notes()
        self._loading = False

    def refresh_notes(self) -> None:
        self.notes_list.clear()
        for note in self.notes:
            item = QListWidgetItem()
            row = ProjectNoteRow(note, self.notes_list)
            row.edit_requested.connect(self.edit_note)
            row.delete_requested.connect(self.delete_note)
            row.checked_changed.connect(self.set_note_checked)
            item.setSizeHint(row.sizeHint())
            self.notes_list.addItem(item)
            self.notes_list.setItemWidget(item, row)

    def current_metadata(self) -> dict | None:
        if not self.project_id:
            return None
        try:
            return json.loads((project_library_path() / f"{self.project_id}.meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def commit_fields(self, *_args) -> None:
        if self._loading:
            return
        meta = self.current_metadata()
        if not meta:
            return
        name = self.name.text().strip()
        if not name:
            self.name.setText(str(meta.get("name", "")))
            return
        self.save_timer.stop()
        meta["downloadDirectory"] = self.download_folder.text().strip()
        if self.project_id == self.owner.current_project_id:
            self.owner.download_directory = meta["downloadDirectory"]
        meta.update({"name": name, "description": self.description.toPlainText().strip(),
                     "collection": self.collection.text().strip(), "status": self.status.currentData() or "",
                     "tags": [box.text() for box in self.tag_boxes if box.isChecked()],
                     "archived": self.archived.isChecked(), "notes": normalize_notes(self.notes),
                     "savedAt": datetime.now(timezone.utc).isoformat()})
        self.owner.persist_library_metadata(meta)
        if self.owner.current_project_id == self.project_id:
            self.owner.mark_dirty()
            self.owner.current_project_name = name
            self.owner.update_window_title()
        self.owner.refresh_project_library(self.project_id)

    def commit_note(self) -> None:
        value = self.note_text.text().strip()
        if not self.project_id or not value:
            return
        date = self.note_date.dateTime().toUTC().toString(Qt.DateFormat.ISODate)
        existing = next((note for note in self.notes if note["id"] == self.editing_note_id), None)
        if existing:
            existing.update({"text": value, "date": date})
        else:
            self.notes.append(new_note(value, date))
        self.editing_note_id = ""
        self.note_text.clear()
        self.note_date.setDateTime(QDateTime.currentDateTime())
        self.notes = normalize_notes(self.notes)
        self.refresh_notes()
        self.commit_fields()

    def create_tag(self) -> None:
        name = self.new_tag.text().strip()
        if not self.project_id or not name:
            return
        existing = {tag["name"].casefold() for tag in self.owner.project_status_tags() + self.owner.project_other_tags()}
        if name.casefold() in existing or name.casefold() == "archive":
            self.message.setText("That tag name is already in use.")
            return
        definitions = self.owner.project_other_tags()
        definitions.append({"name": name, "color": "#567a94"})
        self.owner.settings.setValue("project_other_tags", json.dumps(definitions))
        self.new_tag.clear()
        box = QCheckBox(name)
        box.setChecked(True)
        box.toggled.connect(self.commit_fields)
        self.tags_layout.addWidget(box)
        self.tag_boxes.append(box)
        self.commit_fields()
        self.owner.rebuild_project_filter_buttons()

    def edit_note(self, note_id: str) -> None:
        note = next((value for value in self.notes if value["id"] == note_id), None)
        if note:
            self.editing_note_id = note_id
            self.note_text.setText(note["text"])
            timestamp = QDateTime.fromString(note["date"], Qt.DateFormat.ISODate)
            if timestamp.isValid():
                self.note_date.setDateTime(timestamp.toLocalTime())
            self.note_text.setFocus()

    def delete_note(self, note_id: str) -> None:
        self.notes = [note for note in self.notes if note["id"] != note_id]
        self.refresh_notes()
        self.commit_fields()

    def set_note_checked(self, note_id: str, checked: bool) -> None:
        for note in self.notes:
            if note["id"] == note_id:
                note["checked"] = checked
        self.refresh_notes()
        self.commit_fields()


class ReferenceImageDock(QDockWidget):
    closed_by_user = Signal()

    def closeEvent(self, event):
        self.closed_by_user.emit()
        super().closeEvent(event)


class ProjectLibraryDock(QDockWidget):
    user_resized = Signal(int, int)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        on_separator = QApplication.widgetAt(QCursor.pos()) is self.parentWidget()
        if QApplication.mouseButtons() & Qt.MouseButton.LeftButton and (on_separator or self.isFloating()):
            self.user_resized.emit(self.width(), self.height())


class AIWorkingMarquee(QFrame):
    """A small animated striped status, visible only during real AI work."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("aiWorkMarquee")
        self.setFixedHeight(25)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._phase = 0
        self._title = "✦ AI working…"
        self._clock = QTimer(self)
        self._clock.setInterval(65)
        self._clock.timeout.connect(self._animate)
        self.hide()

    def begin(self, title="✦ AI working…"):
        self._title = title
        self.setAccessibleName(title)
        self._phase = 0
        self.show()
        if not self._clock.isActive():
            self._clock.start()
        self.update()

    def finish(self):
        self._clock.stop()
        self.hide()

    def _animate(self):
        self._phase = (self._phase + 2) % 22
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = self.rect().adjusted(1, 1, -1, -1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#132b3c"))
        painter.drawRoundedRect(outer, 5, 5)
        painter.save()
        painter.setClipRect(outer.adjusted(2, 2, -2, -2))
        painter.setPen(QPen(QColor("#42809e"), 1.6))
        for x in range(-34 + self._phase, self.width() + 34, 22):
            painter.drawLine(x, self.height() - 2, x + 16, 2)
        painter.restore()
        width = min(self.width() - 16,
                    max(126, painter.fontMetrics().horizontalAdvance(self._title) + 28))
        left = (self.width() - width) / 2
        center = QRectF(left, 2, width, self.height() - 4)
        # Shared borderless dark-blue chip for segment and unified refinement.
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#102f4b"))
        painter.drawRoundedRect(center, 5, 5)
        painter.setPen(QColor("#d8f2ff"))
        painter.drawText(center, Qt.AlignmentFlag.AlignCenter, self._title)
        painter.end()


class MiniMaxPromptPanel(QFrame):
    """Inline MiniMax controls. The production editor belongs to MainWindow."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.editor = owner.unified_prompt
        self.copy_button = QPushButton("□ Copy")
        self.copy_button.setObjectName("copyButton")
        self.copy_button.clicked.connect(owner.copy_minimax_prompt)
        self.busy = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        self.header = header
        self.cache_state = QLabel("Not generated")
        self.cache_state.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.cache_state.setMinimumWidth(70)
        self.cache_state.setObjectName("minimaxCacheState")
        label = QLabel("UNIFIED PROMPT")
        self.prompt_label = label
        label.setObjectName("sectionLabel")
        header.addWidget(label)
        header.addWidget(self.cache_state)
        header.addStretch()
        layout.addLayout(header)
        self.ai_marquee = AIWorkingMarquee(self)
        layout.addWidget(self.ai_marquee)
        self.message_panel = QFrame()
        self.message_panel.setObjectName("minimaxMessagePanel")
        message_layout = QHBoxLayout(self.message_panel)
        message_layout.setContentsMargins(8, 4, 8, 4)
        self.message_banner = QLabel()
        self.message_banner.setObjectName("minimaxMessageBanner")
        self.message_banner.setWordWrap(True)
        self.message_banner.setMaximumHeight(40)
        self.message_banner.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        message_layout.addWidget(self.message_banner, 1)
        self.recover_button = QPushButton("✓ Use generated result")
        self.recover_button.clicked.connect(owner.apply_pending_minimax_result)
        message_layout.addWidget(self.recover_button)
        self.copy_recovered_button = QPushButton("□ Copy generated")
        self.copy_recovered_button.clicked.connect(owner.copy_pending_minimax_result)
        message_layout.addWidget(self.copy_recovered_button)
        self.retry_button = QPushButton("↻ Retry")
        self.retry_button.clicked.connect(owner.retry_minimax_operation)
        message_layout.addWidget(self.retry_button)
        self.timeline_refine_button = QPushButton("✦ Regenerate")
        self.timeline_refine_button.clicked.connect(lambda: owner._generate_minimax_prompt(owner.minimax_prompt_mode))
        message_layout.addWidget(self.timeline_refine_button)
        layout.addWidget(self.message_panel)
        self.clear_message()

        # Untimed project references can accompany either MiniMax workflow.
        self.reference_dock = ReferenceImageDock("REFERENCE MEDIA", owner)
        self.reference_dock.setObjectName("minimaxReferenceDock")
        self.reference_dock.setMinimumWidth(200)
        self.reference_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.reference_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable | QDockWidget.DockWidgetFeature.DockWidgetMovable)
        content = QWidget()
        refs_layout = QVBoxLayout(content)
        self.reference_targets = []
        for index in range(4):
            target = MiniMaxReferenceSlot(index + 1, owner)
            target.changed.connect(lambda value, slot=index: owner.set_minimax_reference_image(slot, value))
            target.error.connect(lambda message: self.show_message(message, "warning"))
            target.export_requested.connect(lambda slot=index: owner.export_reference_image(slot))
            refs_layout.addWidget(target)
            self.reference_targets.append(target)
        refs_layout.addStretch()
        self.reference_scroll = QScrollArea()
        self.reference_scroll.setWidgetResizable(True)
        self.reference_scroll.setWidget(content)
        self.reference_dock.setWidget(self.reference_scroll)
        owner.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.reference_dock)
        self.reference_dock.hide()
        self.reference_dock.closed_by_user.connect(lambda: owner.set_reference_panel_open(False))
        self.reference_dock.toggleViewAction().triggered.connect(owner.set_reference_panel_open)
        self.workflow_label = owner.workflow_label

    def set_project(self, project_name: str, prompt: str, instructions: str, cache_state: str) -> None:
        self.refresh_references()
        self.set_cache_state(cache_state)
        self.owner.refresh_shared_editor()

    def refresh_references(self, update_images: bool = True) -> None:
        if self.owner.prompt_only_workspace:
            self.workflow_label.setText("Annotations only")
            self.workflow_label.setToolTip("Refine prompt text without segments or media context")
            return
        segments = self.owner.segments
        refs = reference_slots(self.owner.minimax_reference_images)
        active_refs = self.owner.active_reference_images() if self.owner.unified_workspace or self.owner.workspace_engine == "generic" else []
        workflow = detect_workflow(segments, active_refs)
        self.workflow_label.setText("Frames + references" if self.owner.workspace_engine == "minimax_frames" and any(active_refs)
                                    else "Conditioning frames" if self.owner.workspace_engine == "minimax_frames"
                                    else WORKFLOW_NAMES[workflow])
        inventory = reference_inventory(segments, active_refs)
        self.workflow_label.setToolTip("Detected from timeline and active reference images.\n" + "\n".join(
            f"{item['label']}: {item['name']}"
            + (f" · {item['role']} at {item['checkpoint_time']:.3f}s" if "checkpoint_time" in item else "")
            for item in inventory if item["label"]
        ))
        if update_images:
            labels = {item["reference_slot"]: item["label"] + (" / " + item["audio_label"] if item.get("audio_label") else "") for item in inventory if "reference_slot" in item}
            for index, target in enumerate(self.reference_targets):
                target.set_value(refs[index], labels.get(index))

    def set_cache_state(self, text: str) -> None:
        self.cache_state.setText(text)
        self.cache_state.setToolTip(text)
        self.cache_state.setProperty(
            "active", any(word in text.casefold() for word in
                          ("refining", "generating", "working", "rendering"))
        )
        self.cache_state.style().unpolish(self.cache_state)
        self.cache_state.style().polish(self.cache_state)

    def show_message(self, text: str, level: str = "info", retry: bool = False) -> None:
        if level == "timeline":
            # This used to insert a permanent "Segment changes available"
            # callout. The one Refine button already handles synchronization.
            return
        if level in {"error", "warning"}:
            self.ai_marquee.finish()
        self.message_banner.setText(text)
        self.message_banner.setToolTip(text)
        self.message_panel.setProperty("level", level)
        self.message_panel.style().unpolish(self.message_panel)
        self.message_panel.style().polish(self.message_panel)
        recovery = bool(getattr(self.owner, "pending_minimax_result", ""))
        self.recover_button.setVisible(recovery)
        self.copy_recovered_button.setVisible(recovery)
        # One primary AI control owns all generation/refinement actions.
        self.retry_button.hide()
        self.timeline_refine_button.hide()
        self.message_panel.show()

    def clear_message(self) -> None:
        self.message_banner.clear()
        self.retry_button.hide()
        self.recover_button.hide()
        self.copy_recovered_button.hide()
        self.timeline_refine_button.hide()
        self.message_panel.hide()

    def set_busy(self, busy: bool, status: str = "", *, show_progress: bool = True) -> None:
        self.busy = busy
        if status:
            self.set_cache_state(status)
        if busy:
            if (self.message_panel.isVisible()
                    and not getattr(self.owner, "pending_minimax_result", "")
                    and self.message_panel.property("level") == "success"):
                self.clear_message()  # Don't stack a stale success banner over a new job.
            if show_progress:
                self.ai_marquee.begin("✦ " + (status or "AI working…"))
            else:
                self.ai_marquee.finish()
        elif not getattr(self.owner, "_artwork_jobs", 0):
            self.ai_marquee.finish()
        self.update_actions()

    def update_actions(self) -> None:
        self.copy_button.setEnabled(bool(self.editor.toPlainText().strip()))
        self.retry_button.setEnabled(not self.busy)
        self.recover_button.setEnabled(not self.busy)
        self.copy_recovered_button.setEnabled(bool(getattr(self.owner, "pending_minimax_result", "")))
        self.timeline_refine_button.setEnabled(not self.busy)

    def focus_prompt_editor(self) -> None:
        self.editor.setFocus(Qt.FocusReason.OtherFocusReason)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(application_window_title())
        self.setWindowIcon(QIcon(str(files("ltx_prompt_director").joinpath("assets/icon.png"))))
        self.resize(1450, 900)
        self._viewport_timer=QTimer(self)
        self._viewport_timer.setSingleShot(True);self._viewport_timer.setInterval(0)
        self._viewport_timer.timeout.connect(self.keep_window_on_screen)
        self.settings = QSettings()
        self._settings_sync_timer = QTimer(self)
        self._settings_sync_timer.setSingleShot(True)
        self._settings_sync_timer.setInterval(250)
        self._settings_sync_timer.timeout.connect(self.settings.sync)
        self.session_keys = {"gemini": self.settings.value("gemini_key", ""), "openai": self.settings.value("openai_key", "")}
        self.chatgpt_auth = ChatGPTAuth()
        self.segments: list[Segment] = []
        self.current_project_id: str | None = None
        self.current_project_name = "Untitled"
        self.download_directory = ""
        self._reserved_exports = set()
        self.project_dirty = False
        self.project_sessions: dict[str, dict] = {}
        self.minimax_prompt_text = ""
        self.minimax_refinement_instructions = ""
        self.minimax_prompt_cache_key = ""
        self.minimax_prompt_updated_at = ""
        self.minimax_prompt_mode = "frames"
        self.minimax_reference_images = [None] * 4
        self.pending_minimax_result = ""
        self.minimax_panel: MiniMaxPromptPanel | None = None
        self.workspace_store = WorkspaceStore()
        self.workspace_definitions = self.workspace_store.load()
        self.project_type = "ltx" if "ltx" in self.workspace_definitions else next(iter(self.workspace_definitions), "")
        self.minimax_drafts: dict[str, dict] = {}
        self.workspace_revision = 0
        self.ai_busy = False
        self.current_collection: str | None = None
        self.shot_sequence_settings = dict(SHOT_DEFAULTS)
        self.autofit_tail_extension = 0
        self.timeline_fit_mode = False
        self._restoring_layout = True
        self.project_panel_width = max(180, self.settings.value("project_panel_width", 330, int))
        self.project_panel_height = max(160, self.settings.value("project_panel_height", 300, int))
        saved_icon_size = self.settings.value("project_icon_size", 230, int)
        self.project_icon_size = min((96, 156, 230), key=lambda size: abs(size - saved_icon_size))
        self.pixels_per_second = 65
        self._timeline_fit_timer = QTimer(self)
        self._timeline_fit_timer.setSingleShot(True)
        self._timeline_fit_timer.setInterval(60)
        self._timeline_fit_timer.timeout.connect(self.apply_timeline_fit)
        self._project_open_timer = QTimer(self)
        self._project_open_timer.setSingleShot(True)
        self._project_open_timer.setInterval(90)
        self._project_open_timer.timeout.connect(self.open_pending_library_project)
        self._pending_project_id: str | None = None
        self.timeline_height = max(184, min(430, self.settings.value("timeline_panel_height", 420, int)))
        self.thread_pool = QThreadPool.globalInstance()
        self.disk_pool = QThreadPool(self)
        self.disk_pool.setMaxThreadCount(1)
        self.media_pool = QThreadPool(self)
        self.media_pool.setMaxThreadCount(2)
        self._edit_serial = 0
        self._latest_save_id = {}
        self._pending_disk_jobs = 0
        self._close_after_jobs = False
        self._close_saves_queued = False
        self._closing_progress = None
        self._closing_total = 0
        self._open_serial = 0
        self._timeline_signature = ()
        self._loading = True
        self.setDockNestingEnabled(True)
        splash = getattr(QApplication.instance(), '_director_splash', None)
        if splash:
            splash.set_status('Creating timeline and project panels…')
            QApplication.processEvents()
        self._build_ui()
        self._effect_drop_targets = {
            self.timeline.viewport(): "timeline",
            self.segment_prompt.viewport(): "segment",
            self.intent_segment_prompt.viewport(): "segment",
            self.unified_prompt.viewport(): "unified",
        }
        for viewport in self._effect_drop_targets:
            viewport.installEventFilter(self)
        self._loading = False
        self.project_dirty = False
        for editor in self.findChildren(QTextEdit):
            install_spellcheck(editor)
            if isinstance(editor, PromptTextEdit):
                install_prompt_tags(editor)
        if splash:
            splash.set_status('Preparing text tools and appearance…')
            QApplication.processEvents()
        self._apply_theme()
        self.magic_overlay = MagicBuildOverlay(self)
        self.inline_typing = LiveTypingPreview(self)
        self.paragraph_review = ParagraphDiffReview(self)
        self.segment_prompt_review = SegmentPromptReview(self)
        self.reasoning_dock = ReasoningActivityDock(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.reasoning_dock)
        geometry = self.settings.value("window/geometry")
        state = self.settings.value("window/state")
        if geometry:
            self.restoreGeometry(geometry)
        if state:
            self.restoreState(state)
        self.sync_reference_panel_visibility()
        self.tabifiedDockWidgetActivated.connect(lambda *_: self.queue_project_panel_width_restore())
        QTimer.singleShot(0, self.restore_startup_workspace)
        self.update_window_title()

    def open_catalog_viewer(self, entry):
        entries = [self.media_catalog.tiles.item(i).data(Qt.ItemDataRole.UserRole)
                   for i in range(self.media_catalog.tiles.count())]
        index = next((i for i, value in enumerate(entries) if value['id'] == entry['id']), None)
        if index is not None:
            viewer = CatalogMediaViewer(self, entries, index)
            viewer.setWindowModality(Qt.WindowModality.WindowModal)
            viewer.show()

    def _build_ui(self) -> None:
        self._build_project_dock()
        self._build_project_preview_dock()
        self._build_project_files_dock()
        self._build_project_properties_dock()
        self._build_effects_dock()
        self._build_local_generation_ui()
        self.catalog_dock = QDockWidget("Media Catalog", self)
        self.catalog_dock.setObjectName("mediaCatalogDock")
        self.media_catalog = MediaCatalog(self)
        self.media_catalog.add_to_timeline.connect(self.add_media_paths)
        self.media_catalog.view_requested.connect(self.open_catalog_viewer)
        self.catalog_dock.setWidget(self.media_catalog)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.catalog_dock)
        self.catalog_dock.hide()
        toolbar = QToolBar("Project")
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(22, 22))
        self.addToolBar(toolbar)
        self.toolbar_brand = QWidget();self.toolbar_brand.setObjectName('workspaceBrand')
        brand_row=QHBoxLayout(self.toolbar_brand);brand_row.setContentsMargins(8,0,18,0);brand_row.setSpacing(8)
        brand_icon=QLabel();brand_icon.setPixmap(QPixmap(str(files('ltx_prompt_director').joinpath('assets/icon.png'))).scaled(24,24,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        brand_row.addWidget(brand_icon)
        brand_row.addWidget(QLabel(f'PROMPT DIRECTOR <span style="color:#808b9d;font-size:12px">{__version__}</span>'))
        self.toolbar_brand_action=toolbar.addWidget(self.toolbar_brand)
        projects_action = self.project_dock.toggleViewAction()
        projects_action.setText("Projects")
        projects_action.setIcon(toolbar_icon("projects"))
        toolbar.addAction(projects_action)
        preview_action = self.project_preview_dock.toggleViewAction()
        preview_action.setText("Preview")
        preview_action.setIcon(toolbar_icon("preview"))
        toolbar.addAction(preview_action)
        self.effects_dock.toggleViewAction().setIcon(toolbar_icon('catalog'))
        toolbar.addAction(self.effects_dock.toggleViewAction())
        files_action = self.project_files_dock.toggleViewAction()
        files_action.setText("Project Files")
        files_action.setIcon(toolbar_icon("project-files"))
        toolbar.addAction(files_action)
        properties_action = self.project_properties_dock.toggleViewAction()
        properties_action.setText("Properties")
        properties_action.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        toolbar.addAction(properties_action)
        catalog_action = self.catalog_dock.toggleViewAction()
        catalog_action.setIcon(toolbar_icon("catalog"))
        toolbar.addAction(catalog_action)
        # The image/reference tool belongs alongside Media Catalog, not
        # beside the workspace selector. Icon-only, with a full tooltip.
        self.references_button = QToolButton(toolbar)
        self.references_button.setObjectName("toolbarRefs")
        self.references_button.setIcon(toolbar_icon("references"))
        self.references_button.setIconSize(QSize(22, 22))
        self.references_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.references_button.setAccessibleName("Reference media")
        self.references_button.setToolTip("Open reference artwork and conditioning media")
        self.references_button.clicked.connect(self.show_reference_images)
        toolbar.addWidget(self.references_button)
        toolbar.addSeparator()
        action_groups = [
            (("New Project", "new", self.new_project),
             ("Open", "open", self.open_project),
             ("Save to Library", "save", self.save_library_project),
             ("Export Project", "export-project", self.export_project)),
            (("Import", "import", self.import_ltx),
             ("Export", "export-ltx", self.export_workflow)),
            (("Delete selected", "delete", self.delete_selected),),
        ]
        for group_index, group in enumerate(action_groups):
            for label, icon_name, callback in group:
                action = QAction(toolbar_icon(icon_name), label, self)
                action.triggered.connect(callback)
                if label == "Export":
                    self.workflow_export_action = action
                elif label == "Import":
                    self.timeline_import_action = action
                elif label == "Delete selected":
                    self.delete_segment_action = action
                toolbar.addAction(action)
            if group_index == 1:
                self.comfy_export_action = QAction(QIcon(str(files('ltx_prompt_director').joinpath('assets/comfyui-logo.svg'))), "Export ComfyUI", self)
                self.comfy_export_action.setToolTip("Export a MiniMax ComfyUI workflow and copy its media to the configured ComfyUI input folder")
                self.comfy_export_action.triggered.connect(self.export_comfy_workflow)
                toolbar.addAction(self.comfy_export_action)
                toolbar.widgetForAction(self.comfy_export_action).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            if group_index < len(action_groups) - 1:
                toolbar.addSeparator()
        # The workspace selector is a global choice and belongs with the
        # global commands. No separate full-width "PROJECT TYPE" strip.
        toolbar.addSeparator()
        self.project_type_combo = QComboBox(toolbar)
        self.project_type_combo.setObjectName("workspacePicker")
        self.project_type_combo.setAccessibleName("Project type")
        self.project_type_combo.setToolTip("Choose project type / workspace")
        self.project_type_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.project_type_combo.setMinimumContentsLength(19)
        for key, definition in self.workspace_definitions.items():
            self.project_type_combo.addItem(definition["name"], key)
        self.project_type_combo.currentIndexChanged.connect(self.project_type_changed)
        toolbar.addWidget(self.project_type_combo)
        # Workflow information remains available as a tooltip and in the
        # status bar; repeating it beside the selector wastes horizontal room.
        self.workflow_label = QLabel(self)
        self.workflow_label.setObjectName("muted")
        self.workflow_label.hide()
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        self.downloads_button = QPushButton()
        self.downloads_button.setIcon(toolbar_icon("downloads"))
        self.downloads_button.setIconSize(QSize(22, 22))
        self.downloads_button.setToolTip("Recent exports")
        self.downloads_button.clicked.connect(self.toggle_downloads)
        downloads_toolbar_action = toolbar.addWidget(self.downloads_button)
        self.download_tray = DownloadTray(self.settings, self)
        self.download_tray.folder_button.clicked.connect(self.open_download_folder)
        self.provider_button = QPushButton()
        self.provider_button.setObjectName("toolbarButton")
        self.provider_button.clicked.connect(self.open_settings)
        self.update_provider_button()
        toolbar.addWidget(self.provider_button)
        toolbar.addSeparator()
        current_text_scale = self.settings.value("ui_text_scale", 100, int)
        self.ui_scale_label = QLabel("TEXT")
        self.ui_scale_label.setObjectName("timelineControlLabel")
        self.ui_scale_label.setToolTip("Interface text scale")
        # The percentage field conveys text scale without an extra toolbar label.
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
        settings_action = QAction(toolbar_icon("settings"), "Settings", self)
        settings_action.triggered.connect(self.open_settings)
        toolbar.addAction(settings_action)
        help_button = QToolButton(toolbar)
        help_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogHelpButton))
        help_button.setToolTip("Help and setup")
        help_button.setAccessibleName("Help and setup")
        help_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        help_menu = QMenu(help_button)
        help_menu.addAction("Interactive illustrated manual", self.open_interactive_help)
        help_menu.addAction("Setup wizard…", self.show_onboarding)
        help_menu.addAction("Image generation panel", lambda: self.image_generation_dock.show())
        help_button.setMenu(help_menu); toolbar.addWidget(help_button)

        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(10, 5, 10, 8)
        outer.setSpacing(8)
        timeline_shell = QFrame()
        self.timeline_shell = timeline_shell
        timeline_shell.setObjectName("timelineShell")
        timeline_layout = QVBoxLayout(timeline_shell)
        timeline_layout.setContentsMargins(0, 0, 0, 0)
        timeline_layout.setSpacing(0)
        # Output size stays in the project toolbar; navigation sits by the ruler.
        self.output_size_control = QFrame()
        self.output_size_control.setToolTip("Output resolution (width × height, pixels)")
        self.output_size_control.setAccessibleName("Output resolution")
        self.output_size_control.setObjectName("outputSizeControl")
        output_size_layout = QHBoxLayout(self.output_size_control)
        output_size_layout.setContentsMargins(0, 0, 0, 0)
        output_size_layout.setSpacing(0)
        self.output_width = QSpinBox()
        self.output_width.setObjectName("timelineSpin")
        self.output_width.setRange(256, 4096)
        self.output_width.setSingleStep(32)
        self.output_width.setValue(1280)
        self.output_width.setToolTip("Output width in pixels")
        self.output_width.valueChanged.connect(self.mark_dirty)
        self.output_width.editingFinished.connect(self.normalize_output_dimensions)
        self.output_height = QSpinBox()
        self.output_height.setObjectName("timelineSpin")
        self.output_height.setRange(256, 4096)
        self.output_height.setSingleStep(32)
        self.output_height.setValue(704)
        self.output_height.setToolTip("Output height in pixels")
        self.output_height.valueChanged.connect(self.mark_dirty)
        self.output_height.editingFinished.connect(self.normalize_output_dimensions)
        resolution_separator = QLabel("×")
        resolution_separator.setObjectName("resolutionSeparator")
        resolution_separator.setAlignment(Qt.AlignmentFlag.AlignCenter)
        output_size_layout.addWidget(self.output_width)
        output_size_layout.addWidget(resolution_separator)
        output_size_layout.addWidget(self.output_height)
        self.timeline_scale = QSlider(Qt.Orientation.Horizontal)
        self.timeline_scale.setAccessibleName("Timeline zoom")
        self.timeline_scale.setRange(0, 160)
        self.timeline_scale.setValue(self.pixels_per_second)
        self.timeline_scale.setMinimumWidth(70)
        self.timeline_scale.setMaximumWidth(135)
        self.timeline_scale.setToolTip("Timeline zoom — move left to fit more of the sequence")
        self.timeline_scale.valueChanged.connect(self.set_timeline_scale)
        self.timeline_fit_button = QToolButton()
        self.timeline_fit_button.setObjectName("timelineFitButton")
        self.timeline_fit_button.setAutoRaise(True)
        self.timeline_fit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.timeline_fit_button.setIcon(QIcon(str(files("ltx_prompt_director").joinpath("assets/timeline-fit.svg"))))
        self.timeline_fit_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.timeline_fit_button.setAccessibleName("Fit timeline")
        self.timeline_fit_button.setToolTip("Auto-fit the complete timeline — show every segment without horizontal scrolling")
        self.timeline_fit_button.clicked.connect(self.autofit_timeline)
        self.timeline_output_action = toolbar.insertWidget(downloads_toolbar_action, self.output_size_control)
        toolbar.insertSeparator(downloads_toolbar_action)
        self.ai_timeline_status = QLabel("")
        self.ai_timeline_status.setObjectName("timelineControlLabel")
        self.ai_timeline_status.setToolTip("Live AI timeline edit preview; changes apply only after validated completion")
        self.ai_timeline_status.setMaximumWidth(205)
        self.ai_timeline_status.hide()
        self.ai_edit_feed = QLabel()
        self.ai_edit_feed.setObjectName("aiEditFeed")
        self.ai_edit_feed.setWordWrap(True)
        self.ai_edit_feed.setStyleSheet(
            "QLabel#aiEditFeed{background:#162b41;color:#b6d7ff;"
            "border:1px solid #35608d;border-radius:6px;padding:5px 10px}"
        )
        self.ai_edit_feed.hide()
        timeline_layout.addWidget(self.ai_edit_feed)
        ruler_row = QHBoxLayout()
        ruler_row.setContentsMargins(0, 0, 0, 0)
        self.ruler_spacer = QWidget()
        self.ruler_spacer.setFixedWidth(38)
        self.ruler = TimelineRuler()
        ruler_row.addWidget(self.ruler_spacer)
        ruler_row.addWidget(self.ruler, 1)
        self.timeline_navigation = QWidget()
        navigation_layout = QHBoxLayout(self.timeline_navigation)
        navigation_layout.setContentsMargins(8, 1, 0, 1)
        navigation_layout.setSpacing(7)
        navigation_layout.addWidget(QLabel("Zoom"))
        navigation_layout.addWidget(self.timeline_scale)
        navigation_layout.addWidget(self.timeline_fit_button)
        ruler_row.addWidget(self.timeline_navigation)
        timeline_layout.addLayout(ruler_row)
        track_row = QHBoxLayout()
        track_row.setContentsMargins(0, 0, 0, 0)
        track_row.setSpacing(0)
        self.main_track_label = QLabel("◉")
        self.main_track_label.setObjectName("mainTrackLabel")
        self.main_track_label.setAccessibleName("Main timeline track")
        self.main_track_label.setToolTip("Main timeline track")
        self.main_track_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.main_track_label.setFixedWidth(38)
        track_row.addWidget(self.main_track_label)
        self.timeline = TimelineListWidget()
        self.timeline.setObjectName("timeline")
        self.timeline.setProperty("dropActive", False)
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
        self.timeline.currentRowChanged.connect(self.update_timeline_selection_style)
        self.timeline.itemClicked.connect(self.reload_clicked_segment)
        self.timeline.model().rowsMoved.connect(lambda *_: self.sync_order())
        self.timeline.files_dropped.connect(self.add_media_paths)
        self.timeline.files_replaced.connect(self.replace_timeline_media)
        self.timeline.projects_dropped.connect(self.project_files_dropped)
        self.timeline.horizontalScrollBar().valueChanged.connect(self.ruler.set_offset)
        self.timeline_loading = TimelineLoadingOverlay(self.timeline.viewport())
        track_row.addWidget(self.timeline, 1)
        # Slim insertion rail with two distinct click targets and tooltips.
        self.add_tile = QPushButton("＋")
        self.add_tile.setObjectName("addTile")
        self.add_tile.setToolTip("Add image or video to timeline")
        self.add_tile.setAccessibleName("Add media")
        self.add_tile.clicked.connect(self.add_media)
        self.add_text_tile = QPushButton("T＋")
        self.add_text_tile.setObjectName("addTextTile")
        self.add_text_tile.setToolTip("Add a text segment")
        self.add_text_tile.setAccessibleName("Add text")
        self.add_text_tile.clicked.connect(self.add_text_segment)
        self.retime_all_button = QPushButton("")
        self.retime_all_button.setObjectName("addTile")
        self.retime_all_button.setAccessibleName("Re-time all segments")
        self.retime_all_button.setToolTip(
            "Re-time all segments to the sequence length box using dialogue, action, camera and shot pacing. "
            "Only timing changes; review or revert the result. Set length to 0 to keep the current total.")
        self.retime_all_button.clicked.connect(self.retime_all_segments)
        self.add_tile_wrap = QFrame()
        self.add_tile_wrap.setObjectName("addTileBox")
        self.add_tile_wrap.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.add_tile_wrap.setFixedSize(44, self.timeline_height)
        self.add_tile_layout = QVBoxLayout(self.add_tile_wrap)
        self.add_tile_layout.setContentsMargins(3, 4, 3, 4)
        self.add_tile_layout.setSpacing(5)
        self.add_tile.setFixedSize(32, 32)
        self.add_text_tile.setFixedSize(32, 32)
        self.retime_all_button.setFixedSize(32, 32)
        self.add_tile_layout.addWidget(self.add_tile, 0, Qt.AlignmentFlag.AlignHCenter)
        self.add_tile_layout.addWidget(self.add_text_tile, 0, Qt.AlignmentFlag.AlignHCenter)
        self.add_tile_layout.addStretch(1)
        track_row.addWidget(self.add_tile_wrap)
        timeline_layout.addLayout(track_row)
        self.timeline_height_handle = TimelineHeightHandle(self.timeline_height)
        self.timeline_height_handle.height_changed.connect(self.set_timeline_height)
        self.timeline_height_handle.finished.connect(self.finish_timeline_height_resize)
        timeline_layout.addWidget(self.timeline_height_handle)
        outer.addWidget(timeline_shell)
        self.conditioning_guide = QScrollArea()
        self.conditioning_guide.setObjectName("conditioningGuide")
        self.conditioning_guide.setWidgetResizable(True)
        self.conditioning_height = max(0, min(260, self.settings.value("conditioning_panel_height", 144, int)))
        if self.conditioning_height:
            self.conditioning_height = max(96, self.conditioning_height)
        self.conditioning_guide.setFixedHeight(self.conditioning_height)
        self.conditioning_guide.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.conditioning_content = QWidget()
        self.conditioning_row = QHBoxLayout(self.conditioning_content)
        self.conditioning_row.setContentsMargins(4, 1, 4, 1)
        self.conditioning_row.setSpacing(4)
        self.conditioning_guide.setWidget(self.conditioning_content)
        outer.addWidget(self.conditioning_guide)
        self.conditioning_height_handle = ConditioningHeightHandle(self.conditioning_height)
        self.conditioning_height_handle.last_height = self.conditioning_height or max(
            96, min(260, self.settings.value("conditioning_panel_last_height", 144, int)))
        self.conditioning_height_handle.height_changed.connect(self.set_conditioning_height)
        self.conditioning_height_handle.finished.connect(self.finish_conditioning_height_resize)
        outer.addWidget(self.conditioning_height_handle)

        self.sequence_bar = QLabel()
        self.sequence_bar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.sequence_bar.setObjectName("sequenceBar")
        self.sequence_controls = QHBoxLayout()
        self.sequence_controls.setContentsMargins(0, 0, 0, 0)
        self.sequence_controls.setSpacing(3)
        self.sequence_controls.addWidget(self.sequence_bar)
        # Bulk review is inside the existing metadata strip, not a new row.
        self.timeline_approve_all = QCheckBox("✓ All")
        self.timeline_deny_all = QCheckBox("× Deny all")
        for checkbox, color, tooltip in (
            (self.timeline_approve_all, "#1e4933", "Approve all pending timeline edits"),
            (self.timeline_deny_all, "#542d33", "Deny all pending timeline edits"),
        ):
            checkbox.setFixedHeight(21)
            checkbox.setToolTip(tooltip)
            checkbox.setStyleSheet(
                "QCheckBox{border:0;border-radius:4px;background:" + color +
                ";color:#f0f5f5;font-size:10px;padding:2px 5px;spacing:3px}"
            )
            checkbox.hide()
            self.sequence_controls.addWidget(checkbox)
        self.timeline_approve_all.toggled.connect(
            lambda yes: self.resolve_all_segment_reviews(True) if yes else None)
        self.timeline_deny_all.toggled.connect(
            lambda yes: self.resolve_all_segment_reviews(False) if yes else None)
        outer.addLayout(self.sequence_controls)

        director_panel = QFrame()
        self.director_panel = director_panel
        director_panel.setObjectName("directorPanel")
        self.director_controls = QVBoxLayout(director_panel)
        self.director_controls.setContentsMargins(10, 8, 10, 8)
        self.director_controls.setSpacing(5)
        # Editing is the primary content: the selector and title occupy their
        # own compact header, never a column beside the editable prose.
        intent_row = QHBoxLayout()
        intent_row.setContentsMargins(2, 0, 2, 0)
        intent_row.setSpacing(9)
        intent_label = QLabel("DIRECTOR'S INTENT")
        self.director_intent_label = intent_label
        intent_label.setObjectName("panelTitle")
        intent_label.hide()  # The selector identifies the document already.
        intent_row.addStretch(1)
        self.director_intent_row = intent_row
        self.intent = PromptTextEdit()
        self.intent.setAcceptRichText(False)
        intent_example = (
            "Example: A lost courier discovers a glowing map, crosses the storm, and reaches the beacon at sunrise. "
            "Describe the narrative, action, pacing, camera, dialogue wording, and ending you want."
        )
        self.intent.setPlaceholderText(intent_example)
        self.intent.setToolTip(intent_example)
        self.intent.textChanged.connect(self.mark_dirty)
        # One visible editor at the Director's Intent location. Changing the
        # selector switches underlying documents without changing their data.
        self.director_intent_stack = QStackedWidget()
        self.director_intent_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored
        )
        self.director_intent_stack.setMinimumWidth(0)
        self.director_intent_stack.addWidget(self.intent)
        self.intent_segment_prompt = PromptTextEdit()
        self.intent_segment_prompt.setObjectName("directorSegmentEditor")
        self.intent_segment_prompt.setAcceptRichText(False)
        self.intent_segment_prompt.setPlaceholderText(
            "Select a timeline segment to display and edit its prompt here…"
        )
        self.intent_segment_prompt.textChanged.connect(self.save_director_segment_prompt)
        self.director_intent_stack.addWidget(self.intent_segment_prompt)
        for editor in (self.intent, self.intent_segment_prompt):
            editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            editor.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
            editor.document().setDocumentMargin(10)
            editor.setMinimumWidth(0)
        self.director_intent_stack.setToolTip(
            "Edit the full-width prompt here. Choose Director's Intent or "
            "Selected Segment above; the Refine button remains on the timeline."
        )
        self.sfx = QPushButton("SFX")
        self.sfx.setCheckable(True)
        self.sfx.setObjectName("audioToggle")
        self.sfx.toggled.connect(self.mark_dirty)
        self.spoken_dialog = QPushButton("Dialog")
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
        self.reduce_music = QPushButton("Less music")
        self.reduce_music.setCheckable(True)
        self.reduce_music.setObjectName("qualityToggle")
        self.reduce_music.setToolTip("Ask the model for ambient sound instead of prominent background music")
        self.reduce_music.toggled.connect(self.mark_dirty)
        for toggle in (self.sfx, self.spoken_dialog, self.hdr, self.reduce_music):
            toggle.setObjectName("directorOption")
            toggle.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        # The upper prompt editor has a dedicated, fixed pair of actions.
        self.magic_button = QPushButton("Generate")
        self.magic_button.setObjectName("promptAction")
        self.magic_button.setAccessibleName("Generate selected segment or entire sequence (Global)")
        self.magic_button.clicked.connect(self.generate_upper_prompt)
        self.prompt_generate_button = self.magic_button
        self.prompt_refine_button = QPushButton("Refine")
        self.prompt_refine_button.setObjectName("promptAction")
        self.prompt_refine_button.setAccessibleName("Refine selected segment or entire sequence (Global)")
        self.prompt_refine_button.clicked.connect(self.refine_upper_prompt)
        self.segment_quick_fix_button = self.make_quick_fix_button("Segment quick fixes", "segment")
        self.primary_ai_command = "generate"
        for button in (self.sfx, self.spoken_dialog, self.hdr, self.reduce_music):
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.magic_button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        # The sole Generate/Refine action lives in the active prompt header.
        self.director_controls.addLayout(intent_row)
        self.director_ai_marquee = AIWorkingMarquee(director_panel)
        self.director_controls.addWidget(self.director_ai_marquee)
        self.director_controls.addWidget(self.director_intent_stack, 1)
        # Length and the quality/audio options sit directly below the
        # conditioning preview, not underneath the text editor.
        length_label = QLabel("Len")
        self.length_label = length_label
        length_label.hide()
        length_label.setObjectName("groupLabel")
        self.requested_length = QDoubleSpinBox()
        self.requested_length.setObjectName("timelineSpin")
        self.requested_length.setRange(0, sys.float_info.max)
        self.requested_length.setSingleStep(.01)
        self.requested_length.setDecimals(2)
        self.requested_length.setSuffix(" s")
        self.requested_length.setSpecialValueText("Auto")
        self.requested_length.setToolTip(
            "Target sequence length in seconds. Re-time all fits this duration; Auto keeps the current total for retiming.")
        self.requested_length.setAccessibleName("Total sequence length")
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
        self.language_label = language_label
        self.accent_label = accent_label
        voice_panel = QWidget()
        voice_form = QFormLayout(voice_panel)
        voice_form.setContentsMargins(8, 6, 8, 6)
        voice_form.setSpacing(6)
        voice_form.addRow(language_label, self.speaker_language)
        voice_form.addRow(accent_label, self.speaker_accent)
        self.voice_options_button = QToolButton()
        self.voice_options_button.setObjectName("voiceOptions")
        self.voice_options_button.setText("⚙")
        self.voice_options_button.setAccessibleName("Spoken dialog language and accent")
        self.voice_options_button.setToolTip("Language and accent for spoken dialogue")
        self.voice_options_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.voice_options_menu = QMenu(self.voice_options_button)
        voice_action = QWidgetAction(self.voice_options_menu)
        voice_action.setDefaultWidget(voice_panel)
        self.voice_options_menu.addAction(voice_action)
        self.voice_options_button.setMenu(self.voice_options_menu)
        # A single horizontal strip shares space with sequence metadata.
        options_row = self.sequence_controls
        options_row.setSpacing(4)
        # Time → image quality → sound → speech → music. No duplicate
        # "PROMPT OPTIONS" label or oversized form-style action buttons.
        # No redundant Len label; the total-length spin is self-describing.
        options_row.addWidget(self.requested_length)
        options_row.addWidget(self.hdr)
        options_row.addWidget(self.sfx)
        options_row.addWidget(self.spoken_dialog)
        options_row.addWidget(self.voice_options_button)
        options_row.addWidget(self.reduce_music)
        self.options_row = options_row
        # The Director editor below has only a small scope selector, then text.

        segment_panel = QFrame()
        self.segment_panel = segment_panel
        segment_panel.setObjectName("promptPanel")
        segment_layout = QVBoxLayout(segment_panel)
        segment_layout.setContentsMargins(9, 6, 9, 5)
        self.segment_header = ControlRow()
        self.segment_header.setSpacing(8)
        self.prompt_label = QLabel("SEGMENT PROMPT")
        segment_label = self.prompt_label
        segment_label.setObjectName("sectionLabel")
        self.segment_header.addWidget(segment_label)
        self.prompt_scope = QComboBox()
        self.prompt_scope.addItem("Global", "global")
        self.prompt_scope.addItem("Segment", "segment")
        self.prompt_scope.setToolTip(
            "Global: generate/refine the entire timeline from Director's Intent "
            "and rebuild the combined prompt. Segment: generate/refine only "
            "the selected segment without changing its neighbors."
        )
        self.prompt_scope.setMinimumWidth(0)
        self.prompt_scope.setAccessibleName("Prompt editor scope")
        self.prompt_scope.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.prompt_scope.currentIndexChanged.connect(self.prompt_scope_changed)
        # The upper Generate and Refine controls stay with this editor.
        self.director_intent_row.addWidget(self.segment_quick_fix_button)
        self.director_intent_row.addWidget(self.magic_button)
        self.director_intent_row.addWidget(self.prompt_refine_button)
        self.director_intent_row.addWidget(self.prompt_scope)
        # Combined-prompt actions are created separately below.
        self.refine_timing_button = QPushButton("⏱ Refine Timing")
        self.refine_timing_button.setObjectName("refineButton")
        self.refine_timing_button.setToolTip("Analyze the existing sequence and retime only the selected segment")
        self.refine_timing_button.clicked.connect(self.refine_selected_timing)
        self.refine_timing_button.setEnabled(False)
        self.refine_prompt_button = QPushButton("✎ Refine Prompt")
        self.refine_prompt_button.setObjectName("refineButton")
        self.refine_prompt_button.setToolTip("Refine only the selected segment prompt using adjacent frames for continuity; may also adjust its duration; MiniMax then generates the unified prompt")
        self.refine_prompt_button.clicked.connect(self.refine_project_prompt)
        self.refine_prompt_button.setEnabled(False)
        # Compatibility-only controls; actions now live in the one AI menu.
        # No duplicate refine buttons remain in prompt headers.
        self.refine_timing_button.hide()
        self.refine_prompt_button.hide()
        self.frame_number = QLabel("Frame —")
        self.frame_number.setObjectName("muted")
        self.start_button = QPushButton("Start")
        self.end_button = QPushButton("End")
        self.start_button.setToolTip("Set selected image as start frame")
        self.end_button.setToolTip("Set selected image as end frame")
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
        self.duration_spin.setRange(MIN_DURATION, sys.float_info.max)
        self.duration_spin.setSingleStep(.01)
        self.duration_spin.setDecimals(2)
        self.duration_spin.setSuffix(" s")
        self.duration_spin.setToolTip("Selected segment duration in seconds")
        self.duration_spin.setAccessibleName("Selected segment duration")
        self.duration_spin.valueChanged.connect(self.editor_duration_changed)
        duration_control_layout.addWidget(self.duration_spin)
        self.start_button.clicked.connect(lambda: self.set_role("start"))
        self.end_button.clicked.connect(lambda: self.set_role("end"))
        self.segment_header.addStretch()
        segment_meta = QFrame()
        self.segment_meta = segment_meta
        segment_meta.setObjectName("segmentMetaBar")
        segment_meta_layout = QHBoxLayout(segment_meta)
        segment_meta_layout.setContentsMargins(3, 1, 3, 1)
        segment_meta_layout.setSpacing(3)
        self.frame_number.hide()
        segment_meta_layout.addWidget(self.start_button)
        segment_meta_layout.addWidget(self.end_button)
        duration_label = QLabel("DURATION")
        duration_label.setObjectName("timelineControlLabel")
        duration_label.hide()  # Units in the spinner make this label redundant.
        segment_meta_layout.addWidget(self.duration_control)
        self.sequence_controls.addStretch(1)
        self.sequence_controls.addWidget(segment_meta)
        self.segment_ai_marquee = AIWorkingMarquee(segment_panel)
        segment_layout.addLayout(self.segment_header)
        # Match the Unified Prompt: full-width animated bar directly ABOVE
        # the text editor, only visible while an AI refinement is running.
        segment_layout.addWidget(self.segment_ai_marquee)
        self.segment_prompt = PromptTextEdit()
        self.segment_prompt.setObjectName("promptEditor")
        self.segment_prompt.setAcceptRichText(False)
        self.segment_prompt.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.segment_prompt.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
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
        # The global prompt is stored here for compatibility with generation/export;
        # it is edited through the same visible text box using the scope selector.
        self.global_prompt = PromptTextEdit(self)
        self.global_prompt.hide()
        self.global_prompt.textChanged.connect(self.global_prompt_changed)
        self.applied_label = QLabel(self)
        self.applied_label.hide()
        self.global_count = QLabel(self)
        self.global_count.hide()
        self.unified_panel = QFrame()
        self.unified_panel.setObjectName("promptPanel")
        unified_layout = QVBoxLayout(self.unified_panel)
        unified_layout.setContentsMargins(9, 6, 9, 5)
        self.unified_prompt = PromptTextEdit()
        self.unified_prompt.highlight_timeline_timecodes = True
        self.unified_prompt.setObjectName("promptEditor")
        self.unified_prompt.setAcceptRichText(False)
        self.unified_prompt.setPlaceholderText("Generate the unified prompt from your segments, or write and refine your own…")
        self.unified_prompt.textChanged.connect(self.minimax_editor_changed)
        self.unified_prompt.textChanged.connect(self.highlight_selected_prompt_timecode)
        for editor in (self.intent,self.segment_prompt,self.unified_prompt):
            editor.setMinimumHeight(32)
            editor.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Ignored)
        self.minimax_panel = MiniMaxPromptPanel(self)
        self.unified_count = QLabel("0 chars")
        self.unified_count.setObjectName("muted")
        # Heading, character count, cache state and copy share ONE title bar.
        self.minimax_panel.header.insertWidget(1, self.unified_count)
        self.sync_timeline_button = QPushButton("")
        self.sync_timeline_button.setObjectName("addTile")
        self.sync_timeline_button.setToolTip(
            "Rewrite timeline from combined prompt: match segment wording and "
            "timing to the production brief, then review the timeline changes.")
        self.sync_timeline_button.setAccessibleName("Rewrite timeline from combined prompt")
        self.sync_timeline_button.clicked.connect(self.sync_timeline_from_prompt)
        self.sync_timeline_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.combined_generate_button = QPushButton("Generate")
        self.combined_generate_button.setObjectName("promptAction")
        self.combined_generate_button.setAccessibleName("Generate combined prompt")
        self.combined_generate_button.setToolTip(
            "Generate from all timeline segments and final prompt notes.")
        self.combined_generate_button.clicked.connect(self.generate_combined_prompt)
        self.combined_refine_button = QPushButton("Refine")
        self.combined_refine_button.setObjectName("promptAction")
        self.combined_refine_button.setAccessibleName("Refine combined prompt")
        self.combined_refine_button.setToolTip(
            "Refine the combined prompt with the full sequence and final prompt notes.")
        self.combined_refine_button.clicked.connect(self.refine_combined_prompt)
        self.sync_combined_button = QPushButton("")
        self.sync_combined_button.setObjectName("refineButton")
        self.sync_combined_button.setAccessibleName("Sync combined prompt from all timeline segments")
        self.sync_combined_button.setToolTip(
            "↑ ALL Timeline Segments → Combined Prompt: rebuild the combined "
            "prose from the entire ordered sequence, leaving segments unchanged.")
        self.sync_combined_button.clicked.connect(self.sync_combined_from_segments)
        self.combined_quick_fix_button = self.make_quick_fix_button("Unified prompt quick fixes", "unified")
        self.minimax_panel.header.addWidget(self.combined_quick_fix_button)
        self.minimax_panel.header.addWidget(self.combined_generate_button)
        self.minimax_panel.header.addWidget(self.combined_refine_button)
        self.add_tile_layout.insertWidget(
            2, self.sync_timeline_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.add_tile_layout.insertWidget(
            3, self.retime_all_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.minimax_panel.header.addWidget(self.sync_combined_button)
        self.minimax_panel.header.addWidget(self.minimax_panel.copy_button)
        unified_layout.addWidget(self.minimax_panel)
        unified_layout.addWidget(self.unified_prompt, 1)
        self.prompt_splitter = QSplitter(Qt.Orientation.Vertical)
        self.prompt_splitter.setObjectName("promptEditorSplitter")
        self.prompt_splitter.addWidget(segment_panel)
        self.prompt_splitter.addWidget(self.unified_panel)
        self.prompt_splitter.setStretchFactor(0, 1)
        self.prompt_splitter.setStretchFactor(1, 1)
        self.prompt_splitter.setChildrenCollapsible(False)
        self.prompt_splitter.splitterMoved.connect(self.prompt_splitter_moved)
        self.intent_prompt_splitter = QSplitter(Qt.Orientation.Vertical)
        self.intent_prompt_splitter.setObjectName("intentPromptSplitter")
        self.intent_prompt_splitter.addWidget(director_panel)
        self.intent_prompt_splitter.addWidget(self.prompt_splitter)
        self.intent_prompt_splitter.setCollapsible(0, True)
        self.intent_prompt_splitter.setCollapsible(1, False)
        self.intent_prompt_splitter.setStretchFactor(0, 0)
        self.intent_prompt_splitter.setStretchFactor(1, 1)
        self.intent_prompt_splitter.handle(1).setToolTip("Drag to resize Director's Intent; drag up to collapse, down to reopen")
        self.intent_prompt_splitter.handle(1).setAccessibleName("Resize or collapse Director's Intent")
        self.intent_prompt_splitter.splitterMoved.connect(self.director_intent_splitter_moved)
        outer.addWidget(self.intent_prompt_splitter, 1)
        self.restore_director_intent_splitter()
        QTimer.singleShot(0, self.restore_director_intent_splitter)
        self.shot_sequence_panel = ShotSequencePanel(self, ProjectPreviewPanel, root)
        outer.addWidget(self.shot_sequence_panel, 1)
        self.shot_sequence_panel.hide()
        self.editor_root=root
        root.setMinimumWidth(320)
        root.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Ignored)
        outer.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        root.installEventFilter(self)
        self.setCentralWidget(root)
        self.setStatusBar(QStatusBar())
        self.statusBar().addPermanentWidget(self.ai_timeline_status)
        self.apply_project_type_ui()
        self.update_summary()

    def _build_local_generation_ui(self):
        import threading
        self._local_video_cancel = threading.Event()
        self._local_video_busy = False
        self.image_generation_dock = QDockWidget("IMAGE GENERATION", self)
        self.image_generation_dock.setObjectName("imageGenerationDock")
        self.image_generation_panel = ImageGenerationPanel()
        self._local_artwork_cancel = {}
        self.image_generation_panel.cancel_requested.connect(self.cancel_local_artwork)
        self.image_generation_dock.setWidget(self.image_generation_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.image_generation_dock)
        self.image_generation_dock.hide()
        panel = self.project_preview_panel
        row = QHBoxLayout()
        self.local_video_generate = QPushButton("✦ Generate locally")
        self.local_video_generate.setToolTip("Render unified output with the Reference panel and local video settings.")
        self.local_video_generate.clicked.connect(self.generate_local_video)
        self.local_video_cancel_button = QPushButton("Cancel generation")
        self.local_video_cancel_button.clicked.connect(self._local_video_cancel.set)
        self.local_video_cancel_button.hide()
        row.addWidget(self.local_video_generate); row.addWidget(self.local_video_cancel_button)
        panel.layout().insertLayout(1, row)
        self.local_video_pill = ProgressPill(panel, anchor=lambda: panel.empty if panel.video.isHidden() else panel.video)
        self.local_video_log = GenerationLog()
        panel.layout().addWidget(self.local_video_log)

    def generate_local_video(self):
        if self._local_video_busy: return
        profile = load_profile(self.settings)
        try: validate_profile(profile, "video")
        except ValueError as error:
            QMessageBox.information(self, "Local video setup", str(error))
            self.open_settings(); return
        prompt = prompt_content(self.minimax_prompt_text, preserve_spacing=True)
        if not prompt:
            self.statusBar().showMessage("Write or generate the unified production prompt first.", 10000)
            return
        extra_references = copy.deepcopy([r for r in reference_slots(self.minimax_reference_images) if r])
        request = dict(kind="video", profile=profile, prompt=prompt,
            duration=profile["video_duration"], width=profile["video_width"], height=profile["video_height"], steps=profile["video_steps"],
            seed=profile["video_seed"])
        workspace = (self.workspace_revision, self.current_project_id, self.project_type)
        previous_preview = getattr(self.project_preview_panel, "pending_preview_path", "")
        self.local_video_log.clear()
        self.project_preview_panel._generation_autoplay = True
        self._local_video_busy = True; self._local_video_cancel.clear()
        self.local_video_generate.setEnabled(False); self.local_video_cancel_button.show()
        self.project_preview_dock.show()
        self.statusBar().showMessage("Local video generation started")
        def current(): return workspace == (self.workspace_revision, self.current_project_id, self.project_type)
        def event(value):
            if not current(): return
            self.local_video_log.receive_event(value)
            if value.get("kind") == "progress": self.local_video_pill.update_event(value)
            elif value.get("kind") == "preview":
                panel = self.project_preview_panel
                if value.get("media") == "image":
                    image = QImageReader(value["path"]).read()
                    if image.isNull(): return
                    panel.release_memory_video()
                    panel._source_generation += 1
                    panel._memory_preview = False; panel._initial_media_pending = False
                    panel.player.stop(); panel.player.setSource(QUrl())
                    panel.pending_preview_path = ""
                    panel.title.setText(f"Local video · live noisy latent · step {value.get('step', 0)} · approximate colors")
                    panel.empty.hide(); panel.video.show()
                    panel.export.setEnabled(False); panel.fullscreen.setEnabled(False)
                    panel.video.videoSink().setVideoFrame(QVideoFrame(image))
                    self.local_video_pill.reposition(); self.local_video_pill.raise_()
                    return
                panel.release_memory_video()
                panel._memory_preview = False
                panel._source_generation += 1
                panel.pending_preview_path = value["path"]
                panel._generation_preview_active = True
                panel._initial_media_pending = True
                panel._initial_media_position = 0
                panel._initial_media_autoplay = panel._generation_autoplay
                panel.title.setText("Local video · live noisy latents · approximate colors" if value.get('preview_mode')=='noisy'
                    else "Local video · denoised draft estimate")
                panel.empty.hide(); panel.video.show()
                panel.export.setEnabled(False)
                panel.player.setSource(QUrl.fromLocalFile(value["path"]))
                panel.player.setLoops(QMediaPlayer.Loops.Infinite)
        def operation():
            references = []
            for reference in extra_references:
                if reference.get("audio_only"):
                    reference["kind"] = "audio"
                    reference["path"] = reference.get("audio_path") or reference.get("path")
                references.append(reference)
            return run_request({**request, "references": references}, cancel=self._local_video_cancel)
        def finish():
            self._local_video_busy = False; self.local_video_generate.setEnabled(True)
            self.local_video_cancel_button.hide(); self.local_video_pill.hide()
            if current(): self.project_preview_panel.player.setLoops(QMediaPlayer.Loops.Once)
        def ready(result):
            finish()
            if not current():
                self.statusBar().showMessage("Local video retained in the generation folder; the active project changed.", 8000); return
            self.project_preview_panel.set_project(self.current_project_name or "Generated video", result["path"])
            self._local_result_video = {"scope": workspace, "path": result["path"]}
            if self.current_project_id: self.attach_local_generated_video(result["path"], workspace)
            self.statusBar().showMessage("Local video ready · " + result["path"], 12000)
        def failed(message):
            finish()
            if current():
                self.local_video_log.receive_event({"kind": "error", "message": str(message)})
                self.project_preview_panel.set_project(self.current_project_name or "Video Preview", previous_preview)
                self.statusBar().showMessage(str(message), 15000)
        self.start_disk_job(operation, (), ready, failed, progress=event)

    def cancel_local_artwork(self):
        for event in self._local_artwork_cancel.values(): event.set()

    def local_image_progress(self, event, workspace):
        if workspace != (self.workspace_revision, self.current_project_id, self.project_type): return
        self.image_generation_dock.show()
        self.image_generation_panel.receive_event(event)

    def _build_effects_dock(self):
        self.effects_dock = QDockWidget("Effects Library", self)
        self.effects_dock.setObjectName("effectsLibraryDock")
        self.effects_dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        self.effects_panel = EffectsPanel(self)
        self.effects_panel.apply_requested.connect(self.apply_library_effect)
        self.project_preview_panel.player.playbackStateChanged.connect(
            lambda state: self.effects_panel.stop_preview()
            if state == QMediaPlayer.PlaybackState.PlayingState else None)
        self.effects_dock.setWidget(self.effects_panel)
        self.effects_dock.setMinimumSize(280, 220)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.effects_dock)
        self.effects_dock.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.effects_dock.customContextMenuRequested.connect(self.effects_dock_menu)
        self.effects_dock.dockLocationChanged.connect(lambda *_: self.save_window_panel_state())
        self.effects_dock.topLevelChanged.connect(lambda *_: self.save_window_panel_state())
        self.effects_dock.visibilityChanged.connect(lambda *_: self.save_window_panel_state())
        self.effects_dock.hide()

    def effects_dock_menu(self, point):
        menu = QMenu(self.effects_dock)
        for label, area in (("Dock left", Qt.DockWidgetArea.LeftDockWidgetArea),
                            ("Dock right", Qt.DockWidgetArea.RightDockWidgetArea),
                            ("Dock bottom", Qt.DockWidgetArea.BottomDockWidgetArea)):
            menu.addAction(label, lambda a=area: self.dock_effects(a))
        menu.addAction("Float panel", lambda: self.effects_dock.setFloating(True))
        menu.addSeparator()
        for label, orientation, value in (("Width", Qt.Orientation.Horizontal, self.effects_dock.width()),
                                           ("Height", Qt.Orientation.Vertical, self.effects_dock.height())):
            action = QWidgetAction(menu)
            row = QWidget(); layout = QHBoxLayout(row); layout.addWidget(QLabel(label))
            spin = QSpinBox(); spin.setRange(220, 2400); spin.setValue(value)
            spin.valueChanged.connect(lambda size, o=orientation: self.size_effects_panel(size, o))
            layout.addWidget(spin); action.setDefaultWidget(row); menu.addAction(action)
        menu.exec(self.effects_dock.mapToGlobal(point))

    def dock_effects(self, area):
        self.effects_dock.setFloating(False)
        self.addDockWidget(area, self.effects_dock)

    def size_effects_panel(self, size, orientation):
        if self.effects_dock.isFloating():
            self.effects_dock.resize(size if orientation == Qt.Orientation.Horizontal else self.effects_dock.width(),
                                     size if orientation == Qt.Orientation.Vertical else self.effects_dock.height())
        else:
            self.resizeDocks([self.effects_dock], [size], orientation)
        self.save_window_panel_state()

    def _build_project_preview_dock(self) -> None:
        self.project_preview_dock = QDockWidget("PROJECT VIDEO PREVIEW", self)
        self.project_preview_dock.setObjectName("projectPreviewDock")
        self.project_preview_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.project_preview_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.project_preview_dock.setMinimumWidth(200)
        self.project_preview_panel = ProjectPreviewPanel(self.project_preview_dock)
        self.project_preview_panel.video_dropped.connect(self.attach_project_video)
        self.project_preview_panel.choose_requested.connect(self.choose_project_video)
        self.project_preview_panel.export_requested.connect(self.export_project_video)
        self.project_preview_dock.setWidget(self.project_preview_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.project_preview_dock)
        self.project_preview_dock.dockLocationChanged.connect(lambda *_: self.save_window_panel_state())
        self.project_preview_dock.topLevelChanged.connect(lambda *_: self.save_window_panel_state())
        self.project_preview_dock.visibilityChanged.connect(lambda *_: self.save_window_panel_state())
        self.project_preview_dock.visibilityChanged.connect(self.project_preview_panel.set_preview_visible)
        self.project_preview_dock.hide()

    def _build_project_files_dock(self) -> None:
        self.project_files_dock = QDockWidget("PROJECT FILES", self)
        self.project_files_dock.setObjectName("projectFilesDock")
        self.project_files_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.TopDockWidgetArea | Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.project_files_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.project_files_panel = ProjectFilesPanel(self.project_files_dock)
        self.project_files_panel.add_requested.connect(self.choose_project_workflow)
        self.project_files_panel.file_dropped.connect(self.attach_project_workflow)
        self.project_files_panel.export_requested.connect(self.export_project_workflow)
        self.project_files_panel.remove_requested.connect(self.remove_project_workflow)
        self.project_files_dock.setWidget(self.project_files_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.project_files_dock)
        self.project_files_dock.dockLocationChanged.connect(lambda *_: self.save_window_panel_state())
        self.project_files_dock.topLevelChanged.connect(lambda *_: self.save_window_panel_state())
        self.project_files_dock.visibilityChanged.connect(lambda *_: self.save_window_panel_state())
        self.project_files_dock.hide()

    def _build_project_properties_dock(self) -> None:
        self.project_properties_dock = QDockWidget("PROJECT PROPERTIES", self)
        self.project_properties_dock.setObjectName("projectPropertiesDock")
        self.project_properties_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.project_properties_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.project_properties_panel = ProjectPropertiesPanel(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.project_properties_panel)
        self.project_properties_dock.setWidget(scroll)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.project_properties_dock)
        self.project_properties_dock.dockLocationChanged.connect(lambda *_: self.save_window_panel_state())
        self.project_properties_dock.topLevelChanged.connect(lambda *_: self.save_window_panel_state())
        self.project_properties_dock.visibilityChanged.connect(lambda *_: self.save_window_panel_state())
        self.project_properties_dock.hide()

    def _build_project_dock(self) -> None:
        self.project_dock = ProjectLibraryDock("PROJECT LIBRARY", self)
        self.project_dock.user_resized.connect(self.remember_project_panel_width)
        self.project_dock.setObjectName("projectDock")
        self.project_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self.project_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        self.project_dock.setMinimumWidth(180)

        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
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
        self.project_filter_box = QFrame()
        self.project_filter_box.setObjectName("projectFilters")
        self.project_filter_box.setMinimumWidth(0)
        self.project_filter_box.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.project_filter_layout = QHBoxLayout(self.project_filter_box)
        self.project_filter_layout.setContentsMargins(3, 3, 3, 3)
        self.project_filter_layout.setSpacing(3)
        self.project_filter_buttons: dict[str, QPushButton] = {}
        self.rebuild_project_filter_buttons()
        layout.addWidget(self.project_filter_box)
        layout.addWidget(self.project_search)

        self.project_list = ProjectListWidget()
        self.project_list.setObjectName("projectList")
        self.project_list.setMouseTracking(True)
        self.project_list.setItemDelegate(ProjectTileDelegate(self.project_list))
        self.project_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.project_list.setFlow(QListWidget.Flow.LeftToRight)
        self.project_list.setWrapping(True)
        self.project_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.project_list.setMovement(QListWidget.Movement.Static)
        self.project_list.setSpacing(4)
        self.project_list.itemClicked.connect(self.activate_clicked_project)
        self.project_list.itemDoubleClicked.connect(self.activate_double_clicked_project)
        self.project_list.drag_started.connect(self.activate_custom_sort_for_drag)
        self.project_list.order_changed.connect(self.save_custom_project_order)
        self.project_list.files_dropped.connect(self.project_files_dropped)
        self.project_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.project_list.customContextMenuRequested.connect(self.project_library_menu)
        self.project_list.currentItemChanged.connect(lambda *_: self.sync_project_properties())
        layout.addWidget(self.project_list, 1)

        self.update_project_icon_controls()
        self.project_dock.setWidget(panel)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.project_dock)
        self.project_dock.visibilityChanged.connect(self.project_dock_visibility_changed)
        self.project_dock.dockLocationChanged.connect(lambda *_: self.save_window_panel_state())
        self.project_dock.topLevelChanged.connect(lambda *_: self.save_window_panel_state())
        self.project_dock.hide()
        self.refresh_project_library()

    def project_status_tags(self) -> list[dict[str, str]]:
        return load_project_tags(self.settings.value("project_status_tags", self.settings.value("project_tags", "")))

    def project_other_tags(self) -> list[dict[str, str]]:
        return load_other_tags(self.settings.value("project_other_tags", ""))

    def saved_project_filters(self) -> set[str]:
        try:
            value = json.loads(str(self.settings.value("project_tag_filters", "")))
            if isinstance(value, list):
                return {str(item) for item in value}
        except (TypeError, ValueError):
            pass
        return {"__untagged__", *(f"status:{tag['name']}" for tag in self.project_status_tags())}

    def rebuild_project_filter_buttons(self) -> None:
        while self.project_filter_layout.count():
            item = self.project_filter_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        selected = self.saved_project_filters()
        self.project_filter_buttons = {}
        status_names = {tag["name"] for tag in self.project_status_tags()}
        selected |= {f"status:{name}" for name in status_names if name in selected}
        definitions = [
            ("__untagged__", "No status", "#56616a"),
            *((f"status:{tag['name']}", tag["name"], tag["color"]) for tag in self.project_status_tags()),
            *((f"tag:{tag['name']}", tag["name"], tag["color"]) for tag in self.project_other_tags()),
            ("__archive__", "Archive", ARCHIVE_COLOR),
        ]
        for key, label, color in definitions:
            button = QPushButton(label)
            button.setObjectName("projectFilterButton")
            button.setCheckable(True)
            button.setChecked(key in selected)
            button.setStyleSheet(f"QPushButton:checked{{background:{color};border-color:{color};color:white}}")
            button.toggled.connect(self.project_filter_changed)
            self.project_filter_layout.addWidget(button)
            self.project_filter_buttons[key] = button
        self.project_filter_layout.addStretch(1)

    def project_filter_changed(self, *_args) -> None:
        selected = [key for key, button in self.project_filter_buttons.items() if button.isChecked()]
        self.settings.setValue("project_tag_filters", json.dumps(selected))
        self.queue_settings_sync()
        if hasattr(self, "project_list"):
            self.filter_projects(self.project_search.text())

    def project_dock_visibility_changed(self, visible: bool) -> None:
        if visible:
            QTimer.singleShot(0, self.restore_project_panel_width)

    def remember_project_panel_width(self, width: int, height: int) -> None:
        if self._restoring_layout or self._loading:
            return
        bottom = self.dockWidgetArea(self.project_dock) in {Qt.DockWidgetArea.TopDockWidgetArea, Qt.DockWidgetArea.BottomDockWidgetArea}
        if bottom and not self.project_dock.isFloating():
            self.project_panel_height = max(160, height)
            self.settings.setValue("project_panel_height", self.project_panel_height)
        else:
            self.project_panel_width = max(180, width)
            self.settings.setValue("project_panel_width", self.project_panel_width)
        self.queue_settings_sync()

    def queue_project_panel_width_restore(self) -> None:
        if self._restoring_layout or self.project_dock.isHidden():
            return
        QTimer.singleShot(0, self.restore_project_panel_width)

    def restore_project_panel_width(self) -> None:
        """Preserve the user's extent for side or bottom dock layouts."""
        if self.project_dock.isFloating():
            return
        area = self.dockWidgetArea(self.project_dock)
        if area in {Qt.DockWidgetArea.TopDockWidgetArea, Qt.DockWidgetArea.BottomDockWidgetArea}:
            tab_height = 0
            if self.tabifiedDockWidgets(self.project_dock):
                title = self.project_dock.windowTitle().replace('&', '')
                for bar in self.findChildren(QTabBar):
                    if any(bar.tabText(index).replace('&', '') == title for index in range(bar.count())):
                        tab_height = bar.height()
                        break
            self.resizeDocks([self.project_dock], [self.project_panel_height + tab_height], Qt.Orientation.Vertical)
        else:
            self.set_project_panel_width(self.project_panel_width, persist=False)

    def restore_startup_workspace(self) -> None:
        """Settle saved geometry before loading the previous project's media timeline."""
        self.restore_project_panel_width()
        self.restore_timeline_panel_height()
        self.finish_layout_restore()
        splash = getattr(QApplication.instance(), '_director_splash', None)
        if splash:
            splash.set_status('Checking project thumbnails…')
            QApplication.processEvents()
        if needs_refresh():
            files = list(project_library_path().glob("*.meta.json"))
            if files:
                self.cache_dialog = PreviewCacheDialog(files, self)
                self.cache_dialog.finished.connect(lambda *_: QTimer.singleShot(0, self.restore_last_project))
                self.cache_dialog.finished.connect(lambda *_: self.refresh_project_library())
                if splash:
                    splash.finish(self)
                    QApplication.instance()._director_splash = None
                QTimer.singleShot(0, self.cache_dialog.start)
                return
            mark_ready()
        if splash:
            splash.set_status('Opening the last workspace…')
            QApplication.processEvents()
            splash.finish(self)
            QApplication.instance()._director_splash = None
        QTimer.singleShot(0, self.restore_last_project)
        QTimer.singleShot(600, self.maybe_show_onboarding)

    def finish_layout_restore(self) -> None:
        self._restoring_layout = False

    def set_project_panel_width(self, width: int, persist: bool = True) -> None:
        width = max(180, int(width))
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

    def queue_settings_sync(self) -> None:
        self._settings_sync_timer.start()

    def save_window_panel_state(self) -> None:
        if self._restoring_layout:
            return
        self.settings.setValue("window/state", self.saveState())
        self.queue_settings_sync()

    def _apply_theme(self) -> None:
        scale = max(75, min(200, self.settings.value("ui_text_scale", 100, int))) / 100

        def scaled(size: int) -> int:
            return max(7, round(size * scale))

        def metric(size: int) -> int:
            return max(1, round(size * scale))

        theme = """
        QMainWindow,QWidget{background:#24292c;color:#d9dcde;font:11px Arial} QMainWindow::separator{width:__DOCK_GRIP_WIDTH__px;height:__DOCK_GRIP_WIDTH__px;background:transparent;background-repeat:no-repeat;background-position:center} QMainWindow::separator:vertical{background-image:url("__DOCK_GRIP_IMAGE__")} QMainWindow::separator:horizontal{background-image:url("__DOCK_GRIP_HORIZONTAL_IMAGE__")} QMainWindow::separator:hover{background-color:rgba(88,118,134,35)} QToolBar{background:#1b2023;border:0;border-bottom:1px solid #111517;spacing:3px;padding:5px} QToolBar::separator{background:#394247;width:1px;margin:7px 5px}
        QToolButton,QPushButton,QComboBox,QSpinBox,QDoubleSpinBox,QLineEdit{background:#303436;border:1px solid #101213;border-radius:3px;padding:3px 7px;min-height:19px}
        #mainToolbar QToolButton{background:transparent;border:1px solid transparent;border-radius:4px;padding:5px 9px;color:#c5cdd1} #mainToolbar QToolButton:hover{background:#2b3438;border-color:#3a464c;color:#f3f7f9} #mainToolbar QToolButton:pressed{background:#17232a;border-color:#477d99;color:#bde6fb} #toolbarButton{background:#23343d;border:1px solid #385667;border-radius:5px;color:#c4e8fb;font-weight:bold}
        #minimaxPromptToolbar{background:#1b2023;border:1px solid #354047;border-radius:6px} #minimaxCacheState{background:#283740;color:#afc0ca;border:0;border-radius:8px;padding:2px 7px;font-size:9px} #minimaxCacheState[active="true"]{background:#102f4b;color:#d8f2ff} #minimaxCacheState[cached="true"]{background:#244d37;color:#c9f4d6} #minimaxInstructions{background:#1b2023;border:1px solid #37464d;border-radius:4px;color:#d8e1e5;padding:7px} #minimaxMessagePanel{background:#20313a;border:1px solid #49758a;border-radius:6px} #minimaxMessagePanel[level="success"]{background:#20392c;border-color:#4b8962} #minimaxMessagePanel[level="warning"]{background:#3a321f;border-color:#8a7340} #minimaxMessagePanel[level="timeline"]{background:#3a321f;border-color:#b4974a} #minimaxMessagePanel[level="error"]{background:#3a2325;border-color:#94555a} #minimaxMessageBanner{background:transparent;color:#ccecf8;border:0;padding:1px} #minimaxMessagePanel[level="success"] #minimaxMessageBanner{color:#cef2d9} #minimaxMessagePanel[level="warning"] #minimaxMessageBanner{color:#f2dfb0} #minimaxMessagePanel[level="timeline"] #minimaxMessageBanner{color:#f2dfb0} #minimaxMessagePanel[level="error"] #minimaxMessageBanner{color:#f2c5c8} #minimaxRetryButton{background:#5b451b;color:#ffe7a1;border:1px solid #c79a39;border-radius:4px;font-weight:bold;padding:4px 10px} #minimaxRetryButton:hover{background:#755b25;border-color:#e2bc5a;color:#fff5cf} #minimaxBusyCard{background:#17262d;border:1px solid #65a7c7;border-radius:8px} #minimaxBusyStatus{background:transparent;color:#d9f2ff;font-weight:bold;padding:2px}
        QToolButton:hover,QPushButton:hover{background:#41474a} QToolButton:pressed,QPushButton:pressed{background:#202729;border-color:#79a8c5} QLineEdit{background:#1e2122}
        QSpinBox,QDoubleSpinBox{padding-right:__SPIN_PAD__px} QSpinBox::up-button,QDoubleSpinBox::up-button{subcontrol-origin:border;subcontrol-position:top right;width:__SPIN_BUTTON__px;background:#3b4347;border:0;border-left:1px solid #171a1c;border-bottom:1px solid #202527;border-top-right-radius:3px} QSpinBox::down-button,QDoubleSpinBox::down-button{subcontrol-origin:border;subcontrol-position:bottom right;width:__SPIN_BUTTON__px;background:#343b3f;border:0;border-left:1px solid #171a1c;border-top:1px solid #202527;border-bottom-right-radius:3px}
        QSpinBox::up-button:hover,QDoubleSpinBox::up-button:hover,QSpinBox::down-button:hover,QDoubleSpinBox::down-button:hover{background:#506471} QSpinBox::up-button:pressed,QDoubleSpinBox::up-button:pressed,QSpinBox::down-button:pressed,QDoubleSpinBox::down-button:pressed{background:#274e66} QSpinBox::up-arrow,QDoubleSpinBox::up-arrow,QSpinBox::down-arrow,QDoubleSpinBox::down-arrow{width:__ARROW_SIZE__px;height:__ARROW_SIZE__px}
        QComboBox{padding-right:__COMBO_PAD__px} QComboBox::drop-down{subcontrol-origin:padding;subcontrol-position:top right;width:__COMBO_BUTTON__px;background:#394145;border:0;border-left:1px solid #171a1c;border-top-right-radius:3px;border-bottom-right-radius:3px} QComboBox::drop-down:hover{background:#506471} QComboBox::drop-down:pressed{background:#274e66} QComboBox::down-arrow{width:__ARROW_SIZE__px;height:__ARROW_SIZE__px} QComboBox QAbstractItemView{background:#202527;color:#e1e5e7;border:1px solid #52616a;outline:0;padding:4px;selection-background-color:#3b6f9c;selection-color:#fff}
        QSlider::groove:horizontal{height:__SLIDER_GROOVE__px;background:#161b1d;border:1px solid #0d1011;border-radius:__SLIDER_RADIUS__px} QSlider::sub-page:horizontal{background:#367da6;border:1px solid #4b9ac6;border-radius:__SLIDER_RADIUS__px} QSlider::add-page:horizontal{background:#161b1d;border-radius:__SLIDER_RADIUS__px} QSlider::handle:horizontal{width:__SLIDER_HANDLE__px;margin:-__SLIDER_MARGIN__px 0;background:#607883;border:2px solid #8fb9cd;border-radius:__SLIDER_HANDLE_RADIUS__px} QSlider::handle:horizontal:hover{background:#78a8be;border-color:#c5ebff} QSlider::handle:horizontal:pressed{background:#4aa3d2;border-color:#e1f6ff}
        #timelineShell{background:#0d0f10;border:1px solid #323638;border-radius:3px} #mainTrackLabel{background:#191c1d;border-right:1px solid #34383a;font-weight:bold}
        #timelineTitle{background:#19262d;color:#b9def2;border:1px solid #304955;border-radius:4px;padding:4px 8px;font-weight:bold;letter-spacing:1px} #timelineControlLabel{background:transparent;color:#7f9099;border:0;font-size:9px;font-weight:bold;letter-spacing:1px}
        #outputSizeControl{background:#1a2023;border:1px solid #354047;border-radius:5px} #timelineSpin{background:transparent;border:0;border-radius:0;padding-left:9px;padding-right:__TIMELINE_SPIN_PAD__px;color:#e6edf1;font-weight:bold;selection-background-color:#3b6f9c} #timelineSpin:hover{background:#20292d} #timelineSpin:focus{background:#202b30;color:#fff}
        #timelineSpin::up-button{width:__TIMELINE_SPIN_BUTTON__px;background:transparent;border:0;border-radius:2px;subcontrol-origin:border;subcontrol-position:top right} #timelineSpin::down-button{width:__TIMELINE_SPIN_BUTTON__px;background:transparent;border:0;border-radius:2px;subcontrol-origin:border;subcontrol-position:bottom right} #timelineSpin::up-button:hover,#timelineSpin::down-button:hover{background:#344b57} #timelineSpin::up-button:pressed,#timelineSpin::down-button:pressed{background:#1f668b} #timelineSpin::up-arrow{image:url("__SPIN_UP_IMAGE__");width:__TIMELINE_ARROW__px;height:__TIMELINE_ARROW__px} #timelineSpin::down-arrow{image:url("__SPIN_DOWN_IMAGE__");width:__TIMELINE_ARROW__px;height:__TIMELINE_ARROW__px}
        #resolutionSeparator{background:transparent;color:#60717a;border:0;padding:0 3px;font-weight:bold}
        #promptAction,#quickFixAction{background:#303436;color:#dbe7ee;border:1px solid #101213;border-radius:3px;padding:2px 6px;min-height:0;font-weight:bold}
        #promptAction:hover,#quickFixAction:hover{background:#385062;border-color:#668da7;color:#f4fbff}
        #promptAction:pressed,#quickFixAction:pressed{background:#1e3442;color:#b9e9ff}
        #promptAction:disabled,#quickFixAction:disabled{background:#293033;border-color:#22282b;color:#6b7a81}
        #timelineButton{background:transparent;color:#acd8ef;border:1px solid #3f6679;border-radius:5px;padding:4px 10px;font-weight:bold} #timelineButton:hover{background:#243b46;color:#e0f5ff;border-color:#65a7c7} #timelineButton:pressed{background:#172b35;color:#85c9eb;border-color:#347898}
        QListWidget{background:#0d0f10;border:0;padding:0} QListWidget::item{border:1px solid #696b6c;background:#252728;margin:0} QListWidget::item:selected{border:2px solid #f1f1f1;background:#293034}
        #conditioningGuide{background:#141b1f;border:1px solid #354047;border-radius:4px}
        #conditioningGuide QWidget{background:transparent}
        #conditioningHeading{color:#8ebbd1;font-size:11px;font-weight:bold}
        #conditioningGuide #conditioningTile{background:#17191a;border:1px solid #354047;border-radius:4px}
        #conditioningGuide #conditioningPreview{background:#17191a}
        #conditioningGuide #conditioningFooter{background:#252728;border:0}
        #conditioningLabel{color:#79b8d7;font-size:10px}
        #conditioningValue{color:#a2a7a9;font-size:10px}
        #conditioningGuide #conditioningTitle{background:#242627;color:#d7e9f3;font-size:11px;font-weight:bold;padding:3px 5px}
        #conditioningFix{color:#f2cd86;font-size:10px}
        #catalogLightbox{background:#0b1014} #catalogLightbox QLabel{background:transparent}
        #referenceImageSlot{background:#191d1f;border:1px solid #323638;border-radius:4px}
        #referenceImagePreview{background:#10171c;border:1px solid #40515b;border-radius:4px}
        #referenceImagePreview:hover{background:#20343f;border-color:#68b9ee}
        #referenceImageSlot[dropActive="true"]{border:3px solid #68b9ee;background:#13232c}
        #catalogTiles::item{background:#22282b;border:1px solid #3b464b;border-radius:6px}
        #catalogTiles::item:hover{background:#2d3d47;border-color:#68b9ee}
        #catalogTiles::item:selected{background:#294356;border:2px solid #69a5d0}
        #mediaCatalogPanel{border:1px solid transparent} #mediaCatalogPanel[dropActive="true"]{border:3px solid #68b9ee;background:#13232c}
        #timeline{padding:3px;background:#0d0f10;outline:0} #timeline::item,#timeline::item:selected,#timeline::item:focus{background:#0d0f10;border:0;outline:0} #timeline[dropActive="true"]{border:3px solid #68b9ee;background:#13232c} #timeline[dropActive="false"]{border:1px solid #323638}
        #shotTimeline[dropActive="true"]{border:3px solid #68b9ee;background:#13232c} #shotTimeline[dropActive="false"]{border:1px solid #323638}
        #segmentCard{background:transparent;border:1px solid transparent} #segmentCard[selected="true"]{background:#17262e;border:1px solid #73a9c4} #segmentCardBody{background:#252728;border:0} #mediaBadge{background:#e5e5e5;color:#262626;font-weight:bold;padding:2px} #roleBadge{background:#36393a;color:#eee;padding:2px}
        #tileDelete{padding:0;min-height:0;max-height:20px;background:#454849;color:#ddd;border:0} #tileDelete:hover{background:#a94444;color:#fff;border:1px solid #e07878} #tileTitle{background:#242627;padding:3px;font-size:9px} #tileStartTime{color:#79b8d7;font-size:8px;font-weight:bold} #tileDuration{color:#a2a7a9;font-size:8px}
        #resizeHandle{background:transparent;border:0} #resizeHandle:hover{background:rgba(88,118,134,35);border:0}
        #timelineHeightHandle,#conditioningHeightHandle{background:transparent;border:0}
        #timelineHeightHandle:hover,#conditioningHeightHandle:hover{background:rgba(88,118,134,35);border:0}
        #promptSplitter::handle{background:transparent;border:0} #promptSplitter::handle:hover{background:rgba(88,118,134,35);border:0}
        #segmentPreview{background:#17191a;border-top:1px solid #34383a}
        #addTileBox{border:0;border-left:1px solid #35424b;background:#141b20} #addTile,#addTextTile{min-height:0;border:1px solid #365366;border-radius:5px;background:#1a2b35;color:#c1e5f7;font-size:12px;font-weight:bold;text-align:center;padding:0} #addTile:hover,#addTextTile:hover{background:#254357;border-color:#71a7c4;color:#fff} #sequenceBar{background:#182935;border:0;border-radius:4px;padding:3px 7px;color:#bcd6e6;font-weight:bold}
        #directorPanel{background:#1d2326;border:1px solid #354047;border-radius:6px} #panelTitle{background:transparent;color:#b8d9e9;border:0;font-size:10px;font-weight:bold;letter-spacing:1px} #groupLabel{background:transparent;color:#71838c;border:0;font-size:8px;font-weight:bold;letter-spacing:1px;padding-right:4px}
        #sectionLabel{color:#8ebbd1;font-size:8px;font-weight:bold;letter-spacing:1px} #muted{color:#879095;font-size:9px} #promptPanel{background:#202527;border:1px solid #374044;border-radius:6px} #segmentMetaBar{background:#1a2023;border:0;border-radius:4px} #refineButton{background:#252d31;border:1px solid #45545b;color:#c9dce5;padding-left:9px;padding-right:9px} #refineButton:hover{background:#31414a;border-color:#6590a7;color:#f2fbff} #refineButton:pressed{background:#1d2b32;border-color:#7ca9bf} #refineButton:disabled{background:#202527;border-color:#31393d;color:#606a6f}
        QTextEdit{background:#252728;border:0;color:#e1e4e5;font:11px 'Courier New';padding:4px} #promptEditor{background:#202527;border:0;color:#e1e4e5;padding:7px} #magicButton{background:#3b78a5;border:1px solid #5b9bc6;border-radius:5px;color:#f4fbff;font-weight:bold;padding-left:12px;padding-right:12px} #magicButton:hover{background:#4b8dbd;border-color:#8bc6ea} #magicButton:pressed{background:#285b7c}
        #audioToggle,#qualityToggle,#frameToggle{background:transparent;border:1px solid #455057;color:#b8c0c4} #audioToggle:hover,#qualityToggle:hover,#frameToggle:hover{background:#293236;border-color:#65747c;color:#eef3f5} #audioToggle:checked,#qualityToggle:checked,#frameToggle:checked{background:#244d37;border-color:#4c9b6a;color:#c9f4d6} #copyButton{min-height:0;padding:1px 4px;margin:0;border:0;background:transparent;color:#aeb5b8} #copyButton:hover{background:#303a3f;color:#e5f4fc;border:0} #copyButton:pressed{background:#1b2429;color:#8fd3f7;border:0} QStatusBar{background:#171c1e;color:#7f898d;border-top:1px solid #30383c}
        QDockWidget{background:#191d1f;color:#d9dcde;font-weight:bold} QDockWidget::title{background:#1b2022;border-bottom:1px solid #0e1011;padding:8px;text-align:left}
        #projectLibraryTitle,#magicOverlayTitle{font-size:15px;font-weight:bold;color:#f0f2f3} #libraryControls{background:#1c2225;border:1px solid #343e43;border-radius:5px} #projectSearch{background:#171c1e;border:1px solid #343d41;border-radius:5px;padding-left:10px} #projectSearch:focus{border-color:#4d829d;background:#1b2225} #projectList{background:#15191b;border:1px solid #30383c;border-radius:5px;padding:10px} #projectList[dropActive="true"]{border:3px solid #68b9ee;background:#13232c}
        #projectList::item{background:transparent;border:1px solid #363f43;border-radius:6px;margin:4px;padding:7px;color:#dce0e2} #projectList::item:hover{border-color:#6488a1} #projectList::item:selected{border:2px solid #69a5d0}
        #projectFilters{background:#171c1e;border:1px solid #30383c;border-radius:5px} #projectFilterButton{min-height:17px;padding:2px 6px;background:#23292c;border-color:#394348;color:#aeb8bd} #projectFilterButton:hover{background:#303a3f;color:#fff} #projectNotesList{background:#171b1d;border:1px solid #3a4449;border-radius:4px;padding:3px} #projectNotesList::item{background:transparent;border:0;margin:2px;padding:0} #projectNoteRow{background:#22282b;border:1px solid #394348;border-radius:4px} #projectNoteRow:hover{border-color:#5e8295;background:#283136} #projectNoteDate{color:#86a9ba;font-size:9px} #projectNoteDelete{min-height:0;padding:0;background:transparent;border:0;color:#bca6a6;font-size:15px} #projectNoteDelete:hover{background:#713d3d;color:white}
        #projectPreviewPanel{border:1px solid transparent;background:#191d1f} #projectPreviewPanel[dropActive="true"]{border:3px solid #68b9ee;background:#13232c} #previewTitle{background:#1a2023;border:1px solid #354047;border-radius:4px;padding:5px;color:#d7ebf5;font-weight:bold} #previewEmpty{background:#101314;border:1px dashed #4a565c;border-radius:4px;padding:24px;color:#87959c} #previewSectionTitle{color:#8ebbd1;font-size:8px;font-weight:bold;letter-spacing:1px} #projectWorkflowList{background:#171b1d;border:1px solid #354047;border-radius:4px;padding:3px} #projectWorkflowList[dropActive="true"]{border:3px solid #68b9ee;background:#13232c} #projectWorkflowList::item{background:#22282b;border:1px solid #394348;border-radius:3px;margin:2px;padding:4px} #projectWorkflowList::item:selected{background:#294356;border-color:#69a5d0}
        #librarySave{background:#3b78a5;border-color:#5994bd;font-weight:bold} #librarySave:hover{background:#5596ca;border-color:#8bc8f5;color:#fff} #librarySave:pressed{background:#214865;border:1px solid #b9e1ff;color:#fff} #librarySecondary{background:transparent;border-color:#3d484e;color:#bfc7cb} #librarySecondary:hover{background:#30393d;border-color:#596a73;color:#fff} #libraryDelete{background:transparent;border-color:#4b3b3b;color:#c8b7b7} #libraryDelete:hover{background:#713d3d;border-color:#9b5656;color:#fff}
        QMenu{background:#252a2c;border:1px solid #596267;padding:4px} QMenu::item{padding:7px 28px 7px 12px;border-radius:3px} QMenu::item:selected{background:#3b6f9c;color:#fff} QMenu::separator{height:1px;background:#4b5255;margin:4px 7px}
        QScrollBar:vertical{background:#171b1d;width:12px;margin:0;border:0;border-radius:6px} QScrollBar::handle:vertical{background:#46545c;min-height:28px;margin:2px;border-radius:4px} QScrollBar::handle:vertical:hover{background:#63869b} QScrollBar::handle:vertical:pressed{background:#74a8c6}
        QScrollBar:horizontal{background:#171b1d;height:12px;margin:0;border:0;border-radius:6px} QScrollBar::handle:horizontal{background:#46545c;min-width:28px;margin:2px;border-radius:4px} QScrollBar::handle:horizontal:hover{background:#63869b} QScrollBar::handle:horizontal:pressed{background:#74a8c6}
        QScrollBar::add-line,QScrollBar::sub-line{width:0;height:0;background:transparent;border:0} QScrollBar::up-arrow,QScrollBar::down-arrow,QScrollBar::left-arrow,QScrollBar::right-arrow{width:0;height:0;background:transparent} QScrollBar::add-page,QScrollBar::sub-page{background:transparent} QAbstractScrollArea::corner{background:#171b1d;border:0}
        """
        # Compact controls: retain visible checked/hover states without
        # consuming another line of writing space for padded buttons.
        theme += """
        #timelineFitButton{background:transparent;border:0;border-radius:4px;
                           color:#e1f3fc;padding:0;min-height:0}
        #timelineFitButton:hover{background:rgba(255,255,255,20)}
        #voiceOptions{background:transparent;border:1px solid #455057;border-radius:4px;
                      color:#bfccd2;padding:0;min-height:0}
        #voiceOptions:hover{background:#2b3940;border-color:#7195a8;color:#f0f8fb}
        #voiceOptions:disabled{color:#5b6871;border-color:#333d42}
        #directorOption{background:transparent;border:1px solid #46535b;border-radius:4px;
                        color:#b9c5cb;padding:1px 6px;min-height:16px}
        #directorOption:hover{background:#2b3940;border-color:#7195a8;color:#f0f8fb}
        #directorOption:checked{background:#244c38;border-color:#4a9970;color:#d2f3dd}
        #directorOption:disabled{color:#68757b;border-color:#39454a}
        #workspacePicker{background:#222e35;border:1px solid #395462;border-radius:4px;
                         padding-top:1px;padding-bottom:1px;min-height:18px}
        #toolbarRefs{background:transparent;border:1px solid #425b67;border-radius:4px;
                     color:#c2deeb;padding:1px 5px;min-height:17px}
        #toolbarRefs:hover{background:#2b404b;border-color:#659cb8}
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
        dock_grip_horizontal = str(files("ltx_prompt_director").joinpath("assets/dock-grip-horizontal.png")).replace("\\", "/")
        theme = theme.replace("__DOCK_GRIP_WIDTH__", str(metric(14))).replace("__DOCK_GRIP_IMAGE__", dock_grip).replace("__DOCK_GRIP_HORIZONTAL_IMAGE__", dock_grip_horizontal)
        spin_up = str(files("ltx_prompt_director").joinpath("assets/spin-up.svg")).replace("\\", "/")
        spin_down = str(files("ltx_prompt_director").joinpath("assets/spin-down.svg")).replace("\\", "/")
        theme = theme.replace("__SPIN_UP_IMAGE__", spin_up).replace("__SPIN_DOWN_IMAGE__", spin_down)
        theme = theme.replace("__TIMELINE_SPIN_PAD__", str(metric(16))).replace("__TIMELINE_SPIN_BUTTON__", str(metric(13))).replace("__TIMELINE_ARROW__", str(metric(7)))
        for size in (15, 10, 9, 8):
            theme = theme.replace(f"font-size:{size}px", f"font-size:{scaled(size)}px")
        from .workspace_style import workspace_style
        self.setStyleSheet(workspace_style(theme,scale))
        self.findChild(QToolBar,'mainToolbar').setIconSize(QSize(metric(24),metric(24)))
        self.minimax_panel.cache_state.setMinimumWidth(metric(90))
        for target in self.minimax_panel.reference_targets:target.preview.setFixedSize(metric(116),metric(112))
        self.timeline_scale.setFixedWidth(metric(98))
        self.timeline_fit_button.setIconSize(QSize(metric(18), metric(18)))
        self.timeline_fit_button.setFixedSize(metric(32), metric(32))
        self.output_width.setFixedWidth(metric(75))
        self.output_height.setFixedWidth(metric(75))
        self.ui_scale_spin.setFixedWidth(metric(82))
        self.intent.setMinimumHeight(metric(40))
        self.intent.setMaximumHeight(16777215)
        self.requested_length.setFixedWidth(metric(68))
        self.speaker_language.setMinimumWidth(metric(170))
        self.speaker_accent.setMinimumWidth(metric(170))
        for field in (self.speaker_language,self.speaker_accent):
            field.setMinimumWidth(90);field.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        self.director_controls.setContentsMargins(metric(6), metric(4), metric(6), metric(4))
        self.director_controls.setSpacing(metric(3))
        self.director_intent_row.setSpacing(metric(4))
        self.options_row.setSpacing(metric(4))
        self.segment_header.setSpacing(metric(6))
        self.project_type_combo.setFixedWidth(min(252, max(160, metric(198))))
        self.project_type_combo.setFixedHeight(max(metric(24), self.project_type_combo.fontMetrics().height() + metric(7)))
        self.references_button.setFixedSize(metric(32), metric(30))
        self.references_button.setIconSize(QSize(metric(22), metric(22)))
        for option in (self.hdr, self.sfx, self.spoken_dialog, self.reduce_music):
            option.setMinimumWidth(0)
            option.setFixedWidth(option.fontMetrics().horizontalAdvance(option.text()) + metric(12))
            option.setFixedHeight(max(metric(20), option.fontMetrics().height() + metric(4)))
        self.voice_options_button.setFixedSize(metric(24), metric(23))
        self.prompt_scope.setFixedWidth(metric(94))
        self.prompt_scope.setFixedHeight(metric(28))
        self.ruler.setFixedHeight(metric(28))
        self.timeline_height_handle.set_scale(scale)
        self.conditioning_height_handle.set_scale(scale)
        self.project_list.setSpacing(metric(4))
        for button in (self.provider_button, self.refine_timing_button,
                       self.refine_prompt_button, self.start_button, self.end_button):
            button.setMinimumWidth(button.fontMetrics().horizontalAdvance(button.text()) + metric(18))
        for action, image in (
            (self.magic_button, "action-generate"),
            (self.prompt_refine_button, "action-refine"),
            (self.combined_generate_button, "action-generate"),
            (self.combined_refine_button, "action-refine"),
        ):
            action.setIcon(prompt_action_icon(image))
            action.setIconSize(QSize(metric(15), metric(15)))
            action.setFixedHeight(metric(28))
            action.setMinimumWidth(0)
            action.setFixedWidth(max(
                metric(71),
                action.fontMetrics().horizontalAdvance(action.text()) + metric(26)))
        for action, image in (
            (self.sync_combined_button, "sync-up"),
        ):
            action.setIcon(prompt_action_icon(image))
            action.setIconSize(QSize(metric(18), metric(18)))
            action.setFixedSize(metric(30), metric(28))
        for button in (self.segment_quick_fix_button, self.combined_quick_fix_button):
            button.setFixedHeight(metric(28))
            button.setFixedWidth(button.fontMetrics().horizontalAdvance(button.text()) + metric(27))
        self.output_width.setMinimumWidth(metric(74))
        self.output_height.setMinimumWidth(metric(74))
        self.duration_spin.setFixedWidth(metric(68))
        self.ruler_spacer.setFixedWidth(metric(38))
        self.main_track_label.setFixedWidth(metric(38))
        self.add_tile_wrap.setFixedWidth(metric(44))
        self.add_tile.setFixedSize(metric(32), metric(32))
        self.add_text_tile.setFixedSize(metric(32), metric(32))
        self.sync_timeline_button.setIcon(toolbar_icon("rewrite-timeline"))
        self.sync_timeline_button.setIconSize(QSize(metric(20), metric(20)))
        self.sync_timeline_button.setFixedSize(metric(32), metric(32))
        self.retime_all_button.setIcon(toolbar_icon("retime"))
        self.retime_all_button.setIconSize(QSize(metric(20), metric(20)))
        self.retime_all_button.setFixedSize(metric(32), metric(32))
        self.add_tile_layout.setContentsMargins(metric(3), metric(4), metric(3), metric(4))
        self.segment_ai_marquee.setMinimumWidth(0)
        for button in (self.copy_segment,):
            button.setFixedHeight(button.fontMetrics().height() + metric(4))
            button.setMinimumWidth(button.fontMetrics().horizontalAdvance(button.text()) + metric(10))
        if hasattr(self, "timeline_loading"):
            self.timeline_loading.set_scale(scale)
        if hasattr(self, "magic_overlay"):
            self.magic_overlay.set_scale(scale)

    def refresh_project_library(self, select_id: str | None = None, preserve_scroll: bool = True) -> None:
        self._refreshing_project_library = True
        selected = select_id or self.current_project_id
        previous_scroll = self.project_list.verticalScrollBar().value() if preserve_scroll else 0
        if self.project_list._scroll_animation:
            self.project_list._scroll_animation.stop()
            self.project_list._scroll_animation = None
        self.project_list._scroll_target = previous_scroll
        self.project_list.clear()
        records = self.library_records()
        records.extend(ensure_collection_sequences(project_library_path(), records))
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
                item.setBackground(QColor("#22282b"))
                item.setData(Qt.ItemDataRole.UserRole, {"kind": "collection", "name": name, "description": meta["description"], "members": members})
                item.setToolTip(f"Collection: {name}")
                item.setSizeHint(QSize(self.project_icon_size + 28, self.project_icon_size + 76))
                collection_cover = self.collection_pixmap(members[:4], self.project_icon_size)
                item.setIcon(QIcon(collection_cover))
                self.project_list.addItem(item)
                continue
            name = str(meta.get("name") or "Untitled project")
            description = " ".join(str(meta.get("description") or "No description").split())
            if len(description) > 105:
                description = description[:102] + "…"
            item = QListWidgetItem(f"{name}\n{description}")
            item.setBackground(QColor("#22282b"))
            item.setData(Qt.ItemDataRole.UserRole, {**meta, "kind": "project"})
            item.setToolTip(f"{name}\n\n{meta.get('description', '')}")
            item.setSizeHint(QSize(self.project_icon_size + 28, self.project_icon_size + 76))
            cover = cover_path(str(meta.get("thumbnailData", ""))) if meta.get("thumbnailData") else None
            pixmap = QPixmap(str(cover)) if cover and cover.is_file() else pixmap_from_data_url(str(meta.get("thumbnailData", "")))
            if pixmap.isNull():
                pixmap = QPixmap(self.project_icon_size, self.project_icon_size)
                pixmap.fill(QColor("#171a1b"))
            else:
                pixmap = self.square_pixmap(pixmap, self.project_icon_size)
            tile_color = self.project_state_color(meta)
            if tile_color:
                item.setData(PROJECT_CARD_COLOR_ROLE, tile_color)
            labels = []
            status_colors = {tag["name"].casefold(): tag["color"] for tag in self.project_status_tags()}
            other_colors = {tag["name"].casefold(): tag["color"] for tag in self.project_other_tags()}
            status = str(meta.get("status", ""))
            if status:
                labels.append((status, status_colors.get(status.casefold(), "#56616a")))
            for label in meta.get("tags", []):
                labels.append((str(label), other_colors.get(str(label).casefold(), "#56616a")))
            if meta.get("archived"):
                labels.append(("Archive", ARCHIVE_COLOR))
            if labels:
                pixmap = self.add_thumbnail_labels(pixmap, labels)
            item.setIcon(QIcon(pixmap))
            if meta.get("projectType") == "shot_sequence" and not meta.get("thumbnailData"):
                item.setIcon(QIcon(str(files("ltx_prompt_director").joinpath("assets/shot-sequence.svg"))))
            self.project_list.addItem(item)
            if meta.get("id") == selected:
                selected_item = item
        if selected_item:
            self.project_list.setCurrentItem(selected_item)
        self.filter_projects(self.project_search.text())
        self._refreshing_project_library = False
        self.sync_project_properties()
        QTimer.singleShot(0, lambda value=previous_scroll: self.project_list.verticalScrollBar().setValue(value))

    def sync_project_properties(self) -> None:
        if not hasattr(self, "project_properties_panel") or getattr(self, "_refreshing_project_library", False):
            return
        chosen = self.selected_library_project()
        if chosen and chosen.get("kind") != "collection":
            self.project_properties_panel.set_project(chosen)
        elif self.current_project_id:
            self.project_properties_panel.set_project(self.current_library_metadata())
        else:
            self.project_properties_panel.set_project(None)

    @staticmethod
    def project_entry_key(meta: dict) -> str:
        return f"collection:{meta.get('name', '')}" if meta.get("kind") == "collection" else f"project:{meta.get('id', '')}"

    def project_state_color(self, meta: dict) -> str | None:
        if meta.get("archived"):
            return ARCHIVE_COLOR
        colors = {tag["name"].casefold(): tag["color"] for tag in self.project_status_tags()}
        return colors.get(str(meta.get("status", meta.get("tag", ""))).casefold())

    @staticmethod
    def add_thumbnail_labels(pixmap: QPixmap, labels: list[tuple[str, str]]) -> QPixmap:
        return add_thumbnail_labels(pixmap, labels)

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
            self._pending_project_id = str(meta.get("id", ""))
            self._project_open_timer.start()

    def activate_double_clicked_project(self, item: QListWidgetItem) -> None:
        """Open either a project or a collection from the same tile gesture."""
        meta = item.data(Qt.ItemDataRole.UserRole) or {}
        if meta.get("kind") == "collection":
            self._project_open_timer.stop()
            self._pending_project_id = None
            self.project_list.setCurrentItem(item)
            self.open_library_project()
            return
        self.activate_clicked_project(item)

    def open_pending_library_project(self) -> None:
        project_id = self._pending_project_id
        self._pending_project_id = None
        if project_id:
            self.open_library_project(project_id=project_id)

    def project_files_dropped(self, paths: list[str]) -> None:
        if not paths:
            return
        path = paths[0]
        if Path(path).suffix.casefold() == ".ltxd":
            self.open_project(path=path)
        else:
            self.import_ltx(path=path)

    def library_records(self) -> list[dict]:
        records = []
        for path in project_library_path().glob("*.meta.json"):
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
                if Path(meta.get("projectPath", "")).is_file():
                    normalize_project_labels(meta)
                    meta.setdefault("archived", False)
                    meta["notes"] = normalize_notes(meta.get("notes", []))
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
        line = max(3, round(size * .025))
        for member, (x, y) in zip(members, cells):
            pixmap = pixmap_from_data_url(str(member.get("thumbnailData", "")))
            if not pixmap.isNull():
                painter.drawPixmap(x, y, self.square_pixmap(pixmap, cell))
            color = self.project_state_color(member) or "#657078"
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(color).lighter(125), line))
            inset = max(1, line // 2)
            painter.drawRect(x + inset, y + inset, cell - line, cell - line)
        painter.end()
        return result

    def filter_projects(self, query: str) -> None:
        terms = query.casefold().split()
        selected = {key for key, button in self.project_filter_buttons.items() if button.isChecked()}
        statuses = {tag["name"].casefold(): tag["name"] for tag in self.project_status_tags()}
        selected_other = {key.removeprefix("tag:").casefold() for key in selected if key.startswith("tag:")}

        def visible(meta: dict) -> bool:
            notes = " ".join(str(note.get("text", "")) for note in meta.get("notes", []) if isinstance(note, dict))
            haystack = f"{meta.get('name', '')} {meta.get('description', '')} {meta.get('status', '')} {' '.join(meta.get('tags', []))} {notes}".casefold()
            if not all(term in haystack for term in terms):
                return False
            if meta.get("archived"):
                return "__archive__" in selected
            status = str(meta.get("status", "")).casefold()
            status_key = f"status:{statuses[status]}" if status in statuses else "__untagged__"
            if status_key not in selected:
                return False
            labels = {str(tag).casefold() for tag in meta.get("tags", [])}
            return not selected_other or bool(labels & selected_other)

        for row in range(self.project_list.count()):
            item = self.project_list.item(row)
            meta = item.data(Qt.ItemDataRole.UserRole) or {}
            if meta.get("kind") == "collection":
                item.setHidden(not any(visible(member) for member in meta.get("members", [])))
            else:
                item.setHidden(not visible(meta))

    def selected_library_project(self) -> dict | None:
        item = self.project_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def current_library_metadata(self) -> dict | None:
        if not self.current_project_id:
            return None
        path = project_library_path() / f"{self.current_project_id}.meta.json"
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
            normalize_project_labels(meta)
            return meta
        except (OSError, ValueError, TypeError):
            return None

    def update_project_preview(self) -> None:
        meta = self.current_library_metadata()
        if not meta:
            self.project_preview_panel.clear_project()
            self.project_files_panel.clear_project()
            return
        project_files = meta.get("projectFiles", meta.get("workflowFiles", []))
        project_files = [value for value in project_files if isinstance(value, dict) and Path(str(value.get("path", ""))).is_file()]
        name = str(meta.get("name") or "Untitled project")
        self.project_preview_panel.set_project(name, str(meta.get("previewVideoPath", "")))
        self.project_files_panel.set_project(name, project_files)

    def choose_project_video(self) -> None:
        initial = str(Path.home())
        meta = self.current_library_metadata()
        if meta and meta.get("previewVideoPath"):
            initial = str(Path(meta["previewVideoPath"]).parent)
        path = choose_document_open(self, "Choose rendered project video", initial, "Videos (*.mp4 *.webm *.mov *.mkv *.avi *.m4v)")
        if path:
            self.attach_project_video(path)

    def attach_local_generated_video(self, source_path, workspace):
        meta = self.current_library_metadata()
        if not meta: return
        source = Path(source_path)
        destination = project_library_path() / "previews" / f"{meta['id']}_{uuid4().hex}_generated.mp4"
        def copy_render():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            return str(destination)
        def ready(path):
            if workspace != (self.workspace_revision, self.current_project_id, self.project_type): return
            latest = self.current_library_metadata()
            if not latest: return
            latest["previewVideoPath"] = path
            latest["previewVideoName"] = "Generated video.mp4"
            self.persist_library_metadata(latest)
            self.project_preview_panel.set_project(str(latest.get("name") or "Generated video"), path)
        self.start_disk_job(copy_render, (), ready,
            lambda message: self.statusBar().showMessage("Video remains in the generation folder: " + str(message), 12000), serialized=True)

    def attach_project_video(self, source_path: str) -> None:
        self._local_result_video = None
        meta = self.current_library_metadata()
        source = Path(source_path)
        if not meta:
            QMessageBox.information(self, "Save project first", "Save or open a project before adding its rendered video.")
            return
        if not source.is_file() or source.suffix.lower() not in ProjectVideoWidget.VIDEO_SUFFIXES:
            QMessageBox.warning(self, "Unsupported video", "Choose an MP4, WebM, MOV, MKV, AVI, or M4V video.")
            return
        preview_dir = project_library_path() / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)
        safe_name = safe_media_filename(source.name, "project_preview")
        destination = preview_dir / f"{meta['id']}_{safe_name}"
        previous = Path(str(meta.get("previewVideoPath", "")))
        try:
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
            if previous.is_file() and previous.resolve() != destination.resolve() and previous.parent == preview_dir:
                previous.unlink()
        except OSError as error:
            QMessageBox.critical(self, "Video import failed", str(error))
            return
        meta["previewVideoPath"] = str(destination)
        meta["previewVideoName"] = source.name
        self.persist_library_metadata(meta)
        self.project_preview_panel.set_project(str(meta.get("name") or "Untitled project"), str(destination))
        self.project_preview_dock.show()
        self.statusBar().showMessage(f"Rendered video saved to project: {meta.get('name', 'Untitled project')}")

    def export_project_video(self) -> None:
        meta = self.current_library_metadata()
        generated = getattr(self, "_local_result_video", None)
        scope = (self.workspace_revision, self.current_project_id, self.project_type)
        if generated and generated["scope"] == scope: source = Path(generated["path"])
        elif meta: source = Path(str(meta.get("previewVideoPath", "")))
        else: source = Path()
        if not source.is_file():
            QMessageBox.information(self, "No project video", "Add a rendered video to this project first.")
            return
        original = ("Generated video.mp4" if generated and generated["scope"] == scope
                    else str(meta.get("previewVideoName") or source.name) if meta else source.name)
        target = self.next_export_path(original)
        self.start_disk_job(shutil.copy2, (source, target), lambda _result: self.record_export(target),
                            lambda message: self.export_failed(target, message), serialized=True)

    def choose_project_workflow(self) -> None:
        source_name = choose_document_open(self, "Add project file", str(Path.home()), "All files (*)")
        if source_name:
            self.attach_project_workflow(source_name)

    def attach_project_workflow(self, source_name: str) -> None:
        meta = self.current_library_metadata()
        if not meta:
            QMessageBox.information(self, "Save project first", "Save or open a project before adding project files.")
            return
        source = Path(source_name)
        if not source.is_file():
            QMessageBox.warning(self, "Project file missing", "The dropped project file could not be found.")
            return
        workflow_dir = project_library_path() / "project_files"
        workflow_dir.mkdir(parents=True, exist_ok=True)
        destination = workflow_dir / f"{meta['id']}_{uuid4().hex[:8]}_{safe_media_filename(source.name, 'project_file')}"
        try:
            shutil.copy2(source, destination)
        except OSError as error:
            QMessageBox.critical(self, "Project file import failed", str(error))
            return
        workflows = [value for value in meta.get("projectFiles", meta.get("workflowFiles", [])) if isinstance(value, dict)]
        workflows.append({"name": source.name, "path": str(destination)})
        meta["projectFiles"] = workflows
        meta.pop("workflowFiles", None)
        self.persist_library_metadata(meta)
        self.update_project_preview()
        self.project_files_dock.show()
        self.statusBar().showMessage(f"Project file added: {source.name}")

    def export_project_workflow(self) -> None:
        workflow = self.project_files_panel.selected_file()
        source = Path(str(workflow.get("path", ""))) if workflow else Path()
        if not workflow or not source.is_file():
            return
        target = self.next_export_path(str(workflow.get("name") or source.name))
        self.start_disk_job(shutil.copy2, (source, target), lambda _result: self.record_export(target),
                            lambda message: self.export_failed(target, message), serialized=True)

    def remove_project_workflow(self) -> None:
        meta = self.current_library_metadata()
        selected = self.project_files_panel.selected_file()
        if not meta or not selected:
            return
        selected_path = Path(str(selected.get("path", "")))
        meta["projectFiles"] = [
            value for value in meta.get("projectFiles", meta.get("workflowFiles", []))
            if not isinstance(value, dict) or str(value.get("path", "")) != str(selected_path)
        ]
        meta.pop("workflowFiles", None)
        try:
            if selected_path.is_file() and selected_path.parent in {project_library_path() / "project_files", project_library_path() / "workflows"}:
                selected_path.unlink()
        except OSError as error:
            QMessageBox.warning(self, "Project file removal warning", str(error))
        self.persist_library_metadata(meta)
        self.update_project_preview()
        self.statusBar().showMessage(f"Project file removed: {selected.get('name', selected_path.name)}")

    def project_library_menu(self, point) -> None:
        item = self.project_list.itemAt(point)
        if not item:
            return
        self.project_list.setCurrentItem(item)
        meta = item.data(Qt.ItemDataRole.UserRole) or {}
        menu = QMenu(self)
        is_collection = meta.get("kind") == "collection"
        members = meta.get("members", []) if is_collection else [meta]
        state_menu = menu.addMenu("Set group status" if is_collection else "Set status")
        status_group = QActionGroup(state_menu)
        status_group.setExclusive(True)
        clear = state_menu.addAction("No status")
        clear.setCheckable(True)
        status_group.addAction(clear)
        clear.setChecked(all(not member.get("status") for member in members))
        clear.triggered.connect(lambda: self.set_selected_project_status(""))
        for tag in self.project_status_tags():
            action = state_menu.addAction(tag["name"])
            action.setCheckable(True)
            status_group.addAction(action)
            action.setChecked(bool(members) and all(str(member.get("status", "")).casefold() == tag["name"].casefold() for member in members))
            action.triggered.connect(lambda checked=False, name=tag["name"]: self.set_selected_project_status(name))

        other_definitions = self.project_other_tags()
        if other_definitions:
            labels_menu = menu.addMenu("Group tags" if is_collection else "Tags")
            for tag in other_definitions:
                action = labels_menu.addAction(tag["name"])
                action.setCheckable(True)
                action.setChecked(bool(members) and all(tag["name"].casefold() in {str(value).casefold() for value in member.get("tags", [])} for member in members))
                action.triggered.connect(lambda checked, name=tag["name"]: self.set_selected_project_label(name, checked))

        menu.addSeparator()
        all_archived = bool(members) and all(member.get("archived") for member in members)
        archive = menu.addAction(
            "Restore entire group" if is_collection and all_archived else
            "Archive entire group" if is_collection else
            "Restore from archive" if all_archived else "Archive project"
        )
        archive.triggered.connect(self.toggle_archive_selected)
        if is_collection:
            menu.exec(self.project_list.viewport().mapToGlobal(point))
            return
        menu.addSeparator()
        menu.addAction("Open project", self.open_library_project)
        menu.addAction("Duplicate project", self.duplicate_library_project)
        menu.addAction("Edit project details", self.edit_library_project)
        menu.addAction("Delete project", self.delete_library_project)
        menu.exec(self.project_list.viewport().mapToGlobal(point))

    def duplicate_library_project(self) -> None:
        """Make an independently editable copy of the right-clicked project."""
        meta = self.selected_library_project()
        if not meta or meta.get("kind") != "project":
            return
        project_id = str(meta["id"])
        if project_id == self.current_project_id and self.project_dirty:
            # The serialized disk pool saves this snapshot before it copies the archive.
            self.save_library_project(automatic=True)
        new_id = uuid4().hex
        self.statusBar().showMessage(f"Duplicating project in background: {meta.get('name', 'Untitled project')}…")

        def finished(clone: dict) -> None:
            self.refresh_project_library(str(clone["id"]))
            self.statusBar().showMessage(f"Project duplicated: {clone['name']}")

        def failed(message: str) -> None:
            QMessageBox.critical(self, "Project duplication failed", message)

        self.start_disk_job(
            _duplicate_library_project_files,
            (copy.deepcopy(meta), project_library_path(), new_id),
            finished, failed, serialized=True
        )

    def persist_library_metadata(self, meta: dict) -> None:
        clean = {key: value for key, value in meta.items() if key not in {"kind", "members"}}
        normalize_project_labels(clean)
        root = project_library_path()
        (root / f"{clean['id']}.meta.json").write_text(json.dumps(clean, indent=2), encoding="utf-8")
        self.start_disk_job(_persist_library_archive, (copy.deepcopy(clean), root / f"{clean['id']}.meta.json"),
                            lambda _result: None,
                            lambda message: QMessageBox.warning(self, "Project metadata save failed", message), serialized=True)
        if getattr(self, "project_properties_panel", None) and str(clean["id"]) == self.current_project_id:
            self.project_properties_panel.set_project(clean)

    def selected_project_members(self) -> tuple[dict | None, list[dict]]:
        meta = self.selected_library_project()
        return meta, (meta.get("members", []) if meta and meta.get("kind") == "collection" else [meta] if meta else [])

    def set_selected_project_status(self, name: str) -> None:
        meta, members = self.selected_project_members()
        if not meta:
            return
        for member in members:
            member["status"] = name
            member["tag"] = name
            self.persist_library_metadata(member)
        self.refresh_project_library(str(meta.get("id", "")) or None)
        subject = f"group {meta.get('name', 'Collection')} ({len(members)} projects)" if meta.get("kind") == "collection" else f"project {meta.get('name', 'Untitled project')}"
        self.statusBar().showMessage(f"Status set to {name or 'No status'} for {subject}")

    def set_selected_project_label(self, name: str, enabled: bool) -> None:
        meta, members = self.selected_project_members()
        if not meta:
            return
        for member in members:
            labels = [str(value) for value in member.get("tags", []) if str(value).casefold() != name.casefold()]
            if enabled:
                labels.append(name)
            member["tags"] = labels
            self.persist_library_metadata(member)
        self.refresh_project_library(str(meta.get("id", "")) or None)
        self.statusBar().showMessage(f"Tag {'added to' if enabled else 'removed from'} {len(members)} project{'s' if len(members) != 1 else ''}: {name}")

    def toggle_archive_selected(self) -> None:
        meta, members = self.selected_project_members()
        if not meta:
            return
        archived = not (bool(members) and all(member.get("archived") for member in members))
        for member in members:
            member["archived"] = archived
            self.persist_library_metadata(member)
        self.refresh_project_library(str(meta.get("id", "")) or None)
        action = "Archived" if archived else "Restored"
        subject = f"group: {meta.get('name', 'Collection')} ({len(members)} projects)" if meta.get("kind") == "collection" else f"project: {meta.get('name', 'Untitled project')}"
        self.statusBar().showMessage(f"{action} {subject}")

    def project_is_dirty(self, project_id: str) -> bool:
        if project_id == self.current_project_id:
            return self.project_dirty
        return bool(self.project_sessions.get(project_id, {}).get("dirty"))

    def capture_workspace_state(self) -> dict:
        self.store_minimax_draft()
        return {
            "projectType": self.project_type,
            "workspaceDefinition": copy.deepcopy(self.workspace_definition),
            "shotSequence": dict(self.shot_sequence_settings),
            "downloadDirectory": self.download_directory,
            "minimaxDrafts": {key: dict(value) for key, value in self.minimax_drafts.items()},
            "segments": self.segments,
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
            "timelineAutoFit": self.timeline_fit_mode,
            "timelineHeight": self.timeline_height,
            "minimaxPrompt": self.minimax_prompt_text,
            "minimaxRefinementInstructions": self.minimax_refinement_instructions,
            "minimaxPromptCacheKey": self.minimax_prompt_cache_key,
            "minimaxPromptUpdatedAt": self.minimax_prompt_updated_at,
            "minimaxPromptMode": self.minimax_prompt_mode,
            "minimaxReferenceImages": reference_slots(self.minimax_reference_images),
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
        self.reject_segment_prompt_review()
        self.shot_sequence_settings = shot_settings(state.get("shotSequence"))
        self._loading = True
        self.workspace_revision += 1
        self.segments = state.get("segments", [])
        self.download_directory = str(state.get("downloadDirectory", ""))
        self.global_prompt.setPlainText(str(state.get("globalPrompt", "")))
        self.intent.setPlainText(str(state.get("directorIntent", "")))
        render_prompt_notes(self.intent)
        self.requested_length.setValue(requested_length_value(state.get("requestedLength")))
        self.speaker_language.setCurrentText(str(state.get("speakerLanguage", "(Image/context provided)")))
        self.speaker_accent.setCurrentText(str(state.get("speakerAccent", state.get("speakerNationality", "(Image/context provided)"))))
        self.sfx.setChecked(bool(state.get("sfx")))
        self.spoken_dialog.setChecked(bool(state.get("spokenDialog")))
        self.hdr.setChecked(bool(state.get("hdr")))
        self.reduce_music.setChecked(bool(state.get("reduceMusic")))
        self.output_width.setValue(int(state.get("outputWidth", 1280)))
        self.output_height.setValue(int(state.get("outputHeight", 704)))
        self.timeline_height = max(184, min(430, int(state.get("timelineHeight", 184))))
        self.minimax_prompt_text = str(state.get("minimaxPrompt", ""))
        self.minimax_refinement_instructions = str(state.get("minimaxRefinementInstructions", ""))
        self.minimax_prompt_cache_key = str(state.get("minimaxPromptCacheKey", ""))
        self.minimax_prompt_updated_at = str(state.get("minimaxPromptUpdatedAt", ""))
        self.minimax_prompt_mode = "references" if state.get("minimaxPromptMode") == "references" else "frames"
        self.minimax_reference_images = reference_slots(state.get("minimaxReferenceImages"))
        self.restore_project_type(state.get("projectType"), state.get("minimaxDrafts"))
        self.timeline_height_handle.current_height = self.timeline_height
        self.set_timeline_height(self.timeline_height)
        auto_fit = bool(state.get("timelineAutoFit", False))
        scale = max(1, min(160, int(state.get("timelineScale", 65))))
        self.timeline_scale.blockSignals(True)
        self.timeline_scale.setValue(0 if auto_fit else scale)
        self.timeline_scale.blockSignals(False)
        self.pixels_per_second = scale
        self.timeline_fit_mode = auto_fit
        self.ruler.set_scale(scale)
        self._loading = False
        self._timeline_signature = tuple((item.id, item.duration, item.prompt, item.kind, item.role, item.name) for item in self.segments)
        self.sync_minimax_panel()
        self.refresh_timeline()
        if auto_fit:
            self.apply_timeline_fit()
        self.queue_project_panel_width_restore()

    def leave_collection(self) -> None:
        self.current_collection = None
        self.refresh_project_library(preserve_scroll=False)

    def save_library_project(self, automatic: bool = False, source_filename: str | None = None) -> None:
        if not self.has_project_content and not self.current_project_id:
            if not automatic:
                QMessageBox.information(self, "Nothing to save", "Write a prompt or add an image, video or text segment first.")
            return
        root = project_library_path()
        meta = None
        if self.current_project_id:
            meta_path = root / f"{self.current_project_id}.meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    normalize_project_labels(meta)
                except (OSError, ValueError):
                    meta = None
        if not meta:
            project_id = self.current_project_id or uuid4().hex
            if automatic:
                intent = " ".join(self.intent.toPlainText().split())
                source_name = Path(self.segments[0].name).stem if self.segments else "Sequence"
                suggested = Path(source_filename).stem.strip() if source_filename else (re.split(r"[.!?]", intent, maxsplit=1)[0].strip() if intent else source_name)
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
                    "description": f"Imported from {Path(source_filename).name}." if source_filename else (intent or f"Magic Build sequence generated from {source_name}."),
                    "collection": self.current_collection or "",
                    "projectPath": str(root / f"{project_id}.LTXD"),
                    "status": "", "tags": [], "tag": "", "archived": False, "notes": [],
                }
            else:
                collections = [str(record.get("collection")) for record in self.library_records() if record.get("collection")]
                dialog = ProjectDetailsDialog(self.intent.toPlainText(), self, collection=self.current_collection or "", collections=collections, download_directory=self.download_directory)
                if not dialog.exec():
                    return
                self.download_directory = dialog.download_folder.text().strip()
                meta = {
                    "id": project_id,
                    "name": dialog.name.text().strip(),
                    "description": dialog.description.toPlainText().strip(),
                    "collection": dialog.collection_name(),
                    "projectPath": str(root / f"{project_id}.LTXD"),
                    "status": "", "tags": [], "tag": "", "archived": False, "notes": dialog.notes_value(),
                }
            self.current_project_id = project_id
            self.current_project_name = meta["name"]
            if "downloadDirectory" in meta:
                self.download_directory = str(meta["downloadDirectory"])
            self.update_window_title()
        meta["downloadDirectory"] = self.download_directory
        meta["savedAt"] = datetime.now(timezone.utc).isoformat()
        meta["projectType"] = self.project_type
        meta["duration"] = (shot_layout(self.segments,self.shot_sequence_settings)[1]/FPS
                            if self.workspace_engine == "shot_sequence" else self.total_duration())
        meta["segmentCount"] = len(self.segments)
        if not meta.get("thumbnailSource"):
            visual = next((segment for segment in self.segments if segment.kind != "text" and segment.preview_path), None)
            meta["thumbnailData"] = data_url(visual.preview_path, max_edge=360, quality=84) if visual else ""
        payload = self.project_payload(include_media=False)
        normalize_project_labels(meta)
        payload["library"] = {key: meta.get(key, "") for key in ("id", "name", "description", "collection", "savedAt", "status", "tags", "tag", "archived", "notes", "previewVideoPath", "previewVideoName", "projectFiles", "workflowFiles", "downloadDirectory")}
        project_id = str(meta["id"])
        save_id = uuid4().hex
        self._latest_save_id[project_id] = save_id
        edit_serial = self._edit_serial
        snapshot = copy.deepcopy(self.segments)
        workspace = self.capture_workspace_state()
        self.statusBar().showMessage(f"Saving project in background: {meta['name']}…")

        def saved(_result):
            if self._latest_save_id.get(project_id) != save_id:
                return
            if self.current_project_id == project_id and self._edit_serial == edit_serial:
                self.project_dirty = False
                self.project_sessions[project_id] = {"name": meta["name"], "dirty": False, "state": workspace}
            elif self.current_project_id != project_id:
                session = self.project_sessions.get(project_id)
                if session and session.get("state") == workspace:
                    session["dirty"] = False
            self.refresh_project_library(self.current_project_id)
            if self.current_project_id == project_id:
                self.update_project_preview()
                self.settings.setValue("last_project_id", project_id)
                self.queue_settings_sync()
            self.statusBar().showMessage(f"Project saved to library: {meta['name']}")

        def failed(message):
            self._close_after_jobs = False
            self._close_saves_queued = False
            if self._closing_progress:
                self._closing_progress.close()
                self._closing_progress = None
                self.show()
            self.project_dirty = True
            QMessageBox.critical(self, "Save failed", message)

        self.start_disk_job(_write_library_archive,
                            (str(meta["projectPath"]), payload, snapshot, root / f"{project_id}.meta.json", copy.deepcopy(meta)),
                            saved, failed, serialized=True)

    def open_library_project(self, checked: bool = False, project_id: str | None = None) -> None:
        self._project_open_timer.stop()
        self._pending_project_id = None
        if project_id:
            meta = next((record for record in self.library_records() if str(record.get("id", "")) == project_id), None)
        else:
            meta = self.selected_library_project()
        if not meta:
            return
        if meta.get("kind") == "collection":
            self._open_serial += 1
            self.current_collection = str(meta["name"])
            self.project_search.clear()
            self.refresh_project_library(preserve_scroll=False)
            return
        project_id = str(meta["id"])
        if project_id == self.current_project_id:
            self._open_serial += 1
            return
        self._open_serial += 1
        request = self._open_serial
        self.stage_current_workspace()
        self.cache_current_workspace()

        def apply_project(payload=None):
            if request != self._open_serial:
                return
            try:
                session = self.project_sessions.get(project_id)
                if payload is None and session and session.get("state"):
                    self.restore_workspace_state(session["state"])
                    dirty = bool(session.get("dirty"))
                else:
                    self.load_project_payload(payload)
                    dirty = False
                self.download_directory = str(meta.get("downloadDirectory", self.download_directory))
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
                self.update_project_preview()
                self.settings.setValue("last_project_id", project_id)
                self.queue_settings_sync()
                self.statusBar().showMessage(f"Project opened: {meta['name']}")
            except Exception as error:
                self._loading = False
                QMessageBox.critical(self, "Project open failed", str(error))

        session = self.project_sessions.get(project_id)
        if session and session.get("state"):
            apply_project()
            return
        self.statusBar().showMessage(f"Opening project in background: {meta['name']}…")
        self.start_disk_job(read_project, (meta["projectPath"],), apply_project,
                            lambda message: QMessageBox.critical(self, "Project open failed", message))

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
        if self.current_project_id == meta.get("id"):
            self.project_preview_panel.player.stop()
            self.project_preview_panel.player.setSource(QUrl())
        attached_paths = [Path(str(meta.get("previewVideoPath", "")))]
        attached_paths.extend(Path(str(value.get("path", ""))) for value in meta.get("projectFiles", meta.get("workflowFiles", [])) if isinstance(value, dict))
        for attached in attached_paths:
            if attached.is_file() and attached.parent in {project_library_path() / "previews", project_library_path() / "project_files", project_library_path() / "workflows"}:
                attached.unlink()
        if project_path.is_file():
            project_path.unlink()
        if meta_path.is_file():
            meta_path.unlink()
        self.project_sessions.pop(str(meta.get("id", "")), None)
        if self.current_project_id == meta.get("id"):
            self.current_project_id = None
            self.project_dirty = True
            self.update_project_preview()
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
            thumbnail_options = project_thumbnail_data(meta["projectPath"])
        except (OSError, ValueError, TypeError):
            thumbnail_options = []
        dialog = ProjectDetailsDialog(
            str(meta.get("description", "")), self, name=str(meta.get("name", "")),
            collection=str(meta.get("collection", "")), collections=collections,
            thumbnail_options=thumbnail_options, thumbnail_data=str(meta.get("thumbnailData", "")),
            thumbnail_source=str(meta.get("thumbnailSource", "")),
            notes=meta.get("notes", []),
            download_directory=self.download_directory if meta["id"] == self.current_project_id else str(meta.get("downloadDirectory", "")),
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
            "notes": dialog.notes_value(),
            "downloadDirectory": dialog.download_folder.text().strip(),
            "savedAt": datetime.now(timezone.utc).isoformat(),
        })
        self.persist_library_metadata(meta)
        if self.current_project_id == meta["id"]:
            self.current_project_name = meta["name"]
            if "downloadDirectory" in meta:
                self.download_directory = str(meta["downloadDirectory"])
            self.update_window_title()
            self.update_project_preview()
        if str(meta["id"]) in self.project_sessions:
            self.project_sessions[str(meta["id"])]["name"] = meta["name"]
        self.refresh_project_library(meta["id"])
        self.statusBar().showMessage(f"Project details updated: {meta['name']}")

    def update_window_title(self) -> None:
        self.setWindowTitle(application_window_title(self.current_project_name))
        self.sync_minimax_panel()

    def queue_closing_projects(self) -> None:
        """Save every dirty workspace only after the user requests app close."""
        self.stage_current_workspace()
        self.cache_current_workspace()
        active_id, active_name = self.current_project_id, self.current_project_name
        active_state = self.capture_workspace_state()
        active_dirty = self.project_dirty
        dirty = [(project_id, dict(session)) for project_id, session in self.project_sessions.items()
                 if session.get("dirty") and session.get("state")]
        for project_id, session in dirty:
            self.current_project_id = project_id
            self.current_project_name = session.get("name", "Untitled")
            self.restore_workspace_state(session["state"])
            self.project_dirty = True
            self.save_library_project(automatic=True)
        self.current_project_id, self.current_project_name = active_id, active_name
        if dirty:
            self.restore_workspace_state(active_state)
            self.project_dirty = active_dirty
            self.update_window_title()
        self._close_saves_queued = True

    def update_closing_progress(self) -> None:
        if not self._closing_progress:
            return
        progress = self._closing_progress.findChild(QProgressBar, "closeProgressBar")
        if progress:
            progress.setMaximum(max(1, self._closing_total))
            progress.setValue(self._closing_total - self._pending_disk_jobs)
        label = self._closing_progress.findChild(QLabel, "closeProgressLabel")
        if label:
            label.setText(f"Saving projects… {self._closing_total - self._pending_disk_jobs} of {self._closing_total} complete")
        if self._closing_progress.isVisible():
            if label:
                label.repaint()
            if progress:
                progress.repaint()
            self._closing_progress.repaint()

    def show_closing_progress(self) -> None:
        if not self.isVisible() or self._closing_progress:
            return
        dialog = QDialog(None)
        dialog.setWindowTitle("Finishing project saves")
        dialog.setModal(True)
        dialog.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)
        label = QLabel("Saving projects…")
        label.setObjectName("closeProgressLabel")
        layout.addWidget(label)
        bar = QProgressBar()
        bar.setObjectName("closeProgressBar")
        bar.setTextVisible(True)
        bar.setMinimumHeight(24)
        layout.addWidget(bar)
        # Use a consistent window size while allowing large system fonts to fit.
        hint = dialog.sizeHint()
        dialog.setFixedSize(max(420, hint.width()), max(112, hint.height()))
        self._closing_progress = dialog
        self._closing_total = self._pending_disk_jobs
        self.update_closing_progress()
        dialog.show()
        self.hide()
        dialog.raise_()
        dialog.repaint()
        # Saving snapshots may take time on the GUI thread; paint before it starts.
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    def closeEvent(self, event) -> None:
        self.reject_segment_prompt_review()
        if hasattr(self, "shot_sequence_panel"):
            self.shot_sequence_panel.deactivate()
        if not self._close_saves_queued:
            needs_save = self.project_dirty or any(session.get("dirty") for session in self.project_sessions.values())
            if needs_save or self._pending_disk_jobs:
                self.show_closing_progress()
            self.queue_closing_projects()
            self._closing_total = self._pending_disk_jobs
            self.update_closing_progress()
        if getattr(self, "_local_video_busy", False): self._local_video_cancel.set()
        if hasattr(self, "_local_artwork_cancel"): self.cancel_local_artwork()
        if self._pending_disk_jobs:
            self._close_after_jobs = True
            self.statusBar().showMessage("Finishing background project saves before closing…")
            self.show_closing_progress()
            event.ignore()
            return
        if self._closing_progress:
            self._closing_progress.close()
            self._closing_progress = None
            self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        if hasattr(self, "effects_panel"):
            self.effects_panel.stop_preview()
        if hasattr(self, "project_preview_panel"):
            self.project_preview_panel.player.stop()
            if self.project_preview_panel.fullscreen_window.isVisible():
                self.project_preview_panel.fullscreen_window.reject()
        if hasattr(self, "project_dock"):
            self.settings.setValue("project_panel_width", self.project_panel_width)
            self.settings.setValue("project_panel_height", self.project_panel_height)
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
        if getattr(self, "timeline_fit_mode", False) and hasattr(self, "_timeline_fit_timer"):
            self._timeline_fit_timer.start()
        if hasattr(self,'_viewport_timer'):self._viewport_timer.start()

    def showEvent(self,event) -> None:
        super().showEvent(event)
        self._viewport_timer.start()
        handle=self.windowHandle()
        if handle and not getattr(self,'_screen_connected',False):
            handle.screenChanged.connect(lambda *_:self._viewport_timer.start())
            self._screen_connected=True

    def keep_window_on_screen(self) -> None:
        if not self.isVisible():return
        self.fit_internal_panels()
        if self.isMaximized() or self.isFullScreen():return
        screen=self.screen() or QApplication.primaryScreen()
        if not screen:return
        available=screen.availableGeometry();frame=self.frameGeometry()
        border_x=max(0,frame.width()-self.width());border_y=max(0,frame.height()-self.height())
        width=min(self.width(),max(1,available.width()-border_x))
        height=min(self.height(),max(1,available.height()-border_y))
        if width!=self.width() or height!=self.height():self.resize(width,height)
        frame=self.frameGeometry()
        x=max(available.left(),min(frame.left(),available.right()-frame.width()+1))
        y=max(available.top(),min(frame.top(),available.bottom()-frame.height()+1))
        if x!=frame.left() or y!=frame.top():self.move(self.pos()+QPoint(x-frame.left(),y-frame.top()))

    def eventFilter(self,watched,event):
        targets = getattr(self, "_effect_drop_targets", {})
        if watched in targets:
            kind = event.type()
            if kind == QEvent.Type.DragLeave:
                watched.setStyleSheet("")
            elif kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop) and event.mimeData().hasFormat(EFFECT_MIME):
                try:
                    eid = bytes(event.mimeData().data(EFFECT_MIME)).decode("utf-8")
                except UnicodeDecodeError:
                    eid = ""
                entry = next((e for e in load_effects(self.settings) if e["id"] == eid), None)
                editor = targets[watched]
                row = self.timeline.row(self.timeline.itemAt(event.position().toPoint())) if editor == "timeline" else -1
                if editor == "timeline":
                    allowed = (not self.ai_busy and not self.prompt_only_workspace
                               and self.workspace_engine != "shot_sequence" and 0 <= row < len(self.segments)
                               and bool(self.segments[row].prompt.strip()))
                    editor = "segment"
                else:
                    allowed = self.quick_fix_available(editor)
                allowed = bool(allowed and entry and entry.get("prompt") and entry.get("target") in (editor, "both"))
                if kind == QEvent.Type.Drop:
                    watched.setStyleSheet("")
                    if allowed:
                        if row >= 0: self.timeline.setCurrentRow(row)
                        self.apply_library_effect(entry, editor)
                else:
                    watched.setStyleSheet("border:2px solid " + ("#56cda3" if allowed else "#d47777") + ";")
                if allowed: event.acceptProposedAction()
                else: event.ignore()
                return True
        if watched is getattr(self,'editor_root',None) and event.type()==QEvent.Type.LayoutRequest:
            self._viewport_timer.start()
        return super().eventFilter(watched,event)

    def fit_internal_panels(self):
        if not hasattr(self,'editor_root') or getattr(self,'_fitting_panels',False):return
        self._fitting_panels=True
        try:
            self.toolbar_brand_action.setVisible(self.width()>=1450)
            self.editor_root.layout().activate()
            timeline_resized=False
            if not self.timeline_shell.isHidden():
                limit=self.maximum_safe_timeline_height()
                height=min(self.timeline_height,limit)
                if self.timeline.height()!=height:
                    timeline_resized=True
                    self.timeline.setFixedHeight(height)
                    self.add_tile_wrap.setFixedHeight(height)
                    self.update_timeline_layout()
            self.editor_root.layout().activate()
            if timeline_resized:
                self.set_director_intent_expanded(self.settings.value('director_intent_expanded',True,bool),persist=False)
        finally:self._fitting_panels=False

    def mark_dirty(self, *_args) -> None:
        if self._loading:
            return
        self._edit_serial += 1
        was_dirty = self.project_dirty
        self.project_dirty = True
        signature = tuple((item.id, item.duration, item.prompt, item.kind, item.role, item.name) for item in self.segments)
        changed = signature != self._timeline_signature
        self._timeline_signature = signature
        self.update_conditioning_guide()
        # Segment edits remain authoritative. Do not occupy writing space with
        # a persistent "Segment changes available" warning or extra button.
        if self.current_project_id:
            session = self.project_sessions.setdefault(self.current_project_id, {"name": self.current_project_name})
            session["dirty"] = True
            if not was_dirty and hasattr(self, "project_list"):
                self.refresh_project_library(self.current_project_id)

    def new_project(self) -> None:
        self.reject_segment_prompt_review()
        self._open_serial += 1
        self.stage_current_workspace()
        if self.current_project_id:
            self.cache_current_workspace()
        self.download_directory = ""
        self._loading = True
        self.segments = []
        self.current_project_id = None
        self.current_project_name = "Untitled"
        self.update_project_preview()
        self.intent.clear()
        self.requested_length.setValue(0)
        self.speaker_language.setCurrentText("(Image/context provided)")
        self.speaker_accent.setCurrentText("(Image/context provided)")
        self.segment_prompt.clear()
        self.global_prompt.clear()
        self.minimax_prompt_text = ""
        self.minimax_refinement_instructions = ""
        self.minimax_prompt_cache_key = ""
        self.minimax_prompt_updated_at = ""
        self.minimax_prompt_mode = "frames"
        self.minimax_reference_images = [None] * 4
        self.minimax_drafts = {}
        self.shot_sequence_settings = dict(SHOT_DEFAULTS)
        self.project_type = "ltx" if "ltx" in self.workspace_definitions else next(iter(self.workspace_definitions), "")
        self.workspace_revision += 1
        self.apply_project_type_ui()
        self.sfx.setChecked(False)
        self.spoken_dialog.setChecked(False)
        self.hdr.setChecked(False)
        self.reduce_music.setChecked(False)
        self.output_width.setValue(1280)
        self.output_height.setValue(704)
        self._loading = False
        self.project_dirty = False
        self.refresh_timeline()
        self.initialize_empty_library_project()
        self.update_window_title()
        self.statusBar().showMessage("New project ready")

    def initialize_empty_library_project(self):
        """Give a new, empty workspace a durable library identity immediately."""
        root = project_library_path()
        project_id = uuid4().hex
        names = {str(record.get("name", "")).casefold() for record in self.library_records()}
        name, index = "Untitled project", 2
        while name.casefold() in names:
            name = f"Untitled project ({index})"
            index += 1
        meta = {"id": project_id, "name": name, "description": "",
                "collection": self.current_collection or "", "projectType": self.project_type,
                "projectPath": str(root / f"{project_id}.LTXD"),
                "savedAt": datetime.now(timezone.utc).isoformat(),
                "segmentCount": 0, "duration": 0, "thumbnailData": "",
                "status": "", "tags": [], "archived": False, "notes": []}
        payload = self.project_payload(include_media=False)
        payload["library"] = dict(meta)
        # This writes only an empty manifest and metadata, never media. Later
        # content saves continue through the existing background save worker.
        try:
            _write_library_archive(meta["projectPath"], payload, [], root / f"{project_id}.meta.json", meta)
        except OSError as error:
            QMessageBox.critical(self, "New project failed", str(error))
            return
        self.current_project_id = project_id
        self.current_project_name = name
        self.cache_current_workspace()
        self.refresh_project_library(project_id)
        self.settings.setValue("last_project_id", project_id)
        self.queue_settings_sync()

    def set_timeline_scale(self, value: int) -> None:
        if value == 0:
            self.autofit_timeline()
            return
        self.timeline_fit_mode = False
        self.autofit_tail_extension = 0
        self.pixels_per_second = float(value)
        self.ruler.set_scale(value)
        self.update_timeline_layout()
        self.mark_dirty()

    def preserve_timeline_scale_for_growth(self, target_total: float) -> None:
        """Keep expanded AI/manual segments visibly wider instead of squeezing Auto fit."""
        if not self.timeline_fit_mode or target_total <= self.total_duration() + .005:
            return
        self.timeline_fit_mode = False
        scale = max(1, round(self.pixels_per_second))
        self.timeline_scale.blockSignals(True)
        self.timeline_scale.setMaximum(max(self.timeline_scale.maximum(), scale))
        self.timeline_scale.setValue(scale)
        self.timeline_scale.blockSignals(False)
        self.timeline_scale.setToolTip('Timeline scale; Auto fit is available from the button when you want all segments visible')

    def autofit_timeline(self) -> None:
        self.timeline_fit_mode = True
        self.timeline_scale.blockSignals(True)
        self.timeline_scale.setValue(0)
        self.timeline_scale.blockSignals(False)
        self.apply_timeline_fit()
        self.mark_dirty()

    def apply_timeline_fit(self) -> None:
        if not self.timeline_fit_mode:
            return
        total = self.total_duration()
        if total <= 0:
            return
        available = max(1, self.timeline.viewport().width() - 6)
        self.pixels_per_second = available / total
        self.ruler.set_scale(self.pixels_per_second)
        self.autofit_tail_extension = 0
        self.update_timeline_layout()
        self.timeline.horizontalScrollBar().setValue(0)

    def timeline_item_widths(self, durations: list[float] | None = None) -> list[int]:
        values = durations if durations is not None else [segment.duration for segment in self.segments]
        if not self.timeline_fit_mode:
            widths = [max(48, int(value * self.pixels_per_second)) for value in values]
            if widths:
                widths[-1] += self.autofit_tail_extension
            return widths
        available = max(1, self.timeline.viewport().width() - 6)
        total = sum(max(MIN_DURATION, value) for value in values)
        if durations is None and total > 0:
            self.pixels_per_second = available / total
            self.ruler.set_scale(self.pixels_per_second)
        boundaries = [0]
        elapsed = 0.0
        for value in values:
            elapsed += max(MIN_DURATION, value)
            boundaries.append(round(available * elapsed / total))
        widths = [max(1, boundaries[index + 1] - boundaries[index]) for index in range(len(values))]
        overflow = sum(widths) - available
        for index in range(len(widths) - 1, -1, -1):
            if overflow <= 0:
                break
            reduction = min(overflow, widths[index] - 1)
            widths[index] -= reduction
            overflow -= reduction
        return widths

    def maximum_safe_timeline_height(self) -> int:
        """Keep timeline growth inside the current desktop-sized client area."""
        if not hasattr(self, "timeline") or not self.centralWidget():
            return 430
        central = self.centralWidget()
        current = max(1, self.timeline.height())
        other_minimum = max(0, self.editor_root.minimumSizeHint().height() - current)
        available_client_height = central.height()
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            window_overhead = max(0, self.frameGeometry().height() - central.height())
            desktop_client_height = max(0, screen.availableGeometry().height() - window_overhead)
            available_client_height = min(available_client_height, desktop_client_height)
        reserve=min(100,max(40,round(available_client_height*.14)))
        return max(96, min(430, available_client_height - other_minimum - reserve))

    def set_timeline_height(self, value: int) -> None:
        value = max(184, min(int(value), 430))
        self.timeline_height = value
        self.timeline_height_handle.current_height = value
        effective=min(value,self.maximum_safe_timeline_height())
        self.timeline.setFixedHeight(effective)
        self.add_tile_wrap.setFixedHeight(effective)
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

    @property
    def maximum_sequence_duration(self) -> float | None:
        # Completed-shot editing must never inherit a generation duration budget.
        return None if self.workspace_engine == "shot_sequence" else sequence_limit(self.workspace_definition)

    def available_segment_duration(self, segment: Segment | None) -> float:
        maximum = self.maximum_sequence_duration
        if maximum is None or segment is None:
            return sys.float_info.max
        return max(MIN_DURATION, maximum - sum(item.duration for item in self.segments if item.id != segment.id))

    def timeline_count_allowed(self, count: int, *, report: bool = True) -> bool:
        maximum = shot_limit(self.workspace_definition)
        allowed = maximum is None or count <= max(maximum, len(self.segments))
        if not allowed and report:
            self.statusBar().showMessage(
                f"This workspace allows {maximum} shots. Adjust Maximum shots in Settings → Workspaces to add more.", 8000)
        return allowed

    def total_duration(self) -> float:
        return sum(item.duration for item in self.segments)

    def normalize_output_dimensions(self) -> None:
        for field in (self.output_width, self.output_height):
            normalized = max(256, min(4096, round(field.value() / 32) * 32))
            if field.value() != normalized:
                field.setValue(normalized)

    def add_media(self) -> None:
        if self.prompt_only_workspace:
            return
        paths = choose_media_files(self, True, self.settings.value("last_media_dir", str(Path.home())))
        if not paths:
            return
        self.add_media_paths(paths)

    def add_text_segment(self) -> None:
        if self.prompt_only_workspace or not self.timeline_count_allowed(len(self.segments) + 1):
            return
        number = sum(segment.kind == "text" for segment in self.segments) + 1
        segment = Segment(f"Text {number}", "", "", "text", "text", "", 5.0)
        self.segments.append(segment)
        self.mark_dirty()
        self.refresh_timeline(len(self.segments) - 1)
        self.segment_prompt.setFocus()
        self.statusBar().showMessage("Text-only segment added; enter its prompt or use Magic Build")


    def add_media_paths(self, paths: list[str], insert_index: int | None = None) -> None:
        if self.workspace_engine == "shot_sequence":
            self.shot_sequence_panel.add_paths(paths,insert_index)
            return
        if self.prompt_only_workspace:
            return
        paths = [path for path in paths if Path(path).is_file()]
        if not paths:
            return
        options = self.workspace_definition.get("references", {})
        if not options.get("enabled", False):
            QMessageBox.information(self, "Workspace references", "This workspace accepts text segments only.")
            return
        kinds = options.get("kinds", [])
        allowed_paths = [path for path in paths if ("video" if Path(path).suffix.lower() in TIMELINE_VIDEO_SUFFIXES else "image") in kinds]
        if len(allowed_paths) != len(paths):
            self.statusBar().showMessage("Media types unsupported by this workspace were skipped.")
        paths = allowed_paths
        if not paths:
            return
        if not self.timeline_count_allowed(len(self.segments) + len(paths)):
            return
        self.settings.setValue("last_media_dir", str(Path(paths[0]).parent))
        target = len(self.segments) if insert_index is None else max(0, min(insert_index, len(self.segments)))
        before_id = self.segments[target].id if target < len(self.segments) else None
        workspace = (self.workspace_revision, self.current_project_id)
        self.statusBar().showMessage(f"Preparing {len(paths)} media file(s) in background…")

        def ready(result):
            if workspace != (self.workspace_revision, self.current_project_id):
                return
            prepared, errors = result
            if not self.timeline_count_allowed(len(self.segments) + len(prepared)):
                return
            index = next((i for i, segment in enumerate(self.segments) if segment.id == before_id), len(self.segments))
            for path, kind, preview, frames, trim in prepared:
                duration = 1.0 if kind == "video" else 5.0
                self.segments.insert(index, Segment(Path(path).name, path, preview, kind, "end" if index % 2 else "start", duration=duration, media_duration_frames=frames, trim_start=trim))
                index += 1
            if prepared:
                self.mark_dirty()
                self.refresh_timeline(max(0, index - 1))
            if errors:
                QMessageBox.warning(self, "Media error", "\n".join(errors))
            self.statusBar().showMessage(f"Added {len(prepared)} media file{'s' if len(prepared) != 1 else ''}")

        self.start_disk_job(_prepare_media_batch, (paths,), ready,
                            lambda message: QMessageBox.warning(self, "Media error", message))

    def refresh_timeline(self, selected: int = 0) -> None:
        if self.workspace_engine == "shot_sequence":
            # Recover full imported movies if an older build applied a generation cap.
            for shot in self.segments:
                if shot.media_duration_frames and not shot.shot_trimmed:
                    shot.duration = shot.media_duration_frames / 24
                    shot.trim_start = 0
            self.shot_sequence_panel.refresh()
            self.update_summary()
            return
        targets = fit_sequence([item.duration for item in self.segments], self.maximum_sequence_duration)
        for item, duration in zip(self.segments, targets):
            item.duration = duration
        if hasattr(self, "shot_sequence_panel") and self.workspace_engine == "shot_sequence":
            self.shot_sequence_panel.refresh()
        self.update_conditioning_guide()
        for segment in self.segments:
            segment.prompt_detached = False
        self.autofit_tail_extension = 0
        indicator = getattr(self, "timeline_loading", None) if self.segments else None
        if indicator:
            indicator.show_loading()
            indicator.repaint()
        self._loading = True
        try:
            self.timeline.clear()
            card_height = max(80, self.timeline.height() - 32)
            preview_height = max(24, card_height - 55)
            widths = self.timeline_item_widths()
            start_time = 0.0
            for index, segment in enumerate(self.segments):
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, segment.id)
                item.setSizeHint(QSize(widths[index], card_height))
                self.timeline.addItem(item)
                card = SegmentCard(segment, start_time, preview_height, self.pixels_per_second)
                card.duration_changed.connect(lambda value, sid=segment.id: self.resize_timeline_edge(sid, value))
                card.resize_started.connect(lambda sid=segment.id: self.begin_timeline_edge_drag(sid))
                card.delete_requested.connect(lambda sid=segment.id: self.delete_by_id(sid))
                card.resize_finished.connect(lambda sid=segment.id: self.finish_resize(sid))
                card.review_decision.connect(self.resolve_segment_review)
                card.set_timeline_edges(index == 0, index == len(self.segments) - 1)
                card.resize_handle.setEnabled(self.timeline_boundary_available(index))
                self.timeline.setItemWidget(item, card)
                start_time += segment.duration
            self._loading = False
            if self.segments:
                self.timeline.setCurrentRow(max(0, min(selected, len(self.segments) - 1)))
            self.update_summary()
            if hasattr(self, "timeline_review_pending"):
                self._show_segment_review_controls()
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
            widths = self.timeline_item_widths()
            start_time = 0.0
            for row in range(self.timeline.count()):
                item = self.timeline.item(row)
                segment = by_id.get(item.data(Qt.ItemDataRole.UserRole))
                if not segment:
                    continue
                width = widths[row] if row < len(widths) else 1
                item.setSizeHint(QSize(width, card_height))
                card = self.timeline.itemWidget(item)
                if isinstance(card, SegmentCard):
                    card.set_timeline_edges(row == 0, row == self.timeline.count() - 1)
                    card.resize_handle.setEnabled(self.timeline_boundary_available(row))
                    card.set_start_time(start_time)
                    card.update_layout(preview_height, self.pixels_per_second)
                start_time += segment.duration
            self.timeline.doItemsLayout()
            self.update_summary()
        finally:
            self._loading = previous_loading

    def update_timeline_selection_style(self, selected_row: int) -> None:
        """Use the same enabled, editable segment cards in every workspace."""
        for row in range(self.timeline.count()):
            card = self.timeline.itemWidget(self.timeline.item(row))
            if not isinstance(card, SegmentCard):
                continue
            selected = row == selected_row
            card.setProperty("selected", selected)
            card.style().unpolish(card)
            card.style().polish(card)
            card.update()

    def sync_order(self) -> None:
        if self._loading:
            return
        self.reject_segment_prompt_review()
        ordered_ids = [self.timeline.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.timeline.count())]
        self.segments = order_segments_by_ids(self.segments, ordered_ids)
        self.retime_unified_prompt()
        self.update_timeline_layout()
        self.mark_dirty()

    def update_segment_start_times(self) -> None:
        start_time = 0.0
        by_id = {segment.id: segment for segment in self.segments}
        for row in range(self.timeline.count()):
            item = self.timeline.item(row)
            segment = by_id.get(item.data(Qt.ItemDataRole.UserRole))
            if not segment:
                continue
            card = self.timeline.itemWidget(item)
            if isinstance(card, SegmentCard):
                card.set_start_time(start_time)
            start_time += segment.duration

    def current_segment(self) -> Segment | None:
        row = self.timeline.currentRow()
        return self.segments[row] if 0 <= row < len(self.segments) else None

    def load_editor(self, row: int) -> None:
        self.reject_segment_prompt_review()
        self._loading = True
        segment = self.current_segment()
        self.duration_spin.setMaximum(self.available_segment_duration(segment))
        self.duration_spin.setToolTip("Segment duration in seconds")
        self.duration_spin.setEnabled(bool(segment))
        self.refresh_shared_editor()
        visual = bool(segment and segment.kind != "text")
        self.start_button.setEnabled(visual)
        self.end_button.setEnabled(visual)
        self.start_button.setVisible(visual)
        self.end_button.setVisible(visual)
        self.refine_timing_button.setEnabled(not self.ai_busy and not self.prompt_only_workspace and bool(segment))
        self.update_refine_prompt_enabled()
        if segment:
            self.start_button.setChecked(segment.role == "start" if visual else False)
            self.end_button.setChecked(segment.role == "end" if visual else False)
            self.duration_spin.setValue(segment.duration)
            self.frame_number.setText(f"{'Text' if segment.kind == 'text' else 'Frame'} {row + 1}")
            self.segment_meta.setToolTip(self.frame_number.text() + " · duration and frame role")
        else:
            self.frame_number.setText("Frame —")
        self._loading = False
        self.update_counts()
        self.highlight_selected_prompt_timecode()

    def reload_clicked_segment(self, item: QListWidgetItem) -> None:
        """A timeline click targets the segment, even when already selected."""
        row = self.timeline.row(item)
        if self.ai_busy or not 0 <= row < len(self.segments):
            return
        if self.unified_workspace and not self.prompt_only_workspace:
            self.prompt_scope.setCurrentIndex(self.prompt_scope.findData("segment"))
        self.refresh_segment_prompt_box(row)
        self.highlight_selected_prompt_timecode(scroll=True)

    def refresh_segment_prompt_box(self, row: int) -> None:
        """Update only the segment prompt box, preserving all other editor state."""
        if not 0 <= row < len(self.segments):
            return
        previous_loading = self._loading
        self._loading = True
        try:
            self.refresh_shared_editor()
        finally:
            self._loading = previous_loading
        self.update_counts()

    def sync_selected_duration_control(self) -> None:
        """Keep the selected duration control synchronized with AI timing changes."""
        segment = self.current_segment()
        if not segment:
            return
        self.duration_spin.blockSignals(True)
        self.duration_spin.setMaximum(self.available_segment_duration(segment))
        self.duration_spin.setToolTip("Segment duration in seconds")
        self.duration_spin.setValue(segment.duration)
        self.duration_spin.blockSignals(False)

    def save_prompt(self) -> None:
        if self._loading or self.prompt_only_workspace:
            return
        if self.prompt_scope.currentData() == "global":
            self.global_prompt.blockSignals(True)
            self.global_prompt.setPlainText(self.segment_prompt.toPlainText())
            self.global_prompt.blockSignals(False)
            self.update_refine_prompt_enabled()
            self.mark_dirty()
            self.update_counts()
            return
        if self.current_segment():
            self.current_segment().prompt = self.segment_prompt.toPlainText()
            self.refine_prompt_button.setEnabled(not self.ai_busy and bool(self.current_segment().prompt.strip()))
            if self.current_segment().kind == "text":
                item = self.timeline.currentItem()
                card = self.timeline.itemWidget(item) if item else None
                if isinstance(card, SegmentCard):
                    card.set_text_preview(self.current_segment().prompt)
            self.mark_dirty()
            self.update_counts()
            self.preview_segment_in_unified(self.segment_prompt)

    def refresh_text_segment_previews(self) -> None:
        by_id = {segment.id: segment for segment in self.segments}
        for row in range(self.timeline.count()):
            item = self.timeline.item(row)
            segment = by_id.get(item.data(Qt.ItemDataRole.UserRole))
            card = self.timeline.itemWidget(item)
            if segment and segment.kind == "text" and isinstance(card, SegmentCard):
                card.set_text_preview(segment.prompt)

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
        self.retime_unified_prompt()
        self.mark_dirty()

    def change_duration(self, segment_id: str, value: float) -> None:
        self.autofit_tail_extension = 0
        segment = next(item for item in self.segments if item.id == segment_id)
        value = min(max(MIN_DURATION, round(value, 2)), self.available_segment_duration(segment))
        self.preserve_timeline_scale_for_growth(sum(item.duration for item in self.segments) - segment.duration + value)
        segment.duration = value
        self.retime_unified_prompt()
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
                    card.resize_handle.duration = segment.duration
                    card.duration_label.setText(f"{segment.duration:.2f}s")
                    card.update()
                    card.content.update()
                break
        self.timeline.viewport().update()
        self.update_segment_start_times()
        self.update_summary()
        self.sync_selected_duration_control()

    def finish_resize(self, segment_id: str) -> None:
        self._timeline_edge_drag = None
        self.update_timeline_layout()
        self.sync_selected_duration_control()
        self.update_conditioning_guide()

    def timeline_boundary_available(self, row: int) -> bool:
        return (not self.ai_busy and not getattr(self, "timeline_review_pending", set())
                and all(item.artwork_status != "generating" for item in self.segments[row:row + 2]))

    def begin_timeline_edge_drag(self, segment_id: str) -> None:
        self._timeline_edge_drag = None
        row = next((i for i, item in enumerate(self.segments) if item.id == segment_id), -1)
        if row < 0 or not self.timeline_boundary_available(row):
            return
        animation = getattr(self, "duration_animation", None)
        if animation is not None:
            animation.stop()
            self.update_timeline_layout()
        self._timeline_edge_drag = (
            (self.workspace_revision, self.current_project_id, self.project_type),
            copy.deepcopy(self.segments[row]),
            copy.deepcopy(self.segments[row + 1]) if row + 1 < len(self.segments) else None)

    def resize_timeline_edge(self, segment_id: str, value: float) -> None:
        row = next((i for i, item in enumerate(self.segments) if item.id == segment_id), -1)
        if row < 0 or not self.timeline_boundary_available(row):
            return
        snapshot = getattr(self, "_timeline_edge_drag", None)
        if snapshot is None:
            return
        workspace, before_left, before_right = snapshot
        if (workspace != (self.workspace_revision, self.current_project_id, self.project_type)
                or segment_id != before_left.id):
            return
        if row == len(self.segments) - 1:
            if before_right is None:
                self.change_duration(segment_id, value)
            return
        left, right = self.segments[row:row + 2]
        if before_right is None or right.id != before_right.id:
            return
        try:
            updated = roll_segment_boundary(before_left, before_right, value - before_left.duration, FPS)
        except ValueError:
            return
        changed = False
        for segment, target in zip((left, right), updated):
            for field in ("duration", "trim_start", "shot_trimmed"):
                if getattr(segment, field) != getattr(target, field):
                    setattr(segment, field, getattr(target, field))
                    changed = True
        if not changed:
            return
        self.retime_unified_prompt()
        self.mark_dirty()
        self.update_timeline_layout()
        self.sync_selected_duration_control()
        boundary = sum(item.duration for item in self.segments[:row + 1])
        self.statusBar().showMessage(
            f"Boundary {boundary:.2f}s · {left.duration:.2f}s / {right.duration:.2f}s · total length preserved")

    def update_summary(self) -> None:
        if self.workspace_engine == "shot_sequence":
            self.workflow_label.setText("Completed shots · RAM playback · source audio")
            self.magic_button.hide()
            self.statusBar().showMessage("Shot Sequence · completed movies with source audio")
            return
        if self.prompt_only_workspace:
            self.add_tile.setEnabled(False)
            self.add_text_tile.setEnabled(False)
            self.statusBar().showMessage("Prompt refinement · annotations only")
            return
        if self.minimax_panel:
            self.minimax_panel.refresh_references()
        total = self.total_duration()
        self.sequence_bar.setText(f"{total:.2f}s · {len(self.segments)}")
        self.sequence_bar.setToolTip(
            f"Sequence begins at 0.00 seconds and ends at {total:.2f} seconds "
            f"({len(self.segments)} segments)."
        )
        self.add_tile.setText("＋")
        self.add_tile.setEnabled(True)
        self.add_text_tile.setEnabled(True)
        self.applied_label.setText(f"Applied across all {len(self.segments)} segments")
        visual_count = sum(segment.kind != "text" for segment in self.segments)
        text_count = len(self.segments) - visual_count
        self.statusBar().showMessage(f"{visual_count} visual · {text_count} text · {total:.2f}s")

    def update_counts(self) -> None:
        self.segment_count.setText(f"{len(self.segment_prompt.toPlainText()):,} chars")
        self.unified_count.setText(f"{len(self.minimax_prompt_text):,} chars")
        self.global_count.setText(f"{len(self.global_prompt.toPlainText())} characters")

    def update_provider_button(self) -> None:
        provider = self.settings.value("provider", "gemini")
        label = (
            self.settings.value("gemini_model", GEMINI_MODELS[0]) if provider == "gemini" else
            self.settings.value("chatgpt_model", "ChatGPT") if provider == "chatgpt" else
            "Local writing model" if provider == "local" else "OpenAI"
        )
        self.provider_button.setText(f"✦ {label.replace('gemini-', 'Gemini ').replace('-', ' ').title() if provider == 'gemini' else label}")

    def timeline_menu(self, point) -> None:
        if self.ai_busy:
            return
        item = self.timeline.itemAt(point)
        if not item:
            return
        self.timeline.setCurrentItem(item)
        segment = self.current_segment()
        menu = QMenu(self)
        if segment:
            rect = self.timeline.visualItemRect(item)
            offset = segment.duration * (point.x() - rect.left()) / max(1, rect.width())
            split_menu = menu.addMenu("Split segment")
            split_action = split_menu.addAction("At clicked position")
            split_action.setToolTip("Divide this segment at the clicked position; keep its media and prompt on both sides.")
            try:
                split_segment(segment, offset, FPS)
                can_split = True
            except ValueError:
                can_split = False
            if getattr(self, "timeline_review_pending", set()):
                can_split = False
                split_action.setToolTip("Approve or deny pending timeline edits before splitting.")
            if segment.artwork_status == "generating":
                can_split = False
                split_action.setToolTip("Wait for this segment's artwork to finish before splitting.")
            can_split = can_split and self.timeline_count_allowed(len(self.segments) + 1, report=False)
            split_action.setEnabled(can_split)
            split_action.triggered.connect(
                lambda checked=False, sid=segment.id, at=offset: self.split_timeline_segment(sid, at))
            for count in (2, 3):
                choices = split_menu.addMenu(f"{count}-way split")
                try:
                    split_segment_parts(segment, count, FPS)
                    available = (not getattr(self, "timeline_review_pending", set())
                                 and segment.artwork_status != "generating"
                                 and self.timeline_count_allowed(len(self.segments) + count - 1, report=False))
                except ValueError:
                    available = False
                choices.setEnabled(available)
                for title, style, tip in (
                    ("Keep prompt", "keep", "Equal parts with the same prompt and consecutive source intervals."),
                    ("Divide prompt with AI", "divide", "Distribute the written action beats across equal parts; review before keeping the result."),
                    ("Hard cuts + close-ups with AI", "cuts", "Split action beats into separate shots, with hard cuts to close-ups; review the result."),
                ):
                    action = choices.addAction(title)
                    action.setToolTip(tip)
                    action.setEnabled(available and (style == "keep" or bool(segment.prompt.strip())))
                    action.triggered.connect(
                        lambda checked=False, sid=segment.id, n=count, mode=style:
                        self.split_timeline_segment_parts(sid, n, mode))
            menu.addSeparator()
        if segment:
            artwork_generate = menu.addAction("✦ Generate Artwork")
            artwork_generate.setToolTip(
                "Create artwork from this segment's prompt using neighboring references; "
                "existing image segments are edited using their own original image.")
            artwork_generate.setEnabled(
                segment.kind in {"text", "image"}
                and segment.artwork_status != "generating")
            artwork_generate.triggered.connect(
                lambda checked=False, sid=segment.id: self.generate_artwork_for_segment(sid))
            menu.addSeparator()
        if segment and segment.artwork_prompt and segment.artwork_status in {"awaiting", "failed"}:
            artwork_action = menu.addAction(
                "✦ Render requested artwork" if segment.artwork_status == "awaiting"
                else "↻ Retry artwork generation", self.retry_selected_artwork
            )
            artwork_action.setToolTip("Use your configured image-generation model and API key")
            menu.addSeparator()
        if segment and segment.kind == "text":
            if not self.unified_workspace:
                menu.addAction("Edit text prompt", self.focus_segment_prompt)
            menu.addAction("Convert to image segment…", self.convert_text_segment_to_image)
        else:
            if segment:
                convert = menu.addAction("Convert to text segment")
                convert.setToolTip("Keep this segment's prompt and timing as an editable text beat.")
                convert.setEnabled(not getattr(self, "timeline_review_pending", set())
                                   and segment.artwork_status != "generating")
                convert.triggered.connect(self.convert_selected_segment_to_text)
            menu.addAction("Replace media", self.replace_selected)
            if segment and segment.kind == "image":
                menu.addAction("Copy image", self.copy_selected_image)
                paste_action = menu.addAction("Paste image to replace", self.paste_image_to_replace)
                paste_action.setEnabled(QApplication.clipboard().mimeData().hasImage())
            menu.addAction("Export video" if segment and segment.kind == "video" else "Export image", self.export_selected_segment)
            menu.addAction("Set as start frame", lambda: self.set_role("start"))
            menu.addAction("Set as end frame", lambda: self.set_role("end"))
        menu.addSeparator()
        menu.addAction("Delete segment", self.delete_selected)
        menu.exec(self.timeline.viewport().mapToGlobal(point))

    def split_timeline_segment(self, segment_id: str, offset: float) -> None:
        if (self.ai_busy or getattr(self, "timeline_review_pending", set())
                or not self.timeline_count_allowed(len(self.segments) + 1)):
            return
        row = next((i for i, segment in enumerate(self.segments) if segment.id == segment_id), -1)
        if row < 0 or self.segments[row].artwork_status == "generating":
            return
        try:
            halves = split_segment(self.segments[row], offset, FPS)
        except ValueError:
            return
        self.segments[row:row + 1] = halves
        self.mark_dirty()
        self.refresh_timeline(row + 1)
        self.statusBar().showMessage("Segment split; media, prompts and total sequence duration preserved")

    def split_timeline_segment_parts(self, segment_id: str, count: int, style: str = "keep") -> None:
        if (self.ai_busy or getattr(self, "timeline_review_pending", set())
                or style not in {"keep", "divide", "cuts"}
                or not self.timeline_count_allowed(len(self.segments) + count - 1)):
            return
        self.finish_prompt_reviews()
        self.inline_typing.finish()
        row = next((i for i, item in enumerate(self.segments) if item.id == segment_id), -1)
        if row < 0 or self.segments[row].artwork_status == "generating":
            return
        try:
            parts = split_segment_parts(self.segments[row], count, FPS)
        except ValueError:
            return
        if style == "keep":
            self.segments[row:row + 1] = parts
            self.mark_dirty()
            self.refresh_timeline(row + 1)
            self.statusBar().showMessage(f"Segment split into {count} equal parts; total duration preserved")
            return
        if not self.segments[row].prompt.strip():
            return
        credentials = self.ai_credentials()
        if not credentials:
            return
        self.timeline.setCurrentRow(row)
        before = copy.deepcopy(self.segments)
        provider, model, key = credentials
        self.start_ai_worker(
            divide_segment_prompt,
            (before, row, count, style, provider, model, key,
             self.settings.value("api_timeout", 400, int)),
            f"Splitting segment into {count} {'shots with close-ups' if style == 'cuts' else 'action beats'}…",
            lambda result: self.finish_segment_split(before, row, count, result),
            review_text=False,
        )

    def finish_segment_split(self, before: list[Segment], row: int, count: int, prompts: list[str]) -> None:
        self.set_ai_controls_enabled(True)
        if [item.to_dict() for item in self.segments] != [item.to_dict() for item in before]:
            self.statusBar().showMessage("Split discarded because the timeline changed. Try again from the current segment.")
            return
        if not self.timeline_count_allowed(len(self.segments) + count - 1):
            return
        parts = split_segment_parts(before[row], count, FPS)
        if (not isinstance(prompts, list) or len(prompts) != count
                or any(not isinstance(prompt, str) or not prompt.strip() for prompt in prompts)):
            self.statusBar().showMessage("Split returned invalid prompts; timeline preserved.")
            return
        for part, prompt in zip(parts, prompts):
            part.prompt = prompt
        self.segments[row:row + 1] = parts
        self.mark_dirty()
        self.refresh_timeline(row)
        self._start_segment_review(before, self.segments, [{"kind": "split", "id": parts[0].id}])
        # All parts form one edit: accepting or denying one card resolves the
        # entire split so partial rejection cannot leave gaps or duplicate time.
        self.timeline_split_review_ids = {part.id for part in parts}
        self.timeline_review_pending = set(self.timeline_split_review_ids)
        self._show_segment_review_controls()
        self.statusBar().showMessage("Review split: approve any part to keep all parts, or deny to restore the original segment.")

    def focus_segment_prompt(self) -> None:
        self.prompt_scope.setCurrentIndex(1)
        (self.intent_segment_prompt if self.unified_workspace
         else self.segment_prompt).setFocus()

    def copy_selected_image(self) -> None:
        segment = self.current_segment()
        if not segment or segment.kind != "image":
            return
        source = Path(materialize_source(segment))
        if not source.is_file():
            QMessageBox.warning(self, "Image missing", "The full-resolution source image could not be found.")
            return
        reader = QImageReader(str(source))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            QMessageBox.warning(self, "Copy failed", "The full-resolution source image could not be decoded.")
            return
        QApplication.clipboard().setImage(image)
        self.statusBar().showMessage(f"Full-resolution image copied: {image.width()} × {image.height()}")

    def paste_image_to_replace(self) -> None:
        segment = self.current_segment()
        if not segment or segment.kind != "image":
            return
        image = QApplication.clipboard().image()
        if image.isNull():
            QMessageBox.information(self, "No clipboard image", "Copy an image to the clipboard first.")
            return
        destination = APP_CACHE / f"clipboard-{uuid4().hex}.png"
        if not image.save(str(destination), "PNG"):
            QMessageBox.warning(self, "Paste failed", "The clipboard image could not be saved.")
            return
        row = self.timeline.currentRow()
        segment.name = f"clipboard-{datetime.now().strftime('%Y%m%d-%H%M%S')}.png"
        segment.media_path = str(destination)
        segment.preview_path = thumbnail_for_image(str(destination))
        segment.media_duration_frames = None
        segment.trim_start = None
        clear_archived_source(segment)
        self.mark_dirty()
        self.refresh_timeline(row)
        self.statusBar().showMessage(f"Image replaced from clipboard at full resolution: {image.width()} × {image.height()}")

    def convert_text_segment_to_image(self) -> None:
        segment = self.current_segment()
        if not segment or segment.kind != "text":
            return
        initial = str(self.settings.value("last_media_dir", str(Path.home())))
        path = choose_document_open(self, "Choose image for text segment", initial, "Images (*.png *.jpg *.jpeg *.webp *.gif)")
        if not path:
            return
        try:
            kind, preview, frames, trim = prepare_media(path)
        except Exception as error:
            QMessageBox.warning(self, "Image error", f"{Path(path).name}: {error}")
            return
        if kind != "image":
            QMessageBox.warning(self, "Image required", "Choose a PNG, JPEG, WebP, or GIF image.")
            return
        row = self.timeline.currentRow()
        segment.name = Path(path).name
        segment.media_path = path
        segment.preview_path = preview
        segment.kind = "image"
        segment.role = "end" if row % 2 else "start"
        segment.media_duration_frames = frames
        segment.trim_start = trim
        clear_archived_source(segment)
        self.settings.setValue("last_media_dir", str(Path(path).parent))
        self.mark_dirty()
        self.refresh_timeline(row)
        self.statusBar().showMessage(f"Text segment converted to image: {segment.name}; prompt preserved")

    def convert_selected_segment_to_text(self) -> None:
        segment = self.current_segment()
        if (self.ai_busy or not segment or segment.kind not in {"image", "video"}
                or segment.artwork_status == "generating"
                or getattr(self, "timeline_review_pending", set())):
            return
        self.finish_prompt_reviews()
        self.inline_typing.finish()
        segment.media_path = segment.preview_path = ""
        segment.kind = segment.role = "text"
        segment.media_duration_frames = segment.trim_start = None
        segment.shot_trimmed = False
        segment.artwork_prompt = segment.artwork_status = ""
        segment.artwork_target_role = "start"
        clear_archived_source(segment)
        self.mark_dirty()
        self.refresh_timeline(self.timeline.currentRow())
        self.statusBar().showMessage("Converted to text segment; prompt and timing preserved")

    def export_selected_segment(self) -> None:
        segment = self.current_segment()
        if not segment:
            return
        row = max(0, self.timeline.currentRow()) + 1
        self.export_segment(segment,row)

    def export_segment(self,segment: Segment,row: int) -> None:
        suffix = segment_media_suffix(segment)
        target = self.next_export_path(f"{self.current_project_name} - Segment {row:02d}{suffix}")
        self.start_disk_job(_export_segment_source, (copy.deepcopy(segment), target), lambda _result: self.record_export(target),
                            lambda message: self.export_failed(target, message), serialized=True)

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
            clear_archived_source(segment)
            self.mark_dirty()
            self.refresh_timeline(self.timeline.currentRow())
            self.statusBar().showMessage("Media replaced; timing, role and prompt preserved")
        except Exception as error:
            QMessageBox.critical(self, "Replace failed", str(error))

    def delete_selected(self) -> None:
        if self.prompt_only_workspace:
            return
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

    def open_interactive_help(self):
        from .onboarding import open_manual
        open_manual()

    def show_onboarding(self):
        from .onboarding import OnboardingWizard
        wizard = getattr(self, "onboarding_wizard", None)
        if wizard and wizard.isVisible(): wizard.raise_(); return
        self.onboarding_wizard = OnboardingWizard(self)
        self.onboarding_wizard.show()

    def maybe_show_onboarding(self):
        if (not self.settings.value("onboarding/seen", False, bool)
                and not self.settings.contains("provider") and not self.settings.contains("last_project_id")):
            self.show_onboarding()

    def open_settings(self) -> None:
        if SettingsDialog(self.settings, self).exec():
            value = self.settings.value("ui_text_scale", 100, int)
            self.ui_scale_spin.blockSignals(True)
            self.ui_scale_spin.setValue(value)
            self.ui_scale_spin.blockSignals(False)
            self._apply_theme()
            self.update_provider_button()
            self.rebuild_project_filter_buttons()
            self.refresh_project_library()

    def set_ui_text_scale(self, value: int) -> None:
        value = max(75, min(200, value))
        self.settings.setValue("ui_text_scale", value)
        self._apply_theme()

    def update_spoken_dialog_controls(self, checked: bool) -> None:
        self.update_spoken_dialog_visibility()
        self.speaker_language.setEnabled(checked)
        self.speaker_accent.setEnabled(checked)
        self.mark_dirty()

    def build_director_request(self, *, include_requested_length: bool = True) -> str:
        """Compose focused planning controls into the authoritative model request."""
        lines = []
        creative_intent = self.intent.toPlainText().strip()
        if creative_intent:
            lines.append(creative_intent)
        if self.unified_workspace and self.hdr.isChecked():
            lines.append("HDR: preserve a high dynamic range cinematic look and visible highlight and shadow detail.")
        if not self.unified_workspace and not self.workspace_definition.get("global_prompt", True):
            lines.append("This workspace uses per-segment prompts only. Return an empty globalPrompt and keep required continuity in each segment prompt.")
        if not self.workspace_definition.get("audio_generation", True):
            lines.append("This workspace does not generate audio. Omit sound, music, speech and dialogue generation instructions.")
        requested_length = self.requested_length.value()
        if include_requested_length and requested_length > 0:
            if len(self.segments) == 1:
                item_label = "single text-only segment" if self.segments[0].kind == "text" else "single-frame sequence"
                lines.append(
                    f"Requested total sequence length: {requested_length:.1f} seconds. "
                    f"Because this is a {item_label}, return this exact duration for its one segment."
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
                "In every Spoken Dialog clause, state the selected or context-supported language and accent directly beside the quoted words. Do not leave language or accent inference to the video model, and avoid stereotypes or caricature."
            )
        return "\n\n".join(lines)

    def build_refinement_request(self) -> str:
        lines = [self.build_director_request(include_requested_length=False)]
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

    def ai_credentials(self) -> tuple[str, str, object] | None:
        provider = self.settings.value("provider", "gemini")
        if provider == "local":
            profile = load_profile(self.settings)
            try: validate_profile(profile, "text")
            except ValueError:
                self.open_settings()
                profile = load_profile(self.settings)
                try: validate_profile(profile, "text")
                except ValueError: return None
            return "local", profile["llm_model"], profile
        if provider == "chatgpt":
            profile = self.chatgpt_auth.active_profile()
            model = str(self.settings.value("chatgpt_model", ""))
            if not (profile and profile["connected"] and profile["enabled"] and model):
                self.open_settings()
                profile = self.chatgpt_auth.active_profile()
                model = str(self.settings.value("chatgpt_model", ""))
            if not (profile and profile["connected"] and profile["enabled"] and model):
                return None
            # Access tokens are refreshed by the AI worker to avoid blocking Qt.
            return provider, model, self.chatgpt_auth
        key = self.session_keys.get(provider) or self.settings.value(f"{provider}_key", "")
        if not key:
            self.open_settings()
            key = self.session_keys.get(provider) or self.settings.value(f"{provider}_key", "")
        if not key:
            return None
        return provider, (
            self.settings.value("openai_model", "gpt-5.4-mini") if provider == "openai"
            else self.settings.value("gemini_model", GEMINI_MODELS[0])
        ), key

    def finish_prompt_reviews(self) -> None:
        review = getattr(self, "paragraph_review", None)
        if review is not None:
            review.finish()

    def make_quick_fix_button(self, accessible_name: str, editor: str) -> QPushButton:
        button = QPushButton("Quick Fix")
        button.setObjectName("quickFixAction")
        button.setAccessibleName(accessible_name)
        button.setToolTip("Insert a saved instruction and refine this editor only")
        menu = QMenu(button)
        menu.aboutToShow.connect(lambda: self.populate_quick_fix_menu(menu, editor))
        button.setMenu(menu)
        return button

    def quick_fix_available(self, editor: str) -> bool:
        if self.ai_busy or self.workspace_engine == "shot_sequence":
            return False
        if editor == "segment":
            segment = self.current_segment()
            return not self.prompt_only_workspace and segment is not None and bool(segment.prompt.strip())
        if editor == "unified":
            return self.unified_workspace and bool(self.minimax_prompt_text.strip()) and (bool(self.segments) or self.prompt_only_workspace)
        return False

    def populate_quick_fix_menu(self, menu: QMenu, editor: str) -> None:
        menu.clear()
        for index, (category, presets) in enumerate(preset_groups([e for e in load_effects(self.settings) if e.get("show_in_menu") and e.get("prompt")], editor)):
            if index:
                menu.addSeparator()
            heading = menu.addAction(category)
            heading.setEnabled(False)
            for preset in presets:
                action = menu.addAction(preset["name"])
                action.setToolTip(preset["prompt"])
                action.setEnabled(self.quick_fix_available(editor))
                action.triggered.connect(lambda _=False, value=preset: self.apply_quick_fix(value, editor))
        if menu.actions():
            menu.addSeparator()
        menu.addAction("Effects Library…", lambda: self.open_effects_library(editor))
        menu.setToolTipsVisible(True)

    def edit_quick_fixes(self) -> None:
        self.open_effects_library("segment")

    def apply_library_effect(self, entry, editor):
        self.apply_quick_fix({**entry, "prompt": refinement(entry)}, editor)

    def open_effects_library(self, editor="segment"):
        self.effects_panel.scope.setCurrentIndex(self.effects_panel.scope.findData(editor))
        self.effects_dock.show()
        self.effects_dock.raise_()

    def apply_quick_fix(self, preset: dict, editor: str) -> None:
        instruction = str(preset.get("prompt") or "").strip()
        if (preset.get("target") not in (editor, "both") or not instruction
                or not self.quick_fix_available(editor)):
            return
        self.finish_prompt_reviews()
        self.inline_typing.finish()
        if editor == "segment":
            self.prompt_scope.setCurrentIndex(self.prompt_scope.findData("segment"))
            target = self.intent_segment_prompt if self.unified_workspace else self.segment_prompt
        else:
            target = self.unified_prompt
        baseline = target.toPlainText()
        cursor = QTextCursor(target.document())
        cursor.movePosition(QTextCursor.MoveOperation.End if editor == "segment" else QTextCursor.MoveOperation.Start)
        if editor == "segment":
            cursor.insertText("\n\n")
        insert_note(target, cursor, "/refine-global", instruction)
        if editor == "segment":
            self.refine_selected_prompt()
        else:
            self.refine_minimax_prompt()
        if self.ai_busy:
            # Review the prose change, not deletion of the injected instruction.
            self.ai_review_baseline = baseline

    def set_ai_controls_enabled(self, enabled: bool) -> None:
        if not enabled:
            self.reject_segment_prompt_review()
        self.ai_busy = not enabled
        self.update_sync_timeline_enabled()
        # While streaming, every text source and timeline mutation control is
        # locked. Viewing/scrolling the timeline is still allowed.
        for editor in (self.unified_prompt, self.segment_prompt,
                       self.global_prompt, self.intent, self.intent_segment_prompt):
            editor.setReadOnly(not enabled)
        for widget in (self.prompt_scope, self.project_type_combo, self.duration_spin,
                       self.start_button, self.end_button, self.voice_options_button, self.requested_length,
                       self.timeline_approve_all, self.timeline_deny_all,
                       self.add_tile, self.add_text_tile,
                       self.hdr, self.sfx, self.spoken_dialog, self.reduce_music,
                       self.output_width, self.output_height, self.references_button):
            widget.setEnabled(enabled)
        self.timeline.setDragEnabled(enabled)
        self.timeline.setAcceptDrops(enabled)
        for row in range(self.timeline.count()):
            card = self.timeline.itemWidget(self.timeline.item(row))
            if isinstance(card, SegmentCard):
                card.resize_handle.setEnabled(enabled and self.timeline_boundary_available(row))
                card.review_accept.setEnabled(enabled)
                card.review_reject.setEnabled(enabled)
                for button in card.findChildren(QPushButton, "tileDelete"):
                    button.setEnabled(enabled)
        if enabled:
            self.update_summary()
        segment = self.current_segment()
        self.refine_timing_button.setEnabled(enabled and not self.prompt_only_workspace and bool(segment))
        self.update_refine_prompt_enabled()
        target = getattr(self, "ai_request_editor", None)
        if not hasattr(self, "ai_request_editor"):
            target = self.unified_prompt if self.unified_workspace else self.segment_prompt
        if self.minimax_panel:
            self.minimax_panel.set_busy(not enabled, show_progress=target is self.unified_prompt)
        if target in {self.intent, self.intent_segment_prompt} and not enabled:
            self.director_ai_marquee.begin(getattr(self, "ai_request_activity", "✦ AI working…"))
        else:
            self.director_ai_marquee.finish()
        # The LTX segment editor shares exactly the same blue progress style.
        if hasattr(self, "segment_ai_marquee"):
            if not enabled and target is self.segment_prompt and not self.prompt_only_workspace:
                self.segment_ai_marquee.begin(
                    "✦ Refining segment…" if self.prompt_scope.currentData() == "segment"
                    else "✦ Refining prompt…"
                )
            else:
                self.segment_ai_marquee.finish()
        self.refresh_primary_ai_action()

    def ai_editor_target(self, operation):
        """Choose the editor that owns streaming, progress, and final approvals."""
        if operation in {
            generate_combined, sync_combined_from_timeline,
            build_minimax_h3_prompt, build_minimax_h3_reference_prompt,
            refine_minimax_h3_prompt, refine_minimax_h3_reference_prompt,
            refine_minimax_text_prompt, build_generic_prompt, refine_generic_prompt,
        } and self.unified_workspace:
            return self.unified_prompt, "unified", 0
        if operation in {refine_segment_prompt, generate_segment_prompt, refine_generic_segment, refine_timing, divide_segment_prompt}:
            if self.unified_workspace:
                self.prompt_scope.setCurrentIndex(self.prompt_scope.findData("segment"))
            return (self.intent_segment_prompt if self.unified_workspace else self.segment_prompt), "segment", 0
        if operation in {refine_global_prompt, generate_global_direction, refine_sequence_prompts,
                         build_prompts, build_generic_segments}:
            kind = "global" if self.prompt_scope.currentData() == "global" else "segment"
            target = (self.intent if kind == "global" else self.intent_segment_prompt) if self.unified_workspace else self.segment_prompt
            return target, kind, max(0, self.timeline.currentRow()) if kind == "segment" else 0
        return None, "unified", 0

    def start_ai_worker(self, operation, args: tuple, activity: str, finished, show_main_overlay: bool = True,
                        *, review_text: bool = True) -> None:
        if self.ai_busy:
            return
        self.finish_prompt_reviews()
        self.inline_typing.finish()
        target, kind, selected_index = self.ai_editor_target(operation)
        self.ai_request_editor = target
        self.ai_request_activity = activity
        self.ai_review_text = review_text
        self.ai_review_baseline = (self.minimax_prompt_text if target is self.unified_prompt
                                   else target.toPlainText() if target is not None else "")
        self.ai_review_segment_id = self.current_segment().id if kind == "segment" and self.current_segment() else None
        retries = self.settings.value("api_retries", 2, int)
        retry_cooldown = self.settings.value("api_retry_cooldown", 10, int)
        self.set_ai_controls_enabled(False)
        self.ai_activity_title = (
            "Generic Generate" if operation in {build_generic_prompt, build_generic_segments} else
            "Generic Refine" if operation in {refine_generic_prompt, refine_generic_segment} else
            "Magic Build" if operation is build_prompts else
            "MiniMax H3 Reference Generate" if operation is build_minimax_h3_reference_prompt else
            "MiniMax H3 Frame Generate" if operation is build_minimax_h3_prompt else
            "MiniMax H3 Reference Refine" if operation is refine_minimax_h3_reference_prompt else
            "MiniMax H3 Frame Refine" if operation is refine_minimax_h3_prompt else
            "MiniMax Prompt Refinement" if operation is refine_minimax_text_prompt else
            "MiniMax Timeline Sync" if operation is sync_timeline_to_prompt else
            "MiniMax Combined Sync" if operation is sync_combined_from_timeline else
            "Selected Segment Generate" if operation is generate_segment_prompt else
            "Segment Split" if operation is divide_segment_prompt else
            "Timeline Timing" if operation is plan_sequence_timing else
            "Global Direction Generate" if operation is generate_global_direction else
            "Sequence-Wide Magic Build Refine" if operation is refine_sequence_prompts else
            "Refine Timing" if operation is refine_timing else
            "Refine Prompt"
        )
        self.ai_activity_in_minimax = not show_main_overlay
        # Never cover the real prompt editor with a duplicate streaming window.
        # All text edits animate inline; reasoning is in a dockable side panel.
        self.magic_overlay.hide_overlay()
        instructions = (None if self.unified_workspace and operation in {build_prompts, refine_segment_prompt, refine_global_prompt, refine_timing}
                        else copy.deepcopy(self.workspace_definition.get("generation_prompts", {})))
        maximum_shots = shot_limit(self.workspace_definition)
        def workspace_operation(*values):
            token = WORKSPACE_INSTRUCTIONS.set(instructions)
            shot_token = SHOT_COUNT_LIMIT.set(maximum_shots)
            stream_token = STREAM_SINK.set(worker.signals.stream.emit)
            try:
                return operation(*values)
            finally:
                STREAM_SINK.reset(stream_token)
                SHOT_COUNT_LIMIT.reset(shot_token)
                WORKSPACE_INSTRUCTIONS.reset(token)
        worker = MagicWorker(workspace_operation, args, retries, retry_cooldown, activity)
        # Internal/custom worker calls may legitimately have no model args.
        model = (
            str(args[5]) if operation is divide_segment_prompt else
            str(args[1]) if len(args) >= 2 and isinstance(args[0], str) else
            str(args[2]) if len(args) >= 3 else
            "Background task"
        )
        self.active_live_panel = self.reasoning_dock
        self.active_live_panel.begin(activity, model)
        if review_text and target is not None and target.isVisible():
            # Save the authoritative text before any temporary red diff is shown.
            self.inline_typing.start(target, self.ai_review_baseline, kind, selected_index)
        worker.signals.stream.connect(self.ai_stream_received)
        worker.signals.progress.connect(self.magic_progress)
        self.ai_request_workspace = (self.workspace_revision, self.current_project_id, self.project_type)
        self.ai_finished_callback = finished
        # Bound QObject receivers keep all UI updates on the main Qt thread.
        worker.signals.finished.connect(self.ai_result_received)
        worker.signals.failed.connect(self.ai_failure_received)
        self.thread_pool.start(worker)

    def start_disk_job(self, operation, args: tuple, finished, failed, *, serialized: bool = False, progress=None, cancel=None) -> None:
        """Execute filesystem work without touching widgets outside the GUI thread."""
        worker = BackgroundWorker(operation, args, cancel)
        self._pending_disk_jobs += 1
        callbacks = BackgroundCallbacks(self, finished, failed, progress)
        worker.signals.finished.connect(callbacks.on_finished)
        worker.signals.failed.connect(callbacks.on_failed)
        worker.signals.local_event.connect(callbacks.on_local_event)
        (self.disk_pool if serialized else self.media_pool).start(worker)

    def ai_request_is_current(self) -> bool:
        return getattr(self, "ai_request_workspace", None) == (self.workspace_revision, self.current_project_id, self.project_type)

    def discard_obsolete_ai_result(self) -> None:
        self.inline_typing.finish()
        self.ai_plan_stream_active = False
        if hasattr(self, "_ai_plan_timer"):
            self._ai_plan_timer.stop()
        self._clear_ai_timeline_preview()
        self.set_ai_controls_enabled(True)
        self.magic_overlay.hide_overlay()
        self.statusBar().showMessage("AI result discarded because the project or workflow changed.")

    def _flush_ai_plan_preview(self) -> None:
        if not getattr(self, "ai_plan_stream_active", False):
            return
        records = completed_stream_records(self.ai_plan_raw)
        if len(records) != getattr(self, "ai_plan_record_count", 0):
            self.ai_plan_record_count = len(records)
            self.preview_ai_timeline_operations(
                stream_operations(self.ai_segment_snapshot, self.ai_plan_raw)
            )

    def ai_stream_received(self, kind: str, text: str) -> None:
        if not self.ai_request_is_current():
            return
        pane = getattr(self, "active_live_panel", None)
        if pane is not None:
            pane.receive_stream(kind, text)
            if kind == "reasoning" and text and not pane.isVisible():
                pane.show()
        self.inline_typing.receive(kind, text)
        if kind == "reset" and getattr(self, "ai_plan_stream_active", False):
            # An automatic format repair is a fresh provider stream. Do not
            # concatenate its JSON with the previous incomplete reply.
            self.ai_plan_raw = ""
            self.ai_plan_record_count = 0
            if hasattr(self, "_ai_plan_timer"):
                self._ai_plan_timer.stop()
            self._clear_ai_timeline_preview()
        if kind == "output" and getattr(self, "ai_plan_stream_active", False):
            self.ai_plan_raw += text
            if '"segments"' in self.ai_plan_raw and not self._ai_plan_timer.isActive():
                self._ai_plan_timer.start()

    def ai_result_received(self, result) -> None:
        # Keep the exact pre-generation draft for paragraph-level review.
        review_editor = getattr(self, "ai_request_editor", None)
        review_baseline = getattr(self, "ai_review_baseline", "")
        if review_editor is None and self.inline_typing.active:
            review_editor = self.inline_typing.editor
            review_baseline = self.inline_typing.baseline
        segment_id = getattr(self, "ai_review_segment_id", None)
        if (self.inline_typing.suspended or (segment_id and
                (self.current_segment() is None or self.current_segment().id != segment_id))):
            review_editor = None
        self.inline_typing.finish()
        self.ai_plan_stream_active = False
        if hasattr(self, "_ai_plan_timer"):
            self._ai_plan_timer.stop()
        if not self.ai_request_is_current():
            self.discard_obsolete_ai_result()
            return
        if getattr(self, "active_live_panel", None) is not None:
            self.active_live_panel.finish(True)
        self.ai_finished_callback(result)
        if (getattr(self, "ai_review_text", True) and review_editor is not None
                and review_editor.isVisible() and not self.ai_busy
                and self.ai_request_is_current()):
            self.paragraph_review.begin(
                review_editor, review_baseline, review_editor.toPlainText()
            )

    def ai_failure_received(self, error: str) -> None:
        self.inline_typing.finish()
        self.ai_plan_stream_active = False
        if hasattr(self, "_ai_plan_timer"):
            self._ai_plan_timer.stop()
        self._clear_ai_timeline_preview()
        if not self.ai_request_is_current():
            self.discard_obsolete_ai_result()
            return
        if getattr(self, "active_live_panel", None) is not None:
            self.active_live_panel.finish(False)
        self.magic_failed(error)

    def refine_selected_timing(self) -> None:
        if self.prompt_only_workspace:
            return
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

    def retime_all_segments(self) -> None:
        if (self.ai_busy or self.prompt_only_workspace or self.workspace_engine == "shot_sequence"
                or len(self.segments) < 2 or getattr(self, "timeline_review_pending", set())
                or any(item.artwork_status == "generating" for item in self.segments)):
            return
        self.finish_prompt_reviews()
        self.inline_typing.finish()
        target = self.requested_length.value() or self.total_duration()
        maximum = self.maximum_sequence_duration
        if maximum is not None and target > maximum + .000001:
            self.statusBar().showMessage(
                f"Requested {target:.2f}s exceeds this workspace's {maximum:.2f}s limit. Adjust sequence length or workspace settings.", 9000)
            return
        try:
            retimed_segments(self.segments, [item.duration for item in self.segments], target)
        except ValueError as error:
            self.statusBar().showMessage(str(error), 9000)
            return
        credentials = self.ai_credentials()
        if not credentials:
            return
        before = copy.deepcopy(self.segments)
        references = copy.deepcopy(self.active_reference_images())
        provider, model, key = credentials
        self.ai_timeline_status.setText("◷ Analyzing all timing…")
        self.ai_timeline_status.show()
        self.start_ai_worker(
            plan_sequence_timing,
            (before, provider, model, key, target, self.build_refinement_request(),
             self.minimax_prompt_text, references, self.settings.value("api_timeout", 400, int)),
            f"Analyzing dialogue, action and shot pacing for {target:.2f}s; timing only…",
            lambda result: self.finish_sequence_timing(before, references, target, result),
            review_text=False,
        )

    def finish_sequence_timing(self, before: list[Segment], references: list, target: float, result: dict) -> None:
        self.set_ai_controls_enabled(True)
        self.ai_timeline_status.hide()
        if ([item.to_dict() for item in self.segments] != [item.to_dict() for item in before]
                or self.active_reference_images() != references):
            self.statusBar().showMessage("Timing proposal discarded because the timeline or references changed. Try again.", 9000)
            return
        try:
            updated = retimed_segments(before, result.get("durations") if isinstance(result, dict) else None, target)
        except ValueError as error:
            self.statusBar().showMessage(str(error), 9000)
            return
        changes = [{"kind": "resize", "id": old.id} for old, new in zip(before, updated)
                   if any(getattr(old, field) != getattr(new, field) for field in TIMING_FIELDS)]
        if not changes:
            self.statusBar().showMessage("Timing already matches this proposal; prompt text preserved.", 7000)
            return
        for item, proposed in zip(self.segments, updated):
            item.trim_start, item.shot_trimmed = proposed.trim_start, proposed.shot_trimmed
        self.animate_timeline_durations([item.duration for item in updated])
        self._start_segment_review(before, self.segments, changes)
        self.timeline_timing_review_ids = {change["id"] for change in changes}
        self._show_segment_review_controls()
        self.update_conditioning_guide()
        self.statusBar().showMessage(
            f"Timing adjusted to {target:.2f}s. Review or revert the timing changes; all prompt text preserved.", 10000)

    def refine_selected_prompt(self) -> None:
        if self.ai_busy:
            return
        self.finish_prompt_reviews()
        self.inline_typing.finish()
        if self.prompt_only_workspace:
            self.refine_minimax_prompt()
            return
        if self.prompt_scope.currentData() == "global":
            self.refine_global_direction()
            return
        segment = self.current_segment()
        if not segment:
            return
        # Commit the editor value synchronously before snapshotting the timeline
        # for the background refinement worker.
        segment.prompt = (self.intent_segment_prompt.toPlainText()
                          if self.unified_workspace else self.segment_prompt.toPlainText())
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
            refine_generic_segment if self.workspace_engine == "generic" else refine_segment_prompt,
            (self.segments.copy(), row, provider, model, key, self.build_refinement_request(), self.requested_length.value(), timeout) + ((self.active_reference_images(),) if self.workspace_engine == "generic" else ()),
            "Refining the selected prompt with adjacent-frame context…",
            self.refine_prompt_finished,
        )

    def refine_global_direction(self) -> None:
        if self.ai_busy or not self.segments:
            return
        current = self.global_prompt.toPlainText()
        if not current.strip():
            return
        credentials = self.ai_credentials()
        if not credentials:
            return
        provider, model, key = credentials
        self.global_refinement_snapshot = (current, tuple((s.id, s.duration, s.prompt) for s in self.segments))
        self.start_ai_worker(refine_global_prompt,
                             (copy.deepcopy(self.segments), provider, model, key,
                              self.build_refinement_request(), current,
                              self.settings.value("api_timeout", 400, int)),
                             "Refining global direction and requested sequence timing…",
                             self.refine_global_finished)

    def refine_global_finished(self, result: dict) -> None:
        current = (self.global_prompt.toPlainText(), tuple((s.id, s.duration, s.prompt) for s in self.segments))
        applied = current == getattr(self, 'global_refinement_snapshot', None)
        if not applied:
            self.statusBar().showMessage("Global refinement was not applied because the prompt or timeline changed")
        else:
            self.global_prompt.setPlainText(consume_global_refinements(result['globalPrompt']))
            self.animate_timeline_durations(result['durations'])
            self.refresh_shared_editor()
            self.statusBar().showMessage("Global direction and requested sequence timing refined")
        self.set_ai_controls_enabled(True)
        self.magic_overlay.hide_overlay()
        if applied and self.unified_workspace:
            self._generate_minimax_prompt(self.minimax_prompt_mode)

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
            self.animate_timeline_durations(durations)
            self.statusBar().showMessage(f"Segment {index + 1} timing refined to {self.segments[index].duration:.2f}s; prompt text unchanged")
        self.set_ai_controls_enabled(True)
        self.magic_overlay.hide_overlay()

    def refine_prompt_finished(self, result: dict) -> None:
        target = self.refinement_target()
        if target:
            index, segment = target
            segment.prompt = consume_global_refinements(str(result["prompt"]))
            segment.image_prompt = str(result.get("imagePrompt") or segment.image_prompt).strip()
            durations = [item.duration for item in self.segments]
            durations[index] = float(result["duration"])
            if self.timeline.currentRow() == index:
                self.refresh_segment_prompt_box(index)
            self.refresh_text_segment_previews()
            self.animate_timeline_durations(durations)
            self.statusBar().showMessage(f"Segment {index + 1} prompt refined")
        self.set_ai_controls_enabled(True)
        self.magic_overlay.hide_overlay()
        # Segment-only actions remain isolated; Global runs the cascade in magic_finished().

    def magic_build(self) -> None:
        if self.prompt_only_workspace:
            self.refine_minimax_prompt()
            return
        if not self.segments:
            self.add_media()
            return
        credentials = self.ai_credentials()
        if not credentials:
            return
        provider, model, key = credentials
        self.statusBar().showMessage("Magic Build is analyzing ordered timeline items…")
        timeout = self.settings.value("api_timeout", 400, int)
        self.start_ai_worker(build_generic_segments if self.workspace_engine == "generic" else build_prompts, (
            self.segments.copy(), provider, model, key,
            self.build_director_request(), self.sfx.isChecked(), self.spoken_dialog.isChecked(), self.hdr.isChecked(), self.reduce_music.isChecked(), timeout,
        ) + ((self.active_reference_images(),) if self.workspace_engine == "generic" else ()), "Analyzing timeline context and directing motion…", self.magic_finished)

    def magic_progress(self, attempt: int, total: int, detail: str) -> None:
        if not self.ai_request_is_current():
            return
        if attempt > 1:
            self.statusBar().showMessage(f"{self.ai_activity_title}: {detail} (attempt {attempt}/{total})")
        if getattr(self, "ai_activity_in_minimax", False) and self.minimax_panel:
            action = "Refining" if str(getattr(self, "minimax_operation_kind", "")).startswith("refine_") else "Generating"
            self.minimax_panel.set_busy(
                True, f"{detail}  •  attempt {attempt}/{total}",
                show_progress=getattr(self, "ai_request_editor", None) is self.unified_prompt)
            self.minimax_panel.set_cache_state(f"{action}… attempt {attempt}/{total}")
        else:
            self.magic_overlay.update_attempt(attempt, total, detail)

    def magic_finished(self, result: dict) -> None:
        target_durations = []
        generated_segments = result["segments"]
        for segment, generated in zip(self.segments, generated_segments):
            segment.prompt = str(generated.get("prompt", segment.prompt))
            segment.image_prompt = str(generated.get("imagePrompt") or segment.image_prompt).strip()
            recommended = max(MIN_DURATION, round(float(generated.get("duration", segment.duration)), 2))
            target_durations.append(recommended)
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
        current = self.current_segment()
        self.refresh_text_segment_previews()
        self.mark_dirty()
        self.set_ai_controls_enabled(True)
        self.magic_overlay.hide_overlay()
        self.animate_timeline_durations(target_durations)
        cascade = bool(getattr(self, "global_build_cascade_requested", False))
        self.global_build_cascade_requested = False
        self.statusBar().showMessage(
            "Sequence updated; rebuilding combined prompt…" if cascade
            else "Magic Build complete")
        if cascade and self.unified_workspace:
            # A one-way rebuild preserves the sequence JUST produced by Magic
            # Build. Never run another combined-plan rewrite over these edits.
            self.sync_combined_from_segments()

    def store_minimax_draft(self) -> None:
        self.minimax_drafts[self.workspace_draft_key] = {
            "prompt": self.minimax_prompt_text,
            "instructions": self.minimax_refinement_instructions,
            "sourceHash": self.minimax_prompt_cache_key,
            "updatedAt": self.minimax_prompt_updated_at,
        }

    def restore_project_type(self, value, drafts) -> None:
        self.minimax_drafts = {
            key: {**{field: str(item.get(field, "")) for field in ("prompt", "instructions", "sourceHash", "updatedAt")}}
            for key, item in (drafts.items() if isinstance(drafts, dict) else [])
            if isinstance(item, dict)
        }
        aliases = {'minimax_frames': 'minimax_base_guide', 'minimax_references': 'minimax_full_reference_guide'}
        for old, new in aliases.items():
            old_key = old.removeprefix('minimax_')
            if new not in self.minimax_drafts and old_key in self.minimax_drafts:
                self.minimax_drafts[new] = dict(self.minimax_drafts[old_key])
        legacy = "minimax_" + self.minimax_prompt_mode if self.minimax_prompt_text else "ltx"
        requested = value or legacy
        if requested not in self.workspace_definitions:
            requested = aliases.get(requested, requested)
        self.project_type = requested if requested in self.workspace_definitions else next(iter(self.workspace_definitions), "")
        if self.minimax_prompt_text:
            self.store_minimax_draft()
        if self.unified_workspace:
            self.load_minimax_draft(("frames" if self.workspace_engine == "generic" else self.workspace_engine.removeprefix("minimax_")))
        self.prompt_scope.setCurrentIndex(0 if self.unified_workspace else 1)
        self.sync_reference_panel_visibility()

    def load_minimax_draft(self, mode: str) -> None:
        self.minimax_prompt_mode = mode
        draft = self.minimax_drafts.get(self.workspace_draft_key, {})
        self.minimax_prompt_text = draft.get("prompt", "")
        self.minimax_refinement_instructions = draft.get("instructions", "")
        self.minimax_prompt_cache_key = draft.get("sourceHash", "")
        self.minimax_prompt_updated_at = draft.get("updatedAt", "")

    def project_type_changed(self, _index: int) -> None:
        if not self._loading:
            self.set_project_type(self.project_type_combo.currentData())

    @property
    def workspace_definition(self) -> dict:
        return self.workspace_definitions.get(self.project_type, {})

    @property
    def workspace_engine(self) -> str:
        return self.workspace_definition.get("engine", "ltx")

    @property
    def unified_workspace(self) -> bool:
        return self.workspace_definition.get("prompt_mode") in {"unified", "prompt_only"}

    @property
    def prompt_only_workspace(self) -> bool:
        return self.workspace_definition.get("prompt_mode") == "prompt_only"

    @property
    def has_project_content(self) -> bool:
        return self.workspace_engine == "shot_sequence" or bool(self.segments) or (self.prompt_only_workspace and bool(self.minimax_prompt_text.strip()))

    @property
    def workspace_draft_key(self) -> str:
        return self.minimax_prompt_mode if self.project_type in {"minimax_frames", "minimax_references", "ltx"} else self.project_type

    def reload_workspace_definitions(self, previous_id=None, new_id=None) -> None:
        self.store_minimax_draft()
        old_draft_key = self.workspace_draft_key
        if previous_id == self.project_type and new_id:
            self.project_type = new_id
            if new_id not in {"ltx", "minimax_frames", "minimax_references"} and old_draft_key in self.minimax_drafts:
                self.minimax_drafts[new_id] = self.minimax_drafts.pop(old_draft_key)
        self.workspace_definitions = self.workspace_store.load()
        self.project_type_combo.blockSignals(True)
        self.project_type_combo.clear()
        for key, value in self.workspace_definitions.items():
            self.project_type_combo.addItem(value["name"], key)
        self.project_type_combo.blockSignals(False)
        if self.project_type not in self.workspace_definitions:
            self.project_type = next(iter(self.workspace_definitions), "")
        if self.unified_workspace:
            self.load_minimax_draft(("frames" if self.workspace_engine == "generic" else self.workspace_engine.removeprefix("minimax_")))
        self.workspace_revision += 1
        self.apply_project_type_ui()
        self.mark_dirty()
        if self.workspace_store.errors:
            self.statusBar().showMessage("Some workspace definitions could not be loaded. Review Settings → Workspaces.")

    def set_project_type(self, project_type: str) -> None:
        self.reject_segment_prompt_review()
        if project_type not in self.workspace_definitions:
            project_type = {'minimax_frames': 'minimax_base_guide', 'minimax_references': 'minimax_full_reference_guide'}.get(project_type, project_type)
        if project_type not in self.workspace_definitions or project_type == self.project_type:
            return
        if getattr(self, "duration_animation", None):
            self.duration_animation.stop()
        was_sequence = self.workspace_engine == "shot_sequence"
        self.store_minimax_draft()
        self.project_type = project_type
        self.workspace_revision += 1
        if self.unified_workspace:
            self.load_minimax_draft(("frames" if self.workspace_engine == "generic" else self.workspace_engine.removeprefix("minimax_")))
        self.prompt_scope.blockSignals(True)
        self.prompt_scope.setCurrentIndex(0 if self.unified_workspace else 1)
        self.prompt_scope.blockSignals(False)
        self.minimax_panel.clear_message()
        self.sync_minimax_panel()
        self.sync_reference_panel_visibility()
        if was_sequence or self.workspace_engine == "shot_sequence":self.refresh_timeline()
        self.mark_dirty()

    def apply_project_type_ui(self) -> None:
        if hasattr(self, "timeline") and self.segments and not self._loading:
            current = [item.duration for item in self.segments]
            if fit_sequence(current, self.maximum_sequence_duration) != current:
                self.refresh_timeline(self.timeline.currentRow())
        prompt_only = self.prompt_only_workspace
        ltx = not self.unified_workspace
        references = self.workspace_definition.get("references", {})
        refs = (not ltx or self.workspace_engine == "generic") and references.get("enabled", False) and references.get("untimed_slots", 0) > 0
        self.project_type_combo.blockSignals(True)
        self.project_type_combo.setCurrentIndex(self.project_type_combo.findData(self.project_type))
        self.project_type_combo.blockSignals(False)
        self.project_type_combo.setToolTip(
            f"Project type: {self.project_type_combo.currentText()} — choose another workspace"
        )
        self.prompt_label.setText("SEGMENT PROMPT")
        self.segment_prompt.inline_cue_mode = False
        self.prompt_scope.setVisible(self.unified_workspace and not prompt_only)
        if not self.unified_workspace and self.prompt_scope.currentData() != "segment":
            self.prompt_scope.blockSignals(True)
            self.prompt_scope.setCurrentIndex(1)
            self.prompt_scope.blockSignals(False)
        self.refresh_director_scope_editor()
        self.unified_panel.setVisible(not ltx)
        self.timeline_shell.setVisible(not prompt_only)
        self.sequence_bar.setVisible(not prompt_only)
        self.segment_meta.setVisible(not prompt_only)
        self.segment_panel.setVisible(ltx and not prompt_only)
        self.director_panel.setVisible(not prompt_only)
        # Two independent pairs. Upper pair belongs to the selected segment /
        # global direction box; lower pair always belongs to combined prompt.
        for action in (self.segment_quick_fix_button, self.magic_button, self.prompt_refine_button):
            self.director_intent_row.removeWidget(action)
            self.segment_header.removeWidget(action)
        if ltx:
            self.segment_header.addWidget(self.segment_quick_fix_button)
            self.segment_header.addWidget(self.magic_button)
            self.segment_header.addWidget(self.prompt_refine_button)
        else:
            insert_at = self.director_intent_row.indexOf(self.prompt_scope)
            self.director_intent_row.insertWidget(insert_at, self.segment_quick_fix_button)
            self.director_intent_row.insertWidget(insert_at + 1, self.magic_button)
            self.director_intent_row.insertWidget(insert_at + 2, self.prompt_refine_button)
        self.segment_quick_fix_button.setVisible(not prompt_only)
        self.combined_quick_fix_button.setVisible(not ltx)
        self.magic_button.setVisible(not prompt_only)
        self.prompt_refine_button.setVisible(not prompt_only)
        self.combined_generate_button.setVisible(not ltx)
        self.combined_refine_button.setVisible(not ltx)
        self.update_sync_timeline_enabled()
        self.refine_prompt_button.hide()
        self.refine_timing_button.hide()
        if hasattr(self, "paragraph_review"):
            self.finish_prompt_reviews()
        self.minimax_panel.prompt_label.setText("PROMPT REFINEMENT" if prompt_only else "UNIFIED PROMPT")
        self.unified_prompt.setPlaceholderText("Write or paste a MiniMax prompt. Use / to add refinement annotations…" if prompt_only else
            "Generate the unified prompt from your segments, or write and refine your own…")
        self.references_button.setVisible(refs)
        self.minimax_panel.reference_dock.toggleViewAction().setVisible(refs)
        self.sync_reference_panel_visibility()
        audio = self.workspace_definition.get("audio_generation", True)
        for control in (self.sfx, self.spoken_dialog, self.reduce_music):
            control.setVisible(audio)
        if not audio and not prompt_only:
            for control in (self.sfx, self.spoken_dialog, self.reduce_music):
                control.setChecked(False)
        self.hdr.setVisible(True)
        self.requested_length.setVisible(True)
        self.length_label.hide()
        self.voice_options_button.setVisible(audio and not prompt_only)
        self.update_spoken_dialog_visibility()
        self.output_size_control.setVisible(True)
        self.refine_timing_button.hide()
        self.magic_button.setToolTip(
            "Segment: generate only the selected segment. Global: Magic Build the entire sequence "
            "from Director's Intent, then rebuild the combined prompt.")
        self.prompt_refine_button.setToolTip(
            "Segment: refine only the selected segment. Global: refine every segment "
            "from Director's Intent, then rebuild the combined prompt.")
        self.comfy_export_action.setVisible(self.workspace_engine in {"minimax_frames","minimax_references","minimax_prompt"})
        self.workflow_export_action.setVisible(ltx)
        self.timeline_import_action.setVisible(not prompt_only)
        self.delete_segment_action.setVisible(not prompt_only)
        self.refresh_primary_ai_action()
        visual = bool(self.current_segment() and self.current_segment().kind != "text")
        self.start_button.setVisible(visual)
        self.end_button.setVisible(visual)
        self.update_spoken_dialog_visibility()
        self.minimax_panel.refresh_references()
        reference_options = self.workspace_definition.get("references", {})
        for index, target in enumerate(self.minimax_panel.reference_targets):
            target.setVisible(reference_options.get("enabled", False) and index < reference_options.get("untimed_slots", 0))
            for row in range(target.role.count()):
                target.role.model().item(row).setEnabled(target.role.itemData(row) in reference_options.get("kinds", []))
        self.refresh_shared_editor()
        self.update_conditioning_guide()
        QTimer.singleShot(0, self.restore_director_intent_splitter)

        self.queue_project_panel_width_restore()
        self.update_summary()

        if hasattr(self, "shot_sequence_panel"):
            sequence = self.workspace_engine == "shot_sequence"
            self.shot_sequence_panel.setVisible(sequence)
            for i in range(self.sequence_controls.count()):
                widget=self.sequence_controls.itemAt(i).widget()
                if widget:widget.setVisible(not sequence and not prompt_only)
            self.intent_prompt_splitter.setVisible(not sequence)
            if sequence:
                self.segment_quick_fix_button.hide()
                self.combined_quick_fix_button.hide()
                for widget in (self.timeline_shell,self.conditioning_guide,self.sequence_bar,self.references_button):widget.hide()
                self.magic_button.setEnabled(False)
                self.magic_button.hide()
                self.prompt_refine_button.hide()
                self.combined_generate_button.hide()
                self.combined_refine_button.hide()
                self.workflow_export_action.setVisible(False)
                self.timeline_import_action.setVisible(False)
                self.delete_segment_action.setVisible(False)
                self.shot_sequence_panel.refresh()
                self.workflow_label.setText("Completed shots · RAM playback · source audio")
            else:
                self.shot_sequence_panel.deactivate()

    def update_conditioning_guide(self) -> None:
        if not hasattr(self, "conditioning_guide"):
            return
        available = self.unified_workspace and not self.prompt_only_workspace
        self.conditioning_guide.setVisible(available and bool(self.segments) and self.conditioning_height > 0)
        self.conditioning_height_handle.setVisible(available)
        if self.prompt_only_workspace:
            return
        signature = (self.unified_workspace, self.project_type,
                     tuple((s.id, s.name, s.kind, s.role, s.duration, s.preview_path, s.trim_start) for s in self.segments),
                     tuple(str(r) for r in self.active_reference_images()))
        if signature == getattr(self, '_conditioning_signature', None):
            return
        self._conditioning_signature = signature
        self._conditioning_previews = []
        while self.conditioning_row.count():
            item = self.conditioning_row.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        if not self.unified_workspace:
            return
        inventory = reference_inventory(self.segments, self.active_reference_images())
        timed_images = [r for r in inventory if r['kind'] == 'image' and 'checkpoint_time' in r]
        videos = [r for r in inventory if r['kind'] == 'video' and 'timeline_index' in r]
        total = sum(segment.duration for segment in self.segments)
        mode = ('V2V' if videos else 'T2V' if not timed_images else
                'L2V' if len(timed_images) == 1 and timed_images[0]['role'] == 'end' else
                'I2V' if len(timed_images) == 1 else
                'FL2V' if len(timed_images) == 2 and timed_images[0]['role'] == 'start' and timed_images[1]['role'] == 'end'
                else 'Keyframes')
        self.conditioning_records = inventory
        self.conditioning_fixes = []
        heading = QLabel(f"{mode}\n{total:.2f}s")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setMinimumWidth(68)
        heading.setToolTip("Conditioning setup: click the timeline to edit frames and roles")
        heading.setObjectName("conditioningHeading")
        self.conditioning_row.addWidget(heading)
        for record in inventory:
            if not record['label']:
                continue
            tile = QFrame()
            tile.setObjectName("conditioningTile")
            tile.setFixedWidth(176)
            row = QVBoxLayout(tile)
            row.setContentsMargins(1, 1, 1, 1)
            row.setSpacing(0)
            title = QLabel(record['label'])
            title.setObjectName("conditioningTitle")
            row.addWidget(title)
            if 'timeline_index' in record:
                segment = self.segments[record['timeline_index']]
                preview = QPixmap(segment.preview_path)
            else:
                reference = reference_slots(self.minimax_reference_images)[record['reference_slot']]
                preview = pixmap_from_data_url(reference['image']) if reference.get('image') else QPixmap()
            if preview.isNull():
                preview = toolbar_icon('preview' if record['kind'] == 'video' else 'import').pixmap(64, 48)
            thumbnail = ConditioningPreview(preview)
            self._conditioning_previews.append((thumbnail, preview))
            row.addWidget(thumbnail, 1)
            fix = ''
            if 'checkpoint_time' in record:
                time = record['checkpoint_time']
                role = 'End' if record['role'] == 'end' else 'Start'
                detail = f"{role} · {time:.2f}s"
                if mode in {'I2V', 'FL2V'} and record is timed_images[0] and abs(time) > .5 / FPS:
                    fix = 'Move opening to 0.00s'
                if mode in {'L2V', 'FL2V'} and record is timed_images[-1] and abs(time - total) > .5 / FPS:
                    fix = f'Move ending to {total:.2f}s'
            elif record['kind'] == 'video' and 'timeline_index' in record:
                detail = f"Source {record['source_start']:.2f}–{record['source_end']:.2f}s"
            else:
                detail = f"{record['role'].title()} · untimed"
            footer = QFrame()
            footer.setObjectName("conditioningFooter")
            footer_row = QHBoxLayout(footer)
            footer_row.setContentsMargins(5, 4, 5, 4)
            footer_row.setSpacing(4)
            label = QLabel(f"Start {record['start_time']:.2f}s" if 'timeline_index' in record else detail)
            label.setObjectName("conditioningLabel")
            footer_row.addWidget(label)
            footer_row.addStretch()
            if 'timeline_index' in record:
                value = QLabel(f"End {record['end_time']:.2f}s")
                value.setObjectName("conditioningValue")
                footer_row.addWidget(value)
            if fix:
                warning = QLabel(f"⚠ {fix}")
                warning.setObjectName("conditioningFix")
                warning.setContentsMargins(5, 2, 5, 2)
                row.addWidget(warning)
            row.addWidget(footer)
            tile.setToolTip(f"{record['name']}\n{detail}" +
                            (f"\nMiniMaxCondFrame value: {record['checkpoint_time']:.2f} seconds" if 'checkpoint_time' in record else '') + (f"\n{fix}. Change the segment order or duration above." if fix else ''))
            self.conditioning_row.addWidget(tile)
            if fix:
                self.conditioning_fixes.append(fix)
        if not inventory or not any(record['label'] for record in inventory):
            self.conditioning_row.addWidget(QLabel("Text only · no conditioning images" if self.segments else "Add a timeline segment to begin"))
        self.conditioning_row.addStretch()

    def set_conditioning_height(self, height: int):
        self.conditioning_height = 0 if height < 48 else max(96, min(260, height))
        self.conditioning_height_handle.current_height = self.conditioning_height
        if self.conditioning_height:
            self.conditioning_height_handle.last_height = self.conditioning_height
        self.conditioning_guide.setFixedHeight(self.conditioning_height)
        self.conditioning_guide.setVisible(
            self.conditioning_height > 0 and self.unified_workspace and not self.prompt_only_workspace)

    def finish_conditioning_height_resize(self):
        self.settings.setValue("conditioning_panel_height", self.conditioning_height)
        self.settings.setValue("conditioning_panel_last_height", self.conditioning_height_handle.last_height)

    def restore_director_intent_splitter(self) -> None:
        self.set_director_intent_expanded(self.settings.value("director_intent_expanded", True, bool), persist=False)
        if not self.unified_panel.isHidden() and not self.segment_panel.isHidden():
            total=max(1,sum(self.prompt_splitter.sizes()))
            fraction=max(.05,min(.95,self.settings.value('prompt_splitter_fraction',.35,float)))
            self.prompt_splitter.setSizes([round(total*fraction),round(total*(1-fraction))])

    def set_director_intent_expanded(self, expanded: bool, *, persist: bool = True) -> None:
        if self.prompt_only_workspace:
            return
        sizes = self.intent_prompt_splitter.sizes()
        height = self.settings.value("director_intent_height", 164, int)
        if not self.settings.value("director_intent_fullwidth_v2", False, bool):
            # One-time upgrade: the old 164px split left almost no readable
            # editor below the two lines of controls. Keep larger custom
            # heights and the user's collapsed/expanded preference.
            height = max(240, height)
            self.settings.setValue("director_intent_height", height)
            self.settings.setValue("director_intent_fullwidth_v2", True)
            self.queue_settings_sync()
        # Let the native splitter allocate the current viewport; saved sizes
        # must never impose a minimum height on the whole window.
        self.intent_prompt_splitter.setMinimumHeight(0)
        total = max(1,sum(sizes), self.intent_prompt_splitter.height())
        self.intent_prompt_splitter.setSizes([height if expanded else 0, max(1, total - height if expanded else total)])
        if persist:
            self.director_intent_splitter_moved()

    def prompt_splitter_moved(self,*_):
        sizes=self.prompt_splitter.sizes()
        if all(sizes):
            self.settings.setValue('prompt_splitter_fraction',sizes[0]/sum(sizes))
            self.queue_settings_sync()

    def director_intent_splitter_moved(self, *_args) -> None:
        if self.prompt_only_workspace:
            return
        height = self.intent_prompt_splitter.sizes()[0]
        self.settings.setValue("director_intent_expanded", height > 0)
        if height > 0:
            self.settings.setValue("director_intent_height", height)
        self.queue_settings_sync()

    def prompt_scope_changed(self, _index: int) -> None:
        self.reject_segment_prompt_review()
        # This selects the displayed Director's Intent editor, NOT an AI action.
        self.refresh_director_scope_editor()
        self.refresh_primary_ai_action()

    def save_director_segment_prompt(self) -> None:
        if self._loading or self.ai_busy or not self.unified_workspace:
            return
        segment = self.current_segment()
        if segment is None:
            return
        text = self.intent_segment_prompt.toPlainText()
        if text == segment.prompt:
            return
        segment.prompt = text
        self.segment_prompt.blockSignals(True)
        self.segment_prompt.setPlainText(text)
        self.segment_prompt.blockSignals(False)
        item = self.timeline.currentItem()
        card = self.timeline.itemWidget(item) if item is not None else None
        if segment.kind == "text" and isinstance(card, SegmentCard):
            card.set_text_preview(text)
        self.mark_dirty()
        self.refresh_primary_ai_action()
        self.update_counts()
        self.preview_segment_in_unified(self.intent_segment_prompt)

    def refresh_director_scope_editor(self) -> None:
        if not hasattr(self, "director_intent_stack"):
            return
        show_segment = (
            self.unified_workspace and not self.prompt_only_workspace
            and self.prompt_scope.currentData() == "segment"
        )
        self.director_intent_stack.setCurrentIndex(1 if show_segment else 0)
        self.director_intent_label.setText(
            "SEGMENT PROMPT" if show_segment else "DIRECTOR'S INTENT"
        )
        if show_segment:
            current = self.current_segment()
            content = current.prompt if current else ""
            if content != self.intent_segment_prompt.toPlainText():
                self.intent_segment_prompt.blockSignals(True)
                self.intent_segment_prompt.setPlainText(content)
                render_prompt_notes(self.intent_segment_prompt)
                self.intent_segment_prompt.blockSignals(False)
            self.intent_segment_prompt.setReadOnly(self.ai_busy or current is None)

    def update_refine_prompt_enabled(self) -> None:
        text = self.unified_prompt.toPlainText() if self.prompt_only_workspace else self.segment_prompt.toPlainText()
        self.refine_prompt_button.setEnabled(not self.ai_busy and bool(text.strip()) and
                                            (self.prompt_only_workspace or bool(self.segments)))
        self.refresh_primary_ai_action()

    def refresh_shared_editor(self) -> None:
        self.reject_segment_prompt_review()
        if hasattr(self, "paragraph_review"):
            self.finish_prompt_reviews()
        # Workspace navigation and prompt-scope changes must never replace an
        # editor's live diff projection and later restore the wrong document.
        if hasattr(self, "inline_typing") and self.inline_typing.active:
            self.inline_typing.suspend()
        if self.prompt_scope.currentData() == "global":
            value = self.global_prompt.toPlainText()
        else:
            segment = self.current_segment()
            value = segment.prompt if segment else ""
        if self.segment_prompt.toPlainText() != value:
            self.segment_prompt.blockSignals(True)
            self.segment_prompt.setPlainText(value)
            render_prompt_notes(self.segment_prompt)
            self.segment_prompt.blockSignals(False)
        self.segment_prompt.setPlaceholderText("Select a timeline segment to edit its prompt…")
        self.update_refine_prompt_enabled()
        if self.unified_prompt.toPlainText() != self.minimax_prompt_text:
            self.unified_prompt.blockSignals(True)
            self.unified_prompt.setPlainText(self.minimax_prompt_text)
            render_prompt_notes(self.unified_prompt)
            self.unified_prompt.blockSignals(False)
        self.minimax_panel.update_actions()
        self.refresh_director_scope_editor()
        self.update_refine_prompt_enabled()
        self.update_counts()

    def global_prompt_changed(self) -> None:
        self.mark_dirty()
        if self.prompt_scope.currentData() == "global":
            self.refresh_shared_editor()

    def update_spoken_dialog_visibility(self) -> None:
        if not hasattr(self, "voice_options_button"):
            return
        self.voice_options_button.setEnabled(
            self.spoken_dialog.isChecked() and not self.ai_busy
        )
        # isVisible() is false until the whole main window is shown.
        # Use the explicit hidden flag so this gear remains on the toolbar.
        self.voice_options_button.setVisible(not self.spoken_dialog.isHidden())

    def set_reference_panel_open(self, opened: bool) -> None:
        self.settings.setValue("reference_panel_open", bool(opened))
        self.queue_settings_sync()
        self.sync_reference_panel_visibility()

    def sync_reference_panel_visibility(self) -> None:
        references = self.workspace_definition.get("references", {})
        available = (self.unified_workspace or self.workspace_engine == "generic") and references.get("enabled", False) and references.get("untimed_slots", 0) > 0
        opened = self.settings.value("reference_panel_open", False, bool)
        dock = self.minimax_panel.reference_dock
        if dock.isHidden() == bool(available and opened):
            dock.setVisible(bool(available and opened))
            self.queue_project_panel_width_restore()

    def show_reference_images(self) -> None:
        self.set_reference_panel_open(True)
        self.minimax_panel.reference_dock.raise_()

    def _has_inline_refinement(self) -> bool:
        """Slash annotations in any prompt are the user's edit instructions."""
        sources = [
            self.minimax_prompt_text, self.global_prompt.toPlainText(),
            self.intent.toPlainText(),
            *(item.prompt for item in self.segments),
        ]
        return any("/refine" in text.lower() for text in sources if text)


    def update_sync_timeline_enabled(self) -> None:
        """One dedicated sync action in any unified+timeline workspace."""
        if not hasattr(self, "sync_timeline_button"):
            return
        timing_available = not self.prompt_only_workspace and self.workspace_engine != "shot_sequence"
        self.retime_all_button.setVisible(timing_available)
        self.retime_all_button.setEnabled(
            timing_available and not self.ai_busy and len(self.segments) > 1
            and not getattr(self, "timeline_review_pending", set())
            and not any(item.artwork_status == "generating" for item in self.segments))
        available = (
            self.unified_workspace and not self.prompt_only_workspace
            and self.workspace_engine != "shot_sequence"
        )
        self.sync_timeline_button.setVisible(available)
        self.sync_timeline_button.setEnabled(
            available and not self.ai_busy and bool(self.minimax_prompt_text.strip())
        )
        if hasattr(self, "sync_combined_button"):
            self.sync_combined_button.setVisible(available)
            self.sync_combined_button.setEnabled(
                available and not self.ai_busy and bool(self.segments)
            )

    def sync_combined_from_segments(self) -> None:
        """Sync the combined prompt from ALL timeline segments in this project."""
        if self.ai_busy or not self.unified_workspace or self.prompt_only_workspace:
            return
        self.finish_prompt_reviews()
        self.minimax_editor_changed()
        if not self.segments:
            self.minimax_panel.show_message(
                "Add timeline sequences before synchronizing the combined prompt.",
                "warning")
            return
        credentials = self.ai_credentials()
        if not credentials:
            return
        provider, model, key = credentials
        # This snapshot deliberately uses the entire project timeline, not
        # currentRow(), selected tiles, or the active viewport.
        self.sync_combined_source = self.minimax_prompt_text
        self.sync_combined_segments = copy.deepcopy(self.segments)
        self.sync_combined_signature = tuple(
            (s.id, s.name, s.kind, s.role, s.duration, s.prompt,
             s.image_prompt, s.media_path) for s in self.segments)
        final_notes = (
            self.build_director_request(include_requested_length=False)
            + "\\n\\nFINAL REFINEMENT NOTES:\\n"
            + self.minimax_refinement_instructions
        )
        self.start_ai_worker(
            sync_combined_from_timeline,
            (self.sync_combined_segments, provider, model, key,
             self.sync_combined_source,
             self.settings.value("api_timeout", 400, int),
             self.active_reference_images(), self.workspace_engine,
             self.minimax_prompt_mode, final_notes),
            "Syncing combined prompt from ALL project timeline segments…",
            self.sync_combined_finished,
            show_main_overlay=False,
        )

    def sync_combined_finished(self, result: dict) -> None:
        """Apply new combined prose; never update the original timeline data."""
        signature = tuple(
            (s.id, s.name, s.kind, s.role, s.duration, s.prompt,
             s.image_prompt, s.media_path) for s in self.segments)
        if (signature != getattr(self, "sync_combined_signature", None)
                or self.minimax_prompt_text != self.sync_combined_source
                or self.unified_prompt.toPlainText() != self.sync_combined_source):
            self.set_ai_controls_enabled(True)
            self.minimax_panel.show_message(
                "The timeline or combined prompt changed during reverse sync; "
                "nothing was replaced.", "warning")
            return
        new_prompt = str(result.get("prompt") or "").strip()
        if not new_prompt:
            self.set_ai_controls_enabled(True)
            self.minimax_panel.show_message(
                "Reverse sync returned an empty combined prompt.", "warning")
            return
        self.minimax_prompt_text = new_prompt
        self.minimax_prompt_updated_at = datetime.now(timezone.utc).isoformat()
        self.unified_prompt.setPlainText(new_prompt)
        self.minimax_panel.set_cache_state("Combined prompt synchronized")
        self.mark_dirty()
        self.set_ai_controls_enabled(True)
        self.statusBar().showMessage(
            f"⇄ Combined prompt synchronized from ALL {len(self.segments)} "
            "timeline segments; segment prompts and media unchanged", 9000)

    def sync_timeline_from_prompt(self) -> None:
        """Run an AI-backed one-way alignment, never a unified refinement."""
        if self.ai_busy or not self.unified_workspace or self.prompt_only_workspace:
            return
        # Finish any visual diff projection before reading the authoritative
        # editor; rejected paragraphs must not leak into the AI request.
        self.finish_prompt_reviews()
        self.minimax_editor_changed()
        if not self.minimax_prompt_text.strip():
            self.minimax_panel.show_message(
                "Write a combined prompt before syncing the timeline.",
                "warning")
            return
        credentials = self.ai_credentials()
        if not credentials:
            return
        provider, model, key = credentials
        if not supports_structured_edit(provider, model):
            self.minimax_panel.show_message(
                "Timeline synchronization requires a structured-capable GPT-6+ "
                "or Gemini Flash model. Choose one in Settings.", "warning")
            return
        self.sync_timeline_source = self.minimax_prompt_text
        self.ai_segment_snapshot = copy.deepcopy(self.segments)
        self.ai_plan_raw = ""
        self.ai_plan_record_count = 0
        self.ai_plan_stream_active = True
        if not hasattr(self, "_ai_plan_timer"):
            self._ai_plan_timer = QTimer(self)
            self._ai_plan_timer.setSingleShot(True)
            self._ai_plan_timer.setInterval(90)
            self._ai_plan_timer.timeout.connect(self._flush_ai_plan_preview)
        self._clear_ai_timeline_preview()
        self.ai_timeline_status.setText("⇄ Syncing timeline…")
        self.ai_timeline_status.setToolTip(
            "Aligning timeline wording and timecodes with the read-only combined prompt")
        self.ai_timeline_status.show()
        self.start_ai_worker(
            sync_timeline_to_prompt,
            (self.ai_segment_snapshot, provider, model, key,
             self.sync_timeline_source,
             self.settings.value("api_timeout", 400, int),
             self.active_reference_images(), self.workspace_engine,
             self.minimax_prompt_mode),
            "Matching timeline timings and wording to the combined prompt…",
            self.sync_timeline_finished, show_main_overlay=False,
        )

    def sync_timeline_finished(self, result: dict) -> None:
        """Reload AI-produced timeline JSON without touching the combined prompt."""
        before = self.ai_segment_snapshot
        self._clear_ai_timeline_preview()
        if (self.minimax_prompt_text != self.sync_timeline_source
                or self.unified_prompt.toPlainText() != self.sync_timeline_source):
            self.set_ai_controls_enabled(True)
            self.minimax_panel.show_message(
                "The combined prompt changed during synchronization; no timeline "
                "edits were applied.", "warning")
            return
        records = result["segments"]
        changes = preview_operations(before, records)
        updated = build_segment_objects(before, records)
        if not self.timeline_count_allowed(len(updated)):
            self.set_ai_controls_enabled(True)
            return
        durations = [item.duration for item in updated]
        if fit_sequence(durations, self.maximum_sequence_duration) != durations:
            self.set_ai_controls_enabled(True)
            self.minimax_panel.show_message(
                "Synchronized timing exceeds the workspace limit. "
                "The existing timeline and combined prompt were not changed.",
                "warning")
            return
        current = self.current_segment()
        selected_id = current.id if current else None
        original_ids = {item.id for item in before}
        inserted = {item.id for item in updated} - original_ids

        # Rendering is optional and ONLY for newly proposed image checkpoints.
        # Retimed existing image segments keep precisely their original pixels.
        requests = []
        for row, item in zip(records, updated):
            if row["id"] not in original_ids and str(row.get("artworkPrompt") or "").strip():
                item.artwork_prompt = row["artworkPrompt"]
                requests.append((item, row))
        tool = None
        to_render = []
        if requests:
            candidates = self._available_artwork_tools()
            tool = candidates[0] if candidates else None
            allow = bool(tool and self._approve_artwork_cost(tool))
            for item, row in requests:
                auto = allow and len(to_render) < 4
                item.artwork_status = "generating" if auto else "awaiting"
                if auto:
                    to_render.append((item.id, dict(row)))

        self.timeline_review_pending = set()
        self.segments = updated
        selected = next(
            (i for i, item in enumerate(updated) if item.id == selected_id), 0)
        self.refresh_timeline(selected)
        self._animate_ai_timeline(before, inserted)
        self._start_segment_review(before, updated, changes)
        self.set_ai_controls_enabled(True)
        self.ai_timeline_status.hide()
        self.ai_edit_feed.hide()
        self.mark_dirty()
        self.statusBar().showMessage(
            f"⇄ Timeline synchronized: {len(changes)} proposed changes; "
            "combined prompt unchanged", 8000)
        for segment_id, row in to_render:
            self._start_segment_artwork(segment_id, row, tool)
        waiting = sum(item.artwork_status == "awaiting" for item in updated)
        if waiting:
            self.ai_edit_feed.setText(
                f"✦ {waiting} new image checkpoint(s) awaiting rendering"
                + (f" · {len(to_render)} rendering" if to_render else "")
            )
            self.ai_edit_feed.show()

    def refresh_primary_ai_action(self) -> None:
        """Each editor has its own fixed Generate and Refine controls."""
        if not hasattr(self, "combined_quick_fix_button") or not hasattr(self, "prompt_scope"):
            return
        available = not self.ai_busy and self.workspace_engine != "shot_sequence"
        self.segment_quick_fix_button.setEnabled(available)
        self.combined_quick_fix_button.setEnabled(available)
        selected = self.current_segment()
        segment_text = (self.intent_segment_prompt.toPlainText()
                        if self.unified_workspace else self.segment_prompt.toPlainText())
        global_text = (self.intent.toPlainText()
                       if self.unified_workspace else self.global_prompt.toPlainText())
        global_scope = self.prompt_scope.currentData() == "global"
        selected_text = global_text if global_scope else segment_text
        upper_has_target = bool(self.segments) and (global_scope or selected is not None)
        self.primary_ai_command = "refine" if self.minimax_prompt_text.strip() else "generate"
        self.magic_button.setEnabled(available and upper_has_target)
        self.prompt_refine_button.setEnabled(
            available and upper_has_target
            and (bool(selected_text.strip()) if not global_scope else bool(
                selected_text.strip() or any(item.prompt.strip() for item in self.segments))))
        self.combined_generate_button.setEnabled(
            available and self.unified_workspace and not self.prompt_only_workspace
            and bool(self.segments))
        self.combined_refine_button.setEnabled(
            available and self.unified_workspace and bool(self.minimax_prompt_text.strip())
            and (bool(self.segments) or self.prompt_only_workspace))
        self.update_sync_timeline_enabled()

    def generate_upper_prompt(self) -> None:
        """Global runs Magic Build for all segments; Segment targets one tile."""
        if self.ai_busy or self.prompt_only_workspace or not self.segments:
            return
        if self.prompt_scope.currentData() == "global":
            self._generate_or_refine_upper_global(False)
        else:
            self.generate_selected_segment()

    def refine_upper_prompt(self) -> None:
        """Global refines all segments; Segment refines only itself."""
        if self.ai_busy or self.prompt_only_workspace:
            return
        if self.prompt_scope.currentData() == "global":
            self._generate_or_refine_upper_global(True)
        else:
            self.refine_selected_prompt()

    def generate_selected_segment(self) -> None:
        if self.ai_busy:
            return
        self.finish_prompt_reviews()
        self.inline_typing.finish()
        segment = self.current_segment()
        if segment is None:
            return
        if self.unified_workspace:
            segment.prompt = self.intent_segment_prompt.toPlainText()
        credentials = self.ai_credentials()
        if not credentials:
            return
        provider, model, key = credentials
        index = self.timeline.currentRow()
        self.refinement_segment_id = segment.id
        self.start_ai_worker(
            generate_segment_prompt,
            (copy.deepcopy(self.segments), index, provider, model, key,
             self.build_refinement_request(), self.requested_length.value(),
             self.settings.value("api_timeout", 400, int)),
            f"Generating only timeline segment {index + 1}…",
            self.refine_prompt_finished,
        )

    def _generate_or_refine_upper_global(self, refine: bool) -> None:
        """Legacy Magic Build semantics: the Global box drives every segment.

        Generation builds the full sequence from global instructions. Refinement
        adjusts all existing segment prompts/timing from those instructions.
        Only after the sequence finishes does the combined prompt rebuild.
        """
        if self.ai_busy or not self.segments:
            return
        credentials = self.ai_credentials()
        if not credentials:
            return
        provider, model, key = credentials
        timeout = self.settings.value("api_timeout", 400, int)
        notes = self.build_director_request()
        operation = refine_sequence_prompts if refine else (
            build_generic_segments if self.workspace_engine == "generic" else build_prompts)
        args = (
            copy.deepcopy(self.segments), provider, model, key,
            notes, self.sfx.isChecked(), self.spoken_dialog.isChecked(),
            self.hdr.isChecked(), self.reduce_music.isChecked(), timeout,
        )
        # The existing Magic Build worker accepts the same arguments except
        # the refinement operation's additional current global production
        # prompt, inserted after Director's Intent.
        if refine:
            operation = refine_sequence_prompts
            args = args[:5] + (self.global_prompt.toPlainText(),) + args[5:]
        elif self.workspace_engine == "generic":
            args = args + (self.active_reference_images(),)
        self.global_build_cascade_requested = bool(self.unified_workspace)
        self.start_ai_worker(
            operation, args,
            "Refining ALL sequence segments from Global guidance…" if refine else
            "Magic Build: generating ALL sequence segments from Global guidance…",
            self.magic_finished,
        )

    def generate_combined_prompt(self) -> None:
        if not self.ai_busy and self.unified_workspace and not self.prompt_only_workspace:
            self._generate_minimax_prompt(self.minimax_prompt_mode)

    def refine_combined_prompt(self) -> None:
        if not self.ai_busy and self.unified_workspace:
            self.refine_minimax_prompt()

    def run_primary_ai_action(self) -> None:
        """Compatibility action: route to the upper editor's Generate/Refine."""
        if self.primary_ai_command == "refine":
            self.refine_upper_prompt()
        else:
            self.generate_upper_prompt()

    def generate_project_prompt(self) -> None:
        if not self.ai_busy:
            self.magic_build()

    def refine_project_prompt(self) -> None:
        if not self.ai_busy:
            self.refine_selected_prompt()

    def export_workflow(self) -> None:
        if not self.unified_workspace:
            self.export_ltx()
        else:
            self.copy_minimax_prompt()

    def ensure_minimax_panel(self) -> MiniMaxPromptPanel:
        return self.minimax_panel

    def sync_minimax_panel(self, cache_state: str | None = None) -> None:
        if not self.minimax_panel:
            return
        if self.minimax_refinement_instructions.strip():
            # Migrate older drafts' hidden refinement notes into visible, editable instructions.
            note = self.minimax_refinement_instructions.strip().replace('\n', ' ')
            directive = f'/refine-global {note}'
            if directive not in self.minimax_prompt_text:
                self.minimax_prompt_text = directive + '\n\n' + self.minimax_prompt_text
            self.minimax_refinement_instructions = ''
        self.apply_project_type_ui()
        mode_name = "annotated" if self.prompt_only_workspace else ("reference" if self.minimax_prompt_mode == "references" else "frame")
        state = cache_state or (f"Saved {mode_name} prompt" if self.minimax_prompt_text else "Not generated")
        self.minimax_panel.set_project(self.current_project_name, self.minimax_prompt_text,
                                       self.minimax_refinement_instructions, state)

    def show_minimax_panel(self, cache_state: str | None = None) -> MiniMaxPromptPanel:
        if not self.unified_workspace:
            self.set_project_type("minimax_" + self.minimax_prompt_mode)
        self.sync_minimax_panel(cache_state)
        self.minimax_panel.focus_prompt_editor()
        return self.minimax_panel

    def retime_unified_prompt(self) -> None:
        """Follow timeline edits locally without regenerating the production draft."""
        if self._loading or self.ai_busy or not self.unified_workspace or self.prompt_only_workspace:
            return
        self.reject_segment_prompt_review()
        # A diff projection is display text. Never patch it as an authored draft.
        for review in (getattr(self, 'inline_typing', None), getattr(self, 'paragraph_review', None)):
            if review is not None and review.editor is self.unified_prompt:
                return
        if retime_prompt(self.unified_prompt, [item.duration for item in self.segments],
                         [item.role for item in self.segments]):
            self.minimax_editor_changed()
        self.highlight_selected_prompt_timecode()

    def highlight_selected_prompt_timecode(self, *, scroll: bool = False) -> None:
        """Emphasize the selected beat; navigate the caret only on an explicit click."""
        if not hasattr(self, 'unified_prompt'):
            return
        if not self.unified_workspace or self.prompt_only_workspace:
            self.unified_prompt.setExtraSelections([])
            return
        if getattr(self, 'segment_prompt_review', None) and self.segment_prompt_review.active:
            return
        for review in (getattr(self, 'inline_typing', None), getattr(self, 'paragraph_review', None)):
            if review is not None and review.editor is self.unified_prompt:
                return
        row = self.timeline.currentRow()
        selections = []
        sections = document_sections(self.unified_prompt)
        selected = next((section for section in sections if section.index == row), None)
        if self.workspace_engine.startswith('minimax_'):
            for section in sections:
                selection = QTextEdit.ExtraSelection()
                selection.cursor = QTextCursor(self.unified_prompt.document())
                selection.cursor.setPosition(section.start)
                selection.cursor.setPosition(section.end, QTextCursor.MoveMode.KeepAnchor)
                color = QColor('#6f9faf')
                color.setAlpha(35 if section is selected else 18)
                selection.format.setBackground(color)
                selection.format.setProperty(int(QTextCharFormat.Property.UserProperty) + 32, True)
                selections.append(selection)
        code_selections = []
        cue = selected.codes if selected is not None else ()
        for start, code in cue:
            selection = QTextEdit.ExtraSelection()
            selection.cursor = QTextCursor(self.unified_prompt.document())
            selection.cursor.setPosition(start)
            selection.cursor.setPosition(start + len(code), QTextCursor.MoveMode.KeepAnchor)
            selection.format.setBackground(QColor('#80612a'))
            selection.format.setForeground(QColor('#fff0bf'))
            selections.append(selection)
            code_selections.append(selection)
        self.unified_prompt.setExtraSelections(selections)
        if scroll and selected is not None:
            segment = self.current_segment()
            if code_selections:
                cursor = code_selections[-1 if segment and segment.role == 'end' else 0].cursor
                cursor.setPosition(cursor.selectionEnd())
            else:
                cursor = QTextCursor(self.unified_prompt.document())
                cursor.setPosition(selected.body_start)
            self.unified_prompt.setTextCursor(cursor)
            self.unified_prompt.ensureCursorVisible()

    def reject_segment_prompt_review(self) -> None:
        review = getattr(self, 'segment_prompt_review', None)
        if review is not None:
            review.reject()

    def preview_segment_in_unified(self, editor) -> None:
        if (self._loading or self.ai_busy or not self.unified_workspace or self.prompt_only_workspace
                or self.prompt_scope.currentData() != 'segment'
                or not self.workspace_engine.startswith('minimax_') or not editor.hasFocus()):
            return
        self.finish_prompt_reviews()
        self.segment_prompt_review.preview(editor, editor.toPlainText())

    def minimax_editor_changed(self) -> None:
        review = getattr(self, 'segment_prompt_review', None)
        if review is not None and review.active:
            # An external result replaces the temporary projection. Restore the
            # real document first so the result never becomes diff markup.
            value = self.unified_prompt.toPlainText()
            review.reject()
            self.unified_prompt.setPlainText(value)
            return
        if self._loading or not self.minimax_panel or not self.unified_workspace:
            return
        value = self.unified_prompt.toPlainText()
        if value == self.minimax_prompt_text:
            return
        self.minimax_prompt_text = value
        self.minimax_prompt_updated_at = datetime.now(timezone.utc).isoformat()
        self.minimax_panel.update_actions()
        self.unified_count.setText(f"{len(value)} characters")
        self.update_sync_timeline_enabled()
        self.update_refine_prompt_enabled()
        self.mark_dirty()

    def active_reference_images(self) -> list:
        options = self.workspace_definition.get("references", {})
        if not options.get("enabled"):
            return [None] * 4
        count = options.get("untimed_slots", 0)
        kinds = options.get("kinds", [])
        return [value if index < count and value and value.get("role", "identity") in kinds else None
                for index, value in enumerate(reference_slots(self.minimax_reference_images))]

    def export_reference_image(self, slot: int) -> None:
        references = reference_slots(self.minimax_reference_images)
        if not 0 <= slot < len(references) or not references[slot]:
            return
        reference = references[slot]
        if reference.get('kind') in {'video','audio'}:
            source = Path(reference['path'])
            target = self.next_export_path(reference['name'])
            self.start_disk_job(shutil.copy2,(str(source),str(target)),lambda _result:self.record_export(target),
                                lambda message:self.export_failed(target,message),serialized=True)
            return
        image = str(reference.get("image", ""))
        if not image:
            return
        mime = image.split(";", 1)[0].removeprefix("data:").lower()
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
                  "image/bmp": ".bmp", "image/tiff": ".tiff"}.get(mime, ".png")
        stem = Path(str(reference.get("name") or f"Reference {slot + 1}")).stem
        target = self.next_export_path(stem + suffix)
        self.start_disk_job(write_data_url, (image, target), lambda _result: self.record_export(target),
                            lambda message: self.export_failed(target, message), serialized=True)

    def set_minimax_reference_image(self, slot: int, value: dict | None) -> None:
        if self._loading:
            return
        refs = reference_slots(self.minimax_reference_images)
        refs[slot] = dict(value) if value else None
        image_changed = (self.minimax_reference_images[slot] or {}) != (value or {})
        self.minimax_reference_images = reference_slots(refs)
        self.minimax_prompt_cache_key = ""
        self.mark_dirty()
        if self.minimax_panel:
            self.minimax_panel.refresh_references(update_images=image_changed)
            if not self.minimax_panel.busy:
                self.minimax_panel.set_cache_state("References changed · generate when ready")





    def replace_timeline_media(self, paths: list[str], index: int) -> None:
        paths = [path for path in paths if Path(path).is_file()]
        if not paths or not 0 <= index < len(self.segments):
            return
        target_id = self.segments[index].id
        workspace = (self.workspace_revision, self.current_project_id)
        def ready(result):
            if workspace != (self.workspace_revision, self.current_project_id):
                return
            prepared, errors = result
            location = next((i for i, item in enumerate(self.segments) if item.id == target_id), -1)
            if prepared and location >= 0:
                old = self.segments[location]
                path, kind, preview, frames, trim = prepared[0]
                self.segments[location] = Segment(Path(path).name, path, preview, kind, old.role, old.prompt,
                                                   old.duration, frames, trim, old.id, old.image_prompt, False)
                self.mark_dirty()
                self.refresh_timeline(location)
                if len(prepared) > 1:
                    self.add_media_paths(paths[1:], location + 1)
            if errors:
                QMessageBox.warning(self, "Media error", "\n".join(errors))
        self.start_disk_job(_prepare_media_batch, (paths,), ready,
                            lambda message: QMessageBox.warning(self, "Media error", message))

    def stage_current_workspace(self) -> None:
        """Keep an unsaved project in memory when switching tabs or starting anew."""
        if self.has_project_content and self.project_dirty and not self.current_project_id:
            self.current_project_id = uuid4().hex
            self.current_project_name = (Path(self.segments[0].name).stem if self.segments else "MiniMax Prompt") or "Untitled"
            self.update_window_title()

    def current_minimax_cache_key(self, provider: str | None = None, model: str | None = None) -> str:
        if self.prompt_only_workspace:
            return f"{self.workspace_revision}:{self.current_project_id}:{self.project_type}:" + json.dumps(
                self.workspace_definition.get("generation_prompts", {}), sort_keys=True)
        provider = provider or str(self.settings.value("provider", "gemini"))
        model = model or str(self.settings.value("gemini_model", GEMINI_MODELS[0]))
        return f"{self.workspace_revision}:{self.project_type}:" + minimax_h3_cache_key(
            self.segments,
            provider,
            model,
            self.build_director_request(),
            self.global_prompt.toPlainText(),
            self.sfx.isChecked(),
            self.spoken_dialog.isChecked(),
            self.reduce_music.isChecked(),
            self.active_reference_images(),
        )

    def copy_minimax_prompt(self) -> None:
        self.minimax_editor_changed()
        if not self.minimax_prompt_text.strip():
            return
        QApplication.clipboard().setText(self.minimax_prompt_text)
        message = "MiniMax H3 prompt copied to clipboard"
        self.minimax_panel.show_message(message, "success")

    def generate_minimax_prompt_frames(self) -> None:
        """Manually generate the continuous frame-timeline prompt."""
        if self.workspace_engine == "generic":
            self._generate_minimax_prompt(self.minimax_prompt_mode)
            return
        if self.workspace_engine != "minimax_frames":
            self.set_project_type("minimax_frames")
        self._generate_minimax_prompt("frames")

    def generate_minimax_prompt_references(self) -> None:
        """Manually generate a reference prompt for the detected workflow."""
        if self.workspace_engine == "generic":
            self._generate_minimax_prompt(self.minimax_prompt_mode)
            return
        if self.workspace_engine != "minimax_references":
            self.set_project_type("minimax_references")
        self._generate_minimax_prompt("references")


    def _clear_ai_timeline_preview(self):
        if hasattr(self, "ai_timeline_status"):
            self.ai_timeline_status.hide()
        if hasattr(self, "ai_edit_feed"):
            self.ai_edit_feed.hide()
        if hasattr(self, "timeline"):
            for row in range(self.timeline.count()):
                card = self.timeline.itemWidget(self.timeline.item(row))
                if isinstance(card, SegmentCard):
                    card.stop_rewrite_preview()
                    card.setStyleSheet("")

    def preview_ai_timeline_operations(self, operations):
        """Animate proposed changes visually, without mutating the timeline."""
        from collections import Counter
        quantities = Counter(op["kind"] for op in operations)
        names = {"add": "add", "delete": "remove", "move": "move",
                 "timing": "retime", "role": "set frame role",
                 "rewrite": "rewrite", "artwork": "render artwork"}
        summary = "  ·  ".join(
            f"✦ {total} {names[kind]}" for kind, total in quantities.items()
        )
        self.ai_edit_feed.setText("AI TIMELINE PLAN  " + (summary or "Reading segment JSON…"))
        self.ai_edit_feed.show()
        # A segment may move/retime AND be rewritten. Keep all operations
        # rather than overwriting rewrite with the last operation kind.
        by_id = {}
        for op in operations:
            if op.get("id"):
                by_id.setdefault(op["id"], []).append(op)
        for row in range(self.timeline.count()):
            item = self.timeline.item(row)
            card = self.timeline.itemWidget(item)
            if isinstance(card, SegmentCard):
                edits = by_id.get(item.data(Qt.ItemDataRole.UserRole), [])
                rewrite = next((op for op in edits if op["kind"] == "rewrite"), None)
                if rewrite is not None:
                    old = card.segment
                    if old.prompt != rewrite.get("prompt", old.prompt):
                        card.begin_rewrite_preview(old.prompt, rewrite["prompt"])
                    elif old.image_prompt != rewrite.get("imagePrompt", old.image_prompt):
                        card.begin_rewrite_preview(old.image_prompt, rewrite["imagePrompt"])
                else:
                    card.stop_rewrite_preview()
                kinds = {op["kind"] for op in edits}
                # Rewrites use animated green/red text, NOT another tile border.
                color = ("#76d5b6" if "move" in kinds or "add" in kinds else
                         "#f1ac66" if "timing" in kinds else
                         "#a9b6c0" if "role" in kinds else "")
                card.setStyleSheet(
                    f"QFrame#segmentCard{{border:1px solid {color};border-radius:6px}}"
                    if color else ""
                )
        self.ai_timeline_status.setText(f"✦ {len(operations)} edits")
        self.ai_timeline_status.setToolTip(
            f"{len(operations)} proposed timeline edit(s); "
            "changes apply after validated completion"
        )
        self.ai_timeline_status.show()

    def _animate_ai_timeline(self, before, inserted):
        """Slide new tiles to the model-assigned duration and settle moved tiles."""
        if getattr(self, "_ai_tile_animation", None):
            self._ai_tile_animation.stop()
        original = {item.id: item for item in before}
        target_widths = [self.timeline.item(i).sizeHint().width()
                         for i in range(self.timeline.count())]
        # A new beat grows from the nearest retained timeline tile. This
        # permits a completely fresh JSON timeline without special insert
        # logic; all tiles use the same existing smooth width interpolation.
        def start_width(index, segment):
            if segment.id in original:
                return max(40, round(original[segment.id].duration * self.pixels_per_second))
            candidates = [
                (abs(other_index - index), other_index, original[other.id])
                for other_index, other in enumerate(self.segments)
                if other.id in original
            ]
            if candidates:
                closest = min(candidates, key=lambda item: (item[0], item[1]))[2]
                return min(target_widths[index], max(30, round(closest.duration * self.pixels_per_second * 0.35)))
            return 30
        starting = [start_width(i, item) for i, item in enumerate(self.segments)]
        heights = [self.timeline.item(i).sizeHint().height()
                   for i in range(self.timeline.count())]
        for index, width in enumerate(starting):
            self.timeline.item(index).setSizeHint(QSize(width, heights[index]))
        self.timeline.doItemsLayout()
        animation = QVariantAnimation(self)
        animation.setDuration(1150)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def update(value):
            for index, (left, right) in enumerate(zip(starting, target_widths)):
                item = self.timeline.item(index)
                if item:
                    item.setSizeHint(QSize(round(left + (right-left) * value), heights[index]))
            self.timeline.doItemsLayout()
            self.timeline.viewport().update()

        animation.valueChanged.connect(update)
        animation.finished.connect(self.update_timeline_layout)
        self._ai_tile_animation = animation
        animation.start()

    def _available_artwork_tools(self):
        """Discover configured callable image backends, not subscription OAuth."""
        keys = {
            provider: str(
                self.session_keys.get(provider) or
                self.settings.value(f"{provider}_key", "") or ""
            )
            for provider in ("gemini", "openai")
        }
        keys["local"] = {**load_profile(self.settings),
                         "image_width": self.output_width.value(), "image_height": self.output_height.value()}
        return available_artwork_tools(
            keys,
            preferred=str(self.settings.value("provider", "gemini")),
            selected=str(self.settings.value("artwork/default_model", "auto") or "auto"),
        )

    def _approve_artwork_cost(self, tool, *, manual=False):
        """Ask once per billed provider; remember both approval and rejection."""
        if tool.provider == "local": return True
        setting = f"artwork/cost_decision/{tool.provider}"
        decision = str(self.settings.value(setting, ""))
        if decision == "allow":
            return True
        if decision == "deny" and not manual:
            return False
        # Preserve previously granted approval from earlier builds.
        if self.settings.value(f"artwork/cost_approved/{tool.provider}", False, bool):
            return True
        notice = QMessageBox(self)
        notice.setIcon(QMessageBox.Icon.Warning)
        notice.setWindowTitle("One-time paid artwork approval")
        notice.setText(f"Allow {tool.label} ({tool.model}) to generate timeline artwork?")
        notice.setInformativeText(
            "Artwork requested by your model's segment JSON is rendered using "
            "a separately billed Gemini or OpenAI API key, NOT your ChatGPT plan. "
            "Up to four images can be requested per refinement; charges depend "
            "on the selected model and image size. Your choice is remembered "
            "for this provider. If you decline, the artwork remains pending. "
            "To change your mind, right-click a pending artwork tile and "
            "choose Render requested artwork."
        )
        notice.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        notice.setDefaultButton(QMessageBox.StandardButton.No)
        approved = notice.exec() == QMessageBox.StandardButton.Yes
        self.settings.setValue(setting, "allow" if approved else "deny")
        self.queue_settings_sync()
        return approved

    def combined_refinement_finished(self, result):
        """Apply the validated JSON as one prompt/timeline transaction."""
        before = getattr(self, "ai_segment_snapshot", self.segments)
        # The latest valid refinement supersedes any earlier pending review,
        # while preserving the user-visible draft as its new starting point.
        self.timeline_review_pending = set()
        records = result["segments"]
        changes = preview_operations(before, records)
        updated = build_segment_objects(before, records)
        if not self.timeline_count_allowed(len(updated)):
            self.set_ai_controls_enabled(True)
            return
        # A model requests an image by returning artworkPrompt in segment JSON.
        # Choose the available image tool automatically; don't add a second
        # generate-image button or require a manual checkbox.
        tools = self._available_artwork_tools()
        tool = tools[0] if tools else None
        # A schema-compliant edit request can target an existing frame, not
        # just a new: placeholder. Never silently discard its artworkPrompt.
        # If the model omits the field, honor an explicit /refine instruction
        # physically written on that same existing image segment.
        before_by_id = {segment.id: segment for segment in before}
        requested = []
        inserted = set()
        for data, item in zip(records, updated):
            previous = before_by_id.get(data["id"])
            is_new = previous is None
            if is_new:
                inserted.add(item.id)
            direction = str(data.get("artworkPrompt") or "").strip()
            if not direction and previous and previous.kind == "image":
                direction = explicit_existing_image_edit(previous.prompt)
                if direction:
                    data["artworkPrompt"] = direction
            if direction and (is_new or (previous.kind == "image" and previous.media_path)):
                item.artwork_prompt = direction
                requested.append((item, data))
        should_render = bool(
            tool and requested and self._approve_artwork_cost(tool)
        )
        to_render = []
        for item, data in requested:
            # Limit automatic separately billed images to four per pass.
            auto_render = should_render and len(to_render) < 4
            item.artwork_status = "generating" if auto_render else "awaiting"
            if auto_render:
                to_render.append((item.id, dict(data)))
        if fit_sequence([item.duration for item in updated],
                        self.maximum_sequence_duration) != [item.duration for item in updated]:
            self._clear_ai_timeline_preview()
            self.set_ai_controls_enabled(True)
            self.minimax_panel.show_message(
                "Combined plan exceeds the workspace duration limit. "
                "The existing timeline is unchanged.", "warning"
            )
            return
        # Complete the regular unified prompt workflow while the old timeline
        # is still present, then atomically install the validated segment snapshot.
        self.minimax_h3_finished(result["prompt"])
        current = self.current_segment()
        selected_id = current.id if current else None
        self.segments = updated
        selected = next(
            (i for i, item in enumerate(updated) if item.id == selected_id), 0
        )
        self._clear_ai_timeline_preview()
        self.refresh_timeline(selected)
        self._animate_ai_timeline(before, inserted)
        self._start_segment_review(before, updated, changes)
        self.mark_dirty()
        self.ai_timeline_status.hide()
        if to_render:
            self.ai_edit_feed.setText(
                f"✦ Rendering {len(to_render)} requested image(s)…"
            )
            self.ai_edit_feed.show()
        else:
            # A successful change is not a permanent panel above the timeline.
            self.ai_edit_feed.hide()
        self.statusBar().showMessage(
            f"✦ Applied {len(changes)} timeline edit(s)", 5500
        )
        for segment_id, data in to_render:
            self._start_segment_artwork(segment_id, data, tool)
        waiting = sum(1 for seg in self.segments if seg.artwork_status == "awaiting")
        if waiting and should_render:
            self.ai_edit_feed.setText(
                f"✦ {len(to_render)} artwork requests running · {waiting} awaiting manual render "
                "(automatic batch limited to four images)"
            )
        if waiting and not should_render:
            self.ai_edit_feed.setText(
                f"✦ {waiting} requested image(s) waiting · select an image model "
                "and API key in Settings, then right-click the frame to render."
            )
            self.ai_edit_feed.show()
        if not waiting and not to_render:
            self.ai_edit_feed.hide()

    def _show_segment_review_controls(self):
        """Reattach review buttons when timeline cards are rebuilt or retimed."""
        pending = getattr(self, "timeline_review_pending", set())
        originals = getattr(self, "timeline_review_originals", {})
        for row in range(self.timeline.count()):
            card = self.timeline.itemWidget(self.timeline.item(row))
            if not isinstance(card, SegmentCard):
                continue
            card.resize_handle.setEnabled(self.timeline_boundary_available(row))
            if card.segment.id in pending:
                card.set_review_pending(originals.get(card.segment.id))
                grouped = card.segment.id in getattr(self, "timeline_split_review_ids", set())
                timing = card.segment.id in getattr(self, "timeline_timing_review_ids", set())
                for button, description in (
                    (card.review_accept, "Approve all timing changes" if timing else "Approve entire split" if grouped else "Approve this segment change"),
                    (card.review_reject, "Restore previous timing" if timing else "Restore original segment" if grouped else "Deny this segment change"),
                ):
                    button.setToolTip(description)
                    button.setAccessibleName(description)
            elif card.review_pending:
                card.clear_review_pending()
        visible = bool(pending)
        self.timeline_approve_all.setVisible(visible)
        self.timeline_deny_all.setVisible(visible)
        self.update_sync_timeline_enabled()
        if visible:
            self.timeline_approve_all.setToolTip(
                f"Approve all {len(pending)} pending timeline changes")
            self.timeline_deny_all.setToolTip(
                f"Deny all {len(pending)} pending timeline changes")

    def resolve_segment_review(self, segment_id: str, approved: bool) -> None:
        """One-click timeline review: retain proposal or restore source data."""
        if self.ai_busy:
            return
        pending = getattr(self, "timeline_review_pending", set())
        if segment_id not in pending:
            return
        if segment_id in (getattr(self, "timeline_split_review_ids", set())
                          | getattr(self, "timeline_timing_review_ids", set())):
            self.resolve_all_segment_reviews(approved)
            return
        pending.discard(segment_id)
        if not approved:
            self._artwork_cancelled_segments = getattr(
                self, "_artwork_cancelled_segments", set())
            self._artwork_cancelled_segments.add(segment_id)
            old = self.timeline_review_originals.get(segment_id)
            location = next((i for i, s in enumerate(self.segments)
                             if s.id == segment_id), -1)
            if location >= 0:
                if old is None:
                    self.segments.pop(location)  # deny a newly inserted segment
                else:
                    self.segments[location] = copy.deepcopy(old)
                    original_position = self.timeline_review_positions.get(
                        segment_id, location)
                    restored = self.segments.pop(location)
                    self.segments.insert(
                        min(max(0, original_position), len(self.segments)), restored)
            self.mark_dirty()
            self.refresh_timeline(max(0, min(location, len(self.segments)-1)))
        else:
            self._show_segment_review_controls()
            if self.current_segment() and self.current_segment().id == segment_id:
                self._finish_segment_text_review()
        if not pending:
            self.timeline_review_originals = {}
            self.timeline_review_positions = {}
            self.statusBar().showMessage("✓ Timeline review complete", 5000)
        else:
            self.statusBar().showMessage(
                f"{len(pending)} timeline change(s) awaiting review", 5000)

    def resolve_all_segment_reviews(self, approved: bool) -> None:
        if self.ai_busy:
            return
        if not getattr(self, "timeline_review_pending", set()):
            # The timeline checkmark can also finish the current segment's
            # paragraph review when no structural timeline edits are pending.
            if self.paragraph_review.editor in (self.segment_prompt, self.intent_segment_prompt):
                self.paragraph_review.resolve_all(approved)
            self._finish_segment_text_review()
            self.timeline_approve_all.blockSignals(True)
            self.timeline_deny_all.blockSignals(True)
            self.timeline_approve_all.setChecked(False)
            self.timeline_deny_all.setChecked(False)
            self.timeline_approve_all.blockSignals(False)
            self.timeline_deny_all.blockSignals(False)
            self._show_segment_review_controls()
            return
        timing_ids = getattr(self, "timeline_timing_review_ids", set())
        if timing_ids and getattr(self, "duration_animation", None):
            self.duration_animation.stop()
            self.update_timeline_layout()
        if not approved:
            self._artwork_cancelled_segments = getattr(
                self, "_artwork_cancelled_segments", set())
            self._artwork_cancelled_segments.update(self.timeline_review_pending)
            split_ids = getattr(self, "timeline_split_review_ids", set())
            if timing_ids:
                for item in self.segments:
                    old = self.timeline_review_originals.get(item.id)
                    if item.id in timing_ids and old is not None:
                        for field in TIMING_FIELDS:
                            setattr(item, field, getattr(old, field))
            elif split_ids:
                position = next((i for i, item in enumerate(self.segments) if item.id in split_ids), 0)
                original = next(item for item in self.timeline_review_original_sequence if item.id in split_ids)
                self.segments = [item for item in self.segments if item.id not in split_ids]
                self.segments.insert(min(position, len(self.segments)), copy.deepcopy(original))
            else:
                self.segments = [copy.deepcopy(seg) for seg in
                                 self.timeline_review_original_sequence]
        self.timeline_review_pending.clear()
        self.timeline_split_review_ids = set()
        self.timeline_timing_review_ids = set()
        self.timeline_review_originals = {}
        self.timeline_review_positions = {}
        self.timeline_review_original_sequence = []
        self.timeline_approve_all.blockSignals(True)
        self.timeline_deny_all.blockSignals(True)
        self.timeline_approve_all.setChecked(False)
        self.timeline_deny_all.setChecked(False)
        self.timeline_approve_all.blockSignals(False)
        self.timeline_deny_all.blockSignals(False)
        self._finish_segment_text_review()
        if not approved:
            if timing_ids:
                self.update_timeline_layout()
                self.sync_selected_duration_control()
                self._show_segment_review_controls()
            else:
                self.refresh_timeline()
            self.mark_dirty()
        else:
            self._show_segment_review_controls()
        self.statusBar().showMessage(
            "✓ Approved all timeline changes" if approved else
            "× Restored timeline before the AI changes", 6000)
        if timing_ids:
            self.update_conditioning_guide()
            self.statusBar().showMessage(
                "Timing approved; prompt text preserved." if approved else
                "Previous timing restored; prompt text preserved.", 7000)

    def _finish_segment_text_review(self) -> None:
        """Timeline approval must also leave its segment's editor out of diff mode."""
        editors = (self.segment_prompt, self.intent_segment_prompt)
        if self.inline_typing.editor in editors:
            self.inline_typing.finish()
        if self.paragraph_review.editor in editors:
            self.paragraph_review.finish(plain=True)
        if self.prompt_scope.currentData() == "segment":
            segment = self.current_segment()
            text = segment.prompt if segment else ""
            self.refresh_director_scope_editor()
            # Always rebuild from authoritative text, even when the words
            # already match: a restored document can still carry diff formats.
            for editor in editors:
                reset_plain_review_text(editor, text)

    def _start_segment_review(self, before: list[Segment], updated: list[Segment],
                              changes: list[dict]) -> None:
        self.timeline_split_review_ids = set()
        self.timeline_timing_review_ids = set()
        originals = {seg.id: copy.deepcopy(seg) for seg in before}
        original_positions = {seg.id: i for i, seg in enumerate(before)}
        after_positions = {seg.id: i for i, seg in enumerate(updated)}
        changed_ids = {op["id"] for op in changes if op.get("id")}
        # Move, resize, rewrite, role change, artwork, and new cards are
        # individually reviewable. Deleted segments can still be restored by
        # Deny all (they no longer have a visible card).
        pending = {seg.id for seg in updated if seg.id in changed_ids
                   or seg.id not in originals
                   or after_positions[seg.id] != original_positions.get(seg.id)}
        if any(op["kind"] == "delete" for op in changes):
            pending.add("__deleted_segments__")
        self.timeline_review_original_sequence = [
            copy.deepcopy(seg) for seg in before]
        self.timeline_review_originals = originals
        self.timeline_review_positions = original_positions
        self.timeline_review_pending = pending
        self._show_segment_review_controls()
        if pending:
            self.statusBar().showMessage(
                f"✦ Review {len(pending)} timeline edit(s): ✓ approve or × deny", 10000)

    def _manual_artwork_prompt(self, index: int) -> str:
        """Art-direct a single tile, including adjoining prose in the image prompt.

        The image API already receives real nearby image/video thumbnails from
        _artwork_reference_inputs. Text-only neighbors must instead travel as
        text in the direction, never as invalid image-reference records.
        """
        segment = self.segments[index]
        direction = (segment.prompt or "").strip()
        image_direction = (segment.image_prompt or "").strip()
        nearby = []
        for step, label in ((-1, "PREVIOUS"), (1, "NEXT")):
            neighbor_index = index + step
            if 0 <= neighbor_index < len(self.segments):
                neighbor = self.segments[neighbor_index]
                neighbor_prompt = (neighbor.prompt or "").strip()
                neighbor_visual = (neighbor.image_prompt or "").strip()
                nearby.append(
                    f"{label} SEGMENT {neighbor_index + 1} "
                    f"({neighbor.kind}, {neighbor.role}): "
                    f"{neighbor_prompt[:2000] or '[no written direction]'}"
                    + (f"\nVisual description: {neighbor_visual[:900]}" if neighbor_visual else "")
                )
        if segment.kind == "image":
            instruction = (
                "IMAGE-TO-IMAGE ALIGNMENT: Edit only the supplied SOURCE IMAGE "
                "to better match the TARGET SEGMENT PROMPT. Keep the same "
                "subject identity, scene, camera viewpoint, composition, "
                "lighting, and existing visual details except where the "
                "current segment explicitly requests a change. Do not "
                "replace the subject or change to a different shot. "
                "Nearby segments are secondary continuity references."
            )
        else:
            instruction = (
                "GENERATE THE ARTWORK for this text timeline segment. "
                "Depict the exact action or visual state requested by its "
                "own prompt, using actual neighboring timeline images and "
                "nearby written direction to preserve subject identity, "
                "wardrobe, setting, lighting and chronological progression. "
                "Do not merge previous and next frames into the new artwork."
            )
        return (
            instruction
            + "\n\nTARGET SEGMENT PROMPT:\n" + (direction or "[No segment direction]")
            + ("\n\nTARGET VISUAL DESCRIPTION:\n" + image_direction if image_direction else "")
            + ("\n\nADJACENT TIMELINE CONTEXT (continuity only):\n"
               + "\n\n".join(nearby) if nearby else "")
        )

    def generate_artwork_for_segment(self, segment_id: str) -> None:
        """One right-click action for new text artwork or existing image edits."""
        if self.ai_busy:
            return
        index = next((i for i, item in enumerate(self.segments)
                      if item.id == segment_id), -1)
        if index < 0:
            return
        segment = self.segments[index]
        if segment.kind not in {"text", "image"} or segment.artwork_status == "generating":
            return
        if not (segment.prompt or "").strip() and not (segment.image_prompt or "").strip():
            self.minimax_panel.show_message(
                "Add a prompt to this segment before generating its artwork.",
                "warning")
            return
        # Resolve archived full-resolution originals before authorizing a
        # paid image-to-image edit; a missing source must not become text-to-image.
        if segment.kind == "image":
            try:
                source = Path(materialize_source(segment))
                if not source.is_file():
                    raise FileNotFoundError(source.name)
            except (OSError, ValueError) as error:
                self.minimax_panel.show_message(
                    f"The source image for this segment is unavailable: {error}",
                    "warning")
                return
        tools = self._available_artwork_tools()
        if not tools:
            QMessageBox.information(
                self, "Artwork tool unavailable",
                "Configure a Gemini or OpenAI image API key in Settings to "
                "generate artwork. ChatGPT OAuth cannot render image files.")
            return
        tool = tools[0]
        if not self._approve_artwork_cost(tool, manual=True):
            return
        segment.artwork_prompt = self._manual_artwork_prompt(index)
        if segment.kind == "text":
            segment.artwork_target_role = "start"
        # An earlier review rejection must not cancel this new explicit action.
        getattr(self, "_artwork_cancelled_segments", set()).discard(segment_id)
        self.mark_dirty()
        self._start_segment_artwork(
            segment_id,
            {"id": segment_id, "name": segment.name,
             "artworkPrompt": segment.artwork_prompt,
             "imagePrompt": segment.image_prompt,
             "prompt": segment.prompt},
            tool,
        )

    def _artwork_reference_inputs(self, segment_id: str) -> list[dict]:
        """Provide actual project references and nearby frames to the image API.

        Project reference roles/names stay attached to their pixels. Never feed
        audio, model-authored paths or arbitrary video files as image inputs.
        """
        references = []
        seen_paths = set()
        for slot, reference in enumerate(reference_slots(self.minimax_reference_images), 1):
            if not reference or reference.get("kind", "image") != "image":
                continue
            data = str(reference.get("image") or "")
            if data.startswith("data:image/"):
                references.append({
                    "label": f"Reference {slot}: {reference['name']} — "
                             f"{reference.get('role', 'identity')} role; "
                             f"{reference.get('notes') or 'preserve matching visual details'}",
                    "image": data,
                })

        # Two nearest timeline visual checkpoints establish composition and
        # stage-to-stage continuity. The target itself is sent separately
        # as sourceImage and must not be duplicated as a reference.
        target = next((i for i, s in enumerate(self.segments)
                       if s.id == segment_id), 0)
        candidates = sorted(
            ((abs(i - target), i, s) for i, s in enumerate(self.segments)
             if i != target and s.kind in {"image", "video"}),
            key=lambda value: (value[0], value[1]),
        )
        for _, index, segment in candidates:
            if len(seen_paths) >= 2:
                break
            try:
                path = (
                    str(materialize_source(segment)) if segment.kind == "image"
                    else str(segment.preview_path or "")
                )
            except (OSError, ValueError, FileNotFoundError):
                continue
            if (not path or not Path(path).is_file()
                    or (segment.kind == "video" and Path(path).suffix.lower()
                        not in {".png", ".jpg", ".jpeg", ".webp"})):
                continue
            resolved = str(Path(path).resolve())
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            references.append({
                "label": f"Timeline {index + 1}: {segment.name} — "
                         f"{segment.role} continuity "
                         f"({'video thumbnail' if segment.kind == 'video' else 'frame'})",
                "path": resolved,
            })
        return references[:7]

    def _start_segment_artwork(self, segment_id, record, tool):
        import threading
        cancel = threading.Event() if tool is not None and tool.provider == "local" else None
        item = next((item for item in self.segments if item.id == segment_id), None)
        if not item or tool is None or not item.artwork_prompt:
            return
        if cancel is not None: self._local_artwork_cancel[segment_id] = cancel
        if cancel is not None:self.image_generation_panel.begin()
        self._artwork_jobs = getattr(self, "_artwork_jobs", 0) + 1
        self.minimax_panel.ai_marquee.begin("✦ Rendering image…")
        item.artwork_status = "generating"
        row = next((i for i, s in enumerate(self.segments) if s.id == segment_id), -1)
        if row >= 0 and row < self.timeline.count():
            card = self.timeline.itemWidget(self.timeline.item(row))
            if isinstance(card, SegmentCard):
                card.artwork_timer.start()
                card._advance_artwork_shimmer()
        workspace = (self.workspace_revision, self.current_project_id, self.project_type)
        # Source comes from a real timeline segment, NEVER the AI JSON. For
        # edits this stays the unmodified original until an image succeeds.
        request = dict(record)
        request["id"] = segment_id
        request["referenceImages"] = self._artwork_reference_inputs(segment_id)
        if item.kind == "image":
            # Saved .LTXD projects may keep full-resolution originals inside
            # their archive. Resolve the original lazily before image-to-image.
            request["sourceImage"] = materialize_source(item)

        def ready(images):
            if self._local_artwork_cancel.get(segment_id) is cancel:
                self._local_artwork_cancel.pop(segment_id, None)
            self._artwork_jobs = max(0, getattr(self, "_artwork_jobs", 1) - 1)
            if not self._artwork_jobs and not self.ai_busy:
                self.minimax_panel.ai_marquee.finish()
                self.ai_edit_feed.hide()
            if workspace != (self.workspace_revision, self.current_project_id, self.project_type):
                return
            if segment_id in getattr(self, "_artwork_cancelled_segments", set()):
                return  # A rejected image must never overwrite restored media.
            segment = next((s for s in self.segments if s.id == segment_id), None)
            if not segment:
                return
            path = images.get(segment_id)
            if path and Path(path).is_file():
                original_kind = segment.kind
                segment.kind = "image"
                if original_kind == "text":
                    segment.role = (segment.artwork_target_role
                                    if segment.artwork_target_role in {"start", "end"}
                                    else "start")
                # Only the project reference changes; source file is untouched.
                segment.media_path = segment.preview_path = path
                clear_archived_source(segment)
                segment.artwork_status = "complete"
                self.statusBar().showMessage(
                    f"✦ {'Edited frame' if original_kind == 'image' else 'New frame'} ready: {segment.name}",
                    5500,
                )
            else:
                segment.artwork_status = "failed"
                self.statusBar().showMessage(
                    f"Art generation failed for {segment.name}; right-click to retry", 7000
                )
            index = next(i for i, s in enumerate(self.segments) if s.id == segment_id)
            self.refresh_timeline(index)
            self.mark_dirty()

        def failed(message):
            ready({})
            if workspace != (self.workspace_revision, self.current_project_id, self.project_type): return
            if tool.provider == "local":
                self.image_generation_panel.receive_event({"kind": "error", "message": str(message)})
            detail = str(message).strip()
            self.statusBar().showMessage(
                f"Image failed ({tool.model}); details in prompt panel", 15000
            )
            # Persist the actionable API reason until dismissed, instead of
            # losing the only useful error in a nine-second status bar.
            self.minimax_panel.show_message(
                "Image generation failed for " + segment_id
                + " using " + tool.model + ": " + detail[:950],
                "error",
            )

        self.start_disk_job(
            render_artwork, ([request], tool),
            ready, failed,
            **({"progress": lambda event: self.local_image_progress(event, workspace), "cancel": cancel} if tool.provider == "local" else {}),
        )

    def retry_selected_artwork(self):
        if self.ai_busy:
            return
        segment = self.current_segment()
        if not segment or not segment.artwork_prompt:
            return
        tools = self._available_artwork_tools()
        if not tools:
            selected = str(self.settings.value("artwork/default_model", "auto") or "auto")
            if selected != "auto":
                provider, _, model = selected.partition(":")
                message = (
                    f"The selected image model ({model}) requires a configured "
                    f"{provider.title()} API key. Configure it in Settings, or "
                    "choose Automatic as the default image generation model. "
                    "ChatGPT OAuth cannot generate image files."
                )
            else:
                message = (
                    "Connect a Gemini API key or a conventional OpenAI API key "
                    "in Settings to render this image. ChatGPT OAuth does not "
                    "include image generation."
                )
            QMessageBox.information(self, "Artwork tool unavailable", message)
            return
        tool = tools[0]
        if not self._approve_artwork_cost(tool, manual=True):
            return
        self._start_segment_artwork(
            segment.id,
            {"id": segment.id, "name": segment.name,
             "artworkPrompt": segment.artwork_prompt},
            tool,
        )

    def _generate_minimax_prompt(self, mode: str) -> None:
        if self.prompt_only_workspace:
            self.refine_minimax_prompt()
            return
        if self.ai_busy:
            return
        self.finish_prompt_reviews()
        self.inline_typing.finish()
        self.minimax_editor_changed()
        if not self.segments:
            window = self.show_minimax_panel("Not generated")
            window.show_message("Add at least one timeline item before generating a MiniMax H3 prompt.", "warning")
            return
        reference_mode = mode == "references"
        mode_label = "reference" if reference_mode else "frame"
        window = self.show_minimax_panel(f"Generating {mode_label} prompt manually…")
        credentials = self.ai_credentials()
        if not credentials:
            window.set_busy(False, "Generation cancelled")
            window.show_message("Generation cancelled because no API credentials are configured.", "warning")
            return
        provider, model, key = credentials
        signature = self.current_minimax_cache_key(provider, model)
        self.minimax_operation_signature = signature
        self.minimax_operation_provider_model = (provider, model)
        self.minimax_operation_kind = f"generate_{mode}"
        self.combined_generation_source = self.combined_generation_source_key()
        self.minimax_operation_editor_snapshot = (
            self.minimax_prompt_text,
            self.minimax_refinement_instructions,
        )
        timeout = self.settings.value("api_timeout", 400, int)
        window.set_busy(True, f"Generating from all {len(self.segments)} current segments…")
        operation = build_generic_prompt if self.workspace_engine == "generic" else (build_minimax_h3_reference_prompt if reference_mode else build_minimax_h3_prompt)
        self.start_ai_worker(
            operation,
            (
                copy.deepcopy(self.segments), provider, model, key, self.build_director_request(include_requested_length=False),
                self.global_prompt.toPlainText(), self.sfx.isChecked(), self.spoken_dialog.isChecked(),
                self.reduce_music.isChecked(), timeout,
            ) + (copy.deepcopy(self.active_reference_images()),),
            f"Generating combined prompt from all {len(self.segments)} current segments…",
            self.minimax_h3_finished,
            show_main_overlay=False,
        )

    def combined_generation_source_key(self):
        return tuple((s.id, s.kind, s.role, s.duration, s.prompt, s.image_prompt,
                      (getattr(s, "_media_digest", "") or s.media_path or s.preview_path)
                      if s.kind != "text" else "", s.trim_start) for s in self.segments)

    def minimax_h3_finished(self, prompt: str) -> None:
        # The unified editor and timeline are locked while AI is running.
        # Project/workspace identity is checked once by ai_result_received().
        # Legacy editor-change fingerprint checks caused completed output loss.
        operation = str(getattr(self, "minimax_operation_kind", "generate_frames"))
        source = getattr(self, "combined_generation_source", None)
        self.combined_generation_source = None
        if (operation.startswith("generate_") and source is not None
                and source != self.combined_generation_source_key()):
            self.pending_minimax_result = prompt
            self.set_ai_controls_enabled(True)
            self.minimax_panel.show_message(
                "The timeline changed while Generate was running. The existing draft "
                "was kept; generate again to use the latest segments.", "warning")
            return
        if operation.startswith("refine_"):
            prompt = consume_global_refinements(prompt)
        unchanged = (self.prompt_only_workspace and operation.startswith("refine_") and
                     prompt_content(prompt, preserve_spacing=True) ==
                     prompt_content(self.minimax_prompt_text, preserve_spacing=True))
        if unchanged:
            prompt = self.minimax_prompt_text
        self.minimax_prompt_text = prompt
        self.pending_minimax_result = ""
        self.minimax_prompt_updated_at = datetime.now(timezone.utc).isoformat()
        self.set_ai_controls_enabled(True)
        self.magic_overlay.hide_overlay()
        if operation.startswith("refine_"):
            self.minimax_refinement_instructions = ""
        if operation.endswith("references"):
            self.minimax_prompt_mode = "references"
        elif operation.endswith("frames"):
            self.minimax_prompt_mode = "frames"
        mode_label = "annotated" if self.prompt_only_workspace else ("reference" if self.minimax_prompt_mode == "references" else "frame")
        state = f"Cached • {mode_label} {'refined' if operation.startswith('refine_') else 'generated'}"
        if unchanged:
            state = "Refinement completed • no text changes"
        window = self.show_minimax_panel(state)
        self.mark_dirty()
        if unchanged:
            window.show_message("The model returned the production text unchanged. Your annotations were sent, but no wording changes were made. The current draft is still editable.", "warning")
            return
        window.show_message(
            f"{self.workspace_definition.get('name', 'Production')} prompt refined. Save it from the toolbar or when closing."
            if operation.startswith("refine_") else
            f"{self.workspace_definition.get('name', 'Production')} prompt generated. Save it from the toolbar or when closing.",
            "success",
        )

    def copy_pending_minimax_result(self) -> None:
        prompt = str(getattr(self, "pending_minimax_result", ""))
        if prompt:
            QApplication.clipboard().setText(prompt)
            self.statusBar().showMessage("Generated prompt copied to clipboard", 4500)

    def apply_pending_minimax_result(self) -> None:
        prompt = str(getattr(self, "pending_minimax_result", ""))
        if not prompt or self.ai_busy:
            return
        response = QMessageBox.question(
            self, "Use generated result",
            "The generated prompt was held for review. Replace the current unified prompt with it? "
            "Choose No to retain your edits, or Copy generated to keep both.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return
        # Deliberate user approval permits using an otherwise stale generation.
        provider_model = getattr(self, "minimax_operation_provider_model", None)
        self.minimax_operation_signature = self.current_minimax_cache_key(*provider_model) if provider_model else self.current_minimax_cache_key()
        self.minimax_operation_editor_snapshot = (
            self.minimax_prompt_text, self.minimax_refinement_instructions,
        )
        self.minimax_h3_finished(prompt)

    def refine_minimax_prompt(self) -> None:
        if self.ai_busy:
            return
        self.finish_prompt_reviews()
        self.inline_typing.finish()
        self.minimax_editor_changed()
        if not self.minimax_prompt_text.strip():
            if self.minimax_panel:
                self.minimax_panel.show_message("Write or generate a MiniMax H3 prompt before refining it.", "warning")
            return
        credentials = self.ai_credentials()
        if not credentials:
            if self.minimax_panel:
                self.minimax_panel.show_message("Refinement cancelled because no API credentials are configured.", "warning")
            return
        provider, model, key = credentials
        signature = self.current_minimax_cache_key(provider, model)
        self.minimax_operation_signature = signature
        self.minimax_operation_provider_model = (provider, model)
        reference_mode = self.minimax_prompt_mode == "references"
        self.minimax_operation_kind = "refine_references" if reference_mode else "refine_frames"
        self.minimax_operation_editor_snapshot = (
            self.minimax_prompt_text,
            self.minimax_refinement_instructions,
        )
        timeout = self.settings.value("api_timeout", 400, int)
        if self.minimax_panel:
            self.minimax_panel.set_busy(True, "Refining edited prompt…")
        if self.prompt_only_workspace:
            self.minimax_operation_kind = "refine_prompt"
            self.start_ai_worker(refine_minimax_text_prompt,
                (provider, model, key, self.minimax_prompt_text, timeout),
                "Refining the annotated MiniMax prompt…", self.minimax_h3_finished,
                show_main_overlay=False)
            return
        operation = refine_generic_prompt if self.workspace_engine == "generic" else (refine_minimax_h3_reference_prompt if reference_mode else refine_minimax_h3_prompt)
        self.start_ai_worker(
            operation,
            (
                self.segments.copy(), provider, model, key, self.build_director_request(include_requested_length=False),
                self.global_prompt.toPlainText(), self.sfx.isChecked(), self.spoken_dialog.isChecked(),
                self.reduce_music.isChecked(), self.minimax_prompt_text,
                self.minimax_refinement_instructions, timeout,
            ) + (self.active_reference_images(),),
            "Refining the edited MiniMax prompt with timeline continuity context…",
            self.minimax_h3_finished,
            show_main_overlay=False,
        )

    def retry_minimax_operation(self) -> None:
        """Repeat the failed MiniMax operation using current inline controls."""
        if self.minimax_panel:
            self.minimax_panel.clear_message()
        self.minimax_prompt_cache_key = ""
        operation = str(getattr(self, "minimax_operation_kind", "generate_frames"))
        if operation.startswith("refine_"):
            self.refine_minimax_prompt()
        elif operation.endswith("references"):
            self.generate_minimax_prompt_references()
        else:
            self.generate_minimax_prompt_frames()

    def show_toast(self, message: str, duration: int = 3200) -> None:
        toast = getattr(self, "_toast_label", None)
        if toast is None:
            toast = QLabel(self)
            toast.setObjectName("toastMessage")
            toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
            toast.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            toast.setStyleSheet(
                "#toastMessage{background:#17242c;color:#e8f7ff;border:1px solid #68b9ee;"
                "border-radius:7px;padding:9px 16px;font-weight:bold;}"
            )
            self._toast_label = toast
        self._toast_generation = getattr(self, "_toast_generation", 0) + 1
        generation = self._toast_generation
        toast.setText(message)
        toast.adjustSize()
        toast.resize(max(280, toast.width()), toast.height())
        toast.move(max(12, (self.width() - toast.width()) // 2), max(12, self.height() - self.statusBar().height() - toast.height() - 18))
        toast.show()
        toast.raise_()

        def hide_current_toast() -> None:
            if getattr(self, "_toast_generation", 0) == generation:
                toast.hide()

        QTimer.singleShot(duration, hide_current_toast)

    def animate_timeline_durations(self, target_durations: list[float]) -> None:
        target_durations = fit_sequence(target_durations, self.maximum_sequence_duration)
        self.autofit_tail_extension = 0
        if getattr(self, 'duration_animation', None):
            self.duration_animation.stop()
        self.preserve_timeline_scale_for_growth(sum(target_durations))
        if not self.segments or self.timeline.count() != len(self.segments):
            for segment, target in zip(self.segments, target_durations):
                segment.duration = target
            self.refresh_timeline(self.timeline.currentRow())
            self.mark_dirty()
            return
        start_widths = [self.timeline.item(row).sizeHint().width() for row in range(self.timeline.count())]
        target_widths = self.timeline_item_widths(target_durations)
        for segment, target in zip(self.segments, target_durations):
            segment.duration = target
        self.mark_dirty()
        for row, target in enumerate(target_durations):
            card = self.timeline.itemWidget(self.timeline.item(row))
            if isinstance(card, SegmentCard):
                card.resize_handle.duration = target
        self.sync_selected_duration_control()
        self.update_segment_start_times()
        self.update_summary()
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
                    card.duration_label.setText(f"{target_durations[row]:.2f}s")
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
        self.global_build_cascade_requested = False
        self.set_ai_controls_enabled(True)
        self.magic_overlay.hide_overlay()
        title = getattr(self, "ai_activity_title", "AI operation")
        overloaded = message.startswith("Google Gemini is temporarily overloaded")
        if title.startswith("MiniMax") or (self.workspace_engine == "generic" and self.unified_workspace):
            window = self.show_minimax_panel("AI request failed")
            window.set_busy(False, "Gemini overloaded • try again" if overloaded else "AI request failed")
            window.show_message(message, "warning" if overloaded else "error", retry=True)
            return
        if overloaded:
            QMessageBox.warning(self, f"{title}: Gemini overloaded", message)
            self.statusBar().showMessage("Google Gemini is overloaded; try again shortly or choose another model", 8000)
        else:
            QMessageBox.critical(self, f"{title} failed", message)
            self.statusBar().showMessage(f"{title} failed")

    def export_ltx(self) -> None:
        if not self.segments:
            return
        comfy_root = self.resolve_comfy_root()
        if comfy_root is None:
            return
        path = str(self.next_export_path(f"{self.current_project_name}.json"))
        self.normalize_output_dimensions()
        self.statusBar().showMessage(f"Exporting LTX timeline JSON in background: {path}…")
        self.start_disk_job(_export_ltx_snapshot,
                            (copy.deepcopy(self.segments), self.global_prompt.toPlainText(),
                             self.output_width.value(), self.output_height.value(), comfy_root / "input" / "whatdreamscost", path),
                            lambda _result: self.record_export(path),
                            lambda message: self.export_failed(path, message), serialized=True)

    def export_comfy_workflow(self) -> None:
        self.minimax_editor_changed()
        if not self.minimax_prompt_text.strip():
            self.minimax_panel.show_message("Write or generate a MiniMax prompt before exporting its ComfyUI workflow.", "warning")
            return
        comfy_root = self.resolve_comfy_root()
        if comfy_root is None:
            return
        path = str(self.next_export_path(f"{self.current_project_name}-ComfyUI.json"))
        self.statusBar().showMessage("Exporting MiniMax ComfyUI workflow and copying input media…")
        self.start_disk_job(export_minimax_workflow,
            (path, copy.deepcopy(self.segments) if not self.prompt_only_workspace else [], self.minimax_prompt_text,
             copy.deepcopy(self.active_reference_images()), comfy_root, self.output_width.value(), self.output_height.value()),
            lambda _result:self.record_export(path), lambda message:self.export_failed(path,message), serialized=True)

    def resolve_comfy_root(self) -> Path | None:
        configured = str(self.settings.value("comfy_root_dir", "") or "").strip()
        configured_path = Path(configured).expanduser() if configured else None
        if configured_path and configured_path.is_dir():
            return configured_path.resolve()
        selected = choose_directory(self, "Choose ComfyUI root directory", configured or str(Path.home()))
        if not selected:
            return None
        root = Path(selected).expanduser().resolve()
        self.settings.setValue("comfy_root_dir", str(root))
        self.settings.sync()
        return root

    def import_ltx(self, checked: bool = False, path: str | None = None) -> None:
        path = path or choose_document_open(self, "Import LTX timeline JSON", self.settings.value("last_document_dir", str(Path.home())), "LTX timeline JSON (*.json)")
        if not path:
            return
        self.settings.setValue("last_document_dir", str(Path(path).parent))
        self.stage_current_workspace()
        self.cache_current_workspace()
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            fps = float(payload.get("settings", {}).get("frame_rate", FPS))
            loaded = []
            configured_root = str(self.settings.value("comfy_root_dir", "") or "").strip()
            comfy_input = Path(configured_root).expanduser() / "input" if configured_root else None
            raw_segments = list(payload["timeline"]["segments"])
            raw_segments = [raw for _, raw in sorted(enumerate(raw_segments), key=lambda item: (float(item[1].get("start", 0)), item[0]))]
            for index, raw in enumerate(raw_segments):
                if raw.get("type") not in ("image", "video", "text"):
                    continue
                if raw.get("type") == "text":
                    loaded.append(text_segment_from_ltx(raw, index, fps))
                    continue
                cache = APP_CACHE / f"import-{index}-{Path(path).stem}.jpg"
                preview = raw.get("imageB64")
                media_reference = str(raw.get("imageFile") or raw.get("fileName") or "")
                media_candidate = Path(media_reference).expanduser()
                if media_reference and not media_candidate.is_absolute() and comfy_input:
                    media_candidate = comfy_input / Path(media_reference)
                if preview and preview.startswith("data:image/"):
                    write_data_url(preview, cache)
                elif media_candidate.is_file() and raw.get("type") == "image":
                    cache = media_candidate
                else:
                    continue
                media_path = str(media_candidate) if media_candidate.is_file() else str(cache)
                if raw.get("type") == "video" and str(raw.get("videoB64", "")).startswith(("data:video/webm", "data:video/mp4")):
                    video_path = APP_CACHE / f"import-{index}-{Path(raw.get('fileName', 'clip.webm')).name}"
                    write_data_url(raw["videoB64"], video_path)
                    media_path = str(video_path)
                loaded.append(Segment(raw.get("fileName", f"Segment {index + 1}"), str(media_path), str(cache), raw.get("type", "image"), "end" if raw.get("isEndFrame") else "start", raw.get("prompt", ""), max(MIN_DURATION, round(float(raw.get("length", FPS)) / fps, 2)), raw.get("videoDurationFrames"), raw.get("trimStart"), raw.get("id", "")))
            if not loaded:
                raise ValueError("No supported embedded image, video, or text segments were found.")
            self.segments = loaded
            self.minimax_reference_images = [None] * 4
            self.minimax_prompt_text = ""
            self.minimax_refinement_instructions = ""
            self.minimax_prompt_cache_key = ""
            self.minimax_prompt_updated_at = ""
            self.minimax_prompt_mode = "frames"
            self.minimax_drafts = {}
            self.project_type = "ltx" if "ltx" in self.workspace_definitions else next(iter(self.workspace_definitions), "")
            self.workspace_revision += 1
            self.prompt_scope.setCurrentIndex(0)
            self.apply_project_type_ui()
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
            self.mark_dirty()
            self.statusBar().showMessage(f"Imported: {self.current_project_name} · save to Library from toolbar or when closing")
        except Exception as error:
            QMessageBox.critical(self, "Import failed", str(error))

    def project_payload(self, include_media: bool = True) -> dict:
        self.store_minimax_draft()
        frames = []
        for segment in self.segments:
            value = segment.to_dict()
            if include_media:
                value["previewData"] = data_url(segment.preview_path) if segment.kind != "text" and segment.preview_path and Path(segment.preview_path).exists() else None
                source = Path(materialize_source(segment)) if segment.kind != "text" and segment.media_path else None
                value["sourceData"] = data_url(str(source)) if source and source.exists() else None
            frames.append(value)
        return {
            "app": "ltx-director-director",
            "projectVersion": 8,
            "projectType": self.project_type,
            "workspaceDefinition": copy.deepcopy(self.workspace_definition),
            "shotSequence": dict(self.shot_sequence_settings),
            "downloadDirectory": self.download_directory,
            "globalPrompt": self.global_prompt.toPlainText(),
            "directorIntent": self.intent.toPlainText(),
            "directionOptions": {
                "requestedLength": self.requested_length.value(),
                "speakerLanguage": self.speaker_language.currentText(),
                "speakerAccent": self.speaker_accent.currentText(),
            },
            "magicBuild": {
                "sfx": self.sfx.isChecked(),
                "spokenDialog": self.spoken_dialog.isChecked(),
                "hdr": self.hdr.isChecked(),
                "reduceMusic": self.reduce_music.isChecked(),
            },
            "minimaxH3": {
                "prompt": self.minimax_prompt_text,
                "refinementInstructions": self.minimax_refinement_instructions,
                "sourceHash": self.minimax_prompt_cache_key,
                "updatedAt": self.minimax_prompt_updated_at,
                "mode": self.minimax_prompt_mode,
                "referenceImages": reference_slots(self.minimax_reference_images),
                "drafts": {key: dict(value) for key, value in self.minimax_drafts.items()},
            },
            "output": {"width": self.output_width.value(), "height": self.output_height.value()},
            "timelineView": {"scale": self.pixels_per_second, "autoFit": self.timeline_fit_mode, "height": self.timeline_height},
            "frames": frames,
        }

    def load_project_payload(self, payload: dict) -> None:
        self.reject_segment_prompt_review()
        self.shot_sequence_settings = shot_settings(payload.get("shotSequence"))
        if payload.get("app") not in {"ltx-director-director", "ltx-prompt-director-python"}:
            raise ValueError("This is not an Prompt Director project file.")
        definition = payload.get("workspaceDefinition")
        if isinstance(definition, dict) and definition.get("id") not in self.workspace_definitions and not is_obsolete_stock_definition(definition):
            self.workspace_store.save(definition)
            self.reload_workspace_definitions()
        self._loading = True
        self.download_directory = str(payload.get("library", {}).get("downloadDirectory", payload.get("downloadDirectory", "")))
        loaded = []
        cache_key = uuid4().hex[:10]
        for index, original in enumerate(payload.get("frames", [])):
            raw = dict(original)
            is_text = raw.get("kind") == "text"
            preview_path = APP_CACHE / f"project-{cache_key}-{index}.jpg"
            if raw.get("_archivePath"):
                preview_path = Path(raw["_archivePreviewPath"])
                media_path = Path(raw["_archiveMediaPath"])
            else:
                if raw.get("previewData"):
                    write_data_url(raw["previewData"], preview_path)
                media_path = "" if is_text else preview_path
            if not raw.get("_archivePath") and raw.get("sourceData"):
                original_suffix = Path(raw.get("name", "")).suffix.lower()
                header = base64.b64decode(raw["sourceData"].split(",", 1)[1][:5464])
                suffix = MEDIA_SUFFIXES.get(media_mime_from_header(header, original_suffix),
                                            ".webm" if raw.get("kind") == "video" else ".png")
                media_path = APP_CACHE / f"project-source-{cache_key}-{index}{suffix}"
                write_data_url(raw["sourceData"], media_path)
            if not raw.get("_archivePath") and not is_text and preview_path.is_file():
                preview_path = Path(thumbnail_for_image(str(preview_path)))
            raw.update({"preview_path": "" if is_text else str(preview_path), "media_path": str(media_path)})
            if not is_text:
                fallback = ".webm" if raw.get("kind") == "video" else ".png"
                suffix = (raw.get("_archiveSourceSuffix", fallback) if raw.get("_archivePath") else
                          media_suffix(media_path, fallback) if Path(media_path).is_file() else fallback)
                raw["name"] = safe_media_filename(raw.get("name", ""), f"Segment_{index + 1:02d}", suffix=suffix)
            for key in ("previewData", "sourceData"):
                raw.pop(key, None)
            segment = Segment.from_dict(raw)
            if raw.get("_archivePath"):
                segment._archive_source = (raw["_archivePath"], raw["archiveSource"])
                segment._source_suffix = raw.get("_archiveSourceSuffix", "")
                segment._media_digest = raw.get("mediaDigest", "")
                segment._source_size = raw.get("sourceSize")
            loaded.append(segment)
        prompt_only = self.workspace_definitions.get(payload.get("projectType"), {}).get("prompt_mode") == "prompt_only"
        sequence = self.workspace_definitions.get(payload.get("projectType"), {}).get("engine") == "shot_sequence"
        if not loaded and not prompt_only and not sequence:
            raise ValueError("Project contains no supported main-track segments.")
        self.segments = loaded
        self.global_prompt.setPlainText(payload.get("globalPrompt", ""))
        self.intent.setPlainText(payload.get("directorIntent", ""))
        render_prompt_notes(self.intent)
        direction_options = payload.get("directionOptions", {})
        self.requested_length.setValue(requested_length_value(direction_options.get("requestedLength")))
        self.speaker_language.setCurrentText(str(direction_options.get("speakerLanguage", "(Image/context provided)")))
        self.speaker_accent.setCurrentText(str(direction_options.get("speakerAccent", direction_options.get("speakerNationality", "(Image/context provided)"))))
        self.sfx.setChecked(bool(payload.get("magicBuild", {}).get("sfx")))
        magic = payload.get("magicBuild", {})
        self.spoken_dialog.setChecked(bool(magic.get("spokenDialog", magic.get("vocals", False))))
        self.hdr.setChecked(bool(payload.get("magicBuild", {}).get("hdr")))
        self.reduce_music.setChecked(bool(payload.get("magicBuild", {}).get("reduceMusic")))
        minimax = payload.get("minimaxH3", {})
        if isinstance(minimax, str):
            minimax = {"prompt": minimax}
        if not isinstance(minimax, dict):
            minimax = {}
        self.minimax_prompt_text = str(minimax.get("prompt", ""))
        self.minimax_refinement_instructions = str(minimax.get("refinementInstructions", ""))
        self.minimax_prompt_cache_key = str(minimax.get("sourceHash", ""))
        self.minimax_prompt_updated_at = str(minimax.get("updatedAt", ""))
        self.minimax_prompt_mode = "references" if minimax.get("mode") == "references" else "frames"
        self.minimax_reference_images = reference_slots(minimax.get("referenceImages"))
        self.workspace_revision += 1
        self.restore_project_type(payload.get("projectType"), minimax.get("drafts"))
        output = payload.get("output", {})
        self.output_width.setValue(int(output.get("width", 1280)))
        self.output_height.setValue(int(output.get("height", 704)))
        self.normalize_output_dimensions()
        view = payload.get("timelineView", {})
        self.timeline_height = max(184, min(430, int(view.get("height", self.timeline_height))))
        self.timeline_height_handle.current_height = self.timeline_height
        self.set_timeline_height(self.timeline_height)
        auto_fit = bool(view.get("autoFit", False))
        self.timeline_scale.setValue(0 if auto_fit else max(1, min(160, int(view.get("scale", self.pixels_per_second)))))
        self.timeline_fit_mode = auto_fit
        self._loading = False
        self.sync_minimax_panel()
        self.refresh_timeline()
        if auto_fit:
            self.apply_timeline_fit()
        self.project_dirty = False

    def export_directory(self) -> Path:
        fallback = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or str(Path.home())
        return Path(self.download_directory or str(self.settings.value("segment_export_dir", fallback) or fallback)).expanduser()

    def next_export_path(self, filename: str) -> Path:
        directory = self.export_directory()
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / safe_media_filename(filename, "Export")
        base, suffix = target.stem, target.suffix
        counter = 1
        while target.exists() or str(target) in self._reserved_exports:
            target = directory / f"{base} ({counter}){suffix}"
            counter += 1
        self._reserved_exports.add(str(target))
        self.statusBar().showMessage(f"Exporting {target.name}…")
        return target

    def record_export(self, path) -> None:
        path = Path(path)
        self._reserved_exports.discard(str(path))
        self.download_tray.add(path)
        self.downloads_button.setText("•")
        self.downloads_button.setToolTip(f"Export ready: {path.name}\nClick for recent exports; drag files from the tray.")
        self.download_tray.list.setCurrentRow(0)
        if getattr(self, "_download_feedback_animation", None):
            self._download_feedback_animation.stop()
        animation = QVariantAnimation(self)
        animation.setDuration(900)
        animation.setLoopCount(3)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        def pulse(value):
            alpha = round(70 + 130 * math.sin(value * math.pi))
            self.downloads_button.setStyleSheet(f"QPushButton {{background:rgba(59, 153, 115, {alpha}); border:1px solid #77d3a7; border-radius:6px; padding:5px 10px;}}")
        animation.valueChanged.connect(pulse)
        animation.finished.connect(lambda: self.downloads_button.setStyleSheet(""))
        self._download_feedback_animation = animation
        animation.start()
        if self.isVisible():
            QToolTip.showText(self.downloads_button.mapToGlobal(self.downloads_button.rect().bottomLeft()),
                             f"Export ready: {path.name}\nFind it in ↓ Recent exports", self.downloads_button, self.downloads_button.rect(), 3500)
        self.statusBar().showMessage(f"Exported: {path}")

    def export_failed(self, path, message: str) -> None:
        self._reserved_exports.discard(str(path))
        QMessageBox.critical(self, "Export failed", message)

    def open_download_folder(self) -> None:
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.export_directory())))

    def toggle_downloads(self) -> None:
        if self.download_tray.isVisible():
            self.download_tray.hide()
            return
        self.download_tray.refresh()
        self.download_tray.show()
        self.downloads_button.setText("")

    def export_project(self) -> None:
        if not self.has_project_content:
            return
        path = str(self.next_export_path(f"{self.current_project_name}.LTXD"))
        payload = self.project_payload(include_media=False)
        serial = self._edit_serial
        project_id = self.current_project_id
        self.statusBar().showMessage(f"Saving project in background: {path}…")

        def saved(_result):
            if self.current_project_id == project_id and self._edit_serial == serial:
                self.project_dirty = False
            self.record_export(path)

        self.start_disk_job(save_project_archive, (path, payload, copy.deepcopy(self.segments)), saved,
                            lambda message: self.export_failed(path, message), serialized=True)

    def open_project(self, checked: bool = False, path: str | None = None) -> None:
        path = path or choose_document_open(self, "Open Project", self.settings.value("last_document_dir", str(Path.home())), "Prompt Director Project (*.LTXD *.ltxd)")
        if not path:
            return
        self.settings.setValue("last_document_dir", str(Path(path).parent))
        self.stage_current_workspace()
        self.cache_current_workspace()
        self._open_serial += 1
        request = self._open_serial
        self.statusBar().showMessage(f"Opening project in background: {Path(path).name}…")

        def opened(payload):
            if request != self._open_serial:
                return
            try:
                self.load_project_payload(payload)
                self.current_project_id = None
                self.current_project_name = Path(path).stem
                self.update_window_title()
                self.project_dirty = False
                self.mark_dirty()
                self.statusBar().showMessage(f"Opened: {self.current_project_name} · save to Library from toolbar or when closing")
            except Exception as error:
                self._loading = False
                QMessageBox.critical(self, "Open project failed", str(error))

        self.start_disk_job(read_project, (path,), opened,
                            lambda message: QMessageBox.critical(self, "Open project failed", message))
