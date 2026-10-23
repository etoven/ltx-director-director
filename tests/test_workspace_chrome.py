"""Native chrome, dock and render-list regressions; no GPU/API calls."""
from unittest.mock import Mock,patch
import copy
import pytest
from PySide6.QtCore import QPoint,Qt
from PySide6.QtGui import QFont,QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QLabel,QMainWindow,QMenu,QStyleOptionViewItem,QToolBar,QWidget
from ltx_prompt_director.window_chrome import WindowChrome
from ltx_prompt_director.render_queue import RenderQueue
from ltx_prompt_director.render_widgets import RenderQueuePanel

APP=QApplication.instance() or QApplication([])


def test_header_keeps_commands_and_centered_title_through_resize():
    w=QMainWindow();bar=QToolBar();w.addToolBar(bar)
    for name in ('New Project','Open','Save to Library','Panel','Workflow','Settings'):bar.addAction(name)
    chrome=WindowChrome(w,bar,'A project title');w.show()
    for width in (900,1000,1920):
        w.resize(width,650);QTest.qWait(10)
        assert abs(chrome.document.mapTo(w,chrome.document.rect().center()).x()-w.rect().center().x())<=1
        assert chrome.controls.mapTo(w,QPoint()).x()+chrome.controls.width()<=w.width()
        assert set(chrome.original_actions)==set(bar.actions()+chrome.project_commands.actions()+chrome.utility_commands.actions())
    before=w.geometry();chrome.maximize.click();QTest.qWait(10);assert w.isMaximized()
    chrome.maximize.click();QTest.qWait(10);assert w.geometry()==before
    handle=Mock();handle.startSystemMove.return_value=True;handle.startSystemResize.return_value=True
    with patch.object(w,'windowHandle',return_value=handle):
        QTest.mouseClick(chrome.document,Qt.MouseButton.LeftButton);handle.startSystemMove.assert_called_once()
        QTest.mouseClick(w,Qt.MouseButton.LeftButton,pos=QPoint(1,1));handle.startSystemResize.assert_called_once()
    w.close()


def test_queue_source_labels_stay_in_viewport_at_narrow_width_and_large_text():
    queue=RenderQueue(lambda *a,**k:None);queue.set_paused(True)
    for source in ('cloud','local'):
        for i in range(8):queue.enqueue(lambda:None,(),lambda _:None,lambda _:None,title='Long project title with a frame',source=source,model='A long model name')
    panel=RenderQueuePanel(queue)
    for size in (14,21,28):
        font=QFont('DejaVu Sans');font.setPixelSize(size);panel.setFont(font)
        for width in (300,390,520):
            panel.resize(width,850);panel.show();QTest.qWait(15)
            view=panel.list;assert not view.horizontalScrollBar().isVisible()
            assert view.verticalScrollBar().isVisible()
            assert view.itemWidget(view.item(0)) is None
            assert view.viewport().geometry().right()<view.verticalScrollBar().mapTo(view,QPoint()).x()
            assert view.visualItemRect(view.item(0)).right()<=view.viewport().width()
            shot=panel.grab();assert not shot.isNull()
    queue.shutdown();panel.close()


def make_window():
    from ltx_prompt_director import ui
    with patch.object(ui.MainWindow,'restore_startup_workspace'),patch.object(ui.MainWindow,'keep_window_on_screen'):
        w=ui.MainWindow()
    w._close_saves_queued=True;w.resize(1600,950);w.show();QTest.qWait(50)
    return w


