"""Per-shot joins remain consistent in playback, baking, persistence and NLA edits."""
import io
import json
from pathlib import Path
import av
import numpy as np
import pytest
from PySide6.QtCore import QMimeData,QUrl,Qt,QPointF,QPoint,QElapsedTimer,QEventLoop
from PySide6.QtGui import QDragMoveEvent,QDropEvent,QDragLeaveEvent
from PySide6.QtWidgets import QApplication,QListWidgetItem,QPushButton
from ltx_prompt_director.models import Segment
from ltx_prompt_director.shot_sequence import DEFAULTS,settings,transitions,shot_layout
from ltx_prompt_director.shot_sequence_widgets import import_shots,render_shots
from ltx_prompt_director.shot_preview_cache import PreviewCache,make_plan
from ltx_prompt_director.shot_timeline import ShotTimeline
from test_shot_sequence import movie


def decode(data):
    with av.open(io.BytesIO(data)) as container:images=np.stack([f.to_ndarray(format='rgb24') for f in container.decode(video=0)])
    with av.open(io.BytesIO(data)) as container:audio=np.concatenate([f.to_ndarray() for f in container.decode(audio=0)],axis=1)
    return images,audio


@pytest.mark.parametrize('first_mode,second_mode',[('linear_blend','filmic_crossfade'),('cut','perceptual_crossfade'),('perceptual_crossfade','cut')])
def test_per_shot_preview_and_bake_match_and_reuse_unaffected_regions(tmp_path,first_mode,second_mode):
    paths=[]
    for index,color in enumerate(([180,30,50],[30,180,50],[40,50,180])):
        path=tmp_path/f'{index}.mp4';movie(path,color,frequency=440+index*110);paths.append(str(path))
    shots=import_shots(paths,tmp_path/'sources')
    overrides={shots[0].id:dict(blend=first_mode,overlap=.25,side='source'),
               shots[1].id:dict(blend=second_mode,overlap=.5,side='destination')}
    opts={**DEFAULTS,'width':96,'height':64,'shot_transitions':overrides}
    assert shot_layout(shots,opts)==([6,12],90)
    cache=PreviewCache(root=tmp_path/'cache');plan=make_plan(shots,opts)
    actual,actual_audio=decode(cache.build(plan))
    output=tmp_path/'baked.webm';render_shots(shots,opts,str(output))
    baked,baked_audio=decode(output.read_bytes())
    assert len(actual)==len(baked)==90
    assert np.max(np.abs(actual.astype(float)-baked.astype(float)))<25
    assert actual_audio.shape==baked_audio.shape and np.max(np.abs(actual_audio-baked_audio))<.01
    assert np.sqrt(np.mean(baked_audio**2))>.02
    if first_mode=='linear_blend':
        revised={**opts,'shot_transitions':{**overrides,shots[0].id:{**overrides[shots[0].id],'blend':'perceptual_crossfade'}}}
        changed=make_plan(shots,revised)
        assert sum(end-start for start,end,ready in cache.coverage(changed) if not ready)==pytest.approx(.25)
        before=dict(cache.stats);cache.build(changed)
        assert cache.stats['source_builds']==before['source_builds']
        assert cache.stats['region_builds']-before['region_builds']==1


def test_transition_defaults_validation_reordering_and_short_clip_clamping():
    shots=[Segment(str(index),'','','video',duration=.25) for index in range(3)]
    opts=settings({'shot_transitions':{shots[0].id:{'blend':'cut','overlap':.1}}})
    assert transitions([shot.id for shot in shots],opts)[0]==dict(blend='cut',overlap=.1,side='source')
    assert shot_layout(shots,opts)==([2,6],10)
    assert transitions([shots[1].id,shots[0].id,shots[2].id],opts)[1]['blend']=='cut'
    with pytest.raises(ValueError):settings({'shot_transitions':{shots[0].id:{'overlap':float('nan')}}})


