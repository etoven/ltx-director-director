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


def test_flux_canvas_tracks_current_output_for_both_model_sizes():
    from ltx_prompt_director.artwork_tools import render_artwork
    from ltx_prompt_director import local_generation as local
    with window() as w, tempfile.TemporaryDirectory() as folder:
        w.settings.setValue('artwork/default_model', 'local:flux2')
        result = Path(folder) / 'result.png'; result.write_bytes(b'fake worker output')
        with patch.object(local, 'validate_profile'), patch.object(local, 'run_request', return_value={'path': str(result)}) as run:
            for variant in ('4b', '9b'):
                local.save_profile(w.settings, {'image_variant': variant})
                for width, height, expected in ((1280,704,(1280,704)), (704,1280,(704,1280)), (1000,777,(992,784)), (1001,777,(1008,784)), (1024,1024,(1024,1024))):
                    w.output_width.setValue(width); w.output_height.setValue(height)
                    tool = w._available_artwork_tools()[0]
                    images = render_artwork([{'id':'canvas', 'artworkPrompt':'A lighthouse.', 'sourceImage':'/source.png',
                                              'referenceImages':[{'path':'/reference.png'}]}], tool)
                    request = run.call_args.args[0]
                    assert (request['width'], request['height']) == expected
                    assert request['profile']['image_variant'] == variant
                    assert request['source'] == '/source.png' and request['references'][0]['path'] == '/reference.png'
                    assert Path(images['canvas']).read_bytes() == result.read_bytes()
                assert 'image_width' not in local.load_profile(w.settings)


def test_loras_are_settings_only_persistent_and_cancel_safe():
    from ltx_prompt_director.local_generation import load_profile
    from ltx_prompt_director.local_widgets import LoraList
    from ltx_prompt_director.onboarding_local import WizardLocalModelsPanel
    with window() as w:
        dialog = SettingsDialog(w.settings, w)
        adapters = dialog.local_models.loras
        adapters['video'].add_button.click(); adapters['video'].add_button.click()
        first, second = adapters['video'].rows
        first.path.setText('/models/film.safetensors'); first.trigger.setText('film grain'); first.weight.setValue(.65)
        second.path.setText('/models/action.safetensors'); second.weight.setValue(.35); second.enabled.setChecked(False)
        image = adapters['image'].add_row(); image.path.setText('/models/ink.safetensors'); image.trigger.setText('ink lines')
        assert first.weight.minimum() == 0 and first.weight.maximum() == 1
        dialog.accept()
        saved = load_profile(w.settings)
        assert len(saved['video_loras']) == 2 and saved['video_loras'][0]['weight'] == .65
        assert saved['image_loras'][0]['trigger'] == 'ink lines'
        reopened = SettingsDialog(w.settings, w)
        assert reopened.local_models.loras['video'].rows[0].trigger.text() == 'film grain'
        reopened.local_models.loras['video'].remove_row(reopened.local_models.loras['video'].rows[0])
        reopened.reject()
        assert load_profile(w.settings) == saved
        wizard = WizardLocalModelsPanel(w, lambda: ['video', 'image'])
        assert not wizard.loras and not wizard.findChildren(LoraList)
        assert wizard.value()['video_loras'] == saved['video_loras']
        assert wizard.value()['image_loras'] == saved['image_loras']
        wizard.deleteLater()


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
    from unittest.mock import MagicMock
    from PySide6.QtMultimedia import QMediaPlayer
    with window() as w:
        image=w.image_generation_panel
        image.receive_event({'kind':'error','message':'Previous run failure'})
        image.begin();assert not image.log.output.toPlainText()
        panel=w.project_preview_panel
        with patch.object(panel,'player',MagicMock()) as player:
            panel._generation_preview_active=True;panel._initial_media_pending=True
            panel._initial_media_autoplay=True;panel._initial_media_position=0
            panel.media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
            player.play.assert_called_once();player.pause.assert_not_called()
            panel.media_status_changed(QMediaPlayer.MediaStatus.BufferedMedia)
            player.play.assert_called_once()
            player.playbackState.return_value=QMediaPlayer.PlaybackState.PlayingState
            panel.toggle_playback();assert not panel._generation_autoplay
            panel._initial_media_pending=True;panel._initial_media_autoplay=False
            panel.media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
            assert player.pause.call_count==2
            panel._generation_preview_active=False;player.reset_mock()
            panel._initial_media_pending=True
            panel.media_status_changed(QMediaPlayer.MediaStatus.LoadedMedia)
            player.pause.assert_called_once();player.play.assert_not_called()
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
        from PySide6.QtWidgets import QDialogButtonBox
        buttons = dialog.buttons
        assert isinstance(buttons, QDialogButtonBox) and dialog.isAncestorOf(buttons)
        dialog.local_models.video_random_seed.setChecked(False)
        dialog.local_models.video_seed.setValue(931351171509952)
        dialog.accept()
        from ltx_prompt_director.local_generation import load_profile
        assert load_profile(w.settings)['video_seed'] == 931351171509952


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


