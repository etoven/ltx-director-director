"""Local model setup and generation previews, with no ML imports on Qt's thread."""
from __future__ import annotations
import copy
from pathlib import Path
import sys
import threading
from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QLineEdit, QComboBox, QPushButton, QCheckBox, QSpinBox, QTabWidget, QFileDialog,
    QScrollArea, QDialog, QDialogButtonBox, QTextEdit, QListWidget, QListWidgetItem,
    QMessageBox, QDoubleSpinBox)
from .local_generation import (load_profile, save_profile, scan_models, recommended_variant, recommended_writer,
    run_request, validate_profile, align_h3_frames, engine_python)


def progress_text(event):
    percent = event.get('percent'); eta = event.get('eta_seconds')
    label = f'{round(percent)}%' if isinstance(percent, (float, int)) else event.get('message', 'Preparing…')
    if isinstance(eta, (float, int)) and eta > 0:
        seconds = round(eta); label += f' · ~{seconds // 60}m {seconds % 60:02d}s left'
    return label


class ProgressPill(QLabel):
    def __init__(self, parent, bottom_margin=10):
        super().__init__(parent)
        self.bottom_margin = bottom_margin
        parent.installEventFilter(self)
        self.setStyleSheet('background:rgba(12,28,40,230);color:#d5f9f0;border:1px solid #497f8a;'
                           'border-radius:10px;padding:4px 9px;font-size:11px')
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def update_event(self, event):
        self.setText(progress_text(event)); self.adjustSize()
        self.move(max(4, self.parent().width() - self.width() - 10),
                  max(4, self.parent().height() - self.height() - self.bottom_margin))
        self.show(); self.raise_()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self.move(max(4, watched.width() - self.width() - 10),
                      max(4, watched.height() - self.height() - self.bottom_margin))
        return super().eventFilter(watched, event)


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
            ('Video · H3', [('video_model', 'Pruned H3 checkpoint'), ('video_encoder', 'H3 text encoder · INT8'),
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
        engine_form.addRow('Default sampling steps', self.steps)
        self.every = QSpinBox(); self.every.setRange(1, 50); self.every.setValue(int(self.profile.get('preview_every', 5)))
        engine_form.addRow('Preview every N steps', self.every)
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
                'video_variant': self.video_variant.currentData(), 'writer_repo': self.writer_repo.currentText().strip()}

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
            if 'nvfp4' in self.fields['video_encoder'].text().lower():
                message += ' The existing H3 NVFP4/AWQ encoder needs an INT8 replacement for this native engine; choose Video models → Download model set.'
            if 'fl2va' in self.fields['video_model'].text().lower():
                self.video_variant.setCurrentIndex(self.video_variant.findData('fl2va'))
            self.status.setText(message)
        self.owner.start_disk_job(scan_models, (self.fields['models_root'].text(),), ready,
                                   lambda message: (self.scan_button.setEnabled(True), self.status.setText(message)))

    def confirm_download(self):
        task = self.task.currentData()
        estimate = {'video': 'about 50 GB for the pruned checkpoint, INT8 encoder and VAEs',
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
        self.status.setText('Preparing…')
        def event(value):
            if value.get('kind') == 'progress': self.status.setText(progress_text(value) + '\n' + value.get('message', ''))
        def ready(value):
            for key, path in value.get('profile_changes', {}).items():
                if key in self.fields: self.fields[key].setText(path)
            self.status.setText(value.get('message', 'Ready.')); finish()
        def fail(message): self.status.setText(str(message)); finish()
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
        self.pixmap = QPixmap()

    def receive_event(self, event):
        if event.get('kind') == 'preview' and event.get('media') == 'image':
            self.pixmap = QPixmap(event['path']); self.fit()
        elif event.get('kind') == 'progress':
            self.pill.update_event(event); self.cancel.setEnabled(True)
        elif event.get('kind') == 'result':
            self.pill.hide(); self.cancel.setEnabled(False)

    def fit(self):
        if not self.pixmap.isNull():
            self.image.setPixmap(self.pixmap.scaled(self.image.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event): super().resizeEvent(event); self.fit()


class VideoGenerationDialog(QDialog):
    def __init__(self, owner):
        super().__init__(owner); self.setWindowTitle('Generate local video'); self.resize(640, 560)
        layout = QVBoxLayout(self)
        self.prompt = QTextEdit(); self.prompt.setAcceptRichText(False)
        self.prompt.setPlainText(owner.minimax_prompt_text if owner.unified_workspace else
            '\n\n'.join(filter(None, [owner.global_prompt.toPlainText(), *[s.prompt for s in owner.segments]])))
        layout.addWidget(QLabel('Review the prompt sent to the local video model.'))
        layout.addWidget(self.prompt, 1)
        form = QFormLayout()
        self.duration = QDoubleSpinBox(); self.duration.setRange(4, 14.37); self.duration.setValue(min(14.37, max(4, sum(s.duration for s in owner.segments) or 5)))
        self.duration.setSuffix(' seconds')
        self.width = QSpinBox(); self.width.setRange(256, 2048); self.width.setSingleStep(32); self.width.setValue(832)
        self.height = QSpinBox(); self.height.setRange(256, 2048); self.height.setSingleStep(32); self.height.setValue(480)
        self.steps = QSpinBox(); self.steps.setRange(4, 100); self.steps.setValue(load_profile(owner.settings)['steps'])
        form.addRow('Length', self.duration); form.addRow('Width', self.width); form.addRow('Height', self.height); form.addRow('Steps', self.steps)
        layout.addLayout(form)
        self.references = QListWidget(); self.references.setMaximumHeight(120)
        for segment in owner.segments:
            if segment.kind in ('image', 'video'):
                item = QListWidgetItem(f'{segment.name} · {segment.kind}')
                item.setData(Qt.ItemDataRole.UserRole, segment.id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if segment.kind == 'image' else Qt.CheckState.Unchecked)
                self.references.addItem(item)
        from .minimax_reference import reference_slots
        for index, reference in enumerate(reference_slots(owner.minimax_reference_images)):
            if not reference: continue
            kind = reference.get('kind', 'image')
            label = 'audio' if reference.get('audio_only') else kind
            item = QListWidgetItem(f"Reference {index + 1}: {reference['name']} · {label}")
            item.setData(Qt.ItemDataRole.UserRole, f'refslot:{index}')
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked); self.references.addItem(item)
        layout.addWidget(QLabel('Attach selected references · video sound is off unless audio-only is selected'))
        layout.addWidget(self.references)
        self.message = QLabel('H3 aligns duration to its frame grid. Small canvases reduce memory and render time. No Lightning/Turbo LoRA is used. Review prompt asset labels to match only the checked references.')
        self.message.setWordWrap(True); layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Generate')
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def accept(self):
        try:
            if not self.prompt.toPlainText().strip(): raise ValueError('Enter a video prompt first.')
            if self.width.value() % 32 or self.height.value() % 32: raise ValueError('Width and height must be multiples of 32.')
            self.frames = align_h3_frames(self.duration.value())
        except ValueError as error:
            self.message.setText(str(error)); return
        super().accept()
