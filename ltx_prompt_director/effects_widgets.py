"""Native dockable effect browser with a single shared preview decoder."""
from __future__ import annotations
import json
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QSize, QMimeData, QUrl, Signal, QTimer
from PySide6.QtGui import QDrag, QIcon, QPixmap, QDesktopServices, QPainter, QColor, QPolygon
from PySide6.QtCore import QPoint
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QComboBox, QPushButton, QListWidget, QListWidgetItem, QLabel, QDialog,
    QFormLayout, QTextEdit, QCheckBox, QDialogButtonBox, QFileDialog,
    QMessageBox, QSpinBox, QToolButton)
from .effects_library import (MIME, ROOT, catalog, media_path, load_effects,
                              save_effect, remove_effect, refinement)
from .quick_fixes import TARGETS


class EffectDialog(QDialog):
    def __init__(self, owner, entry=None):
        super().__init__(owner)
        self.owner = owner
        self.entry = dict(entry or {'id': 'user:' + uuid4().hex, 'origin': 'Custom'})
        self.setWindowTitle('Effect details' if entry else 'Add your own effect')
        self.resize(600, 560)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(self.entry.get('name', ''))
        self.category = QLineEdit(self.entry.get('category', 'General'))
        self.target = QComboBox()
        for key, label in TARGETS.items():
            self.target.addItem(label, key)
        self.target.setCurrentIndex(max(0, self.target.findData(self.entry.get('target', 'both'))))
        self.menu = QCheckBox('Show in menu')
        self.menu.setChecked(self.entry.get('show_in_menu', False))
        self.prompt = QTextEdit()
        self.prompt.setPlainText(self.entry.get('prompt', ''))
        self.prompt.setAcceptRichText(False)
        self.thumbnail = QLineEdit(self.entry.get('thumbnail', ''))
        self.preview = QLineEdit(self.entry.get('preview', self.entry.get('audio', '')))
        form.addRow('Name', self.name)
        form.addRow('Category', self.category)
        form.addRow('Apply to', self.target)
        form.addRow('', self.menu)
        form.addRow('Refinement instruction', self.prompt)
        for label, field, filt in [('Thumbnail', self.thumbnail, 'Images (*.png *.jpg *.webp *.gif)'),
                                   ('Preview / sound', self.preview, 'Media (*.mp4 *.webm *.mov *.gif *.wav *.mp3 *.ogg)')]:
            row = QHBoxLayout(); row.addWidget(field)
            choose = QPushButton('Browse…')
            choose.clicked.connect(lambda _, f=field, fl=filt: self.choose(f, fl))
            row.addWidget(choose); form.addRow(label, row)
        layout.addLayout(form)
        self.generate = QPushButton('✦ Generate thumbnail with default image model')
        self.generate.clicked.connect(self.generate_thumbnail)
        layout.addWidget(self.generate)
        self.thumbnail_preview = QLabel(); self.thumbnail_preview.setMaximumHeight(140)
        self.thumbnail_preview.setAlignment(Qt.AlignmentFlag.AlignCenter); layout.addWidget(self.thumbnail_preview)
        self.status = QLabel('Choose the default image model in Settings.'); self.status.setWordWrap(True)
        layout.addWidget(self.status)
        from .local_widgets import GenerationLog
        self.log = GenerationLog(); self.log.output.setMaximumHeight(95)
        self.log.hide(); layout.addWidget(self.log)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.save); self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        import threading
        self.cancel_generation = threading.Event()
        self.job_serial = 0

    def choose(self, field, filt):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose media', '', filt)
        if path: field.setText(path)

    def generate_thumbnail(self):
        if not self.name.text().strip() or not self.prompt.toPlainText().strip():
            self.status.setText('Enter a name and refinement instruction first.'); return
        tools = self.owner._available_artwork_tools()
        if not tools:
            self.status.setText('Configure an image source in Settings first.'); return
        from .artwork_tools import render_artwork
        self.cancel_generation.clear()
        self.job_serial += 1
        serial = self.job_serial
        self.generate.setEnabled(False)
        self.status.setText('Generating thumbnail…')
        self.log.clear(); self.log.setVisible(tools[0].provider == 'local')
        def ready(result):
            if serial != self.job_serial: return
            self.generate.setEnabled(True)
            path = result.get('effect-thumbnail', '')
            self.thumbnail.setText(path)
            if path:
                self.thumbnail_preview.setPixmap(QPixmap(path).scaled(380, 140, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self.status.setText('Thumbnail ready.' if path else 'No image returned.')
        def failed(message):
            if serial == self.job_serial:
                self.generate.setEnabled(True); self.status.setText(str(message)[:700])
                self.log.receive_event({'kind': 'error', 'message': str(message)})
        def progress(event):
            if serial != self.job_serial: return
            self.log.receive_event(event)
            if event.get('kind') == 'preview' and event.get('media') == 'image':
                self.thumbnail_preview.setPixmap(QPixmap(event['path']).scaled(380, 140, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            elif event.get('kind') == 'progress':
                from .local_widgets import progress_text
                self.status.setText(progress_text(event))
        self.owner.start_disk_job(render_artwork, ([{
            'id': 'effect-thumbnail', 'artworkPrompt': 'Create an expressive effect library thumbnail: '
            + self.name.text() + '. ' + self.prompt.toPlainText()}], tools[0]), ready, failed, progress=progress, cancel=self.cancel_generation)

    def done(self, result):
        self.cancel_generation.set()
        self.job_serial += 1
        super().done(result)

    def save(self):
        if not self.name.text().strip() or not self.prompt.toPlainText().strip():
            QMessageBox.information(self, 'Effect', 'Enter a name and refinement instruction.'); return
        preview = self.preview.text().strip()
        self.entry.update(name=self.name.text().strip(), category=self.category.text().strip() or 'General',
            target=self.target.currentData(), show_in_menu=self.menu.isChecked(),
            prompt=self.prompt.toPlainText().strip(), thumbnail=self.thumbnail.text().strip(), preview=preview)
        if Path(preview).suffix.lower() in {'.wav', '.mp3', '.ogg'}:
            self.entry['audio'] = preview
        else:
            self.entry.pop('audio', None)
        save_effect(self.owner.settings, self.entry)
        self.accept()


class EffectList(QListWidget):
    def startDrag(self, actions):
        item = self.currentItem()
        if not item or not item.data(Qt.ItemDataRole.UserRole).get('prompt'): return
        data = QMimeData()
        data.setData(MIME, item.data(Qt.ItemDataRole.UserRole)['id'].encode())
        drag = QDrag(self); drag.setMimeData(data); drag.setPixmap(item.icon().pixmap(128, 80))
        self.parent().stop_preview()
        drag.exec(Qt.DropAction.CopyAction)


class EffectsPanel(QWidget):
    apply_requested = Signal(dict, str)

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.setObjectName('effectsPanel')
        try:self.favorites=set(json.loads(owner.settings.value('effects/favorites','[]')))
        except (ValueError,TypeError):self.favorites=set()
        layout = QVBoxLayout(self);layout.setContentsMargins(8,8,8,8);layout.setSpacing(8)
        row = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText('Search effects and presets…')
        self.search.setClearButtonEnabled(True); row.addWidget(self.search)
        layout.addLayout(row)
        self.category = QComboBox();layout.addWidget(self.category)
        row = QHBoxLayout()
        self.scope = QComboBox()
        self.scope.addItem('Selected segment', 'segment'); self.scope.addItem('Unified prompt', 'unified')
        row.addWidget(self.scope)
        row.addWidget(QLabel('Tile size'))
        self.size = QSpinBox(); self.size.setRange(96, 300); self.size.setSingleStep(16)
        self.size.setValue(int(owner.settings.value('effects/tile_size', 160))); row.addWidget(self.size)
        layout.addLayout(row)
        row=QHBoxLayout();row.setSpacing(6)
        self.list_view=QToolButton();self.list_view.setText('▦ Grid / List');self.list_view.setCheckable(True)
        self.list_view.setToolTip('Switch between thumbnail grid and compact list')
        self.list_view.setChecked(owner.settings.value('effects/list_view',False,bool))
        self.list_view.toggled.connect(self.resize_tiles);row.addWidget(self.list_view)
        row.addStretch()
        self.favorites_only=QToolButton();self.favorites_only.setText('Favorites');self.favorites_only.setCheckable(True)
        self.favorites_only.toggled.connect(self.filter);row.addWidget(self.favorites_only)
        self.favorite=QToolButton();self.favorite.setText('♡');self.favorite.setCheckable(True)
        self.favorite.toggled.connect(lambda checked:self.favorite.setText('♥' if checked else '♡'))
        self.favorite.setToolTip('Add or remove the selected effect from favorites')
        self.favorite.clicked.connect(self.toggle_favorite);row.addWidget(self.favorite)
        layout.addLayout(row)
        self.tiles = EffectList(self)
        self.tiles.setObjectName('effectTiles')
        self.tiles.setViewMode(QListWidget.ViewMode.IconMode)
        self.tiles.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.tiles.setMovement(QListWidget.Movement.Static)
        self.tiles.setWordWrap(True); self.tiles.setSpacing(6); self.tiles.setDragEnabled(True)
        layout.addWidget(self.tiles, 1)
        self.details = QLabel('Drag an effect onto a segment or prompt editor, or select one and Apply.')
        self.details.setWordWrap(True); self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setMaximumHeight(150); layout.addWidget(self.details)
        self.media = QComboBox(); layout.addWidget(self.media)
        row = QHBoxLayout()
        self.play = QPushButton('▶ Play sound')
        self.play.clicked.connect(self.play_sound); row.addWidget(self.play)
        self.export = QPushButton('Save sample…'); self.export.clicked.connect(self.save_sample); row.addWidget(self.export)
        layout.addLayout(row)
        self.apply = QPushButton('Apply and refine'); self.apply.clicked.connect(self.apply_current)
        self.apply.setObjectName('effectApply');layout.addWidget(self.apply)
        row = QHBoxLayout()
        for pair in [[('＋ Add Custom', lambda: self.edit(None)), ('⚙ Edit Selected', self.edit_current)],
                     [('Delete / hide', self.remove_current), ('Source Catalog', self.open_catalog)]]:
            row = QHBoxLayout()
            for label, fn in pair:
                button = QPushButton(label); button.clicked.connect(fn); row.addWidget(button)
                button.setObjectName('effectDelete' if label.startswith('Delete') else 'effectSecondary')
                button.setStyleSheet('border-radius:14px;')
            layout.addLayout(row)
        self.player = QMediaPlayer(self); self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.sink = QVideoSink(self); self.player.setVideoSink(self.sink)
        self.sink.videoFrameChanged.connect(self.frame)
        self.player.setLoops(QMediaPlayer.Loops.Infinite)
        self.active_item = None
        self.search.textChanged.connect(self.filter)
        self.category.currentIndexChanged.connect(self.filter)
        self.tiles.currentItemChanged.connect(self.select)
        self.tiles.itemDoubleClicked.connect(lambda *_: self.apply_current())
        self.tiles.itemClicked.connect(self.tile_clicked)
        self.size.valueChanged.connect(self.resize_tiles)
        self.reflow=QTimer(self);self.reflow.setSingleShot(True);self.reflow.timeout.connect(self.resize_tiles)
        self.media.currentIndexChanged.connect(self.preview_media)
        self.availability = QTimer(self); self.availability.setInterval(400)
        self.availability.timeout.connect(self.update_available); self.availability.start()
        self.reload(); self.resize_tiles()

    def reload(self):
        category = self.category.currentText()
        self.entries = load_effects(self.owner.settings)
        self.category.blockSignals(True); self.category.clear(); self.category.addItem('All categories')
        self.category.addItems(sorted({e['category'] for e in self.entries}))
        self.category.setCurrentIndex(max(0, self.category.findText(category))); self.category.blockSignals(False)
        self.filter()

    def filter(self, *_):
        self.stop_preview(); self.tiles.clear()
        query = self.search.text().casefold(); category = self.category.currentText()
        for e in self.entries:
            if category not in ('All categories', e['category']): continue
            if self.favorites_only.isChecked() and e['id'] not in self.favorites:continue
            searchable = {key: e.get(key, '') for key in ('id', 'name', 'category', 'anatomy', 'stage', 'species', 'prompt', 'match_instruction', 'description')}
            if query and query not in json.dumps(searchable, ensure_ascii=False).casefold(): continue
            label = ('▶ ' if e.get('audio') else '') + e['name']
            icon = self.tile_icon(e)
            if e.get('audio') or e.get('source_audio_ids') and e.get('category') == 'Sound':
                pix = icon.pixmap(192, 120)
                if not pix.isNull():
                    painter = QPainter(pix); center = pix.rect().center()
                    painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(12, 20, 28, 210))
                    painter.drawEllipse(center, 21, 21); painter.setBrush(QColor('white'))
                    painter.drawPolygon(QPolygon([center + QPoint(-5, -10), center + QPoint(-5, 10), center + QPoint(11, 0)]))
                    painter.end(); icon = QIcon(pix)
            item = QListWidgetItem(icon, label)
            item.setData(Qt.ItemDataRole.UserRole, e)
            item.setToolTip(e.get('thumbnail_note', '') + '\n' + e.get('prompt', e.get('description', '')))
            self.tiles.addItem(item)
        if self.tiles.count(): self.tiles.setCurrentRow(0)
        else: self.select(None)
        self.resize_tiles()

    def resize_tiles(self, *_):
        size = self.size.value()
        listing=self.list_view.isChecked()
        self.tiles.setViewMode(QListWidget.ViewMode.ListMode if listing else QListWidget.ViewMode.IconMode)
        self.tiles.setFlow(QListWidget.Flow.TopToBottom if listing else QListWidget.Flow.LeftToRight)
        self.tiles.setWrapping(not listing)
        available=max(120,self.tiles.viewport().width()-8)
        columns=max(1,round(available/(size+28)))
        cell=min(size+16,max(112,available//columns))
        actual=min(size,cell-26)
        self.tiles.setIconSize(QSize(72,46) if listing else QSize(actual,round(actual*.63)))
        self.tiles.setGridSize(QSize() if listing else QSize(cell,round(actual*.63)+64))
        for index in range(self.tiles.count()):
            self.tiles.item(index).setSizeHint(QSize(available,66) if listing else QSize(cell-10,round(actual*.63)+54))
        self.owner.settings.setValue('effects/list_view',listing)
        self.owner.settings.setValue('effects/tile_size', size)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'reflow'):self.reflow.start(0)

    def tile_icon(self,entry):
        icon=QIcon(media_path(entry.get('thumbnail')))
        if not icon.isNull():
            original=icon.pixmap(192,120)
            pix=QPixmap(192,120);pix.fill(QColor('#222631'))
            painter=QPainter(pix);painter.drawPixmap((192-original.width())//2,(120-original.height())//2,original);painter.end()
            return QIcon(pix)
        # Legacy/custom presets have no catalog artwork. Give them a real tile
        # using existing vector icons; bundled effect images remain untouched.
        pix=QPixmap(192,120);pix.fill(QColor('#222a36'))
        painter=QPainter(pix)
        symbol='toolbar-preview.svg' if entry.get('category')=='Camera' else 'ai-prompt.svg'
        mark=QIcon(str(Path(__file__).parent/'assets'/symbol)).pixmap(54,54)
        painter.drawPixmap((pix.width()-mark.width())//2,24,mark)
        painter.setPen(QColor('#9db5c9'));painter.drawText(pix.rect().adjusted(8,82,-8,-8),Qt.AlignmentFlag.AlignCenter,entry.get('category','Custom'))
        painter.end()
        return QIcon(pix)

    def toggle_favorite(self):
        entry=self.current()
        if not entry:return
        if entry['id'] in self.favorites:self.favorites.remove(entry['id'])
        else:self.favorites.add(entry['id'])
        self.owner.settings.setValue('effects/favorites',json.dumps(sorted(self.favorites)))
        if self.favorites_only.isChecked():self.filter()
        else:self.favorite.setChecked(entry['id'] in self.favorites)

    def current(self):
        item = self.tiles.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def select(self, item, *_):
        self.stop_preview(); self.media.blockSignals(True); self.media.clear()
        e = self.current()
        self.favorite.setEnabled(bool(e));self.favorite.setChecked(bool(e and e['id'] in self.favorites))
        if not e:
            self.details.setText('No matching effects.'); self.media.blockSignals(False); self.update_available(); return
        details = [e['name'], e.get('origin', ''), e.get('thumbnail_note', ''), e.get('usage_note', '')]
        if e.get('category') == 'Sound' and not e.get('source_audio_ids'):
            details.append('Written sound recipe · no recording supplied for this entry.')
        self.media.addItem('Still thumbnail', '')
        clips = {c['id']: c for c in catalog()['clips']}
        for cid in e.get('reference_clip_ids', []):
            c = clips.get(cid)
            if c: self.media.addItem(c['title'] + ' · ' + c['kind'], c['path'])
        if e.get('preview') and not e.get('audio'): self.media.addItem('Custom preview', e['preview'])
        samples = {a['id']: a for a in catalog()['audio']}
        if e.get('audio'): self.media.addItem('Mixed source soundtrack' if e['id'].startswith('SRC') else 'Custom sound', e['audio'])
        for aid in e.get('source_audio_ids', []):
            if aid in samples: self.media.addItem('♫ ' + samples[aid]['title'] + ' · mixed soundtrack', samples[aid]['path'])
        details.extend(m['evidence_kind'] + ': ' + m['scope'] for m in e.get('reference_matches', []))
        self.details.setText('\n'.join(filter(None, details)))
        self.media.blockSignals(False)
        if self.media.count() > 1 and e.get('category') != 'Sound':
            self.media.setCurrentIndex(1)  # Video previews automatically, audio always manual.
        self.preview_media()
        self.update_available()

    def stop_preview(self):
        self.player.stop()
        if self.active_item is not None:
            self.active_item.setIcon(self.tile_icon(self.active_item.data(Qt.ItemDataRole.UserRole)))
        self.active_item = None

    def preview_media(self, *_):
        self.stop_preview()
        self.player.setLoops(QMediaPlayer.Loops.Infinite)
        path = media_path(self.media.currentData())
        sound = Path(path).suffix.lower() in {'.wav', '.mp3', '.ogg'}
        self.play.setVisible(sound); self.export.setVisible(sound)
        if path and not sound and self.isVisible():
            self.active_item = self.tiles.currentItem()
            self.owner.project_preview_panel.player.pause()
            self.audio.setMuted(True); self.player.setSource(QUrl.fromLocalFile(path)); self.player.play()

    def frame(self, frame):
        if self.active_item and frame.isValid():
            img = frame.toImage()
            if not img.isNull(): self.active_item.setIcon(QIcon(QPixmap.fromImage(img).scaled(
                self.tiles.iconSize(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)))

    def play_sound(self):
        path = media_path(self.media.currentData())
        if Path(path).suffix.lower() not in {'.wav', '.mp3', '.ogg'}: return
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.stop_preview()
        if not playing:
            self.owner.project_preview_panel.player.pause()
            self.audio.setMuted(False); self.player.setLoops(QMediaPlayer.Loops.Once)
            self.player.setSource(QUrl.fromLocalFile(path)); self.player.play()

    def tile_clicked(self, item):
        entry = self.current()
        if entry and (entry.get('audio') or entry.get('category') == 'Sound'):
            for i in range(self.media.count()):
                if Path(str(self.media.itemData(i))).suffix.lower() in {'.wav', '.mp3', '.ogg'}:
                    self.media.setCurrentIndex(i); self.play_sound(); break

    def save_sample(self):
        import shutil
        path = media_path(self.media.currentData())
        if not path: return
        dest, _ = QFileDialog.getSaveFileName(self, 'Save sample', Path(path).name)
        if dest:
            try: shutil.copyfile(path, dest)
            except OSError as error: QMessageBox.warning(self, 'Save sample', str(error))

    def update_available(self):
        e = self.current(); scope = self.scope.currentData()
        self.apply.setEnabled(bool(hasattr(self.owner, 'timeline') and e and e.get('prompt') and e.get('target') in (scope, 'both')
                                   and self.owner.quick_fix_available(scope)))

    def apply_current(self):
        self.update_available()
        if self.apply.isEnabled():
            self.apply_requested.emit(self.current(), self.scope.currentData())

    def edit(self, entry):
        self.stop_preview()
        if EffectDialog(self.owner, entry).exec(): self.reload()

    def edit_current(self):
        if self.current(): self.edit(self.current())

    def remove_current(self):
        if self.current(): remove_effect(self.owner.settings, self.current()); self.reload()

    def open_catalog(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(ROOT / 'index.html')))

    def hideEvent(self, event):
        self.stop_preview(); super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event); self.player.setLoops(QMediaPlayer.Loops.Infinite); self.preview_media()
