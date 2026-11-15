"""A render keeps its last frame while Qt loads the next video source."""
import subprocess
import time

import imageio_ffmpeg
from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor, QImage
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame
from PySide6.QtWidgets import QApplication

from ltx_prompt_director.ui import ProjectPreviewPanel


def test_last_frame_survives_source_loading_errors_and_final_video(tmp_path):
    app = QApplication.instance() or QApplication([])
    panel = ProjectPreviewPanel()
    panel.resize(500,400)
    panel.show()
    image = QImage(64,64,QImage.Format.Format_RGB32)
    image.fill(QColor('red'))
    panel.show_static_image('Sampling step 1', image)
    panel.hold_render_frame()
    panel._generation_preview_active = True
    panel._initial_media_pending = True
    panel._initial_media_autoplay = True
    panel._initial_media_position = 0
    panel.player.setSource(QUrl.fromLocalFile(str(tmp_path/'missing.mp4')))
    app.processEvents()
    panel.media_error()
    assert not panel.latent_image.isHidden()
    assert panel.current_frame.pixelColor(0,0).red() > 200
    panel.capture_frame(QVideoFrame())
    assert not panel.latent_image.isHidden()
    path = tmp_path/'replacement.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-hide_banner','-loglevel','error','-y',
                    '-f','lavfi','-i','color=c=blue:s=64x64:r=24:d=1','-c:v','libx264',str(path)],check=True)
    panel.set_project('Finished render',str(path),preserve_frame=True)
    assert not panel.latent_image.isHidden()
    deadline = time.monotonic()+5
    while panel._awaiting_render_frame and time.monotonic()<deadline:
        app.processEvents()
        time.sleep(.01)
    assert not panel._awaiting_render_frame
    assert panel.latent_image.isHidden() and not panel.video.isHidden()
    assert panel.current_frame.pixelColor(0,0).blue() > 200
    panel.clear_project()
    assert panel.current_frame is None and panel._latent_pixmap is None
    panel.close()
