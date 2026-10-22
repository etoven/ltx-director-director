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
    preview.capture_clean(SimpleNamespace(sigmas=torch.ones(20)),samples,0,torch.zeros_like(samples))
    preview.finish_step(samples,1)
    assert len(events)==1 and events[0]['media']=='video' and events[0]['frames']==22
    with av.open(events[0]['path']) as movie:
        frames=list(movie.decode(video=0));assert len(frames)==22
        assert movie.streams.video[0].average_rate==24
        assert frames[-1].to_ndarray(format='rgb24').mean()>frames[0].to_ndarray(format='rgb24').mean()+30
    assert torch.equal(samples,before) and not list(tmp_path.glob('*.partial.mp4'))


def test_fast_preview_cancels_high_sigma_noise_without_a_vae(tmp_path):
    import torch
    from types import SimpleNamespace
    from unittest.mock import patch
    from ltx_prompt_director.local_previews import H3VideoPreview
    clean=torch.zeros(1,24,7,4,6);clean[:,9,:,:,3:]=.7
    noise=torch.randn_like(clean);velocity=noise-clean
    preview=H3VideoPreview(SimpleNamespace(),{},tmp_path,96,64,20,torch.bfloat16,lambda *_args,**_kwargs:None)
    sigma=torch.tensor([.999,.95])
    for index in range(2):
        state=clean*(1-sigma[index])+noise*sigma[index]
        before=state.clone();preview.capture_clean(SimpleNamespace(sigmas=sigma),state,index,velocity)
        assert torch.allclose(preview.fast,clean,atol=1e-6)
        with patch.object(preview,'noisy') as show:
            preview.finish_step(state,index+1)
        assert torch.allclose(show.call_args.args[0],clean,atol=1e-6)
        assert torch.equal(state,before) and preview.clean is None and preview.fast is None


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


def test_flux_preview_recovers_clean_prediction_on_shifted_schedule(tmp_path):
    import torch
    from types import SimpleNamespace
    from unittest.mock import Mock
    from PIL import Image
    from diffusers import FlowMatchEulerDiscreteScheduler, Flux2KleinPipeline
    from ltx_prompt_director.local_previews import FluxImagePreview
    torch.set_num_threads(2)
    scheduler=FlowMatchEulerDiscreteScheduler(shift=12)
    scheduler.set_timesteps(4)
    baseline=FlowMatchEulerDiscreteScheduler.from_config(scheduler.config)
    baseline.set_timesteps(4)
    clean=torch.linspace(-.4,.4,6*128).reshape(1,6,128)
    sample=torch.randn(clean.shape,generator=torch.Generator().manual_seed(42))
    velocity=sample-clean
    class VAE(torch.nn.Module):
        dtype=torch.bfloat16
        def __init__(self):
            super().__init__();self.bn=torch.nn.BatchNorm2d(128)
            self.bn.running_mean.copy_(torch.linspace(-.1,.1,128))
            self.bn.running_var.copy_(torch.linspace(.5,1.5,128))
            self.config=SimpleNamespace(batch_norm_eps=1e-4)
            self.decode=Mock(return_value=(torch.zeros(1,3,32,48),))
    vae=VAE();events=[]
    pipe=SimpleNamespace(scheduler=scheduler,vae=vae,vae_scale_factor=8,
        _unpatchify_latents=Flux2KleinPipeline._unpatchify_latents,
        image_processor=SimpleNamespace(postprocess=Mock(return_value=[Image.new('RGB',(48,32))])))
    preview=FluxImagePreview(pipe,tmp_path,48,32,4,2,lambda kind,**data:events.append(dict(kind=kind,**data)))
    mean=vae.bn.running_mean.view(1,128,1,1)
    std=(vae.bn.running_var.view(1,128,1,1)+vae.config.batch_norm_eps).sqrt()
    expected=pipe._unpatchify_latents(clean.transpose(1,2).reshape(1,128,2,3)*std+mean).to(vae.dtype)
    for index,timestep in enumerate(scheduler.timesteps):
        before=sample.clone();before_velocity=velocity.clone()
        expected_step=baseline.step(velocity,timestep,sample,return_dict=False)[0]
        output=scheduler.step(velocity,timestep,sample,return_dict=False)[0]
        assert torch.equal(output,expected_step) and torch.equal(sample,before)
        assert torch.equal(velocity,before_velocity)
        if index==1:
            assert float(scheduler.sigmas[index])>.95
            assert torch.allclose(preview.clean,clean,atol=1e-6)
            assert not torch.allclose(output,clean,atol=.1)
        preview.finish_step(index+1)
        assert preview.clean is None
        sample=output
    # Only step two decodes: the final image is decoded by the pipeline itself.
    vae.decode.assert_called_once()
    assert torch.equal(vae.decode.call_args.args[0],expected)
    assert events[0]['latent_source']=='predicted_clean' and events[0]['step']==2
    assert len(events)==1 and (tmp_path/'preview-2.png').is_file()


@pytest.mark.parametrize('distilled,reference',[(False,False),(False,True),(True,False)])
def test_flux_preview_preserves_real_klein_sampling_and_forward_count(tmp_path,distilled,reference):
    import torch
    from unittest.mock import patch
    from PIL import Image
    from diffusers import AutoencoderKLFlux2, Flux2KleinPipeline, Flux2Transformer2DModel, FlowMatchEulerDiscreteScheduler
    from ltx_prompt_director.local_previews import FluxImagePreview
    torch.set_num_threads(2);torch.manual_seed(42)
    vae=AutoencoderKLFlux2(block_out_channels=(8,8),layers_per_block=1,
        down_block_types=('DownEncoderBlock2D',)*2,up_block_types=('UpDecoderBlock2D',)*2,
        latent_channels=4,norm_num_groups=4,mid_block_add_attention=False).eval()
    transformer=Flux2Transformer2DModel(in_channels=16,num_layers=1,num_single_layers=1,
        attention_head_dim=8,num_attention_heads=2,joint_attention_dim=12,
        axes_dims_rope=(2,2,2,2),guidance_embeds=False).eval()
    pipe=Flux2KleinPipeline(vae=vae,transformer=transformer,scheduler=FlowMatchEulerDiscreteScheduler(),
        text_encoder=None,tokenizer=None,is_distilled=distilled)
    pipe.set_progress_bar_config(disable=True)
    args=dict(prompt_embeds=torch.randn(1,4,12),negative_prompt_embeds=torch.zeros(1,4,12),
        width=24,height=16,num_inference_steps=4,guidance_scale=1 if distilled else 4,output_type='np')
    if reference:args['image']=Image.new('RGB',(64,64),'navy')
    with patch.object(transformer,'forward',wraps=transformer.forward) as forward:
        expected=pipe(**args,generator=torch.Generator().manual_seed(17)).images
    count=forward.call_count
    events=[];preview=FluxImagePreview(pipe,tmp_path,24,16,4,2,lambda kind,**data:events.append(data))
    def callback(pipeline,step,timestep,kwargs):
        before=kwargs['latents'].clone()
        preview.finish_step(step+1)
        assert torch.equal(kwargs['latents'],before)
        return kwargs
    with patch.object(transformer,'forward',wraps=transformer.forward) as forward:
        actual=pipe(**args,generator=torch.Generator().manual_seed(17),callback_on_step_end=callback).images
    assert forward.call_count==count==4*(1 if distilled else 2)
    import numpy as np
    np.testing.assert_array_equal(actual,expected)
    assert actual.shape==(1,16,24,3) and len(events)==1 and events[0]['step']==2
