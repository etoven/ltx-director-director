"""Capture extension and queue UI using authored demo data, without model calls."""
from pathlib import Path
import os
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt,QSettings,QTimer,QItemSelectionModel,QRect
from PySide6.QtGui import QFont,QColor
from PySide6.QtWidgets import QApplication,QListWidgetItem,QMenu
from ltx_prompt_director import ui
from ltx_prompt_director.models import Segment
from ltx_prompt_director.media import thumbnail_for_image

app=QApplication.instance() or QApplication([]);app.setStyle('Fusion');app.setFont(QFont('DejaVu Sans',10))
output=ROOT/'docs/images';output.mkdir(parents=True,exist_ok=True)
backdrop=ROOT/'ltx_prompt_director/assets/splash-aurora-background.png'
with tempfile.TemporaryDirectory() as folder:
    settings=QSettings(str(Path(folder)/'settings.ini'),QSettings.Format.IniFormat)
    with patch('ltx_prompt_director.ui.QSettings',return_value=settings),patch.object(ui.MainWindow,'restore_startup_workspace'),patch.object(ui.MainWindow,'keep_window_on_screen'):
        w=ui.MainWindow()
    w._close_saves_queued=True;w.current_project_name='Aurora journey · UI review';w.current_project_id='review-demo';w.update_window_title()
    w.set_project_type('minimax_references')
    preview=thumbnail_for_image(str(backdrop))
    w.segments=[Segment('Aurora overlook',str(backdrop),preview,'image','start','A traveler pauses beside the overlook.',15)]
    w.requested_length.setValue(15);w.refresh_timeline()
    w.add_video_extension();w.requested_length.setValue(15)
    w.segments[1].kind='image';w.segments[1].role='start';w.segments[1].media_path=str(backdrop);w.segments[1].preview_path=preview
    w.segments[1].prompt='Follow the traveler toward the ridge.'
    w.render_group_options['1'].update(blend='perceptual_crossfade',blendLength=1)
    w.refresh_timeline(1)
    w.intent.setPlainText('A continuous journey toward the aurora-lit overlook. Preserve the traveler, mountain setting and blue-gold light across each continuation.')
    w.unified_prompt.setPlainText('subject_definitions\nA traveler in the aurora-lit mountain landscape.\n\nsummary\nA quiet walk toward the overlook, followed by a moment at the horizon.\n\ndetailed_description\nThe camera moves gently forward. The traveler follows the ridge and comes to rest beneath the aurora.\n\noverall_soundscape\nSoft footsteps and mountain wind.')
    for dock in (w.project_dock,w.effects_dock,w.catalog_dock,w.project_properties_dock,w.project_files_dock,w.reasoning_dock):dock.hide()
    w.minimax_panel.reference_dock.hide();w.conditioning_guide.hide();w.audio_panel.hide()
    w.set_timeline_height(230);w.resize(1900,1080)
    w.project_preview_dock.show();w.render_queue_dock.show()
    w.splitDockWidget(w.project_preview_dock,w.render_queue_dock,Qt.Orientation.Vertical)
    w.resizeDocks([w.project_preview_dock,w.render_queue_dock],[310,430],Qt.Orientation.Vertical)
    w.resizeDocks([w.project_preview_dock],[370],Qt.Orientation.Horizontal)
    queue=w.render_queue;queue.set_paused(True)
    for index,title in enumerate(['Block 1 · 15 s','Block 2 · 15 s','Final · frame blended · 30 s']):
        job=queue.enqueue(lambda:None,(),lambda _:None,lambda _:None,title=title,kind='Video',model='MiniMax H3' if index<2 else 'Frame blend',
            project_id='review-demo',group_id='demo-generation',group_title='Aurora journey · Generation 1',intermediate=index<2,
            workspace_key=w.video_preview_key(),generation_number=1)
        job.state='Complete';job.percent=100
        job.output_path=str(Path(folder)/f'render-{index}.mp4');Path(job.output_path).write_bytes(b'demo-output')
    w._video_generation_counts[w.video_preview_key()]=1
    queue.set_paused(False);queue.changed.emit();w.project_preview_panel.clear_project();w.project_preview_panel.title.setText('Video Preview · ready for a rendered video')
    w.show()
    def settle():
        until=time.monotonic()+.25
        while time.monotonic()<until:app.processEvents();time.sleep(.01)
    settle();w.autofit_timeline();settle()
    w.grab(QRect(0,0,w.centralWidget().geometry().right()+1,420)).save(str(output/'video-extensions-timeline.png'))
    w.render_queue_dock.setFloating(True);w.render_queue_dock.resize(440,800);w.render_queue_dock.show();w.render_queue_dock.raise_();settle()
    w.render_queue_panel.list.setCurrentRow(3);w.render_queue_panel.list.scrollToTop();settle()
    w.render_queue_panel.grab().save(str(output/'video-extensions-queue.png'))
    queue.jobs[0].state='Rendering';queue.jobs[0].percent=47
    for job in queue.jobs[1:]:job.state='Waiting';job.percent=None
    queue.changed.emit()
    w.project_preview_dock.setFloating(True);w.project_preview_dock.resize(520,680);w.project_preview_dock.show();w.project_preview_dock.raise_();settle()
    w.show_video_render_preview(dict(kind='preview',media='image',path=str(backdrop),step=8))
    w._video_generation_counts[w.video_preview_key()]=2;w.update_render_controls();settle()
    w.project_preview_panel.grab().save(str(output/'video-preview-rendering.png'))
    queue.shutdown();w.close();app.processEvents()
print('Captured extension timeline, render queue, and rendering preview.',flush=True)
sys.stdout.flush();os._exit(0)
