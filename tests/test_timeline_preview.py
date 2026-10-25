"""Timeline preview selection, audio intent and stale result isolation."""
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QPoint,Qt,QUrl
from PySide6.QtGui import QColor,QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ltx_prompt_director.models import Segment
from ltx_prompt_director.timeline_preview import resolve_preview,segment_at
from test_video_extensions import clip
from test_video_extension_ui import window

APP=QApplication.instance() or QApplication([])


def test_rendered_sequence_and_cached_blocks_take_priority_with_correct_audio_position(tmp_path):
    first=clip(tmp_path,'first',4,'red')
    final=clip(tmp_path,'final',8,'blue')
    segments=[Segment('First','','','text','text','Opening',4),Segment('Next','','','text','text','Continue',4,render_group=1)]
    result=resolve_preview(segments,5,final)
    assert result['kind']=='rendered' and result['position']==5000 and result['path']==final
    result=resolve_preview(segments,5,'',{'1':{'result':{'path':first}}})
    assert result['kind']=='rendered' and result['position']==1000
    assert resolve_preview(segments,5)['text']=='Continue'
    assert segment_at(segments,4)[0] is segments[1]


def test_unrendered_text_image_and_trimmed_video_are_previewed_without_editor_changes(tmp_path):
    image=QImage(80,60,QImage.Format.Format_RGB32);image.fill(QColor('navy'));path=tmp_path/'image.png';image.save(str(path))
    video=clip(tmp_path,'condition',4,'green',False)
    segments=[Segment('Words','','','text','text','Exact source wording',2),
              Segment('Image',str(path),str(path),'image','start','',2),
              Segment('Video',video,'','video','start','',2,trim_start=24)]
    assert resolve_preview(segments,1)['text']=='Exact source wording'
    assert not resolve_preview(segments,3)['image'].isNull()
    result=resolve_preview(segments,5)
    assert result['kind']=='video' and result['position']==2000
    assert [s.duration for s in segments]==[2,2,2] and segments[0].prompt=='Exact source wording'


def test_ruler_gesture_selects_conditioning_preview_and_keeps_editor_selection(tmp_path):
    with window(tmp_path) as w:
        w.set_project_type('minimax_frames')
        w.segments=[Segment('First','','','text','text','First wording',5),Segment('Second','','','text','text','Second wording',5)]
        w.refresh_timeline(0);w.resize(1500,900);w.show();APP.processEvents();w.autofit_timeline();APP.processEvents()
        requests=[]
        def execute(op,args,ready,failed,**kw):
            requests.append((op,args,ready,failed))
        w.start_disk_job=execute
        x=round(7*w.pixels_per_second)
        QTest.mousePress(w.ruler,Qt.MouseButton.LeftButton,pos=QPoint(x,14))
        QTest.mouseRelease(w.ruler,Qt.MouseButton.LeftButton,pos=QPoint(x,14))
        w.timeline_preview.timer.stop();w.timeline_preview._resolve()
        op,args,ready,failed=requests[-1];ready(op(*args))
        assert w.project_preview_panel.condition_text.toPlainText()=='Second wording'
        assert w.timeline.currentRow()==0 and w.segments[0].prompt=='First wording'
        assert 6.8<w.ruler.playhead<7.2


def test_obsolete_preview_is_discarded_and_new_project_request_is_not_lost(tmp_path):
    with window(tmp_path) as w:
        w.set_project_type('minimax_frames');w.segments=[Segment('Old','','','text','text','Old',5)];w.refresh_timeline()
        jobs=[];w.start_disk_job=lambda op,args,ready,failed,**kw:jobs.append((op,args,ready))
        controller=w.timeline_preview
        controller.seek(1);controller.timer.stop();controller._resolve()
        first=jobs[-1]
        controller.cancel();w.workspace_revision+=1
        w.segments=[Segment('New','','','text','text','New wording',5)]
        controller.seek(2)
        first[2](first[0](*first[1]))
        assert w.project_preview_panel.condition_text.toPlainText()!='Old'
        controller.timer.stop();controller._resolve()
        new=jobs[-1];new[2](new[0](*new[1]))
        assert w.project_preview_panel.condition_text.toPlainText()=='New wording'


