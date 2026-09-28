"""Splash asset and native prompt directive interactions."""
import unittest

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTextEdit

from ltx_prompt_director.prompt_tags import install_prompt_tags
from ltx_prompt_director.spellcheck import install_spellcheck
from ltx_prompt_director.splash import splash_pixmap


class SplashAndTagsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_splash_contains_artwork_and_offline_provider_marks(self):
        image = splash_pixmap().toImage()
        self.assertEqual((image.width(), image.height()), (980, 558))
        self.assertNotEqual(image.pixelColor(70, 80), image.pixelColor(800, 80))

    def test_slash_completion_preserves_text_and_editor_bindings(self):
        editor = QTextEdit()
        editor.show()
        install_spellcheck(editor)
        install_prompt_tags(editor)
        editor.setPlainText('First line\n/')
        editor.moveCursor(QTextCursor.MoveOperation.End)
        helper = editor._prompt_tag_suggestions
        helper.update_suggestions()
        self.assertGreater(helper.completer.completionCount(), 1)
        helper.insert_tag('/refine-global')
        self.assertEqual(editor.toPlainText(), 'First line\n/refine-global ')
        helper.completer.popup().hide()
        editor.close()


if __name__ == '__main__':
    unittest.main()