def test_legacy_toolbar_layout_cannot_hide_or_reclaim_header_commands():
    from PySide6.QtCore import QSettings
    settings=QSettings()
    for visible in (True,False):
        legacy=QMainWindow();legacy.resize(1600,950)
        bar=QToolBar('Project',legacy);bar.setObjectName('mainToolbar')
        legacy.addToolBar(bar);bar.addAction('Legacy command')
        legacy.show();bar.setVisible(visible);QTest.qWait(10)
        settings.setValue('window/state',legacy.saveState());legacy.close()
        w=make_window()
        try:
            current=w.findChild(QToolBar,'mainToolbar')
            assert w.toolBarArea(current)==Qt.ToolBarArea.NoToolBarArea
            assert current.isVisible() and current.parent() is w.chrome.header
            assert w.project_type_combo.isVisible() and w.provider_button.isVisible()
            assert w.project_type_combo.height()>0
            assert set(w.chrome.original_actions)==set(current.actions()+w.chrome.project_commands.actions()+w.chrome.utility_commands.actions())
            for maximized in (True,False):
                w.showMaximized() if maximized else w.showNormal()
                QTest.qWait(20)
                assert current.isVisible() and w.project_type_combo.isVisible()
        finally:w.project_dirty=False;w.close()


def test_project_status_menu_uses_settings_and_recolors_legacy_cards(tmp_path):
    import json
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QColor,QPainter,QPixmap
    from PySide6.QtWidgets import QStyle
    from ltx_prompt_director import ui
    w=make_window()
    try:
        definitions=[{'name':'Review footage','color':'#ba63db'},
                     {'name':'Approved cut','color':'#46b185'}]
        w.settings.setValue('project_status_tags',json.dumps(definitions))
        w.rebuild_project_filter_buttons()
        w.library_surface.clear_filters()
        meta={'id':'status-test','name':'A shot','status':'Review footage','tag':'Review footage',
              'color':'#168cff','tags':[]}
        path=tmp_path/'status-test.meta.json';path.write_text(json.dumps(meta))
        w.library_records=lambda:[json.loads(path.read_text())]
        with patch.object(ui,'ensure_collection_sequences',return_value=[]):
            w.refresh_project_library(meta['id'])
            item=w.project_list.currentItem();menus=[]
            class CapturedMenu(ui.QMenu):
                def exec(self,*_):menus.append(self)
            with patch.object(ui,'QMenu',CapturedMenu):
                w.project_library_menu(w.project_list.visualItemRect(item).center())
            menu=menus[0]
            assert [a.text() for a in menu.actions() if 'status' in a.text().casefold()]==['Status']
            status=next(a.menu() for a in menu.actions() if a.text()=='Status')
            assert [a.text() for a in status.actions()]==['No status','Review footage','Approved cut']
            assert status.actions()[1].isChecked()
            assert all(not a.icon().isNull() for a in status.actions()[1:])
            with patch.object(ui,'project_library_path',return_value=tmp_path),patch.object(w,'start_disk_job'):
                status.actions()[2].trigger()
            saved=json.loads(path.read_text())
            assert saved['status']==saved['tag']=='Approved cut'
            assert w.project_state_color(saved)=='#46b185'
            assert w.project_state_color({**saved,'archived':True})=='#46b185'
            item=w.project_list.currentItem()
            assert item.data(Qt.ItemDataRole.UserRole)['status']=='Approved cut'
            pixmap=QPixmap(QSize(250,220));pixmap.fill(Qt.GlobalColor.transparent)
            option=QStyleOptionViewItem();option.rect=pixmap.rect();option.state=QStyle.StateFlag.State_Enabled
            painter=QPainter(pixmap)
            w.project_list.itemDelegate().paint(painter,option,w.project_list.indexFromItem(item));painter.end()
            assert pixmap.toImage().pixelColor(30,7)==QColor('#46b185')
            # Settings changes recolor existing cards without rewriting projects.
            w.settings.setValue('project_status_tags',json.dumps([{'name':'Approved cut','color':'#e39042'}]))
            w.rebuild_project_filter_buttons()
            assert w.project_state_color(saved)=='#e39042'
            assert w.project_state_color({**saved,'status':'','tag':''}) is None
    finally:w.project_dirty=False;w.close()


