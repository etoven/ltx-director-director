"""Splash asset and native prompt directive interactions."""
import unittest

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTextEdit

from ltx_prompt_director.inline_cues import PromptTextEdit, NOTE_TAG, cue_cells, insert_cue_cells
from ltx_prompt_director.models import Segment
from ltx_prompt_director.prompt_tags import install_prompt_tags, render_prompt_notes
from ltx_prompt_director.spellcheck import install_spellcheck, spellcheck_menu
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
        editor = PromptTextEdit()
        editor.show()
        install_spellcheck(editor)
        install_prompt_tags(editor)
        editor.setFocus()
        QTest.keyClicks(editor, 'First line')
        QTest.keyClick(editor, Qt.Key.Key_Return)
        QTest.keyClicks(editor, '/ref')
        self.app.processEvents()
        helper = editor._prompt_tag_suggestions
        self.assertGreater(helper.completer.completionCount(), 1)
        QTest.keyClick(editor, Qt.Key.Key_Tab)
        self.app.processEvents()
        note = editor.textCursor().currentTable()
        self.assertIsNotNone(note)
        self.assertEqual(note.format().property(NOTE_TAG), '/refine')
        self.assertEqual(note.cellAt(editor.textCursor()), note.cellAt(0, 0))
        QTest.keyClicks(editor, 'Keep the camera stable.')
        self.assertEqual(editor.toPlainText(), 'First line\n/refine Keep the camera stable.')
        editor.setTextCursor(note.cellAt(1, 0).firstCursorPosition())
        QTest.keyClick(editor, Qt.Key.Key_Delete)
        self.assertEqual(editor.toPlainText(), 'First line\n/refine Keep the camera stable.')
        helper.completer.popup().hide()
        editor.close()

    def test_notes_rehydrate_and_keep_multiline_body(self):
        editor = PromptTextEdit()
        original = 'Before\n/refine-global Keep the first line\n    and this second line\nAfter'
        editor.setPlainText(original)
        render_prompt_notes(editor)
        self.assertEqual(editor.toPlainText(), original)
        editor.setPlainText(editor.toPlainText())
        render_prompt_notes(editor)
        self.assertEqual(editor.toPlainText(), original)
        editor.close()

    def test_note_inside_timed_action_keeps_segment_identity(self):
        editor = PromptTextEdit()
        editor.inline_cue_mode = True
        segment = Segment('Beat', '', '', 'text', duration=2)
        prompt = ('[SCENE]\nContinuous shot.\n\n[TIMED ACTION]\n'
                  '00:00:00:00 - 00:00:02:00: She turns /refine Slowly, no camera cut.\n\n'
                  '[SOUND]\nWater.')
        self.assertTrue(insert_cue_cells(editor, prompt, [segment]))
        render_prompt_notes(editor)
        self.assertIn('/refine Slowly, no camera cut.', editor.toPlainText())
        self.assertEqual(list(cue_cells(editor)), [segment.id])
        self.assertIn('/refine Slowly, no camera cut.', cue_cells(editor)[segment.id])
        saved = editor.toPlainText()
        self.assertTrue(insert_cue_cells(editor, saved, [segment]))
        render_prompt_notes(editor)
        self.assertIn('/refine Slowly, no camera cut.', cue_cells(editor)[segment.id])
        editor.close()

    def test_note_can_be_removed_through_context_menu(self):
        editor = PromptTextEdit()
        editor.setPlainText('Before\n/refine Keep the lens fixed\nAfter')
        render_prompt_notes(editor)
        install_spellcheck(editor)
        editor.resize(500, 200)
        editor.show()
        self.app.processEvents()
        block = editor.document().firstBlock().next().next()
        cursor = QTextCursor(editor.document())
        cursor.setPosition(block.position())
        self.assertEqual(cursor.currentTable().format().property(NOTE_TAG), '/refine')
        menu = spellcheck_menu(editor, editor.cursorRect(cursor).center(), editor._spell_highlighter)
        action = next(action for action in menu.actions() if action.text() == 'Remove inline note')
        action.trigger()
        self.assertNotIn('/refine', editor.toPlainText())
        self.assertIn('After', editor.toPlainText())
        editor.close()


if __name__ == '__main__':
    unittest.main()
