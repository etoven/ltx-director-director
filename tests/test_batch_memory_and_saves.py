import copy,json,sys,time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from PySide6.QtWidgets import QApplication
from ltx_prompt_director import local_generation,local_memory,ui
from ltx_prompt_director.models import Segment
from ltx_prompt_director.local_session import WorkerError,ModelSession

APP=QApplication.instance() or QApplication([])


def wait_for(test,seconds=12):
    until=time.monotonic()+seconds
    while not test() and time.monotonic()<until:APP.processEvents();time.sleep(.01)
    assert test()


def test_oom_retry_fresh_worker_same_seed_and_bounded(monkeypatch):
    from ltx_prompt_director import local_session
    monkeypatch.setattr(local_session,'MANAGER',ModelSession())
    seen=[]
    def render(request,*args):
        seen.append(copy.deepcopy(request))
        if len(seen)%2:raise WorkerError('CUDA out of memory',memory_pressure=True)
        return {'path':'done.mp4'}
    monkeypatch.setattr(local_generation,'_run_request',render)
    original=dict(kind='video',seed=-1,profile={'offload':False})
    assert local_generation.run_request(original)['path']=='done.mp4'
    assert len(seen)==2 and seen[0]['seed']==seen[1]['seed']>=0
    assert seen[1]['profile']['offload'] is True and original['seed']==-1
    monkeypatch.setattr(local_generation,'_run_request',lambda *args:(_ for _ in ()).throw(WorkerError('OOM',memory_pressure=True)))
    with pytest.raises(WorkerError):local_generation.run_request(original)


def test_budget_is_absolute_and_updates_for_reused_pipeline(monkeypatch):
    calls=[];gib=1024**3
    cuda=SimpleNamespace(is_available=lambda:True,mem_get_info=lambda:(2*gib,12*gib),
        memory_allocated=lambda:3*gib,memory_reserved=lambda:4*gib,max_memory_allocated=lambda:5*gib,
        reset_peak_memory_stats=lambda:None,empty_cache=lambda:calls.append('empty'))
    monkeypatch.setitem(sys.modules,'torch',SimpleNamespace(cuda=cuda))
    wrapper=SimpleNamespace(vram_limit=99)
    pipe=SimpleNamespace(vram_management_enabled=True,load_models_to_device=lambda names:calls.append(names),modules=lambda:[wrapper])
    request=dict(kind='video',width=256,height=256,duration=4,profile={'device':'cuda'})
    local_memory.prepare(pipe,request)
    first=wrapper.vram_limit
    request.update(width=1280,height=704,duration=15)
    local_memory.prepare(pipe,request)
    assert wrapper.vram_limit<first<=8 and [] in calls
    local_memory.finish(pipe)
    assert calls.count('empty')>=3


def test_native_new_name_save_all_reopen_and_empty_close(tmp_path,monkeypatch):
    from ltx_prompt_director import ai,response_schemas
    from ltx_prompt_director.project_saves import payload_from_state,name_origin
    from ltx_prompt_director.project_archive import read_project
    monkeypatch.setattr(ui,'project_library_path',lambda:tmp_path)
    meta=dict(id='large',name='Large seed',projectPath=str(tmp_path/'large.LTXD'),videoRenderCache={'0':{'result':{'seed':18446744073709551615}}})
    ui._write_library_archive(meta['projectPath'],{'frames':[]},[],tmp_path/'large.meta.json',meta)
    with patch.object(ui.MainWindow,'restore_startup_workspace'):
        w=ui.MainWindow();w._close_saves_queued=True
    try:
        records=[w.project_list.item(i).data(256) for i in range(w.project_list.count())]
        assert any(record.get('videoRenderCache',{}).get('0',{}).get('result',{}).get('seed')==18446744073709551615 for record in records)
        w.new_project();first=w.current_project_id
        assert first and (tmp_path/f'{first}.LTXD').is_file() and not w.project_dirty
        assert name_origin(w.current_library_metadata())=='placeholder'
        assert any(action.text()=='Save All' and not action.icon().isNull() for action in w.findChildren(__import__('PySide6.QtGui',fromlist=['QAction']).QAction))
        w.segments=[Segment('First','','',kind='text',prompt='A lighthouse at dusk',duration=4)]
        w.intent.setPlainText('A lighthouse at dusk');w.mark_dirty()
        baseline=copy.deepcopy(w.capture_workspace_state())
        assert payload_from_state(baseline)['frames']==w.project_payload(False)['frames']
        def generate_prompt():
            with response_schemas.using(response_schemas.PROMPT):
                return json.loads(ai._provider_raw([], 'gemini','fixture','', 'Generate',5))['prompt']
        def provider(*args):
            response=json.dumps(dict(prompt='A lighthouse beams through fog.',projectName='Lighthouse Through Fog'))
            response_schemas.validate(response);return response
        monkeypatch.setattr(ai,'_provider_raw_impl',provider)
        def ready(value):w.global_prompt.setPlainText(value);w.set_ai_controls_enabled(True)
        w.start_ai_worker(generate_prompt,(), 'Generate',ready,review_text=False)
        wait_for(lambda:w.current_project_name=='Lighthouse Through Fog' and w._pending_disk_jobs==0)
        assert w.current_project_id==first
        assert json.loads((tmp_path/f'{first}.meta.json').read_text())['nameOrigin']=='ai'
        w.intent.setPlainText('First edited');w.mark_dirty()
        w.new_project();second=w.current_project_id
        w.intent.setPlainText('Second edited');w.mark_dirty()
        active=w.current_project_id
        w.save_all_projects();w.save_all_projects()
        w.intent.setPlainText('Second edited during save');w.mark_dirty()
        wait_for(lambda:not w._pending_disk_jobs)
        assert w.current_project_id==active==second and not w._save_all_pending and w.project_dirty
        assert read_project(str(tmp_path/f'{first}.LTXD'))['directorIntent']=='First edited'
        assert read_project(str(tmp_path/f'{second}.LTXD'))['directorIntent']=='Second edited'
        meta=w.current_library_metadata();meta['name']='Hand Chosen';w.persist_library_metadata(meta)
        w.current_project_name='Hand Chosen'
        w.finish_project_naming(dict(project_id=second,revision=0,title='Stale AI Name',baseline=baseline))
        assert w.current_project_name=='Hand Chosen'
        wait_for(lambda:not w._pending_disk_jobs)
        w.show();APP.processEvents();w._close_saves_queued=False;w.project_dirty=False
        for session in w.project_sessions.values():session['dirty']=False
        w.close();APP.processEvents()
        wait_for(lambda:not w._pending_disk_jobs and not w._closing_progress,3)
    finally:
        from shiboken6 import isValid
        if isValid(w):w._close_saves_queued=True;w.render_queue.shutdown();w.close()