def test_group_status_updates_every_member_and_clears_legacy_color():
    import json
    from ltx_prompt_director import ui
    w=make_window()
    try:
        w.settings.setValue('project_status_tags',json.dumps([{'name':'Custom ready','color':'#35ac72'}]))
        w.rebuild_project_filter_buttons();w.library_surface.clear_filters()
        records=[{'id':str(i),'name':str(i),'collection':'Cuts','status':'','color':'#168cff','tags':[]} for i in range(2)]
        w.library_records=lambda:copy.deepcopy(records)
        with patch.object(ui,'ensure_collection_sequences',return_value=[]):
            w.refresh_project_library()
            item=next(w.project_list.item(i) for i in range(w.project_list.count()) if w.project_list.item(i).data(Qt.ItemDataRole.UserRole).get('kind')=='collection')
            menus=[]
            class CapturedMenu(ui.QMenu):
                def exec(self,*_):menus.append(self)
            with patch.object(ui,'QMenu',CapturedMenu):
                w.project_library_menu(w.project_list.visualItemRect(item).center())
            actions=[a for a in menus[0].actions() if 'status' in a.text().casefold()]
            assert len(actions)==1 and actions[0].text()=='Group status'
            with patch.object(w,'persist_library_metadata') as save:
                actions[0].menu().actions()[1].trigger()
            assert save.call_count==2
            for call in save.call_args_list:
                meta=call.args[0]
                assert meta['status']==meta['tag']=='Custom ready' and 'color' not in meta
                assert w.project_state_color(meta)=='#35ac72'
    finally:w.project_dirty=False;w.close()


def test_timing_overview_refreshes_when_text_segments_are_created_edited_and_removed():
    from PySide6.QtWidgets import QFrame
    from ltx_prompt_director.models import Segment
    w=make_window()
    try:
        w.set_project_type('minimax_frames')
        w.segments=[Segment('Opening','','','image','start','Opening view',3)]
        w.refresh_timeline(0)
        def tiles():
            return [w.conditioning_row.itemAt(i).widget() for i in range(w.conditioning_row.count())
                    if w.conditioning_row.itemAt(i).widget() and w.conditioning_row.itemAt(i).widget().objectName()=='conditioningTile']
        def ranges():
            return [[l.text() for l in tile.findChild(QFrame,'conditioningFooter').findChildren(QLabel)] for tile in tiles()]
        w.add_text_tile.click();w.add_text_tile.click()
        assert [tile.findChild(QLabel,'conditioningTitle').text() for tile in tiles()]==['Image1','Text 1','Text 2']
        assert ranges()==[['Start 0.00s','End 3.00s'],['Start 3.00s','End 8.00s'],['Start 8.00s','End 13.00s']]
        assert w.conditioning_row.itemAt(0).widget().text()=='I2V\n13.00s'
        image_tile=tiles()[0]
        w.segment_prompt.setPlainText('Continue the movement')
        assert tiles()[0] is image_tile
        assert tiles()[2].findChild(QLabel,'conditioningTextPreview').text()=='Continue the movement'
        w.change_duration(w.segments[1].id,2)
        assert ranges()==[['Start 0.00s','End 3.00s'],['Start 3.00s','End 5.00s'],['Start 5.00s','End 10.00s']]
        assert [r['checkpoint_time'] for r in w.conditioning_records if 'checkpoint_time' in r]==[0]
        assert [r['label'] for r in w.conditioning_records]==['Image1',None,None]
        w.delete_selected()
        assert len(tiles())==2 and ranges()[-1]==['Start 3.00s','End 5.00s']
        w.segments=w.segments[1:];w.refresh_timeline(0)
        assert len(tiles())==1 and ranges()==[['Start 0.00s','End 2.00s']]
        assert w.conditioning_row.itemAt(0).widget().text()=='T2V\n2.00s'
    finally:w.project_dirty=False;w.close()


