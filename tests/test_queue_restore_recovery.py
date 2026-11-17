"""Queue entries remain visible independently of remote discovery and project files."""
import json
from uuid import uuid4
from PySide6.QtCore import QObject, Signal
from ltx_prompt_director.queue_persistence import QueueStore
from ltx_prompt_director.remote_jobs import save_receipt
from test_network_render import Window, APP


class Network(QObject):
    peer_found = Signal(object)
    peers = {}


def test_restore_keeps_pending_and_failed_rows_and_orphan_remote_receipt(tmp_path):
    request_id=uuid4().hex
    def row(title,**extra):
        return dict(id=uuid4().hex,title=title,project_id='temporarily-missing-project',
                    source='network',model='MiniMax H3',kind='Video',state='Waiting',**extra)
    pending=row('Pending',portable=dict(type='video',request=dict(kind='video')))
    broken=row('Bad artwork inputs',portable=dict(type='artwork'))
    malformed=dict(id=uuid4().hex,state='Waiting')
    remote=row('Remote job',portable=dict(type='video',remote_request_id=request_id,request={}))
    path=tmp_path/'render-queue.json'
    path.write_text(json.dumps(dict(version=1,paused=True,jobs=[pending,broken,malformed])))
    save_receipt(request_id,dict(request_id=request_id,record=remote,status='submitted',target={}))
    owner=Window();owner.session_keys={};owner.network_render=Network(owner)
    def disk(operation,args,finished,failed,**options):
        try:result=operation(*args)
        except Exception as error:failed(str(error));return
        finished(result)
    owner.start_disk_job=disk
    store=QueueStore(owner,path)
    try:
        jobs=owner.render_queue.jobs
        assert [job.id for job in jobs]==[pending['id'],broken['id'],remote['id']]
        assert [job.state for job in jobs]==['Waiting','Failed','Waiting']
        assert owner.render_queue_panel.list.count()==3
        assert remote['id'] in store.remote_pending
        assert store.snapshot()['jobs'][-1]==malformed
        assert not store.frozen
    finally:
        store.frozen=True;store.timer.stop();store.remote_timer.stop();owner.close()
