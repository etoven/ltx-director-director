"""Capture actual native setup/preview controls with no provider or model calls."""
import os
from pathlib import Path
import sys
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from ltx_prompt_director.ui import MainWindow, SettingsDialog
from ltx_prompt_director.onboarding import OnboardingWizard
app = QApplication([])
with patch.object(MainWindow, 'restore_startup_workspace'): w = MainWindow()
w._close_saves_queued = True
wizard = OnboardingWizard(w); wizard.show()
out = ROOT / 'docs/images'
def capture_wizard():
    wizard.grab().save(str(out / 'setup-wizard.png'))
    wizard.local.fields['python'].setText('~/.local/share/PromptDirector/local-generation/engine/bin/python')
    wizard.local.fields['models_root'].setText('~/Models')
    wizard.steps.setCurrentRow(3)
    QTimer.singleShot(250, capture_local)
def capture_local():
    wizard.grab().save(str(out / 'local-model-setup.png'))
    wizard.steps.setCurrentRow(4)
    QTimer.singleShot(250, capture_getting_started)
def capture_getting_started():
    wizard.grab().save(str(out / 'onboarding-getting-started.png'))
    wizard.steps.setCurrentRow(5)
    QTimer.singleShot(250, capture_thanks)
def capture_thanks():
    wizard.grab().save(str(out / 'onboarding-thanks.png'))
    wizard.reject()
    panel = w.project_preview_panel
    w.project_preview_dock.setFloating(True); w.project_preview_dock.resize(740, 500)
    panel.title.setText('Local generation · interface example')
    panel.empty.setText('Live noisy latents appear here during generation.\nThis is a control example; no model render was run.')
    w.project_preview_dock.show()
    QTimer.singleShot(250, capture_preview)
def capture_preview():
    w.local_video_pill.update_event({'kind': 'progress', 'percent': 42, 'eta_seconds': 80})
    w.project_preview_dock.grab().save(str(out / 'local-video-controls.png'))
    dialog = SettingsDialog(w.settings, w); dialog.resize(1000, 1060)
    dialog.layout().itemAt(0).widget().setCurrentIndex(1)
    panel = dialog.local_models; panel.tabs.setCurrentIndex(1)
    for key, name in {'video_model': 'minimax_h3_ref2va_pruned_int8_convrot.safetensors',
                      'video_encoder': 'qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors',
                      'video_vae': 'minimax_h3_video_vae_fp16.safetensors',
                      'audio_vae': 'minimax_h3_audio_vae_fp32.safetensors'}.items():
        panel.fields[key].setText('~/Models/' + name)
    panel.fields['video_assets'].setText('~/.local/share/PromptDirector/local-generation/models/h3-assets/Ref2VA/processor')
    panel.video_random_seed.setChecked(False); panel.video_seed.setValue(931351171509952); dialog.show()
    def capture_dialog():
        dialog.grab().save(str(out / 'local-video-settings.png')); dialog.reject()
        print('Captured wizard, local setup, video controls and local video Settings page.', flush=True)
        w.close(); app.quit()
    QTimer.singleShot(250, capture_dialog)
QTimer.singleShot(500, capture_wizard)
app.exec()
