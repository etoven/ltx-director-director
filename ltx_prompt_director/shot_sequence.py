"""CPU shot compositor: streamed video, source audio and seekable RAM WebM previews."""
from __future__ import annotations
from collections import deque
from fractions import Fraction
from itertools import islice
from pathlib import Path
import io
import json
import hashlib
import os
import uuid

import av
import numpy as np

MODES = ('cut', 'linear_blend', 'filmic_crossfade', 'perceptual_crossfade')
FPS = 24
RATE = 48000
DEFAULTS = dict(blend='perceptual_crossfade', overlap=1.0, side='source', crf=15,
                width=1280, height=704)


def settings(value=None):
    result = {**DEFAULTS, **(value if isinstance(value, dict) else {})}
    if 'shot_statuses' in result:
        raw=result['shot_statuses']
        result['shot_statuses']={str(key):str(value) for key,value in raw.items() if isinstance(value,str)} if isinstance(raw,dict) else {}
    if result['blend'] not in MODES or result['side'] not in ('source', 'destination'):
        raise ValueError('Unknown shot blend mode or overlap side.')
    result['overlap'] = float(result['overlap'])
    if not np.isfinite(result['overlap']) or not 0 <= result['overlap'] <= 60:
        raise ValueError('Overlap must be between 0 and 60 seconds.')
    raw=result.get('shot_transitions',{})
    if isinstance(raw,dict):
        overrides={}
        for ident,value in raw.items():
            if not isinstance(value,dict):continue
            entry={key:value[key] for key in ('blend','overlap','side') if key in value}
            if not entry:continue
            effective=_transition(entry,result)
            overrides[str(ident)]={key:effective[key] for key in entry}
        if overrides or 'shot_transitions' in result:result['shot_transitions']=overrides
    elif 'shot_transitions' in result:result['shot_transitions']={}
    result['crf'] = int(result['crf'])
    if not 0 <= result['crf'] <= 63:
        raise ValueError('WebM CRF must be between 0 and 63.')
    for key in ('width', 'height'):
        result[key] = max(32, min(8192, int(result[key]) // 2 * 2))
    return result


def _transition(value,defaults):
    result={key:value.get(key,defaults[key]) for key in ('blend','overlap','side')}
    if result['blend'] not in MODES or result['side'] not in ('source','destination'):
        raise ValueError('Unknown shot blend mode or overlap side.')
    result['overlap']=float(result['overlap'])
    if not np.isfinite(result['overlap']) or not 0<=result['overlap']<=60:
        raise ValueError('Overlap must be between 0 and 60 seconds.')
    return result


def transitions(shot_ids,options):
    """The outgoing join belongs to its source shot, including after reordering."""
    opts=settings(options);overrides=opts.get('shot_transitions',{})
    return [_transition(overrides.get(str(ident),{}),opts) for ident in list(shot_ids)[:-1]]


def independent_transitions(shot_ids,options):
    """Freeze legacy defaults into each shot, including the last shot's next join."""
    opts=settings(options);existing=opts.get('shot_transitions',{})
    return {**opts,'shot_transitions':{str(ident):_transition(existing.get(str(ident),{}),opts) for ident in shot_ids}}


def inherit_transitions(shot_ids,options,neighbor=None):
    """New shots copy their closest neighbor once; subsequent edits are independent."""
    opts=settings(options);existing=dict(opts.get('shot_transitions',{}))
    inherited=_transition(existing.get(str(neighbor),{}),opts)
    for ident in shot_ids:existing[str(ident)]=dict(inherited)
    return {**opts,'shot_transitions':existing}


def shot_layout(shots,options):
    joins=transitions([shot.id for shot in shots],options)
    return overlaps([{'frames':max(1,round(shot.duration*FPS))} for shot in shots],
                    [join['overlap'] for join in joins])


def probe(path, cancel=None):
    """Measure the actual video interval, independent of container/audio timestamps."""
    with av.open(str(path)) as container:
        if not container.streams.video:
            raise ValueError(f'{Path(path).name} has no video stream.')
        video = container.streams.video[0]
        first = last = None
        step = 1 / float(video.average_rate or FPS)
        count = 0
        # WebM often omits stream.duration. Container duration can include a nonzero
        # start timestamp or a longer audio stream, so it is not a clip length.
        for frame in container.decode(video):
            if cancel is not None and cancel.is_set():
                raise RuntimeError('Shot sequence operation cancelled.')
            count += 1
            if frame.time is not None:
                if first is None:first = frame.time
                if last is not None and frame.time > last:step = frame.time-last
                last = frame.time
                if frame.duration:step = float(frame.duration * frame.time_base)
        if not count:raise ValueError(f'{Path(path).name} has no decodable video frames.')
        seconds = last-first+step if first is not None else count / float(video.average_rate or FPS)
        return dict(path=str(path), frames=max(1, round(seconds * FPS)),
                    width=video.width, height=video.height, audio=bool(container.streams.audio),
                    start_time=first if first is not None else float((video.start_time or 0)*video.time_base))


def overlaps(clips, seconds):
    counts = [clip['frames'] for clip in clips]
    result = []
    total = counts[0] if counts else 0
    intervals=[seconds]*(len(counts)-1) if isinstance(seconds,(int,float)) else list(seconds)
    if len(intervals)!=max(0,len(counts)-1):raise ValueError('Each shot join needs an overlap duration.')
    for count,seconds in zip(counts[1:],intervals):
        amount = min(round(seconds * FPS), total, count)
        result.append(amount)
        total += count - amount
    return result, total


def _lab(rgb):
    rgb = np.where(rgb > .04045, ((rgb + .055) / 1.055) ** 2.4, rgb / 12.92)
    xyz = rgb @ np.array([[.4124564,.2126729,.0193339], [.3575761,.7151522,.119192], [.1804375,.072175,.9503041]], dtype=np.float32)
    xyz /= np.array([.95047,1,1.08883], dtype=np.float32)
    f = np.where(xyz > (6/29)**3, np.cbrt(xyz), xyz / (3*(6/29)**2) + 4/29)
    return np.stack((116*f[...,1]-16, 500*(f[...,0]-f[...,1]), 200*(f[...,1]-f[...,2])), axis=-1)


def _rgb(lab):
    fy = (lab[...,0]+16)/116
    f = np.stack((fy+lab[...,1]/500, fy, fy-lab[...,2]/200), axis=-1)
    xyz = np.where(f > 6/29, f**3, 3*(6/29)**2*(f-4/29))
    xyz *= np.array([.95047,1,1.08883],dtype=np.float32)
    rgb = xyz @ np.array([[3.2404542,-.969266,.0556434],[-1.5371385,1.8760108,-.2040259],[-.4985314,.041556,1.0572252]],dtype=np.float32)
    rgb = np.maximum(rgb,0)
    return np.clip(np.where(rgb > .0031308, 1.055*rgb**(1/2.4)-.055,12.92*rgb),0,1)


def blend_frame(source, destination, alpha, mode, side):
    if side == 'destination':
        source, destination = destination, source
    a = source.astype(np.float32)/255
    b = destination.astype(np.float32)/255
    if mode == 'linear_blend':
        out = (1-alpha)*a + alpha*b
    elif mode == 'filmic_crossfade':
        out = ((1-alpha)*a**2.2 + alpha*b**2.2)**(1/2.2)
    elif mode == 'perceptual_crossfade':
        out = _rgb((1-alpha)*_lab(a) + alpha*_lab(b))
    else:
        raise ValueError('Cut transitions do not blend frames.')
    return np.rint(np.clip(out,0,1)*255).astype(np.uint8)


def _frames(clip, width, height, cancel):
    with av.open(clip['path']) as container:
        stream = container.streams.video[0]
        decoded = iter(container.decode(stream))
        current = next(decoded, None)
        if current is None:
            raise ValueError(f"No frames could be decoded from {clip['path']}.")
        origin = current.time or 0
        following = next(decoded, None)
        for index in range(clip['frames']):
            if cancel is not None and cancel.is_set():
                raise RuntimeError('Shot sequence operation cancelled.')
            while following is not None and (following.time or 0)-origin <= index/FPS:
                current, following = following, next(decoded,None)
            # Fit the common canvas without stretching the source aspect ratio.
            scale = min(width/current.width, height/current.height)
            w,h = max(2,round(current.width*scale)),max(2,round(current.height*scale))
            image = current.reformat(width=w,height=h,format='rgb24').to_ndarray()
            canvas = np.zeros((height,width,3),dtype=np.uint8)
            x,y = (width-w)//2,(height-h)//2
            canvas[y:y+h,x:x+w] = image
            yield canvas


def _join(source, destination, amount, mode, side):
    if amount == 0:
        yield from source
        yield from destination
        return
    tail = deque()
    for frame in source:
        tail.append(frame)
        if len(tail) > amount:
            yield tail.popleft()
    head = list(islice(destination, amount))
    if mode == 'cut':
        yield from tail if side == 'destination' else head
    else:
        for index,(a,b) in enumerate(zip(tail,head)):
            yield blend_frame(a,b,(index+1)/(amount+1),mode,side)
    yield from destination


def _audio(clip, cancel):
    result = np.zeros((2, round(clip['frames']*RATE/FPS)), dtype=np.float32)
    if not clip['audio']:
        return result
    with av.open(clip['path']) as container:
        resampler = av.AudioResampler(format='fltp',layout='stereo',rate=RATE)
        cursor = 0
        def place(frame):
            nonlocal cursor
            samples = frame.to_ndarray()
            start = round((frame.time-clip.get('start_time',0))*RATE) if frame.time is not None else cursor
            if start < 0:
                samples=samples[:,-start:];start=0
            size=max(0,min(samples.shape[1],result.shape[1]-start))
            if size:result[:,start:start+size]=samples[:,:size]
            cursor=start+samples.shape[1]
        for frame in container.decode(container.streams.audio[0]):
            if cancel is not None and cancel.is_set():raise RuntimeError('Shot sequence operation cancelled.')
            for converted in resampler.resample(frame):place(converted)
        for converted in resampler.resample(None):place(converted)
    return result


def join_audio(source, destination, amount, mode, side):
    if amount == 0:return np.concatenate((source,destination),axis=1)
    if mode == 'cut':
        return np.concatenate((source,destination[:,amount:]),axis=1) if side=='destination' else np.concatenate((source[:,:-amount],destination),axis=1)
    a,b=source[:,-amount:],destination[:,:amount]
    if side=='destination':a,b=b,a
    alpha=np.linspace(0,1,amount+2,dtype=np.float32)[1:-1][None,:]
    # Smooth audio through the same overlap; avoid equal-power boosts of identical tracks.
    mixed=(1-alpha)*a+alpha*b
    return np.concatenate((source[:,:-amount],mixed,destination[:,amount:]),axis=1)


def render_sequence(paths, options, output=None, preview=False, cancel=None, progress=None):
    """Encode directly to BytesIO for preview, or atomically to a baked WebM."""
    opts=settings(options)
    if not paths:raise ValueError('Add at least one completed shot.')
    report = progress or (lambda percent, stage: None)
    report(0, 'Reading source movies')
    clips=[]
    for index,path in enumerate(paths):
        clips.append(probe(path,cancel))
        report(round(10*(index+1)/len(paths)), 'Reading source movies')
    for clip,limit in zip(clips,opts.get('_frame_limits',[])):
        clip['frames']=max(1,min(clip['frames'],int(limit)))
    join_options=transitions(opts.get('_shot_ids',range(len(clips))),opts)
    if len(join_options)!=len(clips)-1:raise ValueError('Shot identities must match the source movies.')
    joins,count=overlaps(clips,[join['overlap'] for join in join_options])
    width,height=opts['width'],opts['height']
    if preview:
        scale=min(1,640/width,360/height)
        width=max(32,round(width*scale)//2*2);height=max(32,round(height*scale)//2*2)
    images=_frames(clips[0],width,height,cancel)
    report(10, 'Preparing source audio')
    audio=_audio(clips[0],cancel)
    report(10+round(10/len(clips)), 'Preparing source audio')
    for clip_index,(clip,amount,join) in enumerate(zip(clips[1:],joins,join_options),2):
        images=_join(images,_frames(clip,width,height,cancel),amount,join['blend'],join['side'])
        audio=join_audio(audio,_audio(clip,cancel),round(amount*RATE/FPS),join['blend'],join['side'])
        report(10+round(10*clip_index/len(clips)), 'Preparing source audio')
    memory=io.BytesIO() if output is None else None
    temporary=None
    if output is not None:
        target=Path(output);target.parent.mkdir(parents=True,exist_ok=True)
        temporary=target.with_name('.'+target.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with av.open(memory if memory is not None else str(temporary),'w',format='webm') as container:
            video=container.add_stream('libvpx-vp9',rate=FPS)
            video.width=width;video.height=height;video.pix_fmt='yuv420p'
            video.bit_rate=0
            video.options={'crf':str(30 if preview else opts['crf']),'deadline':'realtime' if preview else 'good','cpu-used':'8' if preview else '4'}
            sound=container.add_stream('libopus',rate=RATE)
            sound.layout='stereo';sound.bit_rate=128000
            cursor=0
            def encode_audio(end):
                nonlocal cursor
                while cursor<end:
                    size=min(960,end-cursor)
                    frame=av.AudioFrame.from_ndarray(np.ascontiguousarray(audio[:,cursor:cursor+size]),format='fltp',layout='stereo')
                    frame.sample_rate=RATE;frame.pts=cursor;frame.time_base=Fraction(1,RATE)
                    for packet in sound.encode(frame):container.mux(packet)
                    cursor+=size
            last_progress=20
            report(last_progress, 'Encoding video and audio')
            for index,image in enumerate(images):
                if cancel is not None and cancel.is_set():raise RuntimeError('Shot sequence operation cancelled.')
                frame=av.VideoFrame.from_ndarray(image,format='rgb24')
                frame.pts=index;frame.time_base=Fraction(1,FPS)
                for packet in video.encode(frame):container.mux(packet)
                encode_audio(min(audio.shape[1],round((index+1)*RATE/FPS)))
                percent=20+int(78*(index+1)/count)
                if percent != last_progress:
                    report(percent, 'Encoding video and audio');last_progress=percent
            report(99, 'Finishing WebM')
            encode_audio(audio.shape[1])
            for stream in (video,sound):
                for packet in stream.encode(None):container.mux(packet)
        if temporary is not None:os.replace(temporary,output)
        report(100, 'Complete')
        return memory.getvalue() if memory is not None else str(output)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


def ensure_collection_sequences(root, records):
    """Create one persistent Shot Sequence project per collection, idempotently."""
    existing={r.get('collection') for r in records if r.get('projectType')=='shot_sequence'}
    collections={r.get('collection') for r in records if r.get('collection')}
    added=[]
    for name in sorted(collections-existing):
        ident='shots-'+hashlib.sha256(name.encode()).hexdigest()[:20]
        path=Path(root)/(ident+'.LTXD')
        meta_path=Path(root)/(ident+'.meta.json')
        if meta_path.exists():continue
        payload={'app':'ltx-director-director','projectVersion':9,'projectType':'shot_sequence','frames':[], 'shotSequence':dict(DEFAULTS)}
        meta={'id':ident,'name':'Shot Sequence','description':'Completed shots · continuous preview and WebM baking',
              'collection':name,'projectType':'shot_sequence','projectPath':str(path),'tags':[],'status':'','archived':False,'notes':[]}
        path.write_text(json.dumps(payload)+'\n')
        meta_path.write_text(json.dumps(meta,indent=2)+'\n')
        added.append(meta)
    return added
