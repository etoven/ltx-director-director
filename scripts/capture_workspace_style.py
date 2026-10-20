"""Capture the native workspace reference layout with isolated XDG settings and no inference.

Run under a desktop-sized virtual display, e.g. xvfb-run -a -s "-screen 0 1920x1200x24".
"""
import os,sys,tempfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
capture_environment=tempfile.TemporaryDirectory(prefix='director-workspace-docs-')
for key,folder in (('XDG_CONFIG_HOME','config'),('XDG_DATA_HOME','data'),('XDG_CACHE_HOME','cache')):
 os.environ[key]=str(Path(capture_environment.name)/folder)
from PySide6.QtCore import QTimer,Qt
from PySide6.QtWidgets import QApplication
from ltx_prompt_director.ui import MainWindow
app=QApplication([])
with patch.object(MainWindow,'restore_startup_workspace'):w=MainWindow()
w._close_saves_queued=True
w.set_project_type('minimax_references')
w.project_dock.hide();w.project_preview_dock.hide();w.project_files_dock.hide();w.project_properties_dock.hide()
w.dock_effects(Qt.DockWidgetArea.LeftDockWidgetArea);w.effects_dock.show()
w.set_reference_panel_open(True)
w.resize(1920,1080);w.show()
def capture():
 w.resizeDocks([w.effects_dock,w.minimax_panel.reference_dock],[380,380],Qt.Orientation.Horizontal)
 QTimer.singleShot(500,save)
def save():
 w.grab().save(str(ROOT/'docs/images/aurora-workspace.png'))
 w.effects_dock.grab().save(str(ROOT/'docs/images/effects-library.png'))
 w.close();app.quit()
QTimer.singleShot(1000,capture)
app.exec()
capture_environment.cleanup()
sys.stdout.flush();sys.stderr.flush();os._exit(0)
