"""Whole-timeline, AI-selected reference reuse without changing authored media."""
import copy
import hashlib
import json
from pathlib import Path
from .minimax_reference import reference_slots, reference_inventory, REFERENCE_ROLES
from .prompt_references import subjects, reference_roster
from .output_templates import layout_for, render, parse_rendered, split_legacy


def snapshot(owner):
    options=copy.deepcopy(owner.render_group_options)
    options.setdefault(str(owner._active_render_group),{})['context']=owner.capture_render_group()
    return dict(project=owner.current_project_id,workspace=owner.workspace_revision,active=owner._active_render_group,
                segments=copy.deepcopy(owner.segments),options=options,states=copy.deepcopy(owner.prompt_template_states))


def fingerprint(bundle):
    value=copy.deepcopy(bundle);value['segments']=[s.to_dict() for s in bundle['segments']]
    # View bookkeeping and definition-job flags are not authored content.
    value['states']={k:{field:v for field,v in state.items() if field in
        {'global','fields','prompts','sources','subjects','subject_sources','custom_subjects','excluded_subjects'}}
        for k,state in value['states'].items()}
    paths={s.media_path for s in bundle['segments'] if s.media_path}
    for options in bundle['options'].values():
        paths.add(options.get('clip',{}).get('path',''))
        for ref in options.get('context',{}).get('minimaxH3',{}).get('referenceImages',[]):
            if ref:paths.update(ref.get(k,'') for k in ('path','audio_path'))
    value['file_identity']={}
    for path in sorted(paths-{''}):
        try:
            status=Path(path).stat();value['file_identity'][path]=(status.st_size,status.st_mtime_ns)
        except OSError:value['file_identity'][path]=None
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()


def _state(context, segments, references, saved):
    state=copy.deepcopy(saved);roster=subjects(segments,references,state)
    if 'global' not in state:
        text=context.get('minimaxH3',{}).get('prompt','')
        parsed=parse_rendered(text,layout_for(context['workspaceDefinition']),segments,roster) if text else None
        global_text,prompts=split_legacy(text,segments) if text else ('',{})
        state['global']=global_text
        state['prompts']=prompts
        if parsed:
            state['global']=parsed.get('global','')
            state['prompts']={s.id:parsed.get(s.id,s.prompt) for s in segments}
            state['fields']={k.removeprefix('field:'):v for k,v in parsed.items() if k.startswith('field:')}
            state['subjects']={r['id']:parsed[r['id']] for r in roster if r['id'] in parsed}
    for field in ('fields','prompts','sources','subjects','subject_sources'):state.setdefault(field,{})
    for s in segments:
        if state['sources'].get(s.id,s.prompt)!=s.prompt:state['prompts'][s.id]=s.prompt
        state['prompts'].setdefault(s.id,s.prompt);state['sources'][s.id]=s.prompt
    return state


