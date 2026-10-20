"""Wizard-only local setup cards; Settings retains its compact controls."""
from pathlib import Path
import sys
from PySide6.QtCore import Signal, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QStyle, QFrame
from .local_widgets import LocalModelsPanel, progress_text
from .local_generation import scan_models, recommended_variant, recommended_writer, data_root, run_request, validate_profile
from .onboarding_design import card, label, primary, stepper


def discover_local_models(profile, cancel):
    """Scan bounded, familiar roots without loading weights or changing files."""
    roots = [Path(profile['models_root']).expanduser(), Path.home()/'Programs/ComfyUI/models',
             Path.home()/'ComfyUI/models', Path.home()/'Models', data_root()/'models']
    found = {key: [] for key in ('video_model','video_encoder','video_vae','audio_vae','image_model','image_encoder','image_vae','llm_model')}
    seen = set(); gpu = {'total_gb':0,'free_gb':0}
    for root in roots:
        if cancel.is_set(): raise InterruptedError('Automatic setup cancelled.')
        identity = str(root.absolute())
        if identity in seen or not root.is_dir(): continue
        seen.add(identity); result = scan_models(root); gpu = result['gpu']
        for key, paths in result['files'].items(): found[key].extend(p for p in paths if p not in found[key])
    return {'files':found,'gpu':gpu}


