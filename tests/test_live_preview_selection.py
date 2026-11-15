from pathlib import Path
from unittest.mock import patch
import av
import numpy as np
from PySide6.QtCore import QSettings, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ltx_prompt_director.ui import MainWindow
from ltx_prompt_director.models import Segment
from ltx_prompt_director.grouped_sequence import GroupedSequence

APP=QApplication.instance() or QApplication([])


def test_group_clicks_and_deselection_preserve_live_stream_and_terminal_restore(tmp_path):
    settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat)
    with patch('ltx_prompt_director.ui.QSettings',return_value=settings),patch.object(MainWindow,'restore_startup_workspace'),patch.object(GroupedSequence,'changed'):
        w=MainWindow();w._close_saves_queued=True
        try:
            w.set_project_type('minimax_frames')
            w.segments=[Segment('First','','','text','text','Keep first wording.',4,render_group=0),Segment('Second','','','text','text','Keep second wording.',4,render_group=1)]
            w.video_extensions=1;w._group_video_revision=w.workspace_revision
            w.requested_length.setValue(4);w.minimax_prompt_text='Production prose.'
            attachment=tmp_path/'attached.mp4'
            with av.open(str(attachment),'w') as movie:
                stream=movie.add_stream('libx264',rate=24);stream.width=128;stream.height=96;stream.pix_fmt='yuv420p'
                for index in range(24):
                    frame=av.VideoFrame.from_ndarray(np.full((96,128,3),120,np.uint8),format='rgb24');frame.pts=index
                    for packet in stream.encode(frame):movie.mux(packet)
                for packet in stream.encode():movie.mux(packet)
            w.render_group_options={str(group):{'requestedLength':4,'context':w.capture_render_group(),'clip':{'path':str(attachment),'duration':1}} for group in range(2)}
            w.refresh_timeline();w.resize(1500,1000);w.show();APP.processEvents()
            w.timeline_scale.setValue(24);APP.processEvents()
            original=[segment.prompt for segment in w.segments]
            profile=dict(video_model=str(tmp_path/'minimax_fl2va_pruned.safetensors'),video_variant='fl2va',video_duration=4,video_width=960,video_height=544,video_steps=4,video_seed=42)
            runs=[]
            def execute(operation,args,ready,failed,**kwargs):runs.append((ready,failed,kwargs['progress']))
            w.render_queue.executor=execute
            panel=w.project_preview_panel
            frames=[];panel.latent_player.frame_presented.connect(lambda image:frames.append(image.copy()))
            for outcome in ('Complete','Failed','Cancelled'):
                w.render_queue.jobs.clear();w.render_queue.active=None
                w.minimax_prompt_text='Production prose.'
                with patch('ltx_prompt_director.ui.load_profile',return_value=profile),patch('ltx_prompt_director.ui.validate_profile'):
                    w.generate_local_video()
                APP.processEvents()
                job=w.render_queue.active
                assert job is not None and job.state=='Rendering'
                assert job.mode=='fl2va' and job.model_label.startswith('FL2VA')
                assert all(queued.mode=='fl2va' for queued in w.render_queue.jobs)
                ready,failed,progress=runs[-1]
                bank=tmp_path/(outcome+'.rgb')
                bank.write_bytes(bytes((220,30,40))*6*4+bytes((30,220,40))*6*4)
                progress(dict(kind='preview',media='latent_frames',path=str(bank),width=6,height=4,latent_frames=2,frames=22,fps=24,step=1))
                mapping=panel.latent_player.data;generation=panel._source_generation
                panel.latent_player.seek(100);before=len(frames);origin=panel.latent_player.origin
                for row in (1,0,1):
                    rect=w.timeline.visualItemRect(w.timeline.item(row))
                    QTest.mouseClick(w.timeline.viewport(),Qt.MouseButton.LeftButton,pos=rect.center())
                    w.timeline.clearSelection();w.timeline.setCurrentRow(-1)
                    QTest.mouseClick(w.timeline.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(w.timeline.viewport().width()-4,w.timeline.viewport().height()-4))
                    w.update_project_preview()
                    QTest.qWait(90)
                    assert panel.latent_player.data is mapping and not mapping.closed
                    assert panel.pending_preview_path==str(bank)
                    assert panel._source_generation==generation
                    assert panel.latent_player.playing and panel.latent_player.timer.isActive()
                    assert job is w.render_queue.active and not job.cancel.is_set()
                assert len(frames)>before+2 and panel.latent_player.origin==origin
                assert all(control.isHidden() for control in (panel.play,panel.seek,panel.fullscreen))
                assert [segment.prompt for segment in w.segments]==original
                # Terminal callbacks must release the guard; passive group display works again.
                w.render_queue.set_paused(True)
                if outcome=='Complete':
                    with patch.object(w,'attach_group_video'):ready({'path':str(attachment)})
                elif outcome=='Failed':failed('Fixture render failure')
                else:
                    w.render_queue.cancel_job(job.id);failed('Fixture cancellation')
                assert job.state==outcome and not panel._rendering
                w.grouped_sequence.show_group()
                assert not panel.latent_player.active
                assert panel.pending_preview_path==str(attachment)
                w.render_queue.jobs.clear();w.render_queue.set_paused(False)
            w.render_queue.jobs.clear()
        finally:
            w.grouped_sequence.timer.stop();w.shot_sequence_panel.deactivate();w.render_queue.shutdown();w.close()
