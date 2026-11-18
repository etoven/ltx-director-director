"""One native render-selector scenario; no model or media decoding."""
import copy
from unittest.mock import patch
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from ltx_prompt_director import local_generation,ui
from ltx_prompt_director.models import Segment
APP=QApplication.instance() or QApplication([])


def test_missing_group_selector_preserves_attachments_and_queues_only_gaps(tmp_path,monkeypatch):
    model=tmp_path/'minimax_fl2va_pruned.safetensors';model.touch()
    profile=local_generation.default_profile();profile.update(video_model=str(model),video_variant='fl2va',video_duration=4)
    monkeypatch.setattr(ui,'load_profile',lambda settings:dict(profile));monkeypatch.setattr(ui,'validate_profile',lambda *args:None)
    monkeypatch.setattr(ui,'project_library_path',lambda:tmp_path/'projects')
    settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat)
    with patch('ltx_prompt_director.ui.QSettings',return_value=settings),patch.object(ui.MainWindow,'restore_startup_workspace'):
        w=ui.MainWindow();w._close_saves_queued=True
    try:
        w.set_project_type('minimax_frames');w.current_project_id='missing-groups'
        w.segments=[Segment(str(group),'','','text','text',f'Keep authored group {group}.',4,render_group=group) for group in range(5)]
        w.video_extensions=4;w.requested_length.setValue(4);w.minimax_prompt_text='Production prose.'
        context=w.capture_render_group()
        attached=tmp_path/'attached.mp4';attached.write_bytes(b'existing attachment is not re-rendered')
        w.render_group_options={str(g):dict(requestedLength=4,context=copy.deepcopy(context)) for g in range(5)}
        w.render_group_options['0']['clip']=dict(path=str(attached),duration=4,trim_start=0)
        w.render_group_options['3']['clip']=dict(path=str(attached),duration=4,trim_start=12)
        w.render_group_options['4']['clip']=dict(path=str(tmp_path/'missing.mp4'),duration=4)
        before=[s.to_dict() for s in w.segments];clips={g:copy.deepcopy(w.render_group_options[g]['clip']) for g in ('0','3')}
        w.refresh_timeline();w.render_queue.set_paused(True)
        runs=[];w.render_queue.executor=lambda *a,**kw:runs.append(a)
        w.render_scope.setCurrentIndex(w.render_scope.findData('__missing_group_videos__'))
        w.local_video_generate.click()
        jobs=w.render_queue.jobs
        assert [job.render_group for job in jobs]==['1','2','4']
        assert jobs[0].portable['continuation']['path']==str(attached)
        assert jobs[1].dependencies==(jobs[0].id,)
        assert jobs[2].portable['continuation']['path']==str(attached)
        assert jobs[2].dependencies==()
        assert len({j.group_id for j in jobs})==1 and jobs[0].group_id
        assert w.render_scope.currentData()=='__missing_group_videos__'
        w.refresh_render_scope();assert w.render_scope.currentData()=='__missing_group_videos__'
        w.local_video_generate.click();assert len(jobs)==3  # No duplicate pending work.
        assert not runs and w.render_queue.paused
        assert [s.to_dict() for s in w.segments]==before
        assert all(w.render_group_options[g]['clip']==clips[g] for g in clips)
        assert attached.read_bytes()==b'existing attachment is not re-rendered'
        # Every group attached: the real button is a harmless no-op.
        w.render_queue.jobs.clear()
        for g in range(5):w.render_group_options[str(g)]['clip']=dict(path=str(attached),duration=4)
        w.local_video_generate.click();assert not w.render_queue.jobs
    finally:
        w.queue_store.frozen=True;w.queue_store.timer.stop();w.render_queue.shutdown();w.close()
