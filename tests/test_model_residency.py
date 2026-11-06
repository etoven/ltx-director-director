"""Resident-worker protocol and lifetime checks, using tiny CPU-only stand-ins."""
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import patch
import pytest
from ltx_prompt_director import local_generation as local
from ltx_prompt_director.local_session import MANAGER


@pytest.fixture
def resident_backend(tmp_path,monkeypatch):
    MANAGER.close();monkeypatch.setenv('XDG_DATA_HOME',str(tmp_path))
    real=subprocess.Popen;children=[]
    code='''import sys,os,json,contextlib
from pathlib import Path
from ltx_prompt_director import local_worker as worker
from ltx_prompt_director import local_model_cache as cache
from ltx_prompt_director.local_protocol import emit
loads=0
class Runtime:
 def __init__(self):
  global loads
  loads+=1;self.number=loads

def generate(request):
 runtime=cache.model(request,Runtime)
 if request.get('invalid'):raise ValueError('Invalid request')
 if request.get('pressure') and not request.get('retried'):
  marker=Path(request['pressure'])
  if not marker.exists():marker.touch();raise RuntimeError('CUDA out of memory')
 if request.get('wait'):
  import time;time.sleep(30)
 emit('result',pid=os.getpid(),loads=loads,model=runtime.number,text=request.get('prompt',''))
worker.image_generation=generate;worker.video_generation=generate
worker.serve_requests(sys.argv[1])
'''
    def launch(command,**kwargs):
        child=real([command[0],'-u','-c',code,command[3]],**kwargs)
        children.append(child);return child
    monkeypatch.setattr(local.subprocess,'Popen',launch)
    token=local.LOCAL_KEEP_LOADED.set(True)
    try:yield children
    finally:local.LOCAL_KEEP_LOADED.reset(token);MANAGER.close()


def request(**values):
    return dict(kind='image',profile=dict(python=sys.executable,device='cpu',image_model='one'),**values)


def test_model_and_worker_survive_idle_and_prompt_changes(resident_backend):
    one=local.run_request(request(prompt='First'))
    two=local.run_request(request(prompt='Second'))
    assert one['pid']==two['pid'] and two['loads']==1
    assert two['text']=='Second' and len(resident_backend)==1
    assert resident_backend[0].poll() is None
    assert not list(local.data_root().rglob('request.json'))


def test_model_switch_releases_previous_worker(resident_backend):
    one=local.run_request(request())
    other=request();other['profile']['image_model']='two'
    two=local.run_request(other)
    assert one['pid']!=two['pid'] and resident_backend[0].poll() is not None
    assert two['loads']==1 and len(resident_backend)==2


def test_invalid_input_keeps_model_but_memory_pressure_reloads(resident_backend,tmp_path):
    first=local.run_request(request())
    with pytest.raises(RuntimeError,match='Invalid request'):local.run_request(request(invalid=True))
    assert local.run_request(request())['pid']==first['pid']
    result=local.run_request(request(pressure=str(tmp_path/'oom-once')))
    assert result['pid']!=first['pid'] and len(resident_backend)==2


def test_active_cancellation_releases_worker(resident_backend):
    local.run_request(request())
    cancel=threading.Event();timer=threading.Timer(.2,cancel.set);timer.start()
    try:
        with pytest.raises(InterruptedError):local.run_request(request(wait=True),cancel=cancel)
    finally:timer.cancel()
    assert resident_backend[0].poll() is not None and MANAGER.current is None


def test_job_callbacks_and_conditioning_are_restored():
    from ltx_prompt_director.local_model_cache import job_scope
    class Object:
        def step(self):return 'original'
    class Module:
        def __init__(self):self._forward_pre_hooks={1:'permanent'}
    pipe=Object();pipe.scheduler=Object();pipe.transformer=Module();unit=Object();pipe.units=[unit]
    with job_scope(pipe):
        pipe.step=lambda:'old prompt';pipe.scheduler.step=lambda:'old preview';unit.step=lambda:'old reference'
        pipe.transformer._forward_pre_hooks[2]='temporary'
    assert pipe.step()=='original' and pipe.scheduler.step()=='original' and unit.step()=='original'
    assert pipe.transformer._forward_pre_hooks=={1:'permanent'}


def test_audio_adapter_reuses_weights_and_clears_voice_conditioning(tmp_path,monkeypatch):
    import wave
    from types import ModuleType
    from ltx_prompt_director import local_audio,local_model_cache as cache
    loaded=[];voices=[]
    class IndexTTS:
        def __init__(self,**kwargs):
            loaded.append(self);self.cache_spk_cond=None;self.cache_s2mel_prompt=None
        def infer(self,**kwargs):
            assert self.cache_spk_cond is None and self.cache_s2mel_prompt is None
            self.cache_spk_cond=self.cache_s2mel_prompt=kwargs['spk_audio_prompt']
            voices.append(kwargs['spk_audio_prompt'])
            with wave.open(kwargs['output_path'],'wb') as wav:
                wav.setparams((1,2,16000,0,'NONE','not compressed'));wav.writeframes(b'\0\0'*160)
    parent=ModuleType('indextts');module=ModuleType('indextts.infer_v2_5');module.IndexTTS2=IndexTTS
    monkeypatch.setitem(sys.modules,'indextts',parent);monkeypatch.setitem(sys.modules,'indextts.infer_v2_5',module)
    monkeypatch.setattr(cache,'_ENABLED',True);monkeypatch.setattr(cache,'_MODEL',None);monkeypatch.setattr(cache,'_KEY',None)
    monkeypatch.chdir(tmp_path);monkeypatch.setattr(sys,'path',list(sys.path))
    monkeypatch.setattr(local_audio,'emit',lambda *a,**k:None)
    value=dict(kind='audio',audio_kind='dialog',audio_config=dict(source=str(tmp_path),model=str(tmp_path)),
               job_dir=str(tmp_path),profile=dict(device='cpu'),sample='voice-one.wav',prompt='First')
    local_audio._generate_audio(value)
    local_audio._generate_audio({**value,'sample':'voice-two.wav','prompt':'Second'})
    assert len(loaded)==1 and voices==['voice-one.wav','voice-two.wav']
    assert loaded[0].cache_spk_cond is None and loaded[0].cache_s2mel_prompt is None
