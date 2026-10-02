"""Render current native UI with a local, authored demo project; no provider calls."""
from pathlib import Path
import base64
import json
import os
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt, QSettings, QRect
from PySide6.QtGui import QFont, QTextCursor, QPainter, QColor
from PySide6.QtWidgets import QApplication, QTabWidget, QWidget
from ltx_prompt_director import ui
from ltx_prompt_director.models import Segment
from ltx_prompt_director.media import data_url, thumbnail_for_image
from ltx_prompt_director.workspaces import WorkspaceStore
from ltx_prompt_director.workspace_editor import WorkspaceEditor
from ltx_prompt_director.splash import StartupSplash
from ltx_prompt_director.project_data import new_note
from ltx_prompt_director.prompt_tags import insert_note
import imageio_ffmpeg

OUTPUT = ROOT / 'docs/images'
DEMO = Path('/tmp/ltx-docs-demo')
DEMO.mkdir(parents=True, exist_ok=True)
OUTPUT.mkdir(parents=True, exist_ok=True)
app = QApplication([])
app.setOrganizationName('TovenSolutionsDocs')
app.setApplicationName('DirectorShowcase')
app.setStyle('Fusion')
app.setFont(QFont('DejaVu Sans', 10))
QSettings().clear()
backdrop = ROOT / 'ltx_prompt_director/assets/splash-aurora-background.png'
preview = thumbnail_for_image(str(backdrop))
video = DEMO / 'Aurora - Review.mp4'
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-loglevel', 'error', '-loop', '1', '-i', str(backdrop), '-t', '12', '-vf', 'scale=1280:720', '-r', '24', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], check=True)
store = WorkspaceStore(DEMO / 'workspaces')
with patch.object(ui, 'WorkspaceStore', return_value=store), patch.object(ui.MainWindow, 'restore_startup_workspace'):
    w = ui.MainWindow()
    app.processEvents()
w._restoring_layout = False
w.resize(1560, 1060)
w.project_dock.hide()
w.project_properties_dock.hide()
w.project_files_dock.hide()
w.project_preview_dock.hide()
w.current_project_name = 'Aurora / Journey to the Overlook'
w.update_window_title()
w.download_directory = str(DEMO / 'Exports')
Path(w.download_directory).mkdir(exist_ok=True)
w.intent.setPlainText('Create a graceful, continuous journey toward the aurora-lit overlook. Follow the traveler at walking pace. Preserve the mountain setting, cool blue light and warm lantern glow. End with a quiet moment at the horizon.')
w.requested_length.setValue(12)
w.direction_toggle.setChecked(True)
w.sfx.setChecked(True)
w.reduce_music.setChecked(True)
actions = ['Ease forward as the traveler studies the mountain horizon. Aurora light shimmers overhead.', 'Follow the traveler toward the overlook. The coat moves softly in the breeze; the camera stays at walking pace.', 'Slow to a gentle stop beside the traveler. Hold the horizon while the aurora drifts across the sky.']
w.segments = [Segment('Opening / Aurora overlook', str(backdrop), preview, 'image', 'start', actions[0], 3), Segment('The journey / Walking beat', '', '', 'text', 'text', actions[1], 5), Segment('Arrival / Horizon hold', str(backdrop), preview, 'image', 'end', actions[2], 4)]
w.refresh_timeline(0)
w.set_timeline_height(210)
w.show()

def settle(seconds=.3):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        app.processEvents()
        time.sleep(.01)

def shot(name, widget=None, height=None):
    settle()
    target=widget or w
    pixmap=target.grab(QRect(0,0,target.width(),height)) if height else target.grab()
    pixmap.save(str(OUTPUT / (name+'.png')))
    print(name, flush=True)

