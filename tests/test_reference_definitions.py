"""One native flow for mixed references; no model weights or provider calls."""
import copy
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from PySide6.QtWidgets import QApplication, QPushButton
from ltx_prompt_director import ai, ui
from ltx_prompt_director.models import Segment
from ltx_prompt_director.media import data_url
from ltx_prompt_director.prompt_references import subjects, generate_definitions, reassess_references


def test_reference_contract_and_migrated_media_identities(monkeypatch):
    refs=[dict(name='voice',kind='audio',path='voice.wav',audio_path='voice.wav',role='voice',notes='Soft voice'),None,None,None]
    segments=[Segment('clip','','','video','start','Action',2)]
    state=dict(custom_subjects=[dict(id='subject:legacy:video-1',name='Video 1',prompt='Saved identity',role='identity')])
    records=subjects(segments,refs,state)
    assert [s['name'] for s in records]==['Video 1','Audio1']
    assert records[0]['id']=='subject:legacy:video-1'
    assert records[0]['timeline_id']==segments[0].id
    captured={}
    monkeypatch.setattr(ai,'_mixed_reference_input',lambda record,source,provider:{**record,'audio_analysis_mode':'explicit notes only'})
    def respond(inputs,provider,model,key,rules,timeout,**kwargs):
        captured.update(inputs=inputs,rules=rules)
        return json.dumps(dict(definitions=[dict(id=records[1]['id'],prompt='Soft voice definition')]))
    monkeypatch.setattr(ai,'_structured_raw',respond)
    assert generate_definitions(segments,refs,[records[1]],'openai','model','key',10)=={records[1]['id']:'Soft voice definition'}
    assert len(captured['inputs'])==1 and captured['inputs'][0]['kind']=='audio'
    assert 'unheard audio' in captured['rules']
    custom = dict(id='subject:custom', name='Custom subject', prompt='Blue coat', role='identity')
    def text_only(inputs,*args,**kwargs):
        assert inputs==[]
        return json.dumps(dict(definitions=[dict(id=custom['id'],prompt='Blue coat definition')]))
    monkeypatch.setattr(ai,'_structured_raw',text_only)
    assert generate_definitions(segments,refs,[custom],'local','model','key',10)[custom['id']]=='Blue coat definition'
    monkeypatch.setattr(ai,'_structured_raw',lambda *a,**k:json.dumps(dict(definitions=[dict(id='wrong',prompt='Wrong scope')])) )
    with pytest.raises(ai.AIResponseFormatError):generate_definitions(segments,refs,[records[1]],'openai','model','key',10)


def test_reassessment_returns_model_sections_without_text_matching(monkeypatch):
    original = {'global': 'Quiet scene. Voice from <Audio2>. Keep blue lighting.',
                'shot': 'She says <d>Good evening.</d> using <Audio2>. Then turns.',
                'field:sound': 'Match <Audio2>.', 'field:music': 'No music.'}
    revised = {'global': 'Quiet scene. Keep the blue lighting.',
               'shot': 'She says <d>Good evening.</d>, then turns.',
               'field:sound': '', 'field:music': 'No music.'}
    captured = {}
    def respond(inputs,*args,**kwargs):
        captured.update(inputs=inputs,rules=args[3],schema=kwargs['schema'])
        return json.dumps(dict(fields=revised))
    monkeypatch.setattr(ai,'_structured_raw',respond)
    assert reassess_references(original,[],[],'gemini','model','key',10)==revised
    assert 'renumbered labels by source identity' in captured['rules']
    assert captured['inputs']==[]
    assert '<Audio2>' in original['global']
    assert set(captured['schema']['properties']['fields']['properties'])==set(original)


