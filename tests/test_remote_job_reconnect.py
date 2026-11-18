"""A killed client and a reopened UI reconnect to one retained host render."""
import copy,json,os,subprocess,sys,threading,time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
import pytest
from PySide6.QtTest import QTest
from ltx_prompt_director import local_generation,ui
from ltx_prompt_director.network_render import NetworkRenderController,public_profile,run_remote_request,REMOTE_JOB_RECORD
from ltx_prompt_director.remote_jobs import FEATURE,read_receipt
from test_network_render import Window,loopback_start,wait_for,APP


@pytest.mark.parametrize('kind',['video','image'])
def test_killed_client_finished_offline_and_reconnect_returns_same_job(tmp_path,monkeypatch,kind):
    monkeypatch.setattr(NetworkRenderController,'start',loopback_start)
    profile=local_generation.default_profile();profile.update(video_model=str(tmp_path/'minimax_fl2va_pruned.safetensors'),video_variant='fl2va',image_model='server-flux.safetensors')
    Path(profile['video_model']).touch()
    monkeypatch.setattr(local_generation,'load_profile',lambda settings:dict(profile))
    monkeypatch.setattr(local_generation,'validate_profile',lambda *args:None)
    host=Window();host.network_render=NetworkRenderController(host)
    gate=threading.Event();seen=[]
    output=tmp_path/('finished.mp4' if kind=='video' else 'finished.png');output.write_bytes(b'finished while client offline')
    preview=tmp_path/('live.rgb' if kind=='video' else 'live.png');preview.write_bytes(bytes((30,180,90))*24)
    def inference(request,cancel):
        seen.append(request)
        event=local_generation.LOCAL_PROGRESS.get()
        event(dict(kind='preview',media='latent_frames' if kind=='video' else 'image',path=str(preview),width=6,height=4,latent_frames=1,frames=22,fps=24))
        event(dict(kind='progress',percent=35,eta_seconds=120,message='Sampling'))
        while not gate.wait(.01):
            if cancel.is_set():raise InterruptedError('Cancelled')
        return dict(path=str(output),media=kind)
    monkeypatch.setattr(host.network_render,'_run_host_request',inference)
    target=dict(name='Host PC',url=f'ws://127.0.0.1:{host.network_render.server.socket.getsockname()[1]}',token=host.network_render.token,profile=public_profile(profile),features=[FEATURE])
    request_id=uuid4().hex
    record=dict(id=uuid4().hex,project_id='fixture',portable={'remote_request_id':request_id})
    request=dict(kind=kind,prompt='Keep the authored prompt',profile=profile,duration=4,width=256,height=256)
    code='''import json,sys
from ltx_prompt_director.network_render import run_remote_request,REMOTE_JOB_RECORD
value=json.load(sys.stdin)
REMOTE_JOB_RECORD.set(value['record'])
run_remote_request(value['request'],value['target'],lambda event:None,None,60)
'''
    env={**os.environ,'PYTHONPATH':str(Path(ui.__file__).parent.parent)}
    child=subprocess.Popen([sys.executable,'-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
    child.stdin.write(json.dumps(dict(record=record,request=request,target=target)));child.stdin.close()
    results=[];events=[];errors=[];cancel=threading.Event()
    try:
        wait_for(lambda:len(seen)==1 and (read_receipt(request_id) or {}).get('status')=='submitted')
        original=host.render_queue.jobs[0]
        child.kill();child.wait(timeout=3);QTest.qWait(200)
        assert original.state=='Rendering' and not original.cancel.is_set()
        gate.set();wait_for(lambda:original.state=='Complete')
        assert not host.network_render.detached[request_id].acknowledged
        original_stream=host.network_render._stream_detached;dropped=[]
        def drop_once(connection,mailbox):
            if not dropped:
                dropped.append(True);connection.close();return
            return original_stream(connection,mailbox)
        monkeypatch.setattr(host.network_render,'_stream_detached',drop_once)
        def reconnect():
            token=REMOTE_JOB_RECORD.set(record)
            try:results.append(run_remote_request(request,target,events.append,cancel,20))
            except Exception as error:errors.append(error)
            finally:REMOTE_JOB_RECORD.reset(token)
        thread=threading.Thread(target=reconnect,daemon=True);thread.start()
        wait_for(lambda:results or errors)
        assert not errors and Path(results[0]['path']).read_bytes()==output.read_bytes()
        assert len(seen)==len(host.render_queue.jobs)==1 and host.render_queue.jobs[0] is original
        assert any('Reconnecting' in event.get('message','') for event in events)
        assert any(event.get('kind')=='preview' for event in events)
        assert any(event.get('eta_seconds')==120 for event in events)
        # Explicit Cancel still stops a fresh host job.
        gate.clear();cancel2=threading.Event();cancel_errors=[]
        def cancelled_client():
            try:run_remote_request(request,target,None,cancel2,20)
            except Exception as error:cancel_errors.append(error)
        threading.Thread(target=cancelled_client,daemon=True).start()
        wait_for(lambda:len(seen)==2);cancel2.set()
        wait_for(lambda:host.render_queue.jobs[-1].state=='Cancelled' and cancel_errors)
        assert isinstance(cancel_errors[0],InterruptedError)
    finally:
        gate.set();cancel.set()
        if child.poll() is None:child.kill();child.wait(timeout=3)
        host.network_render.close();host.render_queue.shutdown();host.close()


@pytest.mark.parametrize('recovery',['receipt','snapshot','manual'])
def test_reopened_client_auto_attaches_host_without_starting_pending_work(tmp_path,monkeypatch,recovery):
    from ltx_prompt_director.models import Segment
    import av,numpy as np
    from shiboken6 import isValid
    monkeypatch.setattr(NetworkRenderController,'start',loopback_start)
    profile=local_generation.default_profile();profile.update(video_model=str(tmp_path/'minimax_fl2va_pruned.safetensors'),video_variant='fl2va',video_duration=4,video_steps=4)
    Path(profile['video_model']).touch()
    monkeypatch.setattr(local_generation,'load_profile',lambda settings:dict(profile))
    monkeypatch.setattr(local_generation,'validate_profile',lambda *args:None)
    monkeypatch.setattr(ui,'load_profile',lambda settings:dict(profile));monkeypatch.setattr(ui,'validate_profile',lambda *args:None)
    monkeypatch.setattr(ui,'project_library_path',lambda:tmp_path/'projects');(tmp_path/'projects').mkdir()
    output=tmp_path/'complete.mp4'
    with av.open(str(output),'w') as container:
        stream=container.add_stream('libx264',rate=24);stream.width=128;stream.height=96;stream.pix_fmt='yuv420p'
        for index in range(24):
            for packet in stream.encode(av.VideoFrame.from_ndarray(np.full((96,128,3),90,np.uint8),format='rgb24')):container.mux(packet)
        for packet in stream.encode():container.mux(packet)
    host=Window();host.network_render=NetworkRenderController(host)
    gate=threading.Event();seen=[]
    def inference(request,cancel):
        seen.append(request);event=local_generation.LOCAL_PROGRESS.get();step=0
        while not gate.wait(.05):
            if cancel.is_set():raise InterruptedError('Cancelled')
            step+=1;bank=tmp_path/f'preview-{step}.rgb';bank.write_bytes(bytes((80,150,220))*6*4)
            event(dict(kind='preview',media='latent_frames',path=str(bank),width=6,height=4,latent_frames=1,frames=22,fps=24))
            event(dict(kind='progress',percent=35,eta_seconds=120,message='Sampling'))
        return dict(path=str(output),media='video')
    monkeypatch.setattr(host.network_render,'_run_host_request',inference)
    peer=dict(id='host',name='Host PC',url=f'ws://127.0.0.1:{host.network_render.server.socket.getsockname()[1]}',token=host.network_render.token,profile=public_profile(profile),features=[FEATURE])
    with patch.object(ui.MainWindow,'restore_startup_workspace'):
        first=ui.MainWindow()
    first._close_saves_queued=True;second=None
    try:
        first.new_project();first.set_project_type('minimax_frames')
        first.segments=[Segment('Head','','','text','text','Keep this authored prompt',4)]
        first.requested_length.setValue(4);first.minimax_prompt_text='A continuous shot.'
        first.refresh_timeline();first.mark_dirty();first.save_library_project(automatic=True)
        wait_for(lambda:not first._pending_disk_jobs)
        ident=first.current_project_id
        first.network_render._peer(peer);first.network_render.combo.setCurrentIndex(1)
        first.local_video_generate.click();wait_for(lambda:len(seen)==1 and first.project_preview_panel.latent_player.active)
        queued=first.render_queue.active;request_id=queued.portable['remote_request_id']
        wait_for(lambda:(read_receipt(request_id) or {}).get('status')=='submitted')
        original=host.render_queue.jobs[0]
        first.queue_store.freeze();wait_for(lambda:not first.queue_store.writing)
        first.close();wait_for(lambda:not first._pending_disk_jobs)
        QTest.qWait(150)
        assert original.state=='Rendering' and not original.cancel.is_set()
        from uuid import UUID
        UUID(request_id)
        if recovery=='receipt':
            # Simulate a crash before the queue's next scheduled snapshot.
            (tmp_path/'projects/render-queue.json').unlink(missing_ok=True)
        else:
            # Clean close/restart must recover from the snapshot alone, with
            # no receipt and no ability to rebuild the host's resolved inputs.
            from ltx_prompt_director.remote_jobs import receipt_directory
            (receipt_directory()/(request_id+'.json')).unlink()
            path=tmp_path/'projects/render-queue.json'
            snapshot=json.loads(path.read_text())
            snapshot['jobs'][0]['portable']['continuation']=dict(path=str(tmp_path/'unavailable.mp4'),duration=4,overlap=22/24)
            path.write_text(json.dumps(snapshot))
        with patch.object(ui.MainWindow,'restore_startup_workspace'):
            second=ui.MainWindow();second._close_saves_queued=True
        wait_for(lambda:not second.queue_store.restoring)
        assert len(second.render_queue.jobs)==1
        pending=second.render_queue.enqueue(lambda:seen.append('unexpected pending render'),(),lambda result:None,lambda error:None,title='Never submitted',source='network',kind='Video')
        second.open_render_project(ident);wait_for(lambda:second.current_project_id==ident)
        reconnect_peer=dict(peer,name='Renamed host',features=[])
        # Another Director has the same display name; only the GUID owner
        # may be resumed. The unrelated peer is deliberately tried first.
        wrong=dict(reconnect_peer,id='unrelated',token=second.network_render.token,
                   url=f'ws://127.0.0.1:{second.network_render.server.socket.getsockname()[1]}')
        second.network_render._peer(wrong);second.network_render._peer(reconnect_peer)
        if recovery=='manual':
            second.queue_store.remote_timer.stop()
            # Exercise the user's actual Resume queue control before automatic
            # reattachment. Persisted credentials are deliberately unavailable.
            second.render_queue_panel.pause.click()
        else:second.queue_store.check_remote()
        wait_for(lambda:second.render_queue.active is not None and second.project_preview_panel.latent_player.active)
        assert second.render_queue.paused==(recovery!='manual') and len(seen)==1
        assert second.render_queue.active.portable['remote_request_id']==request_id
        if recovery=='manual':second.render_queue.set_paused(True)
        gate.set();wait_for(lambda:second.render_queue.jobs[0].state=='Complete')
        wait_for(lambda:second.render_group_options.get('0',{}).get('clip'))
        assert len(seen)==1 and len(host.render_queue.jobs)==1
        assert Path(second.render_queue.jobs[0].output_path).is_file()
        if recovery!='manual':assert pending.state=='Waiting' and second.render_queue.paused
    finally:
        gate.set()
        for window in (second,first):
            if window is not None and isValid(window):
                window._close_saves_queued=True;window.close()
        host.network_render.close();host.render_queue.shutdown();host.close()
