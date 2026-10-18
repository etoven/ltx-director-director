import time
import base64
import io
import json
import zipfile
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QColor
from ltx_prompt_director import ui
from ltx_prompt_director import ai, project_archive
from ltx_prompt_director.models import Segment
from PIL import Image
from ltx_prompt_director.workspaces import WorkspaceStore


def test_reference_export_preserves_resolution_uses_actual_format_and_records_history(tmp_path):
    app = QApplication.instance() or QApplication([])
    with patch.object(ui, 'WorkspaceStore', return_value=WorkspaceStore(tmp_path / 'workspaces')), patch.object(ui.MainWindow, 'restore_startup_workspace'):
        window = ui.MainWindow()
    try:
        window.download_directory = str(tmp_path / 'exports')
        window.set_project_type('minimax_references')
        first, second = window.minimax_panel.reference_targets[:2]
        assert not first.export_action.isEnabled()
        assert not second.export_action.isEnabled()
        image = QImage(1024, 768, QImage.Format.Format_RGB32)
        image.fill(QColor('#336699'))
        first.load_image(image, 'Reference.jpg')
        assert first.export_action.isEnabled()
        first.export_action.trigger()
        first.export_action.trigger()
        deadline = time.monotonic() + 10
        while window._pending_disk_jobs and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert window._pending_disk_jobs == 0
        for name in ['Reference.png', 'Reference (1).png']:
            path = tmp_path / 'exports' / name
            restored = QImage(str(path))
            assert restored.size() == image.size()
            assert restored.pixelColor(0, 0) == image.pixelColor(0, 0)
        assert len(window.download_tray.history) >= 2
        assert window.download_tray.history[0]['path'] == str(tmp_path / 'exports' / 'Reference (1).png')
        first.clear()
        assert not first.export_action.isEnabled()
    finally:
        window._close_saves_queued = True
        window.project_dirty = False
        window.close()


def test_reopened_video_reference_uses_source_name_in_ui_provider_and_export(tmp_path):
    app = QApplication.instance() or QApplication([])
    preview = io.BytesIO()
    Image.new('RGB', (24, 16), 'blue').save(preview, 'JPEG')
    video = b'\x00\x00\x00\x20ftypisom\x00\x00\x02\x00original video bytes'
    source = tmp_path / 'legacy.LTXD'
    reference = dict(name='Leadin-Marker.jpg', kind='video', role='scene', notes='Opening shot',
                     archiveMedia='references/video.jpg', archiveImage='references/preview.jpg')
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('project.json', json.dumps(dict(frames=[], minimaxH3=dict(referenceImages=[None, reference]))))
        archive.writestr(reference['archiveMedia'], video)
        archive.writestr(reference['archiveImage'], preview.getvalue())
    with patch.object(project_archive, 'APP_CACHE', tmp_path / 'cache'):
        refs = project_archive.read_project(source)['minimaxH3']['referenceImages']
    with patch.object(ui, 'WorkspaceStore', return_value=WorkspaceStore(tmp_path / 'workspaces')), patch.object(ui.MainWindow, 'restore_startup_workspace'):
        window = ui.MainWindow()
    try:
        window.download_directory = str(tmp_path / 'exports')
        window.set_project_type('minimax_references')
        window.segments = [Segment('Still', '', '', 'image')]
        window.set_minimax_reference_image(1, refs[1])
        target = window.minimax_panel.reference_targets[1]
        assert target.filename.text() == 'Leadin-Marker.mp4'
        assert target.title.text() == 'REFERENCE 2 · Video1'
        sent = ai._minimax_reference_inputs(window.segments, 'gemini', window.active_reference_images())[-1]
        assert sent['label'] == 'Video1'
        assert sent['name'] == 'Leadin-Marker.mp4'
        assert sent['video'].startswith('data:video/mp4;base64,')
        assert base64.b64decode(sent['video'].split(',', 1)[1]) == video
        target.export_action.trigger()
        deadline = time.monotonic() + 10
        while window._pending_disk_jobs and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert window._pending_disk_jobs == 0
        assert (tmp_path / 'exports' / 'Leadin-Marker.mp4').read_bytes() == video
        assert window.download_tray.history[0]['path'].endswith('/Leadin-Marker.mp4')
    finally:
        window._close_saves_queued = True
        window.project_dirty = False
        window.close()