def test_render_again_copies_clicked_batch_with_fresh_cancel_and_dependencies():
    from ltx_prompt_director.render_queue import RenderQueue
    runs=[]
    queue=RenderQueue(lambda op,args,ready,failed,**kwargs:runs.append((op,args,ready,failed)))
    queue.set_paused(True)
    first=queue.enqueue(lambda:{'path':'first.mp4'},(),lambda result:None,lambda error:None,
        title='First',source='network',kind='Video',group_id='batch',group_title='Original batch')
    second=queue.enqueue(lambda:{'path':'second.mp4'},(),lambda result:None,lambda error:None,
        title='Second',source='network',kind='Video',group_id='batch',group_title='Original batch',dependencies=(first.id,),
        replay_factory=lambda cancel,mapping:lambda:{'path':mapping.get(first.id,first).output_path})
    try:
        queue.set_paused(False);APP.processEvents()
        runs[0][2]({'path':'original-first.mp4'});APP.processEvents()
        runs[1][2]({'path':'original-second.mp4'});APP.processEvents()
        copied=queue.render_again('batch');APP.processEvents()
        assert len(copied)==2 and copied[0].cancel is not first.cancel
        assert copied[0].force_render and copied[0].group_id!=first.group_id
        assert copied[1].dependencies==(copied[0].id,)
        runs[2][2]({'path':'fresh-first.mp4'});APP.processEvents()
        assert runs[3][0]() == {'path':'fresh-first.mp4'}
        runs[3][2]({'path':'fresh-second.mp4'});APP.processEvents()
        single=queue.render_again(second.id)
        assert len(single)==1 and single[0].dependencies==(first.id,)
    finally:queue.shutdown()


def test_name_metadata_failure_does_not_discard_prompt(monkeypatch):
    from ltx_prompt_director import ai,response_schemas
    from ltx_prompt_director.project_naming import ACTIVE_NAME
    from ltx_prompt_director.project_saves import name_origin
    naming={'context':'Whole project context'}
    token=ACTIVE_NAME.set(naming)
    try:
        for title in (None,42,''):
            def provider(*args):
                raw=json.dumps(dict(prompt='Keep this valid prompt',projectName=title))
                response_schemas.validate(raw);return raw
            monkeypatch.setattr(ai,'_provider_raw_impl',provider)
            with response_schemas.using(response_schemas.PROMPT):
                assert json.loads(ai._provider_raw([],'gemini','fixture','','Generate',5))=={'prompt':'Keep this valid prompt'}
            assert 'title' not in naming
        assert name_origin({'name':'Untitled project (2)'})=='placeholder'
        assert name_origin({'name':'My Scene'})=='manual'
        assert name_origin({'name':'Untitled project','nameOrigin':'manual'})=='manual'
    finally:ACTIVE_NAME.reset(token)
