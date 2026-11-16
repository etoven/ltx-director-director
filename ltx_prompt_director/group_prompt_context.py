"""Read-only, request-scoped visual continuity for grouped prompt authoring."""
from contextvars import ContextVar
from pathlib import Path
import base64,copy,hashlib,io,json

GROUP_PROMPT_CONTEXT=ContextVar('group_prompt_context',default=None)


def snapshot(owner,target_groups=None):
    if owner.prompt_only_workspace or not (owner.workspace_engine.startswith('minimax') or owner.video_extensions):return None
    grouped=list(owner.render_groups().items())
    if not grouped or not grouped[0][1]:return None
    targets=set(target_groups) if target_groups is not None else {owner._active_render_group}
    first_group,first_segments=grouped[0]
    first_options=owner.render_group_options.get(str(first_group),{})
    refs=first_options.get('context',{}).get('minimaxH3',{}).get('referenceImages',[])
    if owner._active_render_group==first_group:refs=owner.minimax_reference_images
    records=[]
    def authored(segment,where,last=False):
        if segment.kind not in {'image','video'} or not (segment.media_path or getattr(segment,'_source_bytes',None) or getattr(segment,'_media_digest',None)):return None
        return dict(segment=copy.deepcopy(segment),source_group=where,kind=segment.kind,
                    path=segment.media_path,time=max(0,(segment.trim_start or 0)/24+(segment.duration-1/24 if last and segment.kind=='video' else 0)),origin='authored visual')
    for position,(group,segments) in enumerate(grouped):
        if group not in targets or not segments:continue
        candidates=[]
        initial=authored(segments[0],group) if segments[0].kind!='image' or segments[0].role!='end' else None
        current_options=owner.render_group_options.get(str(group),{})
        current_clip=current_options.get('clip',{})
        current_start=(dict(kind='video',path=current_clip['path'],source_group=group,
            time=max(0,float(current_clip.get('trim_start') or 0)/24),origin='attached group start',
            revision=current_options.get('attachmentRevision',0)) if current_clip.get('path') else None)
        for previous in range(position-1,-1,-1):
            source_group,source_segments=grouped[previous]
            clip=owner.render_group_options.get(str(source_group),{}).get('clip',{})
            if clip.get('path'):
                following=grouped[previous+1][0];transition=owner.extension_transition(following)
                duration=float(clip.get('duration') or sum(s.duration for s in source_segments))
                overlap=float(transition.get('overlap',0)) if transition.get('blend')!='cut' else 1/24
                candidate=dict(kind='video',path=clip['path'],source_group=source_group,
                    time=max(0,float(clip.get('trim_start') or 0)/24+duration-min(duration,overlap)),
                    origin='blend source start' if transition.get('blend')!='cut' else 'preceding cut',
                    revision=owner.render_group_options.get(str(source_group),{}).get('attachmentRevision',0))
                if previous==position-1 and current_start is not None and transition.get('blend')!='cut':
                    candidate.update(destination=current_start,blend=transition['blend'],side=transition.get('side','source'),
                        alpha=1/(round(min(duration,overlap)*24)+1),canvas=copy.deepcopy(getattr(owner,'shot_sequence_settings',{})))
                candidates.append(candidate)
            if previous==position-1:
                if initial is not None:candidates.append(initial)
                if current_start is not None:candidates.append(current_start)
            for source in reversed(source_segments):
                item=authored(source,source_group,last=True)
                if item is not None:candidates.append(item)
        if position==0:
            if initial is not None:candidates.append(initial)
            if current_start is not None:candidates.append(current_start)
        records.append(dict(group=group,group_number=position+1,
            first_text_id=next((s.id for s in segments if s.kind=='text'),None),
            first_segment_id=segments[0].id,previous_group=grouped[position-1][0] if position else None,candidates=candidates,
            earlier_authoring=[dict(group=g,segments=[dict(id=s.id,kind=s.kind,role=s.role,duration=s.duration,prompt=s.prompt) for s in items]) for g,items in grouped[:position]],
            first_group_prompt=first_segments[0].prompt,first_group_references=copy.deepcopy(refs)))
    return records or None


def fingerprint(records):
    if not records:return ''
    def safe(value):
        if hasattr(value,'to_dict'):
            return {key:item for key,item in value.to_dict().items() if key in {'id','kind','role','media_path','preview_path','duration','trim_start'}}
        if isinstance(value,dict):
            result={key:safe(item) for key,item in value.items()}
            if value.get('path'):
                try:
                    status=Path(value['path']).stat();result['file_identity']=(status.st_size,status.st_mtime_ns)
                except OSError:pass
            return result
        if isinstance(value,list):return [safe(item) for item in value]
        return value
    payload=safe(records)
    return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def _pil_image(candidate):
    from .project_archive import materialize_source
    from PIL import Image
    source=materialize_source(candidate['segment']) if candidate.get('segment') is not None else candidate['path']
    if candidate['kind']=='image':
        with Image.open(source) as image:return image.convert('RGB')
    import av
    with av.open(str(source)) as media:
        stream=media.streams.video[0];seconds=candidate['time']
        media.seek(max(0,int(seconds/stream.time_base)),stream=stream,backward=True)
        selected=None
        for frame in media.decode(stream):
            selected=frame
            if frame.time is None or frame.time+1e-6>=seconds:break
        if selected is None:raise ValueError('No frame available at the blend anchor.')
        return selected.to_image().convert('RGB')


