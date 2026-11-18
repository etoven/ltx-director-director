"""LAN inference transport. Model loading stays in the receiving Director's queue."""
from contextvars import ContextVar
from pathlib import Path
import base64
import contextlib
import ipaddress
import json
import os
import queue
import secrets
import socket
import threading
import time
from uuid import uuid4

REMOTE_TARGET=ContextVar('director_remote_render_target',default=None)
REMOTE_JOB_RECORD=ContextVar('director_remote_job_record',default=None)
REMOTE_DETACH=ContextVar('director_remote_detach',default=None)
SERVICE='_promptdirector._tcp.local.'
CHUNK=256*1024
MAX_FILE=4*1024**3
MEDIA={'.png','.jpg','.jpeg','.webp','.mp4','.mov','.mkv','.webm','.wav','.mp3','.flac','.ogg','.rgb'}


def json_message(connection,value):connection.send(json.dumps(value,ensure_ascii=False))


def receive_json(connection,timeout=None):
    value=connection.recv(timeout=timeout)
    if not isinstance(value,str):raise ValueError('Expected a render protocol message.')
    result=json.loads(value)
    if not isinstance(result,dict):raise ValueError('Invalid render protocol message.')
    return result


def map_files(value,files,root=None):
    if isinstance(value,dict):return {k:v if k in {'prompt','text','message','name','details'} else map_files(v,files,root) for k,v in value.items() if k not in {'hf_token','api_key','access_token','worker_job_id','job_dir'}}
    if isinstance(value,list):return [map_files(v,files,root) for v in value]
    if root is not None and isinstance(value,str) and value.startswith(('data:image/','data:video/','data:audio/')):
        import mimetypes
        header,encoded=value.split(',',1);suffix=mimetypes.guess_extension(header[5:].split(';')[0]) or ''
        if suffix=='.jpe':suffix='.jpg'
        if suffix not in MEDIA or ';base64' not in header:raise ValueError('Unsupported inline reference media.')
        data=base64.b64decode(encoded,validate=True)
        if len(data)>MAX_FILE:raise ValueError('Inline reference media is too large.')
        key=uuid4().hex;path=Path(root)/(key+suffix);path.write_bytes(data);files[key]=path
        return {'$asset':key}
    if isinstance(value,str) and 0<len(value)<4096 and Path(value).suffix.lower() in MEDIA:
        try:
            path=Path(value)
            if path.is_file():
                key=uuid4().hex;files[key]=path
                return {'$asset':key}
        except OSError:pass
    return value


def restore_files(value,files):
    if isinstance(value,dict):
        if set(value)=={'$asset'}:return str(files[value['$asset']])
        return {key:restore_files(item,files) for key,item in value.items()}
    if isinstance(value,list):return [restore_files(item,files) for item in value]
    return value


def send_file(connection,key,path,cancel=None):
    path=Path(path);size=path.stat().st_size
    if size>MAX_FILE:raise ValueError('A network render file exceeds 4 GiB.')
    json_message(connection,dict(type='file',id=key,suffix=path.suffix.lower(),size=size))
    with path.open('rb') as stream:
        while data:=stream.read(CHUNK):
            if cancel is not None and cancel.is_set():raise InterruptedError('Network render cancelled.')
            connection.send(data)


def receive_file(connection,header,root,cancel=None):
    size=header.get('size');suffix=header.get('suffix')
    if not isinstance(size,int) or not 0<=size<=MAX_FILE or suffix not in MEDIA:raise ValueError('Invalid network render file.')
    path=Path(root)/(uuid4().hex+suffix)
    try:
        with path.open('wb') as stream:
            left=size
            while left:
                if cancel is not None and cancel.is_set():raise InterruptedError('Network render cancelled.')
                chunk=connection.recv(timeout=30)
                if not isinstance(chunk,bytes) or not chunk or len(chunk)>min(CHUNK,left):raise ValueError('Invalid network file chunk.')
                stream.write(chunk);left-=len(chunk)
        return path
    except BaseException:path.unlink(missing_ok=True);raise


def public_profile(profile):
    # Advertise only the model fields needed by the destination selector.
    fields={'video_model','video_encoder','video_vae','audio_vae','video_assets','video_variant',
            'image_model','image_encoder','image_vae','image_assets','image_variant'}
    return {key:value for key,value in profile.items() if key in fields}


