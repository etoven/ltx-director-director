"""Serialized, cancellable ownership of one warm inference process."""
import atexit
import contextlib
from pathlib import Path
import json
import os
import queue
import shutil
import signal
import subprocess
import threading
import time


class WorkerError(RuntimeError):
    def __init__(self,message,*,memory_pressure=False):
        super().__init__(message);self.memory_pressure=memory_pressure


class Session:
    def __init__(self,key,command,env,job,runtime):
        self.key=key;self.runtime=Path(runtime);self.events=queue.Queue();self.readers=[]
        self.process=subprocess.Popen([*command,'--resident'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,text=True,errors='replace',env=env,cwd=str(job),start_new_session=os.name!='nt')
        for stream,protocol in ((self.process.stdout,True),(self.process.stderr,False)):
            thread=threading.Thread(target=self._read,args=(stream,protocol),daemon=True);thread.start();self.readers.append(thread)
    def _read(self,stream,protocol):
        try:
            for line in stream:
                if protocol and line.startswith('DIRECTOR_EVENT '):
                    try:self.events.put(json.loads(line[len('DIRECTOR_EVENT '):]));continue
                    except (ValueError,TypeError):pass
                if line.strip():self.events.put(dict(kind='log',message=line.rstrip()[-4000:]))
        except (ValueError,OSError):pass
        finally:self.events.put(None)
    def submit(self,path):
        while not self.events.empty():self.events.get_nowait()
        self.process.stdin.write(str(path)+'\n');self.process.stdin.flush()
    def close(self):
        process=self.process
        if process.poll() is None:
            try:
                if os.name!='nt':os.killpg(process.pid,signal.SIGTERM)
                else:process.terminate()
            except ProcessLookupError:pass
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name!='nt':os.killpg(process.pid,signal.SIGKILL)
                else:subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
                process.wait()
        for thread in self.readers:thread.join(timeout=1)
        for stream in (process.stdin,process.stdout,process.stderr):
            if stream:stream.close()
        shutil.rmtree(self.runtime,ignore_errors=True)


class ModelSession:
    def __init__(self):self.lock=threading.Lock();self.current=None;self.reused=False
    @contextlib.contextmanager
    def access(self,cancel,timeout):
        start=time.monotonic()
        if cancel is not None and cancel.is_set():raise InterruptedError('Local generation cancelled.')
        while not self.lock.acquire(timeout=.1):
            if cancel is not None and cancel.is_set():raise InterruptedError('Local generation cancelled.')
            if time.monotonic()-start>timeout:raise TimeoutError('Waiting for the local engine timed out.')
        try:yield self
        finally:self.lock.release()
    def close(self):
        current,self.current=self.current,None
        if current:current.close()
    def release_async(self):
        target=self.current
        def release():
            with self.lock:
                if self.current is target:self.close()
        threading.Thread(target=release,daemon=True).start()

MANAGER=ModelSession()
atexit.register(MANAGER.close)
