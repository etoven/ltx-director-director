"""Local model setup and generation previews, with no ML imports on Qt's thread."""
from __future__ import annotations
import copy
from pathlib import Path
import sys
import threading
from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtGui import QPixmap, QValidator
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QComboBox, QPushButton, QCheckBox, QSpinBox, QTabWidget, QFileDialog,
    QMessageBox, QDoubleSpinBox, QPlainTextEdit, QAbstractSpinBox)
from .local_generation import (load_profile, scan_models, recommended_variant, recommended_writer, run_request)


class SeedSpinBox(QAbstractSpinBox):
    """Qt's ordinary integer spin box truncates the 64-bit seeds H3 accepts."""
    MAXIMUM = 2**64 - 1

    def __init__(self):
        super().__init__(); self.lineEdit().setMaxLength(20); self.setValue(42)

    def value(self):
        return min(self.MAXIMUM, max(0, int(self.lineEdit().text() or '0')))

    def setValue(self, value):
        self.lineEdit().setText(str(min(self.MAXIMUM, max(0, int(value)))))

    def validate(self, text, pos):
        state = QValidator.State
        if not text: return state.Intermediate, text, pos
        return (state.Acceptable if text.isascii() and text.isdecimal() and int(text) <= self.MAXIMUM else state.Invalid), text, pos

    def fixup(self, text):
        return str(min(self.MAXIMUM, max(0, int(text or '0')))) if text.isascii() and text.isdecimal() else '0'

    def stepBy(self, steps):
        self.setValue(self.value() + steps)

    def stepEnabled(self):
        flags = QAbstractSpinBox.StepEnabledFlag.StepNone
        if self.value() > 0: flags |= QAbstractSpinBox.StepEnabledFlag.StepDownEnabled
        if self.value() < self.MAXIMUM: flags |= QAbstractSpinBox.StepEnabledFlag.StepUpEnabled
        return flags


def progress_text(event):
    percent = event.get('percent'); eta = event.get('eta_seconds')
    label = f'{round(percent)}%' if isinstance(percent, (float, int)) else event.get('message', 'Preparing…')
    if isinstance(eta, (float, int)) and eta > 0:
        seconds = round(eta); label += f' · ~{seconds // 60}m {seconds % 60:02d}s left'
    return label


class ProgressPill(QLabel):
    def __init__(self, parent, bottom_margin=10, anchor=None):
        super().__init__(parent)
        self.bottom_margin = bottom_margin; self.anchor = anchor
        parent.installEventFilter(self)
        self.setStyleSheet('background:rgba(12,28,40,230);color:#d5f9f0;border:1px solid #497f8a;'
                           'border-radius:10px;padding:4px 9px;font-size:11px')
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def reposition(self):
        target = self.anchor() if callable(self.anchor) else self.anchor
        corner = target.mapTo(self.parent(), target.rect().bottomRight()) if target else self.parent().rect().bottomRight()
        self.move(max(4, corner.x() - self.width() - 9),
                  max(4, corner.y() - self.height() - self.bottom_margin + 1))

    def update_event(self, event):
        self.setText(progress_text(event)); self.adjustSize(); self.reposition()
        self.show(); self.raise_()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize: self.reposition()
        return super().eventFilter(watched, event)


class GenerationLog(QWidget):
    """Bounded, plain-text live diagnostics, retained until the next job."""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.toggle = QCheckBox('Verbose output'); self.toggle.setChecked(True)
        row.addWidget(self.toggle); row.addStretch()
        copy_button = QPushButton('Copy output')
        copy_button.clicked.connect(self.copy_output); row.addWidget(copy_button)
        layout.addLayout(row)
        self.output = QPlainTextEdit(); self.output.setReadOnly(True)
        self.output.document().setMaximumBlockCount(1500)
        self.output.setMaximumHeight(145); self.output.setMinimumHeight(70)
        self.output.setPlaceholderText('Model loading, sampling progress and errors appear here.')
        self.output.setStyleSheet('font-family:monospace;font-size:11px')
        self.toggle.toggled.connect(self.output.setVisible); layout.addWidget(self.output)

    def copy_output(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.output.toPlainText())

    def clear(self): self.output.clear()

    def receive_event(self, event):
        if event.get('kind') in ('log', 'progress', 'error'):
            message = event.get('message', '')
            if message: self.output.appendPlainText(str(message))
        elif event.get('kind') == 'result' and event.get('path'):
            seed = f' · seed {event["seed"]}' if event.get('seed') is not None else ''
            self.output.appendPlainText('Saved ' + str(event['path']) + seed)


