import io
import json
from pathlib import Path
from fractions import Fraction
import threading
import av
import numpy as np
import pytest
from ltx_prompt_director.shot_sequence import (blend_frame,join_audio,overlaps,render_sequence,
    ensure_collection_sequences,probe,DEFAULTS)


def movie(path,color,frequency=440,audio=True):
    with av.open(str(path),'w') as c:
        v=c.add_stream('mpeg4',rate=24);v.width=96;v.height=64;v.pix_fmt='yuv420p'
        a=c.add_stream('aac',rate=48000) if audio else None
        if a:a.layout='stereo'
        sample=0
        for i in range(36):
            f=av.VideoFrame.from_ndarray(np.full((64,96,3),color,dtype=np.uint8),format='rgb24')
            f.pts=i;f.time_base=Fraction(1,24)
            for p in v.encode(f):c.mux(p)
            if a:
                t=np.arange(sample,sample+2000)/48000
                data=np.stack([.1*np.sin(2*np.pi*frequency*t)]*2).astype('float32')
                af=av.AudioFrame.from_ndarray(data,format='fltp',layout='stereo');af.sample_rate=48000;af.pts=sample;af.time_base=Fraction(1,48000)
                for p in a.encode(af):c.mux(p)
                sample+=2000
        for stream in [v]+([a] if a else []):
            for p in stream.encode():c.mux(p)

@pytest.fixture
def movies(tmp_path):
    a,b=tmp_path/'a.mp4',tmp_path/'b.mp4'
    movie(a,[220,20,10]);movie(b,[10,30,220],660)
    return [str(a),str(b)]

@pytest.mark.parametrize('mode',['cut','linear_blend','filmic_crossfade','perceptual_crossfade'])
def test_ram_video_and_original_audio(movies,mode):
    data=render_sequence(movies,dict(blend=mode,overlap=.5,width=96,height=64),preview=True)
    assert isinstance(data,bytes) and data[:4]==b'\x1aE\xdf\xa3'
    with av.open(io.BytesIO(data)) as c:
        assert c.streams.audio[0].codec_context.name=='opus'
        frames=list(c.decode(video=0))
        assert len(frames)==60
    with av.open(io.BytesIO(data)) as c:
        samples=np.concatenate([frame.to_ndarray() for frame in c.decode(audio=0)],axis=1)
        assert np.sqrt(np.mean(samples**2))>.02


def test_baking_webm_and_silent_source(movies,tmp_path):
    silent=tmp_path/'silent.mp4';movie(silent,[40,160,20],audio=False)
    out=tmp_path/'baked.webm'
    assert render_sequence([movies[0],str(silent)],dict(overlap=.5,width=96,height=64,crf=15),str(out))==str(out)
    assert probe(out)['frames']==60
    assert not list(tmp_path.glob('*.tmp'))


def test_perceptual_and_filmic_are_distinct_from_linear():
    a=np.zeros((2,2,3),dtype=np.uint8);b=np.full_like(a,255)
    outputs=[blend_frame(a,b,.5,mode,'source') for mode in ('linear_blend','filmic_crossfade','perceptual_crossfade')]
    assert len({int(o[0,0,0]) for o in outputs})==3
    assert np.array_equal(blend_frame(a,b,.25,'linear_blend','destination'),blend_frame(b,a,.25,'linear_blend','source'))


def test_cut_side_and_clamped_short_overlap():
    a=np.ones((2,8),dtype=np.float32);b=np.zeros((2,8),dtype=np.float32)
    assert np.count_nonzero(join_audio(a,b,4,'cut','source'))==8
    assert np.count_nonzero(join_audio(a,b,4,'cut','destination'))==16
    assert overlaps([{'frames':8},{'frames':4}],10)==([4],8)


def test_one_collection_sequence_and_cancel(movies,tmp_path):
    records=[{'collection':'One'},{'collection':'Two'}]
    added=ensure_collection_sequences(tmp_path,records)
    assert len(added)==2
    assert not ensure_collection_sequences(tmp_path,records+added)
    assert all(json.loads(Path(r['projectPath']).read_text())['shotSequence']==DEFAULTS for r in added)
    cancel=threading.Event();cancel.set()
    with pytest.raises(RuntimeError,match='cancelled'):
        render_sequence(movies,dict(width=96,height=64),cancel=cancel)


