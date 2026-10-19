"""Explicit engine/model preparation, called only by setup buttons in a worker."""
from pathlib import Path
import json
import os
import subprocess
import sys
from .local_protocol import emit, progress
from .local_generation import data_root, engine_python, DIFFSYNTH_REVISION

IMAGE_REPOS = {v: f'black-forest-labs/FLUX.2-klein-base-{v[0]}B' for v in ('4b', '9b')}


def setup_request(request):
    kind = request['kind']
    if kind == 'probe':
        from importlib.util import find_spec
        packages = ('torch', 'diffusers', 'diffsynth', 'transformers', 'safetensors', 'accelerate', 'filelock')
        missing = [name for name in packages if find_spec(name) is None]
        emit('result', profile_changes={}, message=('Missing engine packages: ' + ', '.join(missing)) if missing
             else 'Engine packages are available. Check model files and configurations before rendering.')
        return
    if kind == 'install':
        root = data_root() / 'engine'
        progress('Creating a private local inference environment…')
        subprocess.run([sys.executable, '-m', 'venv', str(root)], check=True, stdout=sys.stderr)
        python = engine_python()
        progress('Installing CUDA PyTorch; this can download several GB…')
        wheel_index = 'https://download.pytorch.org/whl/' + ('cpu' if request['profile'].get('device') == 'cpu' else 'cu128')
        subprocess.run([python, '-m', 'pip', 'install', 'torch==2.9.1', 'torchvision==0.24.1',
                        'torchaudio==2.9.1', '--index-url', wheel_index],
                       check=True, stdout=sys.stderr)
        progress('Installing the native video and image engines…')
        subprocess.run([python, '-m', 'pip', 'install',
            'diffsynth @ git+https://github.com/modelscope/DiffSynth-Studio.git@' + DIFFSYNTH_REVISION,
            'diffusers>=0.41.0,<1', 'transformers>=5,<6', 'accelerate', 'safetensors',
            'huggingface_hub', 'sentencepiece', 'filelock', 'Pillow', 'comfy-kitchen',
            'imageio-ffmpeg', 'av'], check=True, stdout=sys.stderr)
        emit('result', profile_changes={'python': python}, message='Local engine ready.'); return
    from huggingface_hub import hf_hub_download, HfApi
    token = request.get('hf_token') or None
    api = HfApi(token=token)
    profile = request['profile']; changes = {}
    destination = data_root() / 'models'
    destination.mkdir(parents=True, exist_ok=True)
    task = request.get('task', 'image')
    variant = profile.get('image_variant', '4b')
    def download(repo, filename, local):
        progress('Downloading ' + filename + '…')
        # Hub verifies its content-addressed cache; interrupted downloads can resume.
        return hf_hub_download(repo_id=repo, filename=filename, local_dir=str(local), token=token)
    def assets(repo, local, prefix=''):
        files = api.list_repo_files(repo)
        chosen = [name for name in files if name.startswith(prefix)
                  and name.lower().endswith(('.json', '.txt', '.model', '.tiktoken', '.jinja'))
                  and not any(part in name.split('/') for part in ('onnx', 'openvino'))]
        if not chosen: raise ValueError('The model repository has no supported configuration files.')
        for index, name in enumerate(chosen):
            progress('Preparing model configuration ' + name, (index / len(chosen)) * 100)
            download(repo, name, local)
        return str(local / prefix) if prefix else str(local)
    if task == 'text':
        repo = profile.get('writer_repo') or 'Qwen/Qwen3-4B-Instruct-2507'
        local = destination / repo.replace('/', '--')
        if kind == 'assets': raise ValueError('A writing model needs its complete instruction-model folder.')
        files = api.list_repo_files(repo)
        selected = [name for name in files if '/' not in name and name.endswith(('.json', '.safetensors', '.model', '.txt', '.jinja'))]
        for index, name in enumerate(selected):
            progress('Downloading writing model ' + name, index / max(1, len(selected)) * 100)
            download(repo, name, local)
        changes['llm_model'] = str(local)
    elif task == 'image':
        asset_dir = destination / ('klein-' + variant + '-assets')
        changes['image_assets'] = assets(IMAGE_REPOS[variant], asset_dir)
        if kind == 'download':
            paths = {
                'image_model': (f'black-forest-labs/FLUX.2-klein-base-{variant}-fp8', f'flux-2-klein-base-{variant}-fp8.safetensors'),
                'image_encoder': (f'Comfy-Org/flux2-klein-{variant[0]}B',
                                  'split_files/text_encoders/' + ('qwen_3_4b.safetensors' if variant == '4b' else 'qwen_3_8b_fp8mixed.safetensors')),
                'image_vae': ('Comfy-Org/flux2-klein-4B', 'split_files/vae/flux2-vae.safetensors'),
            }
            for key, (repo, name) in paths.items():
                previous = profile.get(key, '')
                if previous and Path(previous).is_file() and Path(previous).name == Path(name).name: continue
                changes[key] = download(repo, name, destination / repo.replace('/', '--'))
    else:
        asset_dir = destination / 'h3-assets'
        changes['video_assets'] = assets('MiniMaxAI/MiniMax-H3', asset_dir, 'Ref2VA/processor/')
        if kind == 'download':
            mode = request.get('video_variant', 'ref2va')
            names = {
                'video_model': f'diffusion_models/minimax_h3_{mode}_pruned_int8_convrot.safetensors',
                'video_encoder': 'text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors',
                'video_vae': 'vae/minimax_h3_video_vae_fp16.safetensors',
                'audio_vae': 'vae/minimax_h3_audio_vae_fp32.safetensors',
            }
            for key, name in names.items():
                previous = profile.get(key, '')
                if previous and Path(previous).is_file() and Path(previous).name == Path(name).name: continue
                changes[key] = download('Comfy-Org/MiniMax-H3', name, destination / 'MiniMax-H3')
    emit('result', profile_changes=changes, message='Selected model files are ready.')
