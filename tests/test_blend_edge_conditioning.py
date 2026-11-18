"""Focused blend-window and conditional-latent contract; no model weights."""
from types import SimpleNamespace
from pathlib import Path
import copy,json
import pytest
from ltx_prompt_director.models import Segment
from ltx_prompt_director.blend_conditioning import outgoing_spec,outgoing_reference,append_outgoing
from ltx_prompt_director.video_extensions import continuation_reference
from ltx_prompt_director.queue_persistence import video_spec
from ltx_prompt_director.local_conditioning import render_conditioning_inputs,install_ref2va_conditioning


def test_text_edges_use_asymmetric_trimmed_windows_and_survive_queue_spec(tmp_path,monkeypatch):
    # An audio/video source and a silent source cover both boundary media types.
    from test_group_prompt_context import owner_fixture
    owner,groups,movie=owner_fixture(tmp_path)
    owner.render_group_duration=lambda group:4
    owner.render_group_options['2']=dict(clip=dict(path=str(movie),duration=3,trim_start=12),attachmentRevision=7)
    owner.extension_transition=lambda group:dict(blend='perceptual_crossfade',overlap=(22 if group==1 else 39)/24,side='destination')
    incoming=continuation_reference(movie,3,22/24,trim_start=12)
    spec=outgoing_spec(owner,1,groups[1]);outgoing=outgoing_reference(spec,4)
    assert incoming['start']==pytest.approx(.5+3-22/24) and incoming['timeline_time']==0
    assert outgoing['start']==.5 and outgoing['duration']==39/24
    assert outgoing['timeline_time']==4-39/24 and outgoing['timeline_end']==4
    assert outgoing['blend_frames']==39 and outgoing['blend_side']=='destination'
    assert 'audio_path' not in outgoing
    assert outgoing_spec(owner,2,groups[2]) is None
    original=groups[1][-1].kind;groups[1][-1].kind='image'
    assert outgoing_spec(owner,1,groups[1]) is None
    groups[1][-1].kind=original
    owner.extension_transition=lambda group:dict(blend='cut',overlap=0,side='source')
    assert outgoing_spec(owner,1,groups[1]) is None
    persisted=json.loads(json.dumps(video_spec(dict(kind='video',duration=4),groups[1],[],outgoing=spec)))
    restored=append_outgoing([],persisted['outgoing'],4,'fl2va')
    assert restored[0]['timeline_time']==4-39/24
    with_audio={**outgoing,'audio_path':str(movie)}
    assert 'audio_path' not in render_conditioning_inputs([with_audio],[],'fl2va')[0]
    assert render_conditioning_inputs([with_audio],[],'ref2va')[0]['audio_path']==str(movie)
    from ltx_prompt_director.render_cache import block_signature
    request=dict(kind='video',profile={},conditioning=[outgoing])
    before=block_signature(request,groups[1]);request['conditioning'][0]['start']+=1/24
    assert block_signature(request,groups[1])!=before