class LocalModelsPanel(QWidget):
    def __init__(self, owner, profile=None):
        super().__init__()
        self.owner = owner
        self.profile = copy.deepcopy(profile or load_profile(owner.settings))
        self.serial = 0; self.cancel = threading.Event(); self.busy = False
        layout = QVBoxLayout(self)
        note = QLabel('Choose your own files or download a matching set. Generation runs inside Director’s private Python worker. '
                      'Model weights, configurations and the inference engine are separate setup steps.')
        note.setWordWrap(True); layout.addWidget(note)
        self.tabs = QTabWidget(); layout.addWidget(self.tabs)
        self.fields = {}
        sections = [
            ('Engine', [('python', 'Inference Python'), ('models_root', 'Model folder')]),
            ('Video · H3', [('video_model', 'Pruned H3 checkpoint'), ('video_encoder', 'H3 text encoder · INT8 / NVFP4'),
                ('video_vae', 'Video VAE'), ('audio_vae', 'Audio VAE'), ('video_assets', 'Processor / tokenizer folder')]),
            ('Images · Flux', [('image_model', 'Flux Klein checkpoint'), ('image_encoder', 'Qwen text encoder'),
                ('image_vae', 'Flux 2 VAE'), ('image_assets', 'Image configuration folder')]),
            ('Writing · local', [('llm_model', 'Complete instruction-model folder')]),
        ]
        for title, fields in sections:
            page = QWidget(); form = QFormLayout(page); self.tabs.addTab(page, title)
            for key, label in fields:
                row = QHBoxLayout(); field = QLineEdit(str(self.profile.get(key, '')))
                self.fields[key] = field; row.addWidget(field)
                browse = QPushButton('…'); browse.setFixedWidth(30)
                browse.clicked.connect(lambda _, k=key: self.browse(k)); row.addWidget(browse)
                form.addRow(label, row)
        engine_form = self.tabs.widget(0).layout()
        self.device = QComboBox(); self.device.addItem('NVIDIA GPU', 'cuda'); self.device.addItem('CPU · images / writing · slower', 'cpu')
        self.device.setCurrentIndex(max(0, self.device.findData(self.profile.get('device', 'cuda'))))
        engine_form.addRow('Compute on', self.device)
        self.offload = QCheckBox('Move model layers out of GPU memory when idle')
        self.offload.setChecked(self.profile.get('offload', True)); engine_form.addRow('', self.offload)
        self.steps = QSpinBox(); self.steps.setRange(4, 100); self.steps.setValue(int(self.profile.get('steps', 30)))
        engine_form.addRow('Default image sampling steps', self.steps)
        self.every = QSpinBox(); self.every.setRange(1, 50); self.every.setValue(int(self.profile.get('preview_every', 5)))
        engine_form.addRow('Image preview every N steps', self.every)
        self.variant = QComboBox(); self.variant.addItem('Klein Base 4B · FP8 · lower memory', '4b')
        self.variant.addItem('Klein Base 9B · FP8 · more detail, more memory', '9b')
        self.variant.setCurrentIndex(max(0, self.variant.findData(self.profile.get('image_variant', '4b'))))
        self.tabs.widget(2).layout().insertRow(0, 'Image model size', self.variant)
        self.writer_repo = QComboBox(); self.writer_repo.setEditable(True)
        self.writer_repo.addItems(['Qwen/Qwen3-1.7B', 'Qwen/Qwen3-4B-Instruct-2507', 'Qwen/Qwen3-8B', 'Qwen/Qwen3-14B'])
        self.writer_repo.setCurrentText(self.profile.get('writer_repo', 'Qwen/Qwen3-4B-Instruct-2507'))
        self.tabs.widget(3).layout().insertRow(0, 'Download writing model', self.writer_repo)
        self.video_variant = QComboBox(); self.video_variant.addItem('Ref2VA · reference media', 'ref2va')
        self.video_variant.addItem('FL2VA · text / first and last frame', 'fl2va')
        self.video_variant.setCurrentIndex(max(0, self.video_variant.findData(self.profile.get('video_variant', 'ref2va'))))
        self.tabs.widget(1).layout().insertRow(0, 'H3 workflow', self.video_variant)
        self.video_random_seed = QCheckBox('Random for each render')
        self.video_seed = SeedSpinBox()
        seed = int(self.profile.get('video_seed', -1))
        self.video_seed.setValue(max(0, seed) if seed >= 0 else 42)
        self.video_random_seed.setChecked(seed < 0); self.video_seed.setEnabled(seed >= 0)
        self.video_random_seed.toggled.connect(lambda random: self.video_seed.setEnabled(not random))
        seed_row = QHBoxLayout(); seed_row.addWidget(self.video_random_seed); seed_row.addWidget(self.video_seed)
        self.tabs.widget(1).layout().insertRow(1, 'Seed', seed_row)
        self.video_steps = QSpinBox(); self.video_steps.setRange(4, 100); self.video_steps.setValue(int(self.profile.get('video_steps', 20)))
        self.tabs.widget(1).layout().insertRow(2, 'Video sampling steps', self.video_steps)
        self.video_sampler = QComboBox(); self.video_sampler.addItem('RES multistep · simple schedule', 'res_multistep'); self.video_sampler.addItem('Euler · native flow schedule', 'euler')
        self.video_sampler.setCurrentIndex(max(0, self.video_sampler.findData(self.profile.get('video_sampler', 'res_multistep'))))
        self.tabs.widget(1).layout().insertRow(3, 'Video sampler', self.video_sampler)
        self.video_width = QSpinBox(); self.video_width.setRange(256, 2048); self.video_width.setSingleStep(32); self.video_width.setValue(int(self.profile.get('video_width', 960)))
        self.video_height = QSpinBox(); self.video_height.setRange(256, 2048); self.video_height.setSingleStep(32); self.video_height.setValue(int(self.profile.get('video_height', 544)))
        canvas = QHBoxLayout(); canvas.addWidget(self.video_width); canvas.addWidget(QLabel('×')); canvas.addWidget(self.video_height)
        self.tabs.widget(1).layout().insertRow(4, 'Video canvas', canvas)
        self.video_duration = QDoubleSpinBox(); self.video_duration.setRange(4, 15); self.video_duration.setValue(float(self.profile.get('video_duration', 15)))
        self.video_duration.setSuffix(' seconds')
        self.video_duration.setToolTip('15 seconds aligns to 362 frames at 24 fps (15.08 s). 16:9 at 0.5 MP uses 960 × 544.')
        self.tabs.widget(1).layout().insertRow(5, 'Video length', self.video_duration)
        self.video_preview_mode = QComboBox()
        self.video_preview_mode.addItem('Live noisy latent · no VAE decode', 'noisy')
        self.video_preview_mode.addItem('Denoised estimate · periodic VAE decode', 'denoised')
        self.video_preview_mode.setCurrentIndex(max(0, self.video_preview_mode.findData(self.profile.get('video_preview_mode', 'noisy'))))
        self.video_preview_mode.setToolTip('Noisy mode shows actual latent changes with approximate colors, without extra model passes. Denoised mode decodes an opening-chunk clean estimate and pauses sampling for the VAE.')
        self.tabs.widget(1).layout().insertRow(6, 'Video preview', self.video_preview_mode)
        self.video_preview_every = QSpinBox(); self.video_preview_every.setRange(1, 50)
        self.video_preview_every.setValue(int(self.profile.get('video_preview_every', 5)))
        self.video_preview_every.setEnabled(self.video_preview_mode.currentData() == 'denoised')
        self.video_preview_mode.currentIndexChanged.connect(lambda: self.video_preview_every.setEnabled(self.video_preview_mode.currentData() == 'denoised'))
        self.tabs.widget(1).layout().insertRow(7, 'Denoised preview every N steps', self.video_preview_every)
        row = QHBoxLayout()
        self.scan_button = QPushButton('Find my model files'); self.scan_button.clicked.connect(self.scan)
        self.prepare = QPushButton('Prepare local engine'); self.prepare.clicked.connect(lambda: self.setup('install'))
        self.check = QPushButton('Check engine'); self.check.clicked.connect(lambda: self.setup('probe'))
        row.addWidget(self.scan_button); row.addWidget(self.prepare); row.addWidget(self.check); layout.addLayout(row)
        row = QHBoxLayout()
        self.task = QComboBox()
        for label, key in [('Image models', 'image'), ('Video models', 'video'), ('Writing model', 'text')]: self.task.addItem(label, key)
        row.addWidget(self.task)
        self.assets = QPushButton('Get configuration files'); self.assets.clicked.connect(lambda: self.setup('assets'))
        self.download = QPushButton('Download model set…'); self.download.clicked.connect(self.confirm_download)
        row.addWidget(self.assets); row.addWidget(self.download); layout.addLayout(row)
        self.hf_token = QLineEdit(); self.hf_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.hf_token.setPlaceholderText('Optional Hugging Face token for gated models · kept for this dialog only')
        layout.addWidget(self.hf_token)
        self.status = QLabel('No Lightning or Turbo LoRA is loaded. 4B is the lower-memory image choice; model fit also depends on host RAM and canvas size.')
        self.status.setWordWrap(True); self.status.setTextFormat(Qt.TextFormat.PlainText); layout.addWidget(self.status)
        self.log = GenerationLog(); layout.addWidget(self.log)
        self.stop = QPushButton('Cancel preparation'); self.stop.clicked.connect(lambda: self.cancel.set()); self.stop.hide(); layout.addWidget(self.stop)
        self.links = QLabel('<a href="https://huggingface.co/Comfy-Org/MiniMax-H3">H3 model files</a> · '
            '<a href="https://huggingface.co/black-forest-labs/FLUX.2-klein-base-4b-fp8">Flux 4B</a> · '
            '<a href="https://huggingface.co/black-forest-labs/FLUX.2-klein-base-9b-fp8">Flux 9B</a> · '
            '<a href="https://huggingface.co/settings/tokens">Hugging Face tokens</a>')
        self.links.setText(self.links.text().replace('<a ', '<a style="color:#80dfd2" '))
        self.links.setOpenExternalLinks(True); self.links.setWordWrap(True); layout.addWidget(self.links)
        layout.addStretch()

    def value(self):
        return {**self.profile, **{k: v.text().strip() for k, v in self.fields.items()},
                'device': self.device.currentData(), 'offload': self.offload.isChecked(),
                'steps': self.steps.value(), 'preview_every': self.every.value(), 'image_variant': self.variant.currentData(),
                'video_preview_mode': self.video_preview_mode.currentData(), 'video_preview_every': self.video_preview_every.value(),
                'video_steps': self.video_steps.value(), 'video_sampler': self.video_sampler.currentData(),
                'video_width': self.video_width.value(), 'video_height': self.video_height.value(), 'video_duration': self.video_duration.value(),
                'video_variant': self.video_variant.currentData(), 'video_seed': -1 if self.video_random_seed.isChecked() else self.video_seed.value(), 'writer_repo': self.writer_repo.currentText().strip()}

    def browse(self, key):
        if key in ('models_root', 'video_assets', 'image_assets', 'llm_model'):
            path = QFileDialog.getExistingDirectory(self, 'Choose folder', self.fields[key].text())
        else:
            path, _ = QFileDialog.getOpenFileName(self, 'Choose model file' if key != 'python' else 'Choose Python interpreter', self.fields[key].text())
        if path: self.fields[key].setText(path)

    def scan(self):
        if self.busy: return
        self.serial += 1; serial = self.serial
        self.scan_button.setEnabled(False); self.status.setText('Finding model files…')
        def ready(result):
            if serial != self.serial: return
            self.scan_button.setEnabled(True)
            memory = result['gpu']; self.profile['vram_gb'] = memory['total_gb']
            variant = self.variant.currentData() if self.fields['image_model'].text().strip() else recommended_variant(memory['total_gb'])
            self.variant.setCurrentIndex(self.variant.findData(variant))
            if not self.fields['llm_model'].text().strip(): self.writer_repo.setCurrentText(recommended_writer(memory['total_gb']))
            for key, paths in result['files'].items():
                if not paths or self.fields[key].text().strip(): continue
                preferred = [p for p in paths if (variant in Path(p).name.lower() if key == 'image_model' else
                             ('4b' if variant == '4b' else '8b') in Path(p).name.lower() if key == 'image_encoder' else True)]
                self.fields[key].setText((preferred or paths)[0])
            message = f'Found {sum(map(len, result["files"].values()))} model files. '
            if memory['total_gb']: message += f'GPU: {memory["total_gb"]:.0f} GB total, {memory["free_gb"]:.1f} GB free. Suggested image model: {variant.upper()}.'
            if 'fl2va' in self.fields['video_model'].text().lower():
                self.video_variant.setCurrentIndex(self.video_variant.findData('fl2va'))
            self.status.setText(message)
        self.owner.start_disk_job(scan_models, (self.fields['models_root'].text(),), ready,
                                   lambda message: (self.scan_button.setEnabled(True), self.status.setText(message)))

    def confirm_download(self):
        task = self.task.currentData()
        estimate = {'video': 'about 40 GB for the pruned checkpoint, NVFP4/AWQ encoder and VAEs',
                    'image': 'roughly 13–20 GB, depending on the selected model', 'text': 'the selected writing model and its tokenizer files; the download can be several GB'}[task]
        answer = QMessageBox.question(self, 'Download local model set',
            f'Download {estimate} from Hugging Face? Existing matching files are reused. '
            'Some repositories require accepting their model license on Hugging Face first. '
            'Downloads and model use follow the linked model terms.')
        if answer == QMessageBox.StandardButton.Yes: self.setup('download')

    def setup(self, kind):
        if self.busy: return
        profile = self.value()
        if kind == 'install': profile['python'] = sys.executable
        self.busy = True; self.cancel = threading.Event(); self.stop.show()
        for button in (self.prepare, self.assets, self.download, self.scan_button, self.check): button.setEnabled(False)
        self.status.setText('Preparing…'); self.log.clear()
        def event(value):
            self.log.receive_event(value)
            if value.get('kind') == 'progress': self.status.setText(progress_text(value) + '\n' + value.get('message', ''))
        def ready(value):
            for key, path in value.get('profile_changes', {}).items():
                if key in self.fields: self.fields[key].setText(path)
            self.status.setText(value.get('message', 'Ready.')); finish()
        def fail(message):
            self.status.setText(str(message)); self.log.receive_event({'kind': 'error', 'message': str(message)}); finish()
        def finish():
            self.busy = False; self.stop.hide()
            for button in (self.prepare, self.assets, self.download, self.scan_button, self.check): button.setEnabled(True)
        request = {'kind': kind, 'profile': profile, 'task': self.task.currentData(),
                   'video_variant': self.video_variant.currentData(), 'hf_token': self.hf_token.text().strip()}
        self.owner.start_disk_job(run_request, (request, None, self.cancel), ready, fail, progress=event)

    def dispose(self):
        self.serial += 1; self.cancel.set()


