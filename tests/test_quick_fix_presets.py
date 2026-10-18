import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QSettings

from ltx_prompt_director.quick_fixes import STARTERS, load_presets, save_presets, preset_groups


class QuickFixPresetTests(unittest.TestCase):
    def test_starters_are_installed_once_and_user_edits_and_empty_list_survive(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'settings.ini')
            settings = QSettings(path, QSettings.Format.IniFormat)
            self.assertEqual(load_presets(settings), STARTERS)
            custom = [{'name': 'My fix', 'target': 'segment', 'prompt': 'Preserve the red lantern.'}]
            save_presets(settings, custom)
            settings.sync()
            self.assertEqual(load_presets(QSettings(path, QSettings.Format.IniFormat)), [{**custom[0], 'category': 'General'}])
            save_presets(settings, [])
            settings.sync()
            self.assertEqual(load_presets(QSettings(path, QSettings.Format.IniFormat)), [])

    def test_categories_roundtrip_and_legacy_blank_or_invalid_categories_use_general(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = QSettings(str(Path(folder) / 'settings.ini'), QSettings.Format.IniFormat)
            entries = [dict(name='Camera', target='both', prompt='Track the subject.', category=' Camera '),
                       dict(name='Legacy', target='unified', prompt='Keep every beat.'),
                       dict(name='Blank', target='segment', prompt='Hold.', category='  '),
                       dict(name='Invalid', target='both', prompt='Keep.', category=['bad'])]
            save_presets(settings, entries)
            loaded = load_presets(settings)
            self.assertEqual([entry['category'] for entry in loaded], ['Camera', 'General', 'General', 'General'])
            self.assertEqual([entry['prompt'] for entry in loaded], [entry['prompt'] for entry in entries])
            save_presets(settings, loaded)
            settings.sync()
            self.assertEqual(load_presets(settings), loaded)

    def test_groups_filter_before_grouping_and_preserve_order_inside_categories(self):
        entries = [dict(name='Pan', target='both', prompt='Pan.', category='Camera'),
                   dict(name='Spacing', target='unified', prompt='Space.', category='Formatting'),
                   dict(name='Close-up', target='segment', prompt='Close.', category='camera'),
                   dict(name='Sound', target='both', prompt='SFX.', category='Audio')]
        segment = preset_groups(entries, 'segment')
        self.assertEqual([name for name, presets in segment], ['Camera', 'Audio'])
        self.assertEqual([item['name'] for item in segment[0][1]], ['Pan', 'Close-up'])
        self.assertEqual([name for name, presets in preset_groups(entries, 'unified')], ['Camera', 'Formatting', 'Audio'])
        self.assertEqual(preset_groups([], 'segment'), [])
