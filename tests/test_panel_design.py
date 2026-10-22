"""Behavior checks for the shared prompt surface and project browser."""
import copy
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QListWidget

from ltx_prompt_director import ui
from ltx_prompt_director.models import Segment

APP = QApplication.instance() or QApplication([])


def window():
    with patch.object(ui.MainWindow, 'restore_startup_workspace'), patch.object(ui.MainWindow, 'keep_window_on_screen'):
        w = ui.MainWindow()
    w._close_saves_queued = True
    w.segments = [Segment('Opening', '', '', kind='text', prompt='First action', duration=2),
                  Segment('Ending', '', '', kind='text', prompt='Second action', duration=3)]
    w.refresh_timeline(0)
    return w


def test_one_surface_keeps_documents_and_scope_actions_across_workspaces():
    w = window()
    try:
        generic=copy.deepcopy(w.workspace_definitions['ltx'])
        generic.update(id='panel_generic',name='Generic demo',engine='generic',prompt_mode='unified')
        w.workspace_definitions['panel_generic']=generic
        w.project_type_combo.addItem('Generic demo','panel_generic')
        for mode in ('ltx', 'minimax_frames', 'minimax_references', 'panel_generic'):
            w.set_project_type(mode)
            w.timeline.setCurrentRow(0)
            surface = w.prompt_surface
            w.intent.setPlainText('Direct the whole sequence')
            surface.select('segment')
            editor = surface.editors.currentWidget()
            editor.setPlainText('Authored first beat')
            surface.select('global')
            assert surface.editors.currentWidget() is w.intent
            surface.select('unified')
            production = surface.editors.currentWidget() if w.unified_workspace else w.global_prompt
            assert (surface.scope.currentData()=='unified')==w.unified_workspace
            production.setPlainText('Independent production draft')
            surface.select('segment')
            assert surface.editors.currentWidget() is editor
            assert w.segments[0].prompt == 'Authored first beat'
            surface.select('unified')
            if w.unified_workspace:assert surface.editors.currentWidget() is production
            assert production.toPlainText() == 'Independent production draft'
            assert w.intent.toPlainText() == 'Direct the whole sequence'
            assert all(p.isHidden() for p in (w.director_panel,w.segment_panel,w.unified_panel))
            for scope, method in [('segment','generate_upper_prompt'),('global','refine_upper_prompt'),
                                  ('unified','refine_combined_prompt' if w.unified_workspace else None)]:
                surface.select(scope)
                if method:
                    with patch.object(w,method) as run:
                        surface.run(scope!='segment');run.assert_called_once()
            surface.select('segment')
            w.timeline.setCurrentRow(1)
            w.reload_clicked_segment(w.timeline.item(1))
            assert surface.editors.currentWidget().toPlainText() == 'Second action'
            state=w.capture_workspace_state()
            w.restore_workspace_state(state)
            assert surface.scope.currentData()==w.prompt_scope.currentData()=='segment'
            assert surface.editors.currentWidget().toPlainText()==w.current_segment().prompt
            assert w.segments[1].prompt=='Second action'
            assert production.toPlainText()=='Independent production draft'
    finally:
        w.project_dirty=False;w.close()


def test_segmented_workflows_hide_unified_and_preserve_stored_production():
    w=window()
    try:
        surface=w.prompt_surface
        w.global_prompt.setPlainText('Preserve stored production text')
        w.settings.setValue('prompt_surface_scope/ltx','unified')
        for mode in ['minimax_frames','ltx']:
            w.set_project_type(mode)
        index=surface.scope.findData('unified')
        assert not surface.scope.model().item(index).isEnabled()
        assert surface.scope.view().isRowHidden(index)
        assert surface.scope.currentData()=='segment'
        surface.select('unified')
        assert surface.scope.currentData()=='segment'
        surface.scope.setCurrentIndex(index)
        assert surface.scope.currentData()=='segment'
        for scope in ['global','segment']:
            surface.select(scope);assert surface.scope.currentData()==scope
        assert w.global_prompt.toPlainText()=='Preserve stored production text'
        generic=copy.deepcopy(w.workspace_definitions['ltx'])
        generic.update(id='panel_segmented',engine='generic',prompt_mode='segmented')
        w.workspace_definitions[generic['id']]=generic
        w.project_type_combo.addItem('Segmented',generic['id'])
        w.set_project_type(generic['id'])
        assert surface.scope.view().isRowHidden(index)
        w.set_project_type('minimax_frames')
        assert surface.scope.model().item(index).isEnabled()
        assert not surface.scope.view().isRowHidden(index)
        surface.select('unified');assert surface.scope.currentData()=='unified'
    finally:
        w.project_dirty=False;w.close()


