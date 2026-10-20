"""Capture the native LoRA Settings page with demo paths; no model calls."""
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
app = QApplication([])
with patch.object(MainWindow, 'restore_startup_workspace'): window = MainWindow()
window._close_saves_queued = True
dialog = SettingsDialog(window.settings, window); dialog.resize(1000, 940)
dialog.layout().itemAt(0).widget().setCurrentIndex(1)
panel = dialog.local_models; panel.tabs.setCurrentIndex(2)
for key, value in dict(image_model='flux-2-klein-base-4b-fp8.safetensors', image_encoder='qwen_3_4b.safetensors', image_vae='flux2-vae.safetensors').items():
    panel.fields[key].setText('~/Models/' + value)
panel.fields['image_assets'].setText('~/Director/models/klein-4b-assets')
panel.loras['image'].add_row(dict(path='~/Models/loras/ink-illustration.safetensors', trigger='ink illustration, fine linework', weight=.65))
panel.loras['image'].add_row(dict(path='~/Models/loras/paper-texture.safetensors', trigger='textured paper', weight=.3))
dialog.show()
def capture():
    dialog.grab().save(str(ROOT / 'docs/images/local-loras.png'))
    dialog.reject(); window.close(); app.quit()
QTimer.singleShot(500, capture)
app.exec()
