import copy
from pathlib import Path
from unittest.mock import patch
import av
import numpy as np
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from ltx_prompt_director.models import Segment
from ltx_prompt_director.ui import MainWindow
from ltx_prompt_director.blend_timing import blend_frames

APP=QApplication.instance() or QApplication([])


def movie(path,color):
    with av.open(str(path),'w') as container:
        stream=container.add_stream('libx264',rate=24);stream.width=96;stream.height=64;stream.pix_fmt='yuv420p'
        for i in range(96):
            pixels=np.zeros((64,96,3),np.uint8);pixels[:,:,color]=180+i%70
            frame=av.VideoFrame.from_ndarray(pixels,format='rgb24');frame.pts=i
            for packet in stream.encode(frame):container.mux(packet)
        for packet in stream.encode():container.mux(packet)
    return str(path)


def test_shared_group_native_workflow(tmp_path):
    assert [blend_frames(v) for v in [0,1,1.5]]==[5,22,39]
    assert blend_frames(30.5/24)==22
    settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat)
    with patch('ltx_prompt_director.ui.QSettings',return_value=settings),patch.object(MainWindow,'restore_startup_workspace'):
        w=MainWindow()
    w._close_saves_queued=True
    w.resize(1400,1000);w.show();APP.processEvents()
    try:
        w.set_project_type('minimax_frames')
        w.segments=[Segment('First','','','text','text','First authored prompt',4,render_group=0),Segment('Second','','','text','text','Second authored prompt',4,render_group=1),Segment('Third','','','text','text','Third authored prompt',4,render_group=2)]
        w.video_extensions=2;w.requested_length.setValue(4)
        w.render_group_options={str(g):{'requestedLength':4,'context':w.capture_render_group()} for g in range(3)}
        w.render_group_options['0']['clip']={'path':movie(tmp_path/'first.mp4',0),'duration':4,'media_duration_frames':96}
        w.render_group_options['1']['clip']={'path':movie(tmp_path/'second.mp4',1),'duration':4,'media_duration_frames':96}
        w.shot_sequence_settings.update(width=96,height=64)
        w.refresh_timeline();w.grouped_sequence.refresh()
        assert w.audio_panel.isHidden()
        assert len(w.extension_brackets.spans())==3 and len(w._extension_boxes)==2
        assert w.extension_transition(1)['overlap']==22/24
        assert len(w.shot_sequence_panel.sequence_shots)==3
        w.grouped_sequence.activate_timeline(0,True)
        import time
        for _ in range(500):
            APP.processEvents();time.sleep(.02)
            if w.shot_sequence_panel._available_frames==w.shot_sequence_panel._plan.count:break
        assert w.shot_sequence_panel._available_frames==w.shot_sequence_panel._plan.count, (w.project_preview_panel.empty.text(),w.shot_sequence_panel.preview.empty.text(),w.shot_sequence_panel._worker_running)
        assert w.project_preview_panel._memory_preview
        assert w.project_preview_panel.player.videoOutput() is w.project_preview_panel.video
        assert not w.project_preview_panel.video.isWindow()
        assert any(not ready for _,_,ready in w.timeline_cache_coverage.ranges)
        # Real Add Video control replaces the selected group's attachment.
        w.save_library_project(automatic=True)
        for _ in range(300):
            APP.processEvents();time.sleep(.01)
            if w._pending_disk_jobs==0:break
        replacement=movie(tmp_path/'replacement.mp4',2)
        w._active_render_group=1
        with patch('ltx_prompt_director.ui.choose_document_open',return_value=replacement):
            w.project_preview_panel.choose.click()
        for _ in range(400):
            APP.processEvents();time.sleep(.01)
            if w.render_group_options['1'].get('attachmentRevision')==1 and w._pending_disk_jobs==0:break
        assert w.render_group_options['1']['clip']['path']!=str(tmp_path/'second.mp4')
        assert Path(w.render_group_options['1']['clip']['path']).is_file()
        assert w.render_group_options['2']['dependencyRevision']==1
        assert w.segments[1].prompt=='Second authored prompt'
        # An older generated completion cannot overwrite that manual replacement.
        attached=w.render_group_options['1']['clip']['path']
        w.attach_group_video(str(tmp_path/'first.mp4'),1,expected_revision=0)
        for _ in range(300):
            APP.processEvents();time.sleep(.01)
            if w._pending_disk_jobs==0:break
        assert w.render_group_options['1']['clip']['path']==attached
        # Model jobs only: blends are handled by the shared preview cache.
        for options in w.render_group_options.values():options['context']['minimaxH3']['prompt']='Production prose.'
        jobs=[]
        from types import SimpleNamespace
        def enqueue(*args,**kwargs):
            jobs.append(kwargs)
            return SimpleNamespace(id=str(len(jobs)),output_path=str(tmp_path/'first.mp4'))
        profile=dict(video_model=str(tmp_path/'minimax_fl2va_pruned.safetensors'),video_variant='fl2va',video_duration=4,video_width=960,video_height=544,video_steps=4,video_seed=42)
        w.minimax_prompt_text='Production prose.'
        with patch('ltx_prompt_director.ui.load_profile',return_value=profile),patch('ltx_prompt_director.ui.validate_profile'),patch.object(w,'start_render_job',side_effect=enqueue):
            w.generate_local_video()
        assert len(jobs)==3 and all(job['model']=='MiniMax H3' for job in jobs)
        before=[s.prompt for s in w.segments]
        w.set_project_type('shot_sequence');APP.processEvents()
        assert len(w.segments)==3 and w.segments[0].kind=='video'
        ident=w.segments[0].id
        w.shot_sequence_panel.trim_shot(ident,12,84)
        w.shot_sequence_panel.split_shot(w.segments[1].id,48)
        assert len(w.segments)==4
        w.set_project_type('minimax_frames');APP.processEvents()
        assert len(w.render_groups())==4
        assert w.segments[0].prompt==before[0]
        assert w.render_group_options['0']['clip']['trim_start']==12
        assert [round(sum(s.duration for s in items),4) for items in w.render_groups().values()]==[3,2,2,4]
        assert abs(sum(w.grouped_sequence.display_durations())-w.shot_sequence_panel.shots.total)<1/24
        snapshot=w.project_payload(False)
        assert snapshot['groupedSequence']['authoring']
        w.load_project_payload(copy.deepcopy(snapshot));w.grouped_sequence.refresh()
        assert len(w.grouped_sequence.shots)==4
        w.set_conditioning_height(180);APP.processEvents()
        assert w.conditioning_guide.verticalScrollBarPolicy()==Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        assert w.timeline.verticalScrollBarPolicy()==Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        assert w.timeline_bottom_scroll.y()>w.conditioning_guide.y()
        assert not w.project_preview_panel.play.isHidden()
        w.project_preview_panel.set_rendering(True)
        assert all(control.isHidden() for control in (w.project_preview_panel.play,w.project_preview_panel.seek,w.project_preview_panel.fullscreen))
        w.project_preview_panel.set_rendering(False)
        w.grouped_sequence.show_group()
        w.project_preview_panel.player.play()
        for _ in range(100):
            APP.processEvents();time.sleep(.01)
            if w.project_preview_panel.current_frame is not None:break
        assert w.project_preview_panel.current_frame is not None
        w.grab().save(str(tmp_path/'grouped-native.png'))
    finally:
        w.grouped_sequence.timer.stop();w.shot_sequence_panel.deactivate();w.render_queue.shutdown();w.close()