def test_long_text_previews_fit_overview_and_keep_all_footers_visible(tmp_path):
    from PySide6.QtWidgets import QFrame
    from ltx_prompt_director.models import Segment
    w=make_window()
    try:
        w.set_project_type('minimax_frames')
        image=tmp_path/'preview.png';frame=QImage(320,180,QImage.Format.Format_RGB32);frame.fill(Qt.GlobalColor.darkBlue);frame.save(str(image))
        text='A detailed description of camera movement and the surrounding landscape. '*100
        w.segments=[Segment('Source','',str(image),'video','start','Source guide',3),
                    Segment('Text 1','','','text','text',text,3),
                    Segment('Text 2','','','text','text',text,3),
                    Segment('Text 3','','','text','text',text,6)]
        w.refresh_timeline(3)
        for scale,height,width in ((100,96,1100),(100,144,1800),(100,220,1800),(150,144,1100),(200,220,1800),(100,144,1100)):
            w.set_ui_text_scale(scale);w.resize(width,1100);w.set_conditioning_height(height);QTest.qWait(30)
            viewport=w.conditioning_guide.viewport()
            assert w.conditioning_content.height()<=viewport.height()
            for i in range(1,5):
                tile=w.conditioning_row.itemAt(i).widget()
                footer=tile.findChild(QFrame,'conditioningFooter')
                assert footer.isVisible()
                y=footer.mapTo(viewport,QPoint()).y()
                assert 0<=y and y+footer.height()<=viewport.height(),(scale,height,y,footer.height(),viewport.height())
                assert tile.height()<=viewport.height()
        # Editing an already-visible preview must not restore content-driven sizing.
        w.segment_prompt.setPlainText(text*3);QTest.qWait(30)
        assert w.conditioning_content.height()<=w.conditioning_guide.viewport().height()
        w.conditioning_guide.grab().save(str(tmp_path/'overview-fitted.png'))
    finally:w.project_dirty=False;w.close()


def test_native_dock_contents_survive_collapse_float_move_and_reference_capabilities():
    w=make_window()
    try:
        original=w.effects_dock.widget();rails=w.panel_rails
        rails.activate(w.effects_dock);QTest.qWait(30)
        assert not w.effects_dock.isHidden() and w.effects_dock.widget() is original
        rails.collapse('left');QTest.qWait(20);assert w.effects_dock.isHidden()
        rails.activate(w.effects_dock);QTest.qWait(20);assert w.effects_dock.widget() is original
        w.effects_dock.setFloating(True);QTest.qWait(20);assert w.effects_dock.isFloating()
        rails.dock_at(w.effects_dock,Qt.DockWidgetArea.BottomDockWidgetArea);QTest.qWait(20)
        assert w.dockWidgetArea(w.effects_dock)==Qt.DockWidgetArea.BottomDockWidgetArea and w.effects_dock.widget() is original
        rails.collapse_dock(w.effects_dock);assert w.effects_dock.isHidden()
        rails.activate(w.effects_dock);assert not w.effects_dock.isHidden()
        w.set_project_type('minimax_references');w.set_reference_panel_open(True);QTest.qWait(20)
        dock=w.minimax_panel.reference_dock;area=w.dockWidgetArea(dock)
        w.set_project_type('minimax_frames');QTest.qWait(20)
        assert dock.isHidden() and rails.buttons[dock].isHidden()
        w.set_project_type('minimax_references');QTest.qWait(20)
        assert not dock.isHidden() and w.dockWidgetArea(dock)==area
        assert not rails.buttons[dock].isHidden()
    finally:w.project_dirty=False;w.close()


