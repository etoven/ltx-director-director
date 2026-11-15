from types import SimpleNamespace

from PySide6.QtCore import QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ltx_prompt_director.ui import MainWindow, ProjectPreviewPanel


def test_direct_frames_loop_update_pause_seek_and_release(tmp_path):
    app = QApplication.instance() or QApplication([])
    panel = ProjectPreviewPanel()
    panel.show()
    panel.set_rendering(True)
    assert all(control.isHidden() for control in (panel.play, panel.seek, panel.fullscreen, panel.position))
    panel._generation_autoplay = True
    owner = SimpleNamespace(project_preview_panel=panel, video_preview_key=lambda: ('test',))
    path = tmp_path / 'step-1.rgb'
    # Odd widths also exercise unpadded RGB rows.
    path.write_bytes(bytes((255, 0, 0)) * 5 * 4 + bytes((0, 255, 0)) * 5 * 4)
    event = dict(media='latent_frames', path=str(path), width=5, height=4,
                 latent_frames=2, frames=6, fps=24)
    frames = []
    panel.latent_player.frame_presented.connect(lambda image: frames.append(image.copy()))
    MainWindow.show_video_render_preview(owner, event)
    assert panel.player.source() == QUrl()
    QTest.qWait(850)  # More than three complete loops, without media end events.
    assert len(frames) >= 8 and panel.latent_player.playing
    assert frames[-1].width() == 5
    assert not panel.latent_image.pixmap().isNull()
    surface = panel.video_surface.grab().toImage()
    color = surface.pixelColor(surface.width() // 2, surface.height() // 2)
    assert color.red() + color.green() > 150, 'Preview surface must actually show the streamed RGB pixels'

    assert all(control.isHidden() for control in (panel.play, panel.seek, panel.fullscreen, panel.position))
    panel.set_rendering(False)
    panel.toggle_playback()
    panel.seek_to(150)
    old = panel.latent_player.data
    second = tmp_path / 'step-2.rgb'
    second.write_bytes(bytes((0, 0, 255)) * 5 * 4 * 2)
    MainWindow.show_video_render_preview(owner, {**event, 'path': str(second)})
    assert old.closed
    assert not panel.latent_player.playing and panel.latent_player.position == 150
    assert panel.current_frame.pixelColor(0, 0).blue() == 255
    panel.resize(640, 480)
    QTest.qWait(80)
    surface = panel.video_surface.grab().toImage()
    assert surface.pixelColor(surface.width() // 2, surface.height() // 2).blue() == 255
    import os
    if os.environ.get('DIRECTOR_PREVIEW_CAPTURE'):
        panel.grab().save(os.environ['DIRECTOR_PREVIEW_CAPTURE'])
    count = len(frames)
    QTest.qWait(150)
    assert len(frames) == count
    panel.toggle_playback()
    QTest.qWait(100)
    assert len(frames) > count
    panel.clear_project()
    count = len(frames)
    QTest.qWait(100)
    assert not panel.latent_player.active and not panel.latent_player.timer.isActive()
    assert len(frames) == count
    panel.close()
