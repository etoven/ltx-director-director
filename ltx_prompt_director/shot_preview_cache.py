"""Preview workfiles and bounded RAM reuse of sources and content-addressed joins.

Only dirty video regions are encoded. Continuous player buffers are assembled by
remuxing cached VP9 packets, with one continuous Opus stream from cached source PCM.
Reusable blocks live in the platform cache; the assembled player buffer lives in RAM.
"""
from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import io
from pathlib import Path
import threading
import time
import zlib

import av
import numpy as np
from PIL import Image

from .project_archive import materialize_source
from .shot_sequence import FPS, RATE, settings, overlaps, _frames, _audio, blend_frame
from .shot_workfiles import Workfiles, DEFAULT_ROOT

BLOCK = FPS
SAMPLES = RATE // FPS


def _hash(value):return hashlib.sha256(repr(value).encode()).hexdigest()


def _check(cancel):
    if cancel is not None and cancel.is_set():raise RuntimeError('Shot sequence operation cancelled.')


def source_identity(shot):
    digest=getattr(shot,'_media_digest','')
    archive=getattr(shot,'_archive_source',None)
    if digest:return ('digest',digest)
    if archive:return ('archive',*archive)
    path=Path(shot.media_path)
    try:
        stat=path.stat()
        return (str(path.resolve()),stat.st_size,stat.st_mtime_ns)
    except OSError:return (str(path),None)


@dataclass(frozen=True)
class Part:
    start: int
    frames: tuple
    key: str


@dataclass
class Plan:
    sources: dict
    parts: list
    count: int
    width: int
    height: int
    signature: tuple


def _shape(frame):
    if frame[0]=='source':return frame[:2]
    return frame[:3]+(frame[4],_shape(frame[5]),_shape(frame[6]))


