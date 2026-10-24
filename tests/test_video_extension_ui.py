"""Extension grouping, duplicate generations, and project preview isolation."""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
import copy
import pytest
from PySide6.QtCore import Qt,QSettings
from PySide6.QtGui import QImage,QColor
from PySide6.QtWidgets import QApplication
from ltx_prompt_director.models import Segment
from ltx_prompt_director.ui import MainWindow
from ltx_prompt_director.render_queue import RenderQueue

APP=QApplication.instance() or QApplication([])


@contextmanager
def window(tmp_path):
    settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat)
    with patch('ltx_prompt_director.ui.QSettings',return_value=settings),patch.object(MainWindow,'restore_startup_workspace'):
        w=MainWindow()
    w._close_saves_queued=True
    try:yield w
    finally:w.render_queue.shutdown();w.close()


def prepare(w):
    w.set_project_type('minimax_references')
    w.segments=[Segment('Opening','','','text','text','Original segment prompt',10)]
    w.requested_length.setValue(10)
    w.refresh_timeline()
    w.unified_prompt.setPlainText('Original production wording.')


def test_brackets_use_existing_length_control_and_save_source_prompts(tmp_path):
    with window(tmp_path) as w:
        prepare(w)
        original=w.minimax_prompt_text
        w.add_extension_tile.click()
        assert len(w.segments)==2 and [s.duration for s in w.segments]==[10,10]
        assert w.segments[1].render_group==1
        w.requested_length.setValue(5)
        w.add_extension_tile.click();w.requested_length.setValue(5)
        assert [s.duration for s in w.segments]==[10,5,5]
        assert [w.render_group_duration(i) for i in range(3)]==[10,5,5]
        assert w.minimax_prompt_text==original
        assert w.segments[0].prompt=='Original segment prompt'
        w.timeline.setCurrentRow(0)
        assert w.requested_length.value()==10
        w.timeline.setCurrentRow(1)
        assert w.requested_length.value()==5
        payload=w.project_payload(False)
        w.load_project_payload(copy.deepcopy(payload))
        assert [s.render_group for s in w.segments]==[0,1,2]
        assert w.render_group_duration(1)==5
        assert w.minimax_prompt_text==original
        w.resize(1500,1000);w.show();APP.processEvents();w.timeline.doItemsLayout()
        assert len(w.extension_brackets.spans())==3
        box=w._extension_boxes[1]
        assert box.mode.count()==4 and all(not box.mode.itemIcon(i).isNull() for i in range(4))
        box.length.setValue(2)
        box.mode.setCurrentIndex(box.mode.findData('cut'))
        assert w.extension_transition(1)=={'blend':'cut','overlap':2,'side':'source'}
        assert not box.length.isEnabled()
        assert w.total_duration()==20
        assert box.diagram.edge_paths()==[]
        assert not w.extension_brackets.grab().isNull()


def test_repeated_generate_and_switch_restores_inactive_latent_then_final(tmp_path):
    with window(tmp_path) as w:
        prepare(w);w.current_project_id='one';w.current_project_name='Project one'
        runs=[]
        w.render_queue.executor=lambda op,args,ready,failed,**kw:runs.append((op,args,ready,failed,kw))
        with patch('ltx_prompt_director.ui.validate_profile'):
            w.local_video_generate.click();w.local_video_generate.click()
        assert len(w.render_queue.jobs)==2
        assert w.local_video_generate.isEnabled()
        assert w.local_video_generate.text().endswith('(3)')
        assert w.project_preview_panel.play.isHidden()
        assert w.project_preview_panel.choose.isHidden()
        w.current_project_id='two';w.update_project_preview()
        assert not w.project_preview_panel.play.isHidden()
        assert w.local_video_generate.text()=='✦ Generate locally'
        image=QImage(64,64,QImage.Format.Format_RGB32);image.fill(QColor('navy'))
        path=tmp_path/'latent.png';image.save(str(path))
        runs[0][4]['progress']({'kind':'preview','media':'image','path':str(path),'step':8})
        assert 'step 8' not in w.project_preview_panel.title.text()
        w.current_project_id='one';w.update_project_preview()
        assert 'step 8' in w.project_preview_panel.title.text()
        assert w.project_preview_panel.play.isHidden()
        final=tmp_path/'final.mp4';final.write_bytes(b'preview placeholder')
        w.current_project_id='two'
        with patch.object(w,'attach_local_generated_video'):
            runs[0][2]({'path':str(final)})
        w.current_project_id='one';w.update_project_preview()
        assert w.project_preview_panel.pending_preview_path==str(final)
        assert w.local_video_generate.text().endswith('(3)')


