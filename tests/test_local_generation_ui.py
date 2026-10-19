from contextlib import contextmanager
from pathlib import Path
import tempfile
from unittest.mock import patch
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from ltx_prompt_director.ui import MainWindow, SettingsDialog
from ltx_prompt_director.onboarding import OnboardingWizard
from ltx_prompt_director.local_generation import PROFILE_KEY

APP = QApplication.instance() or QApplication([])


@contextmanager
def window():
    with tempfile.TemporaryDirectory() as folder:
        settings = QSettings(str(Path(folder) / 'settings.ini'), QSettings.Format.IniFormat)
        with patch('ltx_prompt_director.ui.QSettings', return_value=settings), patch.object(MainWindow, 'restore_startup_workspace'):
            w = MainWindow()
        w._close_saves_queued = True
        try: yield w
        finally: w.close()


def test_wizard_cancel_preserves_credentials_and_finish_separates_sources():
    with window() as w:
        wizard = OnboardingWizard(w)
        assert wizard.pages.count() == 6
        assert not wizard.header_source.isNull()
        wizard.text_key.setText('session-only-example')
        wizard.reject()
        assert not w.settings.contains('gemini_key')
        assert not w.settings.contains('provider')
        wizard = OnboardingWizard(w)
        wizard.text_key.setText('session-only-example')
        wizard.remember.setChecked(False)
        wizard.image_model.setCurrentIndex(wizard.image_model.findData('local:flux2'))
        wizard.finish()
        assert w.settings.value('provider') == 'gemini'
        assert w.settings.value('artwork/default_model') == 'local:flux2'
        assert w.session_keys['gemini'] == 'session-only-example'
        assert not w.settings.contains('gemini_key')
        assert w.settings.value('onboarding/seen', False, bool)
        assert w.settings.contains(PROFILE_KEY)


def test_local_progress_is_ignored_after_a_project_switch_and_no_paid_prompt():
    from ltx_prompt_director.artwork_tools import ArtworkTool
    with window() as w:
        scope = (w.workspace_revision, w.current_project_id, w.project_type)
        with patch.object(w.image_generation_panel, 'receive_event') as receive:
            w.local_image_progress({'kind': 'progress', 'percent': 42, 'eta_seconds': 10}, scope)
            receive.assert_called_once()
            receive.reset_mock(); w.workspace_revision += 1
            w.local_image_progress({'kind': 'preview', 'path': '/old/image.png'}, scope)
            receive.assert_not_called()
        with patch('ltx_prompt_director.ui.QMessageBox') as message:
            assert w._approve_artwork_cost(ArtworkTool('local', 'flux2', '', {}))
            message.assert_not_called()
        dialog = SettingsDialog(w.settings, w)
        assert dialog.provider.findText('local') >= 0
        assert 'video_encoder' in dialog.local_models.fields
        dialog.reject()


def test_generated_video_attachment_preserves_previous_render_and_discards_stale_metadata():
    import tempfile
    from pathlib import Path
    with window() as w, tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        source = root / 'generated.mp4'; source.write_bytes(b'new render')
        previous = root / 'old.mp4'; previous.write_bytes(b'original render')
        w.current_project_id = 'test-project'
        meta = {'id': w.current_project_id, 'name': 'Example', 'previewVideoPath': str(previous)}
        scope = (w.workspace_revision, w.current_project_id, w.project_type)
        jobs = []
        with patch('ltx_prompt_director.ui.project_library_path', return_value=root), \
             patch.object(w, 'current_library_metadata', return_value=dict(meta)), \
             patch.object(w, 'start_disk_job', side_effect=lambda op, args, ready, fail, **kw: jobs.append((op, args, ready))), \
             patch.object(w, 'persist_library_metadata') as persist, \
             patch.object(w.project_preview_panel, 'set_project'):
            w.attach_local_generated_video(str(source), scope)
            op, args, ready = jobs.pop(); path = op(*args); ready(path)
            assert persist.call_args.args[0]['previewVideoPath'] == path
            assert previous.read_bytes() == b'original render'
            assert Path(path).read_bytes() == b'new render'
            persist.reset_mock()
            w.attach_local_generated_video(str(source), scope)
            op, args, ready = jobs.pop(); w.workspace_revision += 1
            path = op(*args); ready(path)
            persist.assert_not_called()
            assert source.read_bytes() == b'new render'
            assert previous.read_bytes() == b'original render'


def test_unsaved_local_video_can_be_exported():
    import tempfile
    import shutil
    with window() as w, tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / 'generated.mp4'; source.write_bytes(b'generated video')
        target = Path(folder) / 'export.mp4'
        w._local_result_video = {'scope': (w.workspace_revision, w.current_project_id, w.project_type), 'path': str(source)}
        with patch.object(w, 'current_library_metadata', return_value=None), \
             patch.object(w, 'next_export_path', return_value=target), \
             patch.object(w, 'start_disk_job') as job:
            w.export_project_video()
            assert job.call_args.args[0] is shutil.copy2
            operation, arguments = job.call_args.args[:2]
            operation(*arguments)
            assert target.read_bytes() == b'generated video'
