"""Independent production contexts loaded into the existing shared editors."""
import copy
import hashlib
import json

CONTEXT_KEYS=('projectType','workspaceDefinition','globalPrompt','directorIntent','directionOptions','magicBuild','minimaxH3','output')

def normalize_context(context):
    from .minimax_reference import reference_slots
    context=copy.deepcopy(context)
    production=context.get('minimaxH3',{})
    production['referenceImages']=reference_slots(production.get('referenceImages',[]))
    for reference in production['referenceImages']:
        if reference:
            reference.pop('archiveMedia',None);reference.pop('archiveAudio',None)
    context['minimaxH3']=production
    return context

def context_from_payload(payload):
    return normalize_context({key:copy.deepcopy(payload[key]) for key in CONTEXT_KEYS if key in payload})

def group_context(payload,group):
    return normalize_context(payload.get('renderGroupOptions',{}).get(str(group),{}).get('context') or context_from_payload(payload))

def input_stamp(segments,context):
    """Lightweight authored-input provenance; excludes selection and UI state."""
    from .render_cache import file_identity
    context=normalize_context(context)
    records=[]
    for segment in segments:
        record={key:value for key,value in segment.to_dict().items() if key not in {'id','preview_path','render_group','artwork_status','source_project_id','source_prompt_type'}}
        if segment.media_path:record['mediaIdentity']=getattr(segment,'_media_digest','') or file_identity(segment.media_path)
        records.append(record)
    # Draft cache metadata and blend/selection changes are not generation inputs.
    production=context.get('minimaxH3',{})
    references=copy.deepcopy(production.get('referenceImages',[]))
    for reference in references:
        if reference:
            for field in ('path','audio_path'):
                if reference.get(field):reference[field+'Identity']=file_identity(reference[field])
    value=dict(segments=records,projectType=context.get('projectType'),workspaceDefinition=context.get('workspaceDefinition'),
               globalPrompt=context.get('globalPrompt'),prompt=production.get('prompt'),mode=production.get('mode'),
               references=references,output=context.get('output'),
               duration=context.get('directionOptions',{}).get('requestedLength'))
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str).encode()).hexdigest()


def production_prompt(context):
    mode=context.get('workspaceDefinition',{}).get('prompt_mode')
    unified=mode in {'unified','prompt_only'} or mode is None and str(context.get('projectType','')).startswith('minimax')
    return context.get('minimaxH3',{}).get('prompt','') if unified else context.get('globalPrompt','')
