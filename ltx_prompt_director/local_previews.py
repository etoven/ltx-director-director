"""Cheap live latent views and explicitly requested H3 clean-estimate previews."""
from pathlib import Path


def denoised_preview_due(profile, done, total):
    return (profile.get('video_preview_mode', 'noisy') == 'denoised'
            and 0 < done < total
            and done % max(1, int(profile.get('video_preview_every', 5))) == 0)


def noisy_latent_image(latents, width, height):
    """Visualize actual current latent channels, without running a VAE/model.

    Colors are a fixed channel view, not a decoded prediction of the final RGB.
    Only one tiny latent frame is transferred to the CPU.
    """
    import torch
    from PIL import Image
    frame = latents.detach()[0, :3, latents.shape[2] // 2].float().cpu()
    rgb = ((torch.nan_to_num(frame).tanh() + 1) * 127.5).clamp(0, 255).byte()
    image = Image.fromarray(rgb.permute(1, 2, 0).numpy())
    scale = min(1, 640 / max(width, height))
    return image.resize((max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.NEAREST)


class H3VideoPreview:
    def __init__(self, pipe, profile, root, width, height, total, dtype, emit):
        self.pipe, self.profile, self.root = pipe, profile, Path(root)
        self.width, self.height, self.total = width, height, total
        self.dtype, self.emit, self.clean = dtype, emit, None

    def noisy(self, latents, done):
        if self.profile.get('video_preview_mode', 'noisy') != 'noisy': return
        path = self.root / f'latent-{done}.png'
        noisy_latent_image(latents, self.width, self.height).save(path)
        self.emit('preview', path=str(path), media='image', draft=True,
                  preview_mode='noisy', step=done, sampling_steps=self.total)

    def capture_clean(self, scheduler, latents, index, noise_pred, input_latents=None, inpaint_mask=None):
        self.clean = None
        done = index + 1
        if not denoised_preview_due(self.profile, done, self.total): return
        # Flow velocity already exists from this sampling pass. Estimate x0 for
        # only the opening chunk; never run an extra denoiser forward pass.
        count = min(latents.shape[2], self.pipe.video_vae.tokens_chunk_size)
        selection = (slice(None), slice(None), slice(0, count))
        current = latents[selection].detach()
        velocity = noise_pred[selection].detach()
        self.clean = current - velocity * scheduler.sigmas[index].to(current)
        if inpaint_mask is not None:
            mask = inpaint_mask[selection]
            self.clean = self.clean * mask + input_latents[selection] * (1 - mask)

    def finish_step(self, latents, done):
        self.noisy(latents, done)
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
