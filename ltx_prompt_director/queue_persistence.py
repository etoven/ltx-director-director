"""Versioned render queue snapshots. Never serialize callables or credentials."""
import copy
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
from PySide6.QtCore import QObject,QTimer,Signal,Slot
from .render_queue import RenderJob

_SECRET={'api_key','hf_token','access_token','refresh_token','authorization','token','key','session_keys'}
_FIELDS=('id','title','project_id','source','model','kind','details','workspace_key','generation_number',
         'render_group','group_id','group_title','dependencies','output_path','intermediate','state','percent',
         'message','error','mode','eta_seconds','elapsed_seconds','reused','force_render','portable','remote_target')


def clean(value):
    if isinstance(value,Path):return str(value)
    if isinstance(value,dict):return {key:clean(item) for key,item in value.items() if key.lower() not in _SECRET}
    if isinstance(value,(list,tuple)):return [clean(item) for item in value]
    return value


def video_spec(request,segments,references,*,continuation=None,previous_job='',workspace_type='',attachment_revision=0,outgoing=None):
    records=[]
    for segment in segments:
        row=segment.to_dict()
        if getattr(segment,'_archive_source',None):row['_archive_source']=list(segment._archive_source)
        records.append(row)
    return dict(type='video',request=clean(copy.deepcopy(request)),segments=records,references=clean(copy.deepcopy(references)),
                continuation=continuation,previous_job=previous_job,workspace_type=workspace_type,attachment_revision=attachment_revision,outgoing=copy.deepcopy(outgoing))


