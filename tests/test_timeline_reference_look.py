"""Reference-led timeline appearance with current widgets and behavior intact."""
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QSettings,Qt,QPoint
from PySide6.QtGui import QImage,QColor
from PySide6.QtWidgets import QApplication,QLabel
from PySide6.QtTest import QTest
from ltx_prompt_director.ui import MainWindow
from ltx_prompt_director.models import Segment
from ltx_prompt_director.grouped_sequence import GroupedSequence
APP=QApplication.instance() or QApplication([])


def test_reference_look_preserves_current_timeline_controls(tmp_path):
    settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat)
    with patch('ltx_prompt_director.ui.QSettings',return_value=settings),patch.object(MainWindow,'restore_startup_workspace'),patch.object(GroupedSequence,'changed'):
        w=MainWindow();w._close_saves_queued=True
        try:
            w.set_project_type('minimax_references')
            text='Continue the same shot without a cut or change of viewpoint. Keep the established composition and lighting. '
            w.segments=[Segment('First','','','text','text',text*4,15,render_group=0),Segment('Second','','','text','text','Enter text prompt…',15,render_group=1)]
            w.video_extensions=1;w.requested_length.setValue(15)
            w.render_group_options={str(g):dict(requestedLength=15,context=w.capture_render_group()) for g in (0,1)}
            w.refresh_timeline();w.resize(1500,1250);w.show();w.resizeDocks([w.timeline_dock],[1000],Qt.Orientation.Vertical)
            w.timeline_scale.setValue(24)
            for _ in range(8):APP.processEvents()
            sections=w.timeline_sections;s=sections.splitter
            s.setSizes([285,100,240,155]);QTest.qWait(50)
            card=w.timeline.itemWidget(w.timeline.item(0));body=card.content.layout()
            assert body.itemAt(1).widget() is card.preview
            badge=card.content.findChild(QLabel,'mediaBadge')
            assert badge and badge.y()<card.preview.y()
            assert card.preview.x()<12 and card.preview.width()>card.width()*.75
            assert not getattr(card,'studio_ready',False)
            assert w.main_track_label.isVisible()
            assert abs(w.ruler.mapTo(w.editor_root,QPoint(0,0)).x()-w.timeline.viewport().mapTo(w.editor_root,QPoint(0,0)).x())<=2
            assert abs(w.extension_brackets.mapTo(w.editor_root,QPoint(0,0)).x()-w.timeline.viewport().mapTo(w.editor_root,QPoint(0,0)).x())<=2
            assert w.studio.transport.isVisible() and w.studio_snap.isVisible()
            assert w.timeline_cache_coverage.isVisible() and w.timeline_bottom_scroll.isVisible()
            assert w.audio_panel.play.isVisible() and w.audio_panel.time.isVisible()
            assert w.audio_panel.enabled.text()=='Conditioning' and w.audio_panel.edit_controls.isVisible()
            assert w.audio_panel.canvas.TOP==26
            assert len(w.audio_panel.findChildren(QLabel,'studioAudioTitle'))==1
            assert len(w.audio_panel.findChildren(__import__('PySide6.QtWidgets',fromlist=['QPushButton']).QPushButton,'studioFilter'))==4
            assert w.timeline.findChildren(__import__('PySide6.QtWidgets',fromlist=['QWidget']).QWidget,'extensionBlendDiagram')
            w.timeline_cache_coverage.set_coverage([(0,12,True),(12,29.08,False)])
            old=s.sizes();handle=s.handle(1);s.moveSplitter(handle.y()-20,1);APP.processEvents()
            assert s.sizes()[0]<old[0] and s.sizes()[1]>old[1]
            assert [item.prompt for item in w.segments]==[text*4,'Enter text prompt…']
            w.timeline_dock.widget().grab().save(str(tmp_path/'reference-timeline.png'))
            w.set_project_type('minimax_frames');APP.processEvents()
            assert w.audio_panel.isHidden() and w.studio.dialogue.isVisible()
            w.timeline_dock.widget().grab().save(str(tmp_path/'reference-keyframes.png'))
        finally:
            w.grouped_sequence.timer.stop();w.shot_sequence_panel.deactivate();w.render_queue.shutdown();w.close()