def test_native_video_excludes_saved_references_in_conditioning_only_workspaces():
    import base64
    import copy
    from io import BytesIO
    from PIL import Image
    from ltx_prompt_director.models import Segment
    from PySide6.QtCore import Qt
    with window() as w:
        image=BytesIO();Image.new('RGB',(16,16),'navy').save(image,'PNG')
        stored=[dict(name='Other-mode identity',role='identity',notes='Full workspace only',
            image='data:image/png;base64,'+base64.b64encode(image.getvalue()).decode()),None,None,None]
        w.minimax_reference_images=copy.deepcopy(stored)
        w.segments=[Segment('Opening','/source/opening.png','',duration=15)]
        original=copy.deepcopy(w.workspace_definitions['minimax_references']['references'])
        w.set_project_type('minimax_references')
        dock=w.minimax_panel.reference_dock
        w.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea,dock)
        w.show_reference_images()
        for mode,options,count in [('minimax_frames',original,0),('minimax_references',original,1),
                                  ('minimax_references',{**original,'enabled':False},0),
                                  ('minimax_references',{**original,'untimed_slots':0},0)]:
            w.workspace_definitions[mode]['references']=copy.deepcopy(options)
            w.set_project_type(mode);w.apply_project_type_ui()
            w.unified_prompt.setPlainText('A continuous shot from the opening composition.')
            assert bool(any(w.active_reference_images()))==bool(count)
            assert w.references_button.isHidden()==(count==0)
            assert dock.isHidden()==(count==0)
            assert w.settings.value('reference_panel_open',False,bool)
            if not count:assert not w._artwork_reference_inputs(w.segments[0].id)
            jobs=[]
            with patch('ltx_prompt_director.ui.validate_profile'),patch.object(w,'start_disk_job',
                    side_effect=lambda op,args,ready,failed,**kw:jobs.append((op,args,failed))):
                w.generate_local_video()
            assert len(jobs)==1
            operation,args,failed=jobs[0]
            with patch('ltx_prompt_director.ui.run_request',return_value={'path':'/result.mp4'}) as run:
                operation(*args)
            references=run.call_args.args[0]['references']
            assert len(references)==1+count and references[0]['name']=='Opening'
            assert ('Other-mode identity' in [ref['name'] for ref in references])==bool(count)
            assert w.minimax_reference_images==stored
            failed('Request check finished.')
        w.workspace_definitions['minimax_references']['references']=original
        w.apply_project_type_ui()
        assert w.active_reference_images()[0]['name']=='Other-mode identity'
        assert not dock.isHidden() and w.dockWidgetArea(dock)==Qt.DockWidgetArea.LeftDockWidgetArea
        w.set_reference_panel_open(False)
        w.set_project_type('minimax_frames');w.set_project_type('minimax_references')
        assert dock.isHidden() and not w.settings.value('reference_panel_open',True,bool)