def test_native_automatic_definitions_and_unified_flag(tmp_path,monkeypatch):
    from PySide6.QtTest import QTest
    app=QApplication.instance() or QApplication([])
    with patch.object(ui.MainWindow,'restore_startup_workspace'):
        w=ui.MainWindow()
    try:
        w.segments=[Segment('Opening','','','text','text','Keep timeline prose',2)]
        w.refresh_timeline(0);w.set_project_type('minimax_official_skill_reference')
        panel=w.prompt_surface.structured;panel.refresh()
        state=panel.state();state['global']='Keep summary';state['fields'].update(sound='Keep sound',music='Keep music')
        panel.refresh()
        launched=[];calls=[];credential_modes=[]
        def credentials(**options):
            credential_modes.append(options);return ('gemini','model','key')
        monkeypatch.setattr(w,'ai_credentials',credentials)
        monkeypatch.setattr(panel,'launch_definition_worker',lambda operation,args,cancel:launched.append((operation,args,cancel)))
        monkeypatch.setattr(w,'start_ai_worker',lambda operation,args,activity,finished,**kw:calls.append((operation,args,finished)))
        image=data_url(str(Path(ui.__file__).parent/'assets/icon.png'))
        fixtures=[dict(name='Picture.png',kind='image',image=image,role='identity',notes='Blue coat'),
                  dict(name='Motion.mp4',kind='video',path='motion.mp4',image=image,audio_path='motion.wav',role='motion',notes='Slow turn'),
                  dict(name='Voice.wav',kind='audio',path='voice.wav',audio_path='voice.wav',role='voice',notes='Soft voice')]
        assert not hasattr(w.minimax_panel,'generate_references_button')
        assert not hasattr(w.minimax_panel,'reassess_references_button')
        assert not hasattr(w.minimax_panel.reference_targets[0],'generate_button')
        # Real add-menu routes; burst imports and note edits become one job.
        for slot,kind in enumerate(('image','video','audio')):
            monkeypatch.setattr(w.minimax_panel.reference_targets[slot],'load_path',lambda path,slot=slot:w.set_minimax_reference_image(slot,fixtures[slot]))
            monkeypatch.setattr('ltx_prompt_director.file_dialogs.choose_document_open',lambda *a,kind=kind:kind)
            next(a for a in panel.add.menu().actions() if a.text()==kind.title()+' reference…').trigger()
        w.set_minimax_reference_image(0,{**fixtures[0],'notes':'Blue coat, final notes'})
        panel.refresh();assert not w.prompt_surface.reference_flag.isHidden()
        QTest.qWait(850)
        assert len(launched)==1 and credential_modes[-1]=={'interactive':False}
        assert not w.ai_busy and not w.intent.isReadOnly()
        selected=launched[-1][1][2]
        assert len(selected)==4 and {s['media_kind'] for s in selected}=={'image','video','audio'}
        # Changing a source while its job runs discards the older response.
        w.set_minimax_reference_image(0,{**fixtures[0],'notes':'Newer instructions'})
        panel.definition_finished({s['id']:'STALE' for s in selected})
        assert 'STALE' not in w.unified_prompt.toPlainText()
        panel.definition_timer.stop();panel.drain_definition_queue()
        selected=launched[-1][1][2]
        panel.definition_finished({s['id']:'Definition '+s['label'] for s in selected})
        assert not state['pending_reference_definitions']
        assert w.minimax_reference_images[1]['audio_description']=='Definition Audio1'
        assert 'Definition Audio2' in w.unified_prompt.toPlainText()
        assert panel.state()['global']=='Keep summary' and w.segments[0].prompt=='Keep timeline prose'
        assert not w.prompt_surface.reference_flag.isHidden()
        # Only the Unified flag reassesses all prompt surfaces.
        w.prompt_surface.reference_flag_button.click();operation,args,finish=calls[-1]
        assert operation is reassess_references
        finish(args[0])
        assert w.prompt_surface.reference_flag.isHidden()
        panel.definition_timer.stop()
        assert not panel.state()['references_need_reassessment']
        count=len(launched)
        w.minimax_panel.reference_targets[2].remove_button.click();panel.refresh()
        panel.definition_timer.stop();panel.drain_definition_queue()
        assert len(launched)==count and not w.prompt_surface.reference_flag.isHidden()
        payload=w.project_payload(include_media=False)
        w.load_project_payload(payload);panel.refresh()
        assert not w.prompt_surface.reference_flag.isHidden()
        w.prompt_surface.reference_flag_button.click();operation,args,finish=calls[-1]
        assert not any(item.get('label')=='Audio2' for item in args[1])
        finish(args[0]);assert w.prompt_surface.reference_flag.isHidden()
        # A failed background definition reports in the flag; Reassess retries
        # definitions first, then continues to reassess without another button.
        w.set_minimax_reference_image(2,fixtures[2]);panel.definition_timer.stop();panel.drain_definition_queue()
        panel.definition_failed('Temporary provider error')
        assert panel.state()['reference_definition_error'] and w.prompt_surface.reference_flag_button.isEnabled()
        count=len(calls);w.prompt_surface.reference_flag_button.click()
        panel.definition_timer.stop();panel.drain_definition_queue()
        selected=launched[-1][1][2]
        panel.definition_finished({s['id']:'Recovered '+s['label'] for s in selected})
        QTest.qWait(30)
        assert len(calls)==count+1
        calls[-1][2](calls[-1][1][0])
        assert w.prompt_surface.reference_flag.isHidden()
        # Automatic generation never overwrites a newer hand-edited definition.
        w.set_minimax_reference_image(0,{**w.minimax_reference_images[0],'notes':'Another change'})
        panel.definition_timer.stop();panel.drain_definition_queue()
        selected=launched[-1][1][2];identity=selected[0]['id']
        panel.rows[identity][0].setPlainText('Manual definition')
        panel.definition_finished({s['id']:'Old generated definition' for s in selected})
        assert panel.state()['subjects'][identity]=='Manual definition'
        assert not w.prompt_surface.reference_flag.isHidden()
        w.resize(1400,900);w.show();app.processEvents();panel.reveal_timer.stop();panel.scroll.verticalScrollBar().setValue(0)
        w.grab().save('/tmp/director-a143-reference-flag.png')
    finally:
        w.project_dirty=False;w._close_saves_queued=True;w.close()
