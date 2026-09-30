import time
import subprocess
from unittest.mock import patch
from PySide6.QtCore import QMimeData, QUrl, QPoint, Qt
from PySide6.QtGui import QImage, QColor, QDragEnterEvent
from PySide6.QtWidgets import QApplication, QPushButton
from ltx_prompt_director import ui
from ltx_prompt_director.minimax_reference_widgets import MiniMaxReferenceSlot
from ltx_prompt_director.workspaces import WorkspaceStore


def pump(app, predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert predicate()


def test_reference_context_actions_and_native_drag(tmp_path):
    app = QApplication.instance() or QApplication([])
    slot = MiniMaxReferenceSlot(1)
    image = QImage(640, 480, QImage.Format.Format_RGB32)
    image.fill(QColor('red'))
    source = tmp_path / 'original.png'
    image.save(str(source))
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(source))])
    event = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    slot.dragEnterEvent(event)
    assert event.isAccepted() and slot.property('dropActive')
    assert slot.load_mime(mime)
    slot.set_drop_active(False)
    assert not slot.property('dropActive')
    assert {b.text() for b in slot.findChildren(QPushButton)} == {'Browse', 'Paste'}
    exported = []
    slot.export_requested.connect(lambda: exported.append(True))
    slot.export_action.trigger()
    assert exported == [True]
    native = slot.drag_mime()
    assert native.hasUrls() and native.hasImage()
    restored = QImage(native.urls()[0].toLocalFile())
    assert restored.size() == image.size()
    assert restored.pixelColor(0, 0) == image.pixelColor(0, 0)
    slot.clear_action.trigger()
    assert slot.value is None and not slot.export_action.isEnabled()
    assert slot.drag_mime() is None
    invalid = QMimeData()
    invalid.setUrls([QUrl.fromLocalFile(str(tmp_path / 'movie.mp4'))])
    assert not slot.accepts_mime(invalid)


def test_lightbox_originals_and_video_frame_tools(tmp_path):
    app = QApplication.instance() or QApplication([])
    with patch.object(ui, 'WorkspaceStore', return_value=WorkspaceStore(tmp_path / 'workspaces')), patch.object(ui.MainWindow, 'restore_startup_workspace'):
        owner = ui.MainWindow()
    owner.download_directory = str(tmp_path / 'exports')
    image = QImage(1024, 768, QImage.Format.Format_RGB32)
    image.fill(QColor('#336699'))
    source = tmp_path / 'photo.png'
    image.save(str(source))
    movie = tmp_path / 'movie.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=320x240:d=2',
                    '-c:v', 'mpeg4', str(movie)], check=True)
    entries = [{'id': str(i), 'path': str(path), 'name': path.name, 'tags': ['Sample'], 'description': 'Test media'}
               for i, path in enumerate([source, movie])]
    viewer = ui.CatalogMediaViewer(owner, entries)
    try:
        viewer.show()
        app.processEvents()
        assert viewer.original.size() == image.size()
        assert 'Sample' in viewer.details.text()
        viewer.export_original()
        pump(app, lambda: owner._pending_disk_jobs == 0)
        assert (tmp_path / 'exports' / 'photo.png').read_bytes() == source.read_bytes()
        viewer.navigate(1)
        pump(app, lambda: viewer.video.current_frame is not None)
        assert isinstance(viewer.video, ui.ProjectPreviewPanel)
        viewer.video.copy_current_frame()
        assert not app.clipboard().image().isNull()
        viewer.video.export_current_frame()
        frames = list((tmp_path / 'exports').glob('movie_*ms.png'))
        assert len(frames) == 1
        assert QImage(str(frames[0])).size() == viewer.video.current_frame.size()
        viewer.navigate(-1)
        assert viewer.video.player.source().isEmpty()
        assert viewer.original.size() == image.size()
        app.processEvents()
        app.processEvents()
        assert viewer.image.pixmap().height() > 500
        viewer.grab().save('/tmp/catalog-lightbox25.png')
    finally:
        viewer.close()
        owner._close_saves_queued = True
        owner.project_dirty = False
        owner.close()
