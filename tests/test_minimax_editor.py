import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QEventLoop, QThread, QTimer, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from ltx_prompt_director import ai
from ltx_prompt_director.models import Segment
from ltx_prompt_director.ui import MainWindow
from ltx_prompt_director.inline_cues import NOTE_TAG
from ltx_prompt_director.prompt_tags import render_prompt_notes


class UnifiedEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self, project_type='ltx'):
        with patch.object(MainWindow, 'restore_startup_workspace'):
            window = MainWindow()
        window.segments = [Segment('First', '', '', kind='text', prompt='First action', duration=2),
                           Segment('Second', '', '', kind='text', prompt='Second action', duration=3)]
        window.refresh_timeline(0)
        window.set_project_type(project_type)
        self.addCleanup(window.close)
        return window

    def test_same_editor_follows_ltx_selection_but_keeps_minimax_production_prompt(self):
        w = self.make_window()
        editor = w.segment_prompt
        self.assertEqual(editor.toPlainText(), 'First action')
        editor.setPlainText('Edited first action')
        w.timeline.setCurrentRow(1)
        self.assertEqual(editor.toPlainText(), 'Second action')
        w.set_project_type('minimax_frames')
        self.assertIs(w.minimax_panel.editor, editor)
        self.assertNotIsInstance(w.minimax_panel, QDialog)
        self.assertFalse(w.minimax_panel.isWindow())
        editor.setPlainText('The full production prompt')
        w.timeline.setCurrentRow(0)
        w.reload_clicked_segment(w.timeline.item(0))
        self.assertEqual(editor.toPlainText(), 'The full production prompt')
        self.assertEqual(w.segments[0].prompt, 'Edited first action')
        w.set_project_type('ltx')
        self.assertEqual(editor.toPlainText(), 'Edited first action')
        w.set_project_type('minimax_frames')
        self.assertEqual(editor.toPlainText(), 'The full production prompt')

    def test_global_prompt_uses_same_editor_and_cannot_overwrite_a_segment(self):
        w = self.make_window()
        w.prompt_scope.setCurrentIndex(1)
        w.segment_prompt.setPlainText('Global continuity')
        w.timeline.setCurrentRow(1)
        self.assertEqual(w.segment_prompt.toPlainText(), 'Global continuity')
        self.assertEqual(w.global_prompt.toPlainText(), 'Global continuity')
        self.assertEqual(w.segments[0].prompt, 'First action')
        self.assertFalse(w.refine_prompt_button.isEnabled())
        w.prompt_scope.setCurrentIndex(0)
        self.assertEqual(w.segment_prompt.toPlainText(), 'Second action')
        self.assertTrue(w.global_prompt.isHidden())

    def test_inline_note_survives_ltx_scope_and_minimax_mode_switch(self):
        w = self.make_window()
        w.segment_prompt.setPlainText('First action\n/refine Slow the movement')
        render_prompt_notes(w.segment_prompt)
        self.assertIn('/refine Slow the movement', w.segments[0].prompt)
        w.prompt_scope.setCurrentIndex(1)
        w.segment_prompt.setPlainText('/refine-global Keep the camera still')
        render_prompt_notes(w.segment_prompt)
        w.prompt_scope.setCurrentIndex(0)
        self.assertIn('/refine Slow the movement', w.segment_prompt.toPlainText())
        w.prompt_scope.setCurrentIndex(1)
        self.assertIn('/refine-global Keep the camera still', w.segment_prompt.toPlainText())
        w.set_project_type('minimax_frames')
        w.segment_prompt.setPlainText('/refine-global Keep continuity\n\n[SCENE]\nOne shot.')
        render_prompt_notes(w.segment_prompt)
        w.set_project_type('ltx')
        w.set_project_type('minimax_frames')
        self.assertIn('/refine-global Keep continuity', w.segment_prompt.toPlainText())
        block = w.segment_prompt.document().firstBlock()
        tags = []
        while block.isValid():
            cursor = QTextCursor(w.segment_prompt.document())
            cursor.setPosition(block.position())
            table = cursor.currentTable()
            if table and table.format().property(NOTE_TAG):
                tags.append(table.format().property(NOTE_TAG))
            block = block.next()
        self.assertIn('/refine-global', tags)

    def test_switches_only_show_relevant_controls_and_never_generate(self):
        w = self.make_window()
        with patch.object(w, 'start_ai_worker') as worker:
            w.project_type_combo.setCurrentIndex(2)
            self.assertEqual(w.project_type, 'minimax_references')
            self.assertTrue(w.hdr.isHidden())
            self.assertTrue(w.refine_timing_button.isHidden())
            self.assertTrue(w.requested_length.isHidden())
            self.assertTrue(w.prompt_scope.isHidden())
            self.assertTrue(w.copy_image_prompt.isHidden())
            self.assertFalse(w.references_button.isHidden())
            self.assertFalse(w.minimax_panel.reference_dock.isHidden())
            w.project_type_combo.setCurrentIndex(1)
            self.assertFalse(w.minimax_panel.reference_dock.isHidden())
            self.assertFalse(w.references_button.isHidden())
            self.assertEqual(w.minimax_panel.workflow_label.text(), 'Conditioning frames')
            w.project_type_combo.setCurrentIndex(0)
            self.assertTrue(w.minimax_panel.reference_dock.isHidden())
            self.assertTrue(w.references_button.isHidden())
            self.assertFalse(w.hdr.isHidden())
            self.assertFalse(w.refine_timing_button.isHidden())
            self.assertFalse(w.prompt_scope.isHidden())
            self.assertTrue(w.minimax_panel.isHidden())
            worker.assert_not_called()

    def test_one_generate_button_routes_all_three_project_types(self):
        w = self.make_window()
        for kind, operation in [('ltx', ai.build_prompts), ('minimax_frames', ai.build_minimax_h3_prompt),
                                ('minimax_references', ai.build_minimax_h3_reference_prompt)]:
            w.set_project_type(kind)
            with patch.object(w, 'ai_credentials', return_value=(w.settings.value('provider', 'gemini'), w.settings.value('gemini_model', ai.GEMINI_MODELS[0]), 'unused')), patch.object(w, 'start_ai_worker') as worker:
                w.magic_button.click()
            self.assertIs(worker.call_args.args[0], operation)
            w.minimax_panel.set_busy(False)

    def test_prompt_and_instructions_remain_editable_while_busy_and_stale_result_is_rejected(self):
        w = self.make_window('minimax_frames')
        w.segment_prompt.setPlainText('Original prompt')
        w.minimax_operation_signature = w.current_minimax_cache_key()
        w.minimax_operation_editor_snapshot = ('Original prompt', '')
        w.set_ai_controls_enabled(False)
        self.assertFalse(w.segment_prompt.isReadOnly())
        self.assertFalse(hasattr(w.minimax_panel, 'notes_toggle'))
        self.assertFalse(w.refine_prompt_button.isEnabled())
        w.segment_prompt.setPlainText('New pasted prompt')
        w.segment_prompt.setPlainText('/refine-global Keep the new ending\nNew pasted prompt')
        self.assertFalse(w.refine_prompt_button.isEnabled())
        w.minimax_h3_finished('Old provider result')
        self.assertEqual(w.segment_prompt.toPlainText(), '/refine-global Keep the new ending\nNew pasted prompt')
        self.assertIn('not applied', w.minimax_panel.message_banner.text())

    def test_refine_uses_edited_prompt_and_notes_for_selected_mode(self):
        for kind, expected in [('minimax_frames', ai.refine_minimax_h3_prompt), ('minimax_references', ai.refine_minimax_h3_reference_prompt)]:
            w = self.make_window(kind)
            w.segment_prompt.setPlainText('/refine-global Preserve my ending\nUser-edited production prompt')
            with patch.object(w, 'ai_credentials', return_value=(w.settings.value('provider', 'gemini'), w.settings.value('gemini_model', ai.GEMINI_MODELS[0]), 'unused')), patch.object(w, 'start_ai_worker') as worker:
                w.refine_prompt_button.click()
            self.assertIs(worker.call_args.args[0], expected)
            self.assertIn('User-edited production prompt', worker.call_args.args[1][9])
            self.assertIn('/refine-global Preserve my ending', worker.call_args.args[1][9])
            self.assertEqual(w.minimax_operation_editor_snapshot, ('/refine-global Preserve my ending\nUser-edited production prompt', ''))
            w.minimax_h3_finished('Refined production prompt')
            self.assertEqual(w.segment_prompt.toPlainText(), 'Refined production prompt')
            self.assertFalse(hasattr(w.minimax_panel, 'instructions'))

    def test_both_minimax_drafts_and_type_survive_export_and_workspace_switch(self):
        w = self.make_window('minimax_frames')
        w.segment_prompt.setPlainText('/refine-global Frame notes\nFrame draft')
        w.set_project_type('minimax_references')
        w.segment_prompt.setPlainText('/refine-global Reference notes\nReference draft')
        payload = w.project_payload()
        state = w.capture_workspace_state()
        w.set_project_type('ltx')
        w.restore_workspace_state(state)
        self.assertEqual(w.project_type, 'minimax_references')
        self.assertEqual(w.segment_prompt.toPlainText(), '/refine-global Reference notes\nReference draft')
        restored = self.make_window()
        restored.load_project_payload(payload)
        self.assertEqual(restored.project_type, 'minimax_references')
        self.assertEqual(restored.segment_prompt.toPlainText(), '/refine-global Reference notes\nReference draft')
        restored.set_project_type('minimax_frames')
        self.assertEqual(restored.segment_prompt.toPlainText(), '/refine-global Frame notes\nFrame draft')
        restored.set_project_type('ltx')
        self.assertEqual(restored.segment_prompt.toPlainText(), 'First action')

    def test_legacy_project_migrates_without_losing_prompts(self):
        w = self.make_window()
        payload = w.project_payload()
        payload.pop('projectType')
        payload['projectVersion'] = 7
        payload['minimaxH3'] = {'prompt': 'Legacy production prompt', 'mode': 'references'}
        w.load_project_payload(payload)
        self.assertEqual(w.project_type, 'minimax_references')
        self.assertEqual(w.segment_prompt.toPlainText(), 'Legacy production prompt')
        payload.pop('minimaxH3')
        w.load_project_payload(payload)
        self.assertEqual(w.project_type, 'ltx')
        self.assertEqual(w.minimax_prompt_text, '')
        self.assertEqual(w.segment_prompt.toPlainText(), 'First action')

    def test_switch_away_and_back_rejects_old_worker_response(self):
        w = self.make_window('minimax_frames')
        w.segment_prompt.setPlainText('Keep me')
        w.minimax_operation_signature = w.current_minimax_cache_key()
        w.set_project_type('ltx')
        w.set_project_type('minimax_frames')
        w.minimax_h3_finished('Stale')
        self.assertEqual(w.segment_prompt.toPlainText(), 'Keep me')

    def test_actual_worker_callback_does_not_apply_after_project_switch(self):
        w = self.make_window('ltx')
        with patch.object(w.thread_pool, 'start') as start:
            w.start_ai_worker(ai.build_prompts, (), 'Testing', w.magic_finished)
        worker = start.call_args.args[0]
        w.set_project_type('minimax_frames')
        w.segment_prompt.setPlainText('Current production prompt')
        worker.signals.finished.emit({'segments': [{'prompt': 'Wrong project', 'duration': 7}]})
        self.assertEqual(w.segments[0].prompt, 'First action')
        self.assertEqual(w.segment_prompt.toPlainText(), 'Current production prompt')
        self.assertFalse(w.ai_busy)

    def test_background_completion_is_delivered_on_gui_thread(self):
        w = self.make_window('minimax_frames')
        loop = QEventLoop()
        threads = []
        def operation():
            return 'Background draft'
        def finished(result):
            threads.append(QThread.currentThread())
            w.segment_prompt.setPlainText(result)
            w.set_ai_controls_enabled(True)
            loop.quit()
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        deadline.start(2000)
        w.start_ai_worker(operation, (), 'Testing', finished, show_main_overlay=False)
        loop.exec()
        deadline.stop()
        self.assertEqual(threads, [self.app.thread()])
        self.assertEqual(w.segment_prompt.toPlainText(), 'Background draft')

    def test_save_on_main_window_close_preserves_manual_edits(self):
        w = self.make_window('minimax_frames')
        w.current_project_id = 'test-project'
        w.segment_prompt.setPlainText('Manual draft without generation')
        with patch.object(w, 'save_library_project') as save:
            w.close()
            save.assert_called_once_with(automatic=True)
            self.assertEqual(w.project_payload()['minimaxH3']['prompt'], 'Manual draft without generation')
        w.current_project_id = None

    def test_minimax_error_and_retry_stay_inline(self):
        w = self.make_window('minimax_references')
        w.ai_activity_title = 'MiniMax H3 Reference Generate'
        w.minimax_operation_kind = 'generate_references'
        with patch.object(QMessageBox, 'warning') as warning, patch.object(QMessageBox, 'critical') as critical:
            w.magic_failed('Provider temporarily overloaded')
        warning.assert_not_called()
        critical.assert_not_called()
        self.assertEqual(w.minimax_panel.message_banner.text(), 'Provider temporarily overloaded')
        self.assertFalse(w.minimax_panel.retry_button.isHidden())
        with patch.object(w, 'generate_minimax_prompt_references') as retry:
            w.minimax_panel.retry_button.click()
        retry.assert_called_once_with()

    def test_minimax_uses_timeline_duration_not_hidden_ltx_length(self):
        w = self.make_window('minimax_references')
        w.requested_length.setValue(99)
        self.assertNotIn('99', w.build_director_request())
        self.assertEqual(w.total_duration(), 5)


if __name__ == '__main__':
    unittest.main()