def test_cursor_drop_targets_scroll_offsets_and_delete_context():
    app=QApplication.instance() or QApplication([])
    timeline=ShotTimeline();timeline.resize(500,300);timeline.show();app.processEvents()
    shots=[Segment(str(index),'','','video',duration=15) for index in range(3)]
    for shot in shots:
        item=QListWidgetItem(shot.name);item.setData(Qt.ItemDataRole.UserRole,shot.id);timeline.addItem(item)
    timeline.set_shots(shots,1);timeline.zoom(5);app.processEvents()
    timeline.horizontalScrollBar().setValue(220)
    mime=QMimeData();mime.setUrls([QUrl.fromLocalFile('/tmp/import.mp4')])
    dropped=[];replaced=[];deleted=[]
    timeline.movies_dropped.connect(lambda paths,index:dropped.append((paths,index)))
    timeline.movies_replaced.connect(lambda paths,ident:replaced.append(ident))
    timeline.remove_requested.connect(lambda:deleted.append([item.data(Qt.ItemDataRole.UserRole) for item in timeline.selectedItems()]))
    def position(index,audio=False):
        rect=timeline.clip_rect(index,audio)
        canvas_point=QPoint(round(rect.left()+rect.width()*.1),round(rect.center().y()))
        return timeline.canvas.mapTo(timeline.viewport(),canvas_point)
    point=position(1)
    move=QDragMoveEvent(point,Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    timeline.dragMoveEvent(move)
    assert move.isAccepted() and timeline.property('dropActive') and timeline.drop_target==1
    drop=QDropEvent(QPointF(point),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    timeline.dropEvent(drop)
    assert dropped==[(['/tmp/import.mp4'],1)] and not timeline.property('dropActive') and timeline.drop_target is None
    point=position(1,True)
    move=QDragMoveEvent(point,Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier)
    timeline.dragMoveEvent(move)
    assert timeline.replace_target==1 and timeline.drop_target is None
    drop=QDropEvent(QPointF(point),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier)
    timeline.dropEvent(drop);assert replaced==[shots[1].id]
    timeline.setCurrentRow(0);menu=timeline.status_menu(2)
    next(action for action in menu.actions() if action.text()=='Delete').trigger()
    assert deleted==[[shots[2].id]]
    timeline.dragMoveEvent(move);timeline.dragLeaveEvent(QDragLeaveEvent())
    assert timeline.drop_target is None and timeline.replace_target is None and not timeline.property('dropActive')
    timeline.close()


def test_selected_shot_settings_persist_delete_and_drop_insert(tmp_path,monkeypatch):
    from ltx_prompt_director import ui
    from ltx_prompt_director.workspaces import WorkspaceStore
    from ltx_prompt_director.project_archive import save_project_archive,read_project
    app=QApplication.instance() or QApplication([])
    store=WorkspaceStore(tmp_path/'workspaces')
    monkeypatch.setattr(ui,'WorkspaceStore',lambda:store)
    monkeypatch.setattr(ui.MainWindow,'restore_startup_workspace',lambda self:None)
    monkeypatch.setattr(ui,'project_library_path',lambda:tmp_path)
    paths=[]
    for index in range(3):
        path=tmp_path/f'{index}.mp4';movie(path,[40+index*50,70,180]);paths.append(str(path))
    window=ui.MainWindow()
    def until(predicate):
        timer=QElapsedTimer();timer.start()
        while not predicate() and timer.elapsed()<6000:app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents,20)
        assert predicate()
    try:
        window.set_project_type('shot_sequence');window.segments=import_shots(paths[:2],tmp_path/'sources')
        window.shot_sequence_settings={**DEFAULTS,'width':96,'height':64,'overlap':.5}
        window.refresh_timeline();panel=window.shot_sequence_panel
        assert not hasattr(panel,'preview_button') and not hasattr(panel,'bake_button')
        assert [button.text() for button in panel.findChildren(QPushButton)].count('Bake WebM…')==1
        panel.shots.setCurrentRow(0);assert panel.transition_default.isEnabled()
        panel.transition_default.setChecked(False);panel.transition_blend.setCurrentText('linear_blend')
        panel.transition_overlap.setValue(.25);panel.transition_side.setCurrentIndex(1)
        ident=window.segments[0].id
        assert window.shot_sequence_settings['shot_transitions'][ident]==dict(blend='linear_blend',overlap=.25,side='destination')
        assert panel.shots.total==2.75 and panel.shots.currentRow()==0
        archive=tmp_path/'sequence.LTXD';save_project_archive(archive,window.project_payload(False),window.segments)
        assert read_project(archive)['shotSequence']['shot_transitions'][ident]['overlap']==.25
        window.save_library_project(automatic=True)
        until(lambda:not window._pending_disk_jobs)
        metadata=[json.loads(path.read_text()) for path in tmp_path.glob('*.meta.json')]
        assert any(meta.get('duration')==2.75 and meta.get('segmentCount')==2 for meta in metadata)
        window.restore_workspace_state(window.capture_workspace_state())
        assert window.shot_sequence_settings['shot_transitions'][ident]['blend']=='linear_blend'
        panel.add_paths([paths[2]],1)
        until(lambda:len(window.segments)==3)
        assert [shot.name for shot in window.segments]==['0.mp4','2.mp4','1.mp4']
        panel.shots.setCurrentRow(0);panel.move(1)
        assert window.segments[1].id==ident and panel.shots.clips[1]['transition']['overlap']==.25
        panel.shots.setCurrentRow(1);menu=panel.shots.status_menu(1)
        next(action for action in menu.actions() if action.text()=='Delete').trigger()
        assert len(window.segments)==2 and ident not in window.shot_sequence_settings['shot_transitions']
        panel.shots.setCurrentRow(1);assert not panel.transition_default.isEnabled()
        # The remaining player transport can request playback before cache finishes.
        panel.preview.play.click();assert panel._desired_playing
        until(lambda:panel.cache_percent==100 and not panel._worker_running and not panel._installing and panel.preview.player.playbackState()==ui.QMediaPlayer.PlaybackState.PlayingState)
        panel.preview.player.pause();panel.preview.play.click()
        until(lambda:panel.preview.player.playbackState()==ui.QMediaPlayer.PlaybackState.PlayingState)
        panel.preview.play.click();assert panel.preview.player.playbackState()==ui.QMediaPlayer.PlaybackState.PausedState
    finally:
        window.project_dirty=False;window._close_saves_queued=True;window.close()
