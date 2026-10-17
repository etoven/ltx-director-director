"""Regression coverage for one AI control and non-destructive red typing previews."""
import os
import subprocess
import sys
import unittest

from ltx_prompt_director.ai_inline_diff import line_diff, select_streamed_text, rich_diff_html, typing_batch_size
from ltx_prompt_director.diff_review import paragraph_changes, reviewed_text


class LiveInlineDiffTests(unittest.TestCase):

    def test_paragraph_review_reject_keeps_other_paragraphs(self):
        before = "One original.\n\nTwo original.\n\nThree original."
        after = "One revised.\n\nTwo original.\n\nThree revised."
        changes = paragraph_changes(before, after)
        self.assertEqual(sum(c.decision == "pending" for c in changes), 2)
        self.assertEqual(reviewed_text(changes), after)
        changes[0].decision = "rejected"
        self.assertEqual(reviewed_text(changes), "One original.\n\nTwo original.\n\nThree revised.")

    def test_paragraph_review_is_compact_inside_editor(self):
        script = r'''
from PySide6.QtWidgets import QApplication, QTextEdit
from ltx_prompt_director.diff_review import ParagraphDiffReview
app = QApplication([])
editor = QTextEdit()
editor.resize(470, 400)
editor.show()
editor.setPlainText("First revised.\n\nSecond original.\n\nThird revised.")
review = ParagraphDiffReview()
assert review.begin(editor,
                    "First original.\n\nSecond original.\n\nThird original.",
                    editor.toPlainText())
app.processEvents()
assert review.pending_count == 2
assert review.approve_all.text().endswith("Approve all")
assert review.approve_all.parent() is editor
assert len(review.buttons) == 2
review.decide(0, False)
assert review.pending_count == 1
assert editor.toPlainText().startswith("First original.")
assert editor.toPlainText().endswith("Third revised.")
review.approve_all.setChecked(True)
assert review.editor is None
assert editor.toPlainText() == "First original.\n\nSecond original.\n\nThird revised."
editor.close()
'''
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        process = subprocess.run([sys.executable, "-c", script], timeout=30,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, env=env)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)

    def test_timeline_rich_diff_has_same_colors_and_typing_speed_as_prompt(self):
        self.assertEqual(typing_batch_size(200), 28)
        self.assertEqual(typing_batch_size(99999), 160)
        html = rich_diff_html("Older text\n", "New text\n")
        self.assertIn("#a7edbf", html)
        self.assertIn("#ed969e", html)
        self.assertIn("line-through", html)
        self.assertIn("New&nbsp;text", html)

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
assert cursor.charFormat().foreground().color().name().lower() == "#a7edbf"
preview.finish()
assert editor.toPlainText() == "Opening shot\nOld movement\nClosing beat"
editor.setFocus()
app.processEvents()
editor.setReadOnly(True)
preview.start(editor, editor.toPlainText())
preview.receive("output", '{"prompt":"Completely new words')
preview.render()
shown = editor.toPlainText()
QTest.keyClicks(editor, "X")
assert not preview.suspended
assert editor.toPlainText() == shown
preview.finish()
assert editor.toPlainText() == "Opening shot\nOld movement\nClosing beat"
editor.setReadOnly(False)
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
