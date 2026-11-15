"""A GUI-thread FIFO for local and cloud renders, with cooperative cancellation."""
from dataclasses import dataclass, field
import threading
import math
import time
from uuid import uuid4
from PySide6.QtCore import QObject, QTimer, Signal

ACTIVE_STATES={'Waiting','Rendering','Cancelling'}


@dataclass
class RenderJob:
    title: str
    project_id: str
    source: str
    model: str
    kind: str
    details: str = ''
    id: str = field(default_factory=lambda:uuid4().hex)
    workspace_key: tuple = ()
    generation_number: int = 0
    group_id: str = ''
    group_title: str = ''
    dependencies: tuple = ()
    output_path: str = ''
    intermediate: bool = False
    state: str = 'Waiting'
    percent: float | None = None
    message: str = ''
    error: str = ''
    cancel: threading.Event = field(default_factory=threading.Event,repr=False)
    operation: object = field(default=None,repr=False)
    args: tuple = field(default_factory=tuple,repr=False)
    finished: object = field(default=None,repr=False)
    failed: object = field(default=None,repr=False)
    progress: object = field(default=None,repr=False)
    started: object = field(default=None,repr=False)
    cancelled: object = field(default=None,repr=False)
    mode: str = ''
    eta_seconds: float | None = None
    expected_seconds: float | None = None
    started_at: float | None = None
    elapsed_seconds: float | None = None
    reused: bool = False

    @property
    def progress_event(self):
        if self.state=='Waiting':
            return dict(percent=None,eta_seconds=None,message='Pending')
        return dict(percent=self.percent,
                    eta_seconds=self.eta_seconds if self.state=='Rendering' else None,
                    message=self.message or 'Preparing…')

    @property
    def model_label(self):
        mode={'fl2va':'FL2VA','ref2va':'Ref2VA'}.get(self.mode,'')
        return f'{mode} · {self.model}' if mode else self.model


