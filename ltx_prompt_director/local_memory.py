"""Job-scoped CUDA accounting and checkpoint-preserving memory reclamation."""
from collections import OrderedDict
import gc
import sys

_GIB=1024**3
_PEAKS=OrderedDict()


def snapshot(stage, *, reset=False):
    torch=sys.modules.get('torch')
    if torch is None or not torch.cuda.is_available():return None
    free,total=torch.cuda.mem_get_info()
    value=dict(stage=stage,free=free,total=total,allocated=torch.cuda.memory_allocated(),
               reserved=torch.cuda.memory_reserved(),peak=torch.cuda.max_memory_allocated())
    from .local_protocol import emit
    emit('log',message=f"VRAM · {stage}: {value['allocated']/_GIB:.2f} GiB allocated, "
         f"{value['reserved']/_GIB:.2f} reserved, {free/_GIB:.2f} free, {value['peak']/_GIB:.2f} peak",memory=value)
    if reset:torch.cuda.reset_peak_memory_stats()
    return value


def workload(request):
    return (request.get('kind'),request.get('profile',{}).get('video_variant'),
            request.get('width'),request.get('height'),request.get('duration'),
            len(request.get('conditioning',[])),len(request.get('references',[])))


def allowance(request):
    from .local_generation import align_h3_frames
    from .local_conditioning import reference_token_budget,render_conditioning_inputs
    frames=align_h3_frames(request.get('duration',5));width=request['width'];height=request['height']
    refs=render_conditioning_inputs(request.get('conditioning',[]),request.get('references',[]),request['profile'].get('video_variant','fl2va'))
    tokens=(((frames-5)//17)*5+2)*(height//32)*(width//32)
    estimate=max(3,(tokens+reference_token_budget(refs,width,height,frames)+2048)*21504*2*2.5/_GIB)+1
    return max(estimate,_PEAKS.get(workload(request),0)+1)


def pressured(state, required=0):
    return state is not None and state['free']<max(_GIB,state['total']*.1,required*_GIB)


def offload(pipe, *, all_models=False):
    if all_models and getattr(pipe,'vram_management_enabled',False):
        pipe.load_models_to_device([])
        return
    for name in ('video_vae','audio_vae','vae'):
        component=getattr(pipe,name,None)
        if component is None:continue
        modules=component.modules() if hasattr(component,'modules') else (component,)
        for module in modules:
            if getattr(module,'vram_management_enabled',False) and callable(getattr(module,'offload',None)):
                module.offload()
    # Accelerate owns Flux's CPU offload hooks and their attached tensors.
    if callable(getattr(pipe,'maybe_free_model_hooks',None)):pipe.maybe_free_model_hooks()


def prepare(pipe,request):
    state=snapshot('before job',reset=True)
    if request['kind']!='video' or state is None or request.get('profile',{}).get('device','cuda')!='cuda':return
    reserve=allowance(request)
    if pressured(state,reserve):
        gc.collect();sys.modules['torch'].cuda.empty_cache()
        offload(pipe,all_models=True)
    ceiling=max(0,state['total']/_GIB-reserve)
    configured=float(request.get('profile',{}).get('vram_gb',0) or 0)
    if configured>0:ceiling=min(ceiling,configured)
    for module in pipe.modules():
        if hasattr(module,'vram_limit'):module.vram_limit=ceiling
    from .local_protocol import progress
    progress(f'VRAM budget: {reserve:.2f} GiB activation/headroom; {ceiling:.2f} GiB device ceiling')


def finish(pipe=None,request=None):
    state=snapshot('job tensors released')
    if pipe is not None:offload(pipe,all_models=pressured(state))
    gc.collect()
    torch=sys.modules.get('torch')
    if torch is not None and torch.cuda.is_available():torch.cuda.empty_cache()
    snapshot('after cleanup')


def observe(request,stage,baseline):
    state=snapshot(stage)
    if state is not None:
        growth=max(0,state['peak']-(baseline or 0))/_GIB
        key=workload(request);_PEAKS[key]=max(_PEAKS.get(key,0),growth);_PEAKS.move_to_end(key)
        while len(_PEAKS)>32:_PEAKS.popitem(last=False)
    return state


def sampling_boundary(pipe):
    """Reclaim inactive stages between steps, never inside a model forward."""
    torch=sys.modules.get('torch')
    if torch is None or not torch.cuda.is_available():return
    free,total=torch.cuda.mem_get_info()
    if free>=max(_GIB,total*.1):return
    offload(pipe)
    gc.collect();torch.cuda.empty_cache()
    snapshot('sampling pressure relief')
