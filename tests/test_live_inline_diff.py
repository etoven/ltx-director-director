"""Regression coverage for one AI control and non-destructive red typing previews."""
import os
import subprocess
import sys
import unittest

from ltx_prompt_director.ai_inline_diff import line_diff, select_streamed_text


class LiveInlineDiffTests(unittest.TestCase):
    def test_only_changed_sections_are_marked_not_entire_original(self):
        base = "subject_definitions\nPreserve identity\n\n00:00:01:00 - 00:00:02:00\nOriginal motion\n"
        new = "subject_definitions\nPreserve identity\n\n00:00:01:00 - 00:00:02:00\nRevised motion\n"
        result = line_diff(base, new)
        self.assertIn(("equal", "subject_definitions\nPreserve identity\n\n00:00:01:00 - 00:00:02:00\n"), result)
        self.assertIn(("removed", "Original motion\n"), result)
        self.assertIn(("added", "Revised motion\n"), result)

    def test_partial_prompt_stream_decodes_text_without_json_wrappers(self):
        source = '{"prompt":"subject_definitions\\n<Subject 1> is now'
        self.assertEqual(
            select_streamed_text(source, "unified"),
            "subject_definitions\n<Subject 1> is now",
        )

    def test_selected_segment_diff_uses_selected_json_segment(self):
        data = '{"segments":[{"prompt":"Opening"},{"prompt":"Ending transformation"}]}'
        self.assertEqual(select_streamed_text(data, "segment", 1), "Ending transformation")

    def test_editor_red_preview_is_ephemeral_and_user_can_take_over(self):
        # Isolate the Qt GUI app from other test modules and avoid headless
        # Qt C-extension interpreter shutdown issues.
        script = r'''
import os
from PySide6.QtWidgets import QApplication, QTextEdit
from PySide6.QtTest import QTest
from ltx_prompt_director.ai_inline_diff import LiveTypingPreview
app = QApplication([])
editor = QTextEdit()
editor.show()
app.processEvents()
editor.setPlainText("Opening shot\nOld movement\nClosing beat")
counter = [0]
editor.textChanged.connect(lambda: counter.__setitem__(0, counter[0] + 1))
preview = LiveTypingPreview()
preview.start(editor, editor.toPlainText())
preview.receive("output", '{"prompt":"Opening shot\\nNew movement')
for _ in range(12):
    preview.render()
shown = editor.toPlainText()
assert "Opening shot" in shown, shown
assert "Old movement" in shown, shown
assert "New movement" in shown, shown
assert counter[0] == 0, counter
cursor = editor.document().find("New movement")
assert not cursor.isNull()
assert cursor.charFormat().foreground().color().name().lower() == "#ff696e"
preview.finish()
assert editor.toPlainText() == "Opening shot\nOld movement\nClosing beat"
editor.setFocus()
app.processEvents()
preview.start(editor, editor.toPlainText())
preview.receive("output", '{"prompt":"Completely new words')
preview.render()
QTest.keyClicks(editor, "X")
assert preview.suspended
assert "Completely new words" not in editor.toPlainText()
assert "Old movement" in editor.toPlainText()
preview.finish()
editor.close()
os._exit(0)
'''
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        process = subprocess.run(
            [sys.executable, "-c", script], timeout=30,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        )
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)


if __name__ == "__main__":
    unittest.main()