def test_native_video_uses_unified_output_reference_panel_and_settings_directly():
    from ltx_prompt_director.local_widgets import LocalModelsPanel, GenerationLog
    from ltx_prompt_director.local_generation import save_profile
    from ltx_prompt_director.models import Segment
    with window() as w:
        w.set_project_type('minimax_references')
        w.workspace_definition['references']['untimed_slots']=4
        w.segments=[Segment('Opening','/source/opening.png','',duration=3),
                    Segment('Timeline video','/source/timeline.mp4','',kind='video',duration=4,trim_start=48),
                    Segment('Ending','/source/ending.png','',role='end',duration=3)]
        w.refresh_timeline(0)
        production = 'summary\nA single continuous shot.\n\ndetailed_description\n[Shot 1] At 00:00.00, steam rises.\n\noverall_soundscape\nQuiet room.\n\nnon_diegetic_music\nN/A'
        authored = production + '\n/refine Do not send this editing instruction.'
        w.unified_prompt.setPlainText(authored)
        assert w.minimax_prompt_text == authored
        w.minimax_reference_images = [
            {'name': 'Existing character', 'image': 'data:image/png;base64,example', 'role': 'identity'},
            {'name': 'Video with sound', 'kind': 'video', 'path': '/source/video.mp4', 'audio_path': '/source/paired.wav'},
            None, {'name': 'Sound only', 'kind': 'video', 'path': '/source/sound.mp4', 'audio_path': '/source/only.wav', 'audio_only': True}]
        setup = LocalModelsPanel(w, {'video_seed': 931351171509952})
        assert setup.video_seed.value() == 931351171509952
        setup.video_seed.stepBy(1); assert setup.video_seed.value() == 931351171509953
        setup.video_seed.setValue(931351171509952)
        assert (setup.video_width.value(), setup.video_height.value(), setup.video_duration.value(), setup.video_steps.value()) == (960, 544, 15, 20)
        assert setup.video_preview_mode.currentData() == 'noisy'
        assert setup.video_preview_every.value() == 5 and not setup.video_preview_every.isEnabled()
        setup.video_preview_mode.setCurrentIndex(setup.video_preview_mode.findData('denoised'))
        assert setup.video_preview_every.isEnabled()
        setup.video_preview_every.setValue(7)
        assert setup.value()['video_preview_mode'] == 'denoised' and setup.value()['video_preview_every'] == 7
        setup.video_preview_mode.setCurrentIndex(0)
        save_profile(w.settings, setup.value())
        jobs = []
        with patch('ltx_prompt_director.ui.validate_profile'), \
             patch.object(w, 'start_disk_job', side_effect=lambda op, args, ready, fail, **kw: jobs.append((op, args, ready, fail, kw))):
            w.generate_local_video()
        assert len(jobs) == 1
        operation, args, ready, failed, callbacks = jobs[0]
        w.minimax_reference_images[0]['name'] = 'Edited after start'
        w.segments[0].media_path='/changed-after-start.png'
        def materialize(segment):
            assert all(segment is not original for original in w.segments)
            if segment.name=='Opening':segment.media_path='/cache/archived-opening.png'
        with patch('ltx_prompt_director.project_archive.materialize_source',side_effect=materialize) as load, patch('ltx_prompt_director.ui.run_request', return_value={'path': '/result.mp4'}) as run:
            operation(*args)
        assert load.call_count==3 and w.segments[0].media_path=='/changed-after-start.png'
        request = run.call_args.args[0]
        assert request['prompt'] == production
        assert (request['width'], request['height'], request['duration'], request['steps'], request['seed']) == (960, 544, 15, 20, 931351171509952)
        assert len(request['references']) == 6
        assert [r['path'] for r in request['references'][:3]]==['/cache/archived-opening.png','/source/timeline.mp4','/source/ending.png']
        assert [r['timeline_time'] for r in request['references'][:3]]==[0,3,10]
        assert request['references'][1]['start']==2 and request['references'][1]['duration']==4
        assert request['references'][3]['name'] == 'Existing character'
        assert request['references'][4]['audio_path'] == '/source/paired.wav'
        assert request['references'][5]['kind'] == 'audio' and request['references'][5]['path'] == '/source/only.wav'
        assert w.unified_prompt.toPlainText() == authored
        assert w.minimax_reference_images[3]['kind'] == 'video'
        # Live frames use the existing video surface without attaching a PNG
        # as the saved project video. Results from a stale workspace are ignored.
        from PySide6.QtGui import QImage
        with tempfile.TemporaryDirectory() as folder:
            frame = QImage(32, 18, QImage.Format.Format_RGB32); frame.fill(0xff808040)
            path = str(Path(folder) / 'latent.png'); assert frame.save(path)
            event = {'kind': 'preview', 'path': path, 'media': 'image', 'preview_mode': 'noisy', 'step': 2}
            callbacks['progress'](event)
            panel = w.project_preview_panel
            assert 'live noisy latent' in panel.title.text()
            assert panel.pending_preview_path == '' and not panel.export.isEnabled()
            assert panel.video.videoSink().videoFrame().isValid()
            assert panel.player.source().isEmpty()
            w.workspace_revision += 1
            with patch.object(panel.video.videoSink(), 'setVideoFrame') as show:
                callbacks['progress'](event); show.assert_not_called()
            w.workspace_revision -= 1
        failed('Diagnostic test finished')
        assert not w._local_video_busy
        setup.video_random_seed.setChecked(True)
        assert setup.value()['video_seed'] == -1 and not setup.video_seed.isEnabled()
        log = GenerationLog()
        log.receive_event({'kind': 'log', 'message': '<b>checkpoint loading</b>'})
        log.receive_event({'kind': 'error', 'message': 'CUDA failure details'})
        assert '<b>checkpoint loading</b>' in log.output.toPlainText()
        assert 'CUDA failure details' in log.output.toPlainText()
        log.clear(); assert not log.output.toPlainText()
