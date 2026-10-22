"""Capture the panel redesign with authored demo data and no model calls."""
from pathlib import Path
import os,sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ltx_prompt_director import ui
from ltx_prompt_director.media import data_url,thumbnail_for_image
from ltx_prompt_director.models import Segment

app=QApplication([]);app.setOrganizationName('DirectorPanelDocs');app.setApplicationName('PanelShowcase')
app.setStyle('Fusion');app.setFont(QFont('DejaVu Sans',10))
with patch.object(ui.MainWindow,'restore_startup_workspace'),patch.object(ui.MainWindow,'keep_window_on_screen'):
    w=ui.MainWindow()
w._close_saves_queued=True
art=ROOT/'ltx_prompt_director/assets/splash-aurora-background.png'
preview=thumbnail_for_image(str(art));cover=data_url(str(art))
w.segments=[Segment('Aurora overlook',str(art),preview,'image','start',
    'Follow the traveler at walking pace toward the aurora-lit overlook. Keep the cool mountain light and warm lantern glow. Preserve the established wardrobe and the gentle camera movement.',5),
    Segment('The quiet arrival','','','text','text',
    'Slow to a quiet stop at the horizon. Hold the traveler in the foreground while the aurora drifts across the mountain sky. Keep the camera continuous and the ending unhurried.\n\nSound: soft footsteps, coat fabric and mountain wind.',5)]
w.refresh_timeline(1);w.set_project_type('minimax_references')
w.intent.setPlainText('A continuous, peaceful journey to a mountain overlook. Preserve the traveler, landscape, aurora and blue-gold light.')
w.unified_prompt.setPlainText('summary\nA quiet journey to an aurora-lit overlook.\n\ndetailed_description\n[Shot 1] At 00:00, follow the traveler toward the horizon.\n[Shot 2] At 00:05, slow to a stop and hold the landscape.\n\noverall_soundscape\nSoft footsteps and mountain wind.\n\nnon_diegetic_music\nN/A')
w.prompt_surface.select('segment')
w.set_minimax_reference_image(0,{'name':'Aurora atmosphere','image':cover,'role':'style','notes':'Cool mountain sky and warm lantern light.'})
w.show_reference_images()
records=[]
for index,(name,status,color) in enumerate([
    ('Journey to the overlook','Draft','#2ed05d'),('Mountain arrival','Ready','#168cff'),
    ('Walking beat','Needs review','#ffbf00'),('Aurora hold','Completed','#893dff'),
    ('Quiet footsteps','Draft','#2ed05d'),('Horizon study','In progress','#168cff')]):
    records.append(dict(id=f'panel-demo-{index}',name=name,description='A calm mountain sequence',status=status,
        color=color,tags=[],archived=False,thumbnailData=cover,collection='',notes=[]))
w.library_records=lambda:records
w.current_project_id='panel-demo-0';w.current_project_name='Aurora / Journey to the Overlook'
with patch.object(ui,'ensure_collection_sequences',return_value=[]):w.refresh_project_library('panel-demo-0')
w.resize(1500,1080);w.show();w.project_dock.show()
w.resizeDocks([w.project_dock,w.minimax_panel.reference_dock],[330,330],Qt.Orientation.Horizontal)
w.set_timeline_height(184);w.autofit_timeline();QTest.qWait(350)
w.project_dock.raise_();QTest.qWait(100)
out=ROOT/'docs/images';out.mkdir(exist_ok=True)
w.grab().save(str(out/'panel-overhaul.png'))
w.project_dock.grab().save(str(out/'project-browser.png'))
w.prompt_surface.grab().save(str(out/'prompt-scope-panel.png'))
# The existing timing strip remains part of the native layout.
w.conditioning_guide.grab().save(str(out/'panel-timing-overview.png'))
w.library_surface.status.menu().popup(w.library_surface.status.mapToGlobal(w.library_surface.status.rect().bottomLeft()))
QTest.qWait(100)
menu=w.library_surface.status.menu()
menu.grab().save(str(out/'project-status-filter.png'));menu.close()
w.library_surface.color_menu.popup(w.library_surface.color.mapToGlobal(w.library_surface.color.rect().bottomLeft()))
QTest.qWait(100)
w.library_surface.color_menu.grab().save(str(out/'project-status-colors.png'));w.library_surface.color_menu.close()
w.project_dirty=False;w.close()
print('Captured panel overhaul, project browser, scope editor, status menu and retained timing overview.',flush=True)
os._exit(0)
