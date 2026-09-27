import base64
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PIL import Image
from PySide6.QtWidgets import QApplication
from ltx_prompt_director import cache_maintenance, ui
from ltx_prompt_director.models import Segment
from ltx_prompt_director.project_archive import read_project


class PropertiesCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_properties_dock_and_context_metadata_update_immediately_without_copying_video(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(ui, 'project_library_path', return_value=Path(folder)), patch.object(ui.MainWindow, 'restore_startup_workspace'):
            window = ui.MainWindow()
            self.addCleanup(window.close)
            window.segments = [Segment('Beat', '', '', kind='text', prompt='Motion')]
            window.save_library_project(automatic=True)
            project = Path(folder) / f'{window.current_project_id}.LTXD'
            size_before = project.stat().st_size
            previous = window.settings.value('project_other_tags', '')
            self.addCleanup(lambda: window.settings.setValue('project_other_tags', previous))
            window.settings.setValue('project_other_tags', json.dumps([{'name':'Review', 'color':'#447799'}]))
            panel = window.project_properties_panel
            window.sync_project_properties()
            self.assertIs(panel.window(), window)
            self.assertEqual(panel.project_id, window.current_project_id)
            panel.status.setCurrentIndex(panel.status.findData('Done'))
            panel.tag_boxes[0].setChecked(True)
            panel.new_tag.setText('Needs Foley')
            panel.create_tag()
            panel.note_text.setText('Check the transition')
            panel.commit_note()
            self.assertEqual(len(panel.notes), 1)
            panel.set_note_checked(panel.notes[0]['id'], True)
            meta = window.current_library_metadata()
            self.assertEqual(meta['status'], 'Done')
            self.assertEqual(meta['tags'], ['Review', 'Needs Foley'])
            self.assertIn('Needs Foley', [tag['name'] for tag in window.project_other_tags()])
            self.assertTrue(meta['notes'][0]['checked'])
            self.assertTrue(zipfile.is_zipfile(project))
            self.assertLess(project.stat().st_size - size_before, 25000)
            self.assertEqual(read_project(project)['library']['notes'][0]['text'], 'Check the transition')
            # The context-menu/dialog metadata path updates the same dock immediately.
            meta['name'] = 'Renamed in dialog'
            window.persist_library_metadata(meta)
            self.assertEqual(panel.name.text(), 'Renamed in dialog')

    def test_stale_cache_dialog_reports_progress_and_writes_marker(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            image = io.BytesIO()
            Image.new('RGB', (400, 400), '#6688aa').save(image, 'PNG')
            value = 'data:image/png;base64,' + base64.b64encode(image.getvalue()).decode()
            metadata = root / 'project.meta.json'
            metadata.write_text(json.dumps({'thumbnailData': value}), encoding='utf-8')
            with patch.object(cache_maintenance, 'APP_CACHE', root), patch.object(cache_maintenance, 'MARKER', root / 'preview-cache.json'):
                self.assertTrue(cache_maintenance.needs_refresh())
                dialog = ui.PreviewCacheDialog([metadata])
                dialog.start()
                for _ in range(300):
                    self.app.processEvents()
                    if not dialog.isVisible():
                        break
                self.assertFalse(dialog.isVisible())
                self.assertEqual(dialog.progress.value(), 1)
                self.assertFalse(cache_maintenance.needs_refresh())
                self.assertTrue(cache_maintenance.cover_path(value).is_file())


if __name__ == '__main__':
    unittest.main()
