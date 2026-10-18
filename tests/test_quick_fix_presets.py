import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QSettings

from ltx_prompt_director.quick_fixes import STARTERS, load_presets, save_presets


class QuickFixPresetTests(unittest.TestCase):
    def test_starters_are_installed_once_and_user_edits_and_empty_list_survive(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'settings.ini')
            settings = QSettings(path, QSettings.Format.IniFormat)
            self.assertEqual(load_presets(settings), STARTERS)
            custom = [{'name': 'My fix', 'target': 'segment', 'prompt': 'Preserve the red lantern.'}]
            save_presets(settings, custom)
            settings.sync()
            self.assertEqual(load_presets(QSettings(path, QSettings.Format.IniFormat)), custom)
            save_presets(settings, [])
            settings.sync()
            self.assertEqual(load_presets(QSettings(path, QSettings.Format.IniFormat)), [])