def _image(candidate):
    image=_pil_image(candidate);origin=candidate['origin']
    if candidate.get('destination') is not None:
        try:destination=_pil_image(candidate['destination'])
        except (OSError,ValueError,RuntimeError,IndexError):destination=None
        if destination is not None:
            from PIL import Image,ImageOps
            import numpy as np
            from .shot_sequence import blend_frame
            canvas=candidate.get('canvas',{})
            width=int(canvas.get('width',image.width));height=int(canvas.get('height',image.height))
            scale=min(1,512/width,512/height);size=(max(2,round(width*scale)),max(2,round(height*scale)))
            def letterbox(frame):
                fitted=ImageOps.contain(frame,size);result=Image.new('RGB',size)
                result.paste(fitted,((size[0]-fitted.width)//2,(size[1]-fitted.height)//2));return np.asarray(result)
            image=Image.fromarray(blend_frame(letterbox(image),letterbox(destination),candidate['alpha'],candidate['blend'],candidate['side']))
            origin='composited blend start'
    image.thumbnail((512,512));output=io.BytesIO();image.save(output,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(output.getvalue()).decode(),origin


def augment(inputs,rules,provider,reference_encoder):
    records=GROUP_PROMPT_CONTEXT.get()
    if not records:return inputs,rules
    from .minimax_reference import reference_inventory
    result=list(inputs)
    context=[]
    for record in records:
        chosen=None;image=None
        for candidate in record['candidates'] if provider!='local' else []:
            try:image,origin=_image(candidate);chosen=candidate;break
            except (OSError,ValueError,RuntimeError,IndexError):continue
        description=dict(group=record['group'],group_number=record['group_number'],
            first_text_segment_id=record['first_text_id'],first_segment_id=record['first_segment_id'],
            earlier_authored_prompts=record['earlier_authoring'])
        if image is not None:
            exact=origin in {'composited blend start','attached group start'} or (chosen['source_group']==record['group'] and origin=='authored visual') or (chosen['source_group']==record['previous_group'] and origin=='blend source start')
            description.update(anchor_exact_start=exact,anchor_source_group=chosen['source_group'],anchor_source=origin,
                source_time=chosen['time'],group_local_time=0)
            result.append(dict(name=f"Group {record['group_number']} starting anchor",role='start',kind='image',
                image=image,group_start_anchor=True,reference_mode_input=True,group=record['group'],
                first_text_segment_id=record['first_text_id'],timeline_time=0,source_time=chosen['time'],
                source_group=chosen['source_group'],anchor_source=origin,anchor_exact_start=exact,source_segment_id=chosen.get('segment').id if chosen.get('segment') is not None else None,prompt_label='VISUAL STARTING STATE — CONTEXT ONLY, NO NEW OUTPUT SEGMENT'))
        else:
            description.update(fallback='Group 1 / Segment 1 prompt and saved Reference-panel data',
                               first_group_first_segment_prompt=record['first_group_prompt'])
            references=record['first_group_references']
            description['first_group_reference_panel']=[{key:value for key,value in reference.items() if key not in {'image','path','audio_path'}} for reference in references if reference]
            for item in reference_inventory([],references):
                if provider=='local':
                    ref={**item,'prompt':references[item['reference_slot']].get('notes',''),'observation_available':False}
                else:
                    try:ref=reference_encoder(item,references[item['reference_slot']],provider)
                    except (OSError,ValueError,RuntimeError,IndexError):ref={**item,'observation_available':False}
                ref.update(name='Group 1 fallback · '+str(ref.get('name','reference')),group_continuity_fallback=True,
                           group=record['group'],context_only=True,reference_mode_input=True)
                media_key=next((key for key in ('image','video','audio') if ref.get(key)),None)
                if media_key and any(existing.get(media_key)==ref[media_key] for existing in result):continue
                result.append(ref)
        context.append(description)
    rules+='''\n\nGROUP STARTING CONTINUITY (read-only context, not extra output segments):
When anchor_exact_start is true, use the supplied starting-anchor image as factual evidence of this group's initial appearance, composition, pose and transformation state at local time zero. An older backward-search image is the last known earlier visual: use intervening authored actions to derive the current opening state, do not reset to that earlier appearance, and do not repeat actions already depicted by the image. For Generate and Refine, the first text segment's production prompt must explicitly describe that observed starting state before developing its authored motion; do not leave the starting anchor implicit. Preserve authored actions, timing, order, dialogue, media and settings. A preceding-cut image is the last known visual context and does not override an authored hard cut or new shot.
When an immediate anchor is unavailable, use the nearest available visual found by searching backward through earlier groups, together with the intervening authored prompts. When no visual is available, use Group 1 / Segment 1's exact prompt and Group 1's saved Reference-panel data as the initial context. Do not invent observations from missing images or unheard audio.
Stay inside the current operation's editing scope. A selected-segment edit may only describe the initial anchor in its own prompt when that segment is the group's first text segment; otherwise use it as continuity context. Global-only output remains global instructions. Do not rewrite previous groups, add segments for context images, emit implementation labels/filenames/new reference IDs, or change the output schema. Describe the starting state in the prompt text itself, using the workspace's existing notation where applicable.
SNAPSHOT OF GROUP CONTEXT:\n'''+json.dumps(context,ensure_ascii=False)
    if provider=='local':rules+='\nThe local text-only writer cannot observe pixels or audio. Use the supplied fallback prompt and reference notes only; do not claim to have seen the image.'
    return result,rules