def remote_profile(profile,kind,mode=''):
    """Use server checkpoints and the client's authored size/steps/seed settings."""
    from .local_generation import workspace_video_profile
    result=dict(profile)
    if kind=='video' and mode:
        if mode not in {'fl2va','ref2va'}:raise ValueError('Unsupported MiniMax mode.')
        result=workspace_video_profile(result,{'engine':'minimax_frames' if mode=='fl2va' else 'minimax_references'})
    return result


def _run_legacy_request(request,target,on_event,cancel,timeout):
    from websockets.sync.client import connect
    from .local_generation import data_root
    root=data_root()/'remote'/uuid4().hex;root.mkdir(parents=True,mode=0o700)
    files={};payload=map_files({key:value for key,value in request.items() if key!='profile'},files,root)
    profile=request.get('profile',{})
    # Never send local model paths, adapters, credentials or arbitrary execution config.
    controls={key:value for key,value in profile.items() if key in {'video_width','video_height','video_duration','video_steps','video_seed','video_sampler','video_preview_mode','video_preview_every','image_width','image_height','steps','preview_every'}}
    payload['controls']=controls;payload['mode']=profile.get('video_variant','')
    start=time.monotonic()
    with connect(target['url'],open_timeout=8,close_timeout=2,max_size=CHUNK*2,proxy=None) as connection:
        json_message(connection,dict(type='render',token=target['token'],request=payload,files=len(files)))
        for key,path in files.items():send_file(connection,key,path,cancel)
        incoming={}
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    json_message(connection,dict(type='cancel'));raise InterruptedError('Network render cancelled.')
                if timeout and time.monotonic()-start>timeout:raise TimeoutError('Network rendering timed out.')
                try:message=receive_json(connection,.2)
                except TimeoutError:continue
                kind=message.get('type')
                if kind=='file':incoming[message['id']]=receive_file(connection,message,root,cancel)
                elif kind=='event':
                    if on_event:on_event(restore_files(message['event'],incoming))
                elif kind=='result':return restore_files(message['result'],incoming)
                elif kind=='error':raise RuntimeError(message.get('message','Network rendering failed.'))
        finally:
            if cancel is not None and cancel.is_set():
                with contextlib.suppress(Exception):json_message(connection,dict(type='cancel'))


class ClientDetached(InterruptedError):
    pass


def _detaching():
    event=REMOTE_DETACH.get()
    return event is not None and event.is_set()


def cancel_remote(target,request_id):
    from websockets.sync.client import connect
    with connect(target['url'],open_timeout=3,close_timeout=1,proxy=None) as connection:
        json_message(connection,dict(type='cancel',token=target['token'],request_id=request_id))
        return receive_json(connection,3).get('type') in {'cancelled','not_found'}


def probe_remote(target,request_id):
    from websockets.sync.client import connect
    with connect(target['url'],open_timeout=3,close_timeout=1,proxy=None) as connection:
        json_message(connection,dict(type='resume',token=target['token'],request_id=request_id,probe=True))
        return receive_json(connection,3).get('type')=='accepted'