def reassess_timeline_context(bundle,provider,model,key,timeout):
    """Two compact requests: select reusable assets, then refine affected documents."""
    from . import ai,response_schemas as schemas
    from .group_prompt_context import _image
    groups={};bank={};inputs=[];unavailable=[]
    def add(identity,value,source):
        bank[identity]=dict(value=copy.deepcopy(value),source=source)
        try:
            record=reference_inventory([], [value])[0]
            encoded=ai._mixed_reference_input(record,value,provider)
        except (OSError,ValueError,RuntimeError,IndexError) as error:
            unavailable.append(dict(source=source,reason=str(error)));bank.pop(identity);return
        encoded.update(name=identity+' — '+source,asset_id=identity,context_only=True)
        inputs.append(encoded)
    def frame(identity,candidate,source):
        try:image,_origin=_image(candidate)
        except (OSError,ValueError,RuntimeError,IndexError) as error:
            unavailable.append(dict(source=source,reason=str(error)));return
        add(identity,dict(kind='image',image=image,name=source,notes=source,role='identity'),source)
    ordered=[]
    for segment in bundle['segments']:
        groups.setdefault(str(segment.render_group),dict(segments=[]))['segments'].append(segment)
        ordered.append(dict(id=segment.id,group=str(segment.render_group),name=segment.name,kind=segment.kind,
                            role=segment.role,duration=segment.duration,prompt=segment.prompt,image_prompt=segment.image_prompt))
    targets={}
    for group_number,(group,entry) in enumerate(groups.items(),1):
        options=bundle['options'].get(group,{})
        context=copy.deepcopy(options.get('context',{}));definition=context.get('workspaceDefinition',{})
        references=reference_slots(context.get('minimaxH3',{}).get('referenceImages',[]))
        state_key=f"{context.get('projectType','')}:{group}"
        saved=bundle['states'].get(state_key,{})
        for slot,ref in enumerate(references):
            if ref:
                ref=copy.deepcopy(ref)
                ref['description']=saved.get('subjects',{}).get(f'reference:{slot}',ref.get('description',ref.get('notes','')))
                ref['audio_description']=saved.get('subjects',{}).get(f'reference:{slot}:audio',ref.get('audio_description',''))
                add(f'group:{group}:reference:{slot}',ref,f"Group {group_number} reference {slot+1}: {ref['name']}; {ref['description']}; audio definition: {ref['audio_description']}")
        for s in entry['segments']:
            if s.kind=='image':
                frame('segment:'+s.id,dict(segment=s,kind='image',time=0,origin='authored artwork'),
                      f'Group {group_number}, segment {s.name}: authored {s.role} artwork; {saved.get("subjects",{}).get("subject:timeline:"+s.id,s.image_prompt or s.prompt)}')
            elif s.kind=='video':
                for direction,seconds in [('opening',max(0,s.trim_start or 0)/24),('ending',max(0,s.trim_start or 0)/24+max(0,s.duration-1/24))]:
                    frame(f'segment:{s.id}:{direction}',dict(segment=s,kind='video',time=seconds,origin=direction),
                          f'Group {group_number}, segment {s.name}: {direction} boundary frame; {s.prompt}')
        clip=options.get('clip',{})
        if clip.get('path'):
            for direction,seconds in [('opening',max(0,float(clip.get('trim_start') or 0))/24),
                    ('ending',max(0,float(clip.get('trim_start') or 0))/24+max(0,float(clip.get('duration') or sum(s.duration for s in entry['segments']))-1/24))]:
                frame(f'group:{group}:attached:{direction}',dict(kind='video',path=clip['path'],time=seconds,origin=direction),
                      f'Group {group_number}: attached video {direction} boundary frame')
        if definition.get('engine')!='minimax_references' or options.get('attachedVideoOnly'):continue
        state=_state(context,entry['segments'],references,saved)
        limit=min(4,int(definition.get('references',{}).get('untimed_slots',0)))
        targets[group]=dict(context=context,references=references,state=state,state_key=state_key,
            segments=entry['segments'],available_slots=[i for i in range(limit) if not references[i]])
    if not targets:return dict(groups={},unavailable=unavailable,added=0)
    addition=schemas.record(source=schemas.text('Exact asset_id from the supplied bank.'),
        name=schemas.text('Descriptive source-aware reference title.'),role={'type':'string','enum':list(REFERENCE_ROLES)},
        description=schemas.text('Definition of what this media contributes; no invented observations.'),
        notes=schemas.text('Why the target segments need this context; distinguish distant context from boundary anchors.'),
        segment_ids={'type':'array','items':schemas.text('Target authored segment UUID.'),'minItems':1})
    selection=schemas.named('timeline_context_selection',schemas.record(groups=schemas.record(**{
        g:dict(type='array',items=addition,minItems=0,maxItems=len(t['available_slots'])) for g,t in targets.items()})))
    roster={g:dict(existing_references=reference_inventory(t['segments'],t['references']),
        definitions=t['state']['subjects'],available_slots=t['available_slots'],
        unified=t['context'].get('minimaxH3',{}).get('prompt','')) for g,t in targets.items()}
    rules=('Review every authored segment across this entire ordered timeline and ALL groups. Decide yourself which '
        'segments lack needed context artwork, identity, setting, motion or visual continuity. Reuse only supplied '
        'asset_ids; add only needed references to free slots, without replacing existing references or duplicating '
        'media already available in that group. You may copy an existing mixed reference or use a supplied video '
        'boundary frame. Do not invent/generate media, impose continuous action across cuts, or treat a distant '
        'frame as the immediate appearance after intervening actions. Definitions and notes label each media source. '
        'Provide source-aware names, roles, definitions and the exact target segment_ids for each addition. '
        'Target group keys are internal IDs; asset source captions use human-readable group numbers. An empty list is valid. '
        'Preserve segments, prompts, timing, settings and media.\nORDERED TIMELINE:\n'+json.dumps(ordered,ensure_ascii=False)+
        '\nTARGET GROUPS:\n'+json.dumps(roster,ensure_ascii=False)+
        '\nASSET BANK:\n'+json.dumps({k:v['source'] for k,v in bank.items()},ensure_ascii=False)+
        '\nUNAVAILABLE MEDIA:\n'+json.dumps(unavailable,ensure_ascii=False))
    raw=ai._structured_raw(inputs,provider,model,key,rules,timeout,schema=selection)
    with schemas.using(selection):schemas.validate(raw)
    chosen=json.loads(raw)['groups'];changed={};added=0
    for group,rows in chosen.items():
        if not rows:continue
        target=targets[group];refs=target['references'];ids={s.id for s in target['segments']}
        seen=set()
        for slot,row in zip(target['available_slots'],rows):
            if row['source'] not in bank or row['source'] in seen or not set(row['segment_ids'])<=ids:
                raise ai.AIResponseFormatError('Context selection returned an unknown source or target segment.')
            seen.add(row['source']);ref=copy.deepcopy(bank[row['source']]['value'])
            ref.update(name=row['name'],role=row['role'],description=row['description'],
                notes=row['notes']+'\nTarget segments: '+', '.join(row['segment_ids'])+'\nSource: '+bank[row['source']]['source'])
            refs[slot]=ref;added+=1
        state=target['state'];records=subjects(target['segments'],refs,state)
        for record in records:
            state['subjects'].setdefault(record['id'],record['prompt'])
            state['subject_sources'][record['id']]=record['prompt']
        fields={'global':state['global'],**{'field:'+k:v for k,v in state['fields'].items()},
            **{s.id:state['prompts'][s.id] for s in target['segments']},
            **{r['id']:state['subjects'][r['id']] for r in records}}
        target.update(fields=fields,records=records);changed[group]=target
    if not changed:return dict(groups={},unavailable=unavailable,added=0)
    contract=schemas.named('timeline_context_refinement',schemas.record(groups=schemas.record(**{
        g:schemas.record(**{k:schemas.text('Complete minimally revised Unified section; preserve unrelated wording.',empty=True)
                            for k in t['fields']}) for g,t in changed.items()})))
    documents={g:dict(fields=t['fields'],references=reference_roster(t['records']),
        global_direction=t['context'].get('globalPrompt',''),director_intent=t['context'].get('directorIntent','')) for g,t in changed.items()}
    raw=ai._structured_raw(inputs,provider,model,key,
        'Quickly refine only these affected Unified document sections to incorporate the newly selected context. '
        'Use the exact ImageN/VideoN/AudioN labels in each group roster; labels are local to that group. '
        'Preserve independent wording, all authored actions and existing definitions. Do not rewrite source Global '
        'instructions or timeline/segment prompts. Retain exact timing and editing scope. Never invent observations. '
        'Return all fields, keeping unrelated sections unchanged. Context notes specify the relevant segments and '
        'source direction; respect intervening actions.\n'+json.dumps(documents,ensure_ascii=False)+
        '\nORDERED TIMELINE:\n'+json.dumps(ordered,ensure_ascii=False),timeout,schema=contract)
    with schemas.using(contract):schemas.validate(raw)
    updates=json.loads(raw)['groups'];result={}
    for group,target in changed.items():
        state=target['state'];state['context_reference_backup']=copy.deepcopy(target['fields'])
        for field,text in updates[group].items():
            if field=='global':state['global']=text
            elif field.startswith('field:'):state['fields'][field[6:]]=text
            elif field in {s.id for s in target['segments']}:state['prompts'][field]=text
            else:state['subjects'][field]=text
        records=target['records']
        for record in records:
            record['prompt']=state['subjects'][record['id']]
            slot=record.get('reference_slot')
            if slot is not None:
                ref=target['references'][slot]
                field='audio_description' if record['media_kind']=='audio' and ref.get('kind')=='video' and not ref.get('audio_only') else 'description'
                ref[field]=record['prompt']
            state['subject_sources'][record['id']]=record['prompt']
        state['reference_inventory']=reference_roster(records)
        state['references_need_reassessment']=False;state['reference_changes']=[]
        state['last_render']=render(layout_for(target['context']['workspaceDefinition']),state['global'],
            [{**s.to_dict(),'prompt':state['prompts'][s.id]} for s in target['segments']],records,state['fields'])
        production=target['context'].setdefault('minimaxH3',{})
        production.update(referenceImages=target['references'],prompt=state['last_render'],sourceHash='')
        draft=production.setdefault('drafts',{}).setdefault(target['context']['projectType'],{})
        draft.update(prompt=state['last_render'],sourceHash='')
        result[group]=dict(context=target['context'],state=state,state_key=target['state_key'])
    return dict(groups=result,unavailable=unavailable,added=added)