class ImageGenerationPanel(QWidget):
    cancel_requested = Signal()

    def __init__(self):
        super().__init__(); layout = QVBoxLayout(self)
        self.image = QLabel('Local image previews appear here while rendering.')
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter); self.image.setMinimumSize(240, 180)
        self.image.setWordWrap(True); layout.addWidget(self.image, 1)
        self.pill = ProgressPill(self.image)
        self.caption = QLabel('Draft samples show the current denoising state.'); self.caption.setWordWrap(True); layout.addWidget(self.caption)
        self.cancel = QPushButton('Cancel image generation'); self.cancel.clicked.connect(self.cancel_requested)
        self.cancel.setEnabled(False)
        layout.addWidget(self.cancel)
        self.log = GenerationLog(); layout.addWidget(self.log)
        self.pixmap = QPixmap()

    def receive_event(self, event):
        self.log.receive_event(event)
        if event.get('kind') == 'preview' and event.get('media') == 'image':
            self.pixmap = QPixmap(event['path']); self.fit()
            self.caption.setText('Local image · draft sample')
        elif event.get('kind') == 'progress':
            self.pill.update_event(event); self.cancel.setEnabled(True)
            self.caption.setText(event.get('message', 'Rendering image…'))
        elif event.get('kind') == 'result':
            if event.get('media') == 'image' and event.get('path'):
                self.pixmap = QPixmap(event['path']); self.fit()
            self.caption.setText('Local image ready')
            self.pill.hide(); self.cancel.setEnabled(False)
        elif event.get('kind') == 'error':
            self.caption.setText('Local image stopped · see verbose output')
            self.pill.hide(); self.cancel.setEnabled(False)

    def fit(self):
        if not self.pixmap.isNull():
            self.image.setPixmap(self.pixmap.scaled(self.image.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event): super().resizeEvent(event); self.fit()
