import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtWidgets import QApplication, QTextEdit
from PySide6.QtGui import QColor, QKeyEvent, QTextCursor

from ltx_prompt_director import ui
from ltx_prompt_director.inline_cues import PromptTextEdit, cue_cells, insert_cue_cells
from ltx_prompt_director.models import Segment
from ltx_prompt_director.spellcheck import install_spellcheck, spellcheck_menu, system_dictionary
from ltx_prompt_director.timed_action import compose_actions


class InlineCuesCloseSpellcheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self):
        with patch.object(ui.MainWindow, 'restore_startup_workspace'):
            window = ui.MainWindow()
        self.addCleanup(lambda: self.close_fixture(window))
        return window

    @staticmethod
    def close_fixture(window):
        try:
            window._close_saves_queued = True
            window.close()
        except RuntimeError:
            pass

    def test_inline_cells_keep_ids_when_surrounding_prose_changes(self):
        window = self.make_window()
        window.segments = [Segment('A', '', '', kind='text', prompt='First', duration=2),
                           Segment('B', '', '', kind='text', prompt='Second', duration=3)]
        window.refresh_timeline(0)
        window.set_project_type('minimax_frames')
        window.segment_prompt.setPlainText(compose_actions('[SCENE]\nKitchen.\n\n[SOUND]\nRain.', window.segments, ['Walk', 'Turn']))
        window.sync_timed_actions()
        self.assertEqual(cue_cells(window.segment_prompt), {window.segments[0].id: 'Walk', window.segments[1].id: 'Turn'})
        cursor = window.segment_prompt.textCursor()
        cursor.setPosition(0)
        cursor.insertText('Prelude.\n')
        window.sync_timed_actions()
        self.assertEqual(cue_cells(window.segment_prompt)[window.segments[1].id], 'Turn')
        window.change_duration(window.segments[0].id, 4)
        self.assertIn('00:00:04:00 - 00:00:07:00: Turn', window.segment_prompt.toPlainText())
        self.assertEqual(cue_cells(window.segment_prompt)[window.segments[1].id], 'Turn')

    def test_wrapped_cue_background_covers_all_visual_lines(self):
        editor = QTextEdit()
        editor.setStyleSheet('QTextEdit{background:#202527;color:#e3e3e3;font:14px Monospace}')
        editor.resize(800, 280)
        editor.show()
        segments = [Segment('A', '', '', kind='text', duration=2)]
        prompt = '[TIMED ACTION]\n00:00:00:00 - 00:00:02:00: ' + ('This is a long action with many words. ' * 8)
        self.assertTrue(insert_cue_cells(editor, prompt, segments))
        self.app.processEvents()
        image = editor.grab().toImage()
        tint = QColor('#28343a')
        for y in (26, 42, 59, 78):
            tinted_pixels = sum(image.pixelColor(x, y) == tint for x in range(250, 730))
            self.assertGreater(tinted_pixels, 180)
        self.assertEqual(image.pixelColor(745, 160), QColor('#202527'))
        editor.close()

    def test_extra_prompt_cues_all_become_cells_without_falsely_reassigning_ids(self):
        editor = QTextEdit()
        segments = [Segment('A', '', '', kind='text', duration=2), Segment('B', '', '', kind='text', duration=3)]
        prompt = ('[TIMED ACTION]\n00:00:00:00 - 00:00:02:00: First\n\n'
                  '00:00:02:00 - 00:00:05:00: Second\n\n'
                  '00:00:05:00 - 00:00:08:00: Additional')
        self.assertTrue(insert_cue_cells(editor, prompt, segments))
        self.assertEqual(cue_cells(editor), {segments[0].id: 'First', segments[1].id: 'Second'})
        tables = set()
        block = editor.document().firstBlock()
        while block.isValid():
            cursor = QTextCursor(editor.document())
            cursor.setPosition(block.position())
            if cursor.currentTable():
                tables.add(cursor.currentTable().firstPosition())
            block = block.next()
        self.assertEqual(len(tables), 3)

    def test_extra_cue_is_preserved_when_timeline_length_changes(self):
        window = self.make_window()
        window.segments = [Segment('A', '', '', kind='text', duration=2), Segment('B', '', '', kind='text', duration=3)]
        window.refresh_timeline(0)
        window.set_project_type('minimax_frames')
        window.segment_prompt.setPlainText(
            '[TIMED ACTION]\n00:00:00:00 - 00:00:02:00: First\n\n'
            '00:00:02:00 - 00:00:05:00: Second\n\n'
            '00:00:05:00 - 00:00:08:00: Extra')
        window.sync_timed_actions()
        window.change_duration(window.segments[0].id, 4)
        self.assertIn('Extra', window.segment_prompt.toPlainText())
        self.assertIn('counts differ', window.minimax_panel.message_banner.text())

    def test_timecode_and_inline_cell_cannot_be_deleted(self):
        editor = PromptTextEdit()
        editor.inline_cue_mode = True
        segment = Segment('A', '', '', kind='text', duration=2)
        prompt = '[TIMED ACTION]\n00:00:00:00 - 00:00:02:00: Walk forward'
        self.assertTrue(insert_cue_cells(editor, prompt, [segment]))
        block = editor.document().firstBlock()
        while block.isValid() and '00:00:00:00' not in block.text():
            block = block.next()
        cursor = QTextCursor(editor.document())
        cursor.setPosition(block.position() + 3)
        editor.setTextCursor(cursor)
        editor.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_X, Qt.KeyboardModifier.NoModifier, 'x'))
        self.assertEqual(cue_cells(editor)[segment.id], 'Walk forward')
        self.assertIn('00:00:00:00 - 00:00:02:00', editor.toPlainText())
        cursor = editor.textCursor()
        table = cursor.currentTable()
        cursor.setPosition(table.firstPosition())
        cursor.setPosition(table.lastPosition(), QTextCursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
        editor.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(cue_cells(editor)[segment.id], 'Walk forward')
        self.assertIn('00:00:00:00 - 00:00:02:00', editor.toPlainText())
        editor.close()

    def test_saved_cue_ids_restore_action_to_original_segment(self):
        window = self.make_window()
        window.segments = [Segment('A', '', '', kind='text', prompt='Start', duration=2),
                           Segment('B', '', '', kind='text', prompt='End', duration=3)]
        window.refresh_timeline(0)
        window.set_project_type('minimax_frames')
        window.segment_prompt.setPlainText(compose_actions('[SCENE]\nStudio.', window.segments, ['Walk', 'Turn']))
        window.sync_timed_actions()
        expected = cue_cells(window.segment_prompt)
        payload = window.project_payload(include_media=False)
        self.assertEqual(payload['minimaxH3']['cueActions'], expected)
        restored = self.make_window()
        restored.load_project_payload(payload)
        self.assertEqual(cue_cells(restored.segment_prompt), expected)

    def test_spelling_suggestions_are_top_level_and_replace_word(self):
        if system_dictionary() is None:
            self.skipTest('No installed spelling dictionary')
        editor = QTextEdit()
        editor.resize(400, 150)
        editor.show()
        editor.setPlainText('spelingg correctly')
        checker = install_spellcheck(editor)
        self.app.processEvents()
        menu = spellcheck_menu(editor, QPoint(20, 10), checker)
        actions = menu.actions()
        self.assertTrue(actions[0].text() and not actions[0].isSeparator())
        self.assertTrue(any('Ignore' in action.text() for action in actions[:8]))
        self.assertTrue(any(action.isSeparator() for action in actions[:10]))
        self.assertTrue(any('Undo' in action.text() for action in actions))
        actions[0].trigger()
        self.assertNotIn('spelingg', editor.toPlainText())
        menu.deleteLater()
        editor.close()

    def test_switching_projects_keeps_edits_in_memory_until_close(self):
        window = self.make_window()
        with tempfile.TemporaryDirectory() as directory, patch.object(ui, 'project_library_path', return_value=Path(directory)):
            window.segments = [Segment('First', '', '', kind='text', prompt='One')]
            window.mark_dirty()
            window.new_project()
            window.segments = [Segment('Second', '', '', kind='text', prompt='Two')]
            window.mark_dirty()
            self.assertEqual(list(Path(directory).glob('*.LTXD')), [])
            window.show()
            self.app.processEvents()
            window.close()
            deadline = time.monotonic() + 5
            while window._pending_disk_jobs and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
            self.assertEqual(window._pending_disk_jobs, 0)
            self.assertEqual(len(list(Path(directory).glob('*.LTXD'))), 2)

    def test_close_hides_main_window_and_reports_save_progress(self):
        window = self.make_window()
        window.segments = [Segment('Beat', '', '', kind='text', prompt='Motion')]
        window.mark_dirty()
        started, release = threading.Event(), threading.Event()
        with tempfile.TemporaryDirectory() as directory, patch.object(ui, 'project_library_path', return_value=Path(directory)):
            real_write = ui._write_library_archive
            def delayed(*args):
                started.set()
                release.wait(4)
                return real_write(*args)
            with patch.object(ui, '_write_library_archive', side_effect=delayed):
                window.show()
                self.app.processEvents()
                window.close()
                self.assertTrue(started.wait(1))
                self.assertFalse(window.isVisible())
                dialog = window._closing_progress
                self.assertIsNotNone(dialog)
                self.assertTrue(dialog.isVisible())
                self.assertIn('project saves', dialog.windowTitle())
                release.set()
                deadline = time.monotonic() + 5
                while window._pending_disk_jobs and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.01)
                self.assertEqual(window._pending_disk_jobs, 0)
                self.app.processEvents()
                self.assertTrue(list(Path(directory).glob('*.LTXD')))
