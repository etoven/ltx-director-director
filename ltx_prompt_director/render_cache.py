"""Content-dependent reuse of completed native video blocks."""
import copy
import hashlib
import json
from pathlib import Path


def file_identity(path):
    try:
        stat=Path(path).stat()
        return [stat.st_size,stat.st_mtime_ns]
    except OSError:return None


def block_signature(request,segments,previous_token=''):
    profile=request.get('profile',{})
    selected={key:value for key,value in profile.items() if key.startswith('video_') and not key.startswith('video_preview') and key!='video_duration'}
    references=copy.deepcopy(request.get('references',[]))
    media=[]
    for index,reference in enumerate(references):
        for field in tuple(reference):
            if field.startswith('archive'):reference.pop(field,None)
        previous=index==0 and previous_token and reference.get('kind')=='video'
        for key in ('path','audio_path'):
            if reference.get(key):
                if previous:reference[key]='<previous-block>'
                else:media.append([reference[key],file_identity(reference[key])])
    records=[]
    for segment in segments:
        records.append({key:value for key,value in segment.to_dict().items()
                        if key not in {'id','preview_path','artwork_status','render_group','prompt_detached'}})
        # Origin labels are archive metadata, not model inputs. Keep the
        # original empty fields so existing block signatures remain compatible.
        records[-1]['source_project_id']='';records[-1]['source_prompt_type']=''
        if segment.media_path:media.append([segment.media_path,file_identity(segment.media_path)])
    for key in ('video_model',):
        if selected.get(key):media.append([selected[key],file_identity(selected[key])])
    value=dict(request={key:value for key,value in request.items() if key not in {'profile','references'}},
               profile=selected,references=references,segments=records,media=media,previous=previous_token)
    def canonical(item):
        if isinstance(item,float) and item.is_integer():return int(item)
        if isinstance(item,dict):return {key:canonical(value) for key,value in item.items()}
        if isinstance(item,(list,tuple)):return [canonical(value) for value in item]
        return item
    return hashlib.sha256(json.dumps(canonical(value),sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def cached_render(cache,group,request,segments,render,cancel,previous_token=''):
    signature=block_signature(request,segments,previous_token)
    entry=cache.get(str(group),{})
    result=entry.get('result',{})
    path=result.get('path','')
    if (path and Path(path).is_file() and entry.get('signature')==signature and entry.get('identity')==file_identity(path)
            and file_identity(path) is not None):
        return {**result,'reused':True,'_cache_record':entry}
    if cancel.is_set():raise RuntimeError('Video render cancelled.')
    result=render()
    path=Path(result['path'])
    if not path.is_file():return result  # Test executors may return symbolic output paths.
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            if cancel.is_set():raise RuntimeError('Video render cancelled.')
            digest.update(chunk)
    stored={key:value for key,value in result.items() if key not in {'reused','_cache_record'}}
    stored['render_token']=digest.hexdigest()
    entry=dict(signature=signature,identity=file_identity(path),result=stored)
    return {**stored,'reused':False,'_cache_record':entry}
