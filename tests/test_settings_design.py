"""Settings navigation and asynchronous readiness without model or GPU execution."""
import sys
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QFormLayout
from ltx_prompt_director.ui import SettingsDialog
from ltx_prompt_director.settings_design import model_file_states
from test_local_generation_ui import window

APP=QApplication.instance() or QApplication([])


def test_settings_sidebar_search_preserves_pages_and_edits():
    from ltx_prompt_director.workspace_editor import WorkspaceEditor
    from ltx_prompt_director.chatgpt_widgets import ChatGPTSettingsPanel
    with window() as w, patch.object(w,'start_disk_job'):
        dialog=SettingsDialog(w.settings,w); dialog.resize(1280,956); dialog.show(); APP.processEvents()
        surface=dialog.surface
        assert dialog.tabs.count()==5 and dialog.tabs.tabBar().isHidden()
        assert dialog.local_models.tabs.tabBar().isHidden()
        assert surface.rail.width()==244
        assert surface.card.mapTo(surface,QPoint(0,0)).x()==286
        assert dialog.findChild(WorkspaceEditor) and dialog.findChild(ChatGPTSettingsPanel)
        assert dialog.storage_panel.export_button and dialog.storage_panel.import_button
        dialog.gemini.setText('settings-secret-value')
        dialog.local_models.loras['image'].add_row(dict(path='/models/ink.safetensors',trigger='ink lines',weight=.65))
        surface.subbuttons[2].click(); assert dialog.tabs.currentIndex()==1 and dialog.local_models.tabs.currentIndex()==2
        assert surface.section_title.text()=='Images · Flux'
        surface.search.setText('seed'); assert dialog.local_models.tabs.currentIndex()==1
        assert surface.subbuttons[2].isHidden() and not surface.subbuttons[1].isHidden()
        surface.search.setText('backup'); assert dialog.tabs.currentIndex()==4
        surface.search.clear(); assert all(not button.isHidden() for button in surface.buttons)
        assert dialog.gemini.text()=='settings-secret-value'
        assert dialog.local_models.loras['image'].rows[0].weight.value()==.65
        assert 'settings-secret-value' not in ' '.join(surface.search_index+surface.local_search)
        surface.search.setText('nothing-matches-this-setting'); assert not surface.no_results.isHidden()
        surface.search.clear(); surface.select(1,2); APP.processEvents()
        assert dialog.local_models.tabs.height()>=dialog.local_models.tabs.currentWidget().layout().sizeHint().height()
        form=dialog.local_models.tabs.widget(2).layout()
        browse=form.itemAt(4,QFormLayout.ItemRole.FieldRole).layout().itemAt(1).widget()
        right=browse.mapTo(dialog.local_models,QPoint(browse.width(),0)).x()
        action_right=dialog.local_models.check.mapTo(dialog.local_models,QPoint(dialog.local_models.check.width(),0)).x()
        assert abs(right-action_right)<=2
        assert dialog.local_models.width()-action_right>=16
        for width,height in ((1040,760),(1280,956)):
            dialog.resize(width,height); APP.processEvents()
            assert dialog.buttons.isVisible() and dialog.buttons.geometry().bottom()<=dialog.buttons.parentWidget().height()
            assert surface.description.height()>=surface.description.heightForWidth(surface.description.width())
        dialog.reject()


def test_settings_large_text_keeps_navigation_and_footer_accessible():
    with window() as w, patch.object(w,'start_disk_job'):
        w.settings.setValue('ui_text_scale',200)
        large=SettingsDialog(w.settings,w); large.resize(1500,960); large.surface.select(1,2); large.show(); APP.processEvents(); APP.processEvents()
        assert large.buttons.isVisible() and large.buttons.geometry().bottom()<=large.buttons.parentWidget().height()
        assert large.surface.navigation_scroll.verticalScrollBar().maximum()>0
        large.surface.navigation_scroll.ensureWidgetVisible(large.surface.buttons[4])
        large.surface.buttons[4].click(); assert large.tabs.currentIndex()==4
        large.reject()


def test_settings_readiness_discards_stale_results_and_requires_explicit_engine_check():
    with window() as w:
        jobs=[]
        def queue(op,args,ready,fail,**kw):jobs.append((op,args,ready,fail))
        with patch.object(w,'start_disk_job',side_effect=queue):
            dialog=SettingsDialog(w.settings,w); surface=dialog.surface; panel=dialog.local_models
            surface.refresh_readiness(); old=[j for j in jobs if j[0] is model_file_states][-1]
            panel.fields['image_model'].setText('/new/model.safetensors')
            old[2]([('ready','Outdated files')]*3)
            assert surface.badges[2].state=='pending'
            surface.refresh_readiness(); new=[j for j in jobs if j[0] is model_file_states][-1]
            new[2]([('ready','Files configured')]*3)
            assert surface.badges[2].state=='ready' and surface.badges[0].state=='pending'
            panel.fields['python'].setText(sys.executable); panel.setup('probe')
            probe=jobs[-1]; assert probe[1][0]['kind']=='probe' and surface.badges[0].state=='busy'
            panel.fields['python'].setText('/changed/interpreter'); probe[2]({'profile_changes':{},'message':'Old runtime'})
            assert surface.badges[0].state=='pending'
            panel.setup('probe'); jobs[-1][2]({'profile_changes':{},'message':'Runtime checked'})
            assert surface.badges[0].state=='ready'
            surface.refresh_readiness(); pending=jobs[-1]
            dialog.reject(); pending[2]([('pending','Late callback')]*3)
            assert surface.badges[0].state=='ready' and surface.disposed
            assert not w.settings.contains('local_generation/profile_v1')


def test_settings_model_file_readiness_never_probes_runtime(tmp_path):
    profile={key:str(tmp_path/key) for key in ('video_model','video_encoder','video_vae','audio_vae','video_assets','image_model','image_encoder','image_vae','image_assets','llm_model')}
    profile['video_model']=str(tmp_path/'h3_pruned.safetensors')
    for path in profile.values():Path(path).touch()
    with patch('ltx_prompt_director.local_generation.run_request',side_effect=AssertionError('No runtime probe')), patch('ltx_prompt_director.local_generation.gpu_memory',side_effect=AssertionError('No GPU query')):
        assert all(state=='ready' for state,_ in model_file_states(profile))
