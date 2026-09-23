import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QMessageBox

from ltx_prompt_director.models import Segment
from ltx_prompt_director.ui import MainWindow, MiniMaxPromptWindow


class _EditorOwner(QMainWindow):
    def __init__(self, settings_path: Path):
        super().__init__()
        self.settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
        self.prompt = ""
        self.instructions_text = ""
        self.window = None

    def minimax_editor_changed(self):
        if self.window:
            self.prompt = self.window.editor.toPlainText()
            self.instructions_text = self.window.instructions.toPlainText()

    def refine_minimax_prompt(self):
        pass

    def copy_minimax_prompt(self):
        pass

    def retry_minimax_operation(self):
        pass

    def minimax_window_destroyed(self, _window=None):
        pass


class MiniMaxEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_prompt_and_instructions_accept_plain_text_paste_even_while_busy(self):
        with tempfile.TemporaryDirectory() as directory:
            owner = _EditorOwner(Path(directory) / "settings.ini")
            window = MiniMaxPromptWindow(owner)
            owner.window = window

            window.set_busy(True, "Refining prompt • attempt 1/3")
            self.assertFalse(window.editor.isReadOnly())
            self.assertFalse(window.instructions.isReadOnly())
            self.assertTrue(window.editor.textInteractionFlags() & Qt.TextInteractionFlag.TextEditable)
            self.assertFalse(window.busy_veil.isHidden())
            self.assertEqual(window.busy_veil.status.text(), "Refining prompt • attempt 1/3")

            QApplication.clipboard().setText("Pasted production prompt")
            window.editor.paste()
            QApplication.clipboard().setText("Private refinement direction")
            window.instructions.paste()

            self.assertEqual(owner.prompt, "Pasted production prompt")
            self.assertEqual(owner.instructions_text, "Private refinement direction")
            self.assertFalse(window.refine_button.isEnabled())
            window.set_busy(False)
            self.assertTrue(window.busy_veil.isHidden())
            self.assertTrue(window.refine_button.isEnabled())
            window.close()
            owner.close()

    def test_show_focuses_existing_prompt_so_space_edits_instead_of_reopening_cache(self):
        window = MainWindow()
        window.minimax_prompt_text = "Existing cached prompt"
        dialog = window.show_minimax_prompt_window("Cached • timeline current")
        QApplication.processEvents()

        self.assertIs(QApplication.focusWidget(), dialog.editor)
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Space)
        self.assertEqual(dialog.editor.toPlainText(), "Existing cached prompt ")
        self.assertEqual(window.minimax_prompt_text, "Existing cached prompt ")

        dialog.close()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QApplication.processEvents()
        window.close()

    def test_pacing_header_uses_actual_previews_and_exact_start_times(self):
        with tempfile.TemporaryDirectory() as directory:
            preview_path = Path(directory) / "frame.png"
            pixmap = QPixmap(80, 45)
            pixmap.fill(QColor("#4f9fbd"))
            self.assertTrue(pixmap.save(str(preview_path)))
            owner = _EditorOwner(Path(directory) / "settings.ini")
            owner.segments = [
                Segment("Opening", str(preview_path), str(preview_path), kind="image", duration=2.5),
                Segment("Transformation beat", "", "", kind="text", role="text", duration=3.25),
                Segment("Motion reference", "clip.webm", str(preview_path), kind="video", duration=1.5),
            ]
            window = MiniMaxPromptWindow(owner)
            owner.window = window
            window.set_project("Pacing test", "Prompt", "", "Saved prompt")

            self.assertEqual(
                [card.findChild(QLabel, "minimaxPacingTime").text() for card in window.pacing_strip.cards],
                ["START  00:00:000", "START  00:02:500", "START  00:05:750"],
            )
            self.assertEqual(window.pacing_strip.total_time.text(), "TOTAL  00:07:250")
            self.assertFalse(window.pacing_strip.cards[0].preview.pixmap().isNull())
            self.assertFalse(window.pacing_strip.cards[2].preview.pixmap().isNull())
            self.assertIn("TEXT SEQUENCE", window.pacing_strip.cards[1].preview.text())
            window.close()
            owner.close()

    def test_refine_snapshots_edited_prompt_and_special_instructions(self):
        window = MainWindow()
        dialog = window.ensure_minimax_prompt_window()
        window.segments = [Segment("Beat", "", "", kind="text", prompt="Motion", duration=2.5)]
        window.current_project_id = "test-project"
        captured = {}

        def capture_worker(operation, args, activity, finished, **kwargs):
            captured["operation"] = operation
            captured["args"] = args
            captured["kwargs"] = kwargs

        with (
            patch.object(window, "ai_credentials", return_value=("gemini", "gemini-3.5-flash-lite", "unused")),
            patch.object(window, "current_minimax_cache_key", return_value="source-signature"),
            patch.object(window, "save_library_project") as save_project,
            patch.object(window, "start_ai_worker", side_effect=capture_worker),
        ):
            dialog.editor.setPlainText("User-pasted production prompt")
            dialog.instructions.setPlainText("Preserve the new ending and smooth the final transition.")

            self.assertFalse(captured)
            save_project.assert_not_called()

            dialog.refine_button.click()

            save_project.assert_not_called()
            window.minimax_h3_finished("Refined production prompt")
            save_project.assert_not_called()
            self.assertEqual(window.minimax_refinement_instructions, "")
            self.assertEqual(dialog.instructions.toPlainText(), "")
            dialog.close()
            QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            QApplication.processEvents()
            save_project.assert_called_once_with(automatic=True)

        self.assertEqual(captured["args"][-3], "User-pasted production prompt")
        self.assertEqual(captured["args"][-2], "Preserve the new ending and smooth the final transition.")
        self.assertFalse(captured["kwargs"]["show_main_overlay"])
        self.assertFalse(hasattr(window, "_minimax_save_timer"))
        self.assertEqual(
            window.minimax_operation_editor_snapshot,
            ("User-pasted production prompt", "Preserve the new ending and smooth the final transition."),
        )
        window.current_project_id = None
        window.close()

    def test_gemini_overload_uses_clear_warning_dialog(self):
        window = MainWindow()
        window.ai_activity_title = "Magic Build"
        message = "Google Gemini is temporarily overloaded for the selected model."
        with (
            patch.object(QMessageBox, "warning") as warning,
            patch.object(QMessageBox, "critical") as critical,
        ):
            window.magic_failed(message)
        warning.assert_called_once_with(window, "Magic Build: Gemini overloaded", message)
        critical.assert_not_called()
        self.assertIn("Google Gemini is overloaded", window.statusBar().currentMessage())
        window.close()

    def test_minimax_failures_stay_in_the_minimax_dialog(self):
        window = MainWindow()
        dialog = window.ensure_minimax_prompt_window()
        window.ai_activity_title = "MiniMax H3 Refine"
        message = "The AI returned a skimpy MiniMax interval.\n\nStopped after 3 attempts."
        with (
            patch.object(QMessageBox, "warning") as warning,
            patch.object(QMessageBox, "critical") as critical,
        ):
            window.magic_failed(message)

        warning.assert_not_called()
        critical.assert_not_called()
        self.assertEqual(dialog.message_banner.text(), message)
        self.assertEqual(dialog.message_panel.property("level"), "error")
        self.assertFalse(dialog.message_panel.isHidden())
        self.assertFalse(dialog.retry_button.isHidden())
        self.assertTrue(dialog.busy_veil.isHidden())
        dialog.close()
        window.close()

    def test_exhausted_minimax_validation_keeps_prompt_and_marks_short_tile(self):
        window = MainWindow()
        window.segments = [
            Segment("Detailed beat", "", "", kind="text", role="text", duration=2.5),
            Segment("Short beat", "", "", kind="text", role="text", duration=2.5),
        ]
        window.ai_activity_title = "MiniMax H3 Export"
        window.minimax_operation_kind = "generate"
        dialog = window.show_minimax_prompt_window("Generating…")
        candidate = "continuous_video:\n00:00:000 Detailed action. Physical response.\n00:02:500 Too short.\nsoundscape:\nNone.\nmusic:\nNone."

        window.magic_failed({
            "message": "The final interval remained under-detailed.\n\nStopped after 3 attempts.",
            "candidate_prompt": candidate,
            "warning_indices": [1],
        })

        self.assertEqual(window.minimax_prompt_text, candidate)
        self.assertEqual(dialog.editor.toPlainText(), candidate)
        self.assertFalse(dialog.retry_button.isHidden())
        self.assertTrue(dialog.pacing_strip.cards[0].warning_icon.isHidden())
        self.assertFalse(dialog.pacing_strip.cards[1].warning_icon.isHidden())
        self.assertTrue(dialog.pacing_strip.cards[1].property("warning"))
        dialog.close()
        window.close()

    def test_retry_button_restarts_the_failed_minimax_operation(self):
        window = MainWindow()
        dialog = window.ensure_minimax_prompt_window()
        window.minimax_operation_kind = "generate"
        window.minimax_prompt_cache_key = "stale-cache"
        window.minimax_warning_indices = [0]
        dialog.show_message("Generation failed.", "error", retry=True)

        with patch.object(window, "export_minimax_h3") as retry_generation:
            dialog.retry_button.click()

        retry_generation.assert_called_once_with()
        self.assertEqual(window.minimax_prompt_cache_key, "")
        self.assertEqual(window.minimax_warning_indices, [])
        dialog.close()
        window.close()


if __name__ == "__main__":
    unittest.main()
