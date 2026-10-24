"""Unchanged prefix blocks remain reusable when only a later group changes."""
import copy
import threading
from pathlib import Path
from ltx_prompt_director.models import Segment
from ltx_prompt_director.render_cache import cached_render


def test_only_changed_group_and_changed_incoming_video_invalidate_cache(tmp_path):
    cache={};calls=[];cancel=threading.Event()
    request=dict(kind='video',prompt='Shared production',duration=15,width=960,height=544,steps=20,seed=-1,
                 profile=dict(video_model='/model',video_preview_mode='noisy'),references=[])
    segments=[Segment('First','','','text','text','Opening',15),Segment('Next','','','text','text','Continue',5,render_group=1)]
    def run(group,req,previous=''):
        def render():
            calls.append(group);path=tmp_path/f'{group}-{len(calls)}.mp4';path.write_bytes(f'output-{len(calls)}'.encode())
            return {'path':str(path)}
        result=cached_render(cache,group,req,[segments[group]],render,cancel,previous)
        cache[str(group)]=result['_cache_record']
        return result
    first=run(0,request)
    next_request={**request,'duration':5}
    second=run(1,next_request,first['render_token'])
    assert calls==[0,1]
    assert run(0,request)['reused'] and run(1,next_request,first['render_token'])['reused']
    assert calls==[0,1]
    segments[1].prompt='Turn toward the camera'
    assert run(0,request)['reused']
    assert not run(1,next_request,first['render_token'])['reused']
    assert calls==[0,1,1]
    segments[0].prompt='Different opening'
    new_first=run(0,request)
    assert not run(1,next_request,new_first['render_token'])['reused']
    assert calls==[0,1,1,0,1]
    Path(new_first['path']).unlink()
    assert not run(0,request)['reused']


def test_display_settings_do_not_re_render_and_output_changes_do(tmp_path):
    cache={};path=tmp_path/'video.mp4';cancel=threading.Event();calls=[]
    request=dict(kind='video',prompt='Production',profile={'video_preview_mode':'noisy'},references=[])
    def render():calls.append(1);path.write_bytes(b'output');return {'path':str(path)}
    result=cached_render(cache,0,request,[],render,cancel);cache['0']=result['_cache_record']
    changed=copy.deepcopy(request);changed['profile']['video_preview_mode']='denoised'
    assert cached_render(cache,0,changed,[],render,cancel)['reused']
    path.write_bytes(b'replaced output')
    assert not cached_render(cache,0,request,[],render,cancel)['reused']
    assert len(calls)==2
