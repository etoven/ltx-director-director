import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ltx_prompt_director.ui import _duplicate_library_project_files


class ProjectDuplicateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.original_id = "original"
        self.archive = self.root / "original.LTXD"
        self.preview = self.root / "previews" / "original_preview.webm"
        self.preview.parent.mkdir()
        self.preview.write_bytes(b"preview-video-original")
        self.attachment = self.root / "project_files" / "original_workflow.json"
        self.attachment.parent.mkdir()
        self.attachment.write_bytes(b'{"workflow":"original"}')
        self.metadata = {
            "id": self.original_id,
            "name": "Moonlight",
            "description": "Lioness transition",
            "projectPath": str(self.archive),
            "projectType": "shot_sequence",
            "collection": "Transformations",
            "status": "Consider",
            "tags": ["Review"],
            "notes": [{"id": "note1", "text": "Keep camera fixed", "date": "2026-10-15",
                       "createdAt": "2026-10-15T12:00:00+00:00", "checked": False}],
            "archived": True,
            "previewVideoName": "review.webm",
            "previewVideoPath": str(self.preview),
            "projectFiles": [{"name": "workflow.json", "path": str(self.attachment)}],
        }
        self.write_metadata()

    def write_metadata(self):
        (self.root / f"{self.original_id}.meta.json").write_text(
            json.dumps(self.metadata), encoding="utf-8"
        )

    def make_archive(self):
        payload = {
            "app": "ltx-director-director",
            "projectType": "shot_sequence",
            "projectVersion": 9,
            "frames": [{"archiveSource": "media/0000.webm", "prompt": "Stretch the arm"}],
            "library": {"id": self.original_id, "name": "Moonlight"},
        }
        with zipfile.ZipFile(self.archive, "w", compression=zipfile.ZIP_STORED) as z:
            z.writestr("project.json", json.dumps(payload))
            z.writestr("media/0000.webm", b"high-quality source media" * 100)

    def test_duplicate_creates_independent_project_archive_and_attachments(self):
        self.make_archive()
        result = _duplicate_library_project_files(self.metadata, self.root, "copy1")
        self.assertEqual(result["id"], "copy1")
        self.assertEqual(result["name"], "Moonlight (Copy)")
        self.assertFalse(result["archived"])
        self.assertEqual(result["status"], "Consider")
        self.assertEqual(result["tags"], ["Review"])
        self.assertEqual(result["notes"], self.metadata["notes"])
        self.assertEqual(result["collection"], "Transformations")
        self.assertEqual(json.loads((self.root / "copy1.meta.json").read_text()), result)
        with zipfile.ZipFile(self.archive) as original, zipfile.ZipFile(result["projectPath"]) as cloned:
            self.assertEqual(cloned.read("media/0000.webm"), original.read("media/0000.webm"))
            manifest = json.loads(cloned.read("project.json"))
            self.assertEqual(manifest["library"]["id"], "copy1")
            self.assertEqual(manifest["library"]["name"], "Moonlight (Copy)")
            self.assertEqual(manifest["frames"][0]["prompt"], "Stretch the arm")
        copied_preview = Path(result["previewVideoPath"])
        copied_file = Path(result["projectFiles"][0]["path"])
        self.assertNotEqual(copied_preview, self.preview)
        self.assertNotEqual(copied_file, self.attachment)
        self.assertEqual(copied_preview.read_bytes(), self.preview.read_bytes())
        self.assertEqual(copied_file.read_bytes(), self.attachment.read_bytes())
        copied_preview.write_bytes(b"changed copy")
        copied_file.write_bytes(b"changed workflow")
        self.assertEqual(self.preview.read_bytes(), b"preview-video-original")
        self.assertEqual(self.attachment.read_bytes(), b'{"workflow":"original"}')

    def test_repeated_duplicates_use_unique_names(self):
        self.make_archive()
        first = _duplicate_library_project_files(self.metadata, self.root, "copy1")
        second = _duplicate_library_project_files(self.metadata, self.root, "copy2")
        self.assertEqual(first["name"], "Moonlight (Copy)")
        self.assertEqual(second["name"], "Moonlight (Copy 2)")
        self.assertNotEqual(first["projectPath"], second["projectPath"])

    def test_duplicate_repairs_preview_filename_using_its_actual_container(self):
        self.make_archive()
        original = b'\x00\x00\x00\x20ftypisom\x00\x00\x02\x00mp4 source bytes'
        self.preview.write_bytes(original)
        self.metadata['previewVideoName'] = '0.5625 — opening breath and urgent appeal'
        self.write_metadata()
        copied = _duplicate_library_project_files(self.metadata, self.root, 'safe-copy')
        destination = Path(copied['previewVideoPath'])
        self.assertEqual(destination.suffix, '.mp4')
        self.assertNotIn(' ', destination.name)
        self.assertEqual(copied['previewVideoName'], '0.5625_opening_breath_and_urgent_appeal.mp4')
        self.assertEqual(destination.read_bytes(), original)
        self.assertEqual(self.preview.read_bytes(), original)

    def test_legacy_json_project_gets_updated_library_identity(self):
        self.archive.write_text(json.dumps({
            "app": "ltx-director-director",
            "projectType": "shot_sequence",
            "frames": [],
            "library": {"id": "original", "name": "Moonlight"},
        }), encoding="utf-8")
        copy_meta = _duplicate_library_project_files(self.metadata, self.root, "copy1")
        contents = json.loads(Path(copy_meta["projectPath"]).read_text(encoding="utf-8"))
        self.assertEqual(contents["library"]["id"], "copy1")
        self.assertEqual(contents["library"]["name"], "Moonlight (Copy)")
        self.assertEqual(json.loads(self.archive.read_text())["library"]["id"], "original")

    def test_attachment_copy_failure_rolls_back_partial_files(self):
        from unittest.mock import patch
        from ltx_prompt_director import ui

        self.make_archive()
        real_copy = ui.shutil.copy2

        def interrupted_copy(source, destination, *args, **kwargs):
            result = real_copy(source, destination, *args, **kwargs)
            if Path(destination).parent.name == "previews":
                raise OSError("interrupted attachment copy")
            return result

        with patch.object(ui.shutil, "copy2", side_effect=interrupted_copy):
            with self.assertRaisesRegex(OSError, "interrupted attachment"):
                _duplicate_library_project_files(self.metadata, self.root, "copy1")
        self.assertFalse((self.root / "copy1.LTXD").exists())
        self.assertFalse((self.root / "copy1.meta.json").exists())
        self.assertEqual(list((self.root / "previews").glob("copy1_*")), [])
        self.assertEqual(self.preview.read_bytes(), b"preview-video-original")

    def test_missing_archive_leaves_no_incomplete_duplicate(self):
        with self.assertRaises(FileNotFoundError):
            _duplicate_library_project_files(self.metadata, self.root, "copy1")
        self.assertFalse((self.root / "copy1.LTXD").exists())
        self.assertFalse((self.root / "copy1.meta.json").exists())


if __name__ == "__main__":
    unittest.main()
