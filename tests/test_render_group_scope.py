"""Selected group rendering preserves all other authored groups and attachments."""
from pathlib import Path
from unittest.mock import patch
import copy
import time
import av
import numpy as np
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from ltx_prompt_director.ui import MainWindow
from ltx_prompt_director.models import Segment

APP=QApplication.instance() or QApplication([])


def wait_for(condition):
    for _ in range(1500):
        APP.processEvents()
        if condition():return
        time.sleep(.005)
    assert condition()


def test_selected_group_only_local_and_network_snapshot_and_attachment(tmp_path):
    settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat)
    with patch('ltx_prompt_director.ui.QSettings',return_value=settings),patch.object(MainWindow,'restore_startup_workspace'):
        w=MainWindow();w._close_saves_queued=True
    runs=[]
    def execute(operation,args,ready,failed,**kwargs):runs.append((operation,args,ready,failed,kwargs))
    w.render_queue.executor=execute
    movie=tmp_path/'render.mp4'
    with av.open(str(movie),'w') as output:
        stream=output.add_stream('libx264',rate=24);stream.width=128;stream.height=96;stream.pix_fmt='yuv420p'
        for index in range(24):
            for packet in stream.encode(av.VideoFrame.from_ndarray(np.full((96,128,3),80,np.uint8),format='rgb24')):output.mux(packet)
        for packet in stream.encode():output.mux(packet)
    try:
        w.set_project_type('minimax_frames')
        w.segments=[Segment('One','','','text','text','First segment.',4,render_group=0),Segment('Two','','','text','text','Second segment.',4,render_group=1)]
        w.video_extensions=1;w.requested_length.setValue(4)
        w.render_group_options={str(g):dict(requestedLength=4,blend='cut',context=w.capture_render_group()) for g in (0,1)}
        for g in (0,1):w.render_group_options[str(g)]['context']['minimaxH3']['prompt']=f'Group {g+1} authored production.'
        w.minimax_prompt_text='Group 1 authored production.';w.refresh_timeline();w.save_library_project(automatic=True)
        w.resize(1450,950);w.show();w.grouped_sequence.refresh();APP.processEvents();w.refresh_render_scope()
        assert w.render_scope.count()==3
        w.render_scope.setCurrentIndex(w.render_scope.findData(1))
        before=copy.deepcopy(w.render_group_options['0']);prompts=[s.prompt for s in w.segments]
        profile=dict(video_model=str(tmp_path/'minimax_fl2va_pruned.safetensors'),video_variant='fl2va',video_duration=4,video_width=960,video_height=544,video_steps=4,video_seed=42)
        with patch('ltx_prompt_director.ui.load_profile',return_value=profile),patch('ltx_prompt_director.ui.validate_profile'):
            w.local_video_generate.click()
        assert len(runs)==1 and len(w.render_queue.jobs)==1
        job=w.render_queue.active;assert 'Group 2' in job.title
        with patch('ltx_prompt_director.ui.run_request',return_value=dict(path=str(movie))) as render:
            result=runs[0][0]();request=render.call_args.args[0]
        assert request['prompt']=='Group 2 authored production.' and request['duration']==4
        runs[0][2](result);wait_for(lambda:w.render_group_options['1'].get('clip'))
        assert w.render_group_options['0']==before and [s.prompt for s in w.segments]==prompts
        assert Path(w.render_group_options['1']['clip']['path']).is_file()
        # Queue submission captures the chosen host for the same group-only path.
        peer=dict(id='host',name='PC2',url='ws://127.0.0.1:1',token='fixture',profile=profile)
        w.network_render._peer(peer);w.network_render.combo.setCurrentIndex(1)
        w.render_scope.setCurrentIndex(w.render_scope.findData(1));w.local_video_generate.click()
        assert len(runs)==2 and w.render_queue.active.source=='network' and w.render_queue.active.remote_target['name']=='PC2'
        assert 'Group 2' in w.render_queue.active.title
        w.render_queue.cancel_job(w.render_queue.active.id);runs[-1][3]('Cancelled')
        w.project_preview_dock.show();w.project_preview_dock.raise_();APP.processEvents()
        w.project_preview_panel.grab().save(str(tmp_path/'render-scope.png'))
    finally:
        w.grouped_sequence.timer.stop();w.shot_sequence_panel.deactivate();w.render_queue.shutdown();w.close()
