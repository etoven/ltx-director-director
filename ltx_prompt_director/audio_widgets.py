"""Dockable audio generators and shared onboarding / settings controls."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import shutil
import threading

from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QTextEdit, QPushButton, QCheckBox, QLabel, QComboBox, QDoubleSpinBox, QSpinBox,
    QSlider, QTabWidget, QDockWidget)

from .file_dialogs import choose_document_open, choose_directory
from .audio_generation import NAMES, KEY, load_audio_settings, setup_audio, generate_audio, music_workflow


def show_status(panel, message):
    from shiboken6 import isValid
    if isValid(panel):
        panel.status.setText(str(message))


class AudioModelsPanel(QWidget):
    def __init__(self, owner, parent=None):
        super().__init__(parent); self.owner = owner; self.fields = {}; self.busy = False
        outer = QVBoxLayout(self)
        note = QLabel('Set up each audio engine independently, or point to an existing Python environment and model files. Downloads run in the background.')
        note.setWordWrap(True); outer.addWidget(note)
        self.tabs = QTabWidget(); outer.addWidget(self.tabs)
        values = load_audio_settings(owner.settings)
        for kind, name in NAMES.items():
            page = QWidget(); form = QFormLayout(page); self.tabs.addTab(page, name)
            fields = {}; self.fields[kind] = fields
            keys = [('source', 'Engine source folder'), ('python', 'Python interpreter'), ('model', 'Model folder' if kind == 'dialog' else 'Model' if kind == 'foley' else 'Diffusion model')]
            if kind == 'music':
                keys += [('encoder', 'Text encoder'), ('vae', 'Audio VAE')]
            for key, label in keys:
                field = QLineEdit(str(values[kind][key])); fields[key] = field
                if kind == 'foley' and key == 'model':
                    field.setToolTip('Stable Audio 3 model name: small-sfx, small-sfx-base, or medium')
                row = QHBoxLayout(); row.addWidget(field)
                if not (kind == 'foley' and key == 'model'):
                    browse = QPushButton('…'); browse.setFixedWidth(32)
                    browse.clicked.connect(lambda _, f=field, folder=key == 'source' or (kind == 'dialog' and key == 'model'): self.browse(f, folder))
                    row.addWidget(browse)
                form.addRow(label, row)
            device = QComboBox(); device.addItems(['cuda', 'cpu']); device.setCurrentText(values[kind].get('device', 'cuda'))
            fields['device'] = device; form.addRow('Device', device)
            controls = QHBoxLayout()
            for label, download in [('Prepare engine', False), ('Download models', True)]:
                button = QPushButton(label); button.clicked.connect(lambda _, k=kind, d=download: self.prepare(k, d)); controls.addWidget(button)
            form.addRow(controls)
        self.status = QLabel(); self.status.setWordWrap(True); outer.addWidget(self.status); outer.addStretch()

    def browse(self, field, folder):
        path = choose_directory(self, 'Choose folder', field.text()) if folder else choose_document_open(self, 'Choose file', field.text(), 'All files (*)')
        if path:
            field.setText(path)

    def value(self):
        return {kind: {key: field.currentText() if isinstance(field, QComboBox) else field.text().strip()
                       for key, field in fields.items()} for kind, fields in self.fields.items()}

    def save(self):
        self.owner.settings.setValue(KEY, json.dumps(self.value()))
        self.owner.queue_settings_sync()

    def prepare(self, kind, download):
        if self.busy:
            return
        self.busy = True; self.setEnabled(False); self.status.setText('Preparing ' + NAMES[kind] + '…')
        self.save()
        def finished(config):
            values = load_audio_settings(self.owner.settings); values[kind] = config
            self.owner.settings.setValue(KEY, json.dumps(values)); self.owner.queue_settings_sync()
            from shiboken6 import isValid
            if not isValid(self):
                return
            self.busy = False; self.setEnabled(True)
            for key, field in self.fields[kind].items():
                if isinstance(field, QLineEdit):
                    field.setText(str(config[key]))
            self.status.setText('Model files ready.' if download else 'Engine ready. Download its model files next.')
        def failed(message):
            from shiboken6 import isValid
            if not isValid(self):
                return
            self.busy = False; self.setEnabled(True); self.status.setText(str(message)[:1400])
        self.owner.start_disk_job(setup_audio, (kind, self.value()[kind], download), finished, failed,
                                  progress=lambda event: show_status(self, event.get('message', 'Preparing audio…')))


class AudioGenerationPanel(QWidget):
    def __init__(self, owner, kind):
        super().__init__(); self.owner = owner; self.kind = kind; self.serial = 0; self.output_path = ''; self.job = None
        self.sampled_request = None; self.sampled_serial = None
        self.cancel_event = threading.Event()
        outer = QVBoxLayout(self); outer.setContentsMargins(8, 6, 8, 6)
        self.prompt = QTextEdit(); self.prompt.setAcceptRichText(False); self.prompt.setMinimumHeight(80)
        self.prompt.setPlaceholderText('Write the dialogue to speak…' if kind == 'dialog' else 'Describe the sound, texture and environment…' if kind == 'foley' else 'Describe the music, vocals, instruments and arrangement…')
        outer.addWidget(self.prompt, 1)
        form = QFormLayout(); outer.addLayout(form)
        self.sample = QLineEdit(); self.language = QComboBox()
        self.language.addItems(['EN', 'ZH', 'JA', 'ES', 'AR'])
        self.lyrics = QTextEdit(); self.lyrics.setAcceptRichText(False); self.lyrics.setPlaceholderText('Lyrics and [Verse] / [Chorus] tags (optional for instrumental music)')
        self.lyrics.setMaximumHeight(120)
        if kind == 'dialog':
            from .dialogue_controls import EMOTIONS
            row = QHBoxLayout(); row.addWidget(self.sample); browse = QPushButton('Sample…'); browse.clicked.connect(self.choose_sample); row.addWidget(browse)
            form.addRow('Voice sample', row); form.addRow('Language', self.language)
            self.emotion = QComboBox(); self.emotion.addItem('Match voice sample', '')
            for emotion in EMOTIONS:
                self.emotion.addItem(emotion.title(), emotion)
            self.emotion_strength = QDoubleSpinBox(); self.emotion_strength.setRange(0, 1)
            self.emotion_strength.setSingleStep(.05); self.emotion_strength.setDecimals(2); self.emotion_strength.setValue(.75)
            self.emotion_strength.setToolTip('Emotion strength: 0 to 1'); self.emotion_strength.setEnabled(False)
            emotion_row = QHBoxLayout(); emotion_row.addWidget(self.emotion, 1); emotion_row.addWidget(self.emotion_strength)
            form.addRow('Emotion', emotion_row)
            self.speed = QDoubleSpinBox(); self.speed.setRange(.5, 2); self.speed.setSingleStep(.1)
            self.speed.setDecimals(2); self.speed.setValue(1); self.speed.setSuffix('×')
            self.speed.setToolTip('Speaking speed. 1× keeps the natural pace; higher values speak faster.')
            form.addRow('Speed', self.speed)
            self.emotion.currentIndexChanged.connect(lambda: self.emotion_strength.setEnabled(bool(self.emotion.currentData())))
            self.emotion.currentIndexChanged.connect(self.input_changed)
            self.emotion_strength.valueChanged.connect(self.input_changed); self.speed.valueChanged.connect(self.input_changed)
        elif kind == 'music':
            outer.insertWidget(1, self.lyrics)
        self.duration = QDoubleSpinBox(); self.duration.setDecimals(2); self.duration.setRange(.1, 300 if kind == 'music' else 47)
        self.duration.setValue(60 if kind == 'music' else 10); self.duration.setSuffix(' s')
        if kind != 'dialog':
            form.addRow('Duration', self.duration)
        self.seed = QSpinBox(); self.seed.setRange(-1, 2147483647); self.seed.setValue(-1); self.seed.setSpecialValueText('Random')
        if kind != 'dialog':
            form.addRow('Seed', self.seed)
        self.refine = QCheckBox('Refine prompt with AI before sampling'); outer.addWidget(self.refine)
        row = QHBoxLayout(); self.generate = QPushButton('Generate'); self.generate.clicked.connect(self.start)
        self.cancel = QPushButton('Cancel'); self.cancel.clicked.connect(self.cancel_generation); self.cancel.setEnabled(False)
        self.export = QPushButton('Export audio'); self.export.clicked.connect(self.export_audio); self.export.setEnabled(False)
        for button in (self.generate, self.cancel, self.export):
            row.addWidget(button)
        outer.addLayout(row)
        playback = QHBoxLayout(); self.play = QPushButton('Play'); self.play.setEnabled(False); self.play.clicked.connect(self.toggle_play)
        self.seek = QSlider(Qt.Orientation.Horizontal); self.seek.setRange(0, 0)
        self.seek.sliderMoved.connect(lambda value: self.player.setPosition(value))
        playback.addWidget(self.play); playback.addWidget(self.seek, 1); outer.addLayout(playback)
        self.player = QMediaPlayer(self); self.audio = QAudioOutput(self); self.player.setAudioOutput(self.audio)
        self.player.durationChanged.connect(lambda value: self.seek.setRange(0, value))
        self.player.positionChanged.connect(self.seek.setValue)
        self.player.playbackStateChanged.connect(lambda state: self.play.setText('Pause' if state == QMediaPlayer.PlaybackState.PlayingState else 'Play'))
        self.status = QLabel(''); self.status.setWordWrap(True); outer.addWidget(self.status)
        self.prompt.textChanged.connect(self.input_changed); self.sample.textChanged.connect(self.input_changed)
        self.lyrics.textChanged.connect(self.input_changed); self.language.currentIndexChanged.connect(self.input_changed)
        self.duration.valueChanged.connect(self.input_changed); self.seed.valueChanged.connect(self.input_changed)
        self.refine.toggled.connect(self.input_changed)

    def input_changed(self, *_):
        self.serial += 1

    def choose_sample(self):
        path = choose_document_open(self, 'Choose voice sample', '', 'Audio (*.wav *.mp3 *.flac *.ogg *.m4a *.opus)')
        if path:
            self.sample.setText(path)

    def request(self):
        request = dict(audio_kind=self.kind, audio_config=load_audio_settings(self.owner.settings)[self.kind],
                    prompt=self.prompt.toPlainText(), lyrics=self.lyrics.toPlainText(), sample=self.sample.text().strip(),
                    language=self.language.currentText(), duration=self.duration.value(), seed=self.seed.value(),
                    refine=self.refine.isChecked(), steps=30 if self.kind == 'music' else 8,
                    timeout=self.owner.settings.value('api_timeout', 400, int))
        if self.kind == 'dialog':
            request.update(emotion=self.emotion.currentData(), emotion_strength=self.emotion_strength.value(), speed=self.speed.value())
        return request

    def start(self):
        if not self.prompt.toPlainText().strip():
            self.status.setText('Write an audio prompt first.'); return
        if self.kind == 'dialog' and not Path(self.sample.text()).is_file():
            self.status.setText('Choose a voice sample first.'); return
        from .audio_generation import validate_audio
        request = self.request()
        try:
            validate_audio(request['audio_config'], self.kind)
        except ValueError as error:
            self.status.setText(str(error)); return
        credentials = self.owner.ai_credentials() if self.refine.isChecked() else None
        if self.refine.isChecked() and not credentials:
            return
        self.serial += 1; serial = self.serial
        project = self.owner.current_project_id; revision = self.owner.workspace_revision
        self.cancel_event = threading.Event(); self.generate.setEnabled(False); self.cancel.setEnabled(True)
        self.status.setText('Queued…')
        def reset():
            from shiboken6 import isValid
            if not isValid(self):
                return False
            self.generate.setEnabled(True); self.cancel.setEnabled(False)
            return True
        def ready(result):
            if not reset():
                return
            if serial != self.serial or project != self.owner.current_project_id or revision != self.owner.workspace_revision:
                self.status.setText('Inputs changed. The earlier audio remains available in the render queue.'); return
            self.output_path = result['path']; self.player.setSource(QUrl.fromLocalFile(self.output_path))
            self.sampled_request = {**copy.deepcopy(request), **result.get('sampling', {})}
            self.sampled_serial = serial
            self.play.setEnabled(True); self.export.setEnabled(True); self.status.setText('Audio ready.')
        def failed(message):
            if reset():
                self.status.setText(str(message)[:1200])
        self.job = self.owner.start_render_job(generate_audio, (request, credentials), ready, failed,
            title=NAMES[self.kind], source='local', model=NAMES[self.kind], kind='Audio', cancel=self.cancel_event,
            progress=lambda event: show_status(self, event.get('message') or event.get('stage') or 'Generating…') if serial == self.serial else None,
            cancelled=lambda: failed('Audio generation cancelled.'))

    def cancel_generation(self):
        self.cancel_event.set()
        if self.job:
            self.owner.render_queue.cancel_job(self.job.id)

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def export_audio(self):
        if not self.output_path:
            return
        owner = self.owner; target = owner.next_export_path(owner.current_project_name + '-' + self.kind + '.wav')
        self.export.setEnabled(False)
        def ready(_):
            self.export.setEnabled(True); owner.record_export(target); self.status.setText('Exported ' + target.name)
        def failed(message):
            self.export.setEnabled(True); owner._reserved_exports.discard(str(target)); self.status.setText(str(message))
        owner.start_disk_job(shutil.copy2, (self.output_path, str(target)), ready, failed)

    def export_workflow(self):
        request = copy.deepcopy(self.sampled_request) if self.sampled_request and self.sampled_serial == self.serial else self.request()
        owner = self.owner
        target = owner.next_export_path(owner.current_project_name + '-music.comfy.json')
        project_name = owner.current_project_name
        def write():
            graph = music_workflow(request, project_name)
            target.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding='utf-8')
            return str(target)
        def failed(message):
            owner._reserved_exports.discard(str(target)); self.status.setText(str(message))
        owner.start_disk_job(write, (), lambda _: owner.record_export(target), failed)

    def dispose(self):
        self.cancel_generation(); self.player.stop()


def build_audio_docks(owner):
    owner.audio_generation_panels = {}; owner.audio_generation_docks = {}
    for kind, name in NAMES.items():
        dock = QDockWidget(name, owner); dock.setObjectName(kind + 'AudioGenerationDock'); dock.setProperty('adobePanel', True)
        panel = AudioGenerationPanel(owner, kind); dock.setWidget(panel)
        owner.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock); dock.hide()
        owner.audio_generation_panels[kind] = panel; owner.audio_generation_docks[kind] = dock
