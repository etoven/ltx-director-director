"""Run each native GUI regression in a fresh process, as in the release workflow."""
from unittest.mock import patch
from pathlib import Path
from PySide6.QtCore import QMimeData, QPointF, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication
from ltx_prompt_director.ui import MainWindow
from ltx_prompt_director.models import Segment
from ltx_prompt_director.effects_library import MIME, load_effects

APP = QApplication.instance() or QApplication([])


def test_effects_dock_search_artwork_and_menu_migration():
    with patch.object(MainWindow, 'restore_startup_workspace'):
        w = MainWindow()
    w._close_saves_queued = True
    try:
        panel = w.effects_panel
        assert panel.tiles.count() >= 117
        effect = next(e for e in panel.entries if e['id'] == 'FX001')
        panel.search.setText('FX001')
        assert panel.tiles.count() == 1
        assert not panel.tiles.item(0).icon().isNull()
        panel.favorite.click();assert 'FX001' in panel.favorites
        panel.favorites_only.setChecked(True);panel.search.clear()
        assert panel.tiles.count()==1 and panel.current()['id']=='FX001'
        panel.list_view.click();assert panel.tiles.viewMode()==panel.tiles.ViewMode.ListMode
        panel.list_view.click();assert panel.tiles.viewMode()==panel.tiles.ViewMode.IconMode
        panel.favorite.click();assert not panel.tiles.count()
        panel.favorites_only.setChecked(False);panel.search.setText('FX001')
        menu = w.segment_quick_fix_button.menu()
        w.populate_quick_fix_menu(menu, 'segment')
        labels = [a.text() for a in menu.actions()]
        assert 'Onset tremor' not in labels
        assert labels[-1] == 'Effects Library…'
        assert menu.actions()[-2].isSeparator()
        assert w.effects_dock.objectName() == 'effectsLibraryDock'
        w.segments = [Segment('One', '', '', kind='text', prompt='A hand turns.', duration=2)]
        w.refresh_timeline(0)
        with patch.object(w, 'apply_quick_fix') as refine:
            w.apply_library_effect(effect, 'segment')
            assert refine.call_args.args[1] == 'segment'
            assert 'No reference media is attached' in refine.call_args.args[0]['prompt']
    finally:
        w.close()


def test_effect_drop_acceptance_matches_scope_and_busy_state():
    with patch.object(MainWindow, 'restore_startup_workspace'):
        w = MainWindow()
    w._close_saves_queued = True
    try:
        w.segments = [Segment('One', '', '', kind='text', prompt='A hand turns.', duration=2)]
        w.refresh_timeline(0)
        data = QMimeData(); data.setData(MIME, b'FX001')
        def drag():
            return QDragEnterEvent(w.segment_prompt.rect().center(), Qt.DropAction.CopyAction,
                                   data, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        event = drag()
        assert w.eventFilter(w.segment_prompt.viewport(), event)
        assert event.isAccepted()
        with patch.object(w, 'apply_library_effect') as refine:
            event = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, data,
                               Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            w.eventFilter(w.segment_prompt.viewport(), event)
            assert event.isAccepted()
            assert refine.call_args.args[0]['id'] == 'FX001'
            assert refine.call_args.args[1] == 'segment'
        w.ai_busy = True
        event = drag(); w.eventFilter(w.segment_prompt.viewport(), event)
        assert not event.isAccepted()
        w.ai_busy = False
        data.setData(MIME, b'SRC001')
        event = drag(); w.eventFilter(w.segment_prompt.viewport(), event)
        assert event.isAccepted()  # Its sound instruction can refine; the recording is not attached.
    finally:
        w.close()


def test_effect_preview_tiles_and_stale_startup_audit():
    from ltx_prompt_director.effects_media import bundled_previews
    from ltx_prompt_director.effects_library import media_path
    from PySide6.QtMultimedia import QMediaPlayer
    with patch.object(MainWindow,'restore_startup_workspace'):
        w=MainWindow()
    w._close_saves_queued=True
    try:
        panel=w.effects_panel; jobs=[]
        assert not hasattr(panel,'media') and not hasattr(panel,'play')
        with patch.object(w,'start_disk_job',side_effect=lambda op,args,ready,failed,**kw:jobs.append((op,args,ready,failed))), \
             patch.object(panel,'isVisible',return_value=True), patch.object(panel.player,'play') as play, \
             patch.object(panel.player,'setSource') as source:
            panel.schedule_media_audit(); panel.audit_timer.stop(); panel.audit_media()
            old=jobs[-1]
            panel.reload(); panel.audit_timer.stop(); panel.audit_media()
            old[2]({'entries':{'obsolete':{}},'issues':[],'repaired':0})
            assert 'obsolete' not in panel.media_cache
            record=bundled_previews()[str(Path(media_path('reference_clips/CLIP001.mp4')).resolve())]
            compressed=media_path(record['preview'])
            panel.search.setText('CLIP001'); play.reset_mock()
            jobs[-1][2]({'entries':{'CLIP001':{'videos':{'reference_clips/CLIP001.mp4':compressed}}},'issues':[],'repaired':0})
            assert play.called and source.call_args.args[0].toLocalFile()==compressed
            assert panel.audio.isMuted() and panel.player.loops()==QMediaPlayer.Loops.Infinite
            play.reset_mock(); panel.search.setText('SRC001'); assert not play.called
            panel.select(panel.tiles.currentItem()); assert not play.called
            panel.tile_clicked(panel.tiles.currentItem()); assert play.called and not panel.audio.isMuted()
            assert source.call_args.args[0].toLocalFile().endswith('CLIP001.wav')
            assert panel.player.loops()==QMediaPlayer.Loops.Once
            play.reset_mock()
            with patch.object(panel.player,'playbackState',return_value=QMediaPlayer.PlaybackState.PlayingState):
                panel.tile_clicked(panel.tiles.currentItem()); assert not play.called
            import tempfile
            with tempfile.TemporaryDirectory() as folder, patch('ltx_prompt_director.effects_widgets.choose_document_save',return_value=folder+'/original.mp4'):
                panel.save_sample('reference_clips/CLIP001.mp4')
                assert Path(folder+'/original.mp4').read_bytes()==Path(media_path('reference_clips/CLIP001.mp4')).read_bytes()
            panel.schedule_media_audit(force=True); panel.audit_timer.stop(); panel.audit_media(); late=jobs[-1]
            panel.dispose(); late[2]({'entries':{'late':{}},'issues':[],'repaired':0})
            assert 'late' not in panel.media_cache and not panel.audit_timer.isActive()
    finally:w.close()
