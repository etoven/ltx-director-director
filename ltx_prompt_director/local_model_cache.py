"""One retained model runtime; generation data and callbacks never belong here."""
import contextlib
import gc
import json
from pathlib import Path
import sys

_ENABLED = False
_KEY = None
_MODEL = None
FIELDS = {
    'image': ('image_model','image_encoder','image_vae','image_assets','image_variant','device','offload'),
    'video': ('video_model','video_encoder','video_vae','audio_vae','video_assets','video_variant','device','offload','vram_gb'),
    'text': ('llm_model','device'),
}


def file_identity(value):
    if not isinstance(value,str) or not value:return value
    path=Path(value).expanduser()
    try:
        stat=path.stat()
        children=[]
        if path.is_dir():
            files=set()
            for pattern in ('*.json','*.yaml','*.safetensors','*.pth','*.pt','*.bin'):files.update(path.glob(pattern))
            for relative in ('transformer/config.json','text_encoder/config.json','vae/config.json','tokenizer/tokenizer_config.json'):
                child=path/relative
                if child.is_file():files.add(child)
            for child in sorted(files):
                item=child.stat();children.append((str(child.relative_to(path)),item.st_size,item.st_mtime_ns))
            return (str(path.absolute()),'directory',children)
        return (str(path.absolute()),stat.st_size,stat.st_mtime_ns,children)
    except OSError:return value


def runtime_key(request):
    kind=request['kind'];profile=request.get('profile',{})
    config=request.get('audio_config',{}) if kind=='audio' else profile
    fields=('source','model','encoder','vae','device') if kind=='audio' else FIELDS.get(kind,())
    values={key:(str(Path(config[key]).expanduser().absolute()) if key=='source' and config.get(key) else file_identity(config.get(key))) for key in fields}
    if kind=='audio':values['execution_device']=profile.get('device',config.get('device','cuda'))
    if kind in ('image','video'):
        values['loras']=[(file_identity(row.get('path')),row.get('weight',1)) for row in profile.get(kind+'_loras',[])
                         if row.get('enabled',True) and float(row.get('weight',1))!=0]
    return json.dumps([kind,request.get('audio_kind'),profile.get('python'),values],sort_keys=True)


def enable():
    global _ENABLED
    _ENABLED=True


def has_model():return _MODEL is not None


def clear():
    global _MODEL,_KEY
    _MODEL=None;_KEY=None;gc.collect()
    torch=sys.modules.get('torch')
    if torch is not None and torch.cuda.is_available():torch.cuda.empty_cache()


def model(request,loader):
    global _KEY,_MODEL
    if not _ENABLED:return loader()
    key=runtime_key(request)
    if key!=_KEY:
        if _MODEL is not None:clear()
        _MODEL=loader();_KEY=key
    else:
        from .local_protocol import progress
        progress('Reusing loaded '+request.get('audio_kind',request['kind'])+' model…')
    return _MODEL


@contextlib.contextmanager
def job_scope(pipe):
    """Undo job-specific monkey patches and hooks before another render starts."""
    targets=[pipe]
    for name in ('scheduler','scheduler_audio','video_vae','audio_vae'):
        target=getattr(pipe,name,None)
        if target is not None:targets.append(target)
    targets.extend(getattr(pipe,'units',()))
    attributes=[]
    for target in targets:
        for name in ('scheduler','scheduler_audio','step','encode_prompt','unit_runner','decode_video','decode_audio','encode_video',
                     'process','_encode_video_ref','_trim_reference_video_length'):
            if hasattr(target,name):attributes.append((target,name,name in vars(target),vars(target).get(name)))
    modules=[]
    transformer=getattr(pipe,'transformer',None)
    if transformer is not None:modules.append(transformer)
    modules.extend(getattr(getattr(pipe,'dit',None),'blocks',()))
    hooks=[(module,set(getattr(module,'_forward_pre_hooks',{}))) for module in modules]
    try:yield pipe
    finally:
        for module,original in hooks:
            for key in set(module._forward_pre_hooks)-original:module._forward_pre_hooks.pop(key,None)
        for target,name,owned,value in reversed(attributes):
            if owned:setattr(target,name,value)
            elif name in vars(target):delattr(target,name)