def test_workspace_panel_memory_player_and_portable_save(movies,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QElapsedTimer,QEventLoop
    from ltx_prompt_director import ui
    from ltx_prompt_director.workspaces import WorkspaceStore
    from ltx_prompt_director.shot_sequence_widgets import import_shots
    from ltx_prompt_director.project_archive import save_project_archive,read_project
    app=QApplication.instance() or QApplication([])
    store=WorkspaceStore(tmp_path/'ws')
    monkeypatch.setattr(ui,'WorkspaceStore',lambda:store)
    monkeypatch.setattr(ui.MainWindow,'restore_startup_workspace',lambda self:None)
    monkeypatch.setattr(ui,'project_library_path',lambda:tmp_path)
    w=ui.MainWindow()
    try:
        w.set_project_type('shot_sequence')
        assert w.maximum_sequence_duration is None
        w.segments=import_shots(movies,tmp_path/'sources')
        w.shot_sequence_settings={**DEFAULTS,'overlap':.5,'width':96,'height':64}
        w.refresh_timeline();p=w.shot_sequence_panel
        assert not p.isHidden() and w.intent_prompt_splitter.isHidden()
        assert p.blend.currentText()=='perceptual_crossfade'
        first=w.segments[0].id
        p.shots.setCurrentRow(0);p.move(1)
        assert w.segments[1].id==first
        payload=w.project_payload(False)
        archive=tmp_path/'sequence.LTXD'
        save_project_archive(archive,payload,w.segments)
        assert read_project(archive)['shotSequence']['overlap']==.5
        state=w.capture_workspace_state()
        w.shot_sequence_settings={**DEFAULTS,'overlap':2}
        w.restore_workspace_state(state)
        assert w.shot_sequence_settings['overlap']==.5
        p.build_preview()
        assert not p.preview_progress.isHidden()
        timer=QElapsedTimer();timer.start()
        while (p.preview.duration==0 or p.preview_progress.value()!=100) and timer.elapsed()<5000:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents,20)
        assert p.preview_progress.value()==100
        assert p.preview.duration>2000
        assert p.preview.player.audioOutput() is p.preview.audio
        timer.restart()
        while p.preview.current_frame is None and timer.elapsed()<3000:
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents,20)
        assert p.preview.current_frame is not None
        p.preview.player.pause()
        assert p.preview._memory_buffer.isOpen()
        assert not p.preview.player.source().isLocalFile()
        p.preview.seek_to(1200)
        assert p.preview.player.position()==1200
        saved=[]
        monkeypatch.setattr(w,'next_export_path',lambda _:tmp_path/'ui-final.webm')
        monkeypatch.setattr(w,'record_export',lambda path:saved.append(path))
        p.bake()
        assert not p.bake_progress.isHidden() and not p.bake_button.isEnabled()
        timer.restart()
        while not saved and timer.elapsed()<5000:app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents,20)
        assert saved==[tmp_path/'ui-final.webm']
        assert p.bake_progress.value()==100 and p.bake_button.isEnabled()
        p.deactivate()
        assert p.preview._memory_buffer is None and p.preview_progress.isHidden()
    finally:
        w.project_dirty=False;w._close_saves_queued=True;w.close()


def test_worker_restores_archived_movies_and_respects_clip_budget(movies,tmp_path):
    from ltx_prompt_director.shot_sequence_widgets import import_shots,render_shots,render_preview
    from ltx_prompt_director.project_archive import save_project_archive
    shots=import_shots(movies,tmp_path/'originals')
    for shot in shots:shot.duration=1
    payload={'app':'ltx-director-director','projectType':'shot_sequence','frames':[s.to_dict() for s in shots]}
    save_project_archive(tmp_path/'portable.LTXD',payload,shots)
    for shot in shots:Path(shot.media_path).unlink()
    data=render_shots(shots,dict(width=96,height=64,overlap=.5),preview=True)
    with av.open(io.BytesIO(data)) as container:
        assert len(list(container.decode(video=0)))==36
    cancel=threading.Event();cancel.set()
    assert render_preview(shots,DEFAULTS,cancel) is None


def test_video_duration_uses_video_timestamps_not_container(tmp_path):
    # Fifteen-second WebM, with timestamps starting at ten seconds and no stream
    # duration. Counting 24fps frames at a different source rate would also fail.
    path=tmp_path/'offset.webm'
    with av.open(str(path),'w') as container:
        stream=container.add_stream('libvpx-vp9',rate=12)
        stream.width=32;stream.height=32;stream.pix_fmt='yuv420p'
        stream.options={'deadline':'realtime','cpu-used':'8'}
        for i in range(180):
            frame=av.VideoFrame.from_ndarray(np.full((32,32,3),i%255,dtype=np.uint8),format='rgb24')
            frame.pts=120+i;frame.time_base=Fraction(1,12)
            for packet in stream.encode(frame):container.mux(packet)
        for packet in stream.encode():container.mux(packet)
    info=probe(path)
    assert info['frames']==360
    assert info['start_time']==pytest.approx(10)
    from ltx_prompt_director.shot_sequence_widgets import import_shots
    shots=import_shots([str(path)],tmp_path/'sources')
    assert shots[0].duration==15 and shots[0].trim_start==0
    data=render_sequence([str(path)],dict(width=32,height=32),preview=True)
    with av.open(io.BytesIO(data)) as container:
        assert len(list(container.decode(video=0)))==360


