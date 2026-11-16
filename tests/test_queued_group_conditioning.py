"""Separately submitted groups resolve queued predecessors only after completion."""
from unittest.mock import patch
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from ltx_prompt_director import local_generation, ui
from ltx_prompt_director.models import Segment

APP=QApplication.instance() or QApplication([])


@pytest.mark.parametrize('outcome', ['Complete','Failed','Cancelled'])
def test_text_only_head_and_queued_group_chain(tmp_path, monkeypatch, outcome):
    model=tmp_path/'minimax_fl2va_pruned.safetensors';model.touch()
    profile=local_generation.default_profile()
    profile.update(video_model=str(model),video_variant='fl2va',video_duration=4)
    monkeypatch.setattr(ui,'load_profile',lambda settings:dict(profile))
    monkeypatch.setattr(ui,'validate_profile',lambda *args:None)
    settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat)
    with patch('ltx_prompt_director.ui.QSettings',return_value=settings),patch.object(ui.MainWindow,'restore_startup_workspace'):
        w=ui.MainWindow();w._close_saves_queued=True
    runs=[];requests=[];tails=[]
    try:
        w.set_project_type('minimax_frames');w.current_project_id='queued-project'
        w.segments=[Segment(f'Group {group+1}','','','text','text',f'Keep prompt {group}.',4,render_group=group) for group in range(3)]
        w.video_extensions=2;w._group_video_revision=w.workspace_revision
        w.requested_length.setValue(4);w.minimax_prompt_text='Production prose.'
        w.render_group_options={str(group):{'requestedLength':4,'context':w.capture_render_group()} for group in range(3)}
        w.refresh_timeline();w.show();APP.processEvents()
        w.render_queue.executor=lambda op,args,ready,failed,**kw:runs.append((op,args,ready,failed))
        w.render_queue.set_paused(True)
        def render(request, **kwargs):
            requests.append(request)
            path=tmp_path/f'completed-{len(requests)}.mp4';path.write_bytes(b'fixture')
            return {'path':str(path)}
        def reference(path,marked,overlap,**kwargs):
            tails.append(path)
            return dict(kind='video',path=path,audio_path=path,timeline_time=0,start=marked-overlap,duration=overlap)
        with patch('ltx_prompt_director.ui.run_request',side_effect=render),patch('ltx_prompt_director.video_extensions.continuation_reference',side_effect=reference),patch.object(w,'attach_group_video'),patch.object(w.project_preview_panel,'set_project'):
            for group in range(3):
                w.render_scope.setCurrentIndex(w.render_scope.findData(group))
                w.local_video_generate.click()
            jobs=w.render_queue.jobs
            assert len(jobs)==3 and [job.render_group for job in jobs]==['0','1','2']
            assert jobs[0].dependencies==()  # Text-only sequence head needs no start image.
            assert jobs[1].dependencies==(jobs[0].id,) and jobs[2].dependencies==(jobs[1].id,)
            assert all(job.state=='Waiting' for job in jobs) and not runs and not tails
            w.render_queue.set_paused(False);APP.processEvents()
            assert len(runs)==1 and jobs[1].state=='Waiting'
            if outcome=='Complete':
                for index in range(3):
                    op,args,ready,failed=runs[index]
                    ready(op(*args));APP.processEvents()
                assert all(job.state=='Complete' for job in jobs)
                assert requests[0]['conditioning']==[] and requests[0]['references']==[]
                assert tails==[jobs[0].output_path,jobs[1].output_path]
                for index in (1,2):
                    condition=requests[index]['conditioning'][0]
                    assert condition['path']==jobs[index-1].output_path and condition['timeline_time']==0
                    assert 'audio_path' not in condition
                assert all(request['profile']['video_variant']=='fl2va' for request in requests)
            else:
                if outcome=='Cancelled':w.render_queue.cancel_job(jobs[0].id)
                runs[0][3]('Upstream fixture failure');APP.processEvents();APP.processEvents()
                assert jobs[0].state==outcome
                assert jobs[1].state==jobs[2].state=='Cancelled'
                assert not requests and not tails and len(runs)==1
    finally:
        w.grouped_sequence.timer.stop();w.shot_sequence_panel.deactivate();w.render_queue.shutdown();w.close()
