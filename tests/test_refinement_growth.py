import os
import unittest
from unittest.mock import patch
import time
import requests

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QTextCursor

from ltx_prompt_director import ai, ui
from ltx_prompt_director.inline_cues import NOTE_TAG, PromptTextEdit
from ltx_prompt_director.models import Segment
from ltx_prompt_director.prompt_tags import render_prompt_notes


class RefinementGrowthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_ai_can_expand_selected_segment_past_requested_total(self):
        segments = [Segment('One', '', '', kind='text', duration=2, prompt='Walk'),
                    Segment('Two', '', '', kind='text', duration=3, prompt='Turn')]
        with patch.object(ai, '_refinement_images', return_value=[]), patch.object(
            ai, '_provider_raw', return_value='{"duration": 8.5}'
        ) as provider:
            self.assertEqual(ai.refine_timing(segments, 0, 'gemini', 'model', 'key', '', 5), {'duration': 8.5})
            self.assertIn('planning preference', provider.call_args.args[4])
            self.assertNotIn('Return exactly', provider.call_args.args[4])

    def test_longer_refinement_keeps_card_scale_and_updates_controls(self):
        with patch.object(ui.MainWindow, 'restore_startup_workspace'):
            window = ui.MainWindow()
        self.addCleanup(lambda: (setattr(window, '_close_saves_queued', True), window.close()))
        window.segments = [Segment('One', '', '', kind='text', duration=2, prompt='Walk'),
                           Segment('Two', '', '', kind='text', duration=3, prompt='Turn')]
        window.refresh_timeline(0)
        window.show()
        self.app.processEvents()
        window.autofit_timeline()
        old_scale = window.pixels_per_second
        window.refinement_segment_id = window.segments[0].id
        window.refine_timing_finished({'duration': 8.5})
        self.assertEqual([segment.duration for segment in window.segments], [8.5, 3])
        self.assertFalse(window.timeline_fit_mode)
        self.assertAlmostEqual(window.pixels_per_second, old_scale, delta=1)
        self.assertEqual(window.timeline.itemWidget(window.timeline.item(0)).resize_handle.duration, 8.5)
        self.assertEqual(window.duration_spin.value(), 8.5)
        window.duration_animation.setCurrentTime(950)
        self.assertGreater(window.timeline.item(0).sizeHint().width(), window.timeline.item(1).sizeHint().width())

    def test_bad_refinement_response_retries_without_mutating_prompt(self):
        segments = [Segment('One', '', '', kind='text', duration=2, prompt='Keep this')]
        attempts = iter(['{"duration": 0}', '{"duration": 4.5}'])
        with patch.object(ai, '_refinement_images', return_value=[]), patch.object(
            ai, '_provider_raw', side_effect=lambda *args: next(attempts)
        ) as provider:
            worker = ui.MagicWorker(ai.refine_timing, (segments, 0, 'gemini', 'model', 'key', '', 2), 1, 0)
            completed, errors = [], []
            worker.signals.finished.connect(completed.append)
            worker.signals.failed.connect(errors.append)
            worker.run()
            self.assertEqual(provider.call_count, 2)
            self.assertEqual(completed, [{'duration': 4.5}])
            self.assertEqual(errors, [])
            self.assertEqual((segments[0].prompt, segments[0].duration), ('Keep this', 2))

    def test_http_failure_restores_refinement_controls_on_gui_thread(self):
        with patch.object(ui.MainWindow, 'restore_startup_workspace'):
            window = ui.MainWindow()
        self.addCleanup(lambda: (setattr(window, '_close_saves_queued', True), window.close()))
        window.segments = [Segment('One', '', '', kind='text', duration=2, prompt='Keep this')]
        window.refresh_timeline(0)
        window.settings.setValue('api_retries', 0)
        response = requests.Response()
        response.status_code = 400
        response.url = 'https://example.invalid'
        failure = requests.HTTPError('400 Bad Request', response=response)
        window.start_ai_worker(lambda: (_ for _ in ()).throw(failure), (), 'Refining…', window.refine_timing_finished)
        self.assertTrue(window.ai_busy)
        with patch.object(ui.QMessageBox, 'critical'):
            deadline = time.monotonic() + 3
            while window.ai_busy and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
        self.assertFalse(window.ai_busy)
        self.assertTrue(window.refine_timing_button.isEnabled())
        self.assertEqual((window.segments[0].prompt, window.segments[0].duration), ('Keep this', 2))

    def test_global_note_uses_full_width_block_body_local_note_stays_compact(self):
        editor = PromptTextEdit()
        original = '/refine-global Preserve continuity\n/refine Soften this transition'
        editor.setPlainText(original)
        render_prompt_notes(editor)
        tables = {}
        block = editor.document().firstBlock()
        while block.isValid():
            cursor = QTextCursor(editor.document())
            cursor.setPosition(block.position())
            table = cursor.currentTable()
            if table and table.format().property(NOTE_TAG):
                tables[table.format().property(NOTE_TAG)] = table
            block = block.next()
        self.assertEqual(tables['/refine-global'].rows(), 2)
        self.assertEqual(tables['/refine-global'].columns(), 1)
        self.assertEqual(tables['/refine'].rows(), 1)
        self.assertEqual(tables['/refine'].columns(), 2)
        self.assertEqual(editor.toPlainText(), original)
        editor.close()


if __name__ == '__main__':
    unittest.main()
