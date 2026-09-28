import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QTextEdit
from PySide6.QtGui import QAction
from ltx_prompt_director.inline_cues import cue_cells
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
        self.addCleanup(lambda: (setattr(window, "_close_saves_queued", True), setattr(window, "project_dirty", False), window.close()))
        window.segments = [Segment('A', '', '', 'text', 'text', 'First', 2), Segment('B', '', '', 'text', 'text', 'Second', 3)]
        window.refresh_timeline(0)
        window.set_project_type('minimax_frames')
        window.segment_prompt.setPlainText(compose_actions('[SCENE]\nOriginal.\n\n[SOUND]\nRain.', window.segments, ['Opening', 'Ending']))
        window.sync_timed_actions()
        self.assertEqual(len(cue_cells(window.segment_prompt)), 2)
        self.assertEqual(len(window.segment_prompt.extraSelections()), 2)
        self.assertFalse(hasattr(window.minimax_panel, 'actions_box'))
        self.assertIn('Opening', window.segment_prompt.toPlainText())
        self.assertEqual(window.segment_prompt.lineWrapMode(), QTextEdit.LineWrapMode.WidgetWidth)
        edited = window.segment_prompt.toPlainText().replace('Opening', 'Opening and moving naturally')
        window.segment_prompt.setPlainText(edited)
        window.sync_timed_actions()
        self.assertEqual(window.segments[0].prompt, 'Opening and moving naturally')
        self.assertEqual(cue_cells(window.segment_prompt)[window.segments[0].id], 'Opening and moving naturally')
        window.change_duration(window.segments[0].id, 4)
        self.assertIn('00:00:04:00 - 00:00:07:00: Ending', window.segment_prompt.toPlainText())
        self.assertIn('[SOUND]\nRain.', window.segment_prompt.toPlainText())
        self.assertIn("Timeline changed", window.minimax_panel.message_banner.text())

    def test_project_saves_only_on_toolbar_or_close(self):
        with patch.object(MainWindow, 'restore_startup_workspace'):
            window = MainWindow()
        self.addCleanup(lambda: (setattr(window, "_close_saves_queued", True), setattr(window, "project_dirty", False), window.close()))
        self.assertIsNone(getattr(window, 'save_library_button', None))
        window.segments = [Segment('A', '', '', 'text', 'text', 'Action', 2)]
        window.mark_dirty()
        self.assertTrue(window.project_dirty)
        self.assertFalse(any(action.text() == 'Save Current' for action in window.findChildren(QAction)))
        self.assertTrue(any(action.text() == 'Save to Library' for action in window.findChildren(QAction)))
        self.assertEqual(window._pending_disk_jobs, 0)
