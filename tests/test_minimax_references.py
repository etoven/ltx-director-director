import base64
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QDropEvent, QImage
from PySide6.QtWidgets import QApplication

from ltx_prompt_director import ai
from ltx_prompt_director.minimax_reference import detect_workflow, reference_inventory
from ltx_prompt_director.models import Segment
from ltx_prompt_director.ui import MainWindow


def segment(kind="image", role="start", duration=3):
    return Segment(kind, "", "", kind=kind, role=role, duration=duration, prompt="Continue the motion.")


class MiniMaxReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self):
        window = MainWindow()
        window.segments = [segment("text")]
        self.addCleanup(window.close)
        return window

    def add_reference(self, window, slot=0):
        dialog = window.ensure_minimax_prompt_window()
        image = QImage(64, 48, QImage.Format.Format_RGB32)
        image.fill(QColor("#3d89aa"))
        dialog.reference_targets[slot].load_image(image, "identity.png")
        return dict(window.minimax_reference_images[slot])

    def test_workflow_is_determined_locally_for_all_media_combinations(self):
        reference = {"name": "face.png", "image": "data:image/png;base64,eA==", "role": "identity"}
        cases = [([segment("text")], [], "t2v"),
                 ([segment()], [], "i2v"),
                 ([segment(), segment(role="end")], [], "fl2v"),
                 ([segment(), segment()], [], "keyframes"),
                 ([segment("video")], [], "v2v"),
                 ([segment("video"), segment()], [], "r2v"),
                 ([segment("text")], [None, reference], "r2v")]
        for segments, refs, expected in cases:
            with self.subTest(expected=expected, segments=segments):
                self.assertEqual(detect_workflow(segments, refs), expected)

    def test_asset_numbers_and_endpoint_roles_are_shared_without_extra_duration(self):
        refs = [None, {"image": "data:image/png;base64,eA==", "role": "style", "name": "look.png"}]
        items = [segment(duration=2), segment("text", duration=1), segment("video", duration=4), segment(role="end", duration=3)]
        inventory = reference_inventory(items, refs)
        self.assertEqual([item["label"] for item in inventory], ["Image1", None, "Video1", "Image2", "Image3"])
        self.assertEqual(inventory[0]["checkpoint_time"], 0)
        self.assertEqual(inventory[3]["checkpoint_time"], 10)
        self.assertEqual(inventory[4]["reference_slot"], 1)
        self.assertNotIn("start_time", inventory[4])
        self.assertNotIn("checkpoint_time", inventory[4])

    def test_drop_persists_full_image_and_never_changes_timeline_or_calls_ai(self):
        window = self.make_window()
        dialog = window.ensure_minimax_prompt_window()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.png"
            image = QImage(320, 180, QImage.Format.Format_RGB32)
            image.fill(QColor("#d29733"))
            image.save(str(path))
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(path))])
            event = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            with patch.object(window, "start_ai_worker") as worker:
                dialog.reference_targets[1].dropEvent(event)
                self.assertTrue(event.isAccepted())
                worker.assert_not_called()
            dialog.reference_targets[1].role.setCurrentIndex(dialog.reference_targets[1].role.findData("scene"))
            dialog.reference_targets[1].notes.setText("Tile color and lighting only")
            payload = json.loads(json.dumps(window.project_payload()))
        self.assertEqual(len(window.segments), 1)
        self.assertEqual(window.total_duration(), 3)
        self.assertTrue(window.project_dirty)
        self.assertIn("Mixed references (R2V)", dialog.workflow_label.text())
        restored = self.make_window()
        restored.load_project_payload(payload)
        self.assertEqual(restored.minimax_reference_images, window.minimax_reference_images)
        self.assertIsNone(restored.minimax_reference_images[0])
        self.assertEqual(restored.minimax_reference_images[1]["role"], "scene")
        restored.show_minimax_prompt_window()
        self.assertFalse(restored.minimax_prompt_window.reference_targets[1].preview.pixmap().isNull())

    def test_sessions_clear_and_old_projects_do_not_leak_references(self):
        window = self.make_window()
        self.add_reference(window)
        state = window.capture_workspace_state()
        window.minimax_prompt_window.reference_targets[0].clear()
        self.assertEqual(window.minimax_reference_images, [None, None])
        window.restore_workspace_state(state)
        self.assertIsNotNone(window.minimax_reference_images[0])
        payload = window.project_payload()
        del payload["minimaxH3"]["referenceImages"]
        window.load_project_payload(payload)
        self.assertEqual(window.minimax_reference_images, [None, None])

    def test_large_image_is_preserved_in_project_and_resized_only_for_provider(self):
        window = self.make_window()
        target = window.ensure_minimax_prompt_window().reference_targets[0]
        image = QImage(2048, 1400, QImage.Format.Format_RGB32)
        image.fill(QColor("#a091e8"))
        target.load_image(image)
        stored = window.project_payload()["minimaxH3"]["referenceImages"][0]["image"]
        sent = ai._minimax_reference_inputs(window.segments, "gemini", window.minimax_reference_images)[-1]["image"]
        def size(value):
            with Image.open(io.BytesIO(base64.b64decode(value.split(",", 1)[1]))) as decoded:
                return decoded.size
        self.assertEqual(size(stored), (2048, 1400))
        self.assertEqual(max(size(sent)), 1024)

    def test_ltx_import_starts_with_empty_reference_slots(self):
        window = self.make_window()
        self.add_reference(window)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new.json"
            path.write_text(json.dumps({"timeline": {"segments": [{"type": "text", "start": 0, "length": 120, "prompt": "New action"}]}}))
            with patch.object(window, "save_library_project"):
                window.import_ltx(path=str(path))
        self.assertEqual(window.minimax_reference_images, [None, None])

    def test_generation_and_refinement_send_the_untimed_image_with_its_role(self):
        window = self.make_window()
        self.add_reference(window, 1)
        refs = window.minimax_reference_images
        for operation, extra in [(ai.build_minimax_h3_reference_prompt, ()),
                                 (ai.refine_minimax_h3_reference_prompt, ("Edited brief", "Preserve my ending"))]:
            with patch.object(ai, "_provider_raw", return_value='{"prompt":"A complete brief"}') as provider:
                result = operation(window.segments, "gemini", "test", "unused", "Intent", "Global", True, False, True, *extra, reference_images=refs)
            inputs = provider.call_args.args[0]
            self.assertEqual(result, "A complete brief")
            self.assertEqual(inputs[-1]["image"], refs[1]["image"])
            self.assertEqual(inputs[-1]["role"], "identity")
            self.assertEqual(inputs[-1]["label"], "Image1")
            self.assertNotIn("cue_timestamp", inputs[-1])
            self.assertNotIn("recommended_detail", inputs[0])
            self.assertNotIn("camera_continuity_requirement", inputs[0])

    def test_both_provider_requests_receive_roles_and_image_pixels(self):
        window = self.make_window()
        self.add_reference(window)
        inputs = ai._minimax_reference_inputs(window.segments, "gemini", window.minimax_reference_images)
        for provider in ("gemini", "openai"):
            response = Mock()
            response.json.return_value = ({"candidates": [{"content": {"parts": [{"text": '{"prompt":"OK"}'}]}}]}
                                          if provider == "gemini" else {"output_text": '{"prompt":"OK"}'})
            with patch.object(ai.requests, "post", return_value=response) as post:
                ai._provider_raw(inputs, provider, "test", "unused", "Rules", 400)
            body = json.dumps(post.call_args.kwargs["json"])
            self.assertIn("REFERENCE INPUT", body)
            self.assertIn("reference_slot", body)
            self.assertIn("identity", body)
            self.assertIn("image/png", body)
            self.assertNotIn("REQUIRED OUTPUT CUE", body)

    def test_ui_passes_reference_snapshot_to_manual_generation_and_refinement(self):
        window = self.make_window()
        self.add_reference(window)
        window.minimax_prompt_mode = "references"
        window.minimax_prompt_window.editor.setPlainText("My edited brief")
        with patch.object(window, "ai_credentials", return_value=("gemini", "test", "unused")), patch.object(window, "start_ai_worker") as worker:
            window.generate_minimax_prompt_references()
            self.assertEqual(worker.call_args.args[1][-1], window.minimax_reference_images)
            self.assertIsNot(worker.call_args.args[1][-1], window.minimax_reference_images)
            window.refine_minimax_prompt()
            self.assertEqual(worker.call_args.args[1][-1], window.minimax_reference_images)

    def test_reference_edits_invalidate_cache_and_reject_stale_worker_result(self):
        window = self.make_window()
        ref = self.add_reference(window)
        before = window.current_minimax_cache_key()
        window.minimax_operation_signature = before
        window.minimax_prompt_text = "Keep this"
        window.set_minimax_reference_image(0, {**ref, "notes": "Background only"})
        self.assertNotEqual(before, window.current_minimax_cache_key())
        window.minimax_h3_finished("Outdated response")
        self.assertEqual(window.minimax_prompt_text, "Keep this")
        self.assertIn("references changed", window.minimax_prompt_window.message_banner.text())


if __name__ == "__main__":
    unittest.main()