def write_snapshot(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    handle,temporary=tempfile.mkstemp(prefix='.render-queue-',dir=path.parent)
    try:
        with os.fdopen(handle,'w',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        if "jobs" in value:
            from .remote_jobs import prune_receipts
            prune_receipts(value)
    finally:Path(temporary).unlink(missing_ok=True)


class QueueStore(QObject):
    remote_checked=Signal(object)
    def __init__(self,owner,path):
        super().__init__(owner);self.owner=owner;self.queue=owner.render_queue;self.path=Path(path)
        from .remote_jobs import receipt_directory,load_queue_with_receipts
        self.remote_pending={};self.remote_probing=set();self.unrestored_rows=[]
        self.remote_timer=QTimer(self);self.remote_timer.setInterval(1500);self.remote_timer.timeout.connect(self.check_remote)
        self.remote_checked.connect(self.remote_check_done)
        owner.network_render.peer_found.connect(self.peer_ready)
        self.frozen=False;self.restoring=self.path.is_file() or any(receipt_directory().glob('*.json'));self.writing=False;self.dirty=False;self.final_snapshot=None
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(500);self.timer.timeout.connect(self.flush)
        self.queue.changed.connect(self.changed)
        if self.restoring:
            owner.start_disk_job(load_queue_with_receipts,(self.path,),self.restore,self.restore_failed,serialized=True)

    def restore_failed(self,message):
        self.restoring=False
        logging.getLogger(__name__).error('Render queue could not be restored: %s',message)
        self.owner.statusBar().showMessage('Render queue could not be restored: '+str(message),12000)
        # Retain unreadable state for recovery rather than overwriting it.
        self.frozen=True

    def changed(self):
        if self.frozen or self.restoring:return
        if self.writing:self.dirty=True
        elif not self.timer.isActive():self.timer.start()

    def snapshot(self):
        rows=[];outputs={**self.queue._removed_outputs,**{job.id:job.output_path for job in self.queue.jobs if job.output_path}}
        for job in self.queue.jobs:
            row=clean({key:getattr(job,key) for key in _FIELDS})
            spec=row['portable'];previous=spec.get('previous_job')
            if previous and outputs.get(previous) and spec.get('continuation'):
                spec['continuation']['path']=outputs[previous]
            rows.append(row)
        panel=self.owner.render_queue_panel;selected=panel.selected_job()
        rows.extend(copy.deepcopy(self.unrestored_rows))
        return dict(version=1,paused=self.queue.paused,jobs=rows,removedStates=dict(self.queue._removed_states),
            removedOutputs=dict(self.queue._removed_outputs),collapsedGroups=list(panel.collapsed_groups),
            selected=selected.id if selected else '',scroll=panel.list.verticalScrollBar().value())

    def flush(self):
        if self.restoring:return
        if self.writing:self.dirty=True;return
        value=self.final_snapshot if self.frozen and self.final_snapshot is not None else self.snapshot()
        self.writing=True;self.dirty=False
        def done(error=None):
            self.writing=False
            if error:self.owner.statusBar().showMessage('Render queue save failed: '+str(error),12000)
            if self.dirty:
                self.dirty=False
                if self.frozen:self.flush()
                else:self.timer.start()
        self.owner.start_disk_job(write_snapshot,(self.path,value),lambda result:done(),done,serialized=True)

    def freeze(self):
        if self.frozen:return
        self.remote_timer.stop()
        self.timer.stop();self.final_snapshot=self.snapshot();self.frozen=True
        if not self.restoring:self.flush()

    def restore(self,value):
        try:
            if value.get('version')!=1:raise ValueError('Unsupported render queue snapshot version')
            self.unrestored_rows=[]
            existing={job.id for job in self.queue.jobs}
            receipts=value.get('remote_receipts',{})
            restored=[]
            for row in value.get('jobs',[]):
                if row.get('id') in existing:continue
                try:
                    data={key:item for key,item in row.items() if key in _FIELDS}
                    data['workspace_key']=tuple(data.get('workspace_key',()))
                    data['dependencies']=tuple(data.get('dependencies',()))
                    job=RenderJob(**data)
                except (TypeError,ValueError,KeyError) as error:
                    # Keep unreadable records in future snapshots, without
                    # withholding all the readable jobs from the queue.
                    self.unrestored_rows.append(copy.deepcopy(row))
                    logging.getLogger(__name__).error('Unreadable saved queue entry %s: %s',row.get('id','unknown'),error)
                    continue
                if job.state in {'Waiting','Rendering'}:
                    job.state='Waiting';job.percent=None;job.eta_seconds=None;job.message='Restored · pending'
                    if not job.portable:
                        job.state='Failed';job.error='Interrupted legacy/incoming render; submit it again from its project.'
                elif job.state=='Cancelling':job.state='Cancelled'
                request_id=job.portable.get('remote_request_id')
                if job.source=='network' and request_id in receipts:
                    job.portable['remote_resume_only']=True
                try:self.bind(job)
                except Exception as error:
                    job.state='Failed';job.error='Could not restore render inputs: '+str(error)
                    logging.getLogger(__name__).exception('Could not restore render inputs for job %s',job.id)
                restored.append(job)
                request_id=job.portable.get('remote_request_id')
                if request_id in receipts:self.remote_pending[job.id]=receipts[request_id]
            self.queue.jobs[:0]=restored
            self.queue._removed_states.update(value.get('removedStates',{}))
            self.queue._removed_outputs.update(value.get('removedOutputs',{}))
            self.queue.paused=bool(value.get('paused')) or any(job.state=='Waiting' for job in restored)
            panel=self.owner.render_queue_panel;panel.collapsed_groups=set(value.get('collapsedGroups',[]))
            self.restoring=False;self.queue.changed.emit()
            for row in range(panel.list.count()):
                if panel.list.item(row).data(256).id==value.get('selected'):panel.list.setCurrentRow(row);break
            panel.list.verticalScrollBar().setValue(int(value.get('scroll',0)))
            if self.remote_pending:self.remote_timer.start();self.check_remote()
        except (TypeError,ValueError,KeyError) as error:self.restore_failed(error)

    @Slot(object)
    def peer_ready(self,peer):
        self.check_remote()

    def check_remote(self):
        if self.frozen or self.restoring:return
        from .remote_jobs import FEATURE
        for ident,receipt in list(self.remote_pending.items()):
            job=next((item for item in self.queue.jobs if item.id==ident),None)
            if job is None or job.state=='Complete':self.remote_pending.pop(ident,None);continue
            if receipt.get('status')=='downloaded':
                result=receipt.get('result',{})
                paths=[result['path']] if result.get('path') else [path for path in result.values() if isinstance(path,str)]
                if paths and all(Path(path).is_file() for path in paths):
                    self.remote_pending.pop(ident,None);job.state='Complete';job.percent=100;job.output_path=paths[0]
                    if job.finished:job.finished(result)
                    self.queue.changed.emit();continue
            if self.queue.active is not None or ident in self.remote_probing:continue
            # The host must confirm ownership of the request GUID. Names can
            # change or collide; old discovery records may lack feature data.
            peers=copy.deepcopy(list(self.owner.network_render.peers.values()))
            if not peers:continue
            saved=receipt.get('target',{})
            peers.sort(key=lambda peer:(peer.get('id')!=saved.get('id'),peer.get('url')!=saved.get('url'),peer.get('name')!=saved.get('name')))
            self.remote_probing.add(ident)
            def probe(ident=ident,peers=peers,receipt=receipt):
                from .network_render import probe_remote,cancel_remote
                for target in peers:
                    try:
                        if not probe_remote(target,receipt['request_id']):continue
                        target['features']=list(set(target.get('features',[]))|{FEATURE})
                        cancelled=receipt.get('status')=='cancel_requested'
                        found=cancel_remote(target,receipt['request_id']) if cancelled else True
                        self.remote_checked.emit((ident,target,found,cancelled));return
                    except Exception:continue
                try:self.remote_checked.emit((ident,{},False,False))
                except RuntimeError:pass
            threading.Thread(target=probe,daemon=True).start()
        if not self.remote_pending:self.remote_timer.stop()

    @Slot(object)
    def remote_check_done(self,result):
        ident,target,found,cancelled=result;self.remote_probing.discard(ident)
        if self.frozen or not found:return
        job=next((item for item in self.queue.jobs if item.id==ident),None)
        receipt=self.remote_pending.get(ident)
        if job is None or receipt is None:return
        if cancelled:
            from .remote_jobs import save_receipt
            receipt['status']='cancelled'
            self.owner.start_disk_job(save_receipt,(receipt['request_id'],receipt),lambda result:None,lambda error:None,serialized=True)
            self.remote_pending.pop(ident,None);self.queue.cancel_job(ident);self.queue.changed.emit();return
        if self.queue.active is not None:return
        self.remote_pending.pop(ident,None)
        job.remote_target=target;job.portable['remote_resume_only']=True
        job.cancel=threading.Event();job.detach=threading.Event();job.state='Waiting';job.message='Reconnecting to host render…'
        self.bind(job)
        # Attach to existing host work while leaving never-submitted jobs paused.
        self.queue._start_job(job)

    def bind(self,job):
        if not job.portable:return
        owner=self.owner;spec=job.portable
        # Resolve credentials from current settings, never from the queue file.
        from .local_generation import load_profile
        profile=load_profile(owner.settings)
        tool=None
        if spec['type']=='artwork':
            from .artwork_tools import ArtworkTool
            provider=spec['provider']
            key=str(owner.session_keys.get(provider) or owner.settings.value(provider+'_key','') or '')
            tool=ArtworkTool(provider,spec['model'],key,{**profile,**spec.get('config',{})})
        def started():
            if job.source=='network':
                peers=[peer for peer in owner.network_render.peers.values() if peer.get('id')==job.remote_target.get('id')]
                if not peers:peers=[peer for peer in owner.network_render.peers.values() if peer.get('url')==job.remote_target.get('url')]
                if not peers and job.remote_target.get('token'):peers=[job.remote_target]
                if not peers:peers=[peer for peer in owner.network_render.peers.values() if peer['name']==job.remote_target.get('name')]
                if len(peers)!=1:raise RuntimeError('Reconnect to '+job.remote_target.get('name','the rendering computer')+' before resuming this job.')
                job.remote_target=copy.deepcopy(peers[0])
            if str(owner.current_project_id or '')==job.project_id:
                if job.kind=='Video':
                    owner.project_preview_panel._generation_autoplay=True
                    owner.local_video_log.clear();owner.project_preview_dock.show()
                    owner.timeline_preview.cancel();owner.grouped_sequence.timeline_active=False
                elif job.kind=='Image':
                    owner.image_generation_panel.begin();owner.image_generation_dock.show()
        def progress(event):
            if job.kind=='Video' and str(owner.current_project_id or '')==job.project_id:
                owner.local_video_log.receive_event(event)
                if event.get('kind')=='preview':owner.show_video_render_preview(event)
                elif event.get('kind')=='progress':owner.local_video_pill.update_event(event)
            elif job.kind=='Image' and str(owner.current_project_id or '')==job.project_id:
                owner.image_generation_panel.receive_event(event)
        def ready(result):
            if spec['type']=='video':
                workspace=(owner.workspace_revision,job.project_id,spec.get('workspace_type',owner.project_type))
                owner.attach_group_video(result['path'],int(job.render_group or 0),workspace,spec.get('attachment_revision',0))
                if str(owner.current_project_id or '')==job.project_id:
                    owner.project_preview_panel.set_project(job.title,result['path'],preserve_frame=True)
            elif result:
                # A restored artwork result remains accessible on its queue tile;
                # apply it only to an unchanged target in the originating project.
                record=spec['record'];path=result.get(record['id'])
                state=owner.project_sessions.get(job.project_id,{}).get('state',{})
                targets=owner.segments if owner.current_project_id==job.project_id else state.get('segments',[])
                if path:
                    target=next((item for item in targets if item.id==record['id']),None)
                    if target and target.artwork_prompt==record.get('artworkPrompt') and target.media_path==spec.get('source_path','') and target.prompt==spec.get('prompt_signature',target.prompt):
                        from .project_archive import clear_archived_source
                        target.media_path=target.preview_path=path;target.kind='image';target.role=target.artwork_target_role
                        target.artwork_status='complete';clear_archived_source(target)
                        if owner.current_project_id==job.project_id:owner.refresh_timeline();owner.mark_dirty()
                        else:owner.project_sessions[job.project_id]['dirty']=True
        def stopped():
            if job.kind=='Video' and str(owner.current_project_id or '')==job.project_id:
                owner.project_preview_panel.set_rendering(False);owner.grouped_sequence.show_group()
        def failed(error):
            owner.statusBar().showMessage(str(error),12000);stopped()
        def factory(cancel,mapping):
            def operation():
                if job.source=='network' and spec.get('remote_resume_only'):
                    # The host already owns the resolved inputs and seed. Only
                    # reconnect; local media/checkpoints need not still exist.
                    from .network_render import run_remote_request
                    from .local_generation import LOCAL_PROGRESS
                    return run_remote_request({},job.remote_target,LOCAL_PROGRESS.get(),cancel,86400)
                if spec['type']=='artwork':
                    from .artwork_tools import render_artwork
                    return render_artwork([copy.deepcopy(spec['record'])],tool)
                from .models import Segment
                from .project_archive import materialize_source
                from .local_conditioning import video_references
                from .local_generation import run_request
                segments=[]
                for row in spec.get('segments',[]):
                    segment=Segment.from_dict(row)
                    if row.get('_archive_source'):segment._archive_source=tuple(row['_archive_source'])
                    if segment.kind in {'image','video'}:materialize_source(segment)
                    segments.append(segment)
                request=copy.deepcopy(spec['request']);request['profile']={**profile,**request.get('profile',{})}
                conditioning=video_references(segments,[],request['duration'])
                continuation=spec.get('continuation')
                if continuation and (not segments or segments[0].kind=='text'):
                    from .video_extensions import continuation_reference
                    path=continuation.get('path','');previous=spec.get('previous_job')
                    parent=mapping.get(previous) or next((item for item in self.queue.jobs if item.id==previous),None)
                    if parent is not None:path=parent.output_path
                    reference=continuation_reference(path,continuation['duration'],continuation['overlap'],continuation.get('trim_start',0))
                    if request['profile'].get('video_variant')!='ref2va':reference.pop('audio_path',None)
                    reference['blend_side']=continuation.get('side','source')
                    conditioning.insert(0,reference)
                from .blend_conditioning import append_outgoing
                append_outgoing(conditioning,spec.get('outgoing'),request['duration'],request['profile'].get('video_variant'))
                request.update(conditioning=conditioning,references=copy.deepcopy(spec.get('references',[])))
                return run_request(request,cancel=cancel)
            def execute():
                from .network_render import REMOTE_TARGET
                token=REMOTE_TARGET.set(job.remote_target if job.source=='network' else None)
                try:return operation()
                finally:REMOTE_TARGET.reset(token)
            return execute
        job.operation=factory(job.cancel,{})
        job.args=();job.finished=ready;job.failed=failed;job.started=started;job.progress=progress;job.cancelled=stopped
        job.recipe=dict(operation=job.operation,args=(),finished=ready,failed=failed,progress=progress,started=started,cancelled=stopped,factory=factory,rebind=self.bind)
