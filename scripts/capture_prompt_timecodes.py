"""Capture the native unified prompt timing demo with isolated local data."""
from pathlib import Path
import os
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
runtime = tempfile.TemporaryDirectory(prefix='director-timecode-docs-')
for kind in ('CONFIG', 'DATA', 'CACHE'):
    os.environ[f'XDG_{kind}_HOME'] = str(Path(runtime.name) / kind.lower())
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from ltx_prompt_director import ui
from ltx_prompt_director.models import Segment
from ltx_prompt_director.workspaces import WorkspaceStore

app = QApplication([])
app.setOrganizationName('TovenSolutionsDocs')
app.setApplicationName('DirectorTimecodes')
app.setStyle('Fusion')
app.setFont(QFont('DejaVu Sans', 10))
store = WorkspaceStore(Path(runtime.name) / 'workspaces')
with patch.object(ui, 'WorkspaceStore', return_value=store), patch.object(ui.MainWindow, 'restore_startup_workspace'):
    window = ui.MainWindow()
window.set_project_type('minimax_base_guide')
window.current_project_name = 'Aurora / Local timing edits'
window.update_window_title()
window.project_dock.hide()
window.project_properties_dock.hide()
window.project_files_dock.hide()
window.project_preview_dock.hide()
window.resize(1320, 940)
window.intent.setPlainText('A quiet journey beneath the aurora. Preserve the traveler and the blue-gold mountain light.')
window.segments = [
    Segment('Opening / Look to the sky', '', '', 'text', 'text', 'Establish the traveler beneath the aurora.', 3),
    Segment('Journey / Approach the overlook', '', '', 'text', 'text', 'Follow the traveler toward the overlook.', 5),
    Segment('Arrival / Hold the horizon', '', '', 'text', 'text', 'Slow to a stop and hold the horizon.', 4),
]
window.refresh_timeline(0)
window.unified_prompt.setPlainText(
    'One continuous cinematic journey beneath the northern lights.\n\n'
    '00:00.00 – 00:03.00: Establish the traveler beneath the aurora.\n\n'
    '00:03.00 – 00:08.00: Follow the traveler toward the overlook. '
    'The coat moves softly in the breeze; the camera stays at walking pace.\n\n'
    '00:08.00 – 00:12.00: Slow to a stop and hold the mountain horizon.\n\n'
    'Sound: quiet footsteps, soft fabric movement and mountain wind.'
)
window.show()
for _ in range(8):
    app.processEvents()
window.set_timeline_height(170)
window.set_conditioning_height(0)
window.set_director_intent_expanded(False, persist=False)
window.prompt_splitter.setSizes([90, 360])
window.autofit_timeline()
window.begin_timeline_edge_drag(window.segments[0].id)
window.resize_timeline_edge(window.segments[0].id, 4)
window.finish_resize(window.segments[0].id)
window.timeline.setCurrentRow(1)
window.reload_clicked_segment(window.timeline.item(1))
for _ in range(8):
    app.processEvents()
output = ROOT / 'docs/images/unified-prompt-timecodes.png'
assert window.grab().save(str(output))
print(output)
window.project_dirty = False
window._close_saves_queued = True
window.close()
app.processEvents()
