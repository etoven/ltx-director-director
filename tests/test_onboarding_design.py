"""Wizard navigation, honest readiness and safe asynchronous setup."""
from pathlib import Path
import sys
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from ltx_prompt_director.onboarding import OnboardingWizard
from test_local_generation_ui import window

APP = QApplication.instance() or QApplication([])


def test_nested_navigation_status_and_stale_engine_verification():
    with window() as w:
        wizard = OnboardingWizard(w); panel = wizard.local
        assert wizard.pages.count()==6 and len(wizard.steps.subbuttons)==4
        wizard.steps.subbuttons[2].click()
        assert wizard.steps.currentRow()==3 and panel.tabs.currentIndex()==2
        assert 'Images · Flux' in wizard.title.text()
        assert panel.readiness()[0][0]=='pending'
        jobs=[]
        with patch.object(w,'start_disk_job',side_effect=lambda op,args,ready,fail,**kw:jobs.append((args,ready,fail))):
            panel.setup('probe')
        assert panel.readiness()[0][0]=='busy'
        jobs.pop()[1]({'profile_changes':{},'message':'Verified'})
        wizard.update_readiness()
        assert wizard.steps.badges[0].state=='ready' and wizard.progress_checks[1].isChecked()
        with patch.object(w,'start_disk_job',side_effect=lambda op,args,ready,fail,**kw:jobs.append((args,ready,fail))):
            panel.setup('probe')
        panel.fields['python'].setText('/a/different/interpreter')
        jobs.pop()[1]({'profile_changes':{},'message':'Old interpreter verified'})
        assert panel.readiness()[0][0]=='pending'
        wizard.reject()
        assert not w.settings.contains('onboarding/seen')


def test_automatic_setup_preserves_manual_files_and_restores_controls(tmp_path):
    with window() as w:
        wizard=OnboardingWizard(w); panel=wizard.local
        wizard.local_video.setChecked(True)
        panel.fields['python'].setText(sys.executable)
        panel.fields['video_model'].setText('/chosen/my_pruned_ref2va.safetensors')
        calls=[]
        def queue(op,args,ready,fail,**kw):calls.append((args,ready,fail))
        with patch.object(w,'start_disk_job',side_effect=queue):
            panel.automatic_setup()
            files={key:[] for key in ('video_model','video_encoder','video_vae','audio_vae','image_model','image_encoder','image_vae','llm_model')}
            files['video_model']=['/discovered/other_pruned_ref2va.safetensors']
            calls.pop(0)[1]({'files':files,'gpu':{'total_gb':12,'free_gb':0}})
            args,ready,fail=calls.pop(0)
            assert args[0]['kind']=='probe'
            assert panel.fields['video_model'].text()=='/chosen/my_pruned_ref2va.safetensors'
            ready({'profile_changes':{},'message':'Verified'});APP.processEvents()
            args,ready,fail=calls.pop(0)
            assert args[0]['kind']=='assets' and args[0]['task']=='video'
            panel.fields['video_assets'].setText('/new/manual/config')
            ready({'profile_changes':{'video_assets':'/downloaded/config'},'message':'Ready'});APP.processEvents()
        assert panel.fields['video_assets'].text()=='/new/manual/config'
        assert not panel.auto_running and not panel.busy
        assert all(button.isEnabled() for button in (panel.auto,panel.prepare,panel.check,panel.download))
        assert not w.settings.contains('local_generation/profile_v1')
        wizard.reject()


def test_setup_errors_cancellation_and_closed_wizard_discard_callbacks():
    with window() as w:
        wizard=OnboardingWizard(w);panel=wizard.local;jobs=[]
        with patch.object(w,'start_disk_job',side_effect=lambda op,args,ready,fail,**kw:jobs.append((ready,fail))):
            panel.setup('probe')
        jobs.pop()[1]('CUDA check failed')
        assert panel.readiness()[0][0]=='pending' and panel.optional_toggle.isChecked()
        assert 'CUDA check failed' in panel.log.output.toPlainText()
        with patch.object(w,'start_disk_job',side_effect=lambda op,args,ready,fail,**kw:jobs.append((ready,fail))):
            panel.setup('assets')
        ready,fail=jobs.pop();before=panel.fields['video_assets'].text()
        wizard.reject();ready({'profile_changes':{'video_assets':'/stale/config'}});fail('stale error')
        assert panel.fields['video_assets'].text()==before and panel.cancel.is_set()


def test_guides_carousel_and_existing_setup_choices_are_preserved():
    with window() as w:
        w.unified_prompt.setPlainText('Keep my authored production prompt.')
        before=w.unified_prompt.toPlainText()
        wizard=OnboardingWizard(w)
        assert wizard.provider.count()==4 and wizard.image_model.count()>1
        assert all(key in wizard.local.fields for key in ('python','video_model','audio_vae','image_model','llm_model'))
        wizard.steps.setCurrentRow(4)
        wizard.open_tutorials()
        assert len(wizard.tutorial_slides.slides)==6
        wizard.tutorial.reject()
        wizard.start_quick_start();tour=wizard.workspace_tour
        for n in range(6):tour.advance()
        assert wizard.progress_checks[2].isChecked()
        assert w.unified_prompt.toPlainText()==before
        wizard.steps.setCurrentRow(5);wizard.showcase.show_slide(4)
        assert wizard.showcase.index==4
        wizard.showcase.next.click();assert wizard.showcase.index==0
        wizard.reject()
        assert not w.settings.contains('onboarding/quick_start_complete')
