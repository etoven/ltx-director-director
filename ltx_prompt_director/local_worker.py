"""Inference process entrypoint. No Qt, web server, or ComfyUI dependency."""
from __future__ import annotations
import contextlib
import json
import math
import os
from pathlib import Path
import sys
import time

from .local_protocol import emit, progress


def _pil_reference(value):
    from PIL import Image
    import base64
    from io import BytesIO
    if value.get('path') and Path(value['path']).is_file(): return Image.open(value['path']).convert('RGB')
    data = value.get('image', '')
    if data.startswith('data:image/'):
        return Image.open(BytesIO(base64.b64decode(data.split(',', 1)[1]))).convert('RGB')
    return None


def prepare_float_checkpoint(path, cache, callback=progress):
    """Expand scaled FP8 one tensor at a time, without a full in-memory state dict.

    The source is immutable; the derived safetensors cache is written atomically.
    This supports the scaled FP8 Klein and Qwen encoder files commonly distributed
    separately from tokenizer/config folders. It does not reinterpret INT4/AWQ.
    """
    import hashlib
    import struct
    import torch
    from safetensors import safe_open
    path = Path(path)
    with path.open('rb') as stream:
        size = struct.unpack('<Q', stream.read(8))[0]
        if size > 64 * 1024 * 1024: raise ValueError('Invalid safetensors header.')
        header = json.loads(stream.read(size))
    floats = {'F8_E4M3', 'F8_E5M2', 'F8_E4M3FN', 'F8_E5M2FNUZ', 'F8_E4M3FNUZ'}
    if not any(v.get('dtype') in floats for k, v in header.items() if k != '__metadata__'):
        return str(path)
    identity = str(path.resolve()) + str(path.stat().st_mtime_ns) + str(path.stat().st_size)
    target = Path(cache) / (hashlib.sha256(identity.encode()).hexdigest() + '.safetensors')
    if target.is_file(): return str(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    converted = {}; offset = 0
    for key, info in header.items():
        if key == '__metadata__' or key.endswith(('.weight_scale', '.input_scale')): continue
        amount = math.prod(info['shape']) * (2 if info['dtype'] in floats else
                 (info['data_offsets'][1] - info['data_offsets'][0]) // max(1, math.prod(info['shape'])))
        converted[key] = {**info, 'dtype': 'BF16' if info['dtype'] in floats else info['dtype'],
                          'data_offsets': [offset, offset + amount]}
        offset += amount
    import shutil
    if shutil.disk_usage(target.parent).free < offset + 512 * 1024 * 1024:
        raise RuntimeError(f'FP8 compatibility cache needs {offset / 2**30:.1f} GB of free disk space.')
    payload = json.dumps(converted, separators=(',', ':')).encode()
    payload += b' ' * (-len(payload) % 8)
    temporary = target.with_suffix('.partial')
    try:
        with safe_open(str(path), framework='pt', device='cpu') as tensors, temporary.open('wb') as stream:
            stream.write(struct.pack('<Q', len(payload))); stream.write(payload)
            for index, (key, info) in enumerate(converted.items()):
                tensor = tensors.get_tensor(key)
                if header[key]['dtype'] in floats:
                    tensor = tensor.to(torch.bfloat16)
                    scale_key = key.removesuffix('.weight') + '.weight_scale'
                    if scale_key in header:
                        scale = tensors.get_tensor(scale_key).to(torch.bfloat16)
                        if scale.ndim == 1 and tensor.ndim > 1 and scale.numel() == tensor.shape[0]:
                            scale = scale.reshape(-1, *([1] * (tensor.ndim - 1)))
                        tensor = tensor * scale
                stream.write(tensor.contiguous().reshape(-1).view(torch.uint8).numpy().tobytes())
                if index % 20 == 0:
                    callback('Preparing local weights ' + path.name + f' · {index + 1}/{len(converted)} tensors')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return str(target)


def image_generation(request):
    progress('Starting native image runtime…')
    import torch
    from PIL import Image
    from transformers import AutoTokenizer, AutoModelForCausalLM
    from diffusers import Flux2KleinPipeline, Flux2Transformer2DModel, AutoencoderKLFlux2
    from safetensors.torch import load_file
    from .local_generation import data_root, validate_profile
    profile = request['profile']; validate_profile(profile, 'image')
    assets = Path(profile['image_assets'])
    if not (assets / 'model_index.json').is_file():
        raise ValueError('Download the image configuration/tokenizer files in Local models first.')
    cache = data_root() / 'weight-cache'
    model_file = prepare_float_checkpoint(profile['image_model'], cache)
    encoder_file = prepare_float_checkpoint(profile['image_encoder'], cache)
    dtype = torch.bfloat16 if profile.get('device') != 'cpu' else torch.float32
    progress('Loading image model…')
    transformer = Flux2Transformer2DModel.from_single_file(model_file, config=str(assets),
        subfolder='transformer', torch_dtype=dtype, local_files_only=True)
    # The Klein text encoder does not use its language-model head. Tie it to the
    # embeddings rather than inventing missing head weights from encoder-only files.
    import shutil
    te_root = cache / ('klein-encoder-' + profile.get('image_variant', '4b'))
    te_root.mkdir(parents=True, exist_ok=True)
    config = json.loads((assets / 'text_encoder/config.json').read_text())
    config['tie_word_embeddings'] = True
    (te_root / 'config.json').write_text(json.dumps(config))
    link = te_root / 'model.safetensors'
    if link.is_symlink() or link.exists(): link.unlink()
    try: link.symlink_to(Path(encoder_file).resolve())
    except OSError: shutil.copyfile(encoder_file, link)
    encoder = AutoModelForCausalLM.from_pretrained(str(te_root), torch_dtype=dtype, local_files_only=True)
    for index, layer in enumerate(encoder.model.layers):
        layer.register_forward_pre_hook(lambda module, inputs, number=index + 1, total=len(encoder.model.layers):
            progress(f'Image prompt encoder · layer {number}/{total}'))
    tokenizer = AutoTokenizer.from_pretrained(str(assets / 'tokenizer'), local_files_only=True)
    vae = AutoencoderKLFlux2.from_config(str(assets / 'vae/config.json'))
    weights = load_file(profile['image_vae'], device='cpu')
    if any(key.startswith('encoder.down.') for key in weights):
        from diffusers.loaders.single_file_utils import convert_ldm_vae_checkpoint
        bn = {k: v for k, v in weights.items() if k.startswith('bn.')}
        weights = {**convert_ldm_vae_checkpoint(weights, vae.config), **bn}
    vae.load_state_dict(weights, strict=True); del weights
    vae.to(dtype=dtype)
    pipe = Flux2KleinPipeline.from_pretrained(str(assets), transformer=transformer,
        text_encoder=encoder, tokenizer=tokenizer, vae=vae, torch_dtype=dtype, local_files_only=True,
        is_distilled='base' not in Path(profile['image_model']).name.lower())
    device = profile.get('device', 'cuda')
    if device == 'cuda' and profile.get('offload', True):
        free = torch.cuda.mem_get_info()[0] / 2**30
        if profile.get('image_variant', '4b') == '4b' and free >= 9:
            pipe.enable_model_cpu_offload()
        else: pipe.enable_sequential_cpu_offload()
    else: pipe.to(device)
    pipe.vae.enable_tiling()
    width = int(request.get('width', 1024)); height = int(request.get('height', 1024))
    steps = int(request.get('steps', 30)); every = max(1, int(profile.get('preview_every', 5)))
    root = Path(request['job_dir']); started = time.monotonic()
    def callback(pipeline, step, timestep, kwargs):
        done = step + 1
        eta = (time.monotonic() - started) / done * max(0, steps - done)
        progress('Rendering image', done / steps * 95, eta)
        if done % every == 0 or done == steps:
            # Decode actual current latents. The preview is explicitly a draft.
            latents = kwargs['latents'].detach()
            ph = height // (pipeline.vae_scale_factor * 2); pw = width // (pipeline.vae_scale_factor * 2)
            latents = latents.transpose(1, 2).reshape(1, -1, ph, pw)
            mean = pipeline.vae.bn.running_mean.view(1, -1, 1, 1).to(latents)
            std = (pipeline.vae.bn.running_var.view(1, -1, 1, 1) + pipeline.vae.config.batch_norm_eps).sqrt().to(latents)
            latents = pipeline._unpatchify_latents(latents * std + mean)
            preview = pipeline.vae.decode(latents, return_dict=False)[0]
            image = pipeline.image_processor.postprocess(preview, output_type='pil')[0]
            path = root / f'preview-{done}.png'; image.save(path)
            emit('preview', path=str(path), media='image', draft=True)
            del preview, latents, image
        return kwargs
    images = []
    if request.get('source'): images.append(Image.open(request['source']).convert('RGB'))
    for ref in request.get('references', []):
        image = _pil_reference(ref)
        if image is not None: images.append(image)
    arguments = dict(prompt=request['prompt'], width=width, height=height, num_inference_steps=steps,
        guidance_scale=4.0 if 'base' in Path(profile['image_model']).name.lower() else 1.0,
        generator=torch.Generator('cpu').manual_seed(int(request.get('seed', 42))),
        callback_on_step_end=callback, callback_on_step_end_tensor_inputs=['latents'])
    if images: arguments['image'] = images
    encode = pipe.encode_prompt
    def encode_prompt(*args, **kwargs):
        progress('Encoding image prompt and references…')
        result = encode(*args, **kwargs)
        progress('Prompt encoded; starting image sampling…')
        return result
    pipe.encode_prompt = encode_prompt
    output = pipe(**arguments).images[0]
    path = root / 'image.png'; output.save(path)
    emit('result', path=str(path), media='image')


def read_reference_video(path, *, start=0, duration=15, width=832, height=480, fps=24):
    """Decode a bounded, trimmed silent reference without reading the full movie."""
    import av
    from PIL import ImageOps
    start = max(0.0, float(start)); duration = min(15.0, max(0.0, float(duration)))
    count = max(1, math.ceil(duration * fps)); output = []
    with av.open(str(path)) as container:
        if not container.streams.video: raise ValueError('Reference has no video stream.')
        stream = container.streams.video[0]
        if start: container.seek(int(start / stream.time_base), stream=stream, backward=True)
        target = start
        for frame in container.decode(stream):
            stamp = float(frame.pts * frame.time_base) if frame.pts is not None else target
            if stamp + 1e-6 < target: continue
            image = None
            while target <= stamp + 1e-6 and len(output) < count:
                if image is None: image = ImageOps.fit(frame.to_image().convert('RGB'), (width, height))
                output.append(image.copy()); target = start + len(output) / fps
            if len(output) >= count: break
    return output


def read_reference_audio(path, *, start=0, duration=15, rate=32000):
    """Decode a bounded reference with the bundled PyAV/FFmpeg runtime."""
    import av
    import numpy as np
    start = max(0.0, float(start)); count = math.ceil(min(15, max(0, float(duration))) * rate)
    chunks = []; copied = 0; cursor = start
    with av.open(str(path)) as container:
        if not container.streams.audio: raise ValueError('Reference has no audio stream.')
        stream = container.streams.audio[0]
        if start: container.seek(int(start / stream.time_base), stream=stream, backward=True)
        resampler = av.AudioResampler(format='fltp', layout='stereo', rate=rate)
        def append(frame):
            nonlocal copied, cursor
            stamp = float(frame.pts * frame.time_base) if frame.pts is not None else cursor
            cursor = stamp + frame.samples / rate
            offset = max(0, round((start - stamp) * rate))
            data = frame.to_ndarray()[:, offset:offset + max(0, count - copied)]
            if data.shape[1]: chunks.append(data.copy()); copied += data.shape[1]
        for frame in container.decode(stream):
            for output in resampler.resample(frame): append(output)
            if copied >= count: break
        if copied < count:
            for output in resampler.resample(None): append(output)
    if not chunks: raise ValueError('The selected audio reference has no samples in this interval.')
    return np.concatenate(chunks, axis=1), rate


def video_generation(request):
    refs = request.get('references', [])
    image_count = sum(r.get('kind', 'image') == 'image' for r in refs)
    video_count = sum(r.get('kind') == 'video' for r in refs)
    audio_count = sum(r.get('kind') == 'audio' or r.get('kind') == 'video' and bool(r.get('audio_path')) for r in refs)
    if image_count > 9 or video_count > 3 or audio_count > 3 or image_count + video_count + audio_count > 12:
        raise ValueError('H3 accepts up to 9 images, 3 video and 3 audio references, 12 files in total.')
    if request['profile'].get('video_variant', 'ref2va' if 'ref2va' in Path(request['profile']['video_model']).name.lower() else 'fl2va') != 'ref2va':
        if video_count or audio_count or image_count > 2:
            raise ValueError('FL2VA uses at most two image keyframes; use Ref2VA for mixed references.')
        roles = [-1 if r.get('role') == 'end' else 0 for r in refs]
        if len(roles) != len(set(roles)):
            raise ValueError('FL2VA keyframes need one Start and one End role.')
    elif not refs:
        raise ValueError('Ref2VA requires reference media. Select references or use a pruned FL2VA checkpoint.')
    progress('Starting native video runtime…')
    import torch
    from PIL import Image
    from diffsynth.pipelines.minimax_h3_audio_video import MiniMaxH3Pipeline, ModelConfig
    from diffsynth.utils.data.audio_video import write_video_audio
    from .local_generation import validate_profile, align_h3_frames, resolve_video_seed
    profile = request['profile']; validate_profile(profile, 'video')
    dtype = torch.bfloat16
    device = profile.get('device', 'cuda')
    # Native disk offload reads safetensors as blocks are needed rather than
    # holding both the 20GB DiT and its text encoder in host RAM.
    config = dict(offload_device='disk', offload_dtype='disk', onload_device='disk',
        onload_dtype='disk', preparing_device=device, preparing_dtype=dtype,
        computation_device=device, computation_dtype=dtype) if profile.get('offload', True) else {}
    from .local_h3_quant import register_encoder, register_packaged_vaes, use_cublas_int8
    use_cublas_int8()
    register_packaged_vaes()
    if register_encoder(profile['video_encoder']):
        progress('Using native NVFP4/AWQ encoder with per-layer BF16 computation…')
    progress('Loading pruned H3 and its encoders…')
    count = align_h3_frames(request.get('duration', 5))
    # VAE downsamples by 16, then DiT packs 2×2 spatial latent patches.
    tokens = (((count - 5) // 17) * 5 + 2) * (request['height'] // 32) * (request['width'] // 32)
    # Packed attention/QKV activations must fit alongside the cached weights.
    reserve = max(3, (tokens + len(refs) * 4096 + 2048) * 21504 * 2 * 2.5 / 2**30)
    limit = max(1, torch.cuda.mem_get_info()[0] / 2**30 - reserve) if device == 'cuda' else None
    if device == 'cuda':
        free, total = torch.cuda.mem_get_info()
        progress(f'GPU memory: {free / 2**30:.2f} GiB free / {total / 2**30:.2f} GiB total; '
                 f'activation allowance {reserve:.2f} GiB; weight cache threshold {limit:.2f} GiB')
    pipe = MiniMaxH3Pipeline.from_pretrained(torch_dtype=dtype, device=device,
        model_configs=[ModelConfig(path=profile[k], **config)
                       for k in ('video_model', 'video_encoder', 'video_vae', 'audio_vae')],
        processor_config=ModelConfig(path=profile['video_assets']), vram_limit=limit)
    from .local_h3_quant import optimize_h3_convolutions, stream_h3_feed_forward, stream_h3_video_pixels
    optimize_h3_convolutions(pipe); stream_h3_feed_forward(pipe); stream_h3_video_pixels(pipe)
    if profile.get('video_sampler', 'res_multistep') == 'res_multistep':
        from .local_h3_sampling import H3ResMultistepScheduler
        pipe.scheduler = H3ResMultistepScheduler()
        pipe.scheduler_audio = H3ResMultistepScheduler(audio=True)
        progress('Sampler: RES multistep · simple schedule · guidance 1 · no Lightning LoRA')
    encoder = pipe.text_encoder.model
    for label, layers in (('vision', encoder.visual.blocks), ('prompt', encoder.language_model.layers)):
        for index, layer in enumerate(layers):
            layer.register_forward_pre_hook(lambda module, inputs, name=label, number=index + 1, total=len(layers):
                progress(f'H3 {name} encoder · layer {number}/{total}'))
    current_step = [0]
    def denoiser_progress(number, total):
        emit('log', message=f'H3 denoiser · block {number}/{total}')
        steps = int(request.get('steps', profile.get('video_steps', 20)))
        fraction = current_step[0] + (number - 1) / total
        eta = (time.monotonic() - started[0]) / fraction * max(0, steps - fraction) if fraction >= 1 and started[0] else None
        progress(f'Rendering video and audio · step {current_step[0] + 1}/{steps} · block {number}/{total}',
                 fraction / steps * 94, eta)
    for index, block in enumerate(pipe.dit.blocks):
        block.register_forward_pre_hook(lambda module, inputs, number=index + 1, total=len(pipe.dit.blocks):
            denoiser_progress(number, total))
    sampling_finished = [False]
    def trace_decode(method, label, percent):
        def decode(*args, **kwargs):
            if sampling_finished[0]: progress(label, percent)
            else: emit('log', message=label)
            return method(*args, **kwargs)
        return decode
    pipe.video_vae.decode_video = trace_decode(pipe.video_vae.decode_video, 'Decoding video latents…', 95)
    pipe.audio_vae.decode_audio = trace_decode(pipe.audio_vae.decode_audio, 'Decoding audio latents…', 96)
    run_unit = pipe.unit_runner
    def unit_runner(unit, *args, **kwargs):
        progress('H3 stage: ' + type(unit).__name__.replace('MiniMaxH3Unit_', ''))
        return run_unit(unit, *args, **kwargs)
    pipe.unit_runner = unit_runner
    references = []
    for ref in request.get('references', []):
        if ref.get('kind') == 'video':
            frames = read_reference_video(ref['path'], height=request['height'], width=request['width'],
                start=ref.get('start', 0), duration=ref.get('duration', 15), fps=24)
            if len(frames) < 48: raise ValueError('H3 video references need at least two seconds of source footage.')
            reference = {'type': 'video', 'video': frames}
            if ref.get('audio_path'):
                audio, rate = read_reference_audio(ref['audio_path'], start=ref.get('start', 0), duration=ref.get('duration', 15))
                if audio.shape[-1] / rate < 2: raise ValueError('H3 audio references need at least two seconds of source audio.')
                reference.update(type='video_audio', audio=torch.from_numpy(audio), sample_rate=rate)
            references.append(reference)
        elif ref.get('kind') == 'audio':
            audio, rate = read_reference_audio(ref['path'], start=ref.get('start', 0), duration=ref.get('duration', 15))
            if audio.shape[-1] / rate < 2: raise ValueError('H3 audio references need at least two seconds of source audio.')
            references.append({'type': 'audio', 'audio': torch.from_numpy(audio), 'sample_rate': rate})
        else:
            image = _pil_reference(ref)
            if image is not None: references.append({'type': 'image', 'image': image})
    steps = int(request.get('steps', profile.get('video_steps', 20))); every = max(1, int(profile.get('preview_every', 5)))
    diagnostic_steps = max(0, int(request.get('diagnostic_steps', 0)))
    completed_steps = [0]
    root = Path(request['job_dir']); started = [None]
    last_video = [None]; original_step = pipe.step
    def step(scheduler, latents, progress_id, **kwargs):
        output = original_step(scheduler, latents, progress_id, **kwargs)
        if scheduler is pipe.scheduler:
            last_video[0] = output.detach()
        return output
    pipe.step = step
    def iterate(values):
        total = len(values); started[0] = time.monotonic()
        for index, value in enumerate(values):
            current_step[0] = index
            progress(f'Rendering video and audio · step {index + 1}/{total}', index / total * 94)
            yield value
            done = index + 1
            completed_steps[0] = done
            eta = (time.monotonic() - started[0]) / done * max(0, total - done)
            progress('Rendering video and audio', done / total * 94, eta)
            diagnostic_end = diagnostic_steps and done >= diagnostic_steps
            if last_video[0] is not None and (done % every == 0 or done == total or diagnostic_end):
                pipe.load_models_to_device(['video_vae'])
                count = min(last_video[0].shape[2], pipe.video_vae.tokens_chunk_size)
                decoded = pipe.video_vae.decode_video(last_video[0][:, :, :count], dtype=dtype,
                                                       tiled=True, tile_size=256, tile_overlap=64)
                frames = pipe.vae_output_to_video(decoded, min_value=0, max_value=1)
                from diffsynth.utils.data import save_video
                path = root / f'preview-{done}.mp4'
                save_video(frames, str(path), fps=24, quality=5)
                emit('preview', path=str(path), media='video', draft=True)
                del decoded, frames
                pipe.load_models_to_device(pipe.in_iteration_models)
            if diagnostic_end:
                progress(f'Diagnostic check: decoding after {done}/{total} sampling steps. Output is unfinished.')
                break
        sampling_finished[0] = True
    count = align_h3_frames(request.get('duration', 5))
    seed = resolve_video_seed(request.get('seed', profile.get('video_seed', -1)))
    progress(f'MiniMax seed: {seed}')
    args = dict(prompt=request['prompt'], height=int(request['height']), width=int(request['width']),
        num_frames=count, num_inference_steps=steps, seed=seed, progress_bar_cmd=iterate,
        ref_image_short_edge=min(768, int(request['height']), int(request['width'])),
        ref_video_short_edge=min(480, int(request['height']), int(request['width'])),
        ref_video_max_pixels=int(request['height']) * int(request['width']))
    if profile.get('video_variant', 'ref2va' if 'ref2va' in Path(profile['video_model']).name.lower() else 'fl2va') == 'ref2va':
        if not references: raise ValueError('The selected Ref2VA checkpoint requires reference media. Add references or select a pruned FL2VA checkpoint.')
        args['references'] = references
    else:
        keyframes = [r['image'] for r in references if r['type'] == 'image']
        indices = [-1 if r.get('role') == 'end' else 0 for r in request.get('references', [])]
        if keyframes: args.update(keyframes=keyframes, keyframe_indices=indices)
    video, audio = pipe(**args)
    progress('Encoding finished video…', 97)
    path = root / 'video.mp4'
    write_video_audio(video=video, audio=audio, output_path=str(path), fps=24, audio_sample_rate=32000)
    emit('result', path=str(path), media='video', duration=count / 24, seed=seed,
         completed_steps=completed_steps[0], sampling_steps=steps, diagnostic=bool(diagnostic_steps))


def text_generation(request):
    progress('Starting native writing runtime…')
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, TextIteratorStreamer
    import threading
    profile = request['profile']
    model_dir = profile['llm_model']
    if not Path(model_dir).is_dir() or not (Path(model_dir) / 'config.json').is_file():
        raise ValueError('Choose a complete instruction-model folder containing config, tokenizer and safetensors weights. Encoder-only image checkpoints cannot refine prompts.')
    progress('Loading local writing model…')
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(model_dir, device_map='cpu' if profile.get('device') == 'cpu' else 'auto',
        torch_dtype='auto', local_files_only=True, trust_remote_code=False)
    # Text-only models receive explicit authored context; never claim they saw media.
    contexts = [{key: item.get(key) for key in ('name', 'prompt', 'duration', 'role', 'kind') if key in item}
                for item in request.get('images', [])]
    prompt = request['prompt'] + '\nReturn only valid JSON matching this schema:\n' + json.dumps(request['schema'])
    prompt += '\nAuthored timeline context (media is not supplied to this text-only model):\n' + json.dumps(contexts)
    messages = [{'role': 'user', 'content': prompt}]
    inputs = tokenizer.apply_chat_template(messages, add_generation_prompt=True, return_tensors='pt',
                                           return_dict=True, enable_thinking=False).to(model.device)
    streamer = TextIteratorStreamer(tokenizer, skip_prompt=True, skip_special_tokens=True, timeout=120)
    failure = []
    def generate():
        try:
            with torch.inference_mode():
                model.generate(**inputs, streamer=streamer, max_new_tokens=min(8192, int(request.get('max_new_tokens', 8192))), do_sample=False,
                               pad_token_id=tokenizer.eos_token_id)
        except BaseException as error:
            failure.append(error); streamer.end()
    thread = threading.Thread(target=generate); thread.start()
    text = ''
    for chunk in streamer:
        text += chunk; emit('text', text=chunk)
    thread.join()
    if failure: raise failure[0]
    text = text.strip()
    if text.startswith('```'): text = text.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    json.loads(text)  # The caller also validates its exact operation-specific schema.
    emit('result', text=text)


def main():
    request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    # Bound CPU threads so loading and format conversion do not starve the editor.
    os.environ.setdefault('OMP_NUM_THREADS', '4')
    os.environ.setdefault('MKL_NUM_THREADS', '4')
    with contextlib.redirect_stdout(sys.stderr):
        try:
            kind = request['kind']
            if kind in ('install', 'download', 'assets', 'probe'):
                from .local_setup import setup_request
                setup_request(request); return
            from .local_generation import gpu_memory, data_root
            from filelock import FileLock
            progress('Waiting for the local engine…')
            with FileLock(str(data_root() / 'inference.lock')):
                if request['profile'].get('device', 'cuda') == 'cuda':
                    memory = gpu_memory()
                    if memory['total_gb'] and memory['free_gb'] < .5:
                        raise RuntimeError('The GPU is busy with another application. Free GPU memory before starting local generation; Director has not stopped any other process.')
                try:
                    {'image': image_generation, 'video': video_generation, 'text': text_generation}[kind](request)
                finally:
                    import gc
                    gc.collect()
                    torch = sys.modules.get('torch')
                    if torch is not None and torch.cuda.is_available(): torch.cuda.empty_cache()
        except Exception as error:
            import traceback
            traceback.print_exc(file=sys.stderr)
            message = str(error)[:1400]
            if request.get('hf_token'): message = message.replace(request['hf_token'], '[redacted]')
            emit('error', message=f'{type(error).__name__}: {message}')
            raise SystemExit(1)


if __name__ == '__main__': main()
