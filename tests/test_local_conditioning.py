import copy
import os
from types import SimpleNamespace

import pytest
from ltx_prompt_director.local_conditioning import video_references
from ltx_prompt_director.models import Segment


def test_timeline_media_precedes_panel_references_and_preserves_timing():
    segments=[Segment('Opening','/opening.png','',role='start',duration=3),
              Segment('Action','','',kind='text',duration=1),
              Segment('Source clip','/clip.mp4','',kind='video',duration=4,trim_start=48),
              Segment('Ending','/ending.png','',role='end',duration=2)]
    panel=[dict(name='Identity',image='data:image/png;base64,example',role='identity'),
           dict(kind='video',path='/voice.mp4',audio_path='/voice.wav',audio_only=True)]
    before=copy.deepcopy([s.to_dict() for s in segments]);original=copy.deepcopy(panel)
    refs=video_references(segments,panel,15)
    assert [r.get('path') for r in refs[:3]]==['/opening.png','/clip.mp4','/ending.png']
    assert [r['timeline_time'] for r in refs[:3]]==[0,4,10]
    assert refs[1]['start']==2 and refs[1]['duration']==4
    assert refs[3]['name']=='Identity' and refs[4]['kind']=='audio' and refs[4]['path']=='/voice.wav'
    assert before==[s.to_dict() for s in segments] and original==panel


def test_out_of_range_conditioning_is_not_silently_discarded():
    with pytest.raises(ValueError,match='beyond'):
        video_references([Segment('Ending','/last.png','',role='end',duration=15)],[],5)


def test_video_conditioning_uses_bounded_tiles_and_valid_short_guide_lengths():
    from unittest.mock import Mock
    from ltx_prompt_director.local_conditioning import configure_video_conditioning_encoder
    encode=Mock();encoder=type('MiniMaxH3Unit_ReferenceEncoder',(),{})()
    pipe=SimpleNamespace(video_vae=SimpleNamespace(encode_video=encode),units=[encoder])
    configure_video_conditioning_encoder(pipe,[dict(kind='video',timeline_time=0)])
    pipe.video_vae.encode_video('video',dtype='bf16',process_image=False)
    encode.assert_called_with('video',dtype='bf16',process_image=False,tiled=True,tile_size=128,tile_overlap=64)
    pipe.video_vae.encode_video('image',dtype='bf16',process_image=True)
    encode.assert_called_with('image',dtype='bf16',process_image=True)
    assert [encoder._trim_reference_video_length(pipe,n) for n in (5,23,48,56)]==[5,22,39,56]
    with pytest.raises(ValueError,match='five'):encoder._trim_reference_video_length(pipe,4)


def test_memory_allowance_counts_video_temporal_rows():
    from ltx_prompt_director.local_conditioning import reference_token_budget
    image=reference_token_budget([dict(kind='image')],960,544,362)
    short=reference_token_budget([dict(kind='video',duration=2)],960,544,362)
    long=reference_token_budget([dict(kind='video',duration=15)],960,544,362)
    assert image==4096 and long>short>=image
    assert long>9*image


@pytest.mark.skipif(os.environ.get('DIRECTOR_TEST_ML')!='1',reason='Private ML runtime CPU test')
def test_packaged_vae_converters_accept_lazy_disk_mappings():
    from ltx_prompt_director.local_h3_quant import video_vae_keys, audio_vae_keys
    class LazyWeights:
        def __iter__(self):return iter(('latents_mean','latents_std'))
        def __getitem__(self,key):raise AssertionError('Normalization metadata must not be loaded.')
    assert video_vae_keys(LazyWeights())=={}
    assert audio_vae_keys(LazyWeights())=={}


@pytest.mark.skipif(os.environ.get('DIRECTOR_TEST_ML')!='1',reason='Private ML runtime CPU test')
def test_ref2va_timeline_coordinates_match_target_grid_and_preserve_panel_refs():
    import torch
    from diffsynth.pipelines.minimax_h3_audio_video import MiniMaxH3Unit_PackedSequenceBuilder
    from ltx_prompt_director.local_conditioning import install_timeline_positions
    builder=MiniMaxH3Unit_PackedSequenceBuilder()
    pipe=SimpleNamespace(units=[builder],device='cpu')
    video=torch.zeros(1,24,12,4,6);audio=torch.zeros(2,32,82)
    blocks=[dict(kind='image',latent_t=1,latent_h=4,latent_w=6),
            dict(kind='video',latent_t=7,latent_h=4,latent_w=6,ref_audio_t=0),
            dict(kind='image',latent_t=1,latent_h=4,latent_w=6)]
    kwargs=dict(ref_blocks=blocks,text_token_tags=torch.ones(11,dtype=torch.long))
    baseline=builder.process(pipe,torch.zeros(11,8),video,audio,**kwargs)['packed']
    events=[]
    install_timeline_positions(pipe,[dict(name='End',timeline_time=2),dict(name='Clip',timeline_time=.5),dict(name='Style')],
        lambda kind,**data:events.append(data))
    result=builder.process(pipe,torch.zeros(11,8),video,audio,**kwargs)['packed']
    positions=result['img_position_ids'];ids=result['img_pos'];target_rows=12*6
    origin=positions[0,ids[-target_rows],0]
    assert torch.all(positions[0,ids[:6],0]==origin+48*builder._FRAME_RESCALE)
    expected=origin+12*builder._FRAME_RESCALE+builder._video_t_grid(7,0).repeat_interleave(6)
    assert torch.equal(positions[0,ids[6:48],0],expected)
    assert torch.equal(positions[0,ids[48:54]],baseline['img_position_ids'][0,ids[48:54]])
    assert torch.equal(positions[0,ids[-target_rows:]],baseline['img_position_ids'][0,ids[-target_rows:]])
    for key in ('img_pos','audio_pos','token_tags','cu_seqlens'):
        assert torch.equal(result[key],baseline[key])
    assert len(events)==2