def test_rendered_seek_auditions_audio_and_condition_video_seeks_silently(tmp_path):
    from PySide6.QtMultimedia import QMediaPlayer
    with window(tmp_path) as w:
        controller=w.timeline_preview;panel=w.project_preview_panel
        video=clip(tmp_path,'render',4,'blue')
        panel.player.setSource(QUrl.fromLocalFile(video))
        panel.pending_preview_path=video
        with patch.object(controller.scrubber,'seek') as audible:
            controller.pending_seek=(video,1250,True);controller._apply_seek()
            audible.assert_called_once_with(1250)
        with patch.object(controller.scrubber,'seek') as audible:
            controller.pending_seek=(video,2250,False);controller._apply_seek()
            audible.assert_not_called()
        assert panel.player.playbackState()!=QMediaPlayer.PlaybackState.PlayingState


def test_real_rendered_clip_loads_at_scrub_position_with_audio_stream(tmp_path):
    from PySide6.QtMultimedia import QMediaPlayer
    with window(tmp_path) as w:
        video=clip(tmp_path,'audio-preview',4,'purple')
        w.set_project_type('minimax_frames');w.segments=[Segment('Text','','','text','text','Source',4)];w.refresh_timeline()
        w.store_render_group()
        from ltx_prompt_director.render_groups import input_stamp
        w._video_preview_cache[w.video_preview_key()]={'kind':'final','path':video,'groupStamps':{'0':input_stamp(w.segments,w.capture_render_group())},'blendSettings':[]}
        w.start_disk_job=lambda op,args,ready,failed,**kw:ready(op(*args))
        w.resize(1300,900);w.show();APP.processEvents()
        w.project_preview_panel.audio.setMuted(True)
        controller=w.timeline_preview;controller.begin();controller.seek(1.25)
        controller.timer.stop();controller._resolve()
        panel=w.project_preview_panel
        for _ in range(30):
            QTest.qWait(25)
            if panel.player.hasAudio() and panel.player.position()>=1200:break
        assert panel.player.source().toLocalFile()==video and panel.player.hasAudio()
        controller.end();QTest.qWait(180)
        assert abs(panel.player.position()-1250)<100
        assert panel.player.playbackState()==QMediaPlayer.PlaybackState.PausedState


def test_editing_a_prompt_discards_an_older_pending_preview(tmp_path):
    with window(tmp_path) as w:
        w.set_project_type('minimax_frames');w.segments=[Segment('Words','','','text','text','Before edit',5)];w.refresh_timeline()
        jobs=[];w.start_disk_job=lambda op,args,ready,failed,**kw:jobs.append((op,args,ready))
        controller=w.timeline_preview;controller.seek(1);controller.timer.stop();controller._resolve()
        op,args,ready=jobs[-1]
        w.segments[0].prompt='After edit';w.mark_dirty()
        ready(op(*args))
        assert w.project_preview_panel.condition_text.toPlainText()!='Before edit'


def test_long_conditioning_text_stays_inside_resizable_preview(tmp_path):
    with window(tmp_path) as w:
        w.project_preview_dock.setFloating(True);w.project_preview_dock.resize(400,650)
        w.project_preview_dock.show();w.show();QTest.qWait(30)
        panel=w.project_preview_panel
        source='A long conditioning prompt. '*6000
        panel.show_conditioning_text('Long text',source)
        assert panel.condition_text.property('sourceText')==source
        w.project_preview_dock.resize(340,500);QTest.qWait(60)
        assert w.project_preview_dock.height()<=500
        assert panel.condition_text.verticalScrollBar().maximum()>0
        assert panel.condition_text.height()<400


def test_cancelled_preview_cannot_reopen_a_panel_in_the_same_project(tmp_path):
    with window(tmp_path) as w:
        w.set_project_type('minimax_frames');w.segments=[Segment('Words','','','text','text','Old preview',5)];w.refresh_timeline()
        jobs=[];w.start_disk_job=lambda op,args,ready,failed,**kw:jobs.append((op,args,ready))
        controller=w.timeline_preview;controller.seek(1);controller.timer.stop();controller._resolve()
        op,args,ready=jobs[-1];controller.cancel();ready(op(*args))
        assert w.project_preview_panel.condition_text.toPlainText()!='Old preview'


def test_continuous_scrubbing_is_throttled_instead_of_waiting_for_drag_to_stop(tmp_path):
    with window(tmp_path) as w:
        w.segments=[Segment('Text','','','text','text','Words',5)]
        controller=w.timeline_preview
        class Timer:
            active=False;starts=0
            def start(self):self.active=True;self.starts+=1
            def stop(self):self.active=False
            def isActive(self):return self.active
        timer=Timer();controller.timer=timer
        for position in (1,1.1,1.2,1.3,1.4):controller.seek(position)
        assert timer.starts==1 and controller.latest==1.4
        assert controller.queued
        with patch.object(controller.scrubber,'seek') as audible:
            controller.seek_player(0)
            audible.assert_not_called()
