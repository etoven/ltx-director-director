"""Shared native audio strip editor and live overlapping audio audition."""
from __future__ import annotations

import copy
import math
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QObject, QTimer, QElapsedTimer, QRectF, QSize, QUrl
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (QWidget, QFrame, QScrollArea, QVBoxLayout, QHBoxLayout,
                              QLabel, QPushButton, QCheckBox, QDoubleSpinBox, QSpinBox,
                              QSizePolicy)

from .audio_tracks import AudioClip, AUDIO_SUFFIXES, FPS, prepare_audio
from .file_dialogs import choose_files
from .responsive_layout import ControlRow


class AudioTransport(QObject):
    position_changed = Signal(int)
    playing_changed = Signal(bool)
    error = Signal(str)

    def __init__(self, parent=None, player_factory=QMediaPlayer, output_factory=QAudioOutput):
        super().__init__(parent)
        self.player_factory, self.output_factory = player_factory, output_factory
        self.clips = []
        self.players = {}
        self.enabled = True
        self.playing = False
        self.position = 0
        self.duration = 0
        self.clock = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.setInterval(25)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.audition = QTimer(self)
        self.audition.setSingleShot(True)
        self.audition.setInterval(130)
        self.audition.timeout.connect(self.finish_scrub)

    def set_clips(self, clips):
        self.pause()
        self.clips = list(clips)
        ids = {clip.id for clip in clips}
        for ident in list(self.players):
            if ident not in ids:
                player, output = self.players.pop(ident)
                player.stop(); player.deleteLater(); output.deleteLater()
        # QMediaPlayer owns streaming decoders; no complete audio buffer is
        # held on the GUI thread, and each overlapping strip can sound at once.
        for clip in clips:
            if clip.id not in self.players:
                output = self.output_factory(self)
                output.setVolume(.8)
                player = self.player_factory(self)
                player.setAudioOutput(output)
                self.players[clip.id] = player, output
                player.mediaStatusChanged.connect(lambda status, p=player: self.media_ready(p, status))
                player.errorOccurred.connect(lambda code, message: self.error.emit(message))
                player.setSource(QUrl.fromLocalFile(clip.media_path))
            else:
                player = self.players[clip.id][0]
                source = QUrl.fromLocalFile(clip.media_path)
                if player.source() != source:
                    player.setSource(source)
        self.seek(self.position)

    def media_ready(self, player, status):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            for clip in self.clips:
                if self.players[clip.id][0] is player:
                    self.sync_clip(clip, self.playing or self.audition.isActive(), force=True)
                    break

    def sync_clip(self, clip, audible, force=False):
        player = self.players[clip.id][0]
        frame = self.position * FPS / 1000
        active = self.enabled and not clip.muted and clip.start_frame <= frame < clip.end_frame
        if active:
            target = round((clip.trim_start + frame - clip.start_frame) * 1000 / FPS)
            if force or abs(player.position() - target) > 110:
                player.setPosition(target)
            if audible:
                player.play()
            else:
                player.pause()
        else:
            player.pause()

    def sync(self, audible=False, force=False):
        for clip in self.clips:
            self.sync_clip(clip, audible, force)

    def seek(self, position, audible=False):
        self.position = max(0, min(int(position), self.duration))
        self.anchor = self.position
        if self.playing:
            self.clock.restart()
        if audible and not self.playing:
            self.audition.start()
        self.sync(self.playing or audible, force=True)
        self.position_changed.emit(self.position)

    def finish_scrub(self):
        if not self.playing:
            self.sync(False, force=True)

    def play(self):
        if self.position >= self.duration:
            self.seek(0)
        self.audition.stop()
        self.playing = True
        self.anchor = self.position
        self.clock.start(); self.timer.start()
        self.sync(True, force=True)
        self.playing_changed.emit(True)

    def pause(self):
        self.timer.stop(); self.audition.stop()
        self.playing = False
        self.sync(False)
        self.playing_changed.emit(False)

    def tick(self):
        self.position = min(self.duration, self.anchor + self.clock.elapsed())
        self.sync(True)
        self.position_changed.emit(self.position)
        if self.position >= self.duration:
            self.pause()


