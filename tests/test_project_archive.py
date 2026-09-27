import base64
import io
import json
import os
import tempfile
import time
import unittest
import zipfile
import subprocess
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PIL import Image
import imageio_ffmpeg
from PySide6.QtWidgets import QApplication

from ltx_prompt_director import ai
from ltx_prompt_director.media import prepare_media
from ltx_prompt_director import media, ui
from ltx_prompt_director.models import Segment
from ltx_prompt_director.project_archive import materialize_source, read_project, save_project_archive
from ltx_prompt_director.ui import MainWindow


class ProjectArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self, segments):
        with patch.object(MainWindow, 'restore_startup_workspace'), patch.object(MainWindow, 'save_minimax_prompt_on_close'):
            window = MainWindow()
        window.segments = segments
        self.addCleanup(window.close)
        return window

    def test_portable_image_and_video_round_trip_is_lazy_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / 'original.png'
            Image.new('RGB', (2400, 1600), '#345d88').save(image)
            kind, preview, _, _ = prepare_media(str(image))
            with Image.open(preview) as thumb:
                self.assertLessEqual(max(thumb.size), 640)
            original = image.read_bytes()
            video = root / 'sample.webm'
            video.write_bytes(b'full original video source bytes' * 400)
            project = root / 'portable.LTXD'
            segments = [Segment(image.name, str(image), preview, kind=kind, prompt='A person walks.'),
                        Segment(video.name, str(video), preview, kind='video', duration=4, media_duration_frames=96, trim_start=0)]
            window = self.make_window(segments)
            window.set_project_type('minimax_references')
            window.minimax_reference_images = [{'name': 'wardrobe.png', 'image': 'data:image/png;base64,' + base64.b64encode(original).decode(), 'role': 'wardrobe'}, None]
            save_project_archive(project, window.project_payload(include_media=False), window.segments)
            with zipfile.ZipFile(project) as archive:
                manifest = json.loads(archive.read('project.json'))
                self.assertNotIn('sourceData', str(manifest))
                self.assertNotIn('data:image/png;base64', str(manifest))
            image.unlink()
            video.unlink()
            loaded = read_project(project)
            restored = self.make_window([])
            restored.load_project_payload(loaded)
            self.assertEqual(restored.project_type, 'minimax_references')
            self.assertFalse(Path(restored.segments[0].media_path).exists())
            self.assertFalse(Path(restored.segments[1].media_path).exists())
            self.assertEqual(restored.minimax_reference_images[0]['role'], 'wardrobe')
            self.assertEqual(restored.segments[0]._source_size, [2400, 1600])
            assert restored.segments[0].preview_path != restored.segments[0].media_path
            ai._segment_input(restored.segments[0])
            self.assertEqual(Path(restored.segments[0].media_path).read_bytes(), original)
            self.assertFalse(Path(restored.segments[1].media_path).exists())
            self.assertEqual(Path(materialize_source(restored.segments[1])).read_bytes(), b'full original video source bytes' * 400)
            # Re-saving a reopened project needs no original file outside its archive.
            save_project_archive(project, restored.project_payload(include_media=False), restored.segments)
            self.assertEqual(read_project(project)['projectVersion'], 9)

    def test_legacy_json_projects_still_open_with_bounded_previews(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / 'source.png'
            Image.new('RGB', (2000, 1200), '#3e2789').save(image)
            original = base64.b64encode(image.read_bytes()).decode()
            legacy = root / 'legacy.LTXD'
            window = self.make_window([Segment('source.png', str(image), str(image))])
            payload = window.project_payload()
            legacy.write_text(json.dumps(payload), encoding='utf-8')
            restored = self.make_window([])
            restored.load_project_payload(read_project(legacy))
            self.assertEqual(Path(restored.segments[0].media_path).read_bytes(), image.read_bytes())
            with Image.open(restored.segments[0].preview_path) as preview:
                self.assertLessEqual(max(preview.size), 640)

    def test_real_video_preview_cache_and_lazy_project_playback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / 'motion.mp4'
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-f', 'lavfi', '-i',
                            'color=c=blue:s=1280x720:d=0.8', '-r', '24', '-pix_fmt', 'yuv420p', str(video)],
                           check=True, capture_output=True)
            first = prepare_media(str(video))
            with patch.object(media, 'probe_video_duration', side_effect=AssertionError('should use cached probe')):
                self.assertEqual(prepare_media(str(video)), first)
            with Image.open(first[1]) as thumbnail:
                self.assertLessEqual(max(thumbnail.size), 640)
            window = self.make_window([Segment('motion.mp4', str(video), first[1], kind='video', duration=0.8)])
            archive = root / 'video.LTXD'
            save_project_archive(archive, window.project_payload(include_media=False), window.segments)
            loaded = self.make_window([])
            loaded.load_project_payload(read_project(archive))
            self.assertFalse(Path(loaded.segments[0].media_path).exists())
            output = root / 'ltx.json'
            with patch.object(loaded, 'resolve_comfy_root', return_value=root), patch.object(ui, 'choose_document_save', return_value=str(output)):
                loaded.export_ltx()
                deadline = time.monotonic() + 10
                while loaded._pending_disk_jobs and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.01)
            self.assertEqual(loaded._pending_disk_jobs, 0)
            exported = json.loads(output.read_text(encoding='utf-8'))
            self.assertIn(base64.b64encode(video.read_bytes()).decode(), str(exported))
            self.assertEqual(Path(materialize_source(loaded.segments[0])).read_bytes(), video.read_bytes())
            loaded.project_preview_panel.set_project('Video', str(video))
            self.assertFalse(loaded.project_preview_panel.player.source().isLocalFile())
            loaded.show()
            loaded.project_preview_dock.show()
            self.app.processEvents()
            self.assertTrue(loaded.project_preview_panel.player.source().isLocalFile())
            loaded.project_preview_dock.hide()
            self.assertFalse(loaded.project_preview_panel.player.source().isLocalFile())


if __name__ == '__main__':
    unittest.main()
