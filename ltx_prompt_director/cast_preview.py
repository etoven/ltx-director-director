"""Cast attached media or a live view of video/Flux inference to another room."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from importlib.resources import files
import json
import contextlib
import mimetypes
import os
import secrets
import socket
import threading
import time
from urllib.parse import urlsplit
from PySide6.QtCore import QObject,Signal,QTimer,Qt
from PySide6.QtGui import QImage,QColor
from PySide6.QtWidgets import QPushButton,QMenu


class PreviewMediaServer:
    def __init__(self):
        self.token=secrets.token_urlsafe(24);self.media=None
        self.changed=threading.Condition();self.latest=None;self.sequence=0
        self.presented_frames=0;self.last_presented=None
        self.closed=threading.Event();self.connections=set()
        from websockets.sync.server import serve
        self.websocket=serve(self._frames,'0.0.0.0',0,max_size=2048,close_timeout=1)
        threading.Thread(target=self.websocket.serve_forever,daemon=True).start()
        server=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_HEAD(self):self.serve(False)
            def do_GET(self):self.serve(True)
            def serve(self,body):
                parts=urlsplit(self.path).path.split('/')
                if len(parts)!=3 or not secrets.compare_digest(parts[1],server.token):self.send_error(404);return
                name=parts[2]
                if name=='canvas':
                    page=files('ltx_prompt_director').joinpath('assets/cast-preview.html').read_text(encoding='utf-8')
                    page=page.replace('__WS_PORT__',str(server.websocket.socket.getsockname()[1]))
                    data=page.encode('utf-8');self.send_response(200)
                    self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store')
                    self.send_header('Content-Length',str(len(data)));self.end_headers()
                    if body:self.wfile.write(data)
                    return
                path=server.media
                if name!='media' or path is None:self.send_error(404);return
                try:
                    with Path(path).open('rb') as stream:
                        size=os.fstat(stream.fileno()).st_size;start=0;end=max(0,size-1)
                        requested=self.headers.get('Range','')
                        if requested.startswith('bytes='):
                            try:
                                first,last=requested[6:].split('-',1)
                                if first:start=int(first);end=min(end,int(last)) if last else end
                                else:start=max(0,size-int(last))
                                if start<0 or start>=size or end<start:raise ValueError()
                            except ValueError:self.send_error(416);return
                        self.send_response(206 if requested else 200)
                        self.send_header('Content-Type',mimetypes.guess_type(str(path))[0] or 'application/octet-stream')
                        self.send_header('Access-Control-Allow-Origin','*');self.send_header('Accept-Ranges','bytes')
                        self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(end-start+1 if size else 0))
                        if requested:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                        self.end_headers()
                        if body:
                            stream.seek(start);left=end-start+1
                            while left>0:
                                data=stream.read(min(256*1024,left))
                                if not data:break
                                self.wfile.write(data);left-=len(data)
                except (OSError,ConnectionError):pass
        self.http=ThreadingHTTPServer(('0.0.0.0',0),Handler);self.http.daemon_threads=True
        threading.Thread(target=self.http.serve_forever,daemon=True).start()

    def url(self,host,name):return f'http://{host}:{self.http.server_port}/{self.token}/{name}'

    def push(self,header,pixels):
        # One owned frame, never an accumulating stream/encoding queue.
        with self.changed:
            self.sequence+=1;self.latest=(dict(header,sequence=self.sequence),pixels)
            self.changed.notify_all()

    def _frames(self,connection):
        if connection.request.path!=f'/{self.token}/frames':connection.close(1008,'Unknown preview session');return
        with self.changed:self.connections.add(connection)
        seen=0
        try:
            while not self.closed.is_set():
                with self.changed:
                    self.changed.wait_for(lambda:self.closed.is_set() or self.sequence>seen,timeout=.5)
                    if self.closed.is_set():break
                    if self.sequence==seen:continue
                    header,pixels=self.latest;seen=header['sequence']
                connection.send(json.dumps(header));connection.send(pixels)
                # One in-flight frame per receiver. A slow TV skips stale frames.
                acknowledgement=json.loads(connection.recv(timeout=10))
                if acknowledgement.get('presented')!=seen:break
                with self.changed:
                    self.presented_frames+=1;self.last_presented=header
        except Exception:pass  # Receiver disconnection must never stop generation.
        finally:
            with self.changed:self.connections.discard(connection)

    def close(self):
        if self.closed.is_set():return
        self.closed.set()
        with self.changed:
            connections=tuple(self.connections);self.changed.notify_all()
        for connection in connections:
            with contextlib.suppress(Exception):connection.close()
        self.websocket.shutdown();self.http.shutdown();self.http.server_close()


class CastPreviewController(QObject):
    device_found=Signal(object)
    status=Signal(str)
    server_ready=Signal(int,object)
    connection_done=Signal(int,str)
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.devices={};self.browser=None;self.device=None;self.server=None
        self.source='video';self.signature=None;self.busy=False;self.generation=0;self.closed=threading.Event()
        self.snapshot=None;self.snapshot_key=None;self.retry_after=0
        self.button=QPushButton('Cast…');self.button.setObjectName('castPreview');self.button.setToolTip('Watch Video Preview or Flux images on a Chromecast')
        owner.network_render.destination_layout.addWidget(self.button)
        self.button.clicked.connect(self.menu);self.device_found.connect(self.found)
        self.status.connect(lambda message:owner.statusBar().showMessage(message,8000))
        self.timer=QTimer(self);self.timer.setInterval(1000//24);self.timer.timeout.connect(self.update)
        self.server_ready.connect(self._accept_server);self.connection_done.connect(self._connection_done)
        self.discover()

    def discover(self):
        if self.browser is not None:return
        def run():
            try:
                import pychromecast
                browser=pychromecast.get_chromecasts(blocking=False,callback=lambda cast:self.device_found.emit(cast))
                if self.closed.is_set():browser.stop_discovery()
                else:self.browser=browser
            except Exception as error:self.status.emit('Chromecast discovery unavailable: '+str(error)[:160])
        threading.Thread(target=run,daemon=True).start()

    def found(self,cast):
        if self.closed.is_set():return
        self.devices[str(cast.uuid)]=cast
        self.button.setToolTip('Cast video or Flux previews · '+str(len(self.devices))+' device(s) available')

    def menu(self):
        menu=QMenu(self.button)
        if not self.devices:menu.addAction('Searching for Chromecast devices…').setEnabled(False)
        for key,device in sorted(self.devices.items(),key=lambda pair:pair[1].name):
            target=menu.addMenu(device.name)
            target.addAction('Watch Video Preview',lambda checked=False,k=key:self.start(k,'video'))
            target.addAction('Watch Flux image preview',lambda checked=False,k=key:self.start(k,'image'))
        if self.device:menu.addAction('Stop casting',self.stop)
        menu.exec(self.button.mapToGlobal(self.button.rect().bottomLeft()))

    def start(self,key,source='video'):
        continuous=self.device is self.devices[key] and self.signature==('live',) and not self.busy
        self.generation+=1;self.device=self.devices[key];self.source=source
        if not continuous:self.signature=None;self.busy=False
        self.snapshot_key=None;self.retry_after=0
        self.button.setText('Casting · '+self.device.name);self.timer.start();self.update()

    def update(self):
        if self.device is None or self.closed.is_set():return
        panel=self.owner.project_preview_panel
        image=self.owner.image_generation_panel.pixmap.toImage() if self.source=='image' else panel.current_frame
        kind='Image' if self.source=='image' else 'Video'
        active=next((job for job in self.owner.render_queue.jobs if job.kind==kind and job.state in {'Rendering','Waiting','Cancelling'}),None)
        caption=''
        if active:
            from .local_widgets import progress_text
            caption=active.title+' · '+progress_text(active.progress_event)
        key=(self.source,image.cacheKey() if image is not None else None,caption)
        server=self.server
        if key!=self.snapshot_key:
            self.snapshot_key=key
            if image is None or image.isNull():
                image=QImage(160,90,QImage.Format.Format_RGBA8888);image.fill(QColor('#101923'))
            if image.width()>960 or image.height()>540:
                image=image.scaled(960,540,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
            image=image.convertToFormat(QImage.Format.Format_RGBA8888)
            self.snapshot=(dict(width=image.width(),height=image.height(),caption=caption,format='rgba'),bytes(image.constBits()))
            if server is not None and not server.closed.is_set():server.push(*self.snapshot)
        path=panel.pending_preview_path
        attached=self.source=='video' and not panel._rendering and path and Path(path).suffix.lower() in {'.mp4','.mov','.mkv','.webm'} and Path(path).is_file()
        signature=('media',path) if attached else ('live',)
        if signature==self.signature or self.busy or time.monotonic()<self.retry_after:return
        self.signature=signature;self.busy=True;device=self.device;generation=self.generation;snapshot=self.snapshot
        position=panel.player.position()/1000
        existing=self.server
        def connect():
            error='';created=None
            try:
                if generation!=self.generation or self.closed.is_set():return
                server=existing
                if server is None:
                    server=created=PreviewMediaServer();server.push(*snapshot)
                    if generation!=self.generation or self.closed.is_set():server.close();return
                    try:self.server_ready.emit(generation,server)
                    except RuntimeError:server.close();return
                address=device.cast_info.host
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as route:
                    route.connect((address,8009));host=route.getsockname()[0]
                device.wait(timeout=8)
                if generation!=self.generation or self.closed.is_set():return
                if attached:
                    server.media=Path(path)
                    device.media_controller.play_media(server.url(host,'media'),mimetypes.guess_type(path)[0] or 'video/mp4',
                        title='Prompt Director · Video Preview',stream_type='BUFFERED',current_time=position)
                else:
                    from pychromecast.controllers.dashcast import DashCastController
                    receiver=getattr(device,'_director_canvas_receiver',None)
                    if receiver is None:
                        receiver=DashCastController();device.register_handler(receiver);device._director_canvas_receiver=receiver
                    receiver.load_url(server.url(host,'canvas'),force=True)
            except Exception as failure:error='Chromecast: '+str(failure)[:180]
            finally:
                try:self.connection_done.emit(generation,error)
                except RuntimeError:pass
        threading.Thread(target=connect,daemon=True).start()

    def _accept_server(self,generation,server):
        if generation!=self.generation or self.closed.is_set() or self.device is None:
            threading.Thread(target=server.close,daemon=True).start();return
        self.server=server
        if self.snapshot is not None:server.push(*self.snapshot)

    def _connection_done(self,generation,error):
        if generation!=self.generation:return
        self.busy=False
        if error:
            self.status.emit(error);self.signature=None;self.retry_after=time.monotonic()+3

    def stop(self):
        self.generation+=1;device=self.device;self.device=None;self.timer.stop();self.button.setText('Cast…')
        self.busy=False;self.signature=None;self.snapshot=None;self.snapshot_key=None
        if device:
            def stop_device():
                with contextlib.suppress(Exception):device.quit_app()
            threading.Thread(target=stop_device,daemon=True).start()
        server,self.server=self.server,None
        if server:threading.Thread(target=server.close,daemon=True).start()

    def close(self):
        self.closed.set();self.stop()
        def disconnect():
            if self.browser:self.browser.stop_discovery()
            for device in self.devices.values():
                with contextlib.suppress(Exception):device.disconnect()
        threading.Thread(target=disconnect,daemon=True).start()
