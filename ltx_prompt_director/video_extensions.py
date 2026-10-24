"""Sequential H3 continuation and cancellable, frame-blended assembly."""
from pathlib import Path
import os
import subprocess
import tempfile
from fractions import Fraction
import imageio_ffmpeg
import av

BLOCK_SECONDS = 15
OVERLAP_SECONDS = 1


def duration(path):
    with av.open(str(path)) as media:
        stream = media.streams.video[0]
        if stream.duration is not None:
            return float(stream.duration * stream.time_base)
        return float(media.duration / av.time_base)


def continuation_reference(path, marked_duration=None):
    length = duration(path)
    if marked_duration is not None:length=min(length,float(marked_duration))
    if length < OVERLAP_SECONDS:
        raise ValueError('The previous block is shorter than the continuation overlap.')
    return dict(kind='video', name='Previous block · trailing second', path=str(path),
                start=length-OVERLAP_SECONDS, duration=OVERLAP_SECONDS,
                timeline_time=0, timeline_start=0, timeline_end=OVERLAP_SECONDS)


def render_join(source, destination, source_length, left_frames, right_frames, mode, output, cancel, side='center'):
    """Stream the existing sequence editor's exact blend math into a join clip."""
    from .shot_sequence import _frames, blend_frame
    with av.open(str(source)) as media:
        width,height=media.streams.video[0].width,media.streams.video[0].height
    total=left_frames+right_frames
    left_count=left_frames if side=='center' else total
    right_count=right_frames if side=='center' else total
    first=iter(_frames(dict(path=source,frames=left_count,trim_start=round(source_length*24)-left_count),width,height,cancel))
    second=iter(_frames(dict(path=destination,frames=right_count,trim_start=0),width,height,cancel))
    left=next(first);right=next(second);left_index=right_index=0
    with av.open(str(output),'w') as container:
        stream=container.add_stream('libx264',rate=24)
        stream.width=width;stream.height=height;stream.pix_fmt='yuv420p'
        stream.options={'crf':'18','preset':'fast'}
        for index in range(total):
            if cancel.is_set():raise RuntimeError('Extension assembly cancelled.')
            while left_index < min(left_count-1,index*left_count//total):left=next(first);left_index+=1
            while right_index < min(right_count-1,index*right_count//total):right=next(second);right_index+=1
            mixed=blend_frame(left,right,(index+1)/(total+1),mode,'source')
            frame=av.VideoFrame.from_ndarray(mixed,format='rgb24')
            frame.pts=index;frame.time_base=Fraction(1,24)
            for packet in stream.encode(frame):container.mux(packet)
        for packet in stream.encode():container.mux(packet)


def blend_blocks(paths, cancel, on_event=None, durations=None, transitions=None):
    from .shot_sequence import MODES
    if len(paths)<2:raise ValueError('An extension needs at least two rendered blocks.')
    if cancel.is_set():raise RuntimeError('Extension assembly cancelled.')
    available=[duration(path) for path in paths]
    lengths=list(durations) if durations is not None else available
    if len(lengths)!=len(paths) or any(length>actual+1/24 for length,actual in zip(lengths,available)):
        raise ValueError('A rendered block is shorter than its marked duration.')
    counts=[round(length*24) for length in lengths]
    if any(count<2 for count in counts):raise ValueError('Every block needs at least two frames.')
    transitions=transitions if transitions is not None else [dict(blend='linear_blend',overlap=1)]*(len(paths)-1)
    if len(transitions)!=len(paths)-1:raise ValueError('Each extension needs one blend setting.')
    joins=[];used=[0]*len(paths)
    for index,setting in enumerate(transitions):
        mode=setting.get('blend','perceptual_crossfade')
        if mode not in MODES:raise ValueError('Unknown blend mode.')
        requested=float(setting.get('overlap',1))
        if not 0<=requested<=15:raise ValueError('Blend duration must be between 0 and 15 seconds.')
        amount=0 if mode=='cut' else min(round(requested*24),counts[index],counts[index+1])
        # Two frames give each half-window at least one input frame.
        if amount==1:amount=2
        side=setting.get('side','center')
        if side not in {'source','destination','center'}:raise ValueError('Unknown blend overlap side.')
        if side!='center':
            target=index if side=='source' else index+1
            amount=min(amount,counts[target]-used[target]);used[target]+=amount
        joins.append((amount,mode,side))
    def trim_counts(join):
        amount,mode,side=join
        if side=='source':return amount,0
        if side=='destination':return 0,amount
        return amount//2,amount-amount//2
    audio=[]
    for path in paths:
        with av.open(str(path)) as media:audio.append(bool(media.streams.audio))
    if any(audio) and not all(audio):raise ValueError('All extension blocks must have matching audio availability.')
    has_audio=all(audio)
    output=Path(paths[0]).parent/'extended-blended.mp4'
    temporary=output.with_name('.extended-blended.partial.mp4')
    if on_event:on_event(dict(kind='progress',percent=0,message='Blending extension blocks…'))
    try:
        with tempfile.TemporaryDirectory(prefix='.director-joins-',dir=output.parent) as folder:
            join_paths={}
            for i,(amount,mode,side) in enumerate(joins):
                if not amount:continue
                path=Path(folder)/f'join-{i}.mp4'
                left,right=trim_counts(joins[i])
                render_join(paths[i],paths[i+1],lengths[i],left,right,mode,path,cancel,side)
                join_paths[i]=path
                if on_event:on_event(dict(kind='progress',percent=60*(i+1)/len(joins),message=f'Blended join {i+1} · {mode}'))
            command=[imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-hide_banner','-loglevel','error','-y']
            for path in paths:command+=['-i',str(path)]
            join_inputs={}
            for i,path in join_paths.items():
                join_inputs[i]=len(paths)+len(join_inputs);command+=['-i',str(path)]
            filters=[];pieces=[]
            for i,count in enumerate(counts):
                head=trim_counts(joins[i-1])[1] if i else 0
                tail=trim_counts(joins[i])[0] if i<len(joins) else 0
                peek_head=(head if joins[i-1][2]=='center' else joins[i-1][0]) if i else 0
                peek_tail=(tail if joins[i][2]=='center' else joins[i][0]) if i<len(joins) else 0
                if count>head+tail:
                    filters.append(f'[{i}:v]fps=24,trim=start_frame={head}:end_frame={count-tail},settb=AVTB,setpts=PTS-STARTPTS,format=yuv420p[body{i}]')
                if has_audio:
                    parts=(['body'] if count>head+tail else [])+(['head'] if peek_head else [])+(['tail'] if peek_tail else [])
                    labels=''.join(f'[a{i}{part}]' for part in parts)
                    filters.append(f'[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS,asplit={len(parts)}{labels}')
                    if count>head+tail:
                        filters.append(f'[a{i}body]atrim=start={head/24}:end={(count-tail)/24},asetpts=PTS-STARTPTS,apad,atrim=duration={(count-head-tail)/24}[abody{i}]')
                    for part,start,stop,frames,amount in [('head',0,peek_head/24,peek_head,joins[i-1][0] if i else 0),('tail',(count-peek_tail)/24,count/24,peek_tail,joins[i][0] if i<len(joins) else 0)]:
                        if not frames:continue
                        tempo=frames/amount
                        # FFmpeg atempo accepts [0.5,100]. Factor small ratios.
                        chain='atempo=0.5,' if tempo<.5 else ''
                        if tempo<.5:tempo*=2
                        filters.append(f'[a{i}{part}]atrim=start={start}:end={stop},asetpts=PTS-STARTPTS,{chain}atempo={tempo},apad,atrim=duration={amount/24}[a{part}{i}]')
                if i and joins[i-1][0]:
                    amount=joins[i-1][0]
                    filters.append(f'[{join_inputs[i-1]}:v]settb=AVTB,setpts=PTS-STARTPTS[join{i}]')
                    if has_audio:filters.append(f'[atail{i-1}][ahead{i}]acrossfade=d={amount/24}:c1=tri:c2=tri[ajoin{i}]')
                    pieces.append(f'[join{i}]'+(f'[ajoin{i}]' if has_audio else ''))
                if count>head+tail:pieces.append(f'[body{i}]'+(f'[abody{i}]' if has_audio else ''))
            filters.append(''.join(pieces)+f'concat=n={len(pieces)}:v=1:a={int(has_audio)}[video]'+('[audio]' if has_audio else ''))
            command+=['-filter_complex_threads','1','-filter_complex',';'.join(filters),'-map','[video]']
            if has_audio:command+=['-map','[audio]','-c:a','aac']
            command+=['-c:v','libx264','-crf','18','-pix_fmt','yuv420p','-movflags','+faststart','-r','24','-fps_mode','cfr',str(temporary)]
            with tempfile.TemporaryFile() as errors:
                process=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=errors)
                try:
                    while process.poll() is None:
                        if cancel.wait(.1):
                            process.terminate()
                            try:process.wait(timeout=3)
                            except subprocess.TimeoutExpired:process.kill();process.wait()
                            raise RuntimeError('Extension assembly cancelled.')
                    if process.returncode:
                        errors.seek(0);raise RuntimeError(errors.read().decode(errors='replace')[-4000:])
                finally:
                    if process.poll() is None:process.kill();process.wait()
            if cancel.is_set():raise RuntimeError('Extension assembly cancelled.')
            os.replace(temporary,output)
            return dict(path=str(output),media='video',duration=sum(counts)/24)
    finally:temporary.unlink(missing_ok=True)


def add_block_thumbnails(result,marked_duration,cancel):
    """Create tiny edge previews off the UI thread; keep model output untouched."""
    path=Path(result['path'])
    if not path.is_file():return result
    images={}
    for name,seconds in [('first_frame',0),('last_frame',max(0,marked_duration-1/24))]:
        if cancel.is_set():raise RuntimeError('Extension render cancelled.')
        with av.open(str(path)) as media:
            stream=media.streams.video[0]
            target=int(seconds/stream.time_base)
            media.seek(target,stream=stream,backward=True)
            selected=None
            for frame in media.decode(stream):
                selected=frame
                if (frame.time or 0)>=seconds:break
            if selected is not None:
                image=selected.to_image();image.thumbnail((256,144))
                output=path.with_name(path.stem+'-'+name+'.jpg');image.save(output,quality=84)
                images[name]=str(output)
    return {**result,**images}