def test_render_progress_reports_real_work_and_completion(movies,tmp_path):
    for output in (None,str(tmp_path/'final.webm')):
        events=[]
        render_sequence(movies,dict(width=96,height=64,overlap=.5),output,preview=output is None,
                        progress=lambda value,stage:events.append((value,stage)))
        percentages=[value for value,_ in events]
        assert percentages==sorted(percentages)
        assert percentages[0]==0 and percentages[-1]==100
        assert len(set(percentages))>30
        assert {'Reading source movies','Preparing source audio','Encoding video and audio','Finishing WebM','Complete'} <= {s for _,s in events}


def test_empty_collection_tile_and_full_clip_recovery(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QApplication
    from ltx_prompt_director import ui
    from ltx_prompt_director.workspaces import WorkspaceStore
    from ltx_prompt_director.models import Segment
    app=QApplication.instance() or QApplication([])
    store=WorkspaceStore(tmp_path/'ws')
    monkeypatch.setattr(ui,'WorkspaceStore',lambda:store)
    monkeypatch.setattr(ui.MainWindow,'restore_startup_workspace',lambda self:None)
    monkeypatch.setattr(ui,'project_library_path',lambda:tmp_path)
    w=ui.MainWindow()
    try:
        record=ensure_collection_sequences(tmp_path,[{'collection':'Finished film'}])[0]
        w.load_project_payload(json.loads(Path(record['projectPath']).read_text()))
        assert w.workspace_engine=='shot_sequence' and w.has_project_content
        assert not w.shot_sequence_panel.isHidden()
        # Simulate loading an old sequence while MiniMax (15s generation budget)
        # is active. The clips must recover their full imported lengths.
        w.set_project_type('minimax_base_guide')
        frames=[Segment(f'Shot {i}','missing.mp4','',kind='video',duration=1.5,media_duration_frames=360,trim_start=0).to_dict() for i in range(10)]
        w.load_project_payload({'app':'ltx-director-director','projectType':'shot_sequence','frames':frames,'shotSequence':DEFAULTS})
        assert [s.duration for s in w.segments]==[15]*10
        assert w.total_duration()==150 and w.maximum_sequence_duration is None
        p=w.shot_sequence_panel
        assert p.shots.total==141 # nine 1-second overlaps
        assert p.shots.clips[1]['start']==14
        assert p.shots.clip_rect(1).x()==pytest.approx(p.shots.LEFT+14*p.shots.scale)
        assert p.shots.clip_rect(0,True).y()>p.shots.clip_rect(1).y()
        ids=[s.id for s in w.segments]
        p.shots.reorder([0,1],4)
        assert [s.id for s in w.segments][:4]==ids[2:4]+ids[:2]
        p.shots.set_playhead(22000)
        assert p.shots.playhead==22
    finally:
        w.project_dirty=False;w._close_saves_queued=True;w.close()


def test_preview_play_pause_does_not_reinitialize_on_buffering(movies):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtMultimedia import QMediaPlayer
    from PySide6.QtCore import QElapsedTimer,QEventLoop
    from ltx_prompt_director.ui import ProjectPreviewPanel
    app=QApplication.instance() or QApplication([])
    panel=ProjectPreviewPanel()
    try:
        panel.set_memory_video('Replay',render_sequence(movies,dict(width=96,height=64,overlap=.5),preview=True))
        def until(predicate,timeout=4000):
            timer=QElapsedTimer();timer.start()
            while not predicate() and timer.elapsed()<timeout:app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents,20)
            assert predicate()
        until(lambda:panel.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState)
        panel.toggle_playback()
        until(lambda:panel.player.playbackState()==QMediaPlayer.PlaybackState.PausedState)
        panel.player.setPosition(0)
        panel.media_status_changed(QMediaPlayer.MediaStatus.BufferedMedia)
        assert panel.player.playbackState()==QMediaPlayer.PlaybackState.PausedState
        panel.toggle_playback()
        until(lambda:panel.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState)
        panel.toggle_playback()
        until(lambda:panel.player.playbackState()==QMediaPlayer.PlaybackState.PausedState)
        panel.player.setPosition(panel.duration-40);panel.toggle_playback()
        until(lambda:panel.player.mediaStatus()==QMediaPlayer.MediaStatus.EndOfMedia)
        panel.toggle_playback()
        until(lambda:panel.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState)
        assert panel.player.position()<panel.duration-100
    finally:
        panel.clear_project();panel.close()
