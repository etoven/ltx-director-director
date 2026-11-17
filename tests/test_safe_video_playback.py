"""Minimum regression: software media playback and isolation of raw live frames."""
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import QThread
from PySide6.QtGui import QImage,QColor
from PySide6.QtMultimedia import QVideoFrame
from PySide6.QtWidgets import QApplication
from ltx_prompt_director.video_runtime import configure_playback
from ltx_prompt_director.ui import ProjectPreviewPanel


def test_playback_defaults_preserve_explicit_configuration():
    env={};configure_playback(env,'linux',enabled=False)
    assert env=={'QT_FFMPEG_DECODING_HW_DEVICE_TYPES':',','QT_DISABLE_HW_TEXTURES_CONVERSION':'1'}
    env['QT_FFMPEG_DECODING_HW_DEVICE_TYPES']='cuda';configure_playback(env,'linux',enabled=False)
    assert env['QT_FFMPEG_DECODING_HW_DEVICE_TYPES']=='cuda'
    env={};configure_playback(env,enabled=True);assert not env


def test_static_and_live_frames_do_not_reenter_video_sink(tmp_path):
    app=QApplication.instance() or QApplication([])
    panel=ProjectPreviewPanel();panel.show()
    try:
        image=QImage(32,24,QImage.Format.Format_RGB32);image.fill(QColor('red'))
        frames=[];panel.video.videoSink().videoFrameChanged.connect(lambda frame:frames.append(frame))
        panel.show_static_image('Remote image',image);app.processEvents()
        assert panel.current_frame.pixelColor(0,0).red()>200
        assert not any(frame.isValid() for frame in frames)
        bank=tmp_path/'live.rgb';bank.write_bytes(bytes((20,190,50))*6*4)
        panel.latent_player.load(dict(path=str(bank),width=6,height=4,latent_frames=1,frames=22,fps=24),True)
        panel.capture_frame(QVideoFrame(image))
        assert panel.current_frame.pixelColor(0,0).green()>150
        panel.latent_player.close();panel._static_latent_active=False
        assert panel.metaObject().indexOfSlot('capture_frame(QVideoFrame)')>=0
    finally:panel.clear_project();panel.close()


def test_layout_virtuals_tolerate_cleared_python_storage():
    from ltx_prompt_director.responsive_layout import ControlRow
    app=QApplication.instance() or QApplication([])
    row=ControlRow();row.__dict__.pop('_items')
    assert row.count()==0 and row.itemAt(0) is None and row.takeAt(0) is None
    assert row.minimumSize().isValid()


def test_hardware_playback_preference_persists(tmp_path):
    from PySide6.QtCore import QSettings
    from ltx_prompt_director.video_runtime import HARDWARE_PLAYBACK_KEY, hardware_playback_enabled
    path=str(tmp_path/'playback.ini')
    settings=QSettings(path,QSettings.Format.IniFormat)
    assert not hardware_playback_enabled(settings)
    settings.setValue(HARDWARE_PLAYBACK_KEY,True);settings.sync()
    reopened=QSettings(path,QSettings.Format.IniFormat)
    assert hardware_playback_enabled(reopened)
    env={};configure_playback(env,enabled=hardware_playback_enabled(reopened));assert not env
    reopened.setValue(HARDWARE_PLAYBACK_KEY,False);reopened.sync()
    env={};configure_playback(env,enabled=hardware_playback_enabled(reopened))
    assert env['QT_FFMPEG_DECODING_HW_DEVICE_TYPES']==','
