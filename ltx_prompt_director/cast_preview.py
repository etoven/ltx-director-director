"""Cast attached media or a live view of video/Flux inference to another room."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import contextlib
import mimetypes
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit
from PySide6.QtCore import QObject,Signal,QTimer,Qt
from PySide6.QtGui import QImage,QPainter,QColor,QFont
from PySide6.QtWidgets import QPushButton,QMenu


class PreviewMediaServer:
    def __init__(self):
        self.root=Path(tempfile.mkdtemp(prefix='director-cast-'));self.token=secrets.token_urlsafe(24)
        self.media=None;self.latest=None;self.lock=threading.Lock();self.closed=threading.Event();self.encoder=None
        server=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_HEAD(self):self.serve(False)
            def do_GET(self):self.serve(True)
            def serve(self,body):
                parts=urlsplit(self.path).path.split('/')
                if len(parts)!=3 or not secrets.compare_digest(parts[1],server.token):self.send_error(404);return
                name=parts[2]
                path=server.media if name=='media' else server.root/name
                if path is None or name!='media' and (Path(name).name!=name or Path(name).suffix not in {'.m3u8','.ts'}):self.send_error(404);return
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
                        self.send_header('Content-Type','application/vnd.apple.mpegurl' if name.endswith('.m3u8') else 'video/mp2t' if name.endswith('.ts') else mimetypes.guess_type(str(path))[0] or 'application/octet-stream')
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

    def push(self,data):
        with self.lock:self.latest=data

    def start_live(self):
        if self.encoder is not None or self.closed.is_set():return
        import imageio_ffmpeg
        self.errors=(self.root/'encoder.log').open('wb')
        self.encoder=subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-hide_banner','-loglevel','error',
            '-f','rawvideo','-pix_fmt','rgb24','-s','960x540','-r','12','-i','pipe:0','-an',
            '-c:v','libx264','-preset','ultrafast','-tune','zerolatency','-g','12','-pix_fmt','yuv420p',
            '-f','hls','-hls_time','1','-hls_list_size','5','-hls_flags','delete_segments+independent_segments',str(self.root/'live.m3u8')],
            stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=self.errors)
        def feed():
            blank=bytes(960*540*3)
            try:
                while not self.closed.is_set() and self.encoder.poll() is None:
                    before=time.monotonic()
                    with self.lock:data=self.latest or blank
                    self.encoder.stdin.write(data);self.encoder.stdin.flush()
                    self.closed.wait(max(0,1/12-(time.monotonic()-before)))
            except (OSError,ValueError):pass
        threading.Thread(target=feed,daemon=True).start()

    def close(self):
        self.closed.set();self.http.shutdown();self.http.server_close()
        if self.encoder:
            with contextlib.suppress(OSError):self.encoder.terminate()
            try:self.encoder.wait(timeout=3)
            except subprocess.TimeoutExpired:self.encoder.kill();self.encoder.wait()
            with contextlib.suppress(OSError):self.encoder.stdin.close()
            self.errors.close()
        shutil.rmtree(self.root,ignore_errors=True)


class CastPreviewController(QObject):
    device_found=Signal(object)
    status=Signal(str)
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.devices={};self.browser=None;self.device=None;self.server=None
        self.source='video';self.signature=None;self.busy=False;self.generation=0;self.closed=threading.Event()
        self.button=QPushButton('Cast…');self.button.setObjectName('castPreview');self.button.setToolTip('Watch Video Preview or Flux images on a Chromecast')
        owner.network_render.destination_layout.addWidget(self.button)
        self.button.clicked.connect(self.menu);self.device_found.connect(self.found)
        self.status.connect(lambda message:owner.statusBar().showMessage(message,8000))
        self.timer=QTimer(self);self.timer.setInterval(160);self.timer.timeout.connect(self.update)
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
        if not continuous:self.signature=None
        self.button.setText('Casting · '+self.device.name);self.timer.start();self.update()

    def update(self):
        if self.device is None or self.closed.is_set():return
        panel=self.owner.project_preview_panel
        image=self.owner.image_generation_panel.pixmap.toImage() if self.source=='image' else panel.current_frame
        if image is None or image.isNull():
            image=QImage(960,540,QImage.Format.Format_RGB888);image.fill(QColor('#101923'))
        canvas=QImage(960,540,QImage.Format.Format_RGB888);canvas.fill(Qt.GlobalColor.black)
        painter=QPainter(canvas)
        fitted=image.scaled(960,540,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
        painter.drawImage((960-fitted.width())//2,(540-fitted.height())//2,fitted)
        kind='Image' if self.source=='image' else 'Video'
        active=next((job for job in self.owner.render_queue.jobs if job.kind==kind and job.state in {'Rendering','Waiting','Cancelling'}),None)
        if active:
            from .local_widgets import progress_text
            text=active.title+' · '+progress_text(active.progress_event)
            painter.fillRect(0,494,960,46,QColor(8,18,28,225));painter.setPen(QColor('#d5f9f0'));painter.setFont(QFont('Sans',13))
            painter.drawText(16,524,painter.fontMetrics().elidedText(text,Qt.TextElideMode.ElideRight,928))
        painter.end()
        if self.server:self.server.push(bytes(canvas.constBits()))
        path=panel.pending_preview_path
        attached=self.source=='video' and not panel._rendering and path and Path(path).suffix.lower() in {'.mp4','.mov','.mkv','.webm'} and Path(path).is_file()
        signature=('media',path) if attached else ('live',)
        if signature==self.signature or self.busy:return
        self.signature=signature;self.busy=True;device=self.device;generation=self.generation;data=bytes(canvas.constBits())
        position=panel.player.position()/1000
        def connect():
            try:
                if generation!=self.generation or self.closed.is_set():return
                server=self.server
                if server is None:
                    server=PreviewMediaServer()
                    if generation!=self.generation or self.closed.is_set():server.close();return
                    self.server=server
                server.push(data)
                address=device.cast_info.host
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as route:
                    route.connect((address,8009));host=route.getsockname()[0]
                device.wait(timeout=8)
                if attached:
                    server.media=Path(path);url=server.url(host,'media');mime=mimetypes.guess_type(path)[0] or 'video/mp4'
                    options=dict(stream_type='BUFFERED',current_time=position)
                else:
                    server.start_live()
                    for _ in range(100):
                        if (server.root/'live.m3u8').is_file():break
                        if self.closed.wait(.1) or server.closed.is_set():return
                    if not (server.root/'live.m3u8').is_file():raise RuntimeError('Live cast encoder did not start.')
                    url=server.url(host,'live.m3u8');mime='application/x-mpegURL';options=dict(stream_type='LIVE')
                if generation==self.generation and not self.closed.is_set():
                    device.media_controller.play_media(url,mime,title='Prompt Director · '+('Video Preview' if attached else 'Live preview'),**options)
            except Exception as error:
                self.status.emit('Chromecast: '+str(error)[:180])
                if generation==self.generation:self.signature=None
            finally:self.busy=False
        threading.Thread(target=connect,daemon=True).start()

    def stop(self):
        self.generation+=1;device=self.device;self.device=None;self.timer.stop();self.button.setText('Cast…')
        if device:
            def stop_device():
                with contextlib.suppress(Exception):device.media_controller.stop()
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
