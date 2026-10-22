"""Sampling previews derived from existing H3 and Flux model predictions."""
from pathlib import Path


class FluxImagePreview:
    """Decode Klein's existing clean prediction without changing sampling."""
    def __init__(self, pipe, root, width, height, total, every, emit):
        self.pipe, self.root = pipe, Path(root)
        self.width, self.height, self.total = width, height, total
        self.every, self.emit = max(1, every), emit
        self.clean = None
        self.done = None
        scheduler = pipe.scheduler
        original_step = scheduler.step

        def step(model_output, timestep, sample, *args, **kwargs):
            self.clean = None
            result = original_step(model_output, timestep, sample, *args, **kwargs)
            # Euler advances its index in step(). Use the sigma and inputs for
            # the step just completed, including Klein's combined CFG velocity.
            done = scheduler.step_index
            if 0 < done < self.total and done % self.every == 0:
                sigma = scheduler.sigmas[done - 1].to(device=sample.device)
                self.clean = sample.detach().float() - sigma * model_output.detach().float()
                self.done = done
            return result

        scheduler.step = step

    def finish_step(self, done):
        clean, self.clean = self.clean, None
        if clean is None or done != self.done: return
        from diffusers.utils.torch_utils import get_module_execution_device
        pipe = self.pipe
        ph = self.height // (pipe.vae_scale_factor * 2)
        pw = self.width // (pipe.vae_scale_factor * 2)
        latents = clean.transpose(1, 2).reshape(clean.shape[0], -1, ph, pw)
        mean = pipe.vae.bn.running_mean.view(1, -1, 1, 1).to(latents)
        std = (pipe.vae.bn.running_var.view(1, -1, 1, 1) + pipe.vae.config.batch_norm_eps).sqrt().to(latents)
        latents = pipe._unpatchify_latents(latents * std + mean)
        latents = latents.to(device=get_module_execution_device(pipe.vae), dtype=pipe.vae.dtype)
        decoded = pipe.vae.decode(latents, return_dict=False)[0]
        image = pipe.image_processor.postprocess(decoded, output_type='pil')[0]
        path = self.root / f'preview-{done}.png'
        image.save(path)
        self.emit('preview', path=str(path), media='image', draft=True,
                  latent_source='predicted_clean', step=done, sampling_steps=self.total)


# Fixed H3 latent-to-RGB projection coefficients. This is an approximate color
# view of predicted clean latents, not a VAE reconstruction.
H3_RGB = (
    (-.018555,.024344,-.017536),(.150164,.137244,.129221),(.027367,-.050369,-.208606),
    (-.000793,-.164622,-.323161),(-.048556,.013970,-.074286),(.011740,.014172,-.006906),
    (.061517,.061212,.110025),(.035321,.086879,.110059),(-.017426,.002997,.035356),
    (.531539,.548819,.624404),(-.024968,-.040234,-.034302),(-.032549,-.029096,-.017221),
    (.022609,.020286,.050661),(-.084001,-.038131,-.020805),(-.018830,.010412,.061120),
    (.020777,.011196,-.030994),(-.008390,-.012201,-.025687),(-.013281,-.002924,.006331),
    (.000260,.001833,-.011038),(.105471,.100482,.132106),(.016529,.015213,.009999),
    (-.014015,-.017438,-.019134),(-.033787,-.009984,-.019725),(.004224,.017284,.027196))


