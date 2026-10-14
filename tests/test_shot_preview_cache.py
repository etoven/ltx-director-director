import io
import threading
import av
import numpy as np
from ltx_prompt_director.shot_preview_cache import PreviewCache,make_plan
from ltx_prompt_director.shot_sequence_widgets import import_shots
from ltx_prompt_director.shot_sequence import DEFAULTS,render_sequence
from test_shot_sequence import movie


def test_incremental_blends_reorder_and_audio(tmp_path):
    paths=[]
    for i,color in enumerate(([200,10,30],[20,180,30],[20,40,200])):
        path=tmp_path/f'{i}.mp4';movie(path,color,frequency=440+i*110);paths.append(str(path))
    shots=import_shots(paths,tmp_path/'source')
    opts={**DEFAULTS,'width':96,'height':64,'overlap':.5}
    cache=PreviewCache(root=tmp_path/'workfiles')
    plan=make_plan(shots,opts)
    events=[];partials=[]
    data=cache.build(plan,region_ready=events.append,partial=lambda data,count:partials.append((data,count)))
    with av.open(io.BytesIO(data)) as c:
        assert len(list(c.decode(video=0)))==84
    with av.open(io.BytesIO(data)) as c:
        audio=np.concatenate([f.to_ndarray() for f in c.decode(audio=0)],axis=1)
        assert np.sqrt(np.mean(audio**2))>.02
    assert all(ready for _,_,ready in cache.coverage(plan))
    assert cache.stats['source_builds']==3
    builds=cache.stats['region_builds']
    cache.build(plan)
    assert cache.stats['region_builds']==builds and cache.stats['source_builds']==3
    edited=make_plan(shots,{**opts,'blend':'linear_blend'})
    dirty=[p for p in edited.parts if cache.get(('region',p.key)) is None]
    assert dirty and sum(len(p.frames) for p in dirty)==24 # only two overlaps
    cache.build(edited)
    assert cache.stats['source_builds']==3
    assert cache.stats['region_builds']==builds+len(dirty)
    reordered=make_plan([shots[2],shots[1],shots[0]],opts)
    assert any(ready for _,_,ready in cache.coverage(reordered))
    cache.build(reordered)
    assert cache.stats['source_builds']==3
    replacement=tmp_path/'replacement.mp4';movie(replacement,[180,70,190])
    new=import_shots([str(replacement)],tmp_path/'source')[0]
    replaced=make_plan([shots[0],new,shots[2]],opts)
    assert any(ready for _,_,ready in cache.coverage(replaced))
    cache.build(replaced)
    assert cache.stats['source_builds']==4 # only the replaced source is decoded
    assert cache.byte_size<=cache.max_bytes
    assert len(partials)>0
    with av.open(io.BytesIO(partials[0][0])) as c:
        assert len(list(c.decode(video=0)))==partials[0][1]


def test_cancellation_and_cache_budget():
    cache=PreviewCache(max_bytes=10,root=None)
    cache.put('one',b'123456',6);cache.put('two',b'789012',6)
    assert cache.get('one') is None and cache.byte_size==6
    cancel=threading.Event();cancel.set()
    assert cache.build(make_plan([],DEFAULTS),cancel) is None


