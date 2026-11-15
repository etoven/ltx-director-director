"""Remote sampling and canvas casting together, through completion/cancellation."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json,re,threading,time,urllib.request
from urllib.parse import urlsplit
import av,numpy as np
from websockets.sync.client import connect
from PySide6.QtCore import QThread
from ltx_prompt_director import local_generation,ui
from ltx_prompt_director.models import Segment
from ltx_prompt_director.network_render import NetworkRenderController,public_profile
from test_network_render import Window,loopback_start,wait_for,APP


def test_remote_updates_keep_cast_and_client_alive_through_restart_and_cancel(tmp_path,monkeypatch):
    monkeypatch.setattr(NetworkRenderController,'start',loopback_start)
    profile=local_generation.default_profile();profile.update(video_model=str(tmp_path/'minimax_fl2va_pruned.safetensors'),video_variant='fl2va',video_duration=4,video_steps=24)
    monkeypatch.setattr(local_generation,'load_profile',lambda settings:dict(profile))
    monkeypatch.setattr(local_generation,'validate_profile',lambda profile,kind:None)
    host=Window();host.network_render=NetworkRenderController(host)
    output=tmp_path/'complete.mp4'
    with av.open(str(output),'w') as movie:
        stream=movie.add_stream('libx264',rate=24);stream.width=128;stream.height=96;stream.pix_fmt='yuv420p'
        for index in range(24):
            for packet in stream.encode(av.VideoFrame.from_ndarray(np.full((96,128,3),90,np.uint8),format='rgb24')):movie.mux(packet)
        for packet in stream.encode():movie.mux(packet)
    release=threading.Event();runs=[]
    def inference(request,cancel):
        runs.append(request);progress=local_generation.LOCAL_PROGRESS.get()
        for step in range(1,25):
            if cancel.is_set():raise InterruptedError('Cancelled')
            bank=tmp_path/f'bank-{len(runs)}-{step}.rgb';bank.write_bytes(bytes((step*8,160,40))*120*68*7)
            progress(dict(kind='preview',media='latent_frames',path=str(bank),width=120,height=68,latent_frames=7,frames=22,fps=24,step=step))
            progress(dict(kind='progress',percent=step*4,eta_seconds=120-step,message='Remote sampling'))
            time.sleep(.06)
        while not release.wait(.01):
            if cancel.is_set():raise InterruptedError('Cancelled')
        return dict(path=str(output),media='video')
    monkeypatch.setattr(host.network_render,'_run_host_request',inference)
    frames=[];pages=[];errors=[];closed=threading.Event()
    def watch(url):
        pages.append(url)
        def read():
            try:
                with urllib.request.urlopen(url,timeout=5) as response:html=response.read().decode()
                assert '<canvas' in html and '<video' not in html
                port=int(re.search(r':(\d+)/',html).group(1));parts=urlsplit(url);token=parts.path.split('/')[1]
                with connect(f'ws://{parts.hostname}:{port}/{token}/frames',proxy=None,close_timeout=1) as socket:
                    while not closed.is_set():
                        header=json.loads(socket.recv(timeout=5));pixels=socket.recv(timeout=5)
                        assert len(pixels)==header['width']*header['height']*4
                        frames.append(header);socket.send(json.dumps(dict(presented=header['sequence'])))
            except __import__('websockets').exceptions.ConnectionClosed:pass
            except Exception as error:
                if not closed.is_set():errors.append(error)
        threading.Thread(target=read,daemon=True).start()
    device=SimpleNamespace(name='Canvas TV',uuid='canvas',cast_info=SimpleNamespace(host='127.0.0.1'),wait=lambda **kwargs:None,
        register_handler=lambda handler:None,quit_app=lambda:None,_director_canvas_receiver=SimpleNamespace(load_url=lambda url,**kwargs:watch(url)),
        media_controller=SimpleNamespace(play_media=lambda *args,**kwargs:None))
    with patch.object(ui.MainWindow,'restore_startup_workspace'):
        w=ui.MainWindow();w._close_saves_queued=True
        try:
            w.set_project_type('minimax_frames');w.minimax_prompt_text='Remote cast fixture.'
            w.segments=[Segment('Scene','','','text','text','Preserve this authored wording.',4)]
            w.requested_length.setValue(4);w.refresh_timeline();w.save_library_project(automatic=True);w.show()
            peer=dict(id='host',name='PC2',url=f'ws://127.0.0.1:{host.network_render.server.socket.getsockname()[1]}',token=host.network_render.token,profile=public_profile(profile))
            w.network_render._peer(peer);w.network_render.combo.setCurrentIndex(1)
            w.cast_preview.found(device);w.cast_preview.start('canvas');w.local_video_generate.click()
            wait_for(lambda:len(frames)>6 or errors);assert not errors
            job=w.render_queue.active;assert job.state=='Rendering' and w.project_preview_panel.latent_player.active
            assert any(frame['width']==120 for frame in frames)
            w.cast_preview.stop();before=job.percent
            wait_for(lambda:job.percent>before)
            w.cast_preview.start('canvas');wait_for(lambda:len(pages)>=2 and w.cast_preview.server and w.cast_preview.server.presented_frames>1)
            release.set();wait_for(lambda:job.state=='Complete')
            wait_for(lambda:w.render_group_options.get('0',{}).get('clip'))
            attachment=w.render_group_options['0']['clip']['path'];assert Path(attachment).is_file()
            # A fresh remote render can be cancelled while its canvas is drawing.
            release.clear();w.minimax_prompt_text='Second remote cast fixture.';w.local_video_generate.click()
            next_job=w.render_queue.active;wait_for(lambda:next_job.percent is not None and next_job.percent>8)
            w.cancel_queued_video();wait_for(lambda:next_job.state=='Cancelled')
            assert w.isVisible() and w.render_group_options['0']['clip']['path']==attachment
            assert w.segments[0].prompt=='Preserve this authored wording.'
            assert not errors
        finally:
            closed.set();release.set();w.cast_preview.stop();w.grouped_sequence.timer.stop();w.shot_sequence_panel.deactivate();w.render_queue.shutdown();w.close()
            host.network_render.close();host.render_queue.shutdown();host.close()
