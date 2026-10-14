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
    exports=[];timeline.export_requested.connect(exports.append)
    menu=timeline.status_menu(1)
    next(action for action in menu.actions() if action.text()=='Export segment video').trigger()
    assert exports==[shots[1].id]
    timeline.close()


def test_modes_and_long_prompts_stay_within_screen_and_reset_confirms(tmp_path,monkeypatch):
    from ltx_prompt_director import ui, storage_widgets
    from ltx_prompt_director.workspaces import WorkspaceStore
    app=QApplication.instance() or QApplication([])
    store=WorkspaceStore(tmp_path/'workspaces')
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
