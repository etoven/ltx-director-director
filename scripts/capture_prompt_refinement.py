"""Capture the standalone annotated prompt editor without provider calls."""
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from ltx_prompt_director import ui
from ltx_prompt_director.workspaces import WorkspaceStore
from ltx_prompt_director.prompt_tags import render_prompt_notes

app = QApplication([])
app.setOrganizationName('TovenSolutionsDocs')
app.setApplicationName('PromptRefinementShowcase')
app.setStyle('Fusion')
app.setFont(QFont('DejaVu Sans', 10))
QSettings().clear()
with tempfile.TemporaryDirectory() as directory:
    store = WorkspaceStore(Path(directory) / 'workspaces')
    with patch.object(ui, 'WorkspaceStore', return_value=store), patch.object(ui.MainWindow, 'restore_startup_workspace'):
        window = ui.MainWindow()
    window.resize(1330, 840)
    for dock in window.findChildren(ui.QDockWidget):
        dock.hide()
    window.set_project_type('minimax_prompt_refinement')
    window.current_project_name = 'Aurora / A Quiet Moment'
    window.update_window_title()
    window.unified_prompt.setPlainText('''integrated_multimodal_description: [Shot 1] Cinematic live-action. A traveler stands at a mountain overlook, holding a warm lantern. Cool aurora light drifts above the horizon. The camera holds a static medium shot while the traveler takes a slow breath and looks toward the northern sky.

/refine Make the lantern movement and breathing feel natural and continuous.

/keep Static camera, mountain setting, blue aurora and warm lantern glow.

/refine-global Add one brief spoken line in English, timed for a twelve-second scene. Preserve the existing format.

overall_soundscape: A quiet breeze crosses the overlook. Soft fabric movement and gentle breathing remain audible.

non_diegetic_music: N/A

/avoid Background music or new camera cuts.''')
    render_prompt_notes(window.unified_prompt)
    window.show()
    for _ in range(5):
        app.processEvents()
    output = ROOT / 'docs/images/minimax-prompt-refinement.png'
    window.grab().save(str(output))
    window._close_saves_queued = True
    window.project_dirty = False
    window.close()
    print(output)
