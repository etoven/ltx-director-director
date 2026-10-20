import os
import pytest

pytestmark=pytest.mark.skipif(os.environ.get('DIRECTOR_TEST_ML')!='1',reason='Private ML runtime CPU test')


def test_noisy_preview_encodes_every_frame_without_loading_vae(tmp_path):
    import av
    import torch
    from types import SimpleNamespace
    from ltx_prompt_director.local_previews import H3VideoPreview
    # Seven temporal latents represent 22 output frames. Channel nine makes
    # temporal changes visible in all RGB channels, unlike a first-three view.
    samples=torch.zeros(1,24,7,8,12)
    samples[:,9]=torch.linspace(-1,1,7)[None,:,None,None]
    before=samples.clone();events=[]
    preview=H3VideoPreview(SimpleNamespace(),{},tmp_path,96,64,20,torch.bfloat16,
        lambda kind,**data:events.append(dict(kind=kind,**data)))
    preview.finish_step(samples,1)
    assert len(events)==1 and events[0]['media']=='video' and events[0]['frames']==22
    with av.open(events[0]['path']) as movie:
        frames=list(movie.decode(video=0));assert len(frames)==22
        assert movie.streams.video[0].average_rate==24
        assert frames[-1].to_ndarray(format='rgb24').mean()>frames[0].to_ndarray(format='rgb24').mean()+30
    assert torch.equal(samples,before) and not list(tmp_path.glob('*.partial.mp4'))


def test_clean_preview_keeps_all_temporal_samples_and_uses_existing_velocity(tmp_path):
    import torch
    from types import SimpleNamespace
    from ltx_prompt_director.local_previews import H3VideoPreview
    samples=torch.ones(1,24,12,2,2);velocity=torch.full_like(samples,.5)
    preview=H3VideoPreview(SimpleNamespace(),{'video_preview_mode':'denoised'},tmp_path,32,32,20,torch.bfloat16,lambda *_:None)
    preview.capture_clean(SimpleNamespace(sigmas=torch.ones(20)),samples,3,velocity)
    assert preview.clean is None
    preview.capture_clean(SimpleNamespace(sigmas=torch.ones(20)),samples,4,velocity)
    assert preview.clean.shape==samples.shape and torch.equal(preview.clean,torch.full_like(samples,.5))
