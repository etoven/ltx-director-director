import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QCheckBox
from ltx_prompt_director import ai, ui, generic_ai
from ltx_prompt_director.models import Segment
from ltx_prompt_director.prompt_templates import stock_prompt
from ltx_prompt_director.workspaces import WorkspaceStore, validate_definition
from ltx_prompt_director.workspace_editor import WorkspaceEditor


class IntentGenericTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        store = WorkspaceStore(Path(folder.name))
        with patch.object(ui, 'WorkspaceStore', return_value=store), patch.object(ui.MainWindow, 'restore_startup_workspace'):
            w = ui.MainWindow()
        previous = w.settings.value('director_intent_expanded', True, bool)
        self.addCleanup(lambda: w.settings.setValue('director_intent_expanded', previous))
        self.addCleanup(lambda: (setattr(w, '_close_saves_queued', True), setattr(w, 'project_dirty', False), w.close()))
        w.segments = [Segment('Beat', '', '', 'text', 'text', 'Action', 3)]
        w.refresh_timeline(0)
        return w

    def generic(self, w, mode):
        value = copy.deepcopy(w.workspace_definitions['ltx' if mode == 'segmented' else 'minimax_frames'])
        value.update(id='generic_' + mode, name='Generic ' + mode, engine='generic', prompt_mode=mode)
        value['generation_prompts'] = {kind: stock_prompt('generic', kind) for kind in ['generate', 'refine']}
        w.workspace_store.save(value)
        w.reload_workspace_definitions()
        w.set_project_type(value['id'])
        return value

    def test_intent_checkbox_stays_collapsed_across_all_modes_and_refreshes(self):
        w = self.window()
        self.assertIsInstance(w.direction_toggle, QCheckBox)
        w.intent.setPlainText('Preserve this intent')
        w.direction_toggle.setChecked(False)
        for mode in ['minimax_frames', 'minimax_references', 'ltx']:
            w.set_project_type(mode)
            w.apply_project_type_ui()
            w.sync_minimax_panel()
            self.assertFalse(w.direction_toggle.isChecked())
            self.assertTrue(w.director_panel.isHidden())
            self.assertEqual(w.intent.toPlainText(), 'Preserve this intent')
        w.direction_toggle.setChecked(True)
        self.assertFalse(w.director_panel.isHidden())
        self.assertTrue(w.settings.value('director_intent_expanded', False, bool))

    def test_generic_is_editable_and_accepts_either_layout(self):
        w = self.window()
        editor = WorkspaceEditor(w.workspace_store, w)
        self.assertGreaterEqual(editor.engine.findData('generic'), 0)
        editor.engine.setCurrentIndex(editor.engine.findData('generic'))
        self.assertEqual(editor.generate.toPlainText(), stock_prompt('generic', 'generate'))
        for mode in ['unified', 'segmented']:
            value = self.generic(w, mode)
            self.assertEqual(validate_definition(value)['engine'], 'generic')

    def test_generic_generate_and_refine_route_without_switching_to_stock(self):
        w = self.window()
        for mode, generate, refine in [('unified', generic_ai.build_generic_prompt, generic_ai.refine_generic_prompt),
                                        ('segmented', generic_ai.build_generic_segments, generic_ai.refine_generic_segment)]:
            self.generic(w, mode)
            if mode == 'unified':
                w.segment_prompt.setPlainText('[SCENE]\nAction.')
            with patch.object(w, 'ai_credentials', return_value=('gemini', 'model', 'key')), patch.object(w, 'start_ai_worker') as worker:
                w.generate_project_prompt()
                self.assertIs(worker.call_args.args[0], generate)
                w.refine_project_prompt()
                self.assertIs(worker.call_args.args[0], refine)
                if mode == 'unified':
                    w.retry_minimax_operation()
                    self.assertEqual(w.workspace_engine, 'generic')

    def test_generic_requests_use_neutral_template_and_parse_both_transports(self):
        segments = [Segment('Beat', '', '', 'text', 'text', 'Action', 3)]
        response = {'prompt': '[SCENE]\nAction.\n\n[TIMED ACTION]', 'timed_actions': [
            {'start': '00:00:00:00', 'end': '00:00:03:00', 'action': 'Move.'}]}
        with patch.object(ai, '_provider_raw', return_value=json.dumps(response)) as provider:
            prompt = generic_ai.build_generic_prompt(segments, 'gemini', 'model', 'key', 'Keep the camera still', '', False, False, False)
            rules = provider.call_args.args[4]
            self.assertIn('Keep the camera still', rules)
            self.assertNotIn('LTXDirector', rules)
            self.assertNotIn('MiniMax H3', rules)
            self.assertIn('00:00:00:00 - 00:00:03:00: Move.', prompt)
        with patch.object(ai, '_provider_raw', return_value=json.dumps({'segments': [{'duration': 4, 'prompt': 'Move.'}], 'globalPrompt': ''})):
            result = generic_ai.build_generic_segments(segments, 'gemini', 'model', 'key', '', False, False, False, False)
            self.assertEqual(result['segments'][0]['duration'], 4)
        with patch.object(ai, '_provider_raw', return_value=json.dumps({'prompt': 'Improved.', 'duration': 4})):
            result = generic_ai.refine_generic_segment(segments, 0, 'gemini', 'model', 'key', '', 3)
            self.assertEqual(result['prompt'], 'Improved.')

    def test_generic_bad_response_uses_normal_validation_errors(self):
        segments = [Segment('Beat', '', '', 'text', 'text', 'Action', 3)]
        with patch.object(ai, '_provider_raw', return_value='{"segments": []}'):
            with self.assertRaises(ai.AIResponseFormatError):
                generic_ai.build_generic_segments(segments, 'gemini', 'model', 'key', '', False, False, False, False)
