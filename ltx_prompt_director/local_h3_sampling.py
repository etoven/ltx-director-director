"""Deterministic RES multistep flow integration for native joint H3 sampling.

Uses the exponential-integrator equations from https://arxiv.org/abs/2308.02157.
No ComfyUI runtime is imported. The audio state is carried on the video schedule
and transformed back onto its own flow schedule after each update.
"""
import math
import torch
from diffsynth.diffusion.flow_match import FlowMatchScheduler


class H3ResMultistepScheduler(FlowMatchScheduler):
    def __init__(self, *, audio=False, video_shift=12.0, audio_shift=3.0):
        super().__init__('MiniMax-H3')
        self.audio = audio
        self.video_shift = video_shift
        self.audio_shift = audio_shift
        self.previous = None

    def set_timesteps(self, num_inference_steps=20, denoising_strength=1.0, **kwargs):
        # Match the workflow's simple scheduler, sampled from a 1000-point grid.
        indices = torch.arange(num_inference_steps) * (1000 / num_inference_steps)
        base = (1000 - indices.floor()).float() / 1000 * denoising_strength
        video = self.video_shift * base / (1 + (self.video_shift - 1) * base)
        audio = self.audio_shift * base / (1 + (self.audio_shift - 1) * base)
        self.video_sigmas = video
        self.sigmas = audio if self.audio else video
        self.timesteps = self.sigmas * 1000
        self.previous = None

    def step(self, model_output, timestep, sample, **kwargs):
        index = int(torch.argmin((self.timesteps - timestep.cpu()).abs()))
        sigma = float(self.video_sigmas[index])
        next_sigma = float(self.video_sigmas[index + 1]) if index + 1 < len(self.video_sigmas) else 0.0
        own_sigma = float(self.sigmas[index])
        ratio = self.video_shift / self.audio_shift if self.audio else 1.0
        normalizer = ratio + (1 - ratio) * sigma
        next_normalizer = ratio + (1 - ratio) * next_sigma
        state = sample.float() * normalizer
        denoised = (sample.float() - model_output.float() * own_sigma) * ratio
        if self.previous is None or next_sigma == 0:
            output = state + (state - denoised) * (next_sigma / sigma - 1)
        else:
            old_sigma, old_denoised = self.previous
            h = math.log(sigma / next_sigma)
            c = math.log(sigma / old_sigma) / h
            phi1 = -math.expm1(-h) / h
            phi2 = (1 - phi1) / h
            b2 = phi2 / c
            b1 = phi1 - b2
            output = math.exp(-h) * state + h * (b1 * denoised + b2 * old_denoised)
        self.previous = (sigma, denoised)
        return (output / next_normalizer).to(sample.dtype)
