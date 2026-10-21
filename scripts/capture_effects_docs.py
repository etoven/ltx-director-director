"""Capture the shipped catalog artwork in the native effects browser, without AI calls."""
import os
from pathlib import Path
import sys
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication
from ltx_prompt_director.ui import MainWindow
app = QApplication([])
with patch.object(MainWindow, 'restore_startup_workspace'):
    w = MainWindow()
w._close_saves_queued = True
w.effects_dock.setFloating(True)
w.effects_dock.resize(850, 740)
w.effects_panel.category.setCurrentText('Body mechanics')
w.effects_dock.show()
def capture():
    w.effects_panel.stop_preview()
    path = ROOT / 'docs/images/effects-library.png'
    path.parent.mkdir(parents=True, exist_ok=True)
    w.effects_dock.grab().save(str(path))
    print(path, flush=True)
    w.effects_panel.category.setCurrentText('Source soundtracks')
    app.processEvents()
    w.effects_dock.grab().save(str(ROOT/'docs/images/effects-sounds.png'))
    w.close(); app.quit()
QTimer.singleShot(1000, capture)
app.exec()
sys.stdout.flush(); sys.stderr.flush(); os._exit(0)
