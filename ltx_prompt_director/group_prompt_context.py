"""Read-only, request-scoped visual continuity for grouped prompt authoring."""
from contextvars import ContextVar
from pathlib import Path
import base64,copy,hashlib,io,json

GROUP_PROMPT_CONTEXT=ContextVar('group_prompt_context',default=None)


def snapshot(owner,target_groups=None,target_segment_ids=None):
    if owner.prompt_only_workspace or not (owner.workspace_engine.startswith('minimax') or owner.video_extensions):return None
    grouped=list(owner.render_groups().items())
    if not grouped or not grouped[0][1]:return None
    targets=set(target_groups) if target_groups is not None else {owner._active_render_group}
    first_group,first_segments=grouped[0]
    first_options=owner.render_group_options.get(str(first_group),{})
    refs=first_options.get('context',{}).get('minimaxH3',{}).get('referenceImages',[])
    if owner._active_render_group==first_group:refs=owner.minimax_reference_images
    records=[];ordered=[];indices={};spans={};numbers={}
    for number,(g,items) in enumerate(grouped,1):
        numbers[g]=number;begin=len(ordered)
        for segment_number,item in enumerate(items,1):
            indices[item.id]=len(ordered)
            ordered.append(dict(id=item.id,group=g,group_number=number,segment_number=segment_number,
                                kind=item.kind,role=item.role,prompt=item.prompt))
        spans[g]=(begin,len(ordered)-1)
    def authored(segment,where,last=False):
        if segment.kind not in {'image','video'} or not (segment.media_path or getattr(segment,'_source_bytes',None) or getattr(segment,'_media_digest',None)):return None
        return dict(segment=copy.deepcopy(segment),source_group=where,kind=segment.kind,
                    path=segment.media_path,time=max(0,(segment.trim_start or 0)/24+(segment.duration-1/24 if last and segment.kind=='video' else 0)),origin='authored visual',
                    boundary_position=indices[segment.id])
    for position,(group,segments) in enumerate(grouped):
        if group not in targets or not segments:continue
        candidates=[]
        initial=authored(segments[0],group) if segments[0].kind!='image' or segments[0].role!='end' else None
        current_options=owner.render_group_options.get(str(group),{})
        current_clip=current_options.get('clip',{})
        current_start=(dict(kind='video',path=current_clip['path'],source_group=group,
            time=max(0,float(current_clip.get('trim_start') or 0)/24),origin='attached group start',
            revision=current_options.get('attachmentRevision',0),boundary_position=spans[group][0]-.5) if current_clip.get('path') else None)
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
                    revision=owner.render_group_options.get(str(source_group),{}).get('attachmentRevision',0),boundary_position=spans[source_group][1]+.5)
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
        if initial is not None and segments[0].kind in {'image','video'}:
            candidates.insert(0,initial)
        outgoing=[]
        terminal=authored(segments[-1],group,last=True)
        if terminal is not None:outgoing.append(terminal)
        for following in range(position+1,len(grouped)):
            source_group,source_segments=grouped[following]
            options=owner.render_group_options.get(str(source_group),{});clip=options.get('clip',{})
            if clip.get('path'):
                join=owner.extension_transition(source_group)
                overlap=float(join.get('overlap',0)) if join.get('blend')!='cut' else 1/24
                length=float(clip.get('duration') or sum(s.duration for s in source_segments))
                immediate=following==position+1
                candidate=dict(kind='video',path=clip['path'],source_group=source_group,
                    time=max(0,float(clip.get('trim_start') or 0)/24+(max(0,min(length,overlap)-1/24) if immediate else 0)),
                    origin='blend destination end' if immediate and join.get('blend')!='cut' else 'later attached clip',
                    revision=options.get('attachmentRevision',0),
                    boundary_position=spans[group][1]+.5 if immediate else spans[source_group][0]-.5)
                if immediate and current_clip.get('path') and join.get('blend')!='cut':
                    source=dict(kind='video',path=current_clip['path'],source_group=group,
                        time=max(0,float(current_clip.get('trim_start') or 0)/24+float(current_clip.get('duration') or sum(s.duration for s in segments))-1/24),
                        origin='attached group end',revision=current_options.get('attachmentRevision',0),
                        boundary_position=spans[group][1]+.5)
                    amount=max(1,round(min(length,overlap)*24))
                    source.update(destination=candidate,blend=join['blend'],side=join.get('side','source'),
                                  alpha=amount/(amount+1),composition_origin='composited blend end',
                                  canvas=copy.deepcopy(getattr(owner,'shot_sequence_settings',{})))
                    candidate=source
                outgoing.append(candidate)
            for source in source_segments:
                item=authored(source,source_group)
                if item is not None:outgoing.append(item)
        records.append(dict(group=group,group_number=position+1,
            last_text_id=next((s.id for s in reversed(segments) if s.kind=='text'),None),
            target_segments=[copy.deepcopy(ordered[indices[item.id]]) for item in segments
                             if target_segment_ids is None or item.id in target_segment_ids],
            ordered_segments=copy.deepcopy(ordered),group_numbers=copy.deepcopy(numbers),outgoing_candidates=outgoing,
            later_authoring=[dict(group=g,segments=[dict(id=s.id,kind=s.kind,role=s.role,duration=s.duration,prompt=s.prompt) for s in items]) for g,items in grouped[position+1:]],
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
            origin=candidate.get('composition_origin','composited blend start')
    image.thumbnail((512,512));output=io.BytesIO();image.save(output,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(output.getvalue()).decode(),origin


def _relationships(record,candidate,direction):
    import math
    ordered=record['ordered_segments'];positions={item['id']:i for i,item in enumerate(ordered)}
    boundary=candidate.get('boundary_position',-.5)
    result=[]
    for target in record['target_segments']:
        index=positions[target['id']]
        between=(ordered[max(0,math.floor(boundary)+1):index] if direction=='incoming'
                 else ordered[index+1:max(index+1,math.ceil(boundary))])
        count=len(between);where='before' if direction=='incoming' else 'after'
        label=(f'Immediately {where} this segment; no intervening segments' if not count
               else f'{count} intervening segments {where} this segment')
        result.append(dict(target_segment_id=target['id'],target_group=target['group_number'],
            target_segment_number=target['segment_number'],direction=where,intervening_segment_count=count,
            relationship=label,intervening_segments=copy.deepcopy(between)))
    return result


def augment(inputs,rules,provider,reference_encoder):
    records=GROUP_PROMPT_CONTEXT.get()
    if not records:return inputs,rules
    from .minimax_reference import reference_inventory
    result=list(inputs);context=[]
    for record in records:
        description=dict(group=record['group'],group_number=record['group_number'],
            first_text_segment_id=record['first_text_id'],last_text_segment_id=record['last_text_id'],
            earlier_authored_prompts=record['earlier_authoring'],later_authored_prompts=record['later_authoring'])
        for direction,key in (('incoming','candidates'),('outgoing','outgoing_candidates')):
            chosen=None;image=None
            for candidate in record[key] if provider!='local' else []:
                try:image,origin=_image(candidate);chosen=candidate;break
                except (OSError,ValueError,RuntimeError,IndexError):continue
            if image is None:
                if direction=='outgoing':continue
                description.update(fallback='Group 1 / Segment 1 prompt and saved Reference-panel data',
                                   first_group_first_segment_prompt=record['first_group_prompt'])
                references=record['first_group_references']
                description['first_group_reference_panel']=[{k:v for k,v in ref.items() if k not in {'image','path','audio_path'}} for ref in references if ref]
                for item in reference_inventory([],references):
                    if provider=='local':ref={**item,'prompt':references[item['reference_slot']].get('notes',''),'observation_available':False}
                    else:
                        try:ref=reference_encoder(item,references[item['reference_slot']],provider)
                        except (OSError,ValueError,RuntimeError,IndexError):ref={**item,'observation_available':False}
                    ref.update(name='Group 1 fallback · '+str(ref.get('name','reference')),group_continuity_fallback=True,
                               group=record['group'],context_only=True,reference_mode_input=True)
                    media_key=next((k for k in ('image','video','audio') if ref.get(k)),None)
                    if media_key and any(existing.get(media_key)==ref[media_key] for existing in result):continue
                    result.append(ref)
                continue
            incoming=direction=='incoming'
            exact=(origin in ({'composited blend start','attached group start'} if incoming
                             else {'composited blend end','blend destination end'})
                   or incoming and chosen['source_group']==record['previous_group'] and origin=='blend source start'
                   or chosen['source_group']==record['group'] and origin=='authored visual')
            label=('Incoming continuity frame — continue from this visual state.' if incoming else
                   'Outgoing continuity frame — lead into this visual state.')
            context_label=('Exact boundary anchor' if exact else 'Earlier visual context' if incoming else 'Later visual context')
            relationships=_relationships(record,chosen,direction)
            description[direction]=dict(label=label,context=context_label,source_group=record['group_numbers'][chosen['source_group']],
                                       source=origin,segment_relationships=relationships)
            if incoming:description['anchor_exact_start']=exact
            else:description['anchor_exact_end']=exact
            result.append(dict(name=f"Group {record['group_number']} — {label}",role='start' if incoming else 'end',kind='image',
                image=image,group_start_anchor=incoming,group_end_anchor=not incoming,reference_mode_input=True,
                context_only=True,group=record['group'],first_text_segment_id=record['first_text_id'],
                last_text_segment_id=record['last_text_id'],source_time=chosen['time'],source_group=chosen['source_group'],source_group_number=record['group_numbers'][chosen['source_group']],
                anchor_source=origin,anchor_exact_start=exact if incoming else False,anchor_exact_end=exact if not incoming else False,
                source_segment_id=chosen.get('segment').id if chosen.get('segment') is not None else None,
                continuity_label=label,context_label=context_label,segment_relationships=relationships,
                prompt_label='READ-ONLY VISUAL CONTINUITY — NO NEW OUTPUT SEGMENT'))
        context.append(description)
    rules+='''\n\nGROUP STARTING AND ENDING CONTINUITY (read-only context, not extra output segments):
Incoming continuity frame: continue from this visual state. Outgoing continuity frame: lead into this visual state. For Generate and Refine, describe an exact incoming appearance in the group's first text segment's prompt text itself, and develop toward the exact outgoing appearance in its last text segment. Preserve authored motion, timing, dialogue, media, settings and the user's editing scope. Global-only output remains global instructions.
Each image is labeled with its direction and the number of authored segments strictly between each target segment and the continuity frame, including across group boundaries. Read their ordered prompts/actions to understand the intervening progression. Zero means immediately before/after that segment. A distant Earlier/Later visual context frame is not an exact boundary state: account for intervening actions rather than copying that distant appearance directly or skipping those actions. An exact incoming group boundary describes the opening state at local time zero. A cut does not force a continuous shot.
Selected-segment edits affect only that segment. Describe the incoming state explicitly only when editing the group's first text segment; describe the outgoing state explicitly only when editing its last text segment. Otherwise use both frames as continuity context. Do not rewrite other groups, add segments for these context images, invent missing observations, emit filenames/new reference IDs, or change the response schema. When no incoming visual exists, use the Group 1 / Segment 1 prompt and saved Reference-panel data.
SNAPSHOT OF GROUP CONTEXT:\n'''+json.dumps(context,ensure_ascii=False)
    if provider=='local':rules+='\nThe local text-only writer cannot observe pixels or audio. Use authored prompts and reference notes; do not claim to have seen an image.'
    return result,rules