def run_remote_request(request,target,on_event,cancel,timeout):
    from websockets.sync.client import connect
    from websockets.exceptions import ConnectionClosed,InvalidHandshake
    from .local_generation import data_root
    from .remote_jobs import FEATURE,read_receipt,save_receipt
    target=dict(target);record=REMOTE_JOB_RECORD.get()
    request_id=(record or {}).get('portable',{}).get('remote_request_id') or uuid4().hex
    resume_only=bool((record or {}).get('portable',{}).get('remote_resume_only'))
    if resume_only:target['features']=list(set(target.get('features',[]))|{FEATURE})
    receipt=read_receipt(request_id) if record else None
    if receipt and receipt.get('status')=='downloaded':
        result=receipt.get('result',{})
        paths=[result.get('path')] if result.get('path') else [path for path in result.values() if isinstance(path,str)]
        if paths and all(Path(path).is_file() for path in paths):return result
    if 'features' not in target:
        with connect(target['url'],open_timeout=8,close_timeout=1,proxy=None) as connection:
            json_message(connection,dict(type='describe',token=target['token']))
            target['features']=receive_json(connection,10).get('features',[])
    if FEATURE not in target['features']:
        return _run_legacy_request(request,target,on_event,cancel,timeout)
    receipt=receipt or dict(request_id=request_id,record=record,target={'name':target.get('name'),'url':target['url']},status='created')
    if record:save_receipt(request_id,receipt)  # durable identity before host submission
    root=data_root()/'remote'/request_id;root.mkdir(parents=True,exist_ok=True,mode=0o700)
    files={};payload={} if resume_only else map_files({key:value for key,value in request.items() if key!='profile'},files,root)
    profile=request.get('profile',{})
    payload['controls']={key:value for key,value in profile.items() if key in {'video_width','video_height','video_duration','video_steps','video_seed','video_sampler','video_preview_mode','video_preview_every','image_width','image_height','steps','preview_every'}}
    payload['mode']=profile.get('video_variant','')
    action='resume';started=time.monotonic();announced=False
    try:
        while not timeout or time.monotonic()-started<timeout:
            if _detaching():raise ClientDetached('Client detached; the host render continues.')
            if cancel is not None and cancel.is_set():raise InterruptedError('Network render cancelled.')
            try:
                with connect(target['url'],open_timeout=5,close_timeout=1,max_size=CHUNK*2,proxy=None) as connection:
                    message=dict(type=action,token=target['token'],request_id=request_id)
                    if action=='render':message.update(request=payload,files=len(files))
                    json_message(connection,message)
                    if action=='render':
                        for key,path in files.items():send_file(connection,key,path,cancel)
                    incoming={}
                    while True:
                        if _detaching():raise ClientDetached('Client detached; the host render continues.')
                        if cancel is not None and cancel.is_set():raise InterruptedError('Network render cancelled.')
                        if timeout and time.monotonic()-started>=timeout:raise RuntimeError('Remote monitoring timed out; the host job is retained.')
                        try:message=receive_json(connection,.2)
                        except TimeoutError:continue
                        kind=message.get('type')
                        if kind=='not_found':
                            if resume_only:
                                receipt.update(status='failed',message='The original host job is unavailable; use Render Again to submit a new job.')
                                if record:save_receipt(request_id,receipt)
                                raise RuntimeError(receipt['message'])
                            action='render';break
                        if kind=='accepted':
                            action='resume';announced=False
                            receipt.update(status='submitted',host_job_id=message.get('job_id'))
                            if record:save_receipt(request_id,receipt)
                        elif kind=='file':incoming[message['id']]=receive_file(connection,message,root,cancel)
                        elif kind=='event':
                            if on_event:on_event(restore_files(message['event'],incoming))
                        elif kind=='result':
                            result=restore_files(message['result'],incoming)
                            receipt.update(status='downloaded',result=result)
                            if record:save_receipt(request_id,receipt)
                            with contextlib.suppress(Exception):json_message(connection,dict(type='ack'))
                            return result
                        elif kind=='error':
                            if message.get('retry_resume'):action='resume';break
                            receipt.update(status='failed',message=message.get('message','Network render failed.'))
                            if record:save_receipt(request_id,receipt)
                            raise RuntimeError(receipt['message'])
            except (ConnectionClosed,InvalidHandshake,OSError,TimeoutError) as error:
                action='resume'
                if not announced and on_event:
                    on_event(dict(kind='progress',message='Reconnecting · host render continues',eta_seconds=None))
                    announced=True
                if cancel is not None:cancel.wait(1)
                else:threading.Event().wait(1)
        raise RuntimeError('Remote monitoring timed out; the host job is retained.')
    except InterruptedError:
        if _detaching():raise ClientDetached('Client detached; the host render continues.')
        receipt['status']='cancel_requested'
        if record:save_receipt(request_id,receipt)
        with contextlib.suppress(Exception):
            if cancel_remote(target,request_id):
                receipt['status']='cancelled'
                if record:save_receipt(request_id,receipt)
        raise


from PySide6.QtCore import QObject,Signal,QTimer
from PySide6.QtWidgets import QComboBox,QHBoxLayout,QWidget


