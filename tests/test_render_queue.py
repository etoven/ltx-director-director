"""Deterministic queue scheduling checks; no model or provider requests."""
import pytest
from PySide6.QtWidgets import QApplication
from ltx_prompt_director.render_queue import RenderQueue

APP=QApplication.instance() or QApplication([])


def setup():
    runs=[]
    def execute(operation,args,ready,failed,**options):runs.append((operation,args,ready,failed,options))
    return RenderQueue(execute),runs


def tick():
    for _ in range(4):APP.processEvents()


def submit(queue,name,source='local',**callbacks):
    return queue.enqueue(lambda:name,(),callbacks.pop('finished',lambda _:None),callbacks.pop('failed',lambda _:None),title=name,source=source,model=name,**callbacks)


def test_local_and_cloud_share_fifo_and_release_snapshots():
    queue,runs=setup();results=[]
    first=submit(queue,'H3',kind='Video',project_id='one',finished=results.append)
    second=submit(queue,'Gemini',source='cloud',project_id='two')
    third=submit(queue,'Flux',project_id='one')
    assert queue.position(second)==1
    tick();assert first.state=='Rendering' and second.state=='Waiting' and len(runs)==1
    assert queue.for_project('one') is first and queue.position(second)==1
    runs[0][2]('video.mp4');tick();assert results==['video.mp4'] and second.state=='Rendering'
    assert first.operation is None and first.args==()
    runs[1][2]('cloud.png');tick();assert third.state=='Rendering' and len(runs)==3
    runs[2][2]('flux.png');tick();assert queue.active is None


def test_pause_waiting_cancel_and_active_cancel_wait_for_worker_ack():
    queue,runs=setup();cancelled=[];applied=[]
    queue.set_paused(True)
    first=submit(queue,'first',finished=applied.append,cancelled=lambda:cancelled.append('first'))
    second=submit(queue,'second',cancelled=lambda:cancelled.append('second'))
    third=submit(queue,'third')
    tick();assert not runs
    queue.cancel_job(second.id);assert second.state=='Cancelled' and cancelled==['second']
    queue.set_paused(False);tick();assert len(runs)==1
    queue.cancel_job(first.id);tick();assert first.state=='Cancelling' and len(runs)==1
    runs[0][2]('discarded.png');tick()
    assert not applied and cancelled==['second','first'] and third.state=='Rendering'
    queue.shutdown();runs[1][2]('discarded.png');tick();assert queue.active is None


def test_worker_failure_advances_and_shutdown_cancels_waiting_without_starting():
    queue,runs=setup();errors=[]
    first=submit(queue,'first',failed=errors.append);second=submit(queue,'second');third=submit(queue,'third')
    tick();runs[0][3]('Model failed');tick()
    assert first.state=='Failed' and errors==['Model failed'] and second.state=='Rendering'
    queue.shutdown();assert third.state=='Cancelled' and second.state=='Cancelling'
    runs[1][3]('Cancelled');tick();assert len(runs)==2 and queue.active is None
    with pytest.raises(RuntimeError):submit(queue,'late')


def test_progress_and_synchronous_executor_failure_are_reported():
    queue,runs=setup();events=[]
    job=submit(queue,'image',progress=events.append);tick()
    runs[0][4]['progress']({'kind':'progress','percent':42,'message':'Sampling'})
    assert job.percent==42 and job.message=='Sampling' and len(events)==1
    queue.cancel_job(job.id);runs[0][4]['progress']({'kind':'preview','path':'obsolete.png'})
    assert len(events)==1
    runs[0][2]('ignored');tick()
    failures=[]
    def broken(*args,**kwargs):raise ValueError('launch failed')
    queue.executor=broken;job=submit(queue,'broken',failed=failures.append);tick()
    assert job.state=='Failed' and failures==['launch failed'] and queue.active is None


def test_cloud_cancel_checks_before_request_and_before_writing_output(tmp_path,monkeypatch):
    import threading
    from unittest.mock import Mock
    from ltx_prompt_director import artwork_tools as artwork
    from ltx_prompt_director.local_generation import LOCAL_CANCEL
    monkeypatch.setenv('XDG_DATA_HOME',str(tmp_path))
    cancel=threading.Event();cancel.set();request=Mock()
    monkeypatch.setattr(artwork,'_gemini_image',request)
    token=LOCAL_CANCEL.set(cancel)
    try:
        with pytest.raises(RuntimeError,match='cancelled'):
            artwork.render_artwork([{'id':'one','artworkPrompt':'A mountain'}],artwork.ArtworkTool('gemini','test','unused'))
        request.assert_not_called()
        cancel.clear()
        def response(*args,**kwargs):cancel.set();return b'provider result'
        monkeypatch.setattr(artwork,'_gemini_image',response)
        with pytest.raises(RuntimeError,match='cancelled'):
            artwork.render_artwork([{'id':'one','artworkPrompt':'A mountain'}],artwork.ArtworkTool('gemini','test','unused'))
        assert not list(tmp_path.rglob('*.png'))
    finally:LOCAL_CANCEL.reset(token)
