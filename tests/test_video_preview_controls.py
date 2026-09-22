import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ltx_prompt_director.ui import ProjectPreviewPanel, SeekSlider


class VideoPreviewControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_scrubber_click_emits_explicit_seek_request(self):
        slider = SeekSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 10_000)
        slider.resize(400, 28)
        slider.show()
        QApplication.processEvents()
        seeks = []
        slider.seek_requested.connect(seeks.append)

        QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(300, 14))

        self.assertEqual(len(seeks), 1)
        self.assertGreater(seeks[0], 5_000)
        slider.close()

    def test_player_has_live_unmuted_audio_output(self):
        panel = ProjectPreviewPanel()
        self.assertIs(panel.player.audioOutput(), panel.audio)
        self.assertFalse(panel.audio.isMuted())
        self.assertGreater(panel.audio.volume(), 0.0)
        panel.close()

    def test_refreshing_same_video_does_not_reload_player_source(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "preview.mp4"
            video.write_bytes(b"placeholder video bytes")
            panel = ProjectPreviewPanel()
            source = QUrl.fromLocalFile(str(video))
            panel.player.setSource(source)
            source_changes = []
            panel.player.sourceChanged.connect(source_changes.append)

            panel.set_project("Same project", str(video))

            self.assertEqual(len(source_changes), 0)
            self.assertEqual(panel.player.source().toLocalFile(), str(video))
            self.assertIs(panel.player.audioOutput(), panel.audio)
            panel.close()


if __name__ == "__main__":
    unittest.main()
