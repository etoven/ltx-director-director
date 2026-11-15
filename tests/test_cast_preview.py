"""Native Cast selection with a fixture receiver and real HTTP/HLS media."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import time
import urllib.request
import av
import numpy as np
from PySide6.QtCore import QSettings,QTimer
from PySide6.QtGui import QImage,QColor
from PySide6.QtWidgets import QApplication,QMenu
from PySide6.QtTest import QTest
from ltx_prompt_director.ui import MainWindow
from ltx_prompt_director.render_queue import RenderJob

APP=QApplication.instance() or QApplication([])


def wait_for(condition):
    for _ in range(2000):
        APP.processEvents()
        if condition():return
        time.sleep(.005)
    assert condition()


def test_cast_video_audio_ranges_live_video_flux_and_stop(tmp_path):
    calls=[];stops=[]
    device=SimpleNamespace(name='Living Room TV',uuid='fixture',cast_info=SimpleNamespace(host='127.0.0.1'),
        wait=lambda **kwargs:None,media_controller=SimpleNamespace(play_media=lambda *args,**kwargs:calls.append((args,kwargs)),stop=lambda:stops.append(True)),disconnect=lambda:None)
    settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat)
    with patch('ltx_prompt_director.ui.QSettings',return_value=settings),patch.object(MainWindow,'restore_startup_workspace'):
        w=MainWindow();w._close_saves_queued=True;w.resize(1300,900);w.show()
        try:
            cast=w.cast_preview;cast.found(device)
            video=tmp_path/'attached.mp4'
            with av.open(str(video),'w') as output:
                stream=output.add_stream('libx264',rate=24);stream.width=128;stream.height=96;stream.pix_fmt='yuv420p'
                for i in range(24):
                    frame=av.VideoFrame.from_ndarray(np.full((96,128,3),(30,160,80),np.uint8),format='rgb24')
                    for packet in stream.encode(frame):output.mux(packet)
                for packet in stream.encode():output.mux(packet)
            panel=w.project_preview_panel;panel.set_project('Fixture clip',str(video))
            image=QImage(128,96,QImage.Format.Format_RGB888);image.fill(QColor('#20a050'));panel.current_frame=image
            # Click the actual Cast button and choose its Video Preview action.
            def choose():
                popup=APP.activePopupWidget();assert isinstance(popup,QMenu)
                action=popup.actions()[0].menu().actions()[0];action.trigger();popup.close()
            QTimer.singleShot(60,choose);cast.button.click()
            wait_for(lambda:len(calls)==1)
            url,mime=calls[0][0];assert mime=='video/mp4' and calls[0][1]['stream_type']=='BUFFERED'
            request=urllib.request.Request(url,headers={'Range':'bytes=0-63'})
            with urllib.request.urlopen(request,timeout=3) as response:
                assert response.status==206 and response.read()==video.read_bytes()[:64]
                assert response.headers['Accept-Ranges']=='bytes'
            # Live sampling switches the same receiver to a streaming endpoint.
            job=RenderJob('Live MiniMax','', 'network','MiniMax H3','Video',state='Rendering',percent=37,eta_seconds=125)
            w.render_queue.jobs.append(job);panel.set_rendering(True);cast.update()
            wait_for(lambda:len(calls)>=2)
            live,mime=calls[-1][0];assert mime=='application/x-mpegURL' and calls[-1][1]['stream_type']=='LIVE'
            with urllib.request.urlopen(live,timeout=3) as response:assert b'#EXTM3U' in response.read()
            segments=list(cast.server.root.glob('*.ts'));assert segments
            with av.open(str(segments[-1])) as media:
                decoded=next(media.decode(video=0)).to_image();assert decoded.size==(960,540)
            assert not panel.play.isVisible() and not panel.seek.isVisible()
            # Flux uses the same cast destination, including a live draft image.
            flux=QImage(128,96,QImage.Format.Format_RGB888);flux.fill(QColor('#c03060'))
            from PySide6.QtGui import QPixmap
            w.image_generation_panel.pixmap=QPixmap.fromImage(flux)
            prior=len(calls);cast.start('fixture','image');cast.update();APP.processEvents()
            assert cast.source=='image' and len(calls)==prior and calls[-1][1]['title'].endswith('Live preview')
            center=(270*960+480)*3
            assert cast.server.latest[center:center+3]==bytes((192,48,96))
            w.project_preview_dock.show();w.project_preview_dock.raise_();APP.processEvents()
            w.project_preview_panel.grab().save(str(tmp_path/'cast-preview.png'))
            server=cast.server;cast.stop();wait_for(lambda:stops and server.closed.is_set())
            assert cast.device is None and not cast.timer.isActive()
            w.render_queue.jobs.clear()
        finally:
            w.grouped_sequence.timer.stop();w.shot_sequence_panel.deactivate();w.render_queue.shutdown();w.close()
