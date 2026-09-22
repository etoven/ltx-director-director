import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QMainWindow

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

    def minimax_window_closed(self):
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

            window.set_busy(True)
            self.assertFalse(window.editor.isReadOnly())
            self.assertFalse(window.instructions.isReadOnly())
            self.assertTrue(window.editor.textInteractionFlags() & Qt.TextInteractionFlag.TextEditable)

            QApplication.clipboard().setText("Pasted production prompt")
            window.editor.paste()
            QApplication.clipboard().setText("Private refinement direction")
            window.instructions.paste()

            self.assertEqual(owner.prompt, "Pasted production prompt")
            self.assertEqual(owner.instructions_text, "Private refinement direction")
            self.assertFalse(window.refine_button.isEnabled())
            window.set_busy(False)
            self.assertTrue(window.refine_button.isEnabled())
            window.close()
            owner.close()

    def test_refine_snapshots_edited_prompt_and_special_instructions(self):
        window = MainWindow()
        dialog = window.ensure_minimax_prompt_window()
        dialog.editor.setPlainText("User-pasted production prompt")
        dialog.instructions.setPlainText("Preserve the new ending and smooth the final transition.")
        window.segments = [Segment("Beat", "", "", kind="text", prompt="Motion", duration=2.5)]
        captured = {}

        def capture_worker(operation, args, activity, finished, **kwargs):
            captured["operation"] = operation
            captured["args"] = args
            captured["kwargs"] = kwargs

        with (
            patch.object(window, "ai_credentials", return_value=("gemini", "gemini-3.5-flash-lite", "unused")),
            patch.object(window, "current_minimax_cache_key", return_value="source-signature"),
            patch.object(window, "persist_minimax_prompt_edit") as persist,
            patch.object(window, "save_library_project") as save_project,
            patch.object(window, "start_ai_worker", side_effect=capture_worker),
        ):
            window.refine_minimax_prompt()

            persist.assert_not_called()
            save_project.assert_not_called()
            window.minimax_h3_finished("Refined production prompt")
            save_project.assert_called_once_with(automatic=True)

        self.assertEqual(captured["args"][-3], "User-pasted production prompt")
        self.assertEqual(captured["args"][-2], "Preserve the new ending and smooth the final transition.")
        self.assertFalse(captured["kwargs"]["show_main_overlay"])
        self.assertFalse(hasattr(window, "_minimax_save_timer"))
        self.assertEqual(
            window.minimax_operation_editor_snapshot,
            ("User-pasted production prompt", "Preserve the new ending and smooth the final transition."),
        )
        window.close()


if __name__ == "__main__":
    unittest.main()
