"""A GUI-thread FIFO for local and cloud renders, with cooperative cancellation."""
from dataclasses import dataclass, field
import threading
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


class RenderQueue(QObject):
    changed=Signal()

    def __init__(self,executor,parent=None):
        super().__init__(parent);self.executor=executor;self.jobs=[];self.active=None
        self.paused=False;self.closing=False
        self._notify=QTimer(self);self._notify.setSingleShot(True);self._notify.setInterval(60)
        self._notify.timeout.connect(self.changed)

    def enqueue(self,operation,args,finished,failed,*,title,project_id='',source='local',model='',kind='Image',details='',progress=None,cancel=None,started=None,cancelled=None,group_id='',group_title='',dependencies=(),intermediate=False,workspace_key=(),generation_number=0):
        if self.closing:raise RuntimeError('Render queue is closing')
        job=RenderJob(title,str(project_id or ''),source,model,kind,details,
                      operation=operation,args=args,finished=finished,failed=failed,
                      progress=progress,started=started,cancelled=cancelled,
                      group_id=group_id,group_title=group_title,dependencies=tuple(dependencies),intermediate=intermediate,
                      workspace_key=workspace_key,generation_number=generation_number)
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
        parents=[next((candidate for candidate in self.jobs if candidate.id==key),None) for key in job.dependencies]
        if any(parent is None or parent.state in {'Failed','Cancelled'} for parent in parents):
            self.cancel_job(job.id);QTimer.singleShot(0,self._advance);return
        if any(parent.state!='Complete' for parent in parents):return
        if job.cancel.is_set():self.cancel_job(job.id);QTimer.singleShot(0,self._advance);return
        self.active=job;job.state='Rendering';self.changed.emit()
        def progress(event):
            if job is not self.active:return
            if event.get('kind')=='progress':
                value=event.get('percent')
                if value is not None:
                    try:job.percent=max(0,min(100,float(value)))
                    except (TypeError,ValueError):pass
                job.message=str(event.get('message') or event.get('stage') or '')[:500]
                self._notify.start()
            if job.progress and not job.cancel.is_set():job.progress(event)
        def finish(result=None,error=None):
            if self.active is not job:return
            callback=None;value=None
            if job.cancel.is_set():
                job.state='Cancelled';callback=job.cancelled
            elif error is not None:
                job.state='Failed';job.error=str(error);callback=job.failed;value=error
            else:
                job.state='Complete';job.percent=100;callback=job.finished;value=result
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
                terminal=[j for j in self.jobs if j.state not in ACTIVE_STATES]
                if len(terminal)>100:
                    protected={j.group_id for j in self.jobs if j.state in ACTIVE_STATES and j.group_id}
                    stale={j.id for j in terminal[:-100] if not j.group_id or j.group_id not in protected};self.jobs[:]=[j for j in self.jobs if j.id not in stale]
                self.changed.emit();QTimer.singleShot(0,self._advance)
        try:
            if job.started:job.started()
            self.executor(job.operation,job.args,lambda result:finish(result),lambda error:finish(error=error),progress=progress,cancel=job.cancel)
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

    def shutdown(self):
        self.closing=True
        for job in tuple(self.jobs):self.cancel_job(job.id)