def test_automatic_gui_cache_and_scrubhead(tmp_path,monkeypatch):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QElapsedTimer,QEventLoop,QPoint,Qt
    from PySide6.QtTest import QTest
    from PySide6.QtMultimedia import QMediaPlayer
    from ltx_prompt_director import ui
    from ltx_prompt_director.workspaces import WorkspaceStore
    app=QApplication.instance() or QApplication([])
    store=WorkspaceStore(tmp_path/'ws')
    monkeypatch.setattr(ui,'WorkspaceStore',lambda:store)
    monkeypatch.setattr(ui.MainWindow,'restore_startup_workspace',lambda self:None)
    monkeypatch.setattr(ui,'project_library_path',lambda:tmp_path)
    paths=[]
    for i,color in enumerate(([200,10,30],[20,180,30],[20,40,200])):
        path=tmp_path/f'clip-{i}.mp4';movie(path,color);paths.append(str(path))
    w=ui.MainWindow()
    def until(predicate):
        timer=QElapsedTimer();timer.start()
        while not predicate() and timer.elapsed()<6000:app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents,20)
        assert predicate()
    try:
        w.shot_sequence_panel.cache=PreviewCache(root=tmp_path/'workfiles')
        w.set_project_type('shot_sequence')
        w.segments=import_shots(paths,tmp_path/'shots')
        w.shot_sequence_settings={**DEFAULTS,'width':96,'height':64,'overlap':.5}
        w.refresh_timeline();p=w.shot_sequence_panel
        assert all(not ready for _,_,ready in p.shots.coverage)
        until(lambda:p._available_frames==84 and not p._worker_running and not p._installing and p.preview.duration>3400)
        assert all(ready for _,_,ready in p.shots.coverage)
        assert p.preview.player.playbackState()==QMediaPlayer.PlaybackState.PausedState
        assert not p.preview.player.source().isLocalFile()
        until(lambda:p.preview.current_frame is not None)
        assert all(len(clip['thumbs'])==2 for clip in p.shots.clips)
        p.seek_sequence(1200)
        until(lambda:p.preview.player.position()==1200)
        assert p.shots.playhead==1.2
        # Dragging the time ruler is continuous scrubbing, never source playback.
        canvas=p.shots.canvas
        x=p.shots.LEFT+round(.5*p.shots.scale)
        QTest.mousePress(canvas,Qt.MouseButton.LeftButton,pos=QPoint(x,15))
        assert p.preview.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState
        assert not p._desired_playing # audio audition does not change transport intent
        x2=p.shots.LEFT+round(1*p.shots.scale)
        QTest.mouseMove(canvas,QPoint(x2,15));QTest.mouseRelease(canvas,Qt.MouseButton.LeftButton,pos=QPoint(x2,15))
        assert abs(p.preview.player.position()-1000)<20
        until(lambda:p.preview.player.playbackState()==QMediaPlayer.PlaybackState.PausedState and abs(p.preview.player.position()-1000)<20)
        assert abs(p.shots.playhead-1)<.02 and not p._desired_playing
        QTest.mouseClick(p.preview.seek,Qt.MouseButton.LeftButton,pos=QPoint(max(1,p.preview.seek.width()*3//4),p.preview.seek.height()//2))
        assert p.preview.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState
        until(lambda:not p._audio_scrub.active)
        assert p.preview.player.playbackState()==QMediaPlayer.PlaybackState.PausedState
        assert not p._desired_playing
        builds=p.cache.stats['source_builds'];region_builds=p.cache.stats['region_builds']
        p.blend.setCurrentText('linear_blend')
        assert sum(end-start for start,end,ready in p.shots.coverage if not ready)==1
        assert any(ready for _,_,ready in p.shots.coverage)
        p.seek_sequence(1800) # retain a seek while that range is rebuilding
        until(lambda:not p._worker_running and not p._installing and p._available_frames==84 and p.preview.duration>3400)
        assert p.cache.stats['source_builds']==builds
        assert p.cache.stats['region_builds']==region_builds+2
        assert p.preview.player.position()==1800
        assert p.shots.playhead==1.8
        revision=p._revision;buffer=p.preview._memory_buffer
        p.crf.setValue(19)
        assert p._revision==revision and p.preview._memory_buffer is buffer
        p.shots.setCurrentRow(1);p.remove()
        until(lambda:not p._worker_running and not p._installing and p._available_frames==60 and p.preview.duration>2400)
        assert p.cache.stats['source_builds']==builds
        p.deactivate()
        assert p.preview._memory_buffer is None and not p._build_timer.isActive()
    finally:
        w.project_dirty=False;w._close_saves_queued=True;w.close()


def test_nested_overlaps_match_source_audio_and_visuals(tmp_path):
    paths=[]
    for i,color in enumerate(([200,10,30],[20,180,30],[20,40,200])):
        path=tmp_path/f'{i}.mp4';movie(path,color,440+i*110);paths.append(str(path))
    shots=import_shots(paths,tmp_path/'shots')
    for shot in shots:shot.duration=.25
    opts={**DEFAULTS,'width':96,'height':64,'overlap':1}
    actual=PreviewCache(root=tmp_path/'workfiles').build(make_plan(shots,opts))
    expected=render_sequence(paths,{**opts,'_frame_limits':[6]*3},preview=True)
    def decode(data):
        with av.open(io.BytesIO(data)) as c:images=np.stack([f.to_ndarray(format='rgb24') for f in c.decode(video=0)])
        with av.open(io.BytesIO(data)) as c:audio=np.concatenate([f.to_ndarray() for f in c.decode(audio=0)],axis=1)
        return images,audio
    a,aa=decode(actual);b,ba=decode(expected)
    assert len(a)==len(b)==6
    assert np.max(np.abs(a.astype(float)-b.astype(float)))<20
    assert aa.shape==ba.shape
    assert np.max(np.abs(aa-ba))<.01


def test_workfiles_survive_reopen_and_corrupt_files_rebuild(tmp_path):
    path=tmp_path/'movie.mp4';movie(path,[180,80,40])
    shot=import_shots([str(path)],tmp_path/'first-import')[0]
    opts={**DEFAULTS,'width':96,'height':64,'overlap':0}
    root=tmp_path/'workfiles';first=PreviewCache(root=root)
    plan=make_plan([shot],opts);first.build(plan)
    assert list(root.glob('*/*.work')) and not list(root.glob('*/*.tmp'))
    # A fresh cache instance and a fresh managed copy reuse the same content.
    reopened=import_shots([str(path)],tmp_path/'second-import')[0]
    other=PreviewCache(root=root);again=make_plan([reopened],opts)
    assert again.signature==plan.signature
    data=other.build(again)
    assert other.stats['source_builds']==0 and other.stats['region_builds']==0
    assert other.stats['region_hits']==len(plan.parts)
    with av.open(io.BytesIO(data)) as c:assert len(list(c.decode(video=0)))==36
    # Damaged disposable workfiles are discarded and regenerated safely.
    key=('region',plan.parts[0].key)
    other.workfiles.path(key).write_bytes(b'corrupt')
    repaired=PreviewCache(root=root);repaired.build(again)
    assert repaired.workfiles.path(key).read_bytes().startswith(b'\x1aE\xdf\xa3')
    assert repaired.stats['source_builds']==0


def test_workfile_disk_budget_prunes_old_entries(tmp_path):
    from ltx_prompt_director.shot_workfiles import Workfiles
    store=Workfiles(tmp_path/'cache',max_bytes=14)
    store.write(('region','one'),b'\x1aE\xdf\xa3' + b'123456')
    store.write(('region','two'),b'\x1aE\xdf\xa3' + b'654321')
    store.prune()
    assert sum(p.stat().st_size for p in (tmp_path/'cache').glob('*/*.work'))<=14
    assert len(list((tmp_path/'cache').glob('*/*.work')))==1


def test_cancelled_build_keeps_completed_regions(tmp_path):
    paths=[]
    for i,color in enumerate(([180,30,30],[20,120,30])):
        path=tmp_path/f'{i}.mp4';movie(path,color);paths.append(str(path))
    shots=import_shots(paths,tmp_path/'shots')
    cache=PreviewCache(root=tmp_path/'cache')
    plan=make_plan(shots,{**DEFAULTS,'width':96,'height':64,'overlap':.5})
    cancel=threading.Event()
    def completed(index):
        if index==1:cancel.set()
    assert cache.build(plan,cancel,region_ready=completed) is None
    assert cache.coverage(plan)[0][2] and cache.coverage(plan)[1][2]
    assert not all(ready for _,_,ready in cache.coverage(plan))
    cache.build(plan)
    assert cache.stats['region_hits']>=2
    assert cache.stats['source_builds']==2


def test_ctrl_drop_replaces_slot_and_configured_status_persists(tmp_path,monkeypatch):
    import json
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QMimeData,QUrl,Qt,QPointF,QElapsedTimer,QEventLoop
    from PySide6.QtGui import QDropEvent
    from ltx_prompt_director import ui
    from ltx_prompt_director.workspaces import WorkspaceStore
    from ltx_prompt_director.project_archive import save_project_archive,read_project
    app=QApplication.instance() or QApplication([])
    store=WorkspaceStore(tmp_path/'ws')
    monkeypatch.setattr(ui,'WorkspaceStore',lambda:store)
    monkeypatch.setattr(ui.MainWindow,'restore_startup_workspace',lambda self:None)
    monkeypatch.setattr(ui,'project_library_path',lambda:tmp_path)
    paths=[]
    for i,color in enumerate(([200,10,30],[20,180,30],[20,40,200],[190,80,160])):
        path=tmp_path/f'{i}.mp4';movie(path,color);paths.append(str(path))
    w=ui.MainWindow()
    def until(predicate):
        timer=QElapsedTimer();timer.start()
        while not predicate() and timer.elapsed()<6000:app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents,20)
        assert predicate()
    try:
        w.shot_sequence_panel.cache=PreviewCache(root=tmp_path/'work')
        w.set_project_type('shot_sequence')
        w.segments=import_shots(paths[:3],tmp_path/'shots')
        w.shot_sequence_settings={**DEFAULTS,'width':96,'height':64,'overlap':.5}
        w.refresh_timeline();p=w.shot_sequence_panel
        until(lambda:p.cache_percent==100 and not p._worker_running)
        assert all(max(clip['peaks'])>0 for clip in p.shots.clips)
        assert all(not button.icon().isNull() for button in p.timeline_buttons)
        monkeypatch.setattr(w,'project_status_tags',lambda:[{'name':'Approved','color':'#4e9271'},{'name':'Review','color':'#a88b40'}])
        p.refresh();ids=[s.id for s in w.segments];revision=p._revision
        menu=p.shots.status_menu(1)
        actions={action.text():action for action in menu.actions()}
        assert 'Approved' in actions and 'Review' in actions and 'Done' not in actions
        actions['Approved'].trigger();menu.deleteLater()
        assert p.shots.clips[1]['status_color']=='#4e9271'
        assert p._revision==revision # status changes don't rebuild the preview
        archive=tmp_path/'sequence.LTXD'
        save_project_archive(archive,w.project_payload(False),w.segments)
        assert read_project(archive)['shotSequence']['shot_statuses'][ids[1]]=='Approved'
        p.overlap.setValue(.25)
        assert w.shot_sequence_settings['shot_statuses'][ids[1]]=='Approved'
        mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(paths[3])])
        view=p.shots;view.zoom(2);view.horizontalScrollBar().setValue(20)
        center=view.clip_rect(1).center().toPoint()
        position=view.canvas.mapTo(view.viewport(),center)
        event=QDropEvent(QPointF(position),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier)
        view.dropEvent(event)
        assert event.isAccepted()
        until(lambda:w.segments[1].name=='3.mp4')
        assert [s.id for s in w.segments]==ids
        assert len(w.segments)==3 and w.segments[1].duration==1.5
        assert p.shots.clips[1]['status']=='Approved'
        until(lambda:p.cache_percent==100 and not p._worker_running)
    finally:
        w.project_dirty=False;w._close_saves_queued=True;w.close()