def test_prompt_only_scope_and_timing_overview_remain_available():
    w=window()
    try:
        w.set_project_type('minimax_references');w.resize(1400,900);w.show();QTest.qWait(60)
        assert not w.conditioning_guide.isHidden()
        assert not w.conditioning_height_handle.isHidden()
        assert w.conditioning_guide.height()>0
        for scope in ('segment','global','unified'):
            w.prompt_surface.select(scope)
            assert not w.conditioning_guide.isHidden()
            assert w.prompt_surface.editors.currentWidget().isVisible()
        w.set_project_type('minimax_prompt_refinement')
        assert w.prompt_surface.scope.currentData()=='unified'
        assert w.prompt_surface.editors.currentWidget() is w.unified_prompt
        assert w.conditioning_guide.isHidden()
        assert not w.prompt_surface.scope.model().item(0).isEnabled()
        w.set_project_type('shot_sequence')
        assert w.prompt_surface.isHidden()
        assert not w.shot_sequence_panel.isHidden()
    finally:
        w.project_dirty=False;w.close()


def test_project_browser_filters_navigation_and_view_preserve_selection():
    w=window()
    try:
        records=[{'id':'a','name':'Opening','description':'Cool mountain light','status':'Draft','tags':[], 'color':'#2ed05d'},
                 {'id':'b','name':'Ending','description':'A quiet hold','status':'Ready','tags':[], 'color':'#168cff'},
                 {'id':'c','name':'Inside','collection':'Journey','status':'Draft','tags':[], 'color':'#2ed05d'}]
        w.library_records=Mock(return_value=records)
        with patch.object(ui,'ensure_collection_sequences',return_value=[]):
            w.refresh_project_library('a')
            surface=w.library_surface
            assert surface.color.text()=='Status: All'
            assert surface.status.text()=='Tags: All'
            assert [action.text() for action in surface.color_actions.values()]==[
                'Use status color','Ready','In progress','Needs review','Completed','Failed','Draft']
            assert all(not action.icon().isNull() for action in surface.color_actions.values())
            assert surface.all_statuses.text()=='All tags'
            surface.set_view('grid')
            w.resize(1400,900);w.show();w.project_dock.raise_();QTest.qWait(80)
            surface.layout_cards();QTest.qWait(30)
            first=w.project_list.visualItemRect(w.project_list.item(0))
            second=w.project_list.visualItemRect(w.project_list.item(1))
            assert first.top()==second.top(), (first,second,w.project_list.gridSize(),w.project_list.viewport().size())
            assert first.left()!=second.left()
            assert w.selected_library_project()['id']=='a'
            for color,action in surface.color_actions.items():action.setChecked(color=='#2ed05d')
            assert surface.color.text()=='Status: 1'
            assert surface.color.icon().pixmap(16,16).toImage().pixelColor(8,8).name()=='#2ed05d'
            visible=[w.project_list.item(i).data(Qt.ItemDataRole.UserRole)['name'] for i in range(w.project_list.count()) if not w.project_list.item(i).isHidden()]
            assert visible==['Journey','Opening']
            w.project_search.setText('Cool mountain')
            visible=[w.project_list.item(i).data(Qt.ItemDataRole.UserRole)['name'] for i in range(w.project_list.count()) if not w.project_list.item(i).isHidden()]
            assert visible==['Opening']
            surface.clear_filters();surface.set_view('list')
            assert w.project_list.viewMode()==QListWidget.ViewMode.ListMode
            assert w.selected_library_project()['id']=='a'
            surface.set_view('grid');surface.navigate('Journey')
            assert w.current_collection=='Journey' and w.project_list.count()==1
            surface.travel(-1);assert w.current_collection is None
            surface.travel(1);assert w.current_collection=='Journey'
            surface.navigate(None)
            w.refresh_project_library('a')
            with patch.object(w,'persist_library_metadata') as save:
                w.set_selected_project_color('#893dff')
            assert save.call_args.args[0]['color']=='#893dff'
            assert save.call_args.args[0]['status']=='Draft'
            w.settings.setValue('project_status_tags','[{"name":"Redo Color grade Issues","color":"#c96e7b"}]')
            w.rebuild_project_filter_buttons()
            assert surface.status_actions['status:Redo Color grade Issues'].text()=='Redo Color grade Issues'
            assert surface.color_actions['#2ed05d'].text()=='Ready'
    finally:
        w.project_dirty=False;w.close()


def test_panel_controls_fit_reference_and_narrow_layouts():
    w=window()
    try:
        w.set_project_type('minimax_frames');w.project_dock.show()
        for scale,width,height in [(100,1400,900),(100,1080,800),(120,1400,1000)]:
            w.settings.setValue('ui_text_scale',scale);w._apply_theme();w.resize(width,height);w.show();QTest.qWait(60)
            surface=w.prompt_surface
            for control in (surface.quick,surface.generate,surface.refine):
                assert surface.rail.rect().contains(control.geometry())
                assert control.height()>=control.fontMetrics().height()
            assert surface.scope.parent() is surface
            assert surface.scope.geometry().bottom()<surface.body.geometry().top()
            assert surface.scope.width()>=surface.width()-24
            assert surface.collapse in surface.scope.actions()
            for scope,label in [('segment','SEGMENT PROMPT'),('global','GLOBAL PROMPT'),('unified','UNIFIED PROMPT')]:
                surface.select(scope)
                assert surface.scope.currentText()==label
            assert surface.width()<=w.editor_root.width()
            assert surface.editors.width()>150
            assert not w.conditioning_guide.isHidden()
        image=QImage(str(Path(ui.__file__).parent/'assets/icon.png'))
        assert image.hasAlphaChannel() and image.pixelColor(0,0).alpha()==0
        assert not w.windowIcon().isNull()
    finally:
        w.project_dirty=False;w.close()


