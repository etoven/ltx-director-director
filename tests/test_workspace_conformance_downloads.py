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
        self.assertIn('minimax_full_reference_guide', values)

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
        w.unified_prompt.setPlainText('Custom draft')
        w.set_project_type('ltx')
        w.set_project_type('custom_ref')
        self.assertEqual(w.unified_prompt.toPlainText(), 'Custom draft')
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



    def test_active_definition_id_rename_keeps_its_draft(self):
        w = self.window()
        value = copy.deepcopy(w.workspace_definitions['minimax_frames'])
        value.update(id='before', name='Before')
        w.workspace_store.save(value)
        w.reload_workspace_definitions()
        w.set_project_type('before')
        w.unified_prompt.setPlainText('Retain this draft')
        value.update(id='after', name='After')
        w.workspace_store.save(value, 'before')
        w.reload_workspace_definitions(previous_id='before', new_id='after')
        self.assertEqual(w.project_type, 'after')
        self.assertEqual(w.unified_prompt.toPlainText(), 'Retain this draft')
