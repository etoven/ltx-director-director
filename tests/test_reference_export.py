import time
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QColor
from ltx_prompt_director import ui
from ltx_prompt_director.workspaces import WorkspaceStore


def test_reference_export_preserves_resolution_uses_actual_format_and_records_history(tmp_path):
    app = QApplication.instance() or QApplication([])
    with patch.object(ui, 'WorkspaceStore', return_value=WorkspaceStore(tmp_path / 'workspaces')), patch.object(ui.MainWindow, 'restore_startup_workspace'):
        window = ui.MainWindow()
    try:
        window.download_directory = str(tmp_path / 'exports')
        window.set_project_type('minimax_references')
        first, second = window.minimax_panel.reference_targets
        assert not first.export_button.isEnabled()
        assert not second.export_button.isEnabled()
        image = QImage(1024, 768, QImage.Format.Format_RGB32)
        image.fill(QColor('#336699'))
        first.load_image(image, 'Reference.jpg')
        assert first.export_button.isEnabled()
        first.export_button.click()
        first.export_button.click()
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
        assert not first.export_button.isEnabled()
    finally:
        window._close_saves_queued = True
        window.project_dirty = False
        window.close()