def test_project_inline_rename_f2_menu_enter_and_escape():
    w=window()
    try:
        records=[dict(id='rename-a',name='Opening',description='Keep description',status='Ready',tags=['Keep tag'],collection=''),
                 dict(id='rename-b',name='Ending',description='Second beat',status='Draft',tags=[],collection='Journey')]
        w.library_records=Mock(return_value=records)
        w.current_project_id='rename-a';w.current_project_name='Opening'
        def persist(meta):
            next(record for record in records if record['id']==meta['id']).update(meta)
        with patch.object(ui,'ensure_collection_sequences',return_value=[]),patch.object(w,'persist_library_metadata',side_effect=persist) as save:
            w.refresh_project_library('rename-a');w._project_open_timer.stop()
            w.resize(1400,900);w.show();w.project_dock.raise_();w.activateWindow();QTest.qWait(60)
            for mode,name in [('grid','Opening renamed'),('list','Opening renamed again')]:
                w.library_surface.set_view(mode);w.project_list.setFocus();QTest.qWait(10)
                QTest.keyClick(w.project_list,Qt.Key.Key_F2);QTest.qWait(10)
                editor=next(e for e in w.project_list.findChildren(QLineEdit,'projectNameEditor') if e.isVisible())
                assert w.project_list.visualItemRect(w.project_list.currentItem()).contains(editor.geometry().center())
                editor.setText(name);QTest.keyClick(editor,Qt.Key.Key_Return);QTest.qWait(30)
                assert records[0]['name']==name and w.current_project_name==name
                assert records[0]['status']=='Ready' and records[0]['tags']==['Keep tag']
                assert records[0]['description']=='Keep description'
                assert not w._project_open_timer.isActive()
            save.reset_mock();w.library_surface.rename.click();QTest.qWait(10)
            editor=next(e for e in w.project_list.findChildren(QLineEdit,'projectNameEditor') if e.isVisible())
            editor.setText('Cancelled');QTest.keyClick(editor,Qt.Key.Key_Escape);QTest.qWait(20)
            save.assert_not_called();assert records[0]['name']=='Opening renamed again'
            captured=[];BaseMenu=ui.QMenu
            class CapturedMenu(BaseMenu):
                def exec(self,*args):captured.append(self)
            with patch.object(ui,'QMenu',CapturedMenu):
                w.project_library_menu(w.project_list.visualItemRect(w.project_list.currentItem()).center())
            action=next(a for a in captured[0].actions() if a.text()=='Rename')
            assert action is w.library_surface.rename_action and action.shortcut().toString()=='F2'
            action.trigger();QTest.qWait(10)
            editor=next(e for e in w.project_list.findChildren(QLineEdit,'projectNameEditor') if e.isVisible())
            editor.setText('From context menu');QTest.keyClick(editor,Qt.Key.Key_Return);QTest.qWait(30)
            assert records[0]['name']=='From context menu'
            collection=next(w.project_list.item(i) for i in range(w.project_list.count())
                            if w.project_list.item(i).data(Qt.ItemDataRole.UserRole).get('kind')=='collection')
            w.project_list.setCurrentItem(collection);w.project_list.setFocus()
            QTest.keyClick(w.project_list,Qt.Key.Key_F2);QTest.qWait(10)
            editor=next(e for e in w.project_list.findChildren(QLineEdit,'projectNameEditor') if e.isVisible())
            assert editor.text()=='Journey'
            editor.setText('Travel');QTest.keyClick(editor,Qt.Key.Key_Return);QTest.qWait(30)
            assert records[1]['collection']=='Travel' and records[1]['name']=='Ending'
            assert w.selected_library_project()['name']=='Travel'
    finally:
        w.project_dirty=False;w.close()


def test_desktop_upgrade_refreshes_the_existing_launcher_icon(tmp_path):
    from ltx_prompt_director import desktop
    entry=tmp_path/'director.desktop';icon=tmp_path/'director.png'
    entry.write_text(desktop.desktop_contents());icon.write_bytes(b'old artwork')
    with (patch.object(desktop.sys,'platform','linux'),patch.object(desktop,'desktop_path',return_value=entry),
          patch.object(desktop,'icon_path',return_value=icon),patch.object(desktop,'legacy_paths',return_value=()),
          patch.object(desktop.shutil,'which',return_value=None)):
        desktop.install_desktop_entry()
    assert icon.read_bytes()==(Path(ui.__file__).parent/'assets/icon.png').read_bytes()
    assert entry.read_text()==desktop.desktop_contents()
