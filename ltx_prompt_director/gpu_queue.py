"""Process-based local-render admission, including pre-admission Director builds."""
from pathlib import Path
import os
import tempfile
import psutil


def director_workers():
    """Return foreign inference workers, without reading requests or credentials."""
    workers=[]
    for process in psutil.process_iter(['pid','cmdline','status']):
        try:
            args=process.info['cmdline'] or []
            module=any(args[i:i+2]==['-m','ltx_prompt_director.local_worker'] for i in range(len(args)-1))
            script=any(Path(arg).name=='local_worker.py' and Path(arg).parent.name=='ltx_prompt_director' for arg in args[1:])
            if not (module or script) or process.info['status']==psutil.STATUS_ZOMBIE:continue
            # Our own warm worker is reusable by this queue; another app's is not.
            if process.pid==os.getpid() or process.ppid()==os.getpid():continue
            workers.append(process.pid)
        except (psutil.NoSuchProcess,psutil.AccessDenied,psutil.ZombieProcess):continue
    return tuple(workers)


class GpuLease:
    """Nonblocking OS lease closes the scan-to-worker-start race in newer builds."""
    def __init__(self):self.stream=None
    def acquire(self):
        if self.stream is not None:return True
        # Independent of XDG profiles, engine location and application version.
        directory=Path(tempfile.gettempdir())/('prompt-director-gpu-'+str(getattr(os,'getuid',lambda:os.environ.get('USERNAME','user'))()))
        directory.mkdir(mode=0o700,exist_ok=True)
        stream=(directory/'render.lock').open('a+b')
        try:
            if os.name=='nt':
                import msvcrt
                stream.seek(0);stream.write(b'0');stream.flush();stream.seek(0)
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:stream.close();return False
        self.stream=stream;return True
    def release(self):
        if self.stream is not None:self.stream.close();self.stream=None