def test_failed_block_prevents_continuations_and_blend(tmp_path):
    queue=RenderQueue(lambda *args,**kwargs:None);queue.set_paused(True)
    first=queue.enqueue(lambda:None,(),lambda _:None,lambda _:None,title='First')
    next_job=queue.enqueue(lambda:None,(),lambda _:None,lambda _:None,title='Next',dependencies=(first.id,))
    final=queue.enqueue(lambda:None,(),lambda _:None,lambda _:None,title='Blend',dependencies=(first.id,next_job.id))
    first.state='Failed';queue.set_paused(False)
    for _ in range(8):APP.processEvents()
    assert next_job.state=='Cancelled' and final.state=='Cancelled'
    queue.shutdown()


def test_grouped_generation_uses_marked_durations_and_previous_tail(tmp_path):
    with window(tmp_path) as w:
        prepare(w);w.current_project_id='one'
        w.add_video_extension();w.requested_length.setValue(5)
        w.add_video_extension();w.requested_length.setValue(5)
        runs=[];requests=[]
        w.render_queue.executor=lambda op,args,ready,failed,**kw:runs.append((op,args,ready,failed,kw))
        def request(value,**options):
            requests.append(value)
            return {'path':str(tmp_path/f'block{len(requests)}.mp4')}
        def reference(path,marked):
            return dict(kind='video',path=path,start=marked-1,duration=1,timeline_time=0)
        with patch('ltx_prompt_director.ui.validate_profile'),patch('ltx_prompt_director.ui.run_request',side_effect=request),\
             patch('ltx_prompt_director.video_extensions.continuation_reference',side_effect=reference),\
             patch('ltx_prompt_director.video_extensions.blend_blocks',return_value={'path':str(tmp_path/'final.mp4')}) as blend,\
             patch.object(w,'attach_local_generated_video'),patch.object(w.project_preview_panel,'set_project'):
            w.generate_local_video()
            for index in range(4):
                for _ in range(4):APP.processEvents()
                op,args,ready,failed,options=runs[index]
                ready(op(*args))
            assert [r['duration'] for r in requests]==[10,5,5]
            assert requests[1]['references'][0]['start']==9
            assert requests[2]['references'][0]['start']==4
            assert requests[1]['references'][0]['timeline_time']==0
            assert [r['prompt'] for r in requests]==['Original production wording.']*3
            assert blend.call_args.args[3]==[10,5,5]
        assert len({j.group_id for j in w.render_queue.jobs})==1
        assert [j.state for j in w.render_queue.jobs]==['Complete']*4


def test_ctrl_selection_and_context_group_follow_visual_order(tmp_path):
    from PySide6.QtWidgets import QListWidgetItem,QMenu
    from PySide6.QtTest import QTest
    with window(tmp_path) as w:
        w.current_collection='Collection';w.project_list.clear()
        for ident in ['z','a','m']:
            item=QListWidgetItem(ident)
            item.setData(Qt.ItemDataRole.UserRole,dict(id=ident,name=ident,kind='project',collection='Collection'))
            w.project_list.addItem(item)
        w.project_list.item(0).setSelected(True);w.project_list.item(2).setSelected(True)
        w.project_list.setCurrentItem(w.project_list.item(0),w.project_list.selectionModel().SelectionFlag.NoUpdate)
        w.activate_clicked_project(w.project_list.item(2))
        assert not w._project_open_timer.isActive()
        assert [r['id'] for r in w.selected_projects_in_order()]==['z','m']
        w.project_dock.show();w.resize(1500,900);w.show();APP.processEvents()
        found=[]
        def inspect(menu,*args):
            action=next(a for a in menu.actions() if a.text()=='Group selected projects into Shot Sequence')
            found.append(action.isEnabled())
        class InspectMenu(QMenu):
            def exec(self,*args):return inspect(self,*args)
        with patch('ltx_prompt_director.ui.QMenu',InspectMenu):
            w.project_library_menu(w.project_list.visualItemRect(w.project_list.item(0)).center())
        assert found==[True]
        assert [r['id'] for r in w.selected_projects_in_order()]==['z','m']