class AudioHeightHandle(QFrame):
    height_changed = Signal(int)
    finished = Signal()

    def __init__(self, height, parent=None):
        super().__init__(parent)
        self.current_height = height
        self.last_height = height or 170
        self.start_y = None
        self.setFixedHeight(12)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setAccessibleName('Resize or collapse audio timeline')
        self.setToolTip('Drag to resize; drag up to collapse. Double-click to collapse or restore.')

    def paintEvent(self, event):
        p = QPainter(self)
        p.setPen(QPen(QColor('#657b88'), 3, Qt.PenStyle.DotLine))
        p.drawLine(self.width() // 2 - 24, 6, self.width() // 2 + 24, 6)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_y = event.globalPosition().y()
            self.start_height = self.current_height
            self.grabMouse()

    def mouseMoveEvent(self, event):
        if self.start_y is not None:
            raw = int(self.start_height + event.globalPosition().y() - self.start_y)
            self.height_changed.emit(0 if raw < 45 else max(90, min(360, raw)))

    def mouseReleaseEvent(self, event):
        if self.start_y is not None:
            self.start_y = None; self.releaseMouse(); self.finished.emit()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.height_changed.emit(0 if self.current_height else self.last_height)
            self.finished.emit()


class AudioCanvas(QWidget):
    TOP = 26
    ROW = 52

    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.drag = None
        self.scrubbing = False
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('Audio waveform timeline')
        self.setToolTip('Drop audio anywhere. Drag a strip to move; drag its edges to trim. Drag the ruler to scrub.')

    def frame_at(self, x):
        return max(0, round((x - self.panel.left) / self.panel.scale * FPS))

    def lane_at(self, y):
        return max(0, int((y - self.TOP) // self.ROW))

    def clip_rect(self, clip):
        return QRectF(self.panel.left + clip.start_frame / FPS * self.panel.scale,
                      self.TOP + clip.lane * self.ROW + 4,
                      max(2, clip.duration_frames / FPS * self.panel.scale), self.ROW - 8)

    def hit(self, point):
        return next((c for c in reversed(self.panel.clips) if self.clip_rect(c).contains(point)), None)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(event.rect(), QColor('#121b22'))
        left, scale = self.panel.left, self.panel.scale
        visible = self.panel.scroll.horizontalScrollBar().value()
        right = visible + self.panel.scroll.viewport().width()
        lanes = max(2, max((c.lane + 1 for c in self.panel.clips), default=0) + 1)
        for lane in range(lanes):
            y = self.TOP + lane * self.ROW
            p.fillRect(QRectF(0, y, self.width(), self.ROW - 1), QColor('#18242c' if lane % 2 == 0 else '#152028'))
            p.setPen(QColor('#90a8b7')); p.drawText(5, y + 30, f'A{lane + 1}')
        interval = max(1, math.ceil(70 / scale))
        first = max(0, int((visible - left) / scale // interval) * interval)
        for second in range(first, math.ceil((right - left) / scale) + 1, interval):
            x = round(left + second * scale)
            p.setPen(QColor('#2b3944')); p.drawLine(x, self.TOP, x, self.height())
            p.setPen(QColor('#96aab8')); p.drawText(x + 4, 17, f'{second}s')
        if self.panel.sequence_duration:
            x = round(left + self.panel.sequence_duration * scale)
            p.setPen(QPen(QColor('#778696'), 1, Qt.PenStyle.DashLine))
            p.drawLine(x, self.TOP, x, self.height())
        colors = ('#65d9ca', '#a99af9', '#f0b77d', '#85b5f3')
        for clip in self.panel.clips:
            rect = self.clip_rect(clip)
            if rect.right() < visible or rect.left() > right:
                continue
            selected = clip.id == self.panel.selected
            color = QColor('#687984' if clip.muted or not self.panel.enabled.isChecked() else colors[clip.lane % len(colors)])
            p.setPen(QPen(color, 2 if selected else 1))
            fill = QColor(color); fill.setAlpha(46 if selected else 22)
            p.setBrush(fill); p.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 4, 4)
            p.save(); p.setClipRect(rect.adjusted(4, 2, -4, -2))
            p.setPen(color)
            p.drawText(rect.adjusted(6, 1, -4, -25), Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(clip.name, Qt.TextElideMode.ElideRight, max(1, int(rect.width() - 12))))
            center = rect.top() + 30
            begin = max(int(rect.left()) + 5, visible)
            end = min(int(rect.right()) - 4, right)
            for x in range(begin, end, 3):
                local = (x - rect.left()) / scale
                index = int((clip.trim_start / FPS + local) / clip.source_duration * len(clip.peaks))
                peak = clip.peaks[max(0, min(len(clip.peaks) - 1, index))] if clip.peaks else 0
                height = max(.7, min(1., peak) * 10)
                p.drawLine(QRectF(x, center - height, 0, height * 2).topLeft(),
                           QRectF(x, center - height, 0, height * 2).bottomLeft())
            p.restore()
            p.setPen(QPen(color, 2))
            for x in (rect.left() + 3, rect.right() - 3):
                p.drawLine(round(x), round(rect.top() + 12), round(x), round(rect.bottom() - 6))
        if not self.panel.clips:
            p.setPen(QColor('#8299a8'))
            p.drawText(QRectF(left + 12, self.TOP + 4, max(1, self.width() - left - 30), 76),
                       Qt.AlignmentFlag.AlignVCenter, 'Drop audio here · independent strips, overlaps and gaps')
        x = round(left + self.panel.transport.position / 1000 * scale)
        p.setPen(QPen(QColor('#ffce75'), 2)); p.drawLine(x, 0, x, self.height())
        p.setBrush(QColor('#ffce75')); p.drawEllipse(QRectF(x - 4, 1, 8, 8))

    def scrub(self, event):
        self.panel.seek(round(self.frame_at(event.position().x()) / FPS * 1000), audible=True)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.setFocus()
        clip = self.hit(event.position()) if event.position().y() >= self.TOP else None
        if clip:
            self.panel.transport.pause()
            self.panel.select(clip.id)
            rect = self.clip_rect(clip)
            edge = 'left' if event.position().x() - rect.left() < 7 else 'right' if rect.right() - event.position().x() < 7 else 'move'
            self.drag = (clip, copy.deepcopy(clip), self.frame_at(event.position().x()), edge)
        else:
            self.scrubbing = True; self.scrub(event)
        self.grabMouse()

    def mouseMoveEvent(self, event):
        if self.scrubbing:
            self.scrub(event)
        elif self.drag:
            clip, before, anchor, edge = self.drag
            clip.start_frame, clip.trim_start, clip.duration_frames, clip.lane = before.start_frame, before.trim_start, before.duration_frames, before.lane
            delta = self.frame_at(event.position().x()) - anchor
            if edge == 'left':
                clip.trim_left(before.start_frame + delta)
            elif edge == 'right':
                clip.trim_right(before.end_frame + delta)
            else:
                clip.move(before.start_frame + delta, self.lane_at(event.position().y()))
            self.panel.update_fields(); self.panel.update_extent()
        else:
            clip = self.hit(event.position())
            if clip:
                self.setToolTip(f'{clip.name}\nStart {clip.start_frame / FPS:.3f}s · End {clip.end_frame / FPS:.3f}s\n'
                                f'Source in {clip.trim_start / FPS:.3f}s · Track {clip.lane + 1}')
            edge = clip and min(abs(event.position().x() - self.clip_rect(clip).left()),
                                abs(event.position().x() - self.clip_rect(clip).right())) < 7
            self.setCursor(Qt.CursorShape.SizeHorCursor if edge else Qt.CursorShape.OpenHandCursor if clip else Qt.CursorShape.CrossCursor)

    def mouseReleaseEvent(self, event):
        if self.drag:
            clip, before, _, _ = self.drag
            if clip != before:
                self.panel.edited()
        self.drag = None; self.scrubbing = False; self.releaseMouse()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.panel.remove_selected()
        elif event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            delta = -1 if event.key() == Qt.Key.Key_Left else 1
            frame = round(self.panel.transport.position * FPS / 1000) + delta
            self.panel.seek(round(max(0, frame) * 1000 / FPS), audible=True)
        elif event.key() == Qt.Key.Key_Space:
            self.panel.toggle_play()
        else:
            super().keyPressEvent(event)

    def drop_paths(self, event):
        return [u.toLocalFile() for u in event.mimeData().urls()
                if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in AUDIO_SUFFIXES]

    def dragEnterEvent(self, event):
        if self.drop_paths(event):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if self.drop_paths(event):
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = self.drop_paths(event)
        if paths:
            self.panel.add_paths(paths, self.frame_at(event.position().x()), self.lane_at(event.position().y()))
            event.acceptProposedAction()


class AudioTimelinePanel(QFrame):
    changed = Signal()
    seek_requested = Signal(int, bool)

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setObjectName('audioTimelinePanel')
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.clips = []; self.selected = None; self.scale = 65.; self.left = 38
        self.sequence_duration = 0
        self._generation = 0
        self.transport = AudioTransport(self)
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)
        header = QWidget(); row = QHBoxLayout(header); row.setContentsMargins(6, 2, 6, 2); row.setSpacing(7)
        self.collapse = QPushButton('▾'); self.collapse.setFixedWidth(24)
        self.collapse.setToolTip('Collapse or reopen audio timeline'); self.collapse.clicked.connect(self.toggle_collapse)
        row.addWidget(self.collapse)
        title = QLabel('AUDIO'); title.setStyleSheet('color:#8edbce;font-weight:700;letter-spacing:1px;')
        row.addWidget(title)
        self.enabled = QCheckBox('Conditioning'); self.enabled.setChecked(True)
        self.enabled.setToolTip('Include independent audio in audition and LTX timeline export')
        self.enabled.toggled.connect(self.enabled_changed); row.addWidget(self.enabled)
        self.add = QPushButton('＋ Audio'); self.add.clicked.connect(self.choose); row.addWidget(self.add)
        self.play = QPushButton('▶'); self.play.setFixedWidth(30); self.play.setToolTip('Play / pause audio (Space on timeline)')
        self.play.clicked.connect(self.toggle_play); row.addWidget(self.play)
        self.time = QLabel('00:00.000'); self.time.setMinimumWidth(85); row.addWidget(self.time)
        self.summary = QLabel('Drop audio · gaps and overlaps allowed')
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self.summary, 1)
        layout.addWidget(header)
        self.body = QWidget(); column = QVBoxLayout(self.body); column.setContentsMargins(0, 0, 0, 0); column.setSpacing(0)
        controls = ControlRow(); controls.setContentsMargins(6, 0, 6, 2)
        self.start, self.end, self.source_in = [QDoubleSpinBox() for _ in range(3)]
        for label, widget in (('Start', self.start), ('End', self.end), ('Source in', self.source_in)):
            widget.setRange(0, 86400); widget.setDecimals(3); widget.setSingleStep(1 / FPS); widget.setSuffix(' s')
            widget.setMaximumWidth(108); widget.setKeyboardTracking(False)
            widget.setAccessibleName('Audio ' + label.lower())
            controls.addWidget(QLabel(label)); controls.addWidget(widget)
            widget.editingFinished.connect(self.fields_edited)
        self.lane = QSpinBox(); self.lane.setRange(1, 999); self.lane.setMaximumWidth(60)
        self.lane.setKeyboardTracking(False); self.lane.editingFinished.connect(self.fields_edited)
        controls.addWidget(QLabel('Track')); controls.addWidget(self.lane)
        self.mute = QCheckBox('Mute'); self.mute.toggled.connect(self.mute_selected); controls.addWidget(self.mute)
        self.remove = QPushButton('×'); self.remove.setFixedWidth(26); self.remove.setToolTip('Remove selected audio strip')
        self.remove.clicked.connect(self.remove_selected); controls.addWidget(self.remove); controls.addStretch()
        column.addLayout(controls)
        self.scroll = QScrollArea(); self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setMinimumHeight(0); self.scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.canvas = AudioCanvas(self); self.scroll.setWidget(self.canvas); column.addWidget(self.scroll, 1)
        layout.addWidget(self.body)
        height = max(0, min(360, owner.settings.value('audio_panel_height', 0, int)))
        self.handle = AudioHeightHandle(height, self)
        self.handle.last_height = max(90, min(360, owner.settings.value('audio_panel_last_height', 170, int)))
        self.handle.height_changed.connect(self.set_body_height); self.handle.finished.connect(self.save_height)
        layout.addWidget(self.handle); self.set_body_height(height)
        self.transport.position_changed.connect(self.show_position)
        self.transport.playing_changed.connect(lambda playing: self.play.setText('Ⅱ' if playing else '▶'))
        self.transport.error.connect(lambda message: self.owner.statusBar().showMessage('Audio preview: ' + message, 8000))
        self.update_fields()

    def sizeHint(self):
        return QSize(400, super().sizeHint().height())

    def choose(self):
        paths = choose_files(self, 'Add conditioning audio', self.owner.settings.value('audio_import_dir', ''),
                             'Audio (*.wav *.mp3 *.flac *.ogg *.opus *.m4a *.aac *.aif *.aiff *.webm *.mp4)')
        if paths:
            self.owner.settings.setValue('audio_import_dir', str(Path(paths[0]).parent))
            lane = max((c.lane for c in self.clips), default=-1) + 1
            self.add_paths(paths, round(self.transport.position * FPS / 1000), lane)

    def add_paths(self, paths, frame=0, lane=0):
        generation = self._generation
        # Project changes invalidate imports, while workspace-view changes keep
        # the same project audio and are safe to finish in the new view.
        self.summary.setText('Reading audio and drawing waveforms…')
        def ready(result):
            if generation != self._generation:
                return
            clips, errors = result
            for index, clip in enumerate(clips):
                clip.move(frame, lane + index); self.clips.append(clip)
            if clips:
                self.select(clips[-1].id); self.edited()
                if not self.handle.current_height:
                    self.set_body_height(self.handle.last_height)
            else:
                self.update_extent()
            if errors:
                self.owner.statusBar().showMessage('; '.join(errors), 12000)
        self.owner.start_disk_job(prepare_audio, (list(paths),), ready,
            lambda message: self.owner.statusBar().showMessage(message, 8000) if generation == self._generation else None)

    def set_project(self, records, enabled=True):
        self._generation += 1
        self.transport.pause()
        self.clips = [AudioClip.from_dict(r) for r in records]
        self.selected = None
        self.enabled.blockSignals(True); self.enabled.setChecked(enabled); self.enabled.blockSignals(False)
        self.transport.enabled = enabled
        self.transport.position = 0
        self.update_extent(); self.transport.set_clips(self.clips); self.update_fields()

    def import_ltx(self, records, fps, input_directory):
        self.set_project([])
        generation = self._generation
        paths, timing = [], []
        for record in records:
            path = Path(str(record.get('audioFile') or record.get('fileName') or ''))
            if not path.is_absolute() and input_directory:
                path = input_directory / path
            paths.append(str(path)); timing.append(record)
        def ready(result):
            if generation != self._generation:
                return
            clips, errors = result
            self.clips = clips
            self.select(clips[0].id if clips else None); self.edited()
            if errors:
                self.owner.statusBar().showMessage('; '.join(errors), 12000)
        if paths:
            self.owner.start_disk_job(prepare_ltx_audio, (paths, timing, fps), ready,
                lambda message: self.owner.statusBar().showMessage(message, 8000) if generation == self._generation else None)

    def records(self):
        return [c.to_dict() for c in self.clips]

    def enabled_changed(self, enabled):
        self.transport.enabled = enabled; self.transport.sync(self.transport.playing)
        self.canvas.update(); self.changed.emit()

    def select(self, ident):
        self.selected = ident; self.update_fields(); self.canvas.update()

    def current(self):
        return next((c for c in self.clips if c.id == self.selected), None)

    def update_fields(self):
        clip = self.current()
        for field in (self.start, self.end, self.source_in, self.lane, self.mute, self.remove):
            field.setEnabled(clip is not None)
        if clip:
            for field, value in ((self.start, clip.start_frame / FPS), (self.end, clip.end_frame / FPS),
                                 (self.source_in, clip.trim_start / FPS), (self.lane, clip.lane + 1), (self.mute, clip.muted)):
                field.blockSignals(True)
                field.setChecked(value) if field is self.mute else field.setValue(value)
                field.blockSignals(False)

    def fields_edited(self):
        clip = self.current()
        if not clip:
            return
        before = clip.to_dict()
        start, end = round(self.start.value() * FPS), round(self.end.value() * FPS)
        source = round(self.source_in.value() * FPS)
        # Start moves the strip while preserving its duration. End trims the
        # source out; Source in slips the source while retaining the placement.
        if start != clip.start_frame:
            clip.move(start)
        if end != before['start_frame'] + before['duration_frames']:
            clip.trim_right(end)
        clip.trim_start = max(0, min(clip.source_frames - 1, source))
        clip.duration_frames = min(clip.duration_frames, clip.source_frames - clip.trim_start)
        clip.lane = self.lane.value() - 1
        self.update_fields()
        if before != clip.to_dict():
            self.edited()

    def mute_selected(self, value):
        if self.current():
            self.current().muted = value; self.edited()

    def remove_selected(self):
        clip = self.current()
        if clip:
            self.clips.remove(clip); self.select(None); self.edited()

    def edited(self):
        self.update_extent(); self.transport.set_clips(self.clips); self.changed.emit()

    def update_extent(self):
        total = max(self.sequence_duration, max((c.end_frame / FPS for c in self.clips), default=0), 1)
        self.transport.duration = round(total * 1000)
        lanes = max(2, max((c.lane + 1 for c in self.clips), default=0) + 1)
        self.canvas.resize(max(self.scroll.viewport().width(), round(self.left + total * self.scale + 48)),
                           max(self.scroll.viewport().height(), self.canvas.TOP + lanes * self.canvas.ROW))
        self.canvas.update()
        self.summary.setText(f'{len(self.clips)} strips · {total:.2f}s' if self.clips else 'Drop audio · gaps and overlaps allowed')

    def set_timeline(self, duration, scale, left=38):
        self.sequence_duration = duration; self.scale = max(.1, scale); self.left = left
        self.update_extent()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'canvas'):
            self.update_extent()

    def set_body_height(self, height):
        height = 0 if height < 45 else max(90, min(360, height))
        self.handle.current_height = height
        if height:
            self.handle.last_height = height
        self.body.setFixedHeight(height); self.body.setVisible(bool(height))
        self.collapse.setText('▾' if height else '▸')

    def save_height(self):
        self.owner.settings.setValue('audio_panel_height', self.handle.current_height)
        self.owner.settings.setValue('audio_panel_last_height', self.handle.last_height)

    def toggle_collapse(self):
        self.set_body_height(0 if self.handle.current_height else self.handle.last_height); self.save_height()

    def toggle_play(self):
        self.transport.pause() if self.transport.playing else self.transport.play()

    def seek(self, position, audible=False):
        self.transport.seek(position, audible); self.seek_requested.emit(self.transport.position, audible)

    def show_position(self, position):
        minutes, seconds = divmod(position / 1000, 60)
        self.time.setText(f'{int(minutes):02d}:{seconds:06.3f}')
        self.canvas.update()


def prepare_ltx_audio(paths, records, fps):
    clips, errors = [], []
    for path, record in zip(paths, records):
        prepared, failed = prepare_audio([path])
        errors.extend(failed)
        for clip in prepared:
            clip.id = str(record.get('id') or clip.id)
            clip.move(round(float(record.get('start', 0)) / fps * FPS), record.get('lane', 0))
            clip.trim_start = max(0, min(clip.source_frames - 1, round(float(record.get('trimStart', 0)) / fps * FPS)))
            clip.duration_frames = max(1, min(clip.source_frames - clip.trim_start,
                round(float(record.get('length', clip.source_frames)) / fps * FPS)))
            clips.append(clip)
    return clips, errors