def test_queue_result_updates_originating_project_session_without_touching_current(tmp_path):
    from ltx_prompt_director.models import Segment
    from ltx_prompt_director.artwork_tools import ArtworkTool
    w=make_window();runs=[]
    try:
        w.current_project_id='first';w.current_project_name='First';w.set_project_type('minimax_frames')
        source=Segment('frame','','','text','text','Authored first prompt',5);source.artwork_prompt='Make original frame'
        w.segments=[source];w.refresh_timeline(0)
        tool=ArtworkTool('gemini','fake','test-key')
        w.render_queue.executor=lambda *args,**kwargs:runs.append((args,kwargs))
        w._start_segment_artwork(source.id,{'artworkPrompt':source.artwork_prompt},tool)
        assert len(runs)==1
        w.cache_current_workspace();w.current_project_id='second';w.current_project_name='Second';w.workspace_revision+=1
        other=Segment('other','','','text','text','Do not replace',5);w.segments=[other];w.refresh_timeline(0)
        image=tmp_path/'result.png';QImage(32,32,QImage.Format.Format_RGB32).save(str(image))
        runs[0][0][2]({source.id:str(image)});QTest.qWait(20)
        saved=w.project_sessions['first']['state']['segments'][0]
        assert saved.media_path==str(image) and w.project_sessions['first']['dirty']
        assert w.segments==[other] and other.prompt=='Do not replace' and not other.media_path
    finally:
        w.current_project_id=None;w.project_sessions.clear();w.project_dirty=False;w.close()


def test_local_video_queue_captures_prompt_and_runs_after_prior_project(tmp_path):
    from ltx_prompt_director import ui
    from ltx_prompt_director.models import Segment
    w=make_window();runs=[]
    try:
        w.set_project_type('minimax_frames');w.current_project_id='video-first';w.current_project_name='First'
        w.minimax_prompt_text='First authored unified output';w.segments=[Segment('first','','','text','text','First',15)]
        w.render_queue.executor=lambda *args,**kwargs:runs.append((args,kwargs))
        profile={'video_duration':15,'video_width':1280,'video_height':704,'video_steps':20,'video_seed':42}
        with patch.object(ui,'load_profile',return_value=profile),patch.object(ui,'validate_profile'):
            w.generate_local_video()
            w.current_project_id='video-second';w.current_project_name='Second';w.workspace_revision+=1
            w.minimax_prompt_text='Second authored unified output';w.segments=[Segment('second','','','text','text','Second',15)]
            w.generate_local_video()
        assert len(runs)==1 and len(w.render_queue.jobs)==2 and w.render_queue.jobs[1].state=='Waiting'
        with patch.object(ui,'run_request',return_value={'path':str(tmp_path/'first.mp4')}) as render:
            result=runs[0][0][0]();assert render.call_args.args[0]['prompt']=='First authored unified output'
            assert render.call_args.args[0]['seed']==42
        runs[0][0][2](result);QTest.qWait(20)
        assert len(runs)==2 and w.render_queue.jobs[1].state=='Rendering'
        with patch.object(ui,'run_request',return_value={'path':str(tmp_path/'second.mp4')}) as render:
            runs[1][0][0]();assert render.call_args.args[0]['prompt']=='Second authored unified output'
        w.render_queue.shutdown();runs[1][0][3]('Cancelled')
    finally:w.current_project_id=None;w.project_sessions.clear();w.project_dirty=False;w.close()


def test_saved_native_layout_restores_outer_rails_and_selected_panel():
    first=make_window()
    first._restoring_layout=False
    first.panel_rails.activate(first.render_queue_dock);QTest.qWait(50)
    first.resizeDocks([first.render_queue_dock],[380],Qt.Orientation.Horizontal);QTest.qWait(20)
    first.project_dirty=False;first.close()
    second=make_window()
    try:
        QTest.qWait(50)
        assert not second.render_queue_dock.isHidden()
        assert second.render_queue_dock.width()>=260
        left=second.panel_rails.rails['left'];right=second.panel_rails.rails['right']
        assert not left.isHidden() and not right.isHidden()
        assert left.x()<second.project_dock.x()
        assert right.x()>second.render_queue_dock.x()
        assert second.render_queue_dock not in second.tabifiedDockWidgets(right)
        assert second.project_dock not in second.tabifiedDockWidgets(left)
    finally:second.project_dirty=False;second.close()