class NetworkRenderController(QObject):
    submit_requested=Signal(object)
    cancel_requested=Signal(str)
    peer_found=Signal(object)
    peer_removed=Signal(str)
    status=Signal(str)
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.id=uuid4().hex;self.token=secrets.token_urlsafe(24)
        self.name=socket.gethostname();self.peers={};self.server=None;self.zeroconf=None;self.browser=None;self.service=None
        self.closed=threading.Event();self.inbound={}
        self.detached={};self.detached_lock=threading.Lock()
        self.combo=QComboBox();self.combo.setObjectName('renderDestination');self.combo.addItem('Generate locally',None)
        self.combo.setToolTip('Destination for MiniMax video and local Flux image renders')
        row=QWidget();self.destination_layout=QHBoxLayout(row);self.destination_layout.setContentsMargins(0,0,0,0);self.destination_layout.setSpacing(5)
        self.destination_layout.addWidget(self.combo,1);owner.project_preview_panel.layout().insertWidget(2,row)
        self.submit_requested.connect(self._submit);self.cancel_requested.connect(owner.render_queue.cancel_job)
        self.peer_found.connect(self._peer);self.peer_removed.connect(self._remove)
        self.status.connect(lambda message:owner.statusBar().showMessage(message,8000))
        self.combo.currentIndexChanged.connect(lambda _:owner.update_render_controls())
        owner.render_queue.changed.connect(self._queue_changed)
        self.start()

    @property
    def target(self):return self.combo.currentData() or None

    def client_profile(self,kind):
        from .local_generation import load_profile
        value=load_profile(self.owner.settings)
        if not self.target:return value
        server=self.target.get('profile',{})
        # Keep authored rendering controls while identifying the host's models.
        for key in ('video_model','video_encoder','video_vae','audio_vae','video_assets','image_model','image_encoder','image_vae','image_assets','image_variant','video_variant'):
            if key in server:value[key]=server[key]
        return value

    def group_profile(self,profile,definition):
        from .local_generation import workspace_video_profile
        if not self.target:return workspace_video_profile(profile,definition)
        mode={'minimax_frames':'fl2va','minimax_references':'ref2va'}.get(definition.get('engine'))
        result=dict(profile)
        if mode:
            result['video_variant']=mode
            result['video_model']=f'network_minimax_{mode}_pruned.safetensors'
        return result

    def validate(self,profile,kind):
        from .local_generation import validate_profile
        if not self.target:validate_profile(profile,kind)
        elif not self.target.get('profile',{}).get(kind+'_model'):raise ValueError('Configure this model on '+self.target['name']+' first.')

    def start(self):threading.Thread(target=self._serve,daemon=True).start()

    def _serve(self):
        try:
            from websockets.sync.server import serve
            from zeroconf import Zeroconf,ServiceInfo,ServiceBrowser
            import psutil
            addresses=[socket.inet_aton(row.address) for rows in psutil.net_if_addrs().values() for row in rows
                       if row.family==socket.AF_INET and not row.address.startswith('127.')]
            self.server=serve(self._connection,'0.0.0.0',0,max_size=CHUNK*2,open_timeout=8,close_timeout=2)
            port=self.server.socket.getsockname()[1]
            self.zeroconf=Zeroconf()
            self.service=ServiceInfo(SERVICE,self.id+'.'+SERVICE,addresses=addresses or [socket.inet_aton('127.0.0.1')],port=port,
                properties={'name':self.name,'id':self.id,'token':self.token,'protocol':'1'})
            self.zeroconf.register_service(self.service)
            controller=self
            class Listener:
                def add_service(self,zc,kind,name):controller._discover(zc,kind,name)
                def update_service(self,zc,kind,name):controller._discover(zc,kind,name)
                def remove_service(self,zc,kind,name):controller.peer_removed.emit(name)
            self.browser=ServiceBrowser(self.zeroconf,SERVICE,Listener())
            if self.closed.is_set():return
            self.server.serve_forever()
        except Exception as error:
            if not self.closed.is_set():self.status.emit('Network rendering unavailable: '+str(error)[:180])
        finally:
            if self.zeroconf:
                if self.service:
                    with contextlib.suppress(Exception):self.zeroconf.unregister_service(self.service)
                self.zeroconf.close()

    def _discover(self,zc,kind,name):
        if name==self.id+'.'+SERVICE or self.closed.is_set():return
        def resolve():
            try:
                from websockets.sync.client import connect
                info=zc.get_service_info(kind,name,timeout=1500)
                if info is None or info.properties.get(b'protocol')!=b'1':return
                addresses=info.parsed_addresses()
                if not addresses:return
                target=dict(id=name,name=info.properties[b'name'].decode(),token=info.properties[b'token'].decode(),url=f'ws://{addresses[0]}:{info.port}')
                with connect(target['url'],open_timeout=3,close_timeout=1,proxy=None) as connection:
                    json_message(connection,dict(type='describe',token=target['token']))
                    description=receive_json(connection,5)
                    target['profile']=description['profile'];target['features']=description.get('features',[])
                self.peer_found.emit(target)
            except Exception:pass
        threading.Thread(target=resolve,daemon=True).start()

    def _peer(self,target):
        if self.closed.is_set():return
        self.peers[target['id']]=target
        for i in range(1,self.combo.count()):
            if self.combo.itemData(i)['id']==target['id']:self.combo.setItemData(i,target);return
        self.combo.addItem('Generate on '+target['name'],target)

    def _remove(self,key):
        self.peers.pop(key,None)
        for i in range(self.combo.count()-1,0,-1):
            if self.combo.itemData(i)['id']==key:self.combo.removeItem(i)

    def _stream_detached(self,connection,mailbox):
        cursor=0
        while not self.closed.is_set():
            try:
                command=receive_json(connection,.02)
                if command.get('type')=='cancel':
                    if mailbox.job_id:self.cancel_requested.emit(mailbox.job_id)
                    mailbox.acknowledged=True
                    json_message(connection,dict(type='cancelled'));return
                if command.get('type')=='ack':mailbox.acknowledged=True;return
            except TimeoutError:pass
            for sequence,kind,value in mailbox.updates(cursor):
                if kind=='retry':json_message(connection,dict(type='not_found'));return
                if kind=='error':json_message(connection,dict(type='error',message=str(value)));return
                outgoing={};encoded=map_files(value,outgoing)
                for key,path in outgoing.items():send_file(connection,key,path)
                if kind=='result':
                    json_message(connection,dict(type='result',result=encoded))
                    if getattr(mailbox,'legacy',False):mailbox.acknowledged=True;return
                    try:
                        ack=receive_json(connection,5)
                        if ack.get('type')=='ack':mailbox.acknowledged=True
                    except Exception:pass
                    return
                json_message(connection,dict(type='event',event=encoded));cursor=sequence

    def _connection(self,connection):
        job_id=None;mailbox=None;reserved=False
        try:
            if not ipaddress.ip_address(connection.remote_address[0]).is_private:raise ValueError('Network rendering is limited to the LAN.')
            message=receive_json(connection,8)
            if not secrets.compare_digest(str(message.get('token','')),self.token):raise ValueError('Unknown Director session.')
            from .remote_jobs import DetachedJob,FEATURE
            kind=message.get('type');request_id=message.get('request_id') or uuid4().hex
            from uuid import UUID
            UUID(request_id)
            if kind in {'resume','cancel'}:
                with self.detached_lock:mailbox=self.detached.get(request_id)
                if mailbox is None:json_message(connection,dict(type='not_found'));return
                if kind=='cancel':
                    if mailbox.job_id:self.cancel_requested.emit(mailbox.job_id)
                    mailbox.acknowledged=True
                    json_message(connection,dict(type='cancelled'));return
                json_message(connection,dict(type='accepted',request_id=request_id,job_id=mailbox.job_id))
                if message.get('probe'):return
                self._stream_detached(connection,mailbox);return
            response=queue.Queue();self.submit_requested.emit(dict(type='profile',response=response))
            config=response.get(timeout=10)
            if kind=='describe':json_message(connection,dict(profile=public_profile(config),features=[FEATURE]));return
            if kind!='render':raise ValueError('Unsupported network render command.')
            with self.detached_lock:
                if request_id in self.detached:
                    json_message(connection,dict(type='error',message='Job already exists; reconnect to its request ID.',retry_resume=True));return
                mailbox=DetachedJob(request_id);mailbox.legacy=not message.get("request_id")
                self.detached[request_id]=mailbox;reserved=True
                delivered=[key for key,value in self.detached.items() if value.acknowledged and value.terminal]
                for key in delivered[:-100]:self.detached.pop(key,None)
            request=message.get('request',{})
            if request.get('kind') not in {'image','video'}:raise ValueError('Only MiniMax video and Flux image rendering are supported.')
            from .local_generation import data_root,validate_profile
            root=data_root()/'network-inputs'/uuid4().hex;root.mkdir(parents=True,mode=0o700)
            count=message.get('files',0)
            if not isinstance(count,int) or not 0<=count<=128:raise ValueError('Invalid reference file count.')
            files={}
            for _ in range(count):
                header=receive_json(connection,30)
                if header.get('type')!='file':raise ValueError('Expected reference media.')
                files[header['id']]=receive_file(connection,header,root)
            request=restore_files(request,files)
            known={str(path) for path in files.values()}
            def validate_media(value,key=''):
                if isinstance(value,dict):
                    for child,item in value.items():validate_media(item,child)
                elif isinstance(value,list):
                    for item in value:validate_media(item,key)
                elif key in {'source','path','audio_path','image','video','audio','references','reference_images','referenceImages'} and isinstance(value,str) and value:
                    if value not in known and not value.startswith(('data:image/','data:video/','data:audio/')):
                        raise ValueError('Reference media must be transferred from the submitting Director.')
            validate_media(request)
            controls=request.pop('controls',{});mode=request.pop('mode','')
            config=remote_profile(config,request['kind'],mode)
            allowed={'video_width','video_height','video_duration','video_steps','video_seed','video_sampler','video_preview_mode','video_preview_every','image_width','image_height','steps','preview_every'}
            config.update({key:value for key,value in controls.items() if key in allowed})
            validate_profile(config,request['kind'])
            # Build a strict request, excluding caller-controlled runtime/execution paths.
            allowed_request={'kind','prompt','source','references','width','height','duration','steps','seed','segments','conditioning','reference_images','referenceImages','fps','audio','transition','continuation'}
            request={key:value for key,value in request.items() if key in allowed_request}
            request['profile']=config
            events=mailbox;accepted=queue.Queue()
            self.submit_requested.emit(dict(type='render',request=request,events=events,accepted=accepted))
            job_id=accepted.get(timeout=10);mailbox.job_id=job_id
            json_message(connection,dict(type='accepted',request_id=request_id,job_id=job_id))
            self._stream_detached(connection,mailbox)
        except Exception as error:
            with contextlib.suppress(Exception):json_message(connection,dict(type='error',message=str(error)[:500]))
        finally:
            # Losing a subscriber must never cancel an accepted host render.
            if reserved and mailbox is not None and mailbox.job_id is None:
                with self.detached_lock:
                    if self.detached.get(mailbox.request_id) is mailbox:self.detached.pop(mailbox.request_id,None)
                mailbox.put(('retry',None))

    def _submit(self,message):
        from .local_generation import load_profile,run_request
        if message['type']!='render':message['response'].put(load_profile(self.owner.settings));return
        request=message['request'];events=message['events']
        def progress(event):events.put(('event',event))
        def operation():return self._run_host_request(request,cancel)
        cancel=threading.Event()
        job=self.owner.render_queue.enqueue(operation,(),lambda result:events.put(('result',result)),lambda error:events.put(('error',error)),
            title='Network render · '+self.name,source='local',model='MiniMax H3' if request['kind']=='video' else 'Flux 2 Klein',
            kind=request['kind'].title(),mode=request['profile'].get('video_variant','') if request['kind']=='video' else '',
            progress=progress,cancel=cancel,cancelled=lambda:events.put(('error','Remote render cancelled.')))
        events.job_id=job.id
        self.inbound[job.id]=(job,events);message['accepted'].put(job.id);self._queue_changed()
        self.owner.render_queue_dock.show()

    def _run_host_request(self,request,cancel):
        from .local_generation import LOCAL_PROGRESS,run_request
        return run_request(request,on_event=LOCAL_PROGRESS.get(),cancel=cancel)

    def _queue_changed(self):
        for key,(job,events) in tuple(self.inbound.items()):
            if job.state=='Waiting':events.put(('event',dict(kind='progress',message=job.message or 'Pending on '+self.name)))
            elif job.state not in {'Rendering','Cancelling'}:self.inbound.pop(key,None)

    def close(self):
        self.closed.set()
        for key in tuple(self.inbound):self.owner.render_queue.cancel_job(key)
        if self.server:self.server.shutdown()
        if self.browser:self.browser.cancel()
