"""Run each native GUI regression in a fresh process, as in the release workflow."""
from unittest.mock import patch
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
        assert not event.isAccepted()  # Previewing source audio never attaches it.
    finally:
        w.close()
