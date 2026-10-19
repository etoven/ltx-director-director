"""A resumable first-run guide using the same account and model controls as Settings."""
from pathlib import Path
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QPixmap, QDesktopServices
from PySide6.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QListWidget, QStackedWidget, QPushButton, QComboBox, QLineEdit, QCheckBox,
    QFormLayout, QScrollArea, QFrame)
from .ai import GEMINI_MODELS
from .artwork_tools import IMAGE_MODEL_CHOICES
from .chatgpt_widgets import ChatGPTSettingsPanel
from .local_generation import load_profile, save_profile, validate_profile
from .local_widgets import LocalModelsPanel

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
        self.resize(1080, 800)
        self.setStyleSheet('QDialog{background:#1c272d} QLabel{font-size:13px} QPushButton{padding:6px 12px} QLineEdit,QComboBox,QSpinBox{min-height:25px}')
        self.setMinimumSize(780, 600)
        outer = QHBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        rail = QFrame(); rail.setStyleSheet('background:#10232f;color:#c9e9ec')
        rail.setFixedWidth(190); left = QVBoxLayout(rail); left.setContentsMargins(18, 24, 18, 24)
        brand = QLabel('DIRECTOR\nYour creative workspace'); brand.setWordWrap(True); brand.setStyleSheet('font-size:17px;font-weight:bold;padding-bottom:18px')
        left.addWidget(brand)
        self.steps = QListWidget(); self.steps.setStyleSheet('QListWidget{border:0;background:transparent} QListWidget::item{padding:12px 5px;border:0;margin:3px 0;color:#b4d2db;font-size:12px} QListWidget::item:selected{background:#235165;border:0;border-radius:5px;color:white}')
        self.steps.addItems([f'{i + 1}  {label}' for i, (label, _) in enumerate(STEPS)])
        left.addWidget(self.steps, 1)
        left.addWidget(text_label('A few choices now.<br>A whole sequence of possibilities.'))
        outer.addWidget(rail)
        main = QWidget(); right = QVBoxLayout(main); right.setContentsMargins(22, 18, 22, 16)
        self.header = QLabel(); self.header.setFixedHeight(175); self.header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header.setStyleSheet('background:#14232d;border-radius:8px')
        right.addWidget(self.header)
        self.title = QLabel(); self.title.setStyleSheet('font-size:22px;font-weight:bold;padding-top:8px')
        right.addWidget(self.title)
        self.pages = QStackedWidget(); right.addWidget(self.pages, 1)
        self.build_welcome(); self.build_writing(); self.build_images(); self.build_local(); self.build_getting_started(); self.build_thanks()
        self.hint = QLabel(); self.hint.setWordWrap(True); self.hint.setStyleSheet('color:#84b9c4;padding:4px')
        right.addWidget(self.hint)
        buttons = QHBoxLayout()
        self.later = QPushButton('Set up later'); self.later.clicked.connect(self.setup_later); buttons.addWidget(self.later)
        buttons.addStretch()
        self.back = QPushButton('Back'); self.back.clicked.connect(lambda: self.advance(-1)); buttons.addWidget(self.back)
        self.next = QPushButton('Continue'); self.next.clicked.connect(lambda: self.advance(1)); buttons.addWidget(self.next)
        right.addLayout(buttons); outer.addWidget(main, 1)
        self.steps.currentRowChanged.connect(self.go)
        self.steps.setCurrentRow(0)

    def page(self):
        page = QWidget(); layout = QVBoxLayout(page); layout.setSpacing(12)
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
        self.local = LocalModelsPanel(self.owner)
        layout.addWidget(self.local)

    def build_getting_started(self):
        layout = self.page()
        self.slide = QStackedWidget(); layout.addWidget(self.slide, 1)
        for title, body in [
            ('1 · Build your timeline', 'Create a project, choose its workspace, then add images, video or text beats. '
                'Put them in order and adjust duration. Save to Library to keep a native project with its media associations.'),
            ('2 · Direct and refine', 'Write the motion you want. Segment scope changes the selected beat; Global scope acts on '
                'the sequence. The unified editor has its own controls. Review proposed text and approve or reject changes. '
                'Drag an effect onto one segment for a focused rewrite.'),
            ('3 · Preview and hand off', 'Open Video Preview to attach a render or generate locally. Draft previews and a progress pill '
                'appear during native generation. Export your timeline or prompt workflow when ready. Use Shot Sequence to arrange '
                'finished shots, trim them and review playback.')]:
            page = QWidget(); content = QVBoxLayout(page); content.addWidget(text_label('<h3>' + title + '</h3><p>' + body + '</p>')); content.addStretch(); self.slide.addWidget(page)
        row = QHBoxLayout()
        for i, label in enumerate(('Timeline', 'Refinement', 'Preview and export')):
            button = QPushButton(label); button.clicked.connect(lambda _, n=i: self.slide.setCurrentIndex(n)); row.addWidget(button)
        layout.addLayout(row)
        help_button = QPushButton('Open the full interactive illustrated manual'); help_button.clicked.connect(lambda: open_manual())
        layout.addWidget(help_button)

    def build_thanks(self):
        layout = self.page()
        layout.addWidget(text_label('<h3>Thanks for making something with Director.</h3>'
            '<p>Your stories, experiments and feedback help shape this workspace.</p>'))
        self.summary = text_label(''); layout.addWidget(self.summary)
        donate = QPushButton('Support development · donation link coming soon'); donate.setEnabled(False); layout.addWidget(donate)
        manual = QPushButton('Explore the interactive help'); manual.clicked.connect(lambda: open_manual()); layout.addWidget(manual)
        layout.addWidget(text_label('You can revisit every choice in Settings, or reopen this wizard from Help.'))
        layout.addStretch()

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
        self.header_source = QPixmap(str(ASSETS / (STEPS[row][1] + '.png'))); self.fit_header()
        self.back.setEnabled(row > 0); self.next.setText('Finish setup' if row == len(STEPS) - 1 else 'Continue')
        self.hint.setText(['Choose cloud and local sources independently.', 'Keep credentials in these controls; you never need to paste keys into a prompt.',
            'Image generation uses its own model and billing choice.', 'Prepare the engine, find files, then get configuration files. Download weights only if needed.',
            'Three short slides cover the core creative loop.', 'Finish saves your choices. You can keep editing while optional model setup remains pending.'][row])
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
        self.settings.setValue('onboarding/seen', True); self.settings.sync()
        self.owner.update_provider_button(); self.accept()

    def setup_later(self):
        self.settings.setValue('onboarding/seen', True); self.reject()

    def done(self, result):
        self.local.dispose(); super().done(result)
