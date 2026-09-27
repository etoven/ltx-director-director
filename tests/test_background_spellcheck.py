import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ltx_prompt_director import ui
from ltx_prompt_director.models import Segment
from ltx_prompt_director.spellcheck import install_spellcheck, system_dictionary


class BackgroundSpellcheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(predicate())

    def make_window(self):
        with patch.object(ui.MainWindow, 'restore_startup_workspace'):
            window = ui.MainWindow()
        self.addCleanup(window.close)
        return window

    def test_media_preparation_does_not_block_ui_and_uses_latest_project(self):
        window = self.make_window()
        started = threading.Event()
        release = threading.Event()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'frame.png'
            path.write_bytes(b'test')

            def prepare(_path):
                started.set()
                self.assertTrue(release.wait(3))
                return 'image', str(path), None, None

            with patch.object(ui, 'prepare_media', side_effect=prepare):
                ticks = []
                QTimer.singleShot(0, lambda: ticks.append('responsive'))
                window.add_media_paths([str(path)])
                self.app.processEvents()
                self.assertEqual(ticks, ['responsive'])
                self.assertEqual(len(window.segments), 0)
                self.assertTrue(started.wait(1))
                window.new_project()
                release.set()
                self.wait_for(lambda: window._pending_disk_jobs == 0)
                self.assertEqual(len(window.segments), 0)

    def test_spellcheck_is_inline_on_prompt_and_refinement_editors(self):
        window = self.make_window()
        if system_dictionary() is None:
            self.skipTest('No installed OS dictionary in this environment')
        editors = (window.intent, window.segment_prompt, window.global_prompt, window.minimax_panel.instructions)
        for editor in editors:
            checker = install_spellcheck(editor)
            self.assertIs(checker, editor._spell_highlighter)
            editor.setPlainText('spelingg correctly')
            self.assertEqual(editor.toPlainText(), 'spelingg correctly')
            self.assertTrue(checker.misspelled('spelingg'))
            self.assertFalse(checker.misspelled('correctly'))
            self.assertEqual(install_spellcheck(editor), checker)

    def test_selecting_current_project_cancels_inflight_project_read(self):
        window = self.make_window()
        window.current_project_id = 'first'
        window.current_project_name = 'First'
        started, release = threading.Event(), threading.Event()
        records = [{'id': 'first', 'name': 'First', 'projectPath': '/tmp/first.LTXD'},
                   {'id': 'second', 'name': 'Second', 'projectPath': '/tmp/second.LTXD'}]

        def slow_read(_path):
            started.set()
            self.assertTrue(release.wait(3))
            return {'app': 'ltx-director-director', 'frames': []}

        with patch.object(window, 'library_records', return_value=records), patch.object(ui, 'read_project', side_effect=slow_read):
            window.open_library_project(project_id='second')
            self.assertTrue(started.wait(1))
            window.open_library_project(project_id='first')
            release.set()
            self.wait_for(lambda: window._pending_disk_jobs == 0)
        self.assertEqual(window.current_project_id, 'first')

    def test_project_archive_save_runs_off_gui_thread_and_commits_on_completion(self):
        window = self.make_window()
        window.segments = [Segment('Beat', '', '', kind='text', prompt='Motion')]
        started, release = threading.Event(), threading.Event()
        real_save = ui._write_library_archive
        with tempfile.TemporaryDirectory() as directory, patch.object(ui, 'project_library_path', return_value=Path(directory)):
            def slow_save(*args):
                started.set()
                self.assertTrue(release.wait(3))
                real_save(*args)

            with patch.object(ui, '_write_library_archive', side_effect=slow_save):
                window.save_library_project(automatic=True)
                self.assertTrue(started.wait(1))
                ticks = []
                QTimer.singleShot(0, lambda: ticks.append(True))
                self.app.processEvents()
                self.assertEqual(ticks, [True])
                self.assertEqual(window._pending_disk_jobs, 1)
                release.set()
                self.wait_for(lambda: window._pending_disk_jobs == 0)


if __name__ == '__main__':
    unittest.main()