def test_ref2va_uses_native_video_audio_masks_without_reference_blocks():
    import importlib.util
    if importlib.util.find_spec('diffsynth') is None:pytest.skip('Runs against the copied private backend')
    import torch
    from PIL import Image
    # Load the installed backend's unit definitions without importing its
    # unrelated model catalogue. The fixture never loads model weights.
    import ast,importlib.util,types,sys,math
    from unittest.mock import patch
    import numpy as np
    backend=Path(importlib.util.find_spec('diffsynth').origin).parent
    class Unit:
        def __init__(self,**fields):self.__dict__.update(fields)
    namespace=dict(torch=torch,np=np,math=math,PipelineUnit=Unit)
    names={'MiniMaxH3Unit_PackedSequenceBuilder','MiniMaxH3Unit_VideoRetakeEmbedder','MiniMaxH3Unit_AudioRetakeEmbedder'}
    nodes=[node for node in ast.parse((backend/'pipelines/minimax_h3_audio_video.py').read_text()).body if isinstance(node,ast.ClassDef) and node.name in names]
    exec('from __future__ import annotations\n'+ast.unparse(ast.Module(body=nodes,type_ignores=[])),namespace)
    MiniMaxH3Unit_PackedSequenceBuilder=namespace['MiniMaxH3Unit_PackedSequenceBuilder']
    MiniMaxH3Unit_VideoRetakeEmbedder=namespace['MiniMaxH3Unit_VideoRetakeEmbedder']
    MiniMaxH3Unit_AudioRetakeEmbedder=namespace['MiniMaxH3Unit_AudioRetakeEmbedder']
    audio_module=types.ModuleType('diffsynth.utils.data.audio')
    nodes=[node for node in ast.parse((backend/'utils/data/audio.py').read_text()).body if isinstance(node,ast.FunctionDef) and node.name in {'convert_to_stereo','resample_waveform'}]
    exec('from __future__ import annotations\n'+ast.unparse(ast.Module(body=nodes,type_ignores=[])),{'torch':torch},audio_module.__dict__)
    from ltx_prompt_director.local_model_cache import job_scope
    pipe=SimpleNamespace(device='cpu',torch_dtype=torch.float32)
    pipe.preprocess_video=lambda frames,**kw:torch.from_numpy(np.stack([np.asarray(frame) for frame in frames])).permute(3,0,1,2).unsqueeze(0).float()/255
    video,audio,builder=MiniMaxH3Unit_VideoRetakeEmbedder(),MiniMaxH3Unit_AudioRetakeEmbedder(),MiniMaxH3Unit_PackedSequenceBuilder()
    pipe.units=[video,audio,builder];pipe.load_models_to_device=lambda names:None
    encoded=[]
    def encode(tensor,**kwargs):
        encoded.append(tensor.shape[2]);count=(tensor.shape[2]-5)//17*5+2
        return torch.full((1,24,count,4,4),.6)
    pipe.video_vae=SimpleNamespace(encode_video=encode)
    pipe.audio_vae=SimpleNamespace(sample_rate=32000,hop_length=800,
        encode_audio=lambda waveform,**kw:torch.full((2,32,round(waveform.shape[-1]/800)),.3))
    frames=[Image.new('RGB',(64,64),'red') for _ in range(22)]
    head=dict(kind='video',name='Incoming',duration=22/24,timeline_time=0)
    tail=dict(kind='video',name='Outgoing',duration=22/24,timeline_time=4-22/24)
    wave=torch.ones(2,round(32000*22/24))
    old_video=video.process;old_audio=audio.process
    with patch.dict(sys.modules,{'diffsynth.utils.data.audio':audio_module}),job_scope(pipe):
        install_ref2va_conditioning(pipe,[(head,frames),(tail,frames)],lambda *a,**k:None,
                                  audio_conditions=[(head,wave,32000),(tail,wave,32000)])
        inputs=video.process(pipe,None,None,torch.zeros(1,24,32,4,4),64,64,107,True,128,64)
        sound=audio.process(pipe,None,None,torch.zeros(2,32,178))
        vm=inputs['denoise_mask_video'];am=sound['denoise_mask_audio']
        assert vm[:,:,0].eq(0).all() and vm[:,:,15].eq(1).all() and vm[:,:,-4].eq(0).all()
        assert am[...,:36].eq(0).all() and am[...,60:100].eq(1).all() and am[...,124:160].eq(0).all()
        assert inputs['input_latents_video'][:,:,0].eq(.6).all()
        assert sound['input_latents_audio'][...,0].eq(.3).all()
        assert set(inputs)=={'input_latents_video','denoise_mask_video'}
        assert set(sound)=={'input_latents_audio','denoise_mask_audio'}
        assert encoded==[22,22]
    assert video.process==old_video and audio.process==old_audio
