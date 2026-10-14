"""Atomic, disposable preview workfiles under the platform's per-user cache."""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import threading
import time
import uuid
import zipfile
from .media import APP_CACHE

DEFAULT_ROOT=APP_CACHE/'shot-sequences'/'v1'


class Workfiles:
    def __init__(self,root=DEFAULT_ROOT,max_bytes=2*1024**3):
        self.root=Path(root) if root is not None else None
        self.max_bytes=max_bytes;self.known=set();self.lock=threading.RLock();self.last_prune=0

    def path(self,key):
        digest=hashlib.sha256(repr(key).encode()).hexdigest()
        return self.root/digest[:2]/(digest+'.work') if self.root is not None else None

    def contains_known(self,key):
        with self.lock:return key in self.known

    def read(self,key):
        path=self.path(key)
        if path is None:return None
        try:
            data=path.read_bytes()
            if key[0]=='source':
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    meta=json.loads(archive.read('manifest.json'))
                    count=int(meta['blocks']);frames=int(meta['frames'])
                    if not 0<count<=100000 or not 0<frames<=2400000:raise ValueError('Invalid preview workfile')
                    result=dict(frames=frames,blocks=[archive.read(f'video/{i}.webm') for i in range(count)],
                                audio=archive.read('audio.pcm.zlib'),thumbs=[archive.read(f'thumbnails/{i}.jpg') for i in range(count)])
            else:
                if not data.startswith(b'\x1aE\xdf\xa3'):raise ValueError('Invalid preview workfile')
                result=data
            with self.lock:self.known.add(key)
            try:os.utime(path,None)
            except OSError:pass
            return result
        except FileNotFoundError:
            with self.lock:self.known.discard(key)
            return None
        except (OSError,ValueError,KeyError,zipfile.BadZipFile):
            try:path.unlink(missing_ok=True)
            except OSError:pass
            with self.lock:self.known.discard(key)
            return None

    def write(self,key,value):
        path=self.path(key)
        if path is None:return
        if key[0]=='source':
            memory=io.BytesIO()
            with zipfile.ZipFile(memory,'w',compression=zipfile.ZIP_STORED) as archive:
                archive.writestr('manifest.json',json.dumps(dict(frames=value['frames'],blocks=len(value['blocks']))))
                archive.writestr('audio.pcm.zlib',value['audio'])
                for i,(data,thumb) in enumerate(zip(value['blocks'],value['thumbs'])):
                    archive.writestr(f'video/{i}.webm',data);archive.writestr(f'thumbnails/{i}.jpg',thumb)
            data=memory.getvalue()
        else:data=value
        temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            temporary.write_bytes(data);os.replace(temporary,path)
            with self.lock:self.known.add(key)
            if time.monotonic()-self.last_prune>60:self.prune()
        except OSError:
            # A full or unavailable cache directory must not break playback.
            pass
        finally:
            try:temporary.unlink(missing_ok=True)
            except OSError:pass

    def remove(self,key):
        path=self.path(key)
        if path is not None:
            try:path.unlink(missing_ok=True)
            except OSError:pass
        with self.lock:self.known.discard(key)

    def prune(self):
        if self.root is None:return
        with self.lock:self.last_prune=time.monotonic()
        # Directory scans and deletions run in the worker without holding the
        # metadata lock queried by the GUI's coverage line.
        files=[]
        for path in self.root.glob('*/*.work'):
            try:
                stat=path.stat();files.append((stat.st_mtime_ns,stat.st_size,path))
            except OSError:pass
        total=sum(size for _,size,_ in files)
        if total<=self.max_bytes:return
        for _,size,path in sorted(files):
            try:path.unlink();total-=size
            except OSError:pass
            if total<=self.max_bytes:break
        with self.lock:known=set(self.known)
        deleted={key for key in known if not self.path(key).is_file()}
        with self.lock:self.known.difference_update(deleted)
