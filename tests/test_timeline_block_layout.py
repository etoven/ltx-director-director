from unittest.mock import patch
from PySide6.QtCore import QSettings, QPoint, Qt
from PySide6.QtWidgets import QApplication
from ltx_prompt_director.ui import MainWindow
from ltx_prompt_director.grouped_sequence import GroupedSequence
from ltx_prompt_director.models import Segment

APP=QApplication.instance() or QApplication([])


def test_timeline_dock_enforces_separate_block_minima(tmp_path):
    settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat)
    with patch('ltx_prompt_director.ui.QSettings',return_value=settings),patch.object(MainWindow,'restore_startup_workspace'),patch.object(GroupedSequence,'changed'):
        w=MainWindow();w._close_saves_queued=True
        try:
            w.set_project_type('minimax_frames')
            w.segments=[Segment('Text','','','text','text','Authored prompt',5) for _ in range(3)]
            w.refresh_timeline();w.resize(1400,1000);w.show();APP.processEvents()
            w.set_timeline_height(122);w.set_conditioning_height(96)
            w.resize(800,450)
            w.resizeDocks([w.timeline_dock],[80],Qt.Orientation.Vertical)
            for _ in range(4):APP.processEvents()
            visual=w.timeline_shell;dialogue=w.studio.dialogue
            assert visual.height()>=visual.minimumSizeHint().height()
            assert dialogue.height()>=dialogue.minimumSizeHint().height()
            visual_bottom=visual.mapTo(w.editor_root,QPoint(0,visual.height())).y()
            dialogue_top=dialogue.mapTo(w.editor_root,QPoint(0,0)).y()
            assert dialogue_top>=visual_bottom
            assert w.timeline.viewport().height()>=100
            assert w.editor_root.width()>=480
            assert w.editor_root.height()>=w.editor_root.layout().minimumSize().height()
            w.grab().save(str(tmp_path/'timeline-block-layout.png'))
        finally:
            w.grouped_sequence.timer.stop();w.shot_sequence_panel.deactivate();w.render_queue.shutdown();w.close()
