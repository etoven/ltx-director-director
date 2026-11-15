"""Real WebSocket transport, native destination controls, fixture model outputs."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import threading
import time
import pytest
from PySide6.QtCore import QObject,Signal,QSettings
from PySide6.QtWidgets import QApplication,QMainWindow,QWidget,QVBoxLayout,QStatusBar,QDockWidget
from PySide6.QtTest import QTest
from ltx_prompt_director.network_render import NetworkRenderController,run_remote_request,public_profile
from ltx_prompt_director.render_queue import RenderQueue
from ltx_prompt_director.render_widgets import RenderQueuePanel

APP=QApplication.instance() or QApplication([])


def wait_for(condition):
    for _ in range(1200):
        APP.processEvents()
        if condition():return
        time.sleep(.005)
    assert condition()


class Task(QObject):
    result=Signal(object);failure=Signal(str);progress=Signal(object)


class Window(QMainWindow):
    def __init__(self):
        super().__init__();self.settings=QSettings();self.tasks=[]
        self.setStatusBar(QStatusBar());self.project_preview_panel=QWidget();QVBoxLayout(self.project_preview_panel);self.setCentralWidget(self.project_preview_panel)
        self.render_queue=RenderQueue(self.execute,self)
        self.render_queue_dock=QDockWidget();self.render_queue_panel=RenderQueuePanel(self.render_queue);self.render_queue_dock.setWidget(self.render_queue_panel)
        self.update_render_controls=lambda:None
    def execute(self,operation,args,ready,failed,progress,cancel):
        task=Task(self);self.tasks.append(task);task.result.connect(ready);task.failure.connect(failed);task.progress.connect(progress)
        def run():
            from ltx_prompt_director.local_generation import LOCAL_PROGRESS,LOCAL_CANCEL
            a=LOCAL_PROGRESS.set(task.progress.emit);b=LOCAL_CANCEL.set(cancel)
            try:task.result.emit(operation(*args))
            except Exception as error:task.failure.emit(str(error))
            finally:LOCAL_PROGRESS.reset(a);LOCAL_CANCEL.reset(b)
        threading.Thread(target=run,daemon=True).start()


def loopback_start(controller):
    from websockets.sync.server import serve
    controller.server=serve(controller._connection,'127.0.0.1',0,max_size=512*1024)
    threading.Thread(target=controller.server.serve_forever,daemon=True).start()


@pytest.mark.parametrize('kind',['video','image'])
def test_remote_video_and_flux_transfer_conditioning_live_frames_eta_and_result(tmp_path,monkeypatch,kind):
    from ltx_prompt_director import local_generation
    monkeypatch.setattr(NetworkRenderController,'start',loopback_start)
    model=tmp_path/'minimax_ref2va_pruned.safetensors';model.touch();model.with_name('minimax_fl2va_pruned.safetensors').touch()
    profile=dict(video_model=str(model),video_variant='ref2va',image_model='server-flux.safetensors',hf_token='private',device='cuda')
    monkeypatch.setattr(local_generation,'load_profile',lambda settings:dict(profile))
    monkeypatch.setattr(local_generation,'validate_profile',lambda profile,kind:None)
    gate=threading.Event();seen=[];events=[];result=[];errors=[]
    def inference(request,on_event,cancel):
        seen.append(request)
        preview=tmp_path/('preview.rgb' if kind=='video' else 'preview.png');preview.write_bytes(bytes([35,180,90])*24)
        on_event(dict(kind='preview',media='latent_frames' if kind=='video' else 'image',path=str(preview),width=6,height=4,frames=22,latent_frames=1,fps=24))
        on_event(dict(kind='progress',percent=25,eta_seconds=120,message='Sampling'))
        while not gate.wait(.01):
            if cancel.is_set():raise InterruptedError('Cancelled')
        output=tmp_path/('output.mp4' if kind=='video' else 'output.png');output.write_bytes(b'fixture output '+kind.encode())
        return dict(path=str(output),media=kind)
    monkeypatch.setattr(local_generation,'run_request',inference)
    owner=Window();controller=NetworkRenderController(owner);owner.show()
    target=dict(id='peer',name='Etoven-PC2',url=f'ws://127.0.0.1:{controller.server.socket.getsockname()[1]}',token=controller.token,profile=public_profile(profile))
    from zeroconf import ServiceInfo
    from ltx_prompt_director.network_render import SERVICE
    import socket
    target['id']='fixture-peer.'+SERVICE
    info=ServiceInfo(SERVICE,target['id'],addresses=[socket.inet_aton('127.0.0.1')],port=controller.server.socket.getsockname()[1],properties={'protocol':'1','name':target['name'],'token':target['token']})
    controller._discover(SimpleNamespace(get_service_info=lambda *args,**kwargs:info),SERVICE,target['id'])
    wait_for(lambda:controller.combo.count()==2)
    controller.combo.setCurrentIndex(1)
    target=controller.target
    assert controller.target['name']=='Etoven-PC2' and controller.combo.currentText()=='Generate on Etoven-PC2'
    source=tmp_path/'conditioning.mp4';source.write_bytes(b'preceding video')
    audio=tmp_path/'conditioning.wav';audio.write_bytes(b'aligned audio')
    request=dict(kind=kind,prompt='Keep authored direction.',profile=dict(video_variant='fl2va',video_steps=4),conditioning=[dict(path=str(source),audio_path=str(audio),duration=22/24,timeline_time=0)])
    def client():
        try:result.append(run_remote_request(request,dict(target),events.append,threading.Event(),30))
        except Exception as error:errors.append(error)
    thread=threading.Thread(target=client,daemon=True);thread.start()
    try:
        wait_for(lambda:any(event.get('percent')==25 for event in events) or errors)
        assert not errors and not result and len(seen)==1
        assert seen[0]['prompt']==request['prompt']
        assert Path(seen[0]['conditioning'][0]['path']).read_bytes()==b'preceding video'
        assert Path(seen[0]['conditioning'][0]['audio_path']).read_bytes()==b'aligned audio'
        assert seen[0]['conditioning'][0]['timeline_time']==0 and seen[0]['conditioning'][0]['duration']==22/24
        assert 'hf_token' not in target['profile']
        preview=next(event for event in events if event.get('kind')=='preview')
        assert Path(preview['path']).read_bytes()==bytes([35,180,90])*24
        assert owner.render_queue.active.percent==25 and owner.render_queue.active.eta_seconds==120
        if kind=='video':assert seen[0]['profile']['video_variant']=='fl2va'
        # Changing the UI destination cannot retarget an already submitted job.
        controller.combo.setCurrentIndex(0);gate.set()
        wait_for(lambda:result or errors);assert not errors
        assert Path(result[0]['path']).read_bytes()==b'fixture output '+kind.encode()
        assert Path(result[0]['path'])!=tmp_path/('output.mp4' if kind=='video' else 'output.png')
        owner.grab().save(str(tmp_path/('network-'+kind+'.png')))
    finally:
        gate.set();controller.close();owner.render_queue.shutdown();owner.close()


def test_remote_mode_profile_and_inline_assets(tmp_path):
    from ltx_prompt_director.network_render import remote_profile,map_files,restore_files
    source=tmp_path/'minimax_fl2va_pruned.safetensors';source.touch()
    reference=tmp_path/'minimax_ref2va_pruned.safetensors';reference.touch()
    assert remote_profile(dict(video_model=str(source)),'video','ref2va')['video_model']==str(reference)
    with pytest.raises(ValueError):remote_profile(dict(video_model=str(source)),'video','unsupported')
    files={};encoded=map_files(dict(image='data:image/png;base64,aW1hZ2U=',hf_token='secret'),files,tmp_path)
    assert 'hf_token' not in encoded and Path(restore_files(encoded,files)['image']).read_bytes()==b'image'


def test_native_client_routes_video_and_flux_and_attaches_results(tmp_path,monkeypatch):
    import av
    import numpy as np
    from PySide6.QtGui import QImage,QColor
    from ltx_prompt_director import local_generation,ui
    from ltx_prompt_director.models import Segment
    monkeypatch.setattr(NetworkRenderController,'start',loopback_start)
    profile=local_generation.default_profile()
    profile.update(video_model=str(tmp_path/'minimax_fl2va_pruned.safetensors'),video_variant='fl2va',video_duration=4,video_steps=4,image_model='flux.safetensors')
    monkeypatch.setattr(local_generation,'load_profile',lambda settings:dict(profile))
    monkeypatch.setattr(local_generation,'validate_profile',lambda profile,kind:None)
    host=Window();host.network_render=NetworkRenderController(host)
    video=tmp_path/'generated.mp4'
    with av.open(str(video),'w') as output:
        stream=output.add_stream('libx264',rate=24);stream.width=128;stream.height=96;stream.pix_fmt='yuv420p'
        for i in range(24):
            frame=av.VideoFrame.from_ndarray(np.full((96,128,3),100,np.uint8),format='rgb24')
            for packet in stream.encode(frame):output.mux(packet)
        for packet in stream.encode():output.mux(packet)
    png=tmp_path/'generated.png';image=QImage(128,96,QImage.Format.Format_RGB888);image.fill(QColor('#70a040'));image.save(str(png))
    seen=[];gate=threading.Event()
    def inference(request,cancel):
        seen.append(request)
        progress=local_generation.LOCAL_PROGRESS.get()
        if request['kind']=='video':
            bank=tmp_path/'frames.rgb';bank.write_bytes(bytes((80,160,40))*48)
            progress(dict(kind='preview',media='latent_frames',path=str(bank),width=6,height=4,latent_frames=2,frames=22,fps=24))
        else:progress(dict(kind='preview',media='image',path=str(png)))
        progress(dict(kind='progress',percent=50,eta_seconds=30,message='Sampling remotely'))
        while not gate.wait(.01):
            if cancel.is_set():raise InterruptedError('Cancelled')
        return dict(path=str(video if request['kind']=='video' else png),media=request['kind'])
    monkeypatch.setattr(host.network_render,'_run_host_request',inference)
    with patch.object(ui.MainWindow,'restore_startup_workspace'):
        w=ui.MainWindow();w._close_saves_queued=True
        try:
            w.set_project_type('minimax_frames');w.minimax_prompt_text='Preserve this production direction.'
            w.segments=[Segment('First','','','text','text','Authored segment wording.',4)]
            w.requested_length.setValue(4);w.refresh_timeline();w.save_library_project(automatic=True);w.resize(1400,950);w.show()
            peer=dict(id='native-host',name='Etoven-PC2',url=f'ws://127.0.0.1:{host.network_render.server.socket.getsockname()[1]}',token=host.network_render.token,profile=public_profile(profile))
            w.network_render._peer(peer);w.network_render.combo.setCurrentIndex(1)
            w.local_video_generate.click()
            wait_for(lambda:w.project_preview_panel.latent_player.active)
            job=w.render_queue.active
            assert job.source=='network' and job.remote_target['name']=='Etoven-PC2' and job.mode=='fl2va'
            assert w.project_preview_panel._rendering and not w.project_preview_panel.play.isVisible()
            wait_for(lambda:job.percent==50)
            w.network_render.combo.setCurrentIndex(0);gate.set();wait_for(lambda:job.state=='Complete')
            wait_for(lambda:w.render_group_options.get('0',{}).get('clip',{}).get('path'))
            attachment=w.render_group_options['0']['clip']['path']
            assert Path(attachment).is_file() and Path(attachment)!=video
            assert w.segments[0].prompt=='Authored segment wording.'
            # Local Flux artwork uses the same selected remote host and previews.
            gate.clear();w.network_render.combo.setCurrentIndex(1)
            segment=w.segments[0];segment.artwork_prompt='A green forest image.'
            from ltx_prompt_director.artwork_tools import ArtworkTool
            tool=ArtworkTool('local','flux2','',profile)
            w._start_segment_artwork(segment.id,dict(artworkPrompt=segment.artwork_prompt),tool)
            wait_for(lambda:not w.image_generation_panel.pixmap.isNull())
            image_job=w.render_queue.active;assert image_job.source=='network' and image_job.kind=='Image'
            gate.set();wait_for(lambda:image_job.state=='Complete')
            assert segment.kind=='image' and Path(segment.media_path).is_file()
            assert segment.prompt=='Authored segment wording.' and [request['kind'] for request in seen]==['video','image']
            w.project_preview_dock.show();w.project_preview_dock.raise_();APP.processEvents()
            w.project_preview_panel.grab().save(str(tmp_path/'network-native.png'))
        finally:
            gate.set();w.grouped_sequence.timer.stop();w.shot_sequence_panel.deactivate();w.render_queue.shutdown();w.close()
            host.network_render.close();host.render_queue.shutdown();host.close()


def test_remote_pending_cancellation_and_session_auth(tmp_path,monkeypatch):
    from ltx_prompt_director import local_generation
    from websockets.sync.client import connect
    from ltx_prompt_director.network_render import json_message,receive_json
    monkeypatch.setattr(NetworkRenderController,'start',loopback_start)
    monkeypatch.setattr(local_generation,'load_profile',lambda settings:dict(image_model='host-flux.safetensors'))
    monkeypatch.setattr(local_generation,'validate_profile',lambda profile,kind:None)
    owner=Window();owner.render_queue.set_paused(True);controller=NetworkRenderController(owner)
    target=dict(url=f'ws://127.0.0.1:{controller.server.socket.getsockname()[1]}',token=controller.token,name='Host')
    events=[];errors=[];cancel=threading.Event()
    def client():
        try:run_remote_request(dict(kind='image',prompt='Image direction.',profile={}),target,events.append,cancel,20)
        except Exception as error:errors.append(error)
    worker=threading.Thread(target=client,daemon=True);worker.start()
    try:
        wait_for(lambda:events or errors)
        assert events[0]['message'].startswith('Pending on ') and not errors
        assert owner.render_queue.jobs[0].state=='Waiting' and not owner.tasks
        cancel.set();wait_for(lambda:errors and owner.render_queue.jobs[0].state=='Cancelled')
        assert isinstance(errors[0],InterruptedError)
        with connect(target['url'],proxy=None) as connection:
            json_message(connection,dict(type='render',token='wrong-session',request=dict(kind='image')))
            assert receive_json(connection,3)['type']=='error'
        assert len(owner.render_queue.jobs)==1
    finally:cancel.set();controller.close();owner.render_queue.shutdown();owner.close()