class RenderQueue(QObject):
    changed=Signal()

    def __init__(self,executor,parent=None):
        super().__init__(parent);self.executor=executor;self.jobs=[];self.active=None
        self.paused=False;self.closing=False
        self._removed_states = {}
        self._remove_when_finished = set()
        self._notify=QTimer(self);self._notify.setSingleShot(True);self._notify.setInterval(60)
        self._notify.timeout.connect(self.changed)
        self.changed.connect(self.update_estimates)

    @property
    def remaining_video_jobs(self):
        return [job for job in self.jobs if job.kind=='Video' and job.state in {'Waiting','Rendering'}]

    @property
    def estimated_video_seconds(self):
        jobs=self.remaining_video_jobs
        if any(job.expected_seconds is None for job in jobs):return None
        return sum(job.expected_seconds for job in jobs)

    def update_estimates(self):
        jobs=self.remaining_video_jobs
        current=self.active if self.active in jobs else None
        fallback=current.eta_seconds if current is not None else None
        now=time.monotonic()
        for job in jobs:
            completed=[other for other in self.jobs if other.state=='Complete' and other.kind==job.kind
                       and other.model==job.model and other.source==job.source and not other.reused
                       and other.elapsed_seconds is not None and other.elapsed_seconds>0]
            batch=[other for other in completed if job.group_id and other.group_id==job.group_id]
            history=batch or completed
            average=sum(other.elapsed_seconds for other in history)/len(history) if history else None
            if job.state=='Rendering' and job.eta_seconds is not None:
                estimate=job.eta_seconds
            elif average is not None:
                elapsed=max(0,now-job.started_at) if job.state=='Rendering' and job.started_at is not None else 0
                estimate=max(0,average-elapsed)
            else:estimate=fallback
            job.expected_seconds=estimate

    def enqueue(self,operation,args,finished,failed,*,title,project_id='',source='local',model='',kind='Image',details='',progress=None,cancel=None,started=None,cancelled=None,group_id='',group_title='',dependencies=(),intermediate=False,workspace_key=(),generation_number=0,mode=''):
        if self.closing:raise RuntimeError('Render queue is closing')
        job=RenderJob(title,str(project_id or ''),source,model,kind,details,
                      operation=operation,args=args,finished=finished,failed=failed,
                      progress=progress,started=started,cancelled=cancelled,
                      group_id=group_id,group_title=group_title,dependencies=tuple(dependencies),intermediate=intermediate,
                      workspace_key=workspace_key,generation_number=generation_number,mode=mode)
        if cancel is not None:job.cancel=cancel
        self.jobs.append(job);self.changed.emit();self._advance()
        return job

    def set_paused(self,paused):
        self.paused=bool(paused);self.changed.emit()
        if not self.paused:QTimer.singleShot(0,self._advance)

    def position(self,job):
        waiting=[j for j in self.jobs if j.state=='Waiting']
        return waiting.index(job)+1 if job in waiting else 0

    def for_project(self,project_id):
        if not project_id:return None
        jobs=[j for j in self.jobs if j.project_id==str(project_id) and j.state in ACTIVE_STATES]
        return next((j for j in jobs if j.state!='Waiting'),jobs[0] if jobs else None)

    def _advance(self):
        if self.closing or self.paused or self.active is not None:return
        job=next((j for j in self.jobs if j.state=='Waiting'),None)
        if job is None:return
        states = {candidate.id: candidate.state for candidate in self.jobs}
        parents = [states.get(key, self._removed_states.get(key)) for key in job.dependencies]
        if any(state is None or state in {'Failed','Cancelled'} for state in parents):
            self.cancel_job(job.id);QTimer.singleShot(0,self._advance);return
        if any(state!='Complete' for state in parents):return
        if job.cancel.is_set():self.cancel_job(job.id);QTimer.singleShot(0,self._advance);return
        self.active=job;job.state='Rendering';job.started_at=time.monotonic();self.changed.emit()
        def progress(event):
            if job is not self.active:return
            if event.get('kind')=='progress':
                value=event.get('percent')
                if value is not None:
                    try:job.percent=max(0,min(100,float(value)))
                    except (TypeError,ValueError):pass
                if 'eta_seconds' in event:
                    eta=event.get('eta_seconds')
                    job.eta_seconds=float(eta) if isinstance(eta,(int,float)) and math.isfinite(eta) and eta>0 else None
                job.message=str(event.get('message') or event.get('stage') or '')[:500]
                self._notify.start()
            if job.progress and not job.cancel.is_set():job.progress(event)
        def finish(result=None,error=None):
            if self.active is not job:return
            callback=None;value=None;job.eta_seconds=None
            if job.cancel.is_set():
                job.state='Cancelled';callback=job.cancelled
            elif error is not None:
                job.state='Failed';job.error=str(error);callback=job.failed;value=error
            else:
                job.state='Complete';job.percent=100;callback=job.finished;value=result
                job.elapsed_seconds=max(0,time.monotonic()-job.started_at) if job.started_at is not None else None
                job.reused=isinstance(result,dict) and bool(result.get('reused'))
                if isinstance(result,dict):
                    path=result.get('path')
                    if not path and job.kind=='Image':path=next((value for value in result.values() if isinstance(value,str)), '')
                    job.output_path=str(path or '')
            try:
                if callback:
                    if job.state=='Cancelled':callback()
                    else:callback(value)
            finally:
                self.active=None
                # Release request snapshots and credentials after the worker has ended.
                job.operation=job.finished=job.failed=job.progress=job.started=job.cancelled=None;job.args=()
                if job.id in self._remove_when_finished:
                    self._remove_entries({job.id})
                terminal=[j for j in self.jobs if j.state not in ACTIVE_STATES]
                if len(terminal)>100:
                    protected={j.group_id for j in self.jobs if j.state in ACTIVE_STATES and j.group_id}
                    stale={j.id for j in terminal[:-100] if not j.group_id or j.group_id not in protected}
                    self._remove_entries(stale)
                self.changed.emit();QTimer.singleShot(0,self._advance)
        try:
            if job.started:job.started()
            def operation(*args):
                from .local_generation import LOCAL_KEEP_LOADED
                token=LOCAL_KEEP_LOADED.set(job.source=='local')
                try:return job.operation(*args)
                finally:LOCAL_KEEP_LOADED.reset(token)
            self.executor(operation,job.args,lambda result:finish(result),lambda error:finish(error=error),progress=progress,cancel=job.cancel)
        except Exception as error:finish(error=str(error))

    def cancel_job(self,job_id):
        job=next((j for j in self.jobs if j.id==job_id),None)
        if job is None or job.state not in ACTIVE_STATES:return
        job.cancel.set()
        if job is self.active:job.state='Cancelling'
        else:
            job.state='Cancelled'
            try:
                if job.cancelled:job.cancelled()
            finally:
                job.operation=job.finished=job.failed=job.progress=job.started=job.cancelled=None;job.args=()
        self.changed.emit()

    def _remove_entries(self, ids):
        for job in self.jobs:
            if job.id in ids:
                self._removed_states[job.id] = job.state
        self.jobs[:] = [job for job in self.jobs if job.id not in ids]
        self._remove_when_finished.difference_update(ids)
        needed = {key for job in self.jobs if job.state in ACTIVE_STATES for key in job.dependencies}
        self._removed_states = {key: state for key, state in self._removed_states.items() if key in needed}

    def sweep(self):
        self._remove_entries({job.id for job in self.jobs if job.state not in ACTIVE_STATES})
        self.changed.emit()
        QTimer.singleShot(0, self._advance)

    def delete_job(self, job_id):
        ids = {job.id for job in self.jobs if job.id == job_id or job.group_id == job_id}
        while True:
            dependents = {job.id for job in self.jobs if ids.intersection(job.dependencies)}
            if dependents <= ids:
                break
            ids.update(dependents)
        self._remove_when_finished.update(ids)
        for job in tuple(self.jobs):
            if job.id in ids:
                self.cancel_job(job.id)
        self._remove_entries({job.id for job in self.jobs if job.id in ids and job is not self.active})
        self.changed.emit()
        QTimer.singleShot(0, self._advance)

    def move_job(self, job_id, before_id=None):
        moving = [job for job in self.jobs if job.id == job_id or job.group_id == job_id]
        if not moving or any(job.state != 'Waiting' for job in moving):
            return False
        ids = {job.id for job in moving}
        if before_id in ids:
            return False
        remaining = [job for job in self.jobs if job.id not in ids]
        position = next((index for index, job in enumerate(remaining)
                         if job.id == before_id or job.group_id == before_id), len(remaining))
        candidate = remaining[:position] + moving + remaining[position:]
        positions = {job.id: index for index, job in enumerate(candidate)}
        if self.active and positions[self.active.id] != self.jobs.index(self.active):
            return False
        for job in candidate:
            if job.state == 'Waiting' and any(positions.get(key, -1) >= positions[job.id]
                                               for key in job.dependencies):
                return False
        # Generation cards stay contiguous when moving their individual blocks.
        groups = {}
        for index, job in enumerate(candidate):
            if job.group_id:
                groups.setdefault(job.group_id, []).append(index)
        if any(indices != list(range(indices[0], indices[-1] + 1)) for indices in groups.values()):
            return False
        self.jobs[:] = candidate
        self.changed.emit()
        QTimer.singleShot(0, self._advance)
        return True

    def shutdown(self):
        self.closing=True
        for job in tuple(self.jobs):self.cancel_job(job.id)
        from .local_session import MANAGER
        MANAGER.release_async()
