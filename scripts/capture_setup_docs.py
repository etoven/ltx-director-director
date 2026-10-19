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
from ltx_prompt_director.ui import MainWindow
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
    wizard.reject()
    panel = w.project_preview_panel
    w.project_preview_dock.setFloating(True); w.project_preview_dock.resize(740, 500)
    panel.title.setText('Local generation · interface example')
    panel.empty.setText('Draft video samples appear here during generation.\nThis is a control example; no model render was run.')
    w.project_preview_dock.show()
    QTimer.singleShot(250, capture_preview)
def capture_preview():
    w.local_video_pill.update_event({'kind': 'progress', 'percent': 42, 'eta_seconds': 80})
    w.project_preview_dock.grab().save(str(out / 'local-video-controls.png'))
    print('Captured wizard, local setup and video controls.', flush=True)
    w.close(); app.quit()
QTimer.singleShot(500, capture_wizard)
app.exec()
