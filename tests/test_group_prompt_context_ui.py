"""Native Generate/Refine buttons bind read-only group context to the AI worker."""
from types import SimpleNamespace
from unittest.mock import patch
import copy,json,threading,time
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from ltx_prompt_director import ai
from ltx_prompt_director.ui import MainWindow
from ltx_prompt_director.grouped_sequence import GroupedSequence
from test_group_prompt_context import owner_fixture
APP=QApplication.instance() or QApplication([])


def wait_for(condition):
    for _ in range(2000):
        APP.processEvents()
        if condition():return
        time.sleep(.005)
    assert condition()


def test_native_generate_refine_and_stale_anchor_preserve_source_groups(tmp_path,monkeypatch):
    owner,groups,path=owner_fixture(tmp_path);requests=[];results=[];workers=[];gate=threading.Event();ready=threading.Event();pause=[False]
    settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat);settings.setValue('api_retries',0)
    monkeypatch.setattr(ai,'streaming_enabled',lambda:False)
    def post(url,**kwargs):
        from ltx_prompt_director.output_templates import ACTIVE_OUTPUT,root_fields
        requests.append(kwargs['json']);context=ACTIVE_OUTPUT.get()
        if context is None:value=dict(prompt='Observed initial state, followed by the authored motion.')
        else:value=dict(variables={name:'Observed initial state.' if name=='global_prompt' else '' for name in root_fields(context['layout'])},subjects=[dict(id=s['id'],prompt=s.get('prompt','')) for s in context['subjects']],segments=[dict(id=s['id'],prompt='Observed initial state, followed by the authored motion.') for s in context['segments']])
        if pause[0]:ready.set();gate.wait(5)
        text=json.dumps(value)
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:dict(output=[dict(type='message',content=[dict(type='output_text',text=text)])]))
    monkeypatch.setattr(ai.requests,'post',post)
    with patch('ltx_prompt_director.ui.QSettings',return_value=settings),patch.object(MainWindow,'restore_startup_workspace'),patch.object(GroupedSequence,'changed'):
        w=MainWindow();w._close_saves_queued=True
        try:
            w.set_project_type('minimax_frames');w.segments=[s for values in groups.values() for s in values];w.video_extensions=2
            w.render_group_options=copy.deepcopy(owner.render_group_options);w.requested_length.setValue(4)
            w.refresh_timeline(2);w.render_group_options['0']['context']=copy.deepcopy(owner.render_group_options['0']['context']);w.minimax_prompt_text='Current production draft.';w.show()
            w.ai_credentials=lambda:('openai','fixture','fixture')
            w.thread_pool=SimpleNamespace(start=workers.append)
            def complete(result):results.append(result);w.set_ai_controls_enabled(True)
            w.minimax_h3_finished=complete
            original=[s.to_dict() for s in w.segments];saved_refs=copy.deepcopy(w.render_group_options['0']['context'])
            for refine in (False,True):
                w.prompt_surface.select('unified')
                button=w.prompt_surface.actions['unified'][2 if refine else 1]
                before=len(results);button.click();assert workers
                w.ai_review_text=False
                thread=threading.Thread(target=workers.pop().run);thread.start()
                wait_for(lambda:len(results)>before or not w.ai_busy);thread.join(timeout=2)
                assert len(results)==before+1
                payload=json.dumps(requests[-1],ensure_ascii=False)
                assert 'Group 3 starting anchor' in payload and 'input_image' in payload and 'prompt text itself' in payload
                assert [s.to_dict() for s in w.segments]==original and w.render_group_options['0']['context']==saved_refs
            pause[0]=True;w.minimax_prompt_text='Current production draft.'
            w.prompt_surface.actions['unified'][2].click();w.ai_review_text=False
            thread=threading.Thread(target=workers.pop().run);thread.start();wait_for(ready.is_set)
            before=len(results);w.render_group_options['0']['attachmentRevision']=99;gate.set()
            wait_for(lambda:not w.ai_busy);thread.join(timeout=2)
            assert len(results)==before and 'anchor' in w.statusBar().currentMessage()
            assert w.render_group_options['0']['attachmentRevision']==99 and [s.to_dict() for s in w.segments]==original
        finally:
            gate.set();w.grouped_sequence.timer.stop();w.shot_sequence_panel.deactivate();w.render_queue.shutdown();w.close()
