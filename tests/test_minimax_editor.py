import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QEventLoop, QThread, QTimer, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from ltx_prompt_director import ai
from ltx_prompt_director.models import Segment
from ltx_prompt_director.ui import MainWindow, SettingsDialog
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

    def test_single_ai_button_follows_active_prompt_editor(self):
        from PySide6.QtWidgets import QPushButton
        w = self.make_window('ltx')
        self.assertEqual(len([x for x in w.findChildren(QPushButton, 'timelineButton') if x is w.magic_button]), 1)
        self.assertTrue(w.timeline_controls.indexOf(w.magic_button) >= 0)
        self.assertTrue(w.refine_prompt_button.isHidden())
        self.assertTrue(w.refine_timing_button.isHidden())
        self.assertIsNone(w.magic_button.menu())
        w.set_project_type('minimax_frames')
        self.assertTrue(w.timeline_controls.indexOf(w.magic_button) >= 0)
        self.assertEqual(len([x for x in w.findChildren(QPushButton, 'timelineButton') if x is w.magic_button]), 1)
        w.unified_prompt.setPlainText('Existing unified draft')
        w.refresh_primary_ai_action()
        self.assertEqual(w.primary_ai_command, 'refine')
        w.set_project_type('minimax_prompt_refinement')
        self.assertEqual(w.minimax_panel.header.indexOf(w.magic_button) >= 0, True)
        w.unified_prompt.clear()
        w.minimax_prompt_text = ''
        w.refresh_primary_ai_action()
        self.assertTrue(w.magic_button.isEnabled())  # unified workspace can generate
        w.unified_prompt.setPlainText('Text to refine')
        w.refresh_primary_ai_action()
        self.assertTrue(w.magic_button.isEnabled())
        w.set_project_type('ltx')
        self.assertTrue(w.timeline_controls.indexOf(w.magic_button) >= 0)

    def test_image_generation_model_setting_persists_and_selects_backend(self):
        import tempfile
        from PySide6.QtCore import QSettings
        from ltx_prompt_director.artwork_tools import IMAGE_MODEL_CHOICES
        w = self.make_window('minimax_frames')
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(directory + "/test-settings.ini", QSettings.Format.IniFormat)
            settings.setValue("provider", "chatgpt")
            w.settings = settings
            w.session_keys = {"gemini": "gemini-key", "openai": "openai-key"}
            dialog = SettingsDialog(settings, w)
            self.assertEqual(dialog.image_gen_model.currentData(), "auto")
            self.assertEqual(dialog.image_gen_model.count(), len(IMAGE_MODEL_CHOICES))
            selected = "openai:gpt-image-2.5-sunburst"
            dialog.image_gen_model.setCurrentIndex(dialog.image_gen_model.findData(selected))
            dialog.accept()
            self.assertEqual(settings.value("artwork/default_model"), selected)
            chosen = w._available_artwork_tools()
            self.assertEqual([(tool.provider, tool.model) for tool in chosen],
                             [("openai", "gpt-image-2.5-sunburst")])
            reopened = SettingsDialog(settings, w)
            self.assertEqual(reopened.image_gen_model.currentData(), selected)
            reopened.reject()
            w.session_keys = {"gemini": "gemini-key", "openai": ""}
            self.assertEqual(w._available_artwork_tools(), [])

    def test_minimax_director_intent_selector_shows_selected_segment(self):
        w = self.make_window('minimax_frames')
        w.intent.setPlainText("Original director's intent")
        w.unified_prompt.setPlainText("My unified production prompt")
        self.assertIs(w.director_intent_stack.currentWidget(), w.intent)
        self.assertEqual(w.prompt_scope.currentData(), "global")
        w.prompt_scope.setCurrentIndex(1)
        self.assertIs(w.director_intent_stack.currentWidget(), w.intent_segment_prompt)
        self.assertEqual(w.intent_segment_prompt.toPlainText(), "First action")
        self.assertTrue(w.segment_panel.isHidden())
        self.assertEqual(w.director_intent_label.text(), "SEGMENT PROMPT")
        w.intent_segment_prompt.setPlainText(
            "First action\\n/refine More deliberate transformation"
        )
        self.assertIn("/refine", w.segments[0].prompt)
        w.timeline.setCurrentRow(1)
        self.assertEqual(w.intent_segment_prompt.toPlainText(), "Second action")
        w.intent_segment_prompt.setPlainText("Second rewritten action")
        self.assertEqual(w.segments[1].prompt, "Second rewritten action")
        w.timeline.setCurrentRow(0)
        self.assertIn("/refine", w.intent_segment_prompt.toPlainText())
        w.prompt_scope.setCurrentIndex(0)
        self.assertIs(w.director_intent_stack.currentWidget(), w.intent)
        self.assertEqual(w.intent.toPlainText(), "Original director's intent")
        self.assertEqual(w.unified_prompt.toPlainText(), "My unified production prompt")
        self.assertEqual(w.combined_focus if hasattr(w, "combined_focus") else "all", "all")

    def test_workspace_picker_is_on_global_toolbar_without_a_wasted_row(self):
        w = self.make_window('minimax_frames')
        from PySide6.QtWidgets import QToolBar, QLabel
        bar = w.findChild(QToolBar, "mainToolbar")
        self.assertIsNotNone(bar)
        self.assertIs(w.project_type_combo.parent(), bar)
        self.assertIs(w.references_button.parent(), bar)
        self.assertEqual(w.references_button.text(), "Refs")
        self.assertTrue(w.workflow_label.isHidden())
        self.assertEqual(w.project_type_combo.accessibleName(), "Project type")
        self.assertFalse(any(
            x.text() == "PROJECT TYPE" for x in w.centralWidget().findChildren(QLabel)
        ))
        before = w.project_type
        w.project_type_combo.setCurrentIndex(w.project_type_combo.findData('ltx'))
        self.assertEqual(w.project_type, 'ltx')
        self.assertNotEqual(before, w.project_type)
        w.set_project_type('minimax_frames')
        self.assertFalse(w.references_button.isHidden())

    def test_full_width_segment_prompt_layout_and_compact_options(self):
        from PySide6.QtWidgets import QPushButton, QLabel
        w = self.make_window('minimax_frames')
        w.resize(1080, 850)
        w.show()
        self.app.processEvents()
        w.set_director_intent_expanded(True)
        self.app.processEvents()
        panel = w.director_panel
        stack = w.director_intent_stack
        label = w.director_intent_label
        selector = w.prompt_scope
        self.assertGreaterEqual(stack.geometry().top(), 0)
        self.assertGreaterEqual(stack.width(), panel.width() - 24)
        self.assertTrue(label.isHidden())  # Picker already identifies the editor.
        self.assertFalse(selector.isHidden())
        options = [w.options_row.itemAt(i).widget()
                   for i in range(w.options_row.count())]
        self.assertEqual(
            [x.text() for x in options if isinstance(x, QPushButton)],
            ["HDR", "SFX", "Dialog", "Less music"]
        )
        self.assertIn(w.requested_length, options)
        self.assertNotIn("PROMPT OPTIONS", [x.text() for x in options
                         if isinstance(x, QLabel)])
        self.assertTrue(all(x.height() <= 30 for x in (w.hdr, w.sfx,
                                                       w.spoken_dialog, w.reduce_music)))
        self.assertIs(w.options_row, w.sequence_controls)
        self.assertFalse(hasattr(w, "planning_row"))
        self.assertFalse(w.voice_options_button.isEnabled())
        self.assertFalse(w.voice_options_button.isHidden())
        self.assertLessEqual(w.prompt_scope.width(), 100)
        w.prompt_scope.setCurrentIndex(1)
        self.assertIs(stack.currentWidget(), w.intent_segment_prompt)
        w.intent_segment_prompt.setPlainText("A wide editor\\n/refine Keep scene continuity")
        self.assertIn("/refine", w.segments[0].prompt)
        self.assertEqual(w.intent_segment_prompt.lineWrapMode(),
                         w.intent_segment_prompt.LineWrapMode.WidgetWidth)
        w.spoken_dialog.setChecked(True)
        self.app.processEvents()
        self.assertTrue(w.voice_options_button.isEnabled())
        self.assertIsNotNone(w.voice_options_button.menu())
        w.speaker_language.setCurrentText("English")
        w.speaker_accent.setCurrentText("British RP")
        self.assertIn("English", w.build_director_request())
        self.assertIn("British RP", w.build_director_request())
        w.spoken_dialog.setChecked(False)
        self.assertFalse(w.voice_options_button.isEnabled())
        self.assertEqual(w.speaker_accent.currentText(), "British RP")
        self.assertIn("/refine", w.intent_segment_prompt.toPlainText())

    def test_single_row_sequence_options_and_unified_prompt_header(self):
        from PySide6.QtWidgets import QLabel, QPushButton
        w = self.make_window('minimax_frames')
        w.resize(1320, 830)
        w.show()
        self.app.processEvents()
        self.assertIs(w.options_row, w.sequence_controls)
        self.assertLessEqual(w.prompt_scope.width(), 100)
        self.assertEqual(w.conditioning_guide.height(), 38)
        self.assertEqual(w.start_button.text(), "Start")
        self.assertEqual(w.end_button.text(), "End")
        self.assertEqual(w.director_controls.count(), 2)
        self.assertIn("chars", w.unified_count.text())
        self.assertEqual(w.minimax_panel.header.indexOf(w.unified_count),
                         w.minimax_panel.header.indexOf(w.minimax_panel.prompt_label) + 1)
        self.assertEqual(w.minimax_panel.header.indexOf(w.minimax_panel.copy_button),
                         w.minimax_panel.header.count() - 1)
        self.assertNotIn("CONDITIONING SETUP", [
            label.text() for label in w.conditioning_content.findChildren(QLabel)
        ])
        self.assertNotIn("SEQUENCE", w.sequence_bar.text())
        self.assertEqual(w.length_label.text(), "Len")
        self.assertTrue(w.length_label.isHidden())
        self.assertNotIn("DURATION", [label.text() for label
                         in w.centralWidget().findChildren(QLabel) if label.isVisible()])
        self.assertIn("Auto-fit", w.timeline_fit_button.toolTip())
        self.assertEqual(w.timeline_fit_button.accessibleName(), "Fit timeline")

    def test_timeline_header_compacts_to_one_label_free_navigation_row(self):
        from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton
        w = self.make_window("minimax_frames")
        w.resize(1010, 780)
        w.show()
        self.app.processEvents()
        self.assertIsInstance(w.timeline_controls, QHBoxLayout)
        self.assertLessEqual(w.timeline_controls.geometry().height(), 40)
        widgets = [w.timeline_controls.itemAt(i).widget()
                   for i in range(w.timeline_controls.count())
                   if w.timeline_controls.itemAt(i).widget()]
        self.assertEqual(widgets,
                         [w.timeline_scale, w.timeline_fit_button,
                          w.output_size_control, w.magic_button])
        self.assertFalse(any(label.text() in {"TIMELINE", "OUTPUT", "SCALE"}
                             for label in w.timeline_shell.findChildren(QLabel)))
        self.assertIs(w.ai_timeline_status.parent(), w.statusBar())
        self.assertEqual(w.output_size_control.accessibleName(), "Output resolution")
        self.assertEqual(w.timeline_scale.accessibleName(), "Timeline zoom")
        self.assertIsInstance(w.timeline_fit_button, QToolButton)
        self.assertEqual(w.timeline_fit_button.text(), "↔")
        self.assertTrue(w.timeline_fit_button.isEnabled())
        w.ai_timeline_status.setText("✦ 2 edits")
        w.ai_timeline_status.show()
        self.app.processEvents()
        self.assertLessEqual(w.timeline_controls.geometry().height(), 40)
        self.assertTrue(w.ai_timeline_status.isVisibleTo(w.statusBar()))
        w.set_project_type("minimax_prompt_refinement")
        self.assertGreaterEqual(w.minimax_panel.header.indexOf(w.magic_button), 0)
        w.set_project_type("minimax_frames")
        self.assertGreaterEqual(w.timeline_controls.indexOf(w.magic_button), 0)

    def test_dense_timeline_gutter_add_rail_and_refinement_styles(self):
        from ltx_prompt_director.ui import AIWorkingMarquee
        w = self.make_window("minimax_frames")
        w.resize(1190, 830)
        w.show()
        self.app.processEvents()
        self.assertEqual(w.main_track_label.text(), "◉")
        self.assertLessEqual(w.main_track_label.width(), 50)
        self.assertEqual(w.main_track_label.width(), w.ruler_spacer.width())
        self.assertLessEqual(w.add_tile_wrap.width(), 55)
        self.assertEqual(w.add_tile.text(), "＋")
        self.assertEqual(w.add_text_tile.text(), "T＋")
        self.assertEqual(w.add_tile.accessibleName(), "Add media")
        self.assertEqual(w.add_text_tile.accessibleName(), "Add text")
        self.assertTrue(w.add_tile.isEnabled())
        self.assertTrue(w.add_text_tile.isEnabled())
        self.assertIsInstance(w.segment_ai_marquee, AIWorkingMarquee)
        self.assertIsInstance(w.minimax_panel.ai_marquee, AIWorkingMarquee)
        self.assertIs(w.segment_ai_marquee.paintEvent.__func__,
                      w.minimax_panel.ai_marquee.paintEvent.__func__)
        self.assertTrue(w.frame_number.isHidden())
        self.assertTrue(w.length_label.isHidden())
        self.assertLessEqual(w.requested_length.width(), 84)
        self.assertLessEqual(w.duration_spin.width(), 84)
        self.assertLess(w.sequence_bar.width(), 135)
        w.set_project_type("ltx")
        w.set_ai_controls_enabled(False)
        self.assertFalse(w.segment_ai_marquee.isHidden())
        w.set_ai_controls_enabled(True)
        self.assertTrue(w.segment_ai_marquee.isHidden())
        w.minimax_panel.set_cache_state("Refining…")
        self.assertTrue(w.minimax_panel.cache_state.property("active"))
        w.minimax_panel.set_cache_state("Saved")
        self.assertFalse(w.minimax_panel.cache_state.property("active"))

    def test_finished_unified_refinement_exposes_inline_paragraph_review(self):
        w = self.make_window('minimax_frames')
        w.resize(1150, 870)
        w.show()
        self.app.processEvents()
        before = "Opening original.\n\nUnchanged center.\n\nEnding original."
        after = "Opening revised.\n\nUnchanged center.\n\nEnding revised."
        w.minimax_prompt_text = before
        w.unified_prompt.setPlainText(before)
        w.inline_typing.start(w.unified_prompt, before, "unified")
        w.ai_request_workspace = (
            w.workspace_revision, w.current_project_id, w.project_type
        )
        w.active_live_panel = None
        def apply(text):
            w.unified_prompt.setPlainText(text)
            w.set_ai_controls_enabled(True)
        w.ai_finished_callback = apply
        w.ai_result_received(after)
        self.assertEqual(w.paragraph_review.pending_count, 2)
        self.assertEqual(len(w.paragraph_review.buttons), 2)
        self.assertIs(w.paragraph_review.approve_all.parent(), w.unified_prompt)
        w.paragraph_review.decide(0, False)
        self.assertEqual(w.paragraph_review.pending_count, 1)
        self.assertTrue(w.minimax_prompt_text.startswith("Opening original."))
        w.paragraph_review.approve_all.setChecked(True)
        self.assertIsNone(w.paragraph_review.editor)
        self.assertTrue(w.minimax_prompt_text.endswith("Ending revised."))
        self.assertEqual(
            w.minimax_prompt_text,
            "Opening original.\n\nUnchanged center.\n\nEnding revised."
        )

    def test_ltx_segment_editor_remains_authoritative(self):
        w = self.make_window('ltx')
        self.assertTrue(w.prompt_scope.isHidden())
        self.assertEqual(w.prompt_scope.currentData(), "segment")
        w.segment_prompt.setPlainText("Edited first action")
        self.assertEqual(w.segments[0].prompt, "Edited first action")
        w.set_project_type('minimax_frames')
        w.prompt_scope.setCurrentIndex(1)
        self.assertEqual(w.intent_segment_prompt.toPlainText(), "Edited first action")
        w.set_project_type('ltx')
        self.assertEqual(w.segment_prompt.toPlainText(), "Edited first action")

    def test_inline_note_survives_scope_switch_and_unified_workspace(self):
        w = self.make_window('minimax_frames')
        w.intent.setPlainText("/refine-global Keep the camera still")
        w.prompt_scope.setCurrentIndex(1)
        w.intent_segment_prompt.setPlainText("First action\\n/refine Slow the movement")
        render_prompt_notes(w.intent_segment_prompt)
        self.assertIn('/refine Slow the movement', w.segments[0].prompt)
        w.prompt_scope.setCurrentIndex(0)
        self.assertEqual(w.intent.toPlainText(), "/refine-global Keep the camera still")
        w.prompt_scope.setCurrentIndex(1)
        self.assertIn('/refine Slow the movement', w.intent_segment_prompt.toPlainText())
        w.unified_prompt.setPlainText('/refine-global Keep continuity\n\n[SCENE]\nOne shot.')
        render_prompt_notes(w.unified_prompt)
        w.set_project_type('ltx')
        w.set_project_type('minimax_frames')
        self.assertIn('/refine-global Keep continuity', w.unified_prompt.toPlainText())

    def test_switches_only_show_relevant_controls_and_never_generate(self):
        w = self.make_window()
        with patch.object(w, 'start_ai_worker') as worker:
            w.project_type_combo.setCurrentIndex(w.project_type_combo.findData('minimax_references'))
            self.assertEqual(w.project_type, 'minimax_references')
            self.assertFalse(w.hdr.isHidden())
            self.assertTrue(w.refine_timing_button.isHidden())
            self.assertIsNone(w.magic_button.menu())
            self.assertFalse(w.requested_length.isHidden())
            self.assertFalse(w.prompt_scope.isHidden())
            self.assertTrue(w.segment_panel.isHidden())
            self.assertFalse(w.unified_panel.isHidden())
            self.assertFalse(hasattr(w, "copy_image_prompt"))
            self.assertFalse(w.references_button.isHidden())
            w.show_reference_images()
            self.assertFalse(w.minimax_panel.reference_dock.isHidden())
            w.project_type_combo.setCurrentIndex(1)
            self.assertFalse(w.minimax_panel.reference_dock.isHidden())
            self.assertFalse(w.references_button.isHidden())
            self.assertEqual(w.minimax_panel.workflow_label.text(), 'Conditioning frames')
            w.project_type_combo.setCurrentIndex(0)
            self.assertTrue(w.minimax_panel.reference_dock.isHidden())
            self.assertTrue(w.references_button.isHidden())
            self.assertFalse(w.hdr.isHidden())
            self.assertTrue(w.refine_timing_button.isHidden())
            self.assertIsNone(w.magic_button.menu())
            self.assertTrue(w.prompt_scope.isHidden())
            self.assertTrue(w.unified_panel.isHidden())
            worker.assert_not_called()

    def test_one_button_handles_generate_and_refine_without_dropdown(self):
        w = self.make_window('minimax_frames')
        self.assertIsNone(w.magic_button.menu())
        w.minimax_prompt_text = ""
        w.unified_prompt.clear()
        w.refresh_primary_ai_action()
        self.assertIn("Generate", w.magic_button.text())
        with patch.object(w, '_generate_minimax_prompt') as generate:
            w.magic_button.click()
        generate.assert_called_once_with(w.minimax_prompt_mode)
        w.minimax_prompt_text = "Existing unified prompt"
        w.refresh_primary_ai_action()
        self.assertIn("Refine", w.magic_button.text())
        with patch.object(w, 'refine_minimax_prompt') as refine:
            w.magic_button.click()
        refine.assert_called_once_with()

    def test_slash_tags_anywhere_activate_refine_without_a_menu(self):
        w = self.make_window('minimax_frames')
        w.minimax_prompt_text = ""
        w.unified_prompt.clear()
        w.segments[0].prompt = "Keep composition\\n/refine Adjust segment timing"
        w.refresh_primary_ai_action()
        self.assertIn("Refine", w.magic_button.text())
        self.assertIsNone(w.magic_button.menu())
        with patch.object(w, '_generate_minimax_prompt') as generate:
            w.magic_button.click()
        generate.assert_called_once_with(w.minimax_prompt_mode)

    def test_unified_editor_and_timeline_controls_lock_during_ai(self):
        w = self.make_window('minimax_frames')
        w.unified_prompt.setPlainText('Keep this draft')
        w.set_ai_controls_enabled(False)
        self.assertTrue(w.unified_prompt.isReadOnly())
        self.assertTrue(w.intent.isReadOnly())
        self.assertFalse(w.prompt_scope.isEnabled())
        self.assertFalse(w.duration_spin.isEnabled())
        self.assertFalse(hasattr(w, "render_artwork_toggle"))
        self.assertFalse(w.timeline.dragEnabled())
        w.set_ai_controls_enabled(True)
        self.assertFalse(w.unified_prompt.isReadOnly())
        self.assertTrue(w.prompt_scope.isEnabled())

    def test_minimax_has_one_visible_prompt_editor_and_ai_toolbar(self):
        from PySide6.QtWidgets import QPushButton
        w = self.make_window('minimax_frames')
        self.assertTrue(w.segment_panel.isHidden())
        self.assertFalse(w.unified_panel.isHidden())
        self.assertTrue(w.timeline_controls.indexOf(w.magic_button) >= 0)
        self.assertTrue(w.director_intent_row.indexOf(w.prompt_scope) >= 0)
        self.assertEqual(len([x for x in w.findChildren(QPushButton, 'timelineButton') if x is w.magic_button]), 1)

    def test_timeline_plan_rewrite_typing_matches_prompt_and_rolls_back(self):
        w = self.make_window("minimax_frames")
        w.resize(1200, 800)
        w.show()
        self.app.processEvents()
        target = w.segments[0]
        original = target.prompt
        altered = original + "\nAdded transformation action and a close-up."
        card = w.timeline.itemWidget(w.timeline.item(0))
        self.assertFalse(card.rewrite_active)
        w.preview_ai_timeline_operations([
            {"kind": "rewrite", "id": target.id, "name": target.name,
             "prompt": altered, "imagePrompt": target.image_prompt}
        ])
        self.assertTrue(card.rewrite_active)
        self.assertEqual(card.rewrite_timer.interval(), w.inline_typing.timer.interval())
        self.assertEqual(target.prompt, original)
        self.assertGreater(len(card.rewrite_shown), 0)
        self.assertLess(len(card.rewrite_shown), len(altered))
        self.assertIn("#a7edbf", card.rewrite_overlay.text())
        # Read-only diff disappears on provider rejection or workspace reset.
        w._clear_ai_timeline_preview()
        self.assertFalse(card.rewrite_active)
        self.assertTrue(card.rewrite_overlay.isHidden())
        self.assertEqual(target.prompt, original)
        w.preview_ai_timeline_operations([
            {"kind": "move", "id": target.id, "name": target.name, "position": 1},
            {"kind": "rewrite", "id": target.id, "name": target.name,
             "prompt": altered, "imagePrompt": target.image_prompt}
        ])
        self.assertTrue(card.rewrite_active)
        w._clear_ai_timeline_preview()

    def test_combined_json_inserts_animated_artwork_placeholder_without_deleting_media(self):
        from unittest.mock import patch
        w = self.make_window('minimax_frames')
        w.session_keys['gemini'] = 'test-image-key'
        w.settings.setValue('artwork/cost_approved/gemini', True)
        old = list(w.segments)
        original = old[0]
        w.minimax_operation_kind = 'generate_frames'
        w.minimax_prompt_text = 'Before unified prompt'
        w.ai_segment_snapshot = old
        w.ai_plan_stream_active = False
        def record(s):
            return {'id': s.id, 'name': s.name, 'kind': s.kind, 'role': s.role,
                    'duration': s.duration, 'prompt': s.prompt,
                    'imagePrompt': s.image_prompt, 'artworkPrompt': ''}
        entries = [record(s) for s in old]
        entries.insert(1, {
            'id': 'new:1', 'name': 'Rendered insert', 'kind': 'text',
            'role': 'text', 'duration': 2.2, 'prompt': 'Smooth continuous motion',
            'imagePrompt': 'New frame composition',
            'artworkPrompt': 'High-quality cinematic 16:9 image',
        })
        with patch.object(w, 'start_disk_job') as disk:
            w.combined_refinement_finished({
                'prompt': 'Combined unified production brief',
                'segments': entries,
            })
        self.assertEqual(len(w.segments), len(old) + 1)
        self.assertEqual(w.segments[0].media_path, original.media_path)
        self.assertEqual(w.minimax_prompt_text, 'Combined unified production brief')
        generated = w.segments[1]
        self.assertEqual(generated.artwork_status, 'generating')
        self.assertTrue(generated.artwork_prompt)
        self.assertTrue(w._ai_tile_animation is not None)
        card = w.timeline.itemWidget(w.timeline.item(1))
        self.assertIn('Generating artwork', card.preview.text())
        disk.assert_called_once()

    def test_image_gen_includes_timeline_neighbors_and_role_labeled_project_artwork(self):
        from PIL import Image
        from tempfile import TemporaryDirectory
        import base64
        from io import BytesIO
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        w = self.make_window('minimax_frames')
        tmp = scratch.name
        source = Path(tmp) / "frame.png"
        neighbor = Path(tmp) / "next.png"
        Image.new("RGB", (16, 9)).save(source)
        Image.new("RGB", (16, 9)).save(neighbor)
        w.segments[0].kind = "image"
        w.segments[0].media_path = w.segments[0].preview_path = str(source)
        w.segments[1].kind = "image"
        w.segments[1].media_path = w.segments[1].preview_path = str(neighbor)
        data = BytesIO()
        Image.new("RGB", (10, 10)).save(data, "PNG")
        w.minimax_reference_images[0] = {
            "kind": "image", "name": "Character costume",
            "role": "wardrobe", "notes": "Keep the brown coat",
            "image": "data:image/png;base64," +
            base64.b64encode(data.getvalue()).decode("ascii")}
        w.refresh_timeline(0)
        refs = w._artwork_reference_inputs(w.segments[0].id)
        self.assertTrue(any("wardrobe" in r["label"] for r in refs))
        self.assertTrue(any(r.get("path") == str(neighbor) for r in refs))
        self.assertFalse(any(r.get("path") == str(source) for r in refs))
        w.session_keys["gemini"] = "fake"
        w.settings.setValue("artwork/cost_decision/gemini", "allow")
        snapshot = list(w.segments)
        w.ai_segment_snapshot = snapshot
        rows = [{"id": s.id, "name": s.name, "kind": s.kind,
                 "role": s.role, "duration": s.duration,
                 "prompt": s.prompt, "imagePrompt": s.image_prompt,
                 "artworkPrompt": ("Keep the coat" if i == 0 else "")}
                for i, s in enumerate(snapshot)]
        with patch.object(w, "start_disk_job") as worker:
            w.combined_refinement_finished({"prompt": "Edit existing", "segments": rows})
        request = worker.call_args.args[1][0][0]
        self.assertEqual(request["sourceImage"], str(source))
        self.assertEqual(len(request["referenceImages"]), 2)
        self.assertIn("wardrobe", request["referenceImages"][0]["label"])
        with patch.object(w, "save_library_project"):
            w.close()

    def test_provider_bad_request_keeps_failed_frame_and_shows_detailed_error(self):
        w = self.make_window("minimax_frames")
        w.session_keys["gemini"] = "image-key"
        tool = w._available_artwork_tools()[0]
        w.segments[0].artwork_prompt = "Make a reference frame"
        w.minimax_panel.clear_message()
        with patch.object(w, "start_disk_job") as worker:
            w._start_segment_artwork(w.segments[0].id, {
                "id": w.segments[0].id, "artworkPrompt": "Make a reference frame"
            }, tool)
        worker.assert_called_once()
        callback = worker.call_args.args[3]
        callback("Gemini image model: HTTP 400 INVALID_ARGUMENT — Invalid imageSize")
        self.assertEqual(w.segments[0].artwork_status, "failed")
        self.assertIn("INVALID_ARGUMENT", w.minimax_panel.message_banner.text())
        self.assertFalse(w.minimax_panel.message_panel.isHidden())

    def test_existing_image_edit_renders_with_original_reference_and_retries(self):
        from io import BytesIO
        from PIL import Image
        from tempfile import TemporaryDirectory
        w = self.make_window('minimax_frames')
        with TemporaryDirectory() as tmp:
            original = Path(tmp) / "original.png"
            Image.new("RGB", (16, 9)).save(original)
            existing = w.segments[0]
            existing.kind = "image"
            existing.role = "end"
            existing.media_path = existing.preview_path = str(original)
            w.refresh_timeline(0)
            w.session_keys['gemini'] = 'image-key'
            w.settings.setValue('artwork/cost_decision/gemini', 'allow')
            before = list(w.segments)
            w.ai_segment_snapshot = before
            w.minimax_operation_kind = "refine_frames"
            def data(s):
                return {"id":s.id, "name":s.name, "kind":s.kind, "role":s.role,
                        "duration":s.duration, "prompt":s.prompt,
                        "imagePrompt":s.image_prompt, "artworkPrompt":""}
            plan = [data(s) for s in before]
            plan[0]["artworkPrompt"] = "Change only the fur color"
            with patch.object(w, "start_disk_job") as job:
                w.combined_refinement_finished({
                    "prompt":"Original production prompt", "segments":plan
                })
            self.assertEqual(w.segments[0].kind, "image")
            self.assertEqual(w.segments[0].media_path, str(original))
            self.assertEqual(w.segments[0].role, "end")
            self.assertEqual(w.segments[0].artwork_status, "generating")
            request = job.call_args.args[1][0][0]
            self.assertEqual(request["sourceImage"], str(original))
            self.assertEqual(request["id"], existing.id)
            tile = w.timeline.itemWidget(w.timeline.item(0))
            self.assertIn("Generating artwork", tile.artwork_overlay.text())
            self.assertTrue(tile.artwork_overlay.isVisibleTo(tile.preview))
            # Complete without overwriting the user's source image file.
            edited = Path(tmp) / "edited.png"
            Image.new("RGB", (9, 16)).save(edited)
            ready = job.call_args.args[2]
            ready({existing.id: str(edited)})
            self.assertEqual(w.segments[0].media_path, str(edited))
            self.assertEqual(w.segments[0].role, "end")
            self.assertTrue(original.is_file())
            # Close while the temporary media still exists; project thumbnail
            # creation on window teardown needs to read the source frame.
            with patch.object(w, "save_library_project"):
                w.close()

    def test_explicit_refine_triggers_existing_frame_if_model_omits_artwork(self):
        from tempfile import TemporaryDirectory
        from PIL import Image
        w = self.make_window('minimax_frames')
        with TemporaryDirectory() as tmp:
            source = Path(tmp)/"frame.png"
            Image.new("RGB", (16, 9)).save(source)
            w.segments[0].kind = "image"
            w.segments[0].media_path = w.segments[0].preview_path = str(source)
            w.segments[0].prompt = "Keep composition\n/refine Edit this image to remove the fog"
            w.refresh_timeline(0)
            w.ai_segment_snapshot = list(w.segments)
            w.minimax_operation_kind = "refine_frames"
            w.session_keys["gemini"] = "key"
            w.settings.setValue("artwork/cost_decision/gemini", "allow")
            rows = [{"id":s.id,"name":s.name,"kind":s.kind,"role":s.role,
                     "duration":s.duration,"prompt":s.prompt,
                     "imagePrompt":s.image_prompt,"artworkPrompt":""}
                    for s in w.segments]
            with patch.object(w, "start_disk_job") as job:
                w.combined_refinement_finished({"prompt":"A draft","segments":rows})
            job.assert_called_once()
            self.assertEqual(w.segments[0].artwork_prompt,
                             "Edit this image to remove the fog")
            with patch.object(w, "save_library_project"):
                w.close()

    def test_unchanged_timeline_accepts_chatgpt_model_completion(self):
        w = self.make_window('minimax_frames')
        w.unified_prompt.setPlainText('Original draft')
        # The old completion code recomputed the signature using the Gemini
        # model even though the request was sent through ChatGPT.
        w.minimax_operation_provider_model = ('chatgpt', 'gpt-6')
        w.minimax_operation_signature = w.current_minimax_cache_key('chatgpt', 'gpt-6')
        w.minimax_operation_editor_snapshot = ('Original draft', '')
        w.minimax_operation_kind = 'generate_frames'
        w.minimax_h3_finished('Successful generated result')
        self.assertEqual(w.minimax_prompt_text, 'Successful generated result')
        self.assertIn('Successful generated result', w.unified_prompt.toPlainText())
        self.assertFalse(w.pending_minimax_result)

    def test_normal_completion_no_longer_rejects_local_editor_fingerprint(self):
        w = self.make_window('minimax_frames')
        w.unified_prompt.setPlainText('Original')
        w.minimax_operation_kind = 'generate_frames'
        w.minimax_operation_signature = 'old signature'
        w.minimax_h3_finished('Validated production prompt')
        self.assertEqual(w.minimax_prompt_text, 'Validated production prompt')
        self.assertIn('Validated production prompt', w.unified_prompt.toPlainText())

    def test_refine_uses_edited_prompt_and_notes_for_selected_mode(self):
        for kind in ('minimax_frames', 'minimax_references'):
            w = self.make_window(kind)
            w.unified_prompt.setPlainText('/refine-global Preserve my ending\nUser-edited production prompt')
            with patch.object(w, 'ai_credentials', return_value=(w.settings.value('provider', 'gemini'), w.settings.value('gemini_model', ai.GEMINI_MODELS[0]), 'unused')), patch.object(w, 'start_ai_worker') as worker:
                w.refine_minimax_prompt()
            self.assertEqual(worker.call_args.args[0].__name__, 'generate_combined')
            self.assertIn('User-edited production prompt', worker.call_args.args[1][9])
            self.assertIn('/refine-global Preserve my ending', worker.call_args.args[1][9])
            self.assertEqual(w.minimax_operation_editor_snapshot, ('/refine-global Preserve my ending\nUser-edited production prompt', ''))
            w.minimax_h3_finished('Refined production prompt')
            self.assertEqual(w.unified_prompt.toPlainText(), 'Refined production prompt')
            self.assertFalse(hasattr(w.minimax_panel, 'instructions'))


    def test_both_minimax_drafts_and_type_survive_export_and_workspace_switch(self):
        w = self.make_window('minimax_frames')
        w.unified_prompt.setPlainText('/refine-global Frame notes\nFrame draft')
        w.set_project_type('minimax_references')
        w.unified_prompt.setPlainText('/refine-global Reference notes\nReference draft')
        payload = w.project_payload()
        state = w.capture_workspace_state()
        w.set_project_type('ltx')
        w.restore_workspace_state(state)
        self.assertEqual(w.project_type, 'minimax_references')
        self.assertEqual(w.unified_prompt.toPlainText(), '/refine-global Reference notes\nReference draft')
        restored = self.make_window()
        restored.load_project_payload(payload)
        self.assertEqual(restored.project_type, 'minimax_references')
        self.assertEqual(restored.unified_prompt.toPlainText(), '/refine-global Reference notes\nReference draft')
        restored.set_project_type('minimax_frames')
        self.assertEqual(restored.unified_prompt.toPlainText(), '/refine-global Frame notes\nFrame draft')
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
        self.assertEqual(w.unified_prompt.toPlainText(), 'Legacy production prompt')
        payload.pop('minimaxH3')
        w.load_project_payload(payload)
        self.assertEqual(w.project_type, 'ltx')
        self.assertEqual(w.minimax_prompt_text, '')
        self.assertEqual(w.segment_prompt.toPlainText(), 'First action')


    def test_old_worker_rejected_on_workspace_identity_change(self):
        w = self.make_window('minimax_frames')
        w.unified_prompt.setPlainText('Keep me')
        w.ai_request_workspace = (w.workspace_revision, w.current_project_id, w.project_type)
        w.ai_finished_callback = w.minimax_h3_finished
        w.set_project_type('ltx')
        w.set_project_type('minimax_frames')
        w.ai_result_received('Stale')
        self.assertEqual(w.unified_prompt.toPlainText(), 'Keep me')

    def test_actual_worker_callback_does_not_apply_after_project_switch(self):
        w = self.make_window('ltx')
        with patch.object(w.thread_pool, 'start') as start:
            w.start_ai_worker(ai.build_prompts, (), 'Testing', w.magic_finished)
        worker = start.call_args.args[0]
        w.set_project_type('minimax_frames')
        w.unified_prompt.setPlainText('Current production prompt')
        worker.signals.finished.emit({'segments': [{'prompt': 'Wrong project', 'duration': 7}]})
        self.assertEqual(w.segments[0].prompt, 'First action')
        self.assertEqual(w.unified_prompt.toPlainText(), 'Current production prompt')
        self.assertFalse(w.ai_busy)


    def test_background_completion_is_delivered_on_gui_thread(self):
        w = self.make_window('minimax_frames')
        loop = QEventLoop()
        threads = []
        def operation():
            return 'Background draft'
        def finished(result):
            threads.append(QThread.currentThread())
            w.unified_prompt.setPlainText(result)
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
        self.assertEqual(w.unified_prompt.toPlainText(), 'Background draft')


    def test_save_on_main_window_close_preserves_manual_edits(self):
        w = self.make_window('minimax_frames')
        w.current_project_id = 'test-project'
        w.unified_prompt.setPlainText('Manual draft without generation')
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
        self.assertTrue(w.minimax_panel.retry_button.isHidden())
        self.assertFalse(hasattr(w, 'retry_ai_action'))
        # Background worker retry remains; the one button has no menu.

    def test_minimax_uses_visible_requested_total_length(self):
        w = self.make_window('minimax_references')
        w.requested_length.setValue(99)
        self.assertIn('99.0 seconds', w.build_director_request())
        self.assertEqual(w.total_duration(), 5)


if __name__ == '__main__':
    unittest.main()