def make_plan(shots,options):
    opts=settings(options)
    scale=min(1,640/opts['width'],360/opts['height'])
    width=max(32,round(opts['width']*scale)//2*2)
    height=max(32,round(opts['height']*scale)//2*2)
    sources={};clips=[]
    for shot in shots:
        count=max(1,round(shot.duration*FPS))
        key=_hash((source_identity(shot),count,width,height))
        sources[key]=shot
        clips.append({'frames':count,'key':key})
    joins,count=overlaps(clips,opts['overlap'])
    frames=[]
    for index,clip in enumerate(clips):
        destination=[('source',clip['key'],i) for i in range(clip['frames'])]
        amount=joins[index-1] if index else 0
        if not amount:frames+=destination
        elif opts['blend']=='cut':
            frames=frames+destination[amount:] if opts['side']=='destination' else frames[:-amount]+destination
        else:
            tail=frames[-amount:]
            mixed=[('blend',opts['blend'],opts['side'],i+1,amount+1,source,destination[i]) for i,source in enumerate(tail)]
            frames=frames[:-amount]+mixed+destination[amount:]
    parts=[];start=0
    while start<len(frames):
        first=frames[start];shape=_shape(first)
        anchor=first[2] if first[0]=='source' else first[3]-1
        end=min(len(frames),start+BLOCK-anchor%BLOCK)
        for i in range(start+1,end):
            if _shape(frames[i])!=shape:end=i;break
        block=tuple(frames[start:end]);key=_hash((width,height,block))
        parts.append(Part(start,block,key));start=end
    return Plan(sources,parts,count,width,height,tuple((part.key,len(part.frames)) for part in parts))


def _encode(images,width,height,cancel):
    memory=io.BytesIO()
    with av.open(memory,'w',format='webm') as container:
        video=container.add_stream('libvpx-vp9',rate=FPS)
        video.width=width;video.height=height;video.pix_fmt='yuv420p';video.bit_rate=0
        video.options={'crf':'30','deadline':'realtime','cpu-used':'8'}
        for index,image in enumerate(images):
            _check(cancel)
            frame=av.VideoFrame.from_ndarray(image,format='rgb24')
            frame.pts=index;frame.time_base=Fraction(1,FPS)
            for packet in video.encode(frame):container.mux(packet)
        for packet in video.encode(None):container.mux(packet)
    return memory.getvalue()


def audio_peaks(pcm,bins=384):
    """Peak envelopes retain quiet sounds and short transients without normalization."""
    count=min(bins,pcm.shape[1])
    if not count:return []
    mono=np.max(np.abs(pcm),axis=0)
    return [min(1.0,float(np.max(part))) for part in np.array_split(mono,count)]


class PreviewCache:
    def __init__(self,max_bytes=128*1024*1024,root=DEFAULT_ROOT,max_disk_bytes=2*1024**3):
        self.max_bytes=max_bytes;self._entries=OrderedDict();self._bytes=0
        self._lock=threading.RLock()
        self.workfiles=Workfiles(root,max_disk_bytes)
        self.stats=dict(source_builds=0,region_builds=0,region_hits=0,assemblies=0)

    @property
    def byte_size(self):
        with self._lock:return self._bytes

    @staticmethod
    def size(value):
        if isinstance(value,bytes):return len(value)
        return sum(map(len,value['blocks']))+len(value['audio'])+sum(map(len,value['thumbs']))

    def memory_get(self,key):
        with self._lock:
            entry=self._entries.get(key)
            if entry is not None:self._entries.move_to_end(key);return entry[0]

    def contains(self,key):
        with self._lock:
            return key in self._entries or self.workfiles.contains_known(key)

    def get(self,key):
        # Disk access is exclusively used by compositor workers. The GUI queries
        # metadata through contains()/memory_get(), never large preview files.
        value=self.memory_get(key)
        if value is not None:return value
        value=self.workfiles.read(key)
        if value is not None:self._remember(key,value,self.size(value))
        return value

    def _remember(self,key,value,size):
        with self._lock:
            previous=self._entries.pop(key,None)
            if previous:self._bytes-=previous[1]
            if size>self.max_bytes:return
            self._entries[key]=(value,size);self._bytes+=size
            while self._bytes>self.max_bytes:
                _,(_,removed)=self._entries.popitem(last=False);self._bytes-=removed

    def put(self,key,value,size):
        self.workfiles.write(key,value)
        self._remember(key,value,size)

    def coverage(self,plan):
        return [(p.start/FPS,(p.start+len(p.frames))/FPS,self.contains(('region',p.key))) for p in plan.parts]

    def thumbnails(self,plan):
        return {key:(source['thumbs'],source.get('peaks',[])) for key in plan.sources if (source:=self.memory_get(('source',key))) is not None}

    def _source(self,key,plan,cancel,notify=None):
        cached=self.get(('source',key))
        if cached is not None:
            if 'peaks' not in cached or not cached['peaks']:
                cached['peaks']=audio_peaks(np.frombuffer(zlib.decompress(cached['audio']),dtype=np.float32).reshape(2,-1))
                self.put(('source',key),cached,self.size(cached))
            if notify:notify(key,cached['thumbs'],cached['peaks'])
            return cached
        _check(cancel)
        shot=plan.sources[key];path=materialize_source(shot)
        with av.open(path) as container:
            first=next(container.decode(video=0),None)
            if first is None:raise ValueError(f'{shot.name} has no decodable video frames.')
            info=dict(path=path,frames=max(1,round(shot.duration*FPS)),audio=bool(container.streams.audio),start_time=first.time or 0)
        blocks=[];batch=[];thumbs=[]
        for image in _frames(info,plan.width,plan.height,cancel):
            batch.append(image)
            if len(batch)==BLOCK:
                identity=('source-block',key,len(blocks))
                encoded=self.get(identity)
                if encoded is None:
                    encoded=_encode(batch,plan.width,plan.height,cancel)
                    self.put(identity,encoded,len(encoded))
                blocks.append(encoded)
                memory=io.BytesIO()
                thumb=Image.fromarray(batch[len(batch)//2]);thumb.thumbnail((120,72));thumb.save(memory,format='JPEG',quality=75)
                thumbs.append(memory.getvalue());batch=[]
        if batch:
            identity=('source-block',key,len(blocks))
            encoded=self.get(identity)
            if encoded is None:
                encoded=_encode(batch,plan.width,plan.height,cancel)
                self.put(identity,encoded,len(encoded))
            blocks.append(encoded)
            memory=io.BytesIO();thumb=Image.fromarray(batch[len(batch)//2]);thumb.thumbnail((120,72));thumb.save(memory,format='JPEG',quality=75);thumbs.append(memory.getvalue())
        pcm=_audio(info,cancel);peaks=audio_peaks(pcm)
        audio=zlib.compress(pcm.tobytes(),1)
        result=dict(blocks=blocks,audio=audio,thumbs=thumbs,peaks=peaks,frames=info['frames'])
        with self._lock:
            for index in range(len(blocks)):
                old=self._entries.pop(('source-block',key,index),None)
                if old:self._bytes-=old[1]
        self.put(('source',key),result,sum(map(len,blocks))+len(audio)+sum(map(len,thumbs)))
        for index in range(len(blocks)):self.workfiles.remove(('source-block',key,index))
        self.stats['source_builds']+=1
        if notify:notify(key,thumbs,peaks)
        return result

    def build(self,plan,cancel=None,progress=None,region_ready=None,thumbnails=None,partial=None):
        report=progress or (lambda *_:None)
        local_sources=OrderedDict();videos=[];last_publish=0
        def source(key):
            if key not in local_sources:
                # Keep only the sources needed by the current region; the LRU is
                # bounded independently of the current assembled player buffer.
                local_sources[key]=self._source(key,plan,cancel,thumbnails)
                while len(local_sources)>4:local_sources.popitem(last=False)
            local_sources.move_to_end(key)
            return local_sources[key]
        def get_image(frame,decoded):
            if frame[0]=='source':
                _,key,index=frame;block=index//BLOCK
                identity=(key,block)
                if identity not in decoded:
                    with av.open(io.BytesIO(source(key)['blocks'][block])) as container:
                        decoded[identity]=[f.to_ndarray(format='rgb24') for f in container.decode(video=0)]
                return decoded[identity][index%BLOCK]
            _,mode,side,numerator,denominator,a,b=frame
            return blend_frame(get_image(a,decoded),get_image(b,decoded),numerator/denominator,mode,side)
        try:
            _check(cancel)
            if not plan.parts:raise ValueError('Add at least one completed shot.')
            report(0,'Updating cached sequence')
            for index,part in enumerate(plan.parts):
                _check(cancel)
                data=self.get(('region',part.key))
                if data is None:
                    local_sources.clear()
                    first=part.frames[0]
                    if first[0]=='source' and first[2]%BLOCK==0:
                        block=source(first[1])['blocks'][first[2]//BLOCK]
                        # An entire native source block can be reused as-is.
                        if len(part.frames)==min(BLOCK,source(first[1])['frames']-first[2]):data=block
                    if data is None:
                        decoded={}
                        data=_encode((get_image(f,decoded) for f in part.frames),plan.width,plan.height,cancel)
                        self.stats['region_builds']+=1
                    self.put(('region',part.key),data,len(data))
                else:self.stats['region_hits']+=1
                videos.append(data)
                if region_ready:region_ready(index)
                report(round(95*(index+1)/max(1,len(plan.parts))),'Updating cached sequence')
                now=time.monotonic()
                if partial and index<len(plan.parts)-1 and now-last_publish>=1.5:
                    # Publish a playable growing prefix, including source audio.
                    partial(self.assemble(plan,videos,cancel,source),part.start+len(part.frames))
                    last_publish=now
            report(99,'Linking cached video and audio')
            data=self.assemble(plan,videos,cancel,source)
            report(100,'Ready')
            return data
        except RuntimeError:
            if cancel is not None and cancel.is_set():return None
            raise

    def assemble(self,plan,videos,cancel,source):
        """Remux video without re-encoding; encode cached PCM as continuous Opus."""
        memory=io.BytesIO();audio_arrays=OrderedDict()
        def samples(frame):
            if frame[0]=='source':
                _,key,index=frame
                if key not in audio_arrays:
                    item=source(key)
                    audio_arrays[key]=np.frombuffer(zlib.decompress(item['audio']),dtype=np.float32).reshape(2,-1)
                    while len(audio_arrays)>4:audio_arrays.popitem(last=False)
                audio_arrays.move_to_end(key)
                return audio_arrays[key][:,index*SAMPLES:(index+1)*SAMPLES]
            _,_,side,numerator,denominator,a,b=frame
            a,b=samples(a),samples(b)
            if side=='destination':a,b=b,a
            # Match the compositor's sample-by-sample linear audio crossfade.
            alpha=(np.arange(SAMPLES,dtype=np.float32)+(numerator-1)*SAMPLES+1)/((denominator-1)*SAMPLES+1)
            return (1-alpha)*a+alpha*b
        with av.open(io.BytesIO(videos[0])) as template,av.open(memory,'w',format='webm') as output:
            video=output.add_stream_from_template(template.streams.video[0])
            sound=output.add_stream('libopus',rate=RATE);sound.layout='stereo';sound.bit_rate=128000
            audio_cursor=0
            for part,data in zip(plan.parts,videos):
                _check(cancel)
                with av.open(io.BytesIO(data)) as block:
                    packets=[p for p in block.demux(video=0) if p.size and p.pts is not None]
                    for index,packet in enumerate(packets):
                        packet.stream=video;packet.pts=part.start+index;packet.dts=packet.pts
                        packet.time_base=Fraction(1,FPS);packet.duration=1
                        output.mux(packet)
                pcm=np.concatenate([samples(f) for f in part.frames],axis=1)
                cursor=0
                while cursor<pcm.shape[1]:
                    size=min(960,pcm.shape[1]-cursor)
                    frame=av.AudioFrame.from_ndarray(np.ascontiguousarray(pcm[:,cursor:cursor+size]),format='fltp',layout='stereo')
                    frame.sample_rate=RATE;frame.pts=audio_cursor;frame.time_base=Fraction(1,RATE)
                    for packet in sound.encode(frame):output.mux(packet)
                    cursor+=size;audio_cursor+=size
            for packet in sound.encode(None):output.mux(packet)
        self.stats['assemblies']+=1
        return memory.getvalue()
