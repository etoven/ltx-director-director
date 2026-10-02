import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

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
