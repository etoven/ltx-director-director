"""Toolbar writing-model switching without constructing the Settings dialog."""
import json
from PySide6.QtWidgets import QComboBox
from .ai import GEMINI_MODELS


class ModelPicker(QComboBox):
    def __init__(self, owner):
        super().__init__(owner); self.owner = owner
        self.setObjectName('writingModelPicker'); self.setAccessibleName('Writing model')
        self.setToolTip('Switch the writing model for your next request')
        self.setMinimumContentsLength(15); self.setMaximumWidth(230)
        self.currentIndexChanged.connect(self.choose); self.refresh()

    def refresh(self):
        settings = self.owner.settings
        current_provider = str(settings.value('provider', 'gemini'))
        current = str(settings.value(current_provider + '_model', GEMINI_MODELS[0] if current_provider == 'gemini' else 'gpt-5.4-mini' if current_provider == 'openai' else ''))
        self.blockSignals(True); self.clear()
        for provider, models in [('gemini', list(GEMINI_MODELS)),
                                  ('openai', ['gpt-6', 'gpt-6-mini', 'gpt-5.4-mini'])]:
            saved = str(settings.value(provider + '_model', ''))
            if saved and saved not in models:
                models.insert(0, saved)
            for model in models:
                self.addItem(('Gemini · ' + model.removeprefix('gemini-')) if provider == 'gemini' else 'OpenAI · ' + model,
                             (provider, model))
        try:
            plan_models = json.loads(str(settings.value('chatgpt_models_cache', '[]')))
        except (TypeError, ValueError):
            plan_models = []
        saved = str(settings.value('chatgpt_model', ''))
        if saved and not any(row[0] == saved for row in plan_models):
            plan_models.insert(0, [saved, saved])
        for slug, label in plan_models:
            self.addItem('ChatGPT · ' + label, ('chatgpt', slug))
        self.addItem('Local writing model', ('local', ''))
        index = self.findData((current_provider, current if current_provider != 'local' else ''))
        self.setCurrentIndex(max(0, index)); self.blockSignals(False)

    def choose(self, index):
        choice = self.itemData(index)
        if not choice:
            return
        provider, model = choice
        self.owner.settings.setValue('provider', provider)
        if model:
            self.owner.settings.setValue(provider + '_model', model)
        self.owner.queue_settings_sync()

    def findData(self, value, *args):
        # Compare Python values: Qt's QVariant equality can miss tuple payloads
        # once native combo models have been styled or reset.
        for index in range(self.count()):
            if self.itemData(index) == value:
                return index
        return -1

    def setText(self, _text):
        # Compatibility for theme/status refreshes that previously addressed a button.
        self.refresh()

    def text(self):
        return self.currentText()
