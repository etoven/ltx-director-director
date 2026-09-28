import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from ltx_prompt_director.ai import _extract_minimax_h3_prompt, AIResponseFormatError
from ltx_prompt_director.models import Segment
from ltx_prompt_director.timed_action import compose_actions, split_actions
from ltx_prompt_director.ui import MainWindow


class TimedActionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_structured_response_uses_authoritative_times_and_preserves_sections(self):
        segments = [Segment('A', '', '', 'text', 'text', '', 2.5), Segment('B', '', '', 'text', 'text', '', 3)]
        payload = {'prompt': '[SCENE]\nA scene.\n\n[TIMED ACTION]\n\n[SOUND]\nBirds.',
                   'timed_actions': ['Moves slowly.', 'Then stops.']}
        prompt = _extract_minimax_h3_prompt(json.dumps(payload), segments)
        self.assertIn('00:00:00:00 - 00:00:02:12: Moves slowly.', prompt)
        self.assertIn('00:00:02:12 - 00:00:05:12: Then stops.', prompt)
        self.assertIn('[SOUND]\nBirds.', prompt)
        descriptions, _ = split_actions(prompt)
        self.assertEqual(descriptions, payload['timed_actions'])
        with self.assertRaises(AIResponseFormatError):
            _extract_minimax_h3_prompt(json.dumps({**payload, 'timed_actions': ['Only one']}), segments)

    def test_timeline_retime_updates_prompt_and_cues_without_erasing_prose(self):
        with patch.object(MainWindow, 'restore_startup_workspace'):
            window = MainWindow()
        self.addCleanup(lambda: (window._autosave_timer.stop(), setattr(window, "project_dirty", False), window.close()))
        window.segments = [Segment('A', '', '', 'text', 'text', 'First', 2), Segment('B', '', '', 'text', 'text', 'Second', 3)]
        window.refresh_timeline(0)
        window.set_project_type('minimax_frames')
        window.segment_prompt.setPlainText(compose_actions('[SCENE]\nOriginal.\n\n[SOUND]\nRain.', window.segments, ['Opening', 'Ending']))
        window.sync_timed_actions()
        self.assertEqual(len(window.minimax_panel.action_editors), 2)
        window.change_duration(window.segments[0].id, 4)
        self.assertIn('00:00:04:00 - 00:00:07:00: Ending', window.segment_prompt.toPlainText())
        self.assertIn('[SOUND]\nRain.', window.segment_prompt.toPlainText())
        self.assertIn("Timeline changed", window.minimax_panel.message_banner.text())

    def test_project_controls_are_context_only_and_autosave_is_scheduled(self):
        with patch.object(MainWindow, 'restore_startup_workspace'):
            window = MainWindow()
        self.addCleanup(lambda: (window._autosave_timer.stop(), setattr(window, "project_dirty", False), window.close()))
        self.assertIsNone(getattr(window, 'save_library_button', None))
        window.segments = [Segment('A', '', '', 'text', 'text', 'Action', 2)]
        window.mark_dirty()
        self.assertTrue(window._autosave_timer.isActive())
