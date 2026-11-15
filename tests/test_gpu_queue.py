"""Native queue admission against a real, legacy-shaped worker process."""
import subprocess
import sys
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from ltx_prompt_director.gpu_queue import director_workers, GpuLease
from ltx_prompt_director.render_queue import RenderQueue
from ltx_prompt_director.render_widgets import RenderQueuePanel

APP=QApplication.instance() or QApplication([])


def wait_for(condition):
    for _ in range(1000):
        APP.processEvents()
        if condition():return
        QTest.qWait(10)
    assert condition()


def test_foreign_legacy_worker_blocks_then_resumes_and_cloud_bypasses(tmp_path):
    # A separate parent owns this worker, matching an older Director instance.
    parent=subprocess.Popen([sys.executable,'-u','-c',
        "import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)','-m','ltx_prompt_director.local_worker','legacy/request.json']); print(p.pid,flush=True); sys.stdin.readline(); p.terminate(); p.wait()"],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    worker=int(parent.stdout.readline());runs=[]
    queue=RenderQueue(lambda *args,**kwargs:runs.append((args,kwargs)))
    queue._gpu_retry.setInterval(30)
    panel=RenderQueuePanel(queue);panel.resize(440,720);panel.show()
    def enqueue(source='local'):
        return queue.enqueue(lambda:None,(),lambda result:None,lambda error:None,title='GPU render',source=source,kind='Video',model='MiniMax H3',mode='fl2va')
    try:
        assert worker in director_workers()
        # Other real renders on the desktop are never touched by this fixture.
        real_scan=director_workers
        scanner=patch('ltx_prompt_director.gpu_queue.director_workers',side_effect=lambda:tuple(pid for pid in real_scan() if pid==worker))
        scanner.start()
        job=enqueue();wait_for(lambda:job.message.startswith('Waiting for GPU'))
        assert job.state=='Waiting' and not runs and job.started_at is None
        assert 'Waiting for GPU' in panel.summary.text()
        assert job.progress_event['message']=='Pending'
        QTest.qWait(60)
        assert panel.summary.height()>=panel.summary.heightForWidth(panel.summary.width())
        panel.grab().save(str(tmp_path/'gpu-wait.png'))
        queue.cancel_job(job.id);APP.processEvents();assert job.state=='Cancelled'
        # Cloud jobs never inspect or wait for local GPU availability.
        cloud=enqueue('cloud');wait_for(lambda:cloud.state=='Rendering');assert len(runs)==1
        runs.pop()[0][2](None);APP.processEvents()
        job=enqueue();wait_for(lambda:job.message.startswith('Waiting for GPU'))
        parent.stdin.write('\n');parent.stdin.flush();parent.wait(timeout=5)
        wait_for(lambda:job.state=='Rendering');assert len(runs)==1 and not job.message
        runs.pop()[0][2](None);wait_for(lambda:not queue._gpu_check_pending)
        assert job.state=='Complete'
        # An owned resident worker is reusable, rather than blocking itself.
        own=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)','-m','ltx_prompt_director.local_worker','own/request.json'])
        try:assert own.pid not in director_workers()
        finally:own.terminate();own.wait()
    finally:
        if parent.poll() is None:parent.stdin.write('\n');parent.stdin.flush();parent.wait(timeout=5)
        queue.shutdown();panel.close()
        if 'scanner' in locals():scanner.stop()
        parent.stdin.close();parent.stdout.close()


def test_lease_excludes_simultaneous_updated_instances_and_releases():
    first=GpuLease();second=GpuLease()
    try:
        assert first.acquire() and not second.acquire()
        first.release();assert second.acquire()
    finally:first.release();second.release()


def test_pause_and_delete_during_scan_never_start_stale_job():
    import threading
    entered=threading.Event();release=threading.Event();runs=[]
    def scan():entered.set();release.wait(3);return ()
    queue=RenderQueue(lambda *args,**kwargs:runs.append(args))
    with patch('ltx_prompt_director.gpu_queue.director_workers',side_effect=scan):
        try:
            job=queue.enqueue(lambda:None,(),lambda value:None,lambda error:None,title='Cancel during scan',source='local')
            wait_for(entered.is_set);queue.set_paused(True);queue.delete_job(job.id);release.set()
            wait_for(lambda:not queue._gpu_check_pending)
            assert not runs and not queue.jobs and queue._gpu_lease.stream is None
        finally:release.set();queue.shutdown()
