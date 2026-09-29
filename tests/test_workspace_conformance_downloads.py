import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QKeyEvent, QTextCursor
from PySide6.QtWidgets import QApplication, QMessageBox

from ltx_prompt_director import ai, ui
from ltx_prompt_director.inline_cues import cue_cells, CUE_ID
from ltx_prompt_director.models import Segment
from ltx_prompt_director.timed_action import parse_timed_plan
from ltx_prompt_director.workspaces import WorkspaceStore, validate_definition
from ltx_prompt_director.workspace_editor import WorkspaceEditor

PROMPT = '[SCENE]\nA continuous shot.\n\n[TIMED ACTION]\n00:00:00:00 - 00:00:03:00: First action.\n\n00:00:03:00 - 00:00:07:12: Second action.\n\n[SOUND]\nRain.'


class WorkspaceConformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        store = WorkspaceStore(Path(folder.name) / 'workspaces')
        with patch.object(ui, 'WorkspaceStore', return_value=store), patch.object(ui.MainWindow, 'restore_startup_workspace'):
            window = ui.MainWindow()
        self.addCleanup(lambda: (setattr(window, '_close_saves_queued', True), setattr(window, 'project_dirty', False), window.close()))
        window.download_directory = str(Path(folder.name) / 'downloads')
        window.segments = [Segment('First.png', '/preserved/first.png', '', 'image', 'start', 'Old', 2),
                           Segment('Second', '', '', 'text', 'text', 'Old 2', 3)]
        window.refresh_timeline(0)
        window.set_project_type('minimax_frames')
        return window

    def settle(self, window):
        window.duration_animation.setCurrentTime(window.duration_animation.duration())
        self.app.processEvents()

    def test_paste_retimes_preserves_media_ids_and_adds_extra_segment(self):
        w = self.window()
        ids = [segment.id for segment in w.segments]
        prompt = PROMPT.replace('\n\n[SOUND]', '\n\n00:00:07:12 - 00:00:09:00: Third action.\n\n[SOUND]')
        mime = QMimeData()
        mime.setText(prompt)
        w.segment_prompt.selectAll()
        w.segment_prompt.insertFromMimeData(mime)
        self.settle(w)
        self.assertEqual([segment.id for segment in w.segments[:2]], ids)
        self.assertEqual(w.segments[0].media_path, '/preserved/first.png')
        self.assertEqual([segment.duration for segment in w.segments], [3, 4.5, 1.5])
        self.assertEqual(w.segments[2].kind, 'text')
        self.assertEqual(len(cue_cells(w.segment_prompt)), 3)
        self.assertIn('[SOUND]\nRain.', w.segment_prompt.toPlainText())
        self.assertEqual(w.duration_animation.duration(), 950)

    def test_keyboard_paste_can_replace_entire_protected_document(self):
        w = self.window()
        w.conform_timed_prompt(PROMPT)
        w.segment_prompt.selectAll()
        QApplication.clipboard().setText(PROMPT.replace('03:00', '04:00'))
        event = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        w.segment_prompt.keyPressEvent(event)
        self.settle(w)
        self.assertEqual(w.segments[0].duration, 4)
        self.assertEqual(len(cue_cells(w.segment_prompt)), 2)

    def test_partial_match_disables_only_unmatched_segments_then_reconnects(self):
        w = self.window()
        bad = PROMPT.replace('00:00:03:00 - 00:00:07:12', '00:00:04:00 - 00:00:07:12')
        w.conform_timed_prompt(bad)
        self.settle(w)
        self.assertFalse(w.segments[0].prompt_detached)
        self.assertTrue(w.segments[1].prompt_detached)
        self.assertFalse(w.timeline.item(1).flags() & Qt.ItemFlag.ItemIsEnabled)
        self.assertEqual(w.timeline.itemWidget(w.timeline.item(1)).graphicsEffect().opacity(), 0.4)
        self.assertEqual(set(cue_cells(w.segment_prompt)), {w.segments[0].id})
        w.conform_timed_prompt(PROMPT)
        self.settle(w)
        self.assertFalse(any(segment.prompt_detached for segment in w.segments))
        self.assertTrue(w.timeline.item(1).flags() & Qt.ItemFlag.ItemIsEnabled)
        self.assertEqual(len(cue_cells(w.segment_prompt)), 2)

    def test_missing_or_malformed_cues_detach_without_discarding_media(self):
        w = self.window()
        malformed = PROMPT.replace('00:00:03:00 - 00:00:07:12', '00:00:03:99 - 00:00:07:12')
        w.conform_timed_prompt(malformed)
        self.assertTrue(w.segments[1].prompt_detached)
        w.conform_timed_prompt('[SCENE]\nNo timed section.')
        self.assertTrue(all(segment.prompt_detached for segment in w.segments))
        self.assertEqual(w.segments[0].media_path, '/preserved/first.png')

    def test_click_focuses_and_highlights_complete_multiline_action(self):
        w = self.window()
        w.conform_timed_prompt(PROMPT.replace('Second action.', 'Second action.\nA second line.'))
        self.settle(w)
        w.timeline.setCurrentRow(1)
        w.reload_clicked_segment(w.timeline.item(1))
        self.assertEqual(w.segment_prompt.textCursor().currentTable().format().property(CUE_ID), w.segments[1].id)
        selections = w.segment_prompt.extraSelections()
        self.assertIn('A second line.', selections[0].cursor.selectedText())

    def test_refinement_result_reparses_new_timed_segments(self):
        w = self.window()
        w.minimax_operation_signature = w.current_minimax_cache_key()
        w.minimax_operation_editor_snapshot = ('', '')
        w.minimax_operation_kind = 'refine_frames'
        prompt = PROMPT.replace('\n\n[SOUND]', '\n\n00:00:07:12 - 00:00:08:00: Finish.\n\n[SOUND]')
        w.minimax_h3_finished(prompt)
        self.settle(w)
        self.assertEqual(len(w.segments), 3)
        self.assertEqual(w.segments[-1].duration, .5)
        self.assertFalse(w.ai_busy)
        self.assertEqual(len(cue_cells(w.segment_prompt)), 3)

    def test_structured_ai_times_accept_new_intervals_and_reject_bad_frames(self):
        w = self.window()
        response = {'prompt': '[SCENE]\nShot.\n\n[TIMED ACTION]\n\n[SOUND]\nRain.', 'timed_actions': [
            {'start': '00:00:00:00', 'end': '00:00:03:00', 'action': 'First'},
            {'start': 3, 'end': 6, 'action': 'Second'}, {'start': 6, 'end': 8, 'action': 'Third'}]}
        result = ai._extract_minimax_h3_prompt(json.dumps(response), w.segments)
        self.assertEqual(len(parse_timed_plan(result)), 3)
        response['timed_actions'][0]['end'] = '00:00:03:24'
        with self.assertRaises(ai.AIResponseFormatError):
            ai._extract_minimax_h3_prompt(json.dumps(response), w.segments)

    def test_definitions_restore_preserves_custom_and_respects_deleted_stock(self):
        w = self.window()
        store = w.workspace_store
        custom = copy.deepcopy(store.load()['minimax_frames'])
        custom.update(id='custom', name='Custom workspace')
        store.save(custom)
        edited = copy.deepcopy(store.load()['ltx'])
        edited['name'] = 'Edited LTX'
        store.save(edited)
        store.delete('minimax_references')
        self.assertNotIn('minimax_references', WorkspaceStore(store.root).load())
        store.restore_stock()
        values = store.load()
        self.assertIn('custom', values)
        self.assertEqual(values['ltx']['name'], 'LTX Video')
        self.assertIn('minimax_references', values)

    def test_custom_definition_routes_layout_and_prompt_instructions(self):
        w = self.window()
        custom = copy.deepcopy(w.workspace_definitions['minimax_references'])
        custom.update(id='custom', name='Custom Ref')
        custom['audio_generation'] = False
        custom['references']['untimed_slots'] = 1
        custom['generation_prompts']['generate'] = 'Use a continuous wide shot.'
        w.workspace_store.save(custom)
        w.reload_workspace_definitions()
        w.set_project_type('custom')
        self.assertTrue(w.unified_workspace)
        self.assertEqual(w.workspace_engine, 'minimax_references')
        self.assertEqual(w.minimax_prompt_mode, 'references')
        self.assertTrue(w.sfx.isHidden())
        self.assertTrue(w.minimax_panel.reference_targets[1].isHidden())
        token = ai.WORKSPACE_INSTRUCTIONS.set(custom['generation_prompts'])
        try:
            self.assertIn('Use a continuous wide shot.', ai._minimax_h3_reference_rules(w.segments, '', '', False, False, False))
        finally:
            ai.WORKSPACE_INSTRUCTIONS.reset(token)
        self.assertIn('custom', w.project_payload()['workspaceDefinition']['id'])

    def test_definition_editor_renames_exports_imports_and_preserves_custom_draft(self):
        w = self.window()
        editor = WorkspaceEditor(w.workspace_store, w)
        editor.new()
        editor.identifier.setText('custom_ref')
        editor.name.setText('My Ref')
        editor.engine.setCurrentIndex(editor.engine.findData('minimax_frames'))
        editor.mode.setCurrentIndex(editor.mode.findData('unified'))
        editor.save()
        w.set_project_type('custom_ref')
        w.segment_prompt.setPlainText('Custom draft')
        w.set_project_type('ltx')
        w.set_project_type('custom_ref')
        self.assertEqual(w.segment_prompt.toPlainText(), 'Custom draft')
        editor.name.setText('Renamed')
        editor.save()
        self.assertEqual(w.workspace_definitions['custom_ref']['name'], 'Renamed')
        editor.export()
        exported = w.export_directory() / 'Renamed.workspace.json'
        self.assertTrue(exported.exists())
        w.workspace_store.delete('custom_ref')
        with patch('ltx_prompt_director.workspace_editor.QFileDialog.getOpenFileName', return_value=(str(exported), '')):
            editor.import_definition()
        self.assertIn('custom_ref', w.workspace_definitions)
        editor.restore()
        self.assertIn('custom_ref', w.workspace_definitions)

    def test_workspace_validation_prevents_path_traversal_and_inconsistent_layout(self):
        w = self.window()
        value = copy.deepcopy(w.workspace_definitions['ltx'])
        value['id'] = '../outside'
        with self.assertRaises(ValueError):
            validate_definition(value)
        value['id'] = 'valid'
        value['prompt_mode'] = 'unified'
        with self.assertRaises(ValueError):
            validate_definition(value)

    def test_export_paths_share_folder_collision_suffix_and_history(self):
        w = self.window()
        first = w.next_export_path('Movie.mp4')
        first.write_bytes(b'video')
        second = w.next_export_path('Movie.mp4')
        third = w.next_export_path('Movie.mp4')
        self.assertEqual(second.name, 'Movie (1).mp4')
        self.assertEqual(third.name, 'Movie (2).mp4')
        self.assertEqual(first.parent, w.export_directory())
        w.record_export(first)
        self.assertEqual(w.download_tray.history[0]['path'], str(first.resolve()))
        self.assertEqual(w.download_tray.list.item(0).data(Qt.ItemDataRole.UserRole), str(first.resolve()))
        w.toggle_downloads()
        self.assertTrue(w.download_tray.isVisible())
        w.toggle_downloads()
        self.assertFalse(w.download_tray.isVisible())
        self.assertEqual(w.project_payload()['downloadDirectory'], w.download_directory)

    def test_export_project_uses_background_archive_and_no_filename_dialog(self):
        w = self.window()
        w.current_project_name = 'Export Test'
        w.segments[0].kind = 'text'
        w.segments[0].media_path = ''
        with patch.object(ui, 'choose_document_save', side_effect=AssertionError('Must not ask for filenames')):
            w.export_project()
            deadline = time.monotonic() + 10
            while w._pending_disk_jobs and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
        self.assertEqual(w._pending_disk_jobs, 0)
        path = w.export_directory() / 'Export Test.LTXD'
        self.assertTrue(path.is_file())
        self.assertEqual(w.download_tray.history[0]['path'], str(path.resolve()))


    def test_frame_precision_survives_conformance_and_project_roundtrip(self):
        w = self.window()
        lines = [f"00:00:00:{index:02d} - 00:00:00:{index + 1:02d}: Action {index}." for index in range(20)]
        w.conform_timed_prompt('[TIMED ACTION]\n' + '\n\n'.join(lines))
        self.settle(w)
        self.assertAlmostEqual(w.total_duration(), 20 / 24, places=4)
        payload = w.project_payload(include_media=False)
        value = Segment.from_dict(payload['frames'][0])
        self.assertAlmostEqual(value.duration, 1 / 24, places=5)
        w.change_duration(w.segments[-1].id, 1)
        self.assertIn('00:00:00:19 -', w.minimax_prompt_text)

    def test_active_definition_id_rename_keeps_its_draft(self):
        w = self.window()
        value = copy.deepcopy(w.workspace_definitions['minimax_frames'])
        value.update(id='before', name='Before')
        w.workspace_store.save(value)
        w.reload_workspace_definitions()
        w.set_project_type('before')
        w.segment_prompt.setPlainText('Retain this draft')
        value.update(id='after', name='After')
        w.workspace_store.save(value, 'before')
        w.reload_workspace_definitions(previous_id='before', new_id='after')
        self.assertEqual(w.project_type, 'after')
        self.assertEqual(w.segment_prompt.toPlainText(), 'Retain this draft')

    def test_conformed_cue_tables_remain_non_deletable(self):
        w = self.window()
        w.conform_timed_prompt(PROMPT)
        before = w.segment_prompt.toPlainText()
        w.segment_prompt.selectAll()
        w.segment_prompt.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(w.segment_prompt.toPlainText(), before)
        self.assertEqual(len(cue_cells(w.segment_prompt)), 2)