def noisy_latent_video(latents, path, width, height, fps=24):
    """Stream all current temporal samples into a lightweight, silent movie.

    H3 represents 17 video frames in each five-token temporal chunk, plus an
    opening five frames. Interpolate the cheap RGB projection across that grid;
    the full VAE decode remains separate. No model is loaded for this preview.
    """
    import av
    import numpy as np
    import torch
    from PIL import Image
    samples = latents.detach()[0].to(device='cpu',dtype=torch.float32)
    if samples.shape[0]!=len(H3_RGB):raise ValueError('Expected 24 H3 latent channels.')
    rgb = torch.einsum('cthw,cr->thwr',samples,torch.tensor(H3_RGB))
    rgb = torch.nan_to_num(rgb+torch.tensor((.057426,-.022078,-.071449)))
    rgb = ((rgb.clamp(-1,1)+1)*127.5).numpy()
    count = max(1,((samples.shape[1]-2)//5)*17+5)
    spans=np.array([1 if index%5==0 else 4 for index in range(len(rgb))])
    times=np.concatenate(([0],np.cumsum(spans[:-1])))
    scale = min(1,640/max(width,height))
    size = tuple(max(2,round(v*scale/2)*2) for v in (width,height))
    target=Path(path);partial=target.with_suffix('.partial.mp4')
    try:
        with av.open(str(partial),'w') as container:
            stream=container.add_stream('libx264',rate=fps)
            stream.width,stream.height=size;stream.pix_fmt='yuv420p'
            stream.options={'preset':'ultrafast','crf':'26'}
            for index in range(count):
                low=max(0,int(np.searchsorted(times,index,side='right'))-1)
                high=min(low+1,len(rgb)-1)
                blend=0 if high==low else (index-times[low])/(times[high]-times[low])
                pixels=(rgb[low]*(1-blend)+rgb[high]*blend).clip(0,255).astype(np.uint8)
                image=Image.fromarray(pixels).resize(size,Image.Resampling.BILINEAR)
                frame=av.VideoFrame.from_image(image);frame.pts=index
                for packet in stream.encode(frame):container.mux(packet)
            for packet in stream.encode():container.mux(packet)
        partial.replace(target)
    finally:partial.unlink(missing_ok=True)
    return count


def denoised_preview_due(profile, done, total):
    return (profile.get('video_preview_mode', 'noisy') == 'denoised'
            and 0 < done < total
            and done % max(1, int(profile.get('video_preview_every', 5))) == 0)


class H3VideoPreview:
    def __init__(self, pipe, profile, root, width, height, total, dtype, emit):
        self.pipe, self.profile, self.root = pipe, profile, Path(root)
        self.width, self.height, self.total = width, height, total
        self.dtype, self.emit, self.clean = dtype, emit, None
        self.fast = None

    def noisy(self, latents, done):
        if self.profile.get('video_preview_mode', 'noisy') != 'noisy': return
        path = self.root / f'latent-{done}.mp4'
        count=noisy_latent_video(latents,path,self.width,self.height)
        self.emit('preview', path=str(path), media='video', draft=True, frames=count,
                  preview_mode='noisy', latent_source='predicted_clean' if done else 'initial_noise',
                  step=done, sampling_steps=self.total)

    def capture_clean(self, scheduler, latents, index, noise_pred, input_latents=None, inpaint_mask=None):
        self.clean = None
        self.fast = None
        done = index + 1
        fast = self.profile.get('video_preview_mode', 'noisy') == 'noisy'
        if not fast and not denoised_preview_due(self.profile, done, self.total): return
        # Flow velocity already exists from this sampling pass. Estimate x0 for
        # the full clip; never run an extra denoiser forward pass.
        selection = (slice(None), slice(None), slice(None))
        current = latents[selection].detach().float()
        velocity = noise_pred[selection].detach().float()
        estimate = current - velocity * scheduler.sigmas[index].to(current)
        if inpaint_mask is not None:
            mask = inpaint_mask[selection].to(estimate)
            estimate = estimate * mask + input_latents[selection].to(estimate) * (1 - mask)
        if fast:self.fast = estimate.to('cpu')
        else:self.clean = estimate

    def finish_step(self, latents, done):
        if self.fast is not None:
            self.noisy(self.fast, done)
            self.fast = None
        if self.clean is None: return
        from diffsynth.utils.data import save_video
        self.emit('log', message=f'Denoised preview · VAE decode · step {done}/{self.total}')
        try:
            self.pipe.load_models_to_device(['video_vae'])
            decoded = self.pipe.video_vae.decode_video(self.clean, dtype=self.dtype,
                tiled=True, tile_size=256, tile_overlap=64)
            frames = self.pipe.vae_output_to_video(decoded, min_value=0, max_value=1)
            path = self.root / f'preview-{done}.mp4'
            save_video(frames, str(path), fps=24, quality=5)
            self.emit('preview', path=str(path), media='video', draft=True,
                      preview_mode='denoised', step=done, sampling_steps=self.total)
        finally:
            self.clean = None
            decoded = frames = None
            self.pipe.load_models_to_device(self.pipe.in_iteration_models)
