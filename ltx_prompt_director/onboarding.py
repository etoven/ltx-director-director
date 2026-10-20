"""A resumable first-run guide using the same account and model controls as Settings."""
from pathlib import Path
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QPixmap, QDesktopServices
from PySide6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QListWidget, QStackedWidget, QPushButton, QComboBox, QLineEdit, QCheckBox,
    QFormLayout, QScrollArea, QFrame, QProgressBar, QSizePolicy, QDialogButtonBox)
from .ai import GEMINI_MODELS
from .artwork_tools import IMAGE_MODEL_CHOICES
from .chatgpt_widgets import ChatGPTSettingsPanel
from .local_generation import load_profile, save_profile, validate_profile
from .onboarding_local import WizardLocalModelsPanel
from .onboarding_design import (STYLE, GridSurface, WizardNavigation, Artwork, Showcase,
    WorkspaceTour, TOUR, card, label, primary)

ASSETS = Path(__file__).parent / 'assets' / 'onboarding'
STEPS = [('Welcome', 'welcome'), ('Your writing companion', 'language'), ('Images and thumbnails', 'images'),
         ('Local model setup', 'local'), ('Getting started', 'getting-started'), ('Thank you', 'thanks')]


def text_label(text):
    widget = QLabel(text.replace('<a ', '<a style="color:#80dfd2" ')); widget.setWordWrap(True); widget.setOpenExternalLinks(True)
    widget.setTextFormat(Qt.TextFormat.RichText)
    return widget


def open_manual(section=''):
    path = Path(__file__).parent / 'assets' / 'help' / 'index.html'
    url = QUrl.fromLocalFile(str(path))
    if section: url.setFragment(section)
    QDesktopServices.openUrl(url)