class WizardLocalModelsPanel(LocalModelsPanel):
    readinessChanged = Signal()

    def __init__(self, owner, requested_tasks):
        super().__init__(owner, include_loras=False); self.requested_tasks = requested_tasks; self.verified_engine = None; self.disposed = False
        self.auto_running = False; self.auto_queue = []; self._auto_next = None
        layout = self.layout()
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() and item.widget() not in (self.tabs,self.hf_token,self.status,self.log,self.stop,self.links): item.widget().deleteLater()
        layout.setContentsMargins(0,0,0,0); layout.setSpacing(12)
        for i in range(self.tabs.count()):self.tabs.widget(i).layout().setVerticalSpacing(6)
        form = self.tabs.widget(0).layout();form.setContentsMargins(0,0,0,0)
        path_rows = [form.takeRow(0),form.takeRow(0)]
        while form.rowCount():
            removed = form.takeRow(0)
            if removed.labelItem and removed.labelItem.widget():removed.labelItem.widget().deleteLater()
        for text,removed in zip(('Inference Python','Model folder'),path_rows):
            if removed.labelItem and removed.labelItem.widget():removed.labelItem.widget().deleteLater()
            form.addRow(text,removed.fieldItem.layout())
        model_row = form.itemAt(1,form.ItemRole.FieldRole).layout()
        model_row.insertWidget(model_row.count()-1,self.scan_button)
        compute = QHBoxLayout();self.device.setFixedWidth(250);compute.addWidget(self.device);compute.addWidget(self.offload)
        form.addRow('Compute on',compute)
        steps = QHBoxLayout();steps.addStretch();steps.addWidget(stepper(self.steps,'image sampling steps'));steps.addSpacing(24)
        steps.addWidget(label('Preview every N steps'));steps.addStretch();steps.addWidget(stepper(self.every,'image preview interval'))
        self.steps.setToolTip('Default image sampling steps. Video has its own sampling settings.')
        form.addRow('Default sampling steps',steps)
        form.setHorizontalSpacing(12)
        for i in range(form.rowCount()):
            item=form.itemAt(i,form.ItemRole.LabelRole)
            if item and item.widget():item.widget().setFixedWidth(180)
        for button in self.tabs.findChildren(QPushButton):
            if button.text()=='…':
                button.setText('');button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon));button.setStyleSheet('padding:3px');button.setFixedSize(36,36)

        self.auto_card, content = card('Option A: Automatic Configuration <span style="color:#2e9eb4">(Recommended)</span>',
            'Let us find your files and configure your local engines in one click.', True)
        content.setContentsMargins(16,12,16,12)
        self.auto = primary(QPushButton('✦  Automatically configure all local engines'))
        self.auto.setProperty('primary',False); self.auto.setStyleSheet('background:#343b46;border:1px solid #259cb3;padding:9px;font-size:22px')
        self.auto.setFixedHeight(46)
        self.auto.clicked.connect(self.automatic_setup); content.addWidget(self.auto)
        content.addWidget(label('Scans standard paths and sets up necessary environments automatically.', 'muted'))
        layout.addWidget(self.auto_card)
        self.or_label=QWidget(); divider=QHBoxLayout(self.or_label);divider.setContentsMargins(0,0,0,0);divider.setSpacing(12)
        for i in range(2):
            line=QFrame();line.setFixedHeight(1);line.setStyleSheet('background:#606675;border:0');divider.addWidget(line,1)
            if i==0:divider.addWidget(label('OR','muted'))
        layout.addWidget(self.or_label)
        self.manual_card, manual = card('Option B: Manual Setup', 'Local Paths')
        manual.setContentsMargins(16,14,16,14);manual.setSpacing(4)
        manual.itemAt(1).widget().setStyleSheet('font-size:18px')
        self.tabs.tabBar().hide(); manual.addWidget(self.tabs); layout.addWidget(self.manual_card)
        self.engine_card, engine = card('3. Engine & Download')
        self.engine_status = label('Local Engine Status: Not checked', 'muted')
        grid = QGridLayout()
        for row, widgets in enumerate(((self.engine_status,self.prepare,self.check),(self.task,self.assets,self.download))):
            for column, widget in enumerate(widgets):grid.addWidget(widget,row,column)
        engine.addLayout(grid); layout.addWidget(self.engine_card)
        optional, options = card('4. Optional Config')
        self.optional_toggle = QPushButton('▸  Optional Hugging Face token and diagnostics')
        self.optional_toggle.setCheckable(True); options.addWidget(self.optional_toggle)
        self.optional_content = QWidget(); box = QVBoxLayout(self.optional_content); box.setContentsMargins(0,0,0,0)
        box.addWidget(self.hf_token); box.addWidget(self.log); box.addWidget(self.links)
        self.optional_content.hide(); self.optional_toggle.toggled.connect(self.optional_content.setVisible)
        options.addWidget(self.optional_content); layout.addWidget(optional)
        layout.addWidget(self.status); layout.addWidget(self.stop); layout.addStretch()
        self.tabs.currentChanged.connect(self.section_changed)
        self.status_timer = QTimer(self); self.status_timer.setSingleShot(True); self.status_timer.setInterval(150)
        self.status_timer.timeout.connect(self.readinessChanged)
        for field in self.fields.values():field.textChanged.connect(self.invalidate_status)
        self.device.currentIndexChanged.connect(self.invalidate_status)
        self.section_changed(0)

    def section_changed(self, index):
        self.auto_card.setVisible(index == 0); self.or_label.setVisible(index == 0)
        self.manual_card.layout().itemAt(0).widget().setText('Option B: Manual Setup' if index == 0 else ('Video · H3','Images · Flux','Writing · local')[index - 1])
        self.tabs.setFixedHeight(self.tabs.widget(index).layout().sizeHint().height()+2)
        if index: self.task.setCurrentIndex(self.task.findData(('','video','image','text')[index]))

    def engine_identity(self):return self.fields['python'].text().strip(), self.device.currentData()

    def invalidate_status(self):
        if self.verified_engine != self.engine_identity(): self.engine_status.setText('Local Engine Status: Not checked')
        self.status_timer.start()

    def readiness(self):
        profile = self.value()
        engine = ('busy','Preparing the local runtime') if self.busy or self.auto_running else ('ready','Runtime imports, dependencies and execution verified') if self.verified_engine == self.engine_identity() else ('pending','Check the engine to verify imports, dependencies and execution')
        states = [engine]
        for kind in ('video','image','text'):
            try:validate_profile(profile,kind); states.append(('ready','Files configured; runtime verification is shown under Engine'))
            except ValueError as error:states.append(('pending',str(error)))
        return states

    def automatic_setup(self):
        if self.busy or self.auto_running:return
        self.auto_running = True
        for button in (self.auto,self.prepare,self.assets,self.download,self.scan_button,self.check):button.setEnabled(False)
        self.log.clear(); self.stop.show(); self.cancel.clear()
        self.serial += 1; serial = self.serial
        self.status.setText('Finding model files in standard locations…'); self.readinessChanged.emit()
        def found(result):
            if self.disposed or serial != self.serial:return
            if self.cancel.is_set():self.end_automatic('Automatic setup cancelled.'); return
            variant = self.variant.currentData() if self.fields['image_model'].text().strip() else recommended_variant(result['gpu']['total_gb'])
            self.variant.setCurrentIndex(self.variant.findData(variant))
            self.profile['vram_gb'] = result['gpu']['total_gb']
            if not self.fields['llm_model'].text().strip():self.writer_repo.setCurrentText(recommended_writer(result['gpu']['total_gb']))
            for key, paths in result['files'].items():
                if self.fields[key].text().strip() or not paths:continue
                if key == 'image_model':paths = [p for p in paths if variant in Path(p).name.lower()]
                elif key == 'image_encoder':paths = [p for p in paths if ('4b' if variant=='4b' else '8b') in Path(p).name.lower()]
                elif key == 'video_model':paths = [p for p in paths if self.video_variant.currentData() in Path(p).name.lower()]
                elif key == 'video_encoder':paths = sorted(paths,key=lambda p:'nvfp4' not in Path(p).name.lower())
                if paths:self.fields[key].setText(paths[0])
            self.auto_queue = [('probe' if Path(self.fields['python'].text()).expanduser().is_file() else 'install', None)]
            tasks = set(self.requested_tasks())
            tasks.update(task for task in ('video','image') if self.fields[task+'_model'].text().strip())
            self.auto_queue += [('assets',task) for task in ('video','image') if task in tasks]
            self.continue_automatic()
        self.owner.start_disk_job(discover_local_models,(self.value(),self.cancel),found,
            lambda message:self.end_automatic(str(message)) if not self.disposed and serial==self.serial else None)

    def continue_automatic(self):
        if self.disposed:return
        if self.cancel.is_set():self.end_automatic('Automatic setup cancelled.');return
        if not self.auto_queue:self.end_automatic('Automatic configuration complete. Review each substep; download any missing weights separately.');return
        kind, task = self.auto_queue.pop(0)
        if task:self.task.setCurrentIndex(self.task.findData(task))
        self.setup(kind)

    def end_automatic(self, message):
        self.auto_running = False; self.auto_queue = []
        for button in (self.auto,self.prepare,self.assets,self.download,self.scan_button,self.check):button.setEnabled(True)
        self.stop.hide()
        self.status.setText(message); self.readinessChanged.emit()

    def setup(self, kind):
        if self.busy or self.disposed:return
        profile = self.value(); identity = self.engine_identity()
        fields_before = {key:field.text() for key,field in self.fields.items()}
        if kind == 'install':profile['python'] = sys.executable
        self.busy = True; self.stop.show(); self.cancel.clear(); self.serial += 1; serial = self.serial
        for button in (self.auto,self.prepare,self.assets,self.download,self.scan_button,self.check):button.setEnabled(False)
        self.status.setText('Preparing…'); self.log.clear(); self.readinessChanged.emit()
        def current():return not self.disposed and serial==self.serial
        def event(value):
            if not current():return
            self.log.receive_event(value)
            if value.get('kind')=='progress':self.status.setText(progress_text(value)+'\n'+value.get('message',''))
        def finish():
            self.busy = False; self.stop.hide()
            for button in (self.auto,self.prepare,self.assets,self.download,self.scan_button,self.check):button.setEnabled(not self.auto_running)
            self.readinessChanged.emit()
        def ready(value):
            if not current():return
            for key,path in value.get('profile_changes',{}).items():
                if key in self.fields and self.fields[key].text()==fields_before[key]:self.fields[key].setText(path)
            expected = (value.get('profile_changes',{}).get('python',identity[0]),profile['device'])
            if kind in ('install','probe') and expected==self.engine_identity():
                self.verified_engine = self.engine_identity(); self.engine_status.setText('Local Engine Status: Ready · verified')
            self.status.setText(value.get('message','Ready.')); finish()
            if self.auto_running:QTimer.singleShot(0,self.continue_automatic)
        def failed(message):
            if not current():return
            self.log.receive_event({'kind':'error','message':str(message)})
            self.optional_toggle.setChecked(True)
            if kind in ('probe','install'):self.verified_engine = None; self.engine_status.setText('Local Engine Status: Check failed')
            self.status.setText(str(message)); finish()
            if self.auto_running:
                if kind=='probe' and not self.cancel.is_set():
                    self.auto_queue.insert(0,('install',None)); QTimer.singleShot(0,self.continue_automatic)
                else:self.end_automatic(str(message))
        request={'kind':kind,'profile':profile,'task':self.task.currentData(),
                 'video_variant':self.video_variant.currentData(),'hf_token':self.hf_token.text().strip()}
        self.owner.start_disk_job(run_request,(request,None,self.cancel),ready,failed,progress=event)

    def dispose(self):
        self.disposed = True; self.status_timer.stop(); super().dispose()
