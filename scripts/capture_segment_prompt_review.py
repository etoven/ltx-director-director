"""Capture the native local segment diff with authored data and no provider calls."""
from pathlib import Path
import os
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
runtime = tempfile.TemporaryDirectory(prefix='director-segment-review-docs-')
for kind in ('CONFIG', 'DATA', 'CACHE'):
    os.environ[f'XDG_{kind}_HOME'] = str(Path(runtime.name) / kind.lower())
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ltx_prompt_director import ui
from ltx_prompt_director.models import Segment
from ltx_prompt_director.workspaces import WorkspaceStore

app = QApplication([])
app.setOrganizationName('TovenSolutionsDocs')
app.setApplicationName('DirectorSegmentReview')
app.setStyle('Fusion')
app.setFont(QFont('DejaVu Sans', 10))
store = WorkspaceStore(Path(runtime.name) / 'workspaces')
with patch.object(ui, 'WorkspaceStore', return_value=store), patch.object(ui.MainWindow, 'restore_startup_workspace'):
    window = ui.MainWindow()
window.set_project_type('minimax_base_guide')
window.current_project_name = 'Aurora / Review one passage'
window.update_window_title()
for dock in (window.project_dock, window.project_properties_dock, window.project_files_dock, window.project_preview_dock):
    dock.hide()
window.resize(1320, 940)
actions = ['Establish the traveler beneath the aurora.',
           'Follow the traveler toward the overlook. The camera holds a steady forward view.',
           'Slow to a stop and hold the mountain horizon.']
window.segments = [Segment('Opening / Aurora', '', '', 'text', 'text', actions[0], 3),
                   Segment('Journey / Walking beat', '', '', 'text', 'text', actions[1], 5),
                   Segment('Arrival / Horizon hold', '', '', 'text', 'text', actions[2], 4)]
window.refresh_timeline(0)
window.unified_prompt.setPlainText(
    'integrated_multimodal_description:\n'
    f'[Shot 1] At 00:00.00, {actions[0]}\n\n'
    f'[Shot 2] At 00:03.00, {actions[1]}\n\n'
    f'[Shot 3] At 00:08.00, {actions[2]}\n\n'
    'overall_soundscape: Quiet footsteps and mountain wind.\n\nnon_diegetic_music: N/A')
window.show()
for _ in range(8):
    app.processEvents()
window.set_timeline_height(170)
window.set_conditioning_height(0)
window.set_director_intent_expanded(True, persist=False)
window.intent_prompt_splitter.setSizes([150, 400])
window.autofit_timeline()
window.timeline.setCurrentRow(1)
window.reload_clicked_segment(window.timeline.item(1))
source = window.intent_segment_prompt
window.raise_()
window.activateWindow()
QTest.qWait(200)
source.setFocus()
app.processEvents()
source.selectAll()
QTest.keyClicks(source, 'Follow the traveler toward the overlook. The camera trucks left at walking pace.')
for _ in range(8):
    app.processEvents()
assert window.segment_prompt_review.active
output = ROOT / 'docs/images/segment-prompt-live-review.png'
assert window.grab().save(str(output))
print(output)
window.segment_prompt_review.reject()
window.project_dirty = False
window._close_saves_queued = True
window.close()
app.processEvents()
