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
from ltx_prompt_director.prompt_references import subjects, generate_definitions


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


def test_native_add_generate_remove_and_project_roundtrip(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    with patch.object(ui.MainWindow,'restore_startup_workspace'):
        w=ui.MainWindow()
    w.segments=[Segment('Opening','','','text','text','Keep timeline prose',2)]
    w.refresh_timeline(0);w.set_project_type('minimax_official_skill_reference')
    panel=w.prompt_surface.structured
    panel.refresh();state=panel.state()
    state.update(global_ignored='unused', global_source=None)
    state['global']='Keep summary';state['fields'].update(sound='Keep sound',music='Keep music',retention='Keep retention')
    panel.refresh()
    image=data_url(str(Path(ui.__file__).parent/'assets/icon.png'))
    fixtures={
        'image':dict(name='Picture.png',kind='image',image=image,role='identity',notes='Blue coat'),
        'video':dict(name='Motion.mp4',kind='video',path='motion.mp4',image=image,audio_path='motion.wav',role='motion',notes='Slow turn'),
        'audio':dict(name='Voice.wav',kind='audio',path='voice.wav',audio_path='voice.wav',role='voice',notes='Soft voice')}
    try:
        menu=panel.add.menu()
        for slot,kind in enumerate(fixtures):
            monkeypatch.setattr(w.minimax_panel.reference_targets[slot],'load_path',lambda path,slot=slot,kind=kind:w.set_minimax_reference_image(slot,fixtures[kind]))
            monkeypatch.setattr('ltx_prompt_director.file_dialogs.choose_document_open',lambda *a,kind=kind:kind)
            next(a for a in menu.actions() if a.text()==kind.title()+' reference…').trigger()
        panel.refresh()
        assert {s['media_kind'] for s in panel.subjects()}=={'image','video','audio'}
        assert len(panel.subjects())==4 # video and its paired soundtrack
        before=copy.deepcopy(w.project_payload(include_media=False)['frames'])
        monkeypatch.setattr(w,'ai_credentials',lambda:('gemini','model','key'))
        calls=[]
        monkeypatch.setattr(w,'start_ai_worker',lambda operation,args,activity,finished,**kwargs:calls.append((args,finished)))
        w.minimax_panel.reference_targets[1].generate_button.click()
        selected=calls[-1][0][2]
        assert len(selected)==2 and {s['media_kind'] for s in selected}=={'video','audio'}
        calls[-1][1]({s['id']:'Generated '+s['label'] for s in selected})
        assert 'Generated Video1' in w.unified_prompt.toPlainText() and 'Generated Audio1' in w.unified_prompt.toPlainText()
        assert panel.state()['global']=='Keep summary' and panel.state()['fields']['retention']=='Keep retention'
        assert w.project_payload(include_media=False)['frames']==before
        assert w.minimax_panel.reference_targets[1].generate_button.text()=='Regenerate'
        w.minimax_panel.generate_references_button.click()
        selected=calls[-1][0][2]
        calls[-1][1]({s['id']:'All '+s['label'] for s in selected})
        payload=w.project_payload(include_media=False)
        w.load_project_payload(payload);panel.refresh()
        assert 'All Image1' in w.unified_prompt.toPlainText() and 'All Audio2' in w.unified_prompt.toPlainText()
        # A result arriving after replacement cannot overwrite the newer reference.
        panel.generate_references(0);args,finish=calls[-1]
        w.set_minimax_reference_image(0,{**fixtures['image'],'notes':'Changed since request'})
        finish({s['id']:'STALE' for s in args[2]})
        assert 'STALE' not in w.unified_prompt.toPlainText()
        # Explicit Remove in Reference Media clears paired definitions too.
        w.minimax_panel.reference_targets[1].remove_button.click();panel.refresh()
        assert w.minimax_reference_images[1] is None and 'All Video1' not in w.unified_prompt.toPlainText()
        panel.add_subject();panel.refresh()
        subject=next(s for s in panel.subjects() if s['id'].startswith('subject:') and not s.get('timeline_id'))
        next(b for b in panel.rows[subject['id']][0].parentWidget().findChildren(QPushButton) if b.text()=='×').click()
        panel.refresh();assert subject['id'] not in panel.rows
        panel.add_segment();panel.refresh()
        added=w.segments[-1]
        next(b for b in panel.rows[added.id][0].parentWidget().findChildren(QPushButton) if b.text()=='×').click()
        assert added not in w.segments
        assert panel.state()['fields']['sound']=='Keep sound' and panel.state()['fields']['music']=='Keep music'
        w.resize(1400,900);w.show();app.processEvents();panel.reveal_timer.stop();panel.scroll.verticalScrollBar().setValue(0)
        w.grab().save('/tmp/director-a139-references.png')
    finally:
        w.project_dirty=False;w._close_saves_queued=True;w.close()
