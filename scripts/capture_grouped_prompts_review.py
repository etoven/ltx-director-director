"""Capture the working group UI; no provider/model calls or layout substitutes."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import sys,tempfile,time,copy
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import requests
from PySide6.QtCore import Qt,QSettings,QRect
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication
from ltx_prompt_director import ui
from ltx_prompt_director.models import Segment
from ltx_prompt_director.media import thumbnail_for_image
OUT=ROOT/'docs/reviews/grouped-prompts';OUT.mkdir(parents=True,exist_ok=True)
app=QApplication([]);app.setStyle('Fusion');app.setFont(QFont('DejaVu Sans',10))
with tempfile.TemporaryDirectory(prefix='director-working-review-') as folder:
    settings=QSettings(str(Path(folder)/'settings.ini'),QSettings.Format.IniFormat)
    with patch('ltx_prompt_director.ui.QSettings',return_value=settings),patch.object(ui.MainWindow,'restore_startup_workspace'),patch.object(ui.MainWindow,'keep_window_on_screen'):
        w=ui.MainWindow()
    w._close_saves_queued=True;w.current_project_name='Grouped prompts · working UI review';w.current_project_id='review';w.update_window_title()
    w.set_project_type('minimax_references')
    image=ROOT/'ltx_prompt_director/assets/splash-aurora-background.png';preview=thumbnail_for_image(str(image))
    w.segments=[Segment('Prompt 1',str(image),preview,'image','start','A traveler reaches the aurora-lit ridge.',5),
        Segment('Prompt 2','','','text','text','The camera follows the traveler toward the overlook.',5),
        Segment('Prompt 3',str(image),preview,'image','end','The traveler pauses at the overlook.',5),
        Segment('Prompt 4','','','text','text','Continue from the previous ending. Turn toward the horizon.',5,render_group=1)]
    w.video_extensions=1;w._active_render_group=0
    w.requested_length.blockSignals(True);w.requested_length.setValue(15);w.requested_length.blockSignals(False)
    w.unified_prompt.setPlainText('subject_definitions\nA traveler beside an aurora-lit ridge.\n\nsummary\nReach the ridge, follow the traveler, then pause at the overlook.\n\ndetailed_description\n0–5 s: Reach the ridge.\n5–10 s: Follow toward the overlook.\n10–15 s: Pause at the overlook.')
    w.sfx.setChecked(True);w.store_render_group()
    other=copy.deepcopy(w.capture_render_group());other['directionOptions']['requestedLength']=5
    other['minimaxH3']['prompt']='subject_definitions\nThe same traveler and mountain landscape.\n\nsummary\nContinue from the previous ending; turn toward the horizon.\n\ndetailed_description\n0–5 s: Preserve the incoming motion, then turn slowly toward the horizon.'
    other['minimaxH3']['drafts']={};other['magicBuild']['sfx']=False
    w.render_group_options['1']=dict(context=other,requestedLength=5,blendLength=1,blend='perceptual_crossfade')
    w.refresh_timeline(0)
    for dock in (w.project_dock,w.effects_dock,w.catalog_dock,w.project_properties_dock,w.project_files_dock,w.reasoning_dock,w.render_queue_dock):dock.hide()
    w.minimax_panel.reference_dock.hide();w.conditioning_height=0;w.update_conditioning_guide();w.audio_panel.hide()
    w.project_preview_dock.show();w.project_preview_panel.clear_project()
    w.set_timeline_height(260);w.resize(1900,1000);w.show();w.prompt_surface.select('unified')
    def settle():
        until=time.monotonic()+.2
        while time.monotonic()<until:app.processEvents();time.sleep(.01)
    settle();w.autofit_timeline();settle()
    for group,row in [(0,0),(1,3)]:
        w.timeline.setCurrentRow(row);settle()
        w.grab().save(str(OUT/f'working-group-{group+1}.png'))
    w.set_timeline_scale(240);settle()
    boundary=w.timeline.visualItemRect(w.timeline.item(3)).left()
    w.timeline.horizontalScrollBar().setValue(w.timeline.horizontalScrollBar().value()+boundary-600);settle()
    w.centralWidget().grab(QRect(0,0,w.centralWidget().width(),440)).save(str(OUT/'working-blend-padding.png'))
    box=w._extension_boxes[1]
    from ltx_prompt_director.extension_timeline import preview_bounds
    bounds=[preview_bounds(w.timeline.itemWidget(w.timeline.item(i)),w.timeline.viewport()) for i in (2,3)]
    assert box.y()==max(value[0] for value in bounds)+10
    assert box.y()+box.height()==min(value[1] for value in bounds)-10
    w.render_queue.shutdown();w.close();app.processEvents()
print('Captured working UI: shared controls, 3+1 prompts, one scaled blend, 10 px padding.',flush=True)
sys.stdout.flush();os._exit(0)
