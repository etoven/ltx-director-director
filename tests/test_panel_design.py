"""Behavior checks for the shared prompt surface and project browser."""
import copy
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListWidget

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
            production = surface.editors.currentWidget()
            production.setPlainText('Independent production draft')
            surface.select('segment')
            assert surface.editors.currentWidget() is editor
            assert w.segments[0].prompt == 'Authored first beat'
            surface.select('unified')
            assert surface.editors.currentWidget() is production
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


def test_ltx_unified_generation_preserves_segments_and_uses_shared_export_prompt():
    w=window()
    try:
        w.set_project_type('ltx');w.prompt_surface.select('unified')
        before=copy.deepcopy([s.to_dict() for s in w.segments])
        with patch.object(w,'ai_credentials',return_value=('gemini','test','unused')),patch.object(w,'start_ai_worker') as worker:
            w.prompt_surface.run(False)
        assert worker.call_args.args[0] is ui.generate_global_direction
        worker.call_args.args[3]({'globalPrompt':'Shared production direction'})
        assert w.global_prompt.toPlainText()=='Shared production direction'
        assert [s.to_dict() for s in w.segments]==before
        from ltx_prompt_director import ai
        with patch.object(ai,'_structured_raw',return_value='{"prompt":"LTX production text"}') as provider:
            assert ai.refine_shared_production_prompt('gemini','test','unused','Shared LTX prose')=='LTX production text'
        assert provider.call_args.args[0]==[]
        assert 'Shared LTX prose' in provider.call_args.args[4]
        assert 'MiniMax' not in provider.call_args.args[4]
        with patch.object(w,'ai_credentials',return_value=('gemini','test','unused')),patch.object(w,'start_ai_worker') as worker:
            w.prompt_surface.run(True)
        assert worker.call_args.args[0] is ui.refine_shared_production_prompt
        assert 'Shared production direction' in worker.call_args.args[1]
        worker.call_args.args[3]('Refined production direction')
        assert w.global_prompt.toPlainText()=='Refined production direction'
        assert [s.to_dict() for s in w.segments]==before
        jobs=[]
        with patch.object(ui,'validate_profile'),patch.object(w,'start_disk_job',side_effect=lambda op,args,ready,failed,**kw:jobs.append((op,args,failed))):
            w.generate_local_video()
        with patch.object(ui,'run_request',return_value={'path':'/stand-in.mp4'}) as request:
            jobs[0][0](*jobs[0][1])
        assert request.call_args.args[0]['prompt']=='Refined production direction'
        jobs[0][2]('Request verification finished')
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
            w.resize(1400,900);w.show();w.project_dock.raise_();QTest.qWait(80)
            surface.layout_cards();QTest.qWait(30)
            first=w.project_list.visualItemRect(w.project_list.item(0))
            second=w.project_list.visualItemRect(w.project_list.item(1))
            assert first.top()==second.top(), (first,second,w.project_list.gridSize(),w.project_list.viewport().size())
            assert first.left()!=second.left()
            assert w.selected_library_project()['id']=='a'
            for color,action in surface.color_actions.items():action.setChecked(color=='#2ed05d')
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
    finally:
        w.project_dirty=False;w.close()


def test_panel_controls_fit_reference_and_narrow_layouts():
    w=window()
    try:
        w.set_project_type('minimax_frames');w.project_dock.show()
        for scale,width,height in [(100,1400,900),(100,1080,800),(120,1400,1000)]:
            w.settings.setValue('ui_text_scale',scale);w._apply_theme();w.resize(width,height);w.show();QTest.qWait(60)
            surface=w.prompt_surface
            for control in (surface.quick,surface.generate,surface.refine,surface.scope):
                assert surface.rail.rect().contains(control.geometry())
                assert control.height()>=control.fontMetrics().height()
            assert surface.width()<=w.editor_root.width()
            assert surface.editors.width()>150
            assert not w.conditioning_guide.isHidden()
        image=QImage(str(Path(ui.__file__).parent/'assets/icon.png'))
        assert image.hasAlphaChannel() and image.pixelColor(0,0).alpha()==0
        assert not w.windowIcon().isNull()
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
