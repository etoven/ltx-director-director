"""Explicit engine/model preparation, called only by setup buttons in a worker."""
from pathlib import Path
import json
import os
import subprocess
import sys
from .local_protocol import emit, progress
from .local_generation import data_root, engine_python, DIFFSYNTH_REVISION

IMAGE_REPOS = {v: f'black-forest-labs/FLUX.2-klein-base-{v[0]}B' for v in ('4b', '9b')}


def pytorch_wheel_variant(device):
    if device == 'cpu': return 'cpu'
    try:
        info = subprocess.run(['nvidia-smi', '--query-gpu=driver_version,compute_cap', '--format=csv,noheader'],
                              capture_output=True, text=True, check=True, timeout=8)
        driver, capability = info.stdout.splitlines()[0].split(',')
        # CUDA 13 requires R580+ and Turing or newer. Retain CUDA 12 fallback.
        if int(driver.strip().split('.')[0]) >= 580 and float(capability.strip()) >= 7.5:
            return 'cu130'
    except (OSError, ValueError, IndexError, subprocess.SubprocessError): pass
    return 'cu128'


def install_pytorch(python, root, variant):
    target = '2.9.1+' + variant
    metadata = subprocess.run([python, '-c',
        'import importlib.metadata as m,json; '
        'print(json.dumps({d.metadata["Name"]:d.version for d in m.distributions()}))'],
        check=True, capture_output=True, text=True)
    packages = json.loads(metadata.stdout)
    if packages.get('torch') and packages['torch'] != target:
        # cu12/cu13 distributions can share library paths. Remove the old
        # managed runtime before installing the replacement, including upgrades
        # whose public torch version is unchanged but CUDA suffix differs.
        previous = [name for name in packages if name.lower().startswith('nvidia-')
                    or name.lower() in ('torch', 'torchvision', 'torchaudio')]
        progress('Replacing the previous PyTorch runtime in the private venv…')
        subprocess.run([python, '-m', 'pip', 'uninstall', '-y', *previous], check=True, stdout=sys.stderr)
    subprocess.run([python, '-m', 'pip', '--isolated', '--cache-dir', str(root / '.cache/pip'),
        'install', 'torch==' + target, 'torchvision==0.24.1+' + variant,
        'torchaudio==2.9.1+' + variant, '--index-url', 'https://download.pytorch.org/whl/' + variant],
        check=True, stdout=sys.stderr)


def setup_request(request):
    kind = request['kind']
    if kind == 'probe':
        check_engine(request['profile'].get('device', 'cuda'))
        emit('result', profile_changes={}, message='Engine imports and dependencies verified. Model rendering can now be tested.')
        return
    if kind == 'install':
        root = data_root() / 'engine'
        progress('Creating a private local inference environment…')
        subprocess.run([sys.executable, '-m', 'venv', str(root)], check=True, stdout=sys.stderr)
        python = engine_python()
        progress('Installing CUDA PyTorch; this can download several GB…')
        variant = pytorch_wheel_variant(request['profile'].get('device', 'cuda'))
        progress('Using PyTorch runtime ' + variant + ' in the private engine venv…')
        install_pytorch(python, root, variant)
        progress('Installing the native video and image engines…')
        subprocess.run([python, '-m', 'pip', '--isolated', '--cache-dir', str(root / '.cache/pip'), 'install',
            'diffsynth @ https://github.com/modelscope/DiffSynth-Studio/archive/' + DIFFSYNTH_REVISION + '.zip',
            'diffusers==0.41.0', 'transformers==5.20.2', 'accelerate', 'safetensors',
            'huggingface_hub', 'sentencepiece', 'filelock', 'Pillow', 'comfy-kitchen==0.2.39',
            'imageio-ffmpeg', 'av', 'setuptools'], check=True, stdout=sys.stderr)
        subprocess.run([python, '-m', 'pip', 'check'], check=True, stdout=sys.stderr)
        subprocess.run([python, '-c', 'from ltx_prompt_director.local_setup import check_engine; check_engine(' + repr(request['profile'].get('device', 'cuda')) + ')'], check=True, stdout=sys.stderr)
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
                'video_encoder': 'text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors',
                'video_vae': 'vae/minimax_h3_video_vae_fp16.safetensors',
                'audio_vae': 'vae/minimax_h3_audio_vae_fp32.safetensors',
            }
            for key, name in names.items():
                previous = profile.get(key, '')
                if previous and Path(previous).is_file():
                    if Path(previous).name == Path(name).name: continue
                    if key == 'video_encoder' and Path(previous).name in (
                        'qwen3vl_32b_minimax_h3_int8_convrot.safetensors', 'qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors'): continue
                changes[key] = download('Comfy-Org/MiniMax-H3', name, destination / 'MiniMax-H3')
    emit('result', profile_changes=changes, message='Selected model files are ready.')


def check_engine(device='cuda'):
    """Exercise imports in the inference interpreter, never in the GUI process."""
    import importlib
    progress('Verifying dependencies in ' + sys.executable)
    subprocess.run([sys.executable, '-m', 'pip', 'check'], check=True, stdout=sys.stderr)
    for name in ('torch', 'torchvision', 'torchaudio', 'safetensors', 'accelerate', 'comfy_kitchen',
                 'transformers', 'diffusers', 'av', 'imageio_ffmpeg', 'filelock', 'PIL'):
        progress('Importing ' + name + '…')
        module = importlib.import_module(name)
        print(name + ' ' + str(getattr(module, '__version__', 'available')), file=sys.stderr, flush=True)
    from diffsynth.pipelines.minimax_h3_audio_video import MiniMaxH3Pipeline
    from diffusers import Flux2KleinPipeline
    import torch
    if device == 'cuda':
        if not torch.cuda.is_available(): raise RuntimeError('PyTorch cannot use CUDA in this interpreter.')
        result = (torch.ones(4, device='cuda') * 2).sum().item()
        if result != 8: raise RuntimeError('CUDA tensor check failed.')
        print('CUDA check passed: ' + torch.cuda.get_device_name(), file=sys.stderr, flush=True)
    progress('Engine import checks passed.')