class OnboardingWizard(QDialog):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner; self.settings = owner.settings
        self.setWindowTitle('Welcome to Director · setup and first steps')
        self.setObjectName('onboardingWizard'); self.setStyleSheet(STYLE)
        self.resize(1280, 950); self.setMinimumSize(1000, 720)
        outer = QHBoxLayout(self); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)
        rail = QFrame(); rail.setObjectName('onboardingRail'); rail.setFixedWidth(248)
        left = QVBoxLayout(rail); left.setContentsMargins(14,18,14,24); left.setSpacing(20)
        brand = label('workspace'); brand.setStyleSheet('font-size:23px;font-weight:650;color:#b9bdc9;padding-left:4px')
        left.addWidget(brand)
        self.steps = WizardNavigation([name for name,_ in STEPS]); left.addWidget(self.steps,1)
        self.rail_progress = QProgressBar(); self.rail_progress.setRange(0,6); self.rail_progress.setTextVisible(False)
        left.addWidget(self.rail_progress)
        left.addWidget(label('A few choices now…\nA whole sequence of possibilities.', 'muted'))
        outer.addWidget(rail)
        main = GridSurface(); right = QVBoxLayout(main); right.setContentsMargins(0,0,0,0); right.setSpacing(0)
        heading = QWidget(); top = QVBoxLayout(heading); top.setContentsMargins(34,25,34,16); top.setSpacing(8)
        title_row = QHBoxLayout(); self.title = label('', 'pageTitle'); title_row.addWidget(self.title,1)
        self.donate = QPushButton('♥ Support & Donate'); self.donate.setEnabled(False)
        self.donate.setToolTip('The donation link will be added later.'); title_row.addWidget(self.donate)
        top.addLayout(title_row); self.subtitle = label('', 'muted'); top.addWidget(self.subtitle)
        self.header = QLabel(); self.header.setFixedHeight(105); self.header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(self.header); right.addWidget(heading)
        self.pages = QStackedWidget(); right.addWidget(self.pages,1)
        self.build_welcome(); self.build_writing(); self.build_images(); self.build_local(); self.build_getting_started(); self.build_thanks()
        footer = QFrame(); footer.setObjectName('onboardingFooter'); footer.setStyleSheet('#onboardingFooter{background:#171a25;border-top:1px solid #303441}')
        bottom = QVBoxLayout(footer); bottom.setContentsMargins(24,10,24,20); bottom.setSpacing(10)
        self.hint = label('', 'muted'); self.hint.setAlignment(Qt.AlignmentFlag.AlignRight); bottom.addWidget(self.hint)
        buttons = QHBoxLayout()
        self.later = QPushButton('Set up later'); self.later.clicked.connect(self.setup_later); buttons.addWidget(self.later)
        buttons.addStretch(); self.back = QPushButton('Back'); self.back.clicked.connect(lambda:self.advance(-1)); buttons.addWidget(self.back)
        self.next = primary(QPushButton('Continue')); self.next.clicked.connect(lambda:self.advance(1)); buttons.addWidget(self.next)
        bottom.addLayout(buttons); right.addWidget(footer); outer.addWidget(main,1)
        self.steps.currentRowChanged.connect(self.go)
        self.steps.localSectionChanged.connect(self.local.tabs.setCurrentIndex)
        self.local.tabs.currentChanged.connect(self.select_local_section)
        self.local.readinessChanged.connect(self.update_readiness)
        self.steps.setCurrentRow(0); self.update_readiness()

    def page(self):
        page = QWidget(); page.setObjectName('onboardingPage'); layout = QVBoxLayout(page); layout.setContentsMargins(32,0,32,24); layout.setSpacing(16)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame); scroll.setWidget(page)
        self.pages.addWidget(scroll)
        return layout

    def build_welcome(self):
        layout = self.page()
        layout.addWidget(text_label('<h3>Plan the scene. Shape the motion. Keep your creative control.</h3>'
            '<p>Director brings timelines, prompt refinement, reference media and finished-video review into one native workspace.</p>'
            '<p>Choose an online writing companion or run your own models. Image generation is a separate choice, '
            'so you can combine a cloud writing model with local images and video.</p>'))
        self.local_video = QCheckBox('Set up local H3 video generation too')
        profile = load_profile(self.settings)
        self.local_video.setChecked(bool(profile.get('video_model')))
        layout.addWidget(self.local_video)
        layout.addWidget(text_label('You can reopen this guide from <b>Help → Setup wizard</b>. '
            'Your current projects and media stay in place while you configure providers.'))
        layout.addStretch()

    def build_writing(self):
        layout = self.page(); form = QFormLayout()
        self.provider = QComboBox()
        for label, key in [('Google Gemini · recommended starting point', 'gemini'), ('ChatGPT account', 'chatgpt'),
                           ('OpenAI API key', 'openai'), ('Local instruction model', 'local')]: self.provider.addItem(label, key)
        self.provider.setCurrentIndex(max(0, self.provider.findData(self.settings.value('provider', 'gemini'))))
        form.addRow('Writing companion', self.provider)
        self.text_model = QComboBox(); self.text_model.setEditable(True); form.addRow('Model', self.text_model)
        self.text_key = QLineEdit(); self.text_key.setEchoMode(QLineEdit.EchoMode.Password); form.addRow('API key', self.text_key)
        layout.addLayout(form)
        self.text_info = text_label(''); layout.addWidget(self.text_info)
        self.chatgpt = ChatGPTSettingsPanel(self.owner.chatgpt_auth, self.settings, self)
        layout.addWidget(self.chatgpt)
        self.remember = QCheckBox('Remember API keys on this device')
        self.remember.setChecked(self.settings.value('remember_keys', False, bool)); layout.addWidget(self.remember)
        layout.addWidget(text_label('With Remember off, API keys stay in memory for this session. '
            'The browser-based ChatGPT connection uses its own existing account storage.'))
        layout.addStretch()
        self._text_keys = {p: str(self.owner.session_keys.get(p) or self.settings.value(p + '_key', '') or '') for p in ('gemini', 'openai')}
        self._last_text_provider = None
        self.provider.currentIndexChanged.connect(self.update_writing); self.update_writing()

    def update_writing(self):
        previous = self._last_text_provider
        if previous in ('gemini', 'openai'): self._text_keys[previous] = self.text_key.text().strip()
        provider = self.provider.currentData(); self._last_text_provider = provider
        self.text_model.clear()
        self.text_model.addItems(GEMINI_MODELS if provider == 'gemini' else ['gpt-6', 'gpt-6-mini', 'gpt-5.4-mini'] if provider == 'openai' else [])
        if provider in ('gemini', 'openai'):
            self.text_model.setCurrentText(str(self.settings.value(provider + '_model', 'gemini-3.5-flash-lite' if provider == 'gemini' else 'gpt-5.4-mini')))
            self.text_key.setText(self._text_keys[provider])
        else: self.text_key.clear()
        self.text_key.setVisible(provider in ('gemini', 'openai')); self.text_model.setVisible(provider in ('gemini', 'openai'))
        self.chatgpt.setVisible(provider == 'chatgpt')
        messages = {
            'gemini': '<b>A useful free text tier, with model and regional limits.</b> '
                'Open <a href="https://aistudio.google.com/api-keys">Google AI Studio</a>, sign in, choose or create a project, '
                'then create an API key and paste it above. Confirm the selected model’s '
                '<a href="https://ai.google.dev/gemini-api/docs/pricing">current free-tier availability</a>. '
                'Gemini can analyze supplied images and video; cloud requests need internet access.',
            'openai': '<b>A choice for detailed reasoning and structured prompt work.</b> '
                'Create a project and key in the <a href="https://platform.openai.com/api-keys">OpenAI platform</a>, '
                'enable API billing, and paste the key above. Choose a model available to your account. '
                'API usage is billed separately from a ChatGPT subscription.',
            'chatgpt': '<b>Use an eligible existing ChatGPT plan.</b> Continue in your browser below and grant plan usage. '
                'Choose an available model after connecting. Account eligibility and plan limits still apply. '
                '<a href="https://developers.openai.com/siwc/quickstart">Official sign-in guide</a>. '
                'Image generation remains a separate source choice.',
            'local': '<b>Private writing, with no API token bill.</b> Use a complete instruction-model folder containing '
                'configuration, tokenizer and model weights. Local models take more memory and can be slower. '
                'Their context windows and model licenses still apply. The built-in local writer uses authored text context; '
                'it does not claim to inspect video. Choose files or download a model in the Local setup step.'}
        self.text_info.setText(messages[provider])

    def build_images(self):
        layout = self.page(); form = QFormLayout()
        self.image_model = QComboBox()
        choices = [(label, key) for label, key in IMAGE_MODEL_CHOICES if key != 'auto']
        for label, key in choices: self.image_model.addItem(label, key)
        selected = str(self.settings.value('artwork/default_model', 'gemini:gemini-3.1-flash-lite-image'))
        if selected == 'auto': selected = 'gemini:gemini-3.1-flash-lite-image'
        self.image_model.setCurrentIndex(max(0, self.image_model.findData(selected)))
        form.addRow('Image source', self.image_model)
        self.image_key = QLineEdit(); self.image_key.setEchoMode(QLineEdit.EchoMode.Password); form.addRow('Image API key', self.image_key)
        layout.addLayout(form)
        self.image_info = text_label(''); layout.addWidget(self.image_info)
        layout.addWidget(text_label('Each provider uses one shared API key for writing and images. This source creates segment artwork and custom Effects Library thumbnails. '
            'The bundled effects keep their original catalog illustrations. An explicit model selection never silently switches providers.'))
        layout.addStretch()
        self._image_keys = dict(self._text_keys); self._last_image_provider = None
        self.image_model.currentIndexChanged.connect(self.update_images); self.update_images()

    def update_images(self):
        previous = self._last_image_provider
        if previous in ('gemini', 'openai'): self._image_keys[previous] = self.image_key.text().strip()
        provider = self.image_model.currentData().split(':', 1)[0]; self._last_image_provider = provider
        self.image_key.setVisible(provider != 'local')
        self.image_key.setText(self.text_key.text() if provider == self.provider.currentData() and provider in ('gemini', 'openai')
                               else self._image_keys.get(provider, '') or self._text_keys.get(provider, ''))
        messages = {
            'gemini': '<b>Flash Lite Image is the fast default.</b> Use a Gemini API key from '
                '<a href="https://aistudio.google.com/api-keys">AI Studio</a>. Unlike the free text tier, image API output '
                'requires a paid tier. Check <a href="https://ai.google.dev/gemini-api/docs/pricing">current pricing</a> '
                'and enable billing for that project. Other Gemini image models trade speed and cost for capability.',
            'openai': '<b>GPT Image supports generation and reference-based edits.</b> '
                'Use an <a href="https://platform.openai.com/api-keys">OpenAI API key</a> with image-model access and billing. '
                'It uses separate API usage; the ChatGPT sign-in source above is for eligible prompt work.',
            'local': '<b>Make images with your own Flux 2 Klein and Qwen encoder.</b> '
                'Choose 4B or 9B and the matching encoder and VAE in Local setup. The lower-memory option is suggested from '
                'detected VRAM; fit also depends on system RAM and image size. Local generation has no API token charges '
                'and may take longer. Model licenses and available hardware still apply.'}
        self.image_info.setText(messages[provider])

    def build_local(self):
        layout = self.page()
        self.local = WizardLocalModelsPanel(self.owner, self.local_tasks)
        layout.addWidget(self.local)

    def local_tasks(self):
        tasks = []
        if self.local_video.isChecked(): tasks.append('video')
        if self.image_model.currentData().startswith('local:'): tasks.append('image')
        if self.provider.currentData() == 'local': tasks.append('text')
        return tasks

    def select_local_section(self, index):
        self.steps.section = index; self.steps.refresh()
        if self.steps.currentRow() == 3:self.update_local_title()

    def update_local_title(self):
        self.title.setText('Local model setup — ' + ('Engine','Video · H3','Images · Flux','Writing · local')[self.local.tabs.currentIndex()])

    def update_readiness(self):
        states = self.local.readiness()
        for index,(state,explanation) in enumerate(states):self.steps.status(index,state,explanation)
        if hasattr(self,'progress_checks'):
            tasks = self.local_tasks(); indexes = {'video':1,'image':2,'text':3}
            self.progress_checks[0].setChecked(bool(tasks) and all(states[indexes[k]][0]=='ready' for k in tasks))
            self.progress_checks[1].setChecked(states[0][0]=='ready')

    def build_getting_started(self):
        layout = self.page(); frame, content = card('Getting started',
            'Let’s get you familiar with Director’s tools. Choose where you want to begin, or browse the guide.')
        row = QHBoxLayout(); row.setSpacing(16); self.start_cards = []; self.guide_buttons = []
        choices = [
            ('Highly Recommended','Interactive Quick Start','Try a guided tour of your workspace. We will show you the exact controls to use.',
             'Start Onboarding Tour','../help/images/aurora-startup.png',self.start_quick_start),
            ('Interactive Tutorial','Onboarding Tutorials','Explore short illustrated walkthroughs of setup, prompts, reference media and local generation.',
             'Interactive Tutorial','local.png',self.open_tutorials),
            ('Manual Guide','Explore Features Manually','Go directly to your workspace. Explore files, generation settings and local models at your own pace.',
             'Go to Workspace','../help/images/timeline-navigation.png',self.go_to_workspace)]
        for index,(badge,title,body,button_text,image,action) in enumerate(choices):
            tile, tile_layout = card(recommended=index==0); self.start_cards.append(tile)
            tag = label(badge); tag.setWordWrap(False); tag.setMinimumHeight(30); tag.setStyleSheet('background:#214b59;border-radius:8px;padding:6px;font-size:13px')
            tile_layout.addWidget(tag,0,Qt.AlignmentFlag.AlignLeft)
            tile_layout.addWidget(Artwork(ASSETS/image,145)); tile_layout.addWidget(label(title,'cardTitle'))
            tile_layout.addWidget(label(body)); tile_layout.addStretch()
            button = QPushButton(button_text)
            if index==0:primary(button)
            button.clicked.connect(action); tile_layout.addWidget(button); self.guide_buttons.append(button)
            if index==2:
                manual = QPushButton('Browse Features'); manual.clicked.connect(lambda:open_manual()); tile_layout.addWidget(manual)
            row.addWidget(tile,1)
        content.addLayout(row)
        content.addWidget(label('Onboarding Progress', 'cardTitle'))
        progress = QHBoxLayout(); self.progress_checks = []
        for text in ('Setup Local Files','Verify Python Engine','Complete First Guide','Explore Workspace'):
            check = QCheckBox(text); check.setEnabled(False); progress.addWidget(check); self.progress_checks.append(check)
        self.progress_checks[2].setChecked(self.settings.value('onboarding/quick_start_complete',False,bool))
        content.addLayout(progress); layout.addWidget(frame); layout.addStretch()

    def start_quick_start(self):
        self.hide(); self.workspace_tour = WorkspaceTour(self.owner)
        self.workspace_tour.completed.connect(lambda:self.progress_checks[2].setChecked(True))
        self.workspace_tour.finished.connect(lambda:self.show())
        self.workspace_tour.show(); self.workspace_tour.raise_()

    def open_tutorials(self):
        self.tutorial = QDialog(self); self.tutorial.setObjectName('onboardingWizard'); self.tutorial.setStyleSheet(STYLE)
        self.tutorial.setWindowTitle('Director · illustrated tutorials'); self.tutorial.resize(820,650)
        layout = QVBoxLayout(self.tutorial)
        self.tutorial_slides = Showcase([(title,body,image) for title,body,_,image in TOUR]); layout.addWidget(self.tutorial_slides)
        manual = QPushButton('Open the full interactive illustrated manual'); manual.clicked.connect(lambda:open_manual()); layout.addWidget(manual)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close); close.rejected.connect(self.tutorial.reject); layout.addWidget(close)
        self.tutorial.show()

    def go_to_workspace(self):
        self.progress_checks[3].setChecked(True); self.finish()

    def build_thanks(self):
        layout = self.page(); frame, content = card()
        self.showcase = Showcase([
            ('Explore what’s possible','Craft prompts, create thumbnails and render cinematic video with your local engines and writing companion.','local.png'),
            ('Find your visual language','Generate and refine scene artwork, use references and create your own Effects Library thumbnails.','images.png'),
            ('Direct every detail','Shape action, camera motion and sound with focused prompt refinement and reviewable changes.','language.png'),
            ('Build a sequence','Arrange beats, preserve their media associations and keep the unified production prompt in sync.','getting-started.png'),
            ('Make your next story','Preview finished work, organize shots and export a production handoff. The full illustrated guide is always in Help.','welcome.png')])
        self.showcase.setMinimumHeight(500); frame.setMinimumHeight(600)
        content.addWidget(self.showcase)
        self.summary_toggle = QPushButton('Review source choices and pending setup'); self.summary_toggle.setCheckable(True)
        content.addWidget(self.summary_toggle)
        self.summary = text_label(''); content.addWidget(self.summary); self.summary.hide()
        self.summary_toggle.toggled.connect(self.summary.setVisible)
        layout.addWidget(frame); layout.addStretch()

    def needs_local(self):
        return self.local_video.isChecked() or self.provider.currentData() == 'local' or self.image_model.currentData().startswith('local:')

    def advance(self, delta):
        row = self.steps.currentRow()
        if row == len(STEPS) - 1 and delta > 0: self.finish(); return
        row += delta
        if row == 3 and not self.needs_local(): row += delta
        self.steps.setCurrentRow(max(0, min(len(STEPS) - 1, row)))

    def go(self, row):
        if row < 0: return
        if self._last_text_provider in ('gemini', 'openai'): self._text_keys[self._last_text_provider] = self.text_key.text().strip()
        if row == 2 and self._last_image_provider == self.provider.currentData():
            self.image_key.setText(self.text_key.text())
        elif row == 2 and not self.image_key.text().strip():
            self.image_key.setText(self._text_keys.get(self._last_image_provider, ''))
        if self.pages.currentIndex() == 2 and row != 2 and self._last_image_provider in ('gemini', 'openai'):
            self._text_keys[self._last_image_provider] = self.image_key.text().strip()
            if self._last_image_provider == self.provider.currentData(): self.text_key.setText(self.image_key.text())
        self.pages.setCurrentIndex(row); self.title.setText(STEPS[row][0])
        self.header.setVisible(row < 3); self.donate.setVisible(row == 5)
        self.rail_progress.setValue(row+1)
        if row == 3:self.update_local_title()
        elif row == 4:self.title.setText('Getting started — Quick Tour & Onboarding')
        elif row == 5:self.title.setText('Thank you for completing the setup')
        self.subtitle.setText(['A few choices now. A whole sequence of possibilities.',
            'Choose the writing companion that fits your workflow.', 'Choose how Director creates artwork and thumbnails.',
            'Set up and configure your local generation engine. Point to existing files or download a matching set.',
            'Let’s get you familiar with Director’s tools. Choose where you want to begin, or browse the guide.',
            'You’re all set to begin creating with Director. Explore what’s possible through the showcase below.'][row])
        self.header_source = QPixmap(str(ASSETS / (STEPS[row][1] + '.png'))); self.fit_header()
        self.back.setEnabled(row > 0); self.next.setText('Finish' if row == len(STEPS) - 1 else 'Continue')
        self.hint.setText(['Choose cloud and local sources independently.', 'Keep credentials in these controls; you never need to paste keys into a prompt.',
            'Image generation uses its own model and billing choice.', 'Prepare the engine, find files, then get configuration files. Download weights only if needed.',
            'Ready to go? Explore at your own pace.', 'We hope you enjoy your experience!'][row])
        self.update_readiness()
        if row == len(STEPS) - 1: self.update_summary()

    def fit_header(self):
        if hasattr(self, 'header_source'):
            self.header.setPixmap(self.header_source.scaled(self.header.width(), self.header.height(),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event): super().resizeEvent(event); self.fit_header()

    def update_summary(self):
        pending = []
        provider = self.provider.currentData()
        if provider in ('gemini', 'openai') and not self.text_key.text().strip(): pending.append('Writing API key')
        if provider == 'chatgpt':
            p = self.owner.chatgpt_auth.active_profile()
            if not (p and p['connected'] and p['enabled']): pending.append('ChatGPT connection')
        image_provider = self.image_model.currentData().split(':')[0]
        if image_provider != 'local' and not (self.image_key.text().strip() or self._text_keys.get(image_provider)): pending.append('Image API key')
        profile = self.local.value()
        kinds = ([] if provider != 'local' else ['text']) + ([] if image_provider != 'local' else ['image']) + ([] if not self.local_video.isChecked() else ['video'])
        for kind in kinds:
            try: validate_profile(profile, kind)
            except ValueError: pending.append('Local ' + kind + ' files / configuration')
        self.summary.setText('<b>Your sources:</b> ' + self.provider.currentText() + '<br>' + self.image_model.currentText()
            + ('<p>Still to set up: ' + ', '.join(pending) + '. You can finish now and complete these in Settings.</p>' if pending else '<p>Your source choices are configured. You are ready for a first project.</p>'))

    def finish(self):
        provider = self.provider.currentData()
        if provider in ('gemini', 'openai'):
            self._text_keys[provider] = self.text_key.text().strip()
            self.settings.setValue(provider + '_model', self.text_model.currentText())
        image_provider = self.image_model.currentData().split(':')[0]
        if image_provider != 'local' and self.image_key.text().strip(): self._text_keys[image_provider] = self.image_key.text().strip()
        self.owner.session_keys = {**self.owner.session_keys, **self._text_keys}
        self.settings.setValue('provider', provider)
        self.settings.setValue('artwork/default_model', self.image_model.currentData())
        self.settings.setValue('remember_keys', self.remember.isChecked())
        for key, value in self._text_keys.items():
            if self.remember.isChecked(): self.settings.setValue(key + '_key', value)
            else: self.settings.remove(key + '_key')
        save_profile(self.settings, self.local.value())
        self.settings.setValue('onboarding/quick_start_complete', self.progress_checks[2].isChecked())
        self.settings.setValue('onboarding/seen', True); self.settings.sync()
        self.owner.update_provider_button(); self.accept()

    def setup_later(self):
        self.settings.setValue('onboarding/seen', True); self.reject()

    def done(self, result):
        self.local.dispose(); super().done(result)
