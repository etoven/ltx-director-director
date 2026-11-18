"""Request-scoped neighboring clips for text-ended group boundaries."""
from pathlib import Path
from .blend_timing import blend_frames,FPS


def outgoing_spec(owner,group,segments):
    if not segments or segments[-1].kind!='text':return None
    groups=list(owner.render_groups())
    position=groups.index(group)
    if position+1==len(groups):return None
    following=groups[position+1];join=owner.extension_transition(following)
    if join.get('blend')=='cut' or join.get('overlap',0)<=0:return None
    options=owner.render_group_options.get(str(following),{});clip=options.get('clip',{})
    if not clip.get('path') or not Path(clip['path']).is_file():return None
    return dict(path=clip['path'],duration=float(clip.get('duration') or owner.render_group_duration(following)),
        trim_start=float(clip.get('trim_start') or 0),overlap=join['overlap'],side=join.get('side','source'),
        source_group=following,attachment_revision=options.get('attachmentRevision',0))


def outgoing_reference(spec,target_duration):
    from .video_extensions import duration
    import av
    available=max(0,duration(spec['path'])-spec.get('trim_start',0)/FPS)
    frames=blend_frames(spec['overlap'],min(available,spec['duration'],target_duration))
    if not frames:return None
    length=frames/FPS;start=max(0,float(target_duration)-length)
    result=dict(kind='video',name='Outgoing blend conditioning',path=spec['path'],
        start=spec.get('trim_start',0)/FPS,duration=length,timeline_time=start,
        timeline_start=start,timeline_end=float(target_duration),continuation=True,
        boundary='outgoing',blend_frames=frames,blend_side=spec.get('side','source'),
        source_group=spec.get('source_group'),attachment_revision=spec.get('attachment_revision',0))
    with av.open(str(spec['path'])) as media:
        if media.streams.audio:result['audio_path']=str(spec['path'])
    return result


def append_outgoing(conditioning,spec,target_duration,variant):
    if not spec:return conditioning
    reference=outgoing_reference(spec,target_duration)
    if reference:
        if variant!='ref2va':reference.pop('audio_path',None)
        conditioning.append(reference)
    return conditioning
