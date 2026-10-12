"""Render MiniMax mixed reference slots and the branded ComfyUI export action."""
import os,sys,tempfile,wave,subprocess
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from PySide6.QtCore import QSettings
from ltx_prompt_director import ui
from ltx_prompt_director.models import Segment
from ltx_prompt_director.workspaces import WorkspaceStore
from ltx_prompt_director.reference_media import import_reference
from ltx_prompt_director.media import data_url,thumbnail_for_image
import imageio_ffmpeg
app=QApplication([]);app.setOrganizationName('TovenSolutionsDocs');app.setApplicationName('ComfyExportShowcase');app.setStyle('Fusion');app.setFont(QFont('DejaVu Sans',10));QSettings().clear()
with tempfile.TemporaryDirectory() as d:
    root=Path(d);store=WorkspaceStore(root/'workspaces')
    with patch.object(ui,'WorkspaceStore',return_value=store),patch.object(ui.MainWindow,'restore_startup_workspace'):
        w=ui.MainWindow()
    w.resize(1540,1020)
    for dock in w.findChildren(ui.QDockWidget):dock.hide()
    source=ROOT/'ltx_prompt_director/assets/splash-aurora-background.png'
    preview=thumbnail_for_image(str(source))
    w.segments=[Segment('Aurora Opening',str(source),preview,'image','start','Hold the mountain overlook.',2),Segment('Aurora Ending',str(source),preview,'image','end','The traveler reaches the lantern.',4)]
    w.refresh_timeline(0);w.set_project_type('minimax_full_reference_guide')
    w.current_project_name='Aurora / Mixed References';w.update_window_title()
    w.set_director_intent_expanded(False)
    audio=root/'Wind reference.wav'
    with wave.open(str(audio),'wb') as f:f.setnchannels(2);f.setsampwidth(2);f.setframerate(48000);f.writeframes(b'\0\0'*2*48000)
    video=root/'Voice sample.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-v','error','-loop','1','-i',str(source),'-i',str(audio),'-t','1','-vf','scale=320:180','-r','24','-pix_fmt','yuv420p',str(video)],check=True)
    values=[{'name':'Character identity.png','image':data_url(str(source)),'role':'identity'},
            {'name':'Mountain lighting.png','image':data_url(str(source)),'role':'style'},
            {**import_reference(video),'role':'voice','audio_only':True,'notes':'Use this voice; ignore the video appearance.'},import_reference(audio,'sound')]
    for i,value in enumerate(values):w.set_minimax_reference_image(i,value)
    w.unified_prompt.setPlainText('[REFERENCE USE]\nImage3 controls the traveler’s identity; Image4 controls cool aurora lighting. Audio1 supplies the voice sample; Audio2 supplies the wind texture.\n\n[SCENE]\nA quiet mountain overlook, warm lantern glow and drifting aurora.\n\n[TIMED ACTION]\n00:00.000–00:06.000: In one continuous shot, follow the traveler toward the lantern. Preserve the start image and reach the end image at 6.0 seconds.')
    w.show_reference_images();w.show()
    for _ in range(10):app.processEvents()
    out=ROOT/'docs/images/minimax-comfyui-export.png';w.grab().save(str(out));print(out)
    w._close_saves_queued=True;w.project_dirty=False;w.close()