brief = "subject_definitions\nSubject1: the traveler, guided by Picture1 and Picture2.\n\nsummary\nA quiet journey toward an aurora-lit mountain overlook.\n\nretention_analysis\nPreserve the traveler's wardrobe, mountain setting and blue-gold light.\n\ndetailed_description\n[Shot 1] " + ' '.join(actions) + "\n\noverall_soundscape\nSoft footsteps, mountain wind and coat fabric.\n\nnon_diegetic_music\nN/A"
w.set_project_type('minimax_references')
w.unified_prompt.setPlainText(brief)
w.autofit_timeline()
w.minimax_panel.clear_message()
w.segment_prompt.moveCursor(QTextCursor.MoveOperation.Start)
shot('ltx-director-director-overview')
shot('timeline-and-magic-build', w.timeline.parentWidget())
w.timeline.setCurrentRow(1)
w.reload_clicked_segment(w.timeline.item(1))
shot('shared-segment-editors', w.prompt_splitter)
shot('conditioning-guide', w.conditioning_guide)
# Inline direction is entered using the same native note component as the app.
w.unified_prompt.moveCursor(QTextCursor.MoveOperation.End)
w.unified_prompt.insertPlainText('\n\n')
insert_note(w.unified_prompt, w.unified_prompt.textCursor(), '/refine-global', 'Keep the journey gentle. Give the final horizon hold room to breathe.')
w.unified_prompt.moveCursor(QTextCursor.MoveOperation.End)
w.unified_prompt.insertPlainText('\n\n')
insert_note(w.unified_prompt, w.unified_prompt.textCursor(), '/keep', 'Blue-gold lighting and a continuous camera path.')
w.unified_prompt.moveCursor(QTextCursor.MoveOperation.End)
w.unified_prompt.ensureCursorVisible()
shot('inline-refinement-notes', w.unified_prompt.parentWidget())
# References view.
w.set_project_type('minimax_references')
w.unified_prompt.setPlainText(brief.replace('[FRAME USE]', '[REFERENCE USE]'))
w.set_minimax_reference_image(0, {'name':'Aurora atmosphere.png', 'image':data_url(str(backdrop)), 'role':'style', 'notes':'Use the cool sky, warm lanterns and luminous atmosphere.'})
w.show_reference_images()
w.resizeDocks([w.minimax_panel.reference_dock], [350], Qt.Orientation.Horizontal)
settle()
w.autofit_timeline()
w.minimax_panel.clear_message()
w.unified_prompt.moveCursor(QTextCursor.MoveOperation.Start)
shot('minimax-references')
shot('reference-images', w.minimax_panel.reference_dock, 350)
# LTX segment editor.
w.set_project_type('ltx')
w.minimax_panel.reference_dock.hide()
w.global_prompt.setPlainText('One continuous cinematic journey. Preserve the traveler, mountain setting and blue-gold lighting. Maintain a calm walking pace. Ambient wind and footsteps; no background music.')
w.segments[1].prompt = actions[1] + '\n\nCamera: maintain a gentle forward tracking movement, with the traveler in the foreground and the mountain horizon visible beyond. Keep the motion continuous and unhurried.\n\nPerformance: relaxed shoulders, steady steps and a brief glance toward the sky. Let the coat respond naturally to the breeze.\n\nContinuity: preserve the established wardrobe, blue aurora illumination and warm lantern accents as the shot approaches the final hold.\n\nSound: quiet footsteps, soft fabric movement and mountain wind.'
w.timeline.setCurrentRow(1)
w.load_editor(1)
w.autofit_timeline()
shot('ltx-workspace')
w.spoken_dialog.setChecked(True)
w.speaker_language.setCurrentText('English')
w.speaker_accent.setCurrentText('General American')
shot('direction-and-audio', w.director_panel)
w.spoken_dialog.setChecked(False)
# A genuinely playable sample clip in the native player.
w.project_preview_dock.show()
w.resizeDocks([w.project_preview_dock], [560], Qt.Orientation.Horizontal)
w.project_preview_panel.set_project(w.current_project_name, str(video))
settle(.8)
w.autofit_timeline()
w.project_preview_panel.player.play()
settle(1.3)
w.project_preview_panel.player.pause()
# QVideoWidget's GPU surface is absent in offscreen Qt. Paint the actual
# decoded frame through a native raster widget for faithful headless capture.
assert w.project_preview_panel.current_frame is not None
captured_frame=w.project_preview_panel.current_frame.copy()
class DecodedFrameSurface(QWidget):
    def paintEvent(self, event):
        painter=QPainter(self)
        painter.fillRect(self.rect(), QColor('#080c0f'))
        frame=captured_frame
        size=frame.size().scaled(self.size(),Qt.AspectRatioMode.KeepAspectRatio)
        from PySide6.QtCore import QRect
        painter.drawImage(QRect((self.width()-size.width())//2,(self.height()-size.height())//2,size.width(),size.height()),frame)
        painter.end()
frame_surface=DecodedFrameSurface(w.project_preview_panel.video)
frame_surface.setGeometry(w.project_preview_panel.video.rect())
frame_surface.show()
shot('video-review')
w.project_preview_dock.setFloating(True)
w.project_preview_dock.resize(900,620)
settle()
frame_surface.setGeometry(w.project_preview_panel.video.rect())
shot('video-preview', w.project_preview_panel)
w.project_preview_dock.setFloating(False)
frame_surface.hide()
w.project_preview_dock.hide()
# Gallery and metadata contain demonstration projects only.
cover=data_url(str(backdrop), max_edge=512)
records=[]
for i,(name,desc,status) in enumerate([('Aurora / Journey to the Overlook','A twelve-second cinematic journey beneath the northern lights.','Consider'),('Lanterns / Evening Arrival','A quiet approach to a mountain village.','Done'),('Horizon / Final Hold','A calm finishing beat with room to breathe.','Re-shoot')]):
    records.append({'id':f'demo-{i}', 'name':name, 'description':desc, 'collection':'', 'status':status, 'tags':[], 'archived':False, 'thumbnailData':cover, 'savedAt':'2026-09-30T01:00:00', 'notes':[new_note('Review pacing at the transition into the final hold.'), {**new_note('Confirm the reference image roles.'), 'checked':True}], 'downloadDirectory':w.download_directory})
w.current_project_id='demo-0'
w.library_records=lambda: records
w.project_dock.show()
w.set_project_icon_size(156)
w.refresh_project_library('demo-0')
w.project_properties_dock.show()
w.resizeDocks([w.project_dock,w.project_properties_dock],[320,350],Qt.Orientation.Horizontal)
w.direction_toggle.setChecked(False)
w.resize(1560, 870)
settle()
w.autofit_timeline()
shot('project-properties')
w.resize(1560,1060)
shot('project-library', w.project_dock)
shot('project-details', w.project_properties_dock)
w.project_dock.hide()
w.project_properties_dock.hide()
w.project_files_dock.show()
w.project_files_panel.set_project(w.current_project_name,[{'id':'workflow-1','name':'Aurora - ComfyUI workflow.json','path':str(DEMO/'Aurora - ComfyUI workflow.json')}])
w.project_files_dock.setFloating(True)
w.project_files_dock.resize(450,300)
shot('project-files',w.project_files_dock)
w.project_files_dock.setFloating(False)
w.project_files_dock.hide()
# Export examples are real local demo files, not fabricated user exports.
for name,content in [('Aurora - Director.json','{"demo":true}'),('Aurora - Review.mp4',None),('Aurora - Opening.png',None)]:
    target=Path(w.download_directory)/name
    target.write_bytes((video if name.endswith('.mp4') else backdrop).read_bytes() if content is None else content.encode())
    w.record_export(target)
w.toggle_downloads()
settle(1)
shot('export-history',w.download_tray)
w.download_tray.hide()
# Global catalog with actual demonstration image and video sources.
from ltx_prompt_director.catalog import CatalogStore
w.media_catalog.store = CatalogStore(DEMO / 'media-catalog.json')
w.media_catalog.store.entries = []
w.media_catalog.store.folders = ['Aurora', 'Aurora/References']
w.media_catalog.store.add([str(backdrop), str(video)], 'Aurora')
for entry in w.media_catalog.store.entries:
    entry['tags'] = ['Aurora', 'Review' if entry['path'].endswith('.mp4') else 'Reference']
    entry['description'] = 'Aurora demonstration footage for the journey to the overlook.'
w.media_catalog.store.save()
w.media_catalog.refresh_folders()
w.media_catalog.refresh_tiles()
w.catalog_dock.setFloating(True)
w.catalog_dock.resize(820, 580)
w.catalog_dock.show()
from PySide6.QtCore import QThreadPool
QThreadPool.globalInstance().waitForDone(10000)
settle()
w.media_catalog.tiles.setCurrentRow(0)
shot('media-catalog', w.catalog_dock)
viewer = ui.CatalogMediaViewer(w, w.media_catalog.store.entries)
viewer.show()
shot('catalog-image-viewer', viewer)
viewer.navigate(1)
settle(1)
# Qt's native video surface is not included by QWidget.grab offscreen.
# Paint the actual decoded player frame for the documentation capture.
if viewer.video.current_frame is not None:
    captured_frame = viewer.video.current_frame.copy()
    catalog_surface = DecodedFrameSurface(viewer.video.video)
    catalog_surface.setGeometry(viewer.video.video.rect())
    catalog_surface.show()
shot('catalog-video-viewer', viewer)
viewer.close()
w.catalog_dock.hide()
# Current settings and workspace editor.
settings=ui.SettingsDialog(w.settings,w)
settings.resize(960,850)
settings.show()
shot('application-settings',settings)
tabs=settings.findChild(QTabWidget)
tabs.setCurrentIndex(1)
editor=settings.findChild(WorkspaceEditor)
editor.new()
editor.identifier.setText('cinematic_journey')
editor.name.setText('Cinematic Journey')
editor.engine.setCurrentIndex(editor.engine.findData('generic'))
editor.mode.setCurrentIndex(editor.mode.findData('unified'))
editor.global_prompt.setChecked(False)
editor.slots.setValue(2)
editor.generate.setPlainText('Write a continuous cinematic production brief from the timeline and Director\'s Intent.\n\nPreserve the reference roles, character identity and lighting. Describe the camera path and visible action between checkpoints.\n\nUse the supplied intervals for timed actions. Add natural ambient sound and finish with a quiet visual hold.\n\nDirector\'s Intent: ${director_intent}\nTimeline: ${ordered_plan}\nTotal duration: ${total_duration}')
editor.refine.setPlainText('Refine the current prompt using the inline direction and reference images.\n\nPreserve the established scene and requested pacing. Improve clarity, transitions and camera motion.\n\nCurrent prompt: ${current_prompt}\nRefinement direction: ${refinement_instructions}')
shot('workspace-definitions',settings)
settings.hide()
splash=StartupSplash()
splash.set_status('Opening your creative workspace…')
splash.show()
shot('aurora-startup',splash)
splash.hide()
w._close_saves_queued=True
w.project_dirty=False
w.close()
print('Native screenshot capture complete. Demo prompts are authored examples.',flush=True)
