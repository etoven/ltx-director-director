"""Capture the native Shot Sequence workspace with an authored geometric film demo."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tempfile
from unittest.mock import patch
from fractions import Fraction
import numpy as np
import av
from PIL import Image,ImageDraw
from PySide6.QtWidgets import QApplication,QLabel
from PySide6.QtCore import QElapsedTimer,QEventLoop,Qt
from PySide6.QtGui import QPixmap
from ltx_prompt_director.ui import MainWindow
from ltx_prompt_director.workspaces import WorkspaceStore
from ltx_prompt_director.shot_sequence import render_sequence,DEFAULTS
from ltx_prompt_director.shot_sequence_widgets import import_shots


def make_clip(path,palette,offset):
    with av.open(str(path),'w') as container:
        stream=container.add_stream('mpeg4',rate=24);stream.width=640;stream.height=352;stream.pix_fmt='yuv420p'
        for i in range(48):
            y=np.linspace(0,1,352)[:,None,None]
            rgb=(1-y)*np.array(palette[0])[None,None,:]+y*np.array(palette[1])[None,None,:]
            pixels=np.broadcast_to(rgb,(352,640,3)).astype('uint8').copy()
            image=Image.fromarray(pixels);draw=ImageDraw.Draw(image)
            draw.ellipse((445+i//4,54,504+i//4,113),fill=(246,223,160))
            draw.polygon([(0,245),(130,148),(242,265),(420,167),(640,256),(640,352),(0,352)],fill=(23,59,65))
            draw.polygon([(0,286),(200,231),(430,302),(640,240),(640,352),(0,352)],fill=(12,33,43))
            draw.rectangle((278+offset,173,343+offset,262),fill=(24,44,57))
            draw.pieslice((265+offset,139,356+offset,225),180,360,fill=(36,64,74))
            draw.rectangle((294+offset,194,309+offset,227),fill=(235,174,88))
            frame=av.VideoFrame.from_ndarray(np.asarray(image),format='rgb24');frame.pts=i;frame.time_base=Fraction(1,24)
            for packet in stream.encode(frame):container.mux(packet)
        for packet in stream.encode():container.mux(packet)

app=QApplication.instance() or QApplication([])
with tempfile.TemporaryDirectory() as temporary:
    root=Path(temporary);movies=[]
    for title,palette,offset in [('01 - The observatory',[(26,61,90),(224,135,90)],0),
                                ('02 - The approach',[(26,91,111),(174,155,116)],12),
                                ('03 - Blue hour',[(20,39,91),(102,121,135)],24)]:
        path=root/(title+'.mp4');make_clip(path,palette,offset);movies.append(str(path))
    with patch('ltx_prompt_director.ui.WorkspaceStore',return_value=WorkspaceStore(root/'workspaces')),patch.object(MainWindow,'restore_startup_workspace'),patch('ltx_prompt_director.ui.project_library_path',return_value=root):
        window=MainWindow();window.set_project_type('shot_sequence')
        window.current_project_name='Observatory · finished sequence';window.current_collection='Observatory'
        window.shot_sequence_settings={**DEFAULTS,'width':640,'height':352,'overlap':.5}
        window.segments=import_shots(movies,root/'shots');window.refresh_timeline()
        for dock in [window.project_preview_dock,window.project_files_dock,window.project_properties_dock,window.catalog_dock]:dock.hide()
        window.resize(1460,900);window.show()
        panel=window.shot_sequence_panel
        panel.preview.set_memory_video('Observatory · continuous sequence',render_sequence(movies,window.shot_sequence_settings,preview=True))
        timer=QElapsedTimer();timer.start()
        while (panel.preview.current_frame is None or timer.elapsed()<500) and timer.elapsed()<3000:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents,20)
        # Native video surfaces are excluded by QWidget.grab; show the identical decoded frame.
        if panel.preview.current_frame is not None:
            still=QLabel(panel.preview.video)
            still.setGeometry(panel.preview.video.rect())
            still.setAlignment(Qt.AlignmentFlag.AlignCenter)
            still.setPixmap(QPixmap.fromImage(panel.preview.current_frame).scaled(panel.preview.video.size(),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
            still.show()
            app.processEvents()
        target=Path(__file__).resolve().parents[1]/'docs/images/shot-sequence.png'
        window.grab().save(str(target))
        print(target)
        window.project_dirty=False;window._close_saves_queued=True;window.close()
