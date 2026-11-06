from PySide6.QtCore import QObject, QPoint, Qt, QSettings
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from ltx_prompt_director.audio_scrub import AudioScrubber
from ltx_prompt_director.models import Segment
from ltx_prompt_director.shot_timeline import ShotTimeline


def test_audio_scrub_preserves_pause_and_play_intent():
    app=QApplication.instance() or QApplication([])
    class Transport(QObject):
        def __init__(self):super().__init__();self.state=QMediaPlayer.PlaybackState.PausedState;self.position=0
        def playbackState(self):return self.state
        def setPosition(self,value):self.position=value
        def play(self):self.state=QMediaPlayer.PlaybackState.PlayingState
        def pause(self):self.state=QMediaPlayer.PlaybackState.PausedState
    player=Transport();scrub=AudioScrubber(player)
    scrub.begin();scrub.seek(1250)
    assert player.state==QMediaPlayer.PlaybackState.PlayingState and scrub.active
    player.position=1330;scrub.end();scrub.finish()
    assert player.state==QMediaPlayer.PlaybackState.PausedState and player.position==1250 and not scrub.active
    player.play();scrub.begin();scrub.seek(2300);scrub.end();scrub.finish()
    assert player.state==QMediaPlayer.PlaybackState.PlayingState and not scrub.active
    scrub.cancel()


def test_timeline_follows_playback_and_exports_clicked_shot():
    from PySide6.QtWidgets import QListWidgetItem
    app=QApplication.instance() or QApplication([])
    timeline=ShotTimeline();timeline.resize(440,300);timeline.show();app.processEvents()
    shots=[Segment(f'{i}.webm','source.webm','','video','start','',15) for i in range(3)]
    for shot in shots:
        item=QListWidgetItem(shot.name);item.setData(Qt.ItemDataRole.UserRole,shot.id);timeline.addItem(item)
    timeline.set_shots(shots,1);timeline.zoom(4);app.processEvents()
    timeline.set_playhead(30000,follow=True)
    assert timeline.horizontalScrollBar().value()>0
    x=timeline.LEFT+timeline.playhead*timeline.scale-timeline.horizontalScrollBar().value()
    assert 0<=x<=timeline.viewport().width()
    before=timeline.horizontalScrollBar().value();timeline.set_playhead(500)
    assert timeline.horizontalScrollBar().value()==before
    timeline.set_playhead(0,follow=True);assert timeline.horizontalScrollBar().value()==0
    steps=[];timeline.frame_seek_requested.connect(steps.append)
    for _ in range(100):QTest.keyClick(timeline,Qt.Key.Key_Right)
    assert steps[-1]==4167 and timeline.playhead==4.167
    for _ in range(101):QTest.keyClick(timeline,Qt.Key.Key_Left)
    assert steps[-1]==0 and timeline.playhead==0
    timeline.set_playhead(round(timeline.total*1000))
    QTest.keyClick(timeline,Qt.Key.Key_Right)
    assert steps[-1]==round((round(timeline.total*24)-1)*1000/24)
    assert timeline.horizontalScrollBar().value()>0
    exports=[];timeline.export_requested.connect(exports.append)
    menu=timeline.status_menu(1)
    assert not any(action.text().startswith('Edit transition') for action in menu.actions())
    next(action for action in menu.actions() if action.text()=='Export segment video').trigger()
    assert exports==[shots[1].id]
    timeline.close()


def test_modes_and_long_prompts_stay_within_screen_and_reset_confirms(tmp_path,monkeypatch):
    from ltx_prompt_director import ui, storage_widgets
    from ltx_prompt_director.workspaces import WorkspaceStore
    app=QApplication.instance() or QApplication([])
    store=WorkspaceStore(tmp_path/'workspaces')
    store.restore_stock()
    monkeypatch.setattr(ui,'WorkspaceStore',lambda:store)
    monkeypatch.setattr(ui,'project_library_path',lambda:tmp_path/'projects')
    monkeypatch.setattr(ui.MainWindow,'restore_startup_workspace',lambda self:None)
    window=ui.MainWindow();window.settings=QSettings(str(tmp_path/'test.ini'),QSettings.Format.IniFormat)
    window.show()
    try:
        for mode in ('ltx','minimax_frames','minimax_prompt','shot_sequence','ltx'):
            window.set_project_type(mode);window.global_prompt.setPlainText('A long prompt. '*2000)
            window.resize(2000,1600)
            for _ in range(12):app.processEvents()
            assert window.screen().availableGeometry().contains(window.frameGeometry())
        # Storage/reset operations are deferred until restart, with default No.
        jobs=[]
        monkeypatch.setattr(window,'start_disk_job',lambda *args,**kwargs:jobs.append((args,kwargs)))
        panel=storage_widgets.StorageSettingsPanel(window);panel.set_busy(False);jobs.clear()
        confirmations=[]
        def answer(*args):confirmations.append(args);return QMessageBox.StandardButton.No
        monkeypatch.setattr(storage_widgets.QMessageBox,'question',answer)
        panel.reset_settings();assert not jobs
        assert confirmations[0][-1]==QMessageBox.StandardButton.No
        assert 'Saved projects, collections and managed media will be kept' in confirmations[0][2]
        monkeypatch.setattr(storage_widgets.QMessageBox,'question',lambda *args:QMessageBox.StandardButton.Yes)
        panel.reset_settings();assert jobs[0][0][0] is storage_widgets.stage_reset
    finally:
        window.project_dirty=False;window._close_saves_queued=True;window.close()


def test_internal_layout_and_native_prompt_drag_without_outer_scrollbar(tmp_path,monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from ltx_prompt_director import ui
    from ltx_prompt_director.workspaces import WorkspaceStore
    app=QApplication.instance() or QApplication([])
    store=WorkspaceStore(tmp_path/'workspaces')
    monkeypatch.setattr(ui,'WorkspaceStore',lambda:store)
    monkeypatch.setattr(ui,'project_library_path',lambda:tmp_path)
    monkeypatch.setattr(ui.MainWindow,'restore_startup_workspace',lambda self:None)
    window=ui.MainWindow();window._close_saves_queued=True
    try:
        window.resize(1080,900);window.show()
        window.set_project_type('minimax_official_skill_reference');QTest.qWait(60)
        assert window.timeline_dock.widget() is window.editor_root
        assert window.centralWidget().height()==0
        assert window.editor_root.layout().indexOf(window.intent_prompt_splitter)<0
        assert window.intent_prompt_splitter.isHidden()
        dock=window.timeline_dock;state=window.saveState()
        dock.setFloating(True);dock.resize(620,700);QTest.qWait(60)
        assert dock.isFloating() and dock.isVisible()
        assert window.sequence_options_scroll.horizontalScrollBar().maximum()>0
        assert window.sequence_options_scroll.isVisible()
        dock.setFloating(False);assert window.restoreState(state)
        QTest.qWait(60)
        assert not dock.isFloating() and dock.isVisible()
        window.set_project_type('minimax_prompt_refinement');QTest.qWait(20)
        assert dock.isHidden()
        window.set_project_type('ltx');QTest.qWait(20)
        assert dock.isVisible() and window.unified_prompt_dock.isHidden()
    finally:
        window.project_dirty=False;window.close()