def test_cancelled_render_failure_does_not_leave_closing_window_hidden():
    from PySide6.QtCore import QObject
    from ltx_prompt_director.ui import BackgroundCallbacks
    owner=QObject();owner._close_after_jobs=True;owner._pending_disk_jobs=1
    owner.update_closing_progress=Mock();owner.close=Mock()
    failed=Mock();callbacks=BackgroundCallbacks(owner,Mock(),failed,allow_close_on_error=True)
    callbacks.on_failed('Render cancelled')
    assert owner._pending_disk_jobs==0 and owner._close_after_jobs
    failed.assert_called_once_with('Render cancelled')
    owner._pending_disk_jobs=1
    callbacks=BackgroundCallbacks(owner,Mock(),failed)
    callbacks.on_failed('Archive save failed');assert not owner._close_after_jobs


def test_changed_target_keeps_edit_and_releases_generating_state(tmp_path):
    from ltx_prompt_director.models import Segment
    from ltx_prompt_director.artwork_tools import ArtworkTool
    w=make_window();runs=[]
    try:
        w.current_project_id=None
        segment=Segment('frame','','','text','text','Initial prompt',5);segment.artwork_prompt='A mountain'
        w.segments=[segment];w.refresh_timeline(0)
        w.render_queue.executor=lambda *a,**k:runs.append((a,k))
        w._start_segment_artwork(segment.id,{'artworkPrompt':'A mountain'},ArtworkTool('gemini','test','unused'))
        segment.prompt='My newer authored direction'
        image=tmp_path/'new.png';QImage(16,16,QImage.Format.Format_RGB32).save(str(image))
        runs[0][0][2]({segment.id:str(image)})
        assert segment.prompt=='My newer authored direction' and not segment.media_path
        assert segment.artwork_status=='awaiting' and image.is_file()
        assert 'target changed' in w.render_queue.jobs[-1].message
    finally:w.current_project_id=None;w.project_dirty=False;w.close()


def test_image_panel_cancel_marks_waiting_local_jobs_and_keeps_cloud_work():
    from types import SimpleNamespace
    from ltx_prompt_director.ui import MainWindow
    runs=[];queue=RenderQueue(lambda *a,**k:runs.append((a,k)))
    local=queue.enqueue(lambda:None,(),lambda _:None,lambda _:None,title='Local one',source='local')
    waiting=queue.enqueue(lambda:None,(),lambda _:None,lambda _:None,title='Local two',source='local')
    cloud=queue.enqueue(lambda:None,(),lambda _:None,lambda _:None,title='Cloud',source='cloud')
    owner=SimpleNamespace(render_queue=queue,_local_artwork_cancel={})
    MainWindow.cancel_local_artwork(owner)
    assert local.state=='Cancelling' and waiting.state=='Cancelled' and cloud.state=='Waiting'
    assert len(runs)==1
    queue.shutdown();runs[0][0][2](None)


def test_mode_switch_cannot_apply_image_through_cached_segment_alias(tmp_path):
    from ltx_prompt_director.models import Segment
    from ltx_prompt_director.artwork_tools import ArtworkTool
    w=make_window();runs=[]
    try:
        w.current_project_id='same-project';w.set_project_type('minimax_references')
        segment=Segment('frame','','','text','text','Keep the authored beat',5);segment.artwork_prompt='A mountain'
        w.segments=[segment];w.refresh_timeline(0);w.cache_current_workspace()
        w.render_queue.executor=lambda *a,**k:runs.append((a,k))
        w._start_segment_artwork(segment.id,{'artworkPrompt':'A mountain'},ArtworkTool('gemini','test','unused'))
        w.set_project_type('minimax_frames')
        image=tmp_path/'old-mode.png';QImage(16,16,QImage.Format.Format_RGB32).save(str(image))
        runs[0][0][2]({segment.id:str(image)})
        assert w.project_sessions['same-project']['state']['segments'][0] is segment
        assert not segment.media_path and segment.prompt=='Keep the authored beat'
        assert segment.artwork_status=='awaiting'
    finally:w.current_project_id=None;w.project_sessions.clear();w.project_dirty=False;w.close()