def test_edit_next_group_reuses_completed_prefix_and_blend_only_reuses_both(tmp_path):
    with window(tmp_path) as w:
        prepare(w);w.requested_length.setValue(15);w.segments[0].duration=15
        w.add_video_extension();w.requested_length.setValue(5)
        runs=[];requests=[]
        w.render_queue.executor=lambda op,args,ready,failed,**kw:runs.append((op,args,ready,failed,kw))
        def request(value,**options):
            requests.append(value)
            path=tmp_path/f'block-{len(requests)}.mp4';path.write_bytes(f'render{len(requests)}'.encode())
            return {'path':str(path)}
        def reference(path,marked):return dict(kind='video',path=path,start=marked-1,duration=1,timeline_time=0)
        def build():
            first=len(runs)
            w.generate_local_video()
            for index in range(first,first+3):
                for _ in range(4):APP.processEvents()
                op,args,ready,failed,options=runs[index];ready(op(*args))
            for _ in range(4):APP.processEvents()
        with patch('ltx_prompt_director.ui.validate_profile'),patch('ltx_prompt_director.ui.run_request',side_effect=request),\
             patch('ltx_prompt_director.video_extensions.continuation_reference',side_effect=reference),\
             patch('ltx_prompt_director.video_extensions.add_block_thumbnails',side_effect=lambda result,*args:result),\
             patch('ltx_prompt_director.video_extensions.blend_blocks',return_value={'path':str(tmp_path/'final.mp4')}),\
             patch.object(w.project_preview_panel,'set_project'):
            build();assert len(requests)==2
            first_output=w.video_render_cache['0']['result']['path']
            w.segments[1].prompt='Turn and wave in the final five seconds.'
            build();assert len(requests)==3
            assert w.video_render_cache['0']['result']['path']==first_output
            assert 'Turn and wave' in requests[-1]['prompt']
            assert requests[-1]['references'][0]['path']==first_output
            w.set_extension_transition(1,2,'filmic_crossfade')
            build();assert len(requests)==3
            assert 'Reused completed block' in w.render_queue.jobs[-2].message


def test_single_mix_box_has_only_edge_length_controls_and_stays_centered(tmp_path):
    from PySide6.QtCore import QEvent,QPointF,QPoint
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QSlider
    with window(tmp_path) as w:
        prepare(w);w.add_video_extension();w.requested_length.setValue(5)
        w.resize(1800,1000);w.show();APP.processEvents();w.autofit_timeline();APP.processEvents()
        assert len(w.segments)==2 and len(w._extension_boxes)==1
        assert w.extension_brackets.isHidden()
        box=w._extension_boxes[1]
        assert box.findChildren(QSlider)==[]
        boundary=w.timeline.visualItemRect(w.timeline.item(1)).left()
        assert abs(box.geometry().center().x()-boundary)<=1
        handle=box.right_handle
        start=handle.mapToGlobal(handle.rect().center())
        press=QMouseEvent(QEvent.Type.MouseButtonPress,QPointF(handle.rect().center()),QPointF(start),Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        handle.mousePressEvent(press)
        move=QMouseEvent(QEvent.Type.MouseMove,QPointF(50,20),QPointF(start+QPoint(50,0)),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        handle.mouseMoveEvent(move)
        release=QMouseEvent(QEvent.Type.MouseButtonRelease,QPointF(50,20),QPointF(start+QPoint(50,0)),Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier)
        handle.mouseReleaseEvent(release)
        assert w.extension_transition(1)['overlap']>1
        assert abs(box.geometry().center().x()-boundary)<=1
        card=w.timeline.itemWidget(w.timeline.item(0))
        preview=card.preview
        top=preview.mapTo(w.timeline.viewport(),QPoint(0,0)).y()
        assert box.geometry().top()==top+2
        assert box.geometry().bottom()==top+preview.height()-3
        from PySide6.QtCore import QRect
        for control in (box.mode,box.side,box.diagram,box.left_handle,box.right_handle):
            region=QRect(control.mapTo(box,QPoint(0,0)),control.size())
            assert box.rect().contains(region), (control.objectName(),region,box.rect())


def test_original_single_video_is_reused_when_extension_is_added(tmp_path):
    with window(tmp_path) as w:
        prepare(w);w.requested_length.setValue(15);w.segments[0].duration=15
        runs=[];requests=[]
        w.render_queue.executor=lambda op,args,ready,failed,**kw:runs.append((op,args,ready,failed,kw))
        def request(value,**options):
            requests.append(value);path=tmp_path/f'output-{len(requests)}.mp4';path.write_bytes(f'video{len(requests)}'.encode())
            return {'path':str(path)}
        def reference(path,marked):return dict(kind='video',path=path,start=marked-1,duration=1,timeline_time=0)
        with patch('ltx_prompt_director.ui.validate_profile'),patch('ltx_prompt_director.ui.run_request',side_effect=request),\
             patch('ltx_prompt_director.video_extensions.continuation_reference',side_effect=reference),\
             patch('ltx_prompt_director.video_extensions.add_block_thumbnails',side_effect=lambda result,*args:result),\
             patch('ltx_prompt_director.video_extensions.blend_blocks',return_value={'path':str(tmp_path/'final.mp4')}),\
             patch.object(w.project_preview_panel,'set_project'):
            w.generate_local_video();op,args,ready,failed,options=runs[0];ready(op(*args))
            for _ in range(4):APP.processEvents()
            original=w.video_render_cache['0']['result']['path']
            w.add_video_extension();w.requested_length.setValue(5);w.generate_local_video()
            for index in range(1,4):
                for _ in range(4):APP.processEvents()
                op,args,ready,failed,options=runs[index];ready(op(*args))
            assert len(requests)==2
            assert requests[-1]['references'][0]['path']==original
            assert w.video_render_cache['0']['result']['path']==original
            assert w.render_queue.jobs[1].message=='Reused completed block'
