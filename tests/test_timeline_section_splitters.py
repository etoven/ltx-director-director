from unittest.mock import patch
from PySide6.QtCore import QSettings, QPoint, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from ltx_prompt_director.ui import MainWindow
from ltx_prompt_director.models import Segment
from ltx_prompt_director.grouped_sequence import GroupedSequence

APP=QApplication.instance() or QApplication([])


def test_adjacent_sections_trade_height_and_dock_shrinks(tmp_path):
    settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat)
    with patch('ltx_prompt_director.ui.QSettings',return_value=settings),patch.object(MainWindow,'restore_startup_workspace'),patch.object(GroupedSequence,'changed'):
        w=MainWindow();w._close_saves_queued=True
        try:
            w.set_project_type('minimax_frames')
            w.segments=[Segment('Text','','','text','text','Authored prompt',5) for _ in range(3)]
            w.refresh_timeline();w.resize(1600,1100);w.show()
            w.resizeDocks([w.timeline_dock],[800],Qt.Orientation.Vertical)
            for _ in range(8):APP.processEvents()
            sections=w.timeline_sections;s=sections.splitter
            assert s.widget(0) is w.timeline_shell and s.widget(1) is w.studio.dialogue
            assert w.conditioning_guide.parentWidget() is s
            assert w.timeline_height_handle.parentWidget() is sections.parking
            assert w.conditioning_height_handle.parentWidget() is sections.parking
            names=sections.visible_names();assert names==['visual','dialogue','conditioning']
            # Give the initial visual section ample space above its minimum.
            sizes=s.sizes();sizes[0]+=60;sizes[1]+=50;sizes[-1]+=50;s.setSizes(sizes)
            for _ in range(4):APP.processEvents()
            before=s.sizes();frame_height=sections.frame.height()
            handle=s.handle(1)
            s.moveSplitter(handle.y()-25,1)
            for _ in range(4):APP.processEvents()
            after=s.sizes()
            assert after[0]<before[0] and after[1]>before[1],(before,after,s.height(),s.sizeHint().height(),s.minimumSizeHint().height(),sections.frame.height(),w.timeline_dock.height())
            assert abs(sum(after)-sum(before))<=2
            assert abs(sections.frame.height()-frame_height)<=2
            # The second visible grip lies between Dialogue and I2V, not below I2V.
            index=s.indexOf(w.conditioning_guide);lower=s.handle(index)
            dialogue_bottom=w.studio.dialogue.mapTo(s,QPoint(0,w.studio.dialogue.height())).y()
            assert abs(lower.y()-dialogue_bottom)<=2
            assert lower.y()<w.conditioning_guide.y()
            assert not lower.isHidden()
            old=s.sizes();s.moveSplitter(lower.y()+12,index)
            for _ in range(3):APP.processEvents()
            new=s.sizes();assert new[1]>old[1] and new[index]<old[index]
            sections.moved();saved=dict(settings.value(sections.SETTINGS,{}));assert saved['dialogue']==w.studio.dialogue.height()
            expanded_dock_height=w.timeline_dock.height()
            # A lower dock can push Timeline to its content minimum.
            w.resizeDocks([w.timeline_dock],[w.timeline_dock.minimumSizeHint().height()],Qt.Orientation.Vertical)
            for _ in range(5):APP.processEvents()
            assert w.timeline_dock.height()<expanded_dock_height
            assert w.timeline_shell.height()>=w.timeline_shell.minimumSizeHint().height()
            assert w.studio.dialogue.y()>=w.timeline_shell.y()+w.timeline_shell.height()
            assert w.conditioning_guide.y()>=w.studio.dialogue.y()+w.studio.dialogue.height()
            assert w.timeline_shell.y()==0
            assert w.add_tile_wrap.mapTo(sections.frame,QPoint(0,0)).y()<=4
            assert w.timeline_bottom_scroll.parentWidget() is w.studio.dialogue.parentWidget().parentWidget()
            w.grab().save(str(tmp_path/'timeline-sections.png'))
            w.set_project_type('minimax_references')
            for _ in range(4):APP.processEvents()
            assert 'audio' in sections.visible_names()
            w.set_project_type('minimax_frames')
            for _ in range(4):APP.processEvents()
            assert sections.visible_names()==['visual','dialogue','conditioning']
            assert sections.heights['dialogue']==saved['dialogue']
            # Reopen with the same settings, retaining section identities and sizes.
            w.grouped_sequence.timer.stop();w.shot_sequence_panel.deactivate();w.render_queue.shutdown();w.close()
            w=MainWindow();w._close_saves_queued=True
            assert w.timeline_sections.heights['dialogue']==saved['dialogue']
        finally:
            w.grouped_sequence.timer.stop();w.shot_sequence_panel.deactivate();w.render_queue.shutdown();w.close()
