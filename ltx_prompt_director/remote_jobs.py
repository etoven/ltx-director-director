"""Detached host mailboxes and durable, credential-free client job receipts."""
from collections import deque
import json
import copy
from pathlib import Path
import threading

FEATURE='detached-jobs-v1'


class DetachedJob:
    def __init__(self,request_id):
        self.request_id=request_id;self.job_id=None;self.sequence=0;self.acknowledged=False
        self.condition=threading.Condition();self.logs=deque(maxlen=32)
        self.preview=None;self.progress=None;self.terminal=None
    def put(self,item):
        kind,value=item
        with self.condition:
            self.sequence+=1;event=(self.sequence,kind,value)
            if kind=='event' and value.get('kind')=='preview':self.preview=event
            elif kind=='event' and value.get('kind')=='progress':self.progress=event
            elif kind in {'result','error','retry'}:self.terminal=event
            else:self.logs.append(event)
            self.condition.notify_all()
    def updates(self,after):
        with self.condition:
            self.condition.wait_for(lambda:self.sequence>after,timeout=.05)
            events=[*self.logs,self.progress,self.preview,self.terminal]
            return sorted((event for event in events if event and event[0]>after),key=lambda event:event[0])


def receipt_directory():
    from .local_generation import data_root
    root=data_root()/'remote-sessions';root.mkdir(parents=True,exist_ok=True,mode=0o700)
    return root


def read_receipt(request_id):
    try:return json.loads((receipt_directory()/(request_id+'.json')).read_text(encoding='utf-8'))
    except (OSError,ValueError):return None


def save_receipt(request_id,value):
    from .queue_persistence import write_snapshot,clean
    write_snapshot(receipt_directory()/(request_id+'.json'),clean(value))


def load_queue_with_receipts(path):
    path=Path(path)
    value=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else dict(version=1,jobs=[],paused=True)
    by_id={row['id']:row for row in value.get('jobs',[])}
    receipts={}
    for source in receipt_directory().glob('*.json'):
        try:receipt=json.loads(source.read_text(encoding='utf-8'))
        except (OSError,ValueError):continue
        record=receipt.get('record')
        if not record:continue
        if record['id'] not in by_id and not (path.parent/(record.get('project_id','')+'.meta.json')).is_file():continue
        if receipt.get('status') in {'failed','cancelled'}:
            if record['id'] not in by_id:
                row=copy.deepcopy(record);row['state']='Failed' if receipt['status']=='failed' else 'Cancelled'
                row['error']=receipt.get('message','');value.setdefault('jobs',[]).append(row);by_id[row['id']]=row
        if receipt.get('status') in {'created','submitted','downloaded','cancel_requested'}:
            row=by_id.get(record['id'])
            if row is None:value.setdefault('jobs',[]).append(record);by_id[record['id']]=record
            row.setdefault('portable',{}).setdefault('remote_request_id',receipt['request_id'])
            receipts[receipt['request_id']]=receipt
    # The queue snapshot is itself a durable receipt. A separate receipt may
    # be absent after interrupted writes, migration or restoring a backup.
    for row in value.get('jobs',[]):
        request_id=row.get('portable',{}).get('remote_request_id')
        if row.get('source')=='network' and request_id and row.get('state') in {'Waiting','Rendering','Cancelling'}:
            receipts.setdefault(request_id,dict(request_id=request_id,record=copy.deepcopy(row),
                target=copy.deepcopy(row.get('remote_target',{})),status='submitted'))
    value['remote_receipts']=receipts
    return value


def prune_receipts(snapshot):
    """Only discard receipts after their terminal queue state is durable."""
    for row in snapshot.get('jobs',[]):
        request_id=row.get('portable',{}).get('remote_request_id')
        if not request_id:continue
        receipt=read_receipt(request_id)
        if receipt and ((row.get('state')=='Complete' and receipt.get('status')=='downloaded') or
                        (row.get('state')=='Cancelled' and receipt.get('status')=='cancelled')):
            (receipt_directory()/(request_id+'.json')).unlink(missing_ok=True)
