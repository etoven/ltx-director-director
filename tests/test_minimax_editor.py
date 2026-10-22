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
        # A completed asynchronous close can delete its native window before
        # unittest runs cleanup, especially when a test opens several windows.
        from shiboken6 import isValid
        self.addCleanup(lambda: window.close() if isValid(window) else None)
        return window

    def test_onboarding_navigation_readiness_and_guides(self):
        from test_onboarding_design import (test_nested_navigation_status_and_stale_engine_verification,
            test_automatic_setup_preserves_manual_files_and_restores_controls,
            test_setup_errors_cancellation_and_closed_wizard_discard_callbacks,
            test_guides_carousel_and_existing_setup_choices_are_preserved)
        test_nested_navigation_status_and_stale_engine_verification()
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            test_automatic_setup_preserves_manual_files_and_restores_controls(Path(folder))
        test_setup_errors_cancellation_and_closed_wizard_discard_callbacks()
        test_guides_carousel_and_existing_setup_choices_are_preserved()

    def test_settings_sidebar_design(self):
        from test_settings_design import test_settings_sidebar_search_preserves_pages_and_edits
        test_settings_sidebar_search_preserves_pages_and_edits()

    def test_panel_scope_documents(self):
        from test_panel_design import test_one_surface_keeps_documents_and_scope_actions_across_workspaces
        test_one_surface_keeps_documents_and_scope_actions_across_workspaces()

    def test_panel_ltx_unified(self):
        from test_panel_design import test_ltx_unified_generation_preserves_segments_and_uses_shared_export_prompt
        test_ltx_unified_generation_preserves_segments_and_uses_shared_export_prompt()

    def test_panel_timing_overview(self):
        from test_panel_design import test_prompt_only_scope_and_timing_overview_remain_available
        test_prompt_only_scope_and_timing_overview_remain_available()

    def test_panel_library_browser(self):
        from test_panel_design import test_project_browser_filters_navigation_and_view_preserve_selection
        test_project_browser_filters_navigation_and_view_preserve_selection()

    def test_panel_compact_layout(self):
        from test_panel_design import test_panel_controls_fit_reference_and_narrow_layouts
        test_panel_controls_fit_reference_and_narrow_layouts()

    def test_panel_desktop_icon_upgrade(self):
        import tempfile
        from test_panel_design import test_desktop_upgrade_refreshes_the_existing_launcher_icon
        with tempfile.TemporaryDirectory() as folder:
            test_desktop_upgrade_refreshes_the_existing_launcher_icon(Path(folder))

    def test_settings_large_text(self):
        from test_settings_design import test_settings_large_text_keeps_navigation_and_footer_accessible
        test_settings_large_text_keeps_navigation_and_footer_accessible()

    def test_settings_readiness(self):
        from test_settings_design import (test_settings_readiness_discards_stale_results_and_requires_explicit_engine_check,
            test_settings_model_file_readiness_never_probes_runtime)
        test_settings_readiness_discards_stale_results_and_requires_explicit_engine_check()
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            test_settings_model_file_readiness_never_probes_runtime(Path(folder))

    def test_local_setup_and_credential_scope(self):
        from test_local_generation_ui import test_wizard_cancel_preserves_credentials_and_finish_separates_sources
        test_wizard_cancel_preserves_credentials_and_finish_separates_sources()

    def test_local_lora_settings(self):
        from test_local_generation_ui import test_loras_are_settings_only_persistent_and_cancel_safe
        test_loras_are_settings_only_persistent_and_cancel_safe()

    def test_local_flux_output_canvas(self):
        from test_local_generation_ui import test_flux_canvas_tracks_current_output_for_both_model_sizes
        test_flux_canvas_tracks_current_output_for_both_model_sizes()

    def test_download_popup_anchor(self):
        import tempfile
        from test_download_tray import (test_download_tray_remains_below_and_right_aligned_when_window_moves,
                                        test_download_focus_sweep_and_thumbnails)
        with tempfile.TemporaryDirectory() as folder:
            test_download_tray_remains_below_and_right_aligned_when_window_moves(Path(folder))
            test_download_focus_sweep_and_thumbnails(Path(folder))

    def test_desktop_file_picker_routing(self):
        from test_file_dialogs import (test_kde_open_save_folder_multiple_and_cancel,
            test_qt_and_zenity_fallbacks_keep_filters_and_selection, test_all_file_pickers_share_desktop_routing)
        test_kde_open_save_folder_multiple_and_cancel()
        test_qt_and_zenity_fallbacks_keep_filters_and_selection()
        test_all_file_pickers_share_desktop_routing()

    def test_local_existing_references_and_verbose_output(self):
        from test_local_generation_ui import test_native_video_uses_unified_output_reference_panel_and_settings_directly
        test_native_video_uses_unified_output_reference_panel_and_settings_directly()

    def test_local_stale_progress_and_settings(self):
        from test_local_generation_ui import test_local_progress_is_ignored_after_a_project_switch_and_no_paid_prompt
        test_local_progress_is_ignored_after_a_project_switch_and_no_paid_prompt()

    def test_local_render_media_preservation_and_export(self):
        from test_local_generation_ui import (test_generated_video_attachment_preserves_previous_render_and_discards_stale_metadata,
                                               test_unsaved_local_video_can_be_exported)
        test_generated_video_attachment_preserves_previous_render_and_discards_stale_metadata()
        test_unsaved_local_video_can_be_exported()

    def test_local_generation_transport_and_media_contracts(self):
        import subprocess, sys, os
        code = "import os,sys,pytest; rc=pytest.main(['-q','tests/test_local_generation.py','tests/test_local_lora.py']); sys.stdout.flush(); sys.stderr.flush(); os._exit(rc)"
        env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', QT_QPA_PLATFORM='offscreen')
        timeout=max(20,int(os.environ.get('DIRECTOR_NATIVE_TIMEOUT','25'))-5)
        subprocess.run([sys.executable, '-c', code], env=env, check=True, timeout=timeout)

    def test_effects_catalog_and_dock(self):
        from test_effects_ui import test_effects_dock_search_artwork_and_menu_migration
        test_effects_dock_search_artwork_and_menu_migration()

    def test_effects_drag_acceptance(self):
        from test_effects_ui import test_effect_drop_acceptance_matches_scope_and_busy_state
        test_effect_drop_acceptance_matches_scope_and_busy_state()

    def test_effects_media_prompts_and_startup(self):
        from test_effects_library import test_source_sounds_and_videos_have_editable_refinement_prompts
        from test_effects_ui import test_effect_preview_tiles_and_stale_startup_audit
        test_source_sounds_and_videos_have_editable_refinement_prompts()
        test_effect_preview_tiles_and_stale_startup_audit()

    def test_effects_thumbnail_integrity_and_repair(self):
        import tempfile
        from test_effects_media import (test_shipped_thumbnail_videos_are_small_silent_and_valid,
            test_startup_repairs_bad_posters_and_corrupted_video_cache, test_missing_media_does_not_abort_other_thumbnail_checks)
        test_shipped_thumbnail_videos_are_small_silent_and_valid()
        with tempfile.TemporaryDirectory() as folder:
            test_startup_repairs_bad_posters_and_corrupted_video_cache(Path(folder))
            test_missing_media_does_not_abort_other_thumbnail_checks(Path(folder))

    def test_quick_fix_buttons_menu_and_preset_editor(self):
        from ltx_prompt_director.effects_library import load_effects, USER_KEY, save_effect
        from ltx_prompt_director.effects_widgets import EffectDialog
        w = self.make_window('minimax_references')
        w._close_saves_queued = True
        w.unified_prompt.setPlainText('Existing combined prompt')
        self.assertLess(w.director_intent_row.indexOf(w.segment_quick_fix_button), w.director_intent_row.indexOf(w.magic_button))
        upper, lower = w.segment_quick_fix_button.menu(), w.combined_quick_fix_button.menu()
        upper.aboutToShow.emit(); lower.aboutToShow.emit()
        self.assertEqual([a.text() for a in upper.actions() if not a.isSeparator()], ['Camera', 'Refine Camera', 'Effects Library…'])
        self.assertEqual([a.text() for a in lower.actions() if not a.isSeparator()], ['Camera', 'Refine Camera', 'Formatting', 'Format Prompt', 'Effects Library…'])
        original = w.settings.value(USER_KEY)
        self.addCleanup(w.settings.setValue, USER_KEY, original)
        editor = EffectDialog(w)
        editor.name.setText('My segment fix')
        editor.category.setText('Framing')
        editor.target.setCurrentIndex(editor.target.findData('segment'))
        editor.prompt.setPlainText('Keep the hands visible.')
        self.assertFalse(editor.menu.isChecked())
        editor.menu.setChecked(True); editor.save()
        w.populate_quick_fix_menu(upper, 'segment'); w.populate_quick_fix_menu(lower, 'unified')
        self.assertIn('My segment fix', [a.text() for a in upper.actions()])
        self.assertNotIn('My segment fix', [a.text() for a in lower.actions()])
        entry = next(e for e in load_effects(w.settings) if e['name'] == 'My segment fix')
        cancelled = EffectDialog(w, entry); cancelled.name.setText('Discard this'); cancelled.reject()
        self.assertIn('My segment fix', [e['name'] for e in load_effects(w.settings)])
        w.set_project_type('ltx'); w.populate_quick_fix_menu(upper, 'segment')
        self.assertTrue(next(a for a in upper.actions() if a.text() == 'Refine Camera').isEnabled())
        self.assertTrue(w.combined_quick_fix_button.isHidden())

    def test_quick_fix_categories_group_custom_actions_and_filter_empty_groups(self):
        from ltx_prompt_director.effects_library import load_effects, USER_KEY, save_effect
        w = self.make_window('minimax_references')
        w._close_saves_queued = True
        w.unified_prompt.setPlainText('Existing production draft')
        original = w.settings.value(USER_KEY)
        self.addCleanup(w.settings.setValue, USER_KEY, original)
        w.settings.setValue(USER_KEY, '[]')
        for i, entry in enumerate([
            dict(name='Pan', target='both', category='Camera', prompt='Pan right.'),
            dict(name='Space', target='unified', category='Formatting', prompt='Add spacing.'),
            dict(name='Close-up', target='segment', category='camera', prompt='Close-up.'),
            dict(name='Voice', target='segment', category='Audio', prompt='Preserve the accent.'),
        ]):
            save_effect(w.settings, {**entry, 'id': f'user:{i}', 'show_in_menu': True})
        upper, lower = w.segment_quick_fix_button.menu(), w.combined_quick_fix_button.menu()
        upper.aboutToShow.emit(); lower.aboutToShow.emit()
        self.assertEqual([a.text() for a in upper.actions() if not a.isSeparator()],
                         ['Camera', 'Pan', 'Close-up', 'Audio', 'Voice', 'Effects Library…'])
        self.assertEqual([a.text() for a in lower.actions() if not a.isSeparator()],
                         ['Camera', 'Pan', 'Formatting', 'Space', 'Effects Library…'])
        with patch.object(w, 'apply_quick_fix') as refine:
            next(a for a in upper.actions() if a.text() == 'Close-up').trigger()
            self.assertEqual(refine.call_args.args[0]['name'], 'Close-up')
            self.assertEqual(refine.call_args.args[1], 'segment')
        w.settings.setValue(USER_KEY, '[]'); upper.aboutToShow.emit()
        self.assertEqual([a.text() for a in upper.actions()], ['Effects Library…'])

    def test_quick_fix_segment_injects_at_bottom_and_never_uses_global_scope(self):
        w = self.make_window('minimax_frames')
        w._close_saves_queued = True
        w.unified_prompt.setPlainText('Keep unified unchanged')
        w.global_prompt.setPlainText('Keep global unchanged')
        before_neighbor = w.segments[1].to_dict()
        w.prompt_scope.setCurrentIndex(w.prompt_scope.findData('global'))
        with patch.object(w, 'ai_credentials', return_value=('gemini', 'model', 'key')), patch.object(w.thread_pool, 'start') as pool:
            w.apply_quick_fix({'name': 'Hands', 'target': 'segment', 'prompt': 'Keep hands visible.'}, 'segment')
            worker = pool.call_args.args[0]
            self.assertEqual(w.prompt_scope.currentData(), 'segment')
            self.assertTrue(worker.args[0][0].prompt.startswith('First action'))
            self.assertTrue(worker.args[0][0].prompt.rstrip().endswith('Keep hands visible.'))
            self.assertEqual(w.ai_request_editor, w.intent_segment_prompt)
            worker.signals.finished.emit({'prompt': 'First action with hands visible.', 'duration': 2})
            self.assertEqual(pool.call_count, 1)
        self.assertEqual(w.segments[0].prompt, 'First action with hands visible.')
        self.assertEqual(w.segments[1].to_dict(), before_neighbor)
        self.assertEqual(w.minimax_prompt_text, 'Keep unified unchanged')
        self.assertEqual(w.global_prompt.toPlainText(), 'Keep global unchanged')

    def test_quick_fix_unified_injects_at_top_and_automatically_refines(self):
        from ltx_prompt_director.quick_fixes import STARTERS
        w = self.make_window('minimax_references')
        w._close_saves_queued = True
        w.unified_prompt.setPlainText('Shot one.\nShot two.')
        before = [s.to_dict() for s in w.segments]
        menu = w.combined_quick_fix_button.menu()
        w.populate_quick_fix_menu(menu, 'unified')
        with patch.object(w, 'ai_credentials', return_value=('gemini', 'model', 'key')), patch.object(w.thread_pool, 'start') as pool:
            next(a for a in menu.actions() if a.text() == "Format Prompt").trigger()
            worker = pool.call_args.args[0]
            current = worker.args[9]
            self.assertTrue(current.startswith('/refine-global ' + STARTERS[1]['prompt']))
            self.assertLess(current.index(STARTERS[1]['prompt']), current.index('Shot one.'))
            self.assertEqual(w.ai_request_editor, w.unified_prompt)
            worker.signals.finished.emit('Shot one.\n\nShot two.')
            self.assertEqual(pool.call_count, 1)
        self.assertEqual(w.minimax_prompt_text, 'Shot one.\n\nShot two.')
        self.assertEqual([s.to_dict() for s in w.segments], before)

    def test_both_quick_fix_from_segment_refines_only_that_segment(self):
        import json
        from ltx_prompt_director.quick_fixes import STARTERS
        for mode, combined in [('minimax_frames', ''), ('minimax_frames', 'Keep combined'), ('ltx', '')]:
            with self.subTest(mode=mode, combined=combined):
                w = self.make_window(mode)
                w._close_saves_queued = True
                w.unified_prompt.setPlainText(combined)
                w.show()
                self.app.processEvents()
                neighbor = w.segments[1].to_dict()
                menu = w.segment_quick_fix_button.menu()
                w.populate_quick_fix_menu(menu, 'segment')
                self.assertTrue(next(a for a in menu.actions() if a.text() == "Refine Camera").isEnabled())
                with patch.object(w, 'ai_credentials', return_value=('gemini', 'model', 'key')), patch.object(w.thread_pool, 'start') as pool:
                    next(a for a in menu.actions() if a.text() == "Refine Camera").trigger()
                    first = pool.call_args.args[0]
                    w.apply_quick_fix(STARTERS[0], 'segment')
                    self.assertEqual(pool.call_count, 1)
                    self.assertEqual(w.minimax_prompt_text, combined)
                    self.assertIn(STARTERS[0]['prompt'], first.args[0][0].prompt)
                    with patch.object(ai, '_provider_raw', return_value=json.dumps({'prompt': 'A steady tracking shot.', 'duration': 2.5})):
                        first.signals.finished.emit(first.operation(*first.args))
                    self.assertEqual(pool.call_count, 1)
                self.assertEqual(w.segments[0].prompt, 'A steady tracking shot.')
                self.assertEqual(w.segments[1].to_dict(), neighbor)
                self.assertEqual(w.minimax_prompt_text, combined)
                expected_editor = w.intent_segment_prompt if w.unified_workspace else w.segment_prompt
                self.assertEqual(w.paragraph_review.editor, expected_editor)
                w.paragraph_review.resolve_all(False)
                self.assertEqual(w.segments[0].prompt, 'First action')
                w.close()

    def test_both_quick_fix_from_unified_refines_only_unified_without_a_selected_segment(self):
        from ltx_prompt_director.quick_fixes import STARTERS
        w = self.make_window('minimax_frames')
        w._close_saves_queued = True
        w.unified_prompt.setPlainText('Existing combined camera direction')
        w.timeline.setCurrentRow(-1)
        before = [s.to_dict() for s in w.segments]
        menu = w.combined_quick_fix_button.menu()
        w.populate_quick_fix_menu(menu, 'unified')
        self.assertTrue(next(a for a in menu.actions() if a.text() == "Refine Camera").isEnabled())
        with patch.object(w, 'ai_credentials', return_value=('gemini', 'model', 'key')), patch.object(w.thread_pool, 'start') as pool:
            next(a for a in menu.actions() if a.text() == "Refine Camera").trigger()
            worker = pool.call_args.args[0]
            self.assertEqual(worker.args[0][0].prompt, before[0]['prompt'])
            self.assertTrue(worker.args[9].startswith('/refine-global ' + STARTERS[0]['prompt']))
            worker.signals.finished.emit('Updated unified camera direction')
            self.assertEqual(pool.call_count, 1)
        self.assertEqual(w.minimax_prompt_text, 'Updated unified camera direction')
        self.assertEqual([s.to_dict() for s in w.segments], before)

    def test_quick_fix_both_is_available_in_prompt_only_workspace(self):
        w = self.make_window('minimax_prompt_refinement')
        w._close_saves_queued = True
        w.unified_prompt.setPlainText('Shot one. Shot two.')
        menu = w.combined_quick_fix_button.menu()
        w.populate_quick_fix_menu(menu, 'unified')
        self.assertTrue(next(a for a in menu.actions() if a.text() == "Refine Camera").isEnabled())
        self.assertTrue(next(a for a in menu.actions() if a.text() == "Format Prompt").isEnabled())
        self.assertTrue(w.segment_quick_fix_button.isHidden())
        with patch.object(w, 'ai_credentials', return_value=('gemini', 'model', 'key')), patch.object(w.thread_pool, 'start') as pool:
            next(a for a in menu.actions() if a.text() == "Refine Camera").trigger()
            worker = pool.call_args.args[0]
            self.assertEqual(worker.args[0], 'gemini')
            self.assertTrue(worker.args[3].startswith('/refine-global refine camera'))
            worker.signals.finished.emit('An improved camera description.')
            self.assertEqual(pool.call_count, 1)
        self.assertEqual(w.minimax_prompt_text, 'An improved camera description.')

    def test_quick_fix_cannot_run_from_the_wrong_menu(self):
        from ltx_prompt_director.quick_fixes import STARTERS
        w = self.make_window('minimax_frames')
        w._close_saves_queued = True
        w.unified_prompt.setPlainText('Combined draft')
        before = [s.to_dict() for s in w.segments]
        with patch.object(w.thread_pool, 'start') as pool:
            w.apply_quick_fix(STARTERS[1], 'segment')
            w.apply_quick_fix({'name': 'Segment only', 'target': 'segment', 'prompt': 'Keep hands visible.'}, 'unified')
        pool.assert_not_called()
        self.assertEqual([s.to_dict() for s in w.segments], before)
        self.assertEqual(w.minimax_prompt_text, 'Combined draft')

    def test_format_quick_fix_keeps_spacing_changes_in_prompt_only_workspace(self):
        w = self.make_window('minimax_prompt_refinement')
        w._close_saves_queued = True
        w.unified_prompt.setPlainText('Shot one. Shot two.')
        menu = w.combined_quick_fix_button.menu()
        menu.aboutToShow.emit()
        with patch.object(w, 'ai_credentials', return_value=('gemini', 'model', 'key')), patch.object(w.thread_pool, 'start') as pool:
            next(a for a in menu.actions() if a.text() == "Format Prompt").trigger()
            worker = pool.call_args.args[0]
            self.assertTrue(worker.args[3].startswith('/refine-global Format the spacing'))
            worker.signals.finished.emit('Shot one.\n\nShot two.')
            self.assertEqual(pool.call_count, 1)
        self.assertEqual(w.minimax_prompt_text, 'Shot one.\n\nShot two.')

    def test_both_quick_fix_still_runs_once_on_failure_or_project_change(self):
        from ltx_prompt_director.quick_fixes import STARTERS
        for origin in ('segment', 'unified'):
            for obsolete in (False, True):
                with self.subTest(origin=origin, obsolete=obsolete):
                    w = self.make_window('minimax_frames')
                    w._close_saves_queued = True
                    w.unified_prompt.setPlainText('Combined draft')
                    with patch.object(w, 'ai_credentials', return_value=('gemini', 'model', 'key')), patch.object(w.thread_pool, 'start') as pool:
                        w.apply_quick_fix(STARTERS[0], origin)
                        worker = pool.call_args.args[0]
                        if obsolete:
                            w.workspace_revision += 1
                            worker.signals.finished.emit({'prompt': 'Discard this', 'duration': 2} if origin == 'segment' else 'Discard this')
                        else:
                            with patch.object(w, 'magic_failed'):
                                worker.signals.failed.emit('Provider failed')
                        self.assertEqual(pool.call_count, 1)
                        self.assertNotEqual(w.segments[0].prompt, 'Discard this')
                        self.assertNotEqual(w.minimax_prompt_text, 'Discard this')

    def test_refine_after_quick_fix_reviews_uses_clean_authoritative_text(self):
        w = self.make_window('minimax_frames')
        w._close_saves_queued = True
        w.prompt_scope.setCurrentIndex(w.prompt_scope.findData('segment'))
        w.unified_prompt.setPlainText('Current unified camera direction')
        self.assertTrue(w.paragraph_review.begin(w.intent_segment_prompt, 'Deleted segment direction', 'First action'))
        with patch.object(w, 'ai_credentials', return_value=('gemini', 'model', 'key')), patch.object(w.thread_pool, 'start') as pool:
            w.refine_selected_prompt()
            first = pool.call_args.args[0]
            self.assertEqual(first.args[0][0].prompt, 'First action')
            first.signals.finished.emit({'prompt': 'Refined first action', 'duration': 2})
            w.finish_prompt_reviews()
            self.assertTrue(w.paragraph_review.begin(w.unified_prompt, 'Deleted unified direction', w.minimax_prompt_text))
            w.refine_minimax_prompt()
            second = pool.call_args.args[0]
            self.assertEqual(second.args[9], 'Current unified camera direction')
            self.assertNotIn('Deleted', second.args[9])
            second.signals.finished.emit('Current unified camera direction, refined')

    def test_conditioning_footer_is_the_shot_range_for_start_end_and_video(self):
        from PySide6.QtWidgets import QFrame, QLabel
        w = self.make_window('minimax_frames')
        w._close_saves_queued = True
        w.segments = [Segment('Video', '', '', 'video', 'start', 'Opening', .5),
                      Segment('Start', '', '', 'image', 'start', 'Depart', 2),
                      Segment('End', '', '', 'image', 'end', 'Arrive', 3)]
        w.refresh_timeline(0)
        footers = [t.findChild(QFrame, 'conditioningFooter') for t in
                   w.conditioning_content.findChildren(QFrame, 'conditioningTile')]
        self.assertEqual([[l.text() for l in f.findChildren(QLabel)] for f in footers],
                         [['Start 0.00s', 'End 0.50s'], ['Start 0.50s', 'End 2.50s'], ['Start 2.50s', 'End 5.50s']])

    def test_editor_specific_generate_and_refine_buttons_do_not_move(self):
        from PySide6.QtWidgets import QPushButton
        w = self.make_window("ltx")
        self.assertGreaterEqual(w.segment_header.indexOf(w.magic_button), 0)
        self.assertGreaterEqual(w.segment_header.indexOf(w.prompt_refine_button), 0)
        self.assertTrue(w.combined_generate_button.isHidden())
        self.assertTrue(w.combined_refine_button.isHidden())
        w.set_project_type("minimax_frames")
        self.assertGreaterEqual(w.director_intent_row.indexOf(w.magic_button), 0)
        self.assertGreaterEqual(w.director_intent_row.indexOf(w.prompt_refine_button), 0)
        self.assertGreaterEqual(w.minimax_panel.header.indexOf(w.combined_generate_button), 0)
        self.assertGreaterEqual(w.minimax_panel.header.indexOf(w.combined_refine_button), 0)
        self.assertNotEqual(w.magic_button, w.combined_generate_button)
        self.assertTrue(w.combined_generate_button.isHidden())
        self.assertFalse(w.prompt_surface.generate.isHidden())
        self.assertTrue(w.combined_refine_button.isHidden())
        self.assertFalse(w.prompt_surface.refine.isHidden())
        self.assertEqual(w.magic_button.text(), "Generate")
        self.assertEqual(w.prompt_refine_button.text(), "Refine")
        w.set_project_type("minimax_prompt_refinement")
        self.assertTrue(w.magic_button.isHidden())
        self.assertTrue(w.prompt_refine_button.isHidden())
        self.assertGreaterEqual(w.minimax_panel.header.indexOf(w.combined_generate_button), 0)
        w.set_project_type("ltx")
        self.assertGreaterEqual(w.segment_header.indexOf(w.magic_button), 0)
        self.assertGreaterEqual(w.segment_header.indexOf(w.prompt_refine_button), 0)
        self.assertEqual(len(w.findChildren(QPushButton, "promptAction")), 4)

    def test_sync_button_follows_unified_timeline_capability(self):
        w = self.make_window("ltx")
        self.assertTrue(w.sync_timeline_button.isHidden())
        w.set_project_type("minimax_frames")
        w.unified_prompt.setPlainText("00:00–00:08 master")
        self.assertFalse(w.sync_timeline_button.isHidden())
        self.assertTrue(w.sync_timeline_button.isEnabled())
        w.set_project_type("minimax_references")
        w.unified_prompt.setPlainText("00:00–00:08 reference master")
        self.assertFalse(w.sync_timeline_button.isHidden())
        self.assertTrue(w.sync_timeline_button.isEnabled())
        w.set_project_type("minimax_prompt_refinement")
        self.assertTrue(w.sync_timeline_button.isHidden())

    def test_sync_worker_receives_original_segments_and_combined_prompt(self):
        from ltx_prompt_director.segment_plan import sync_timeline_to_prompt
        w = self.make_window("minimax_frames")
        w.unified_prompt.setPlainText("00:00-00:01 opening; 00:01-00:09 closing")
        snapshot = w.minimax_prompt_text
        self.assertEqual(w.add_tile_layout.indexOf(w.sync_timeline_button), 2)
        self.assertEqual(w.minimax_panel.header.indexOf(w.sync_timeline_button), -1)
        self.assertEqual(w.sync_timeline_button.size(), w.add_tile.size())
        with (patch.object(w, "ai_credentials", return_value=("gemini", "gemini-3.5-flash", "key")),
              patch.object(w, "start_ai_worker") as start):
            w.sync_timeline_button.click()
        args = start.call_args.args
        self.assertIs(args[0], sync_timeline_to_prompt)
        self.assertEqual(args[1][4], snapshot)
        self.assertEqual([x.id for x in args[1][0]], [x.id for x in w.segments])
        self.assertEqual(args[3], w.sync_timeline_finished)
        self.assertEqual(w.unified_prompt.toPlainText(), snapshot)

    def test_sync_result_retimes_existing_segments_and_preserves_combined_prompt(self):
        from ltx_prompt_director.segment_plan import normalize_timeline_sync
        import json
        w = self.make_window("minimax_frames")
        master = "00:00-00:01 Opening action. 00:01-00:09 Closing action."
        w.unified_prompt.setPlainText(master)
        before = [x.to_dict() for x in w.segments]
        records = [{**{field: value for field, value in row.items()
                       if field in ("id", "name", "kind", "role", "prompt", "image_prompt")},
                    "imagePrompt": row["image_prompt"],
                    "duration": duration, "prompt": direction}
                   for row, duration, direction in zip(
                       before, (1.0, 8.0), ("Opening action", "Closing action"))]
        records[0].pop("image_prompt", None)
        records[1].pop("image_prompt", None)
        response = normalize_timeline_sync(json.dumps({"segments": records}), w.segments, master)
        w.sync_timeline_source = master
        w.ai_segment_snapshot = [Segment.from_dict(item) for item in before]
        with patch.object(w, "_animate_ai_timeline") as animate:
            w.sync_timeline_finished(response)
        animate.assert_called_once()
        self.assertEqual([seg.duration for seg in w.segments], [1.0, 8.0])
        self.assertEqual([seg.id for seg in w.segments], [x["id"] for x in before])
        self.assertEqual(w.unified_prompt.toPlainText(), master)
        self.assertEqual(w.minimax_prompt_text, master)
        self.assertEqual(w.paragraph_review.pending_count, 0)

    def test_timeline_click_switches_to_segment_scope_including_reselected_card(self):
        from PySide6.QtTest import QTest
        w = self.make_window("minimax_frames")
        w.intent.setPlainText("Keep the whole-sequence direction")
        w.global_prompt.setPlainText("Keep the global prompt")
        w.unified_prompt.setPlainText("Keep the production draft")
        before = [segment.to_dict() for segment in w.segments]
        w.show()
        self.app.processEvents()
        for row in (1, 1, 0):
            w.prompt_scope.setCurrentIndex(w.prompt_scope.findData("global"))
            item = w.timeline.item(row)
            w.timeline.scrollToItem(item)
            self.app.processEvents()
            QTest.mouseClick(w.timeline.viewport(), Qt.MouseButton.LeftButton,
                            pos=w.timeline.visualItemRect(item).center())
            self.assertEqual(w.prompt_scope.currentData(), "segment")
            self.assertEqual(w.intent_segment_prompt.toPlainText(), w.segments[row].prompt)
            self.assertIs(w.prompt_surface.editors.currentWidget(), w.intent_segment_prompt)
        self.assertEqual([segment.to_dict() for segment in w.segments], before)
        self.assertEqual(w.intent.toPlainText(), "Keep the whole-sequence direction")
        self.assertEqual(w.global_prompt.toPlainText(), "Keep the global prompt")
        self.assertEqual(w.unified_prompt.toPlainText(), "Keep the production draft")
        w.prompt_scope.setCurrentIndex(w.prompt_scope.findData("global"))
        w.refresh_timeline(1)
        self.assertEqual(w.prompt_scope.currentData(), "global")

    def test_segment_context_menu_has_manual_generate_artwork(self):
        w = self.make_window("minimax_frames")
        w.resize(1150, 820)
        w.show()
        self.app.processEvents()
        # A fake menu avoids QMenu.exec()'s native modal event loop in CI.
        with patch("ltx_prompt_director.ui.QMenu") as menu_class:
            point = w.timeline.visualItemRect(w.timeline.item(0)).center()
            w.timeline_menu(point)
            menu = menu_class.return_value
            names = [call.args[0] for call in menu.addAction.call_args_list]
            self.assertIn("✦ Generate Artwork", names)
            menu.addAction.return_value.setEnabled.assert_any_call(True)
            menu.exec.assert_called_once()

        w.segments[0].kind = "video"
        w.segments[0].role = "start"
        w.refresh_timeline(0)
        with patch("ltx_prompt_director.ui.QMenu") as menu_class:
            point = w.timeline.visualItemRect(w.timeline.item(0)).center()
            w.timeline_menu(point)
            menu = menu_class.return_value
            self.assertIn("✦ Generate Artwork",
                          [call.args[0] for call in menu.addAction.call_args_list])
            menu.addAction.return_value.setEnabled.assert_any_call(False)

    def test_manual_text_artwork_uses_neighbor_images_and_prompt_context(self):
        from tempfile import TemporaryDirectory
        from PIL import Image
        from ltx_prompt_director.artwork_tools import ArtworkTool
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        directory = scratch.name
        w = self.make_window("minimax_frames")
        first = Path(directory) / "before.png"
        last = Path(directory) / "after.png"
        Image.new("RGB", (16, 9)).save(first)
        Image.new("RGB", (16, 9)).save(last)
        w.segments = [
            Segment("Before", str(first), str(first), "image", "start",
                    "Standing in a wet shower", 1),
            Segment("Bridge", "", "", "text", "text",
                    "She stretches her arm gradually", 2),
            Segment("After", str(last), str(last), "image", "end",
                    "Arm becomes a lioness foreleg", 2),
        ]
        w.refresh_timeline(1)
        tool = ArtworkTool("gemini", "gemini-3.1-flash-lite-image", "test-key")
        with (patch.object(w, "_available_artwork_tools", return_value=[tool]),
              patch.object(w, "_approve_artwork_cost", return_value=True),
              patch.object(w, "_start_segment_artwork") as worker):
            w.generate_artwork_for_segment(w.segments[1].id)
        worker.assert_called_once()
        segment_id, record, dispatched_tool = worker.call_args.args
        self.assertEqual(segment_id, w.segments[1].id)
        self.assertEqual(dispatched_tool, tool)
        self.assertEqual(
            {Path(ref["path"]) for ref in w._artwork_reference_inputs(segment_id)
             if ref.get("path")}, {first, last})
        self.assertIn("She stretches her arm gradually", record["artworkPrompt"])
        self.assertIn("Standing in a wet shower", record["artworkPrompt"])
        self.assertIn("Arm becomes a lioness foreleg", record["artworkPrompt"])
        self.assertEqual(w.segments[1].kind, "text")
        self.assertEqual(w.segments[1].artwork_prompt, record["artworkPrompt"])
        # No background render was started; avoid scheduling a fixture save.
        w.project_dirty = False
        w.project_sessions.clear()

    def test_manual_image_artwork_uses_original_as_edit_source(self):
        from tempfile import TemporaryDirectory
        from PIL import Image
        from ltx_prompt_director.artwork_tools import ArtworkTool
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        directory = scratch.name
        w = self.make_window("minimax_frames")
        before = Path(directory) / "source.png"
        following = Path(directory) / "next.png"
        Image.new("RGB", (16, 9)).save(before)
        Image.new("RGB", (16, 9)).save(following)
        w.segments = [
            Segment("Subject", str(before), str(before), "image", "start",
                    "Extend claws and lengthen the forearm", 2),
            Segment("Next", str(following), str(following), "image", "end",
                    "Later transformation stage", 3),
        ]
        w.refresh_timeline(0)
        original_id = w.segments[0].id
        tool = ArtworkTool("gemini", "gemini-3.1-flash-lite-image", "key")
        with (patch.object(w, "_available_artwork_tools", return_value=[tool]),
              patch.object(w, "_approve_artwork_cost", return_value=True),
              patch.object(w, "_start_segment_artwork") as worker):
            w.generate_artwork_for_segment(original_id)
        worker.assert_called_once()
        segment_id, record, dispatched_tool = worker.call_args.args
        self.assertEqual(segment_id, original_id)
        self.assertEqual(dispatched_tool, tool)
        self.assertEqual(Path(w.segments[0].media_path), before)
        self.assertEqual([Path(ref["path"]) for ref in w._artwork_reference_inputs(original_id)
                          if ref.get("path")], [following])
        self.assertIn("Extend claws and lengthen the forearm", record["artworkPrompt"])
        self.assertIn("IMAGE-TO-IMAGE ALIGNMENT", record["artworkPrompt"])
        self.assertEqual(w.segments[0].media_path, str(before))
        self.assertEqual(w.segments[0].role, "start")
        w._artwork_jobs = 0
        # No background render was started; avoid scheduling a fixture save.
        w.project_dirty = False
        w.project_sessions.clear()

    def test_manual_artwork_denied_approval_does_not_start(self):
        from ltx_prompt_director.artwork_tools import ArtworkTool
        w = self.make_window("minimax_frames")
        current = w.segments[0]
        tool = ArtworkTool("gemini", "gemini-3.1-flash-lite-image", "key")
        with (patch.object(w, "_available_artwork_tools", return_value=[tool]),
              patch.object(w, "_approve_artwork_cost", return_value=False),
              patch.object(w, "start_disk_job") as job):
            w.generate_artwork_for_segment(current.id)
        job.assert_not_called()
        self.assertEqual(current.artwork_prompt, "")
        self.assertEqual(current.artwork_status, "")

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
        w.prompt_surface.select('global')
        self.assertIs(w.prompt_surface.editors.currentWidget(), w.intent)
        self.assertEqual(w.prompt_scope.currentData(), "global")
        w.prompt_scope.setCurrentIndex(1)
        self.assertIs(w.prompt_surface.editors.currentWidget(), w.intent_segment_prompt)
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
        w.prompt_surface.select('global')
        self.assertIs(w.prompt_surface.editors.currentWidget(), w.intent)
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
        self.assertEqual(w.references_button.text(), "")
        self.assertFalse(w.references_button.icon().isNull())
        self.assertEqual(w.references_button.accessibleName(), "Reference media")
        self.assertLess(
            next(i for i, action in enumerate(bar.actions())
                 if bar.widgetForAction(action) is w.references_button),
            next(i for i, action in enumerate(bar.actions())
                 if bar.widgetForAction(action) is w.project_type_combo)
        )
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
        panel = w.prompt_surface.content
        stack = w.prompt_surface.editors
        label = w.director_intent_label
        selector = w.prompt_surface.scope
        self.assertGreaterEqual(stack.geometry().top(), 0)
        self.assertGreaterEqual(stack.width(), panel.width() - w.prompt_surface.rail.width() - 32)
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
        self.assertGreaterEqual(w.prompt_surface.scope.width(), w.prompt_surface.width() - 80)
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
        self.assertGreaterEqual(w.prompt_surface.scope.width(), w.prompt_surface.width() - 80)
        self.assertGreaterEqual(w.conditioning_guide.height(), 96)
        self.assertEqual(w.start_button.text(), "Start")
        self.assertEqual(w.end_button.text(), "End")
        self.assertIs(w.prompt_surface.editors.currentWidget().parent(), w.prompt_surface.editors)
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

    def test_timeline_navigation_is_located_beside_ruler(self):
        from PySide6.QtWidgets import QLabel, QToolBar, QToolButton
        w = self.make_window("minimax_frames")
        w.resize(1120, 780)
        w.show()
        self.app.processEvents()
        toolbar = w.findChild(QToolBar, "mainToolbar")
        self.assertIsNotNone(toolbar)
        self.assertFalse(hasattr(w, "timeline_controls"))
        self.assertIs(w.timeline_scale.parent(), w.timeline_navigation)
        self.assertIs(w.timeline_fit_button.parent(), w.timeline_navigation)
        self.assertIs(w.output_size_control.parent(), toolbar)
        self.assertIs(w.timeline_shell.layout().itemAt(1).layout().itemAt(1).widget(),
                      w.ruler)
        toolbar_widgets = [toolbar.widgetForAction(action)
                           for action in toolbar.actions()]
        positions = [toolbar_widgets.index(widget) for widget in (
            w.project_type_combo, w.output_size_control, w.downloads_button)]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(w.output_size_control.accessibleName(), "Output resolution")
        self.assertEqual(w.timeline_scale.accessibleName(), "Timeline zoom")
        self.assertIsInstance(w.timeline_fit_button, QToolButton)
        self.assertEqual(w.timeline_fit_button.text(), "")
        self.assertEqual(w.timeline_fit_button.toolButtonStyle(), Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.assertFalse(w.timeline_fit_button.icon().isNull())
        self.assertGreaterEqual(w.timeline_fit_button.height(), 30)
        self.assertIn("Auto-fit", w.timeline_fit_button.toolTip())

        self.assertIs(w.ai_timeline_status.parent(), w.statusBar())
        self.assertTrue(w.director_intent_row.indexOf(w.magic_button) >= 0)
        self.assertEqual(w.magic_button.objectName(), "promptAction")
        w.set_project_type("minimax_prompt_refinement")
        self.assertGreaterEqual(w.minimax_panel.header.indexOf(w.combined_generate_button), 0)
        w.set_project_type("ltx")
        self.assertGreaterEqual(w.segment_header.indexOf(w.magic_button), 0)
        w.set_project_type("minimax_frames")
        self.assertGreaterEqual(w.director_intent_row.indexOf(w.magic_button), 0)
        self.assertGreaterEqual(w.minimax_panel.header.indexOf(w.combined_refine_button), 0)
        self.assertFalse(any(label.text() in {"TIMELINE", "OUTPUT", "SCALE"}
                             for label in w.timeline_shell.findChildren(QLabel)))

    def test_conditioning_tiles_resize_collapse_and_restore(self):
        from tempfile import TemporaryDirectory
        from PIL import Image
        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QFrame, QLabel
        with TemporaryDirectory() as folder:
            image = Path(folder) / "frame.png"
            Image.new("RGB", (160, 90), "blue").save(image)
            w = self.make_window("minimax_frames")
            w._close_saves_queued = True
            w.segments = [Segment("Opening.png", str(image), str(image), "image", "start", "Open", 2),
                          Segment("Ending.png", str(image), str(image), "image", "end", "Close", 3)]
            w.refresh_timeline(0)
            w.resize(1200, 920)
            w.show()
            self.app.processEvents()
            tiles = w.conditioning_content.findChildren(QFrame, "conditioningTile")
            self.assertEqual(len(tiles), 2)
            self.assertGreaterEqual(tiles[0].width(), 160)
            self.assertIn("Start", " ".join(label.text() for label in tiles[0].findChildren(QLabel)))
            w.set_conditioning_height(220)
            self.assertEqual(w.conditioning_guide.height(), 220)
            self.assertGreater(w._conditioning_previews[0][0].height(), 80)
            handle = w.conditioning_height_handle
            QTest.mouseDClick(handle, Qt.MouseButton.LeftButton, pos=QPoint(20, 6))
            self.app.processEvents()
            self.assertTrue(w.conditioning_guide.isHidden())
            self.assertFalse(handle.isHidden())
            self.assertEqual(w.settings.value("conditioning_panel_height", -1, int), 0)
            QTest.mouseDClick(handle, Qt.MouseButton.LeftButton, pos=QPoint(20, 6))
            self.app.processEvents()
            self.assertFalse(w.conditioning_guide.isHidden())
            self.assertEqual(w.conditioning_guide.height(), 220)
            start = w.conditioning_height
            QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=QPoint(20, 6))
            QTest.mouseMove(handle, QPoint(20, 36))
            QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=QPoint(20, 6))
            self.assertGreater(w.conditioning_height, start)

    def test_new_project_is_in_library_immediately_and_keeps_unsaved_draft(self):
        import json
        import zipfile
        from tempfile import TemporaryDirectory
        from ltx_prompt_director import ui
        w = self.make_window()
        with TemporaryDirectory() as folder, patch.object(ui, "project_library_path", return_value=Path(folder)):
            w.new_project()
            first_id = w.current_project_id
            self.assertTrue(first_id)
            self.assertTrue((Path(folder) / f"{first_id}.meta.json").is_file())
            archive = Path(folder) / f"{first_id}.LTXD"
            with zipfile.ZipFile(archive) as project:
                self.assertEqual(json.loads(project.read("project.json"))["frames"], [])
            w.segments = [Segment("Draft", "", "", kind="text", prompt="Unsaved action")]
            w.intent.setPlainText("Unsaved direction")
            w.mark_dirty()
            w.refresh_timeline(0)
            w.new_project()
            self.assertNotEqual(w.current_project_id, first_id)
            self.assertEqual(len(w.library_records()), 2)
            w.open_library_project(project_id=first_id)
            self.assertEqual(w.current_project_id, first_id)
            self.assertEqual(w.segments[0].prompt, "Unsaved action")
            self.assertEqual(w.intent.toPlainText(), "Unsaved direction")
            self.assertTrue(w.project_dirty)
            w._close_saves_queued = True

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
        w.prompt_surface.select('unified')
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

    def test_segment_generate_and_refine_own_the_request_stream_progress_and_review(self):
        import json
        from ltx_prompt_director import ui
        for generate, baseline in ((False, "Original segment action."),
                                   (True, "Original segment action."), (True, "")):
            with self.subTest(generate=generate, baseline=baseline):
                w = self.make_window("minimax_frames")
                w.resize(1150, 850)
                w.show()
                self.app.processEvents()
                w.prompt_scope.setCurrentIndex(1)
                w.intent_segment_prompt.setPlainText(baseline)
                w.unified_prompt.setPlainText("Untouched production draft")
                neighbor = w.segments[1].to_dict()
                with (patch.object(w, "ai_credentials", return_value=("gemini", "test-model", "key")),
                      patch.object(w, "start_ai_worker", wraps=w.start_ai_worker) as dispatch,
                      patch.object(w.thread_pool, "start") as pool):
                    (w.generate_upper_prompt if generate else w.refine_upper_prompt)()
                    operation, args = dispatch.call_args.args[:2]
                    self.assertIs(operation, ui.generate_segment_prompt if generate else ui.refine_segment_prompt)
                    self.assertEqual(args[1], 0)
                    self.assertEqual(args[0][0].prompt, baseline)
                    self.assertNotIn("Untouched production draft", str(args))
                    # Exercise the real prompt builder, with only the provider transport mocked.
                    result = {"prompt": "Revised segment action.", "duration": 2.5}
                    with patch.object(ai, "_provider_raw", return_value=json.dumps(result)) as provider:
                        self.assertEqual(operation(*args)["prompt"], result["prompt"])
                    images = provider.call_args.args[0]
                    self.assertEqual(next(i["prompt"] for i in images if i["selected"]), baseline)
                    self.assertIs(w.inline_typing.editor, w.intent_segment_prompt)
                    self.assertFalse(w.director_ai_marquee.isHidden())
                    self.assertTrue(w.minimax_panel.ai_marquee.isHidden())
                    worker = pool.call_args.args[0]
                    worker.signals.stream.emit("output", json.dumps(result))
                    w.inline_typing.render()
                    self.assertEqual(w.segments[0].prompt, baseline)
                    self.assertEqual(w.unified_prompt.toPlainText(), "Untouched production draft")
                    worker.signals.finished.emit(result)
                self.assertTrue(w.director_ai_marquee.isHidden())
                self.assertIs(w.paragraph_review.editor, w.intent_segment_prompt)
                self.assertIs(w.paragraph_review.approve_all.parent(), w.intent_segment_prompt)
                self.assertEqual(w.paragraph_review.pending_count, 1)
                w.paragraph_review.deny_all.setChecked(True)
                self.assertEqual(w.segments[0].prompt, baseline)
                self.assertEqual(w.segments[1].to_dict(), neighbor)
                self.assertEqual(w.unified_prompt.toPlainText(), "Untouched production draft")
                w._close_saves_queued = True
                w.project_dirty = False
                w.close()

    def test_pasting_image_discards_the_previous_archive_origin_before_split(self):
        from PySide6.QtGui import QImage, QColor
        from ltx_prompt_director import ui
        w = self.make_window("minimax_frames")
        w.segments[0].kind = "image"
        w.segments[0]._archive_source = ("old.LTXD", "media/0000.png")
        w.segments[0]._media_digest = "previous-image"
        w.refresh_timeline(0)
        image = QImage(64, 48, QImage.Format.Format_RGB32)
        image.fill(QColor("#25a866"))
        self.app.clipboard().setImage(image)
        w.paste_image_to_replace()
        w.split_timeline_segment(w.segments[0].id, 1)
        for segment in w.segments[:2]:
            self.assertFalse(hasattr(segment, "_archive_source"))
            self.assertFalse(hasattr(segment, "_media_digest"))
            self.assertEqual(QImage(segment.media_path).pixelColor(0, 0), QColor("#25a866"))

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
            self.assertFalse(w.prompt_surface.scope.isHidden())
            self.assertTrue(w.segment_panel.isHidden())
            self.assertTrue(w.unified_panel.isHidden())
            self.assertFalse(w.prompt_surface.isHidden())
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

    def test_upper_buttons_target_segment_or_global_only(self):
        from ltx_prompt_director.ai import generate_segment_prompt
        w = self.make_window("minimax_frames")
        w.prompt_scope.setCurrentIndex(1)
        w.intent_segment_prompt.setPlainText("Selected segment direction")
        w.unified_prompt.setPlainText("Authoritative combined draft")
        with (patch.object(w, "ai_credentials",
                           return_value=("gemini", "gemini-3.5-flash", "key")),
              patch.object(w, "start_ai_worker") as start):
            w.magic_button.click()
        self.assertIs(start.call_args.args[0], generate_segment_prompt)
        self.assertEqual(start.call_args.args[1][1], w.timeline.currentRow())
        self.assertEqual(w.minimax_prompt_text, "Authoritative combined draft")
        with patch.object(w, "refine_selected_prompt") as refined:
            w.prompt_refine_button.click()
        refined.assert_called_once_with()
        w.prompt_scope.setCurrentIndex(0)
        with patch.object(w, "_generate_or_refine_upper_global") as global_call:
            w.magic_button.click()
            w.intent.setPlainText("Keep it in one shot")
            w.prompt_refine_button.click()
        self.assertEqual([call.args for call in global_call.call_args_list],
                         [(False,), (True,)])

    def test_global_buttons_run_sequence_wide_magic_build_and_refinement(self):
        from ltx_prompt_director.ai import build_prompts, refine_sequence_prompts
        w = self.make_window("minimax_frames")
        w.prompt_scope.setCurrentIndex(0)
        w.intent.setPlainText("/refine-global Maintain the full transformation arc")
        original_ids = [s.id for s in w.segments]
        with (patch.object(w, "ai_credentials",
                           return_value=("gemini", "gemini-3.5-flash", "key")),
              patch.object(w, "start_ai_worker") as start):
            w.magic_button.click()
            operation, args, activity, done = start.call_args.args
            self.assertIs(operation, build_prompts)
            self.assertEqual([s.id for s in args[0]], original_ids)
            self.assertEqual(done, w.magic_finished)
            self.assertTrue(w.global_build_cascade_requested)
            self.assertIn("ALL sequence", activity)
            w.prompt_refine_button.click()
            operation, args, activity, done = start.call_args.args
            self.assertIs(operation, refine_sequence_prompts)
            self.assertEqual([s.id for s in args[0]], original_ids)
            self.assertIn("/refine-global", args[4])
            self.assertEqual(done, w.magic_finished)

    def test_global_magic_finished_cascades_to_combined_only_after_sequence(self):
        w = self.make_window("minimax_frames")
        w.prompt_scope.setCurrentIndex(0)
        w.unified_prompt.setPlainText("Previous combined")
        w.global_build_cascade_requested = True
        ids = [s.id for s in w.segments]
        with patch.object(w, "sync_combined_from_segments") as cascade:
            w.magic_finished({
                "segments": [
                    {"prompt": "New opening", "duration": 2},
                    {"prompt": "New closing", "duration": 3},
                ],
                "globalPrompt": "Sequence direction",
            })
        cascade.assert_called_once_with()
        self.assertEqual([s.prompt for s in w.segments],
                         ["New opening", "New closing"])
        self.assertEqual([s.id for s in w.segments], ids)
        self.assertEqual(w.minimax_prompt_text, "Previous combined")
        self.assertFalse(w.global_build_cascade_requested)

    def test_segment_refinement_only_changes_selected_segment_without_cascade(self):
        w = self.make_window("minimax_frames")
        original_second = w.segments[1].prompt
        original_combined = "Full production prompt"
        w.unified_prompt.setPlainText(original_combined)
        current = w.segments[0]
        original_id, original_kind, original_role = current.id, current.kind, current.role
        w.refinement_segment_id = current.id
        with patch.object(w, "sync_combined_from_segments") as sync:
            w.refine_prompt_finished({
                "prompt": "Refined opening only", "duration": 2.5,
                "imagePrompt": "Better opening frame",
            })
        sync.assert_not_called()
        self.assertEqual(w.segments[0].prompt, "Refined opening only")
        self.assertEqual(w.segments[1].prompt, original_second)
        self.assertEqual((current.id, current.kind, current.role),
                         (original_id, original_kind, original_role))
        self.assertEqual(w.minimax_prompt_text, original_combined)

    def test_timing_refinement_keeps_combined_prompt_and_other_segments(self):
        w = self.make_window("minimax_frames")
        w.unified_prompt.setPlainText("Preserved production draft")
        original = w.segments[0].prompt
        neighbor = w.segments[1].to_dict()
        w.refinement_segment_id = w.segments[0].id
        with patch.object(w, "_generate_minimax_prompt") as generate:
            w.refine_timing_finished({"duration": 2.5})
        generate.assert_not_called()
        self.assertEqual(w.segments[0].duration, 2.5)
        self.assertEqual(w.segments[0].prompt, original)
        self.assertEqual(w.segments[1].to_dict(), neighbor)
        self.assertEqual(w.unified_prompt.toPlainText(), "Preserved production draft")

    def test_editor_action_icons_and_sync_direction(self):
        w = self.make_window("minimax_frames")
        for button in (w.magic_button, w.prompt_refine_button,
                       w.combined_generate_button, w.combined_refine_button,
                       w.sync_timeline_button, w.sync_combined_button):
            self.assertFalse(button.icon().isNull())
        self.assertIn("Rewrite timeline", w.sync_timeline_button.toolTip())
        self.assertIn("↑", w.sync_combined_button.toolTip())
        self.assertEqual(w.sync_timeline_button.text(), "")
        self.assertEqual(w.sync_combined_button.text(), "")
        self.assertIn("Segment", w.prompt_refine_button.toolTip())
        self.assertIn("Global", w.prompt_refine_button.toolTip())

    def test_lower_buttons_only_generate_or_refine_combined(self):
        w = self.make_window("minimax_frames")
        with (patch.object(w, "_generate_minimax_prompt") as generated,
              patch.object(w, "refine_minimax_prompt") as refined):
            w.combined_generate_button.click()
            w.unified_prompt.setPlainText("Combined with /refine instructions")
            w.combined_refine_button.click()
        generated.assert_called_once_with(w.minimax_prompt_mode)
        refined.assert_called_once_with()
        self.assertEqual(w.magic_button.text(), "Generate")
        self.assertEqual(w.prompt_refine_button.text(), "Refine")

    def test_reverse_sync_uses_all_segments_and_never_selected_subset(self):
        from ltx_prompt_director.segment_plan import sync_combined_from_timeline
        w = self.make_window("minimax_frames")
        w.segments.append(Segment("Third", "", "", kind="text",
                                  prompt="Final beat at the end", duration=4))
        w.refresh_timeline(0)
        w.unified_prompt.setPlainText("Outdated combined scene")
        with (patch.object(w, "ai_credentials",
                           return_value=("gemini", "gemini-3.5-flash", "key")),
              patch.object(w, "start_ai_worker") as start):
            w.sync_combined_button.click()
        args = start.call_args.args
        self.assertIs(args[0], sync_combined_from_timeline)
        self.assertEqual([seg.id for seg in args[1][0]],
                         [seg.id for seg in w.segments])
        self.assertEqual(args[1][4], "Outdated combined scene")
        self.assertEqual(args[3], w.sync_combined_finished)
        self.assertIn("ALL project timeline segments", args[2])

    def test_reverse_sync_completion_updates_only_combined_prompt(self):
        import copy
        w = self.make_window("minimax_frames")
        w.unified_prompt.setPlainText("Old combined")
        original = copy.deepcopy(w.segments)
        w.sync_combined_source = w.minimax_prompt_text
        w.sync_combined_signature = tuple(
            (seg.id, seg.name, seg.kind, seg.role, seg.duration,
             seg.prompt, seg.image_prompt, seg.media_path) for seg in w.segments)
        w.sync_combined_finished({"prompt": "Revised complete combined sequence"})
        self.assertEqual(w.minimax_prompt_text, "Revised complete combined sequence")
        self.assertEqual(w.unified_prompt.toPlainText(), "Revised complete combined sequence")
        self.assertEqual([(s.id, s.prompt, s.duration) for s in w.segments],
                         [(s.id, s.prompt, s.duration) for s in original])

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
        self.assertTrue(w.unified_panel.isHidden())
        self.assertFalse(w.prompt_surface.isHidden())
        self.assertTrue(w.director_intent_row.indexOf(w.magic_button) >= 0)
        self.assertTrue(w.director_intent_row.indexOf(w.prompt_refine_button) >= 0)
        self.assertTrue(w.director_intent_row.indexOf(w.prompt_scope) >= 0)
        self.assertGreaterEqual(w.minimax_panel.header.indexOf(w.combined_generate_button), 0)
        self.assertGreaterEqual(w.minimax_panel.header.indexOf(w.combined_refine_button), 0)
        self.assertEqual(len(w.findChildren(QPushButton, 'promptAction')), 4)

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
        while card.rewrite_shown != altered:
            card._advance_rewrite_preview()
        # Text tiles render the rich diff in their native prompt preview,
        # while image/video tiles display a compact overlay over the media.
        rich = (card.preview.text() if target.kind == "text"
                else card.rewrite_overlay.text())
        self.assertIn("#80e991", rich)
        self.assertEqual(card.rewrite_timer.interval(), 80)
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

        # The same text-diff animation also appears in a small overlay over
        # image/video thumbnails; no project content changes until validation.
        from ltx_prompt_director.models import Segment
        from ltx_prompt_director.ui import SegmentCard
        visual = Segment("Visual", "", "", "image", "start",
                         "Maintain the original camera", 3.0)
        media_card = SegmentCard(visual, 0.0, 115, 90)
        media_card.resize(230, 172)
        media_card.show()
        media_card.begin_rewrite_preview(
            visual.prompt, visual.prompt + "\nChange only the moonlight.")
        self.assertTrue(media_card.rewrite_active)
        self.assertEqual(media_card.rewrite_timer.interval(), 80)
        while media_card.rewrite_shown != media_card.rewrite_target:
            media_card._advance_rewrite_preview()
        self.assertIn("#80e991", media_card.rewrite_overlay.text())
        self.assertEqual(visual.prompt, "Maintain the original camera")
        media_card.stop_rewrite_preview()
        self.assertTrue(media_card.rewrite_overlay.isHidden())
        media_card.close()

    def test_combined_video_refinement_keeps_original_role_and_source(self):
        import json
        from ltx_prompt_director import segment_plan
        w = self.make_window("minimax_frames")
        # This test uses dummy external files. Skip writing a real project
        # archive on window teardown; the validation path is under test.
        w.save_library_project = lambda *args, **kwargs: None
        w.segments[0] = Segment(
            "Original footage", "/tmp/original-scene.mp4", "/tmp/thumb.png",
            "video", "end", "Original film direction", 3.0,
            id="7ef289b4-aa34-4389-82f8-fb1357862450")
        w.refresh_timeline(0)
        snapshot = list(w.segments)
        rows = [
            {"id": s.id, "name": s.name, "kind": s.kind, "role": s.role,
             "duration": s.duration, "prompt": s.prompt,
             "imagePrompt": s.image_prompt, "artworkPrompt": ""}
            for s in snapshot
        ]
        rows[0]["role"] = "text"  # model-output mistake, NOT a conversion
        rows[0]["prompt"] = "A smoother progression without camera cuts"
        parsed = segment_plan.normalize_plan(json.dumps({
            "prompt": "Updated unified direction", "segments": rows
        }), snapshot)
        w.ai_segment_snapshot = snapshot
        w.combined_refinement_finished(parsed)
        self.assertEqual(w.segments[0].id, snapshot[0].id)
        self.assertEqual(w.segments[0].kind, "video")
        self.assertEqual(w.segments[0].role, "end")
        self.assertEqual(w.segments[0].media_path, snapshot[0].media_path)
        self.assertEqual(w.segments[0].prompt, rows[0]["prompt"])
        self.assertEqual(w.minimax_prompt_text, "Updated unified direction")

    def test_segment_review_inline_diff_and_individual_deny(self):
        import copy
        w = self.make_window("minimax_frames")
        w.resize(1300, 820)
        w.show()
        self.app.processEvents()
        before = copy.deepcopy(w.segments)
        rows = [
            {"id": s.id, "name": s.name, "kind": s.kind, "role": s.role,
             "duration": s.duration, "prompt": s.prompt,
             "imagePrompt": s.image_prompt, "artworkPrompt": ""}
            for s in before
        ]
        rows[0]["prompt"] = before[0].prompt + "\nLioness shoulders gradually expand."
        rows[1]["prompt"] = before[1].prompt + "\nClaws extend naturally."
        w.ai_segment_snapshot = copy.deepcopy(before)
        w.combined_refinement_finished({
            "prompt": "Unified production direction", "segments": rows
        })
        changed = {before[0].id, before[1].id}
        self.assertTrue(changed.issubset(w.timeline_review_pending))
        card = w.timeline.itemWidget(w.timeline.item(0))
        self.assertTrue(card.review_pending)
        self.assertFalse(card.review_accept.isHidden())
        self.assertFalse(card.review_reject.isHidden())
        self.assertTrue(card.rewrite_active)
        self.assertEqual(card.rewrite_timer.interval(), 80)
        while card.rewrite_shown != card.rewrite_target:
            card._advance_rewrite_preview()
        rich = (card.preview.text() if card.segment.kind == "text"
                else card.rewrite_overlay.text())
        self.assertIn("#80e991", rich)
        # A pure addition does not falsely mark preserved words as deleted.
        self.assertNotIn("#9c3f4b", rich)
        w.resolve_segment_review(before[0].id, False)
        self.assertEqual(next(s.prompt for s in w.segments if s.id == before[0].id),
                         before[0].prompt)
        self.assertIn(before[1].id, w.timeline_review_pending)
        self.assertFalse(w.timeline_approve_all.isHidden())
        self.assertFalse(w.timeline_deny_all.isHidden())
        w.timeline_deny_all.setChecked(True)
        self.assertFalse(w.timeline_review_pending)
        self.assertEqual([(s.id, s.prompt) for s in w.segments],
                         [(s.id, s.prompt) for s in before])
        self.assertTrue(w.timeline_approve_all.isHidden())

    def test_timeline_approve_all_and_full_width_segment_progress(self):
        import copy
        w = self.make_window("minimax_frames")
        before = copy.deepcopy(w.segments)
        rows = [
            {"id": s.id, "name": s.name, "kind": s.kind, "role": s.role,
             "duration": s.duration, "prompt": s.prompt,
             "imagePrompt": s.image_prompt, "artworkPrompt": ""}
            for s in before
        ]
        rows[0]["prompt"] += " Additional film movement."
        w.ai_segment_snapshot = before
        w.combined_refinement_finished({
            "prompt": "Updated unified prompt", "segments": rows
        })
        self.assertIn(before[0].id, w.timeline_review_pending)
        w.timeline_approve_all.setChecked(True)
        self.assertFalse(w.timeline_review_pending)
        self.assertEqual(w.segments[0].prompt, rows[0]["prompt"])
        w.set_project_type("ltx")
        w.resize(1280, 820)
        w.show()
        self.app.processEvents()
        self.assertIs(w.segment_ai_marquee.parent(), w.prompt_surface.body)
        w.set_ai_controls_enabled(False)
        self.app.processEvents()
        self.assertFalse(w.segment_ai_marquee.isHidden())
        self.assertGreater(w.segment_ai_marquee.width(), 100)
        self.assertGreaterEqual(w.segment_ai_marquee.width(), w.prompt_surface.body.width() - 20)
        w.set_ai_controls_enabled(True)
        self.assertTrue(w.segment_ai_marquee.isHidden())

    def test_timeline_approve_all_clears_the_selected_segment_diff_projection(self):
        import copy
        from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
        for preview in ("paragraph", "stream"):
            with self.subTest(preview=preview):
                w = self.make_window("minimax_frames")
                before = copy.deepcopy(w.segments)
                after = "A completely revised segment action."
                w.segments[0].prompt = after
                w.prompt_scope.setCurrentIndex(1)
                w.refresh_timeline(0)
                w._start_segment_review(before, w.segments,
                                        [{"id": w.segments[0].id, "kind": "rewrite"}])
                editor = w.intent_segment_prompt
                if preview == "paragraph":
                    self.assertTrue(w.paragraph_review.begin(editor, before[0].prompt, after))
                else:
                    w.inline_typing.start(editor, before[0].prompt, "segment")
                    w.inline_typing.last_candidate = after
                    w.inline_typing.render()
                # Reproduce styling that survived a document swap even when
                # the authoritative prompt's characters already match.
                document = (w.paragraph_review.source_document if preview == "paragraph"
                            else w.inline_typing.original_document)
                stale = QTextCharFormat()
                stale.setFontStrikeOut(True)
                stale.setBackground(QColor("#9c3f4b"))
                cursor = QTextCursor(document)
                cursor.select(QTextCursor.SelectionType.Document)
                cursor.setCharFormat(stale)
                QTimer.singleShot(0, w.inline_typing.render)
                w.timeline_approve_all.setChecked(True)
                self.app.processEvents()
                self.assertFalse(w.timeline_review_pending)
                self.assertIsNone(w.paragraph_review.editor)
                self.assertFalse(w.inline_typing.active)
                self.assertEqual(editor.toPlainText(), after)
                cursor = QTextCursor(editor.document())
                cursor.movePosition(QTextCursor.MoveOperation.NextCharacter, QTextCursor.MoveMode.KeepAnchor)
                self.assertFalse(cursor.charFormat().fontStrikeOut())
                self.assertEqual(cursor.charFormat().background().style(), Qt.BrushStyle.NoBrush)
                self.assertFalse(editor.currentCharFormat().fontStrikeOut())
                self.assertEqual(editor.currentCharFormat().background().style(), Qt.BrushStyle.NoBrush)
                self.assertFalse(editor.extraSelections())
                w._close_saves_queued = True
                w.project_dirty = False
                w.close()

    def test_timeline_checkmark_resolves_segment_text_review_without_timeline_changes(self):
        for approve in (True, False):
            with self.subTest(approve=approve):
                w = self.make_window("minimax_frames")
                w.prompt_scope.setCurrentIndex(1)
                before = w.segments[0].prompt
                after = "Revised segment text."
                w.intent_segment_prompt.setPlainText(after)
                self.assertTrue(w.paragraph_review.begin(w.intent_segment_prompt, before, after))
                self.assertFalse(getattr(w, "timeline_review_pending", set()))
                button = w.timeline_approve_all if approve else w.timeline_deny_all
                button.setChecked(True)
                self.assertIsNone(w.paragraph_review.editor)
                expected = after if approve else before
                self.assertEqual(w.segments[0].prompt, expected)
                self.assertEqual(w.intent_segment_prompt.toPlainText(), expected)
                self.assertFalse(w.intent_segment_prompt.extraSelections())
                self.assertFalse(button.isChecked())

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

    def test_reference_changes_preserve_draft_during_generate_and_refine(self):
        for refine in (False, True):
            w = self.make_window('minimax_references')
            w.unified_prompt.setPlainText('Keep the authored draft')
            with (patch.object(w, 'ai_credentials', return_value=('chatgpt', 'gpt-6', 'unused')),
                  patch.object(w, 'start_ai_worker')):
                if refine:
                    w.refine_minimax_prompt()
                else:
                    w.generate_minimax_prompt_references()
            w.set_minimax_reference_image(0, {'name': 'New reference', 'role': 'identity',
                                             'image': 'data:image/png;base64,eA=='})
            w.minimax_h3_finished('Outdated result')
            self.assertEqual(w.minimax_prompt_text, 'Keep the authored draft')
            self.assertEqual(w.unified_prompt.toPlainText(), 'Keep the authored draft')
            self.assertEqual(w.pending_minimax_result, 'Outdated result')
            self.assertIn('references changed', w.minimax_panel.message_banner.text())
            # A fresh operation after the edit accepts its own unchanged snapshot.
            with (patch.object(w, 'ai_credentials', return_value=('chatgpt', 'gpt-6', 'unused')),
                  patch.object(w, 'start_ai_worker')):
                w.generate_minimax_prompt_references()
            w.minimax_h3_finished('Current result')
            self.assertEqual(w.minimax_prompt_text, 'Current result')

    def test_refine_uses_edited_prompt_and_notes_for_selected_mode(self):
        for kind in ('minimax_frames', 'minimax_references'):
            w = self.make_window(kind)
            w.unified_prompt.setPlainText('/refine-global Preserve my ending\nUser-edited production prompt')
            with patch.object(w, 'ai_credentials', return_value=(w.settings.value('provider', 'gemini'), w.settings.value('gemini_model', ai.GEMINI_MODELS[0]), 'unused')), patch.object(w, 'start_ai_worker') as worker:
                w.refine_minimax_prompt()
            self.assertEqual(worker.call_args.args[0].__name__,
                             'refine_minimax_h3_prompt' if kind == 'minimax_frames' else
                             'refine_minimax_h3_reference_prompt')
            self.assertIn('User-edited production prompt', worker.call_args.args[1][9])
            self.assertIn('/refine-global Preserve my ending', worker.call_args.args[1][9])
            self.assertEqual(w.minimax_operation_editor_snapshot, ('/refine-global Preserve my ending\nUser-edited production prompt', ''))
            w.minimax_h3_finished('Refined production prompt')
            self.assertEqual(w.unified_prompt.toPlainText(), 'Refined production prompt')
            self.assertFalse(hasattr(w.minimax_panel, 'instructions'))

    def test_combined_generate_and_refine_never_rewrite_timeline_for_any_provider(self):
        import json
        for provider, model in (("openai", "gpt-6"), ("gemini", "gemini-3.5-flash-lite")):
            for kind in ("minimax_frames", "minimax_references"):
                for refine in (False, True):
                    with self.subTest(provider=provider, kind=kind, refine=refine):
                        w = self.make_window(kind)
                        w.resize(1150, 850)
                        w.show()
                        self.app.processEvents()
                        w.unified_prompt.setPlainText("Existing production brief")
                        before = [segment.to_dict() for segment in w.segments]
                        with (patch.object(w, "ai_credentials", return_value=(provider, model, "key")),
                              patch.object(w.thread_pool, "start") as pool):
                            (w.combined_refine_button if refine else w.combined_generate_button).click()
                        worker = pool.call_args.args[0]
                        # Exercise the actual production rules and extractor. Even
                        # unsolicited timeline records cannot enter the segment path.
                        response = json.dumps({"prompt": "Updated production brief", "segments": []})
                        with patch.object(ai, "_provider_raw", return_value=response) as raw:
                            result = worker.operation(*worker.args)
                        self.assertIn(ai.UNIFIED_PROMPT_TRANSPORT, raw.call_args.args[4])
                        self.assertNotIn("ADDITIVE STRUCTURED SEGMENT JSON CONTRACT", raw.call_args.args[4])
                        self.assertFalse(getattr(w, "ai_plan_stream_active", False))
                        self.assertIs(w.ai_request_editor, w.unified_prompt)
                        worker.signals.finished.emit(result)
                        self.assertEqual(w.minimax_prompt_text, "Updated production brief")
                        self.assertEqual([segment.to_dict() for segment in w.segments], before)
                        self.assertIs(w.paragraph_review.editor, w.unified_prompt)
                        w.paragraph_review.finish()
                        w._close_saves_queued = True
                        w.project_dirty = False
                        w.close()


    def test_combined_generate_sends_every_current_split_edited_and_inserted_beat(self):
        import copy
        import json
        from tempfile import TemporaryDirectory
        from PIL import Image
        for kind in ("minimax_frames", "minimax_references"):
            with self.subTest(kind=kind), TemporaryDirectory() as folder:
                path = Path(folder) / "same-image.png"
                Image.new("RGB", (48, 32), "blue").save(path)
                w = self.make_window(kind)
                w._close_saves_queued = True
                w.resize(1150, 850)
                w.show()
                w.segments = [Segment("Checkpoint", str(path), str(path), "image", "start", "Old action", 4),
                              Segment("Removed", "", "", "text", "text", "DELETED SHOT", 1)]
                w.refresh_timeline(0)
                w.split_timeline_segment(w.segments[0].id, 2)
                first, last = w.segments[:2]
                first.prompt, last.prompt = "Current opening action.", "Current final action beyond the old stopping point."
                bridge = Segment("New bridge", "", "", "text", "text", "Current inserted action.", 1)
                w.segments = [first, bridge, last]
                w.refresh_timeline(0)
                w.global_prompt.setPlainText("Old guidance: stop before the inserted and final actions.")
                w.unified_prompt.setPlainText("OBSOLETE COMBINED DRAFT")
                w.minimax_prompt_cache_key = "obsolete cached source"
                w.prompt_scope.setCurrentIndex(1)
                self.assertTrue(w.paragraph_review.begin(w.intent_segment_prompt, "Old action", first.prompt))
                expected = copy.deepcopy([s.to_dict() for s in w.segments])
                with (patch.object(w, "ai_credentials", return_value=("gemini", "gemini-flash-lite-latest", "key")),
                      patch.object(w.thread_pool, "start") as pool):
                    w.combined_generate_button.click()
                worker = pool.call_args.args[0]
                self.assertEqual([s.id for s in worker.args[0]], [s.id for s in w.segments])
                self.assertTrue(all(a is not b for a, b in zip(worker.args[0], w.segments)))
                with patch.object(ai, "_provider_raw", return_value='{"prompt":"All current actions combined."}') as raw:
                    result = worker.operation(*worker.args)
                rules = raw.call_args.args[4]
                plan = json.loads(rules.rsplit("\n", 1)[1])
                self.assertEqual([r["id"] for r in plan], [s.id for s in w.segments])
                self.assertEqual([r["prompt"] for r in plan], [s.prompt for s in w.segments])
                self.assertEqual([(r["start"], r["end"]) for r in plan], [(0, 2), (2, 3), (3, 5)])
                self.assertEqual([r["segment_id"] for r in raw.call_args.args[0]], [s.id for s in w.segments])
                self.assertIn("override conflicting older global direction", rules)
                self.assertNotIn("DELETED SHOT", rules)
                self.assertNotIn("OBSOLETE COMBINED DRAFT", rules)
                worker.signals.finished.emit(result)
                self.assertEqual(w.minimax_prompt_text, "All current actions combined.")
                self.assertEqual([s.to_dict() for s in w.segments], expected)
                w.paragraph_review.finish()
                w.close()

    def test_combined_generate_holds_result_if_background_work_changes_the_timeline(self):
        w = self.make_window("minimax_frames")
        w.unified_prompt.setPlainText("Keep this draft")
        with (patch.object(w, "ai_credentials", return_value=("gemini", "gemini-flash-lite-latest", "key")),
              patch.object(w.thread_pool, "start") as pool):
            w.combined_generate_button.click()
        worker = pool.call_args.args[0]
        original = worker.args[0][0].prompt
        w.segments[0].prompt = "Background update after Generate was clicked"
        w.segments.append(Segment("New", "", "", kind="text", prompt="A newly inserted beat"))
        self.assertEqual(worker.args[0][0].prompt, original)
        self.assertEqual(len(worker.args[0]), 2)
        worker.signals.finished.emit("Old snapshot's generated result")
        self.assertEqual(w.minimax_prompt_text, "Keep this draft")
        self.assertEqual(w.pending_minimax_result, "Old snapshot's generated result")
        self.assertFalse(w.ai_busy)
        self.assertEqual(w.paragraph_review.pending_count, 0)

    def test_combined_generate_accepts_materialized_path_for_unchanged_archived_media(self):
        w = self.make_window("minimax_frames")
        w._close_saves_queued = True
        w.segments[0].kind = "image"
        w.segments[0].media_path = "/unmaterialized/source.png"
        w.segments[0]._media_digest = "unchanged original media digest"
        w.unified_prompt.setPlainText("Old draft")
        with (patch.object(w, "ai_credentials", return_value=("gemini", "model", "key")),
              patch.object(w.thread_pool, "start") as pool):
            w.combined_generate_button.click()
        w.segments[0].media_path = "/materialized/cache/source.png"
        pool.call_args.args[0].signals.finished.emit("Fresh draft from the same media")
        self.assertEqual(w.minimax_prompt_text, "Fresh draft from the same media")
        self.assertFalse(w.pending_minimax_result)

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
