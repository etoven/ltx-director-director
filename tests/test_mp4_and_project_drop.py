import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import imageio_ffmpeg
from PySide6.QtCore import QPointF, QUrl
from PySide6.QtWidgets import QApplication, QFileDialog

from ltx_prompt_director import ui
from ltx_prompt_director.media import data_url, prepare_media
from ltx_prompt_director.models import Segment


class _MimeData:
    def __init__(self, paths):
        self._urls = [QUrl.fromLocalFile(str(path)) for path in paths]

    def hasUrls(self):
        return bool(self._urls)

    def urls(self):
        return self._urls


class _DropEvent:
    def __init__(self, paths):
        self._mime = _MimeData(paths)
        self.accepted = False

    def mimeData(self):
        return self._mime

    def position(self):
        return QPointF(1, 1)

    def acceptProposedAction(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


class Mp4AndProjectDropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_media_picker_exposes_mp4(self):
        with patch.object(ui.sys, "platform", "win32"), patch.object(
            QFileDialog, "getOpenFileNames", return_value=([], "")
        ) as picker:
            ui.choose_media_files(None, True, str(Path.home()))
        self.assertIn("*.mp4", picker.call_args.args[3])

    def test_mp4_is_decoded_as_video_and_keeps_its_container(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.mp4"
            subprocess.run(
                [
                    imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi", "-i",
                    "color=c=blue:s=64x64:d=0.5", "-r", "24", "-pix_fmt", "yuv420p", str(path),
                ],
                check=True,
                capture_output=True,
            )
            kind, preview, frames, trim = prepare_media(str(path))
            self.assertEqual(kind, "video")
            self.assertTrue(Path(preview).is_file())
            self.assertGreater(frames, 0)
            self.assertGreaterEqual(trim, 0)
            self.assertTrue(data_url(str(path)).startswith("data:video/mp4;base64,"))
            segment = Segment(path.name, str(path), preview, kind)
            self.assertEqual(ui.segment_media_suffix(segment), ".mp4")
            self.assertEqual(ui.segment_kind_label(segment), "MP4")

    def test_timeline_video_labels_show_the_actual_container(self):
        self.assertEqual(ui.segment_kind_label(Segment("clip.webm", "/tmp/clip.webm", "", "video")), "WEBM")
        self.assertEqual(ui.segment_kind_label(Segment("clip.mp4", "/tmp/clip.mp4", "", "video")), "MP4")
        self.assertEqual(ui.segment_kind_label(Segment("still.png", "/tmp/still.png", "", "image")), "IMAGE")

    def test_timeline_mp4_drop_uses_media_feedback_and_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "clip.mp4"
            path.touch()
            timeline = ui.TimelineListWidget()
            received = []
            timeline.files_dropped.connect(lambda paths, index: received.append((paths, index)))
            event = _DropEvent([path])
            timeline.dragEnterEvent(event)
            self.assertTrue(event.accepted)
            self.assertTrue(timeline.property("dropActive"))
            timeline.dropEvent(event)
            self.assertEqual(received, [([str(path)], 0)])
            self.assertFalse(timeline.property("dropActive"))

    def test_timeline_project_drop_uses_same_feedback_and_project_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.LTXD"
            path.write_text("{}", encoding="utf-8")
            timeline = ui.TimelineListWidget()
            received = []
            timeline.projects_dropped.connect(received.append)
            event = _DropEvent([path])
            timeline.dragEnterEvent(event)
            self.assertTrue(event.accepted)
            self.assertTrue(timeline.property("dropActive"))
            timeline.dropEvent(event)
            self.assertEqual(received, [[str(path)]])
            self.assertFalse(timeline.property("dropActive"))


if __name__ == "__main__":
    unittest.main()
