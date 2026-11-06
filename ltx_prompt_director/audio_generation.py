"""Audio model configuration, private setup, refinement and worker requests."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .local_generation import data_root, run_request, LOCAL_PROGRESS, LOCAL_CANCEL, resolve_video_seed

NAMES = {'dialog': 'Dialogue · IndexTTS 2.5', 'foley': 'Foley · Stable Audio 3', 'music': 'Music · MiniMax Music 3 (H3)'}
REPOSITORIES = {'dialog': 'https://github.com/index-tts/index-tts.git',
                'foley': 'https://github.com/Stability-AI/stable-audio-3.git',
                'music': 'https://github.com/Comfy-Org/ComfyUI.git'}
KEY = 'audio_generation/models_v1'


def venv_python(root):
    return str(Path(root) / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python'))


def defaults():
    root = data_root() / 'audio'
    values = {kind: dict(source=str(root / kind), python=venv_python(root / kind), device='cuda',
                       model=str(root / kind / 'checkpoints') if kind == 'dialog' else 'small-sfx' if kind == 'foley' else '',
                       encoder='', vae='') for kind in NAMES}
    values['music'].update(model=str(root / 'music/models/diffusion_models/minimax_music3_dit_int8_convrot.safetensors'),
                           encoder=str(root / 'music/models/text_encoders/minimax_music3_text_encoder_pruned_int8_convrot.safetensors'),
                           vae=str(root / 'music/models/vae/minimax_music3_dav.safetensors'))
    return values


def load_audio_settings(settings):
    try:
        saved = json.loads(str(settings.value(KEY, '{}')))
    except (ValueError, TypeError):
        saved = {}
    return {kind: {**value, **(saved.get(kind, {}) if isinstance(saved, dict) else {})}
            for kind, value in defaults().items()}


def validate_audio(config, kind):
    for key in ('python', 'source'):
        if not config.get(key) or not Path(config[key]).expanduser().exists():
            raise ValueError('Set up ' + NAMES[kind] + ' in Settings → Audio models first.')
    fields = ('model', 'encoder', 'vae') if kind == 'music' else ('model',) if kind == 'dialog' else ()
    for key in fields:
        if not config.get(key) or not Path(config[key]).expanduser().exists():
            raise ValueError('Choose the ' + key + ' for ' + NAMES[kind] + ' in Audio models.')


def setup_audio(kind, config, download=False):
    """Explicit setup buttons only. Each incompatible engine has its own venv."""
    callback = LOCAL_PROGRESS.get(); cancel = LOCAL_CANCEL.get()
    def run(args, **kwargs):
        if cancel is not None and cancel.is_set():
            raise InterruptedError('Audio setup cancelled.')
        result = subprocess.run(args, capture_output=True, text=True, **kwargs)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout)[-2000:])
    def status(message):
        if callback:
            callback({'kind': 'progress', 'message': message})
    config = copy.deepcopy(config); root = Path(config['source']).expanduser().absolute()
    root.parent.mkdir(parents=True, exist_ok=True)
    if not download:
        if not root.exists():
            status('Downloading the audio engine source…'); run(['git', 'clone', '--depth', '1', REPOSITORIES[kind], str(root)])
        elif not (root / 'pyproject.toml').exists() and not (root / 'requirements.txt').exists():
            raise ValueError('Choose a valid engine source folder, or a new folder for setup.')
        bootstrap = data_root() / 'audio' / 'setup-tools'
        python = bootstrap / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        if not python.is_file():
            run([sys.executable, '-m', 'venv', str(bootstrap)])
            run([str(python), '-m', 'pip', 'install', 'uv'])
        uv = str(python.parent / ('uv.exe' if os.name == 'nt' else 'uv'))
        status('Installing a private Python 3.11 audio runtime…')
        if kind == 'music':
            run([uv, 'venv', '--python', '3.11', str(root / '.venv')])
            run([uv, 'pip', 'install', '--python', venv_python(root), '-r', str(root / 'requirements.txt'), 'soundfile', 'filelock'])
        else:
            run([uv, 'sync', '--directory', str(root), '--python', '3.11'])
            run([uv, 'pip', 'install', '--python', venv_python(root), 'filelock', 'soundfile'])
        config.update(source=str(root), python=venv_python(root))
        status('Audio engine ready. Download its model files next.')
        return config
    if not Path(config['python']).is_file():
        raise ValueError('Prepare the audio engine before downloading its models.')
    status('Downloading audio model files…')
    if kind == 'music':
        names = dict(model='diffusion_models/minimax_music3_dit_int8_convrot.safetensors',
                     encoder='text_encoders/minimax_music3_text_encoder_pruned_int8_convrot.safetensors',
                     vae='vae/minimax_music3_dav.safetensors')
        code = ('from huggingface_hub import hf_hub_download; '
                + '; '.join('hf_hub_download(repo_id="Comfy-Org/MiniMax-Music-3", filename=' + repr(name)
                            + ', local_dir=' + repr(str(root / 'models')) + ')' for name in names.values()))
        run([config['python'], '-c', code])
        config.update({key: str(root / 'models' / name) for key, name in names.items()})
    else:
        repo = 'IndexTeam/IndexTTS-2.5' if kind == 'dialog' else 'stabilityai/stable-audio-3-small-sfx'
        target = Path(config['model']) if kind == 'dialog' else None
        code = ('from huggingface_hub import snapshot_download; snapshot_download(repo_id=' + repr(repo)
                + (', local_dir=' + repr(str(target)) if target else '') + ')')
        env = dict(os.environ)
        if kind == 'foley':
            env['HF_HOME'] = str(root / '.cache/huggingface')
        run([config['python'], '-c', code], env=env)
    return config


def refine_audio_prompt(kind, prompt, lyrics, credentials, timeout):
    from .ai import _structured_raw
    from . import response_schemas as schemas
    provider, model, key = credentials
    if kind == 'music':
        from .music_caption import rewrite
        return rewrite(prompt, lyrics, credentials, timeout)
    elif kind == 'dialog':
        rules = ('Prepare the following spoken script for IndexTTS 2.5. Preserve every spoken word, meaning and language. '
                 'Improve punctuation and pronunciation clarity only. Return only the spoken script, without directions or headings.\n' + prompt)
    else:
        rules = ('Rewrite this Foley request as a concrete Stable Audio 3 sound-effects description. Preserve every requested '
                 'sound, timing, texture, environment and exclusion. Do not add music or speech unless explicitly requested. '
                 'Return only the audio description.\n' + prompt)
    raw = _structured_raw([], provider, model, key, rules, timeout, schema=schemas.PROMPT)
    return json.loads(raw)['prompt']


def generate_audio(request, credentials=None):
    request = copy.deepcopy(request)
    config = request['audio_config']; validate_audio(config, request['audio_kind'])
    if request.pop('refine', False):
        if not credentials:
            raise ValueError('Choose a writing model before enabling AI refinement.')
        callback = LOCAL_PROGRESS.get()
        if callback:
            callback({'kind': 'progress', 'message': 'Refining the audio prompt…'})
        request['prompt'] = refine_audio_prompt(request['audio_kind'], request['prompt'], request.get('lyrics', ''),
                                                 credentials, request.get('timeout', 400))
    request['kind'] = 'audio'
    request['seed'] = resolve_video_seed(request.get('seed', -1))
    request['profile'] = dict(python=config['python'], device=config.get('device', 'cuda'))
    result = run_request(request)
    result['sampling'] = {key: request.get(key) for key in ('prompt', 'lyrics', 'duration', 'seed')}
    return result


def music_api_graph(request):
    config = request['audio_config']
    seed = resolve_video_seed(request.get('seed', -1))
    node = lambda kind, **inputs: dict(class_type=kind, inputs=inputs)
    return {
        '1': node('UNETLoader', unet_name=Path(config['model']).name, weight_dtype='default'),
        '2': node('CLIPLoader', clip_name=Path(config['encoder']).name, type='minimax', device='default'),
        '3': node('VAELoader', vae_name=Path(config['vae']).name),
        '4': node('MiniMaxMusic3TextEncode', clip=['2', 0], caption=request['prompt'], lyrics=request.get('lyrics', ''),
                  seed=seed, max_duration=request['duration'], cfg_scale=1.7, top_k=50),
        '5': node('ConditioningZeroOut', conditioning=['4', 0]),
        '6': node('EmptyMiniMaxMusic3LatentAudio', seconds=['4', 1], batch_size=1),
        '7': node('KSampler', model=['1', 0], seed=seed, steps=request.get('steps', 30), cfg=1.7,
                  sampler_name='euler', scheduler='simple', positive=['4', 0], negative=['5', 0], latent_image=['6', 0], denoise=1),
        '8': node('VAEDecodeAudioTiled', samples=['7', 0], vae=['3', 0], tile_size=1536, overlap=64),
        '9': node('SaveAudio', audio=['8', 0], filename_prefix='audio/director-music'),
    }


def music_workflow(request, project_name='Untitled'):
    """Fill Comfy's official editable Music 3 graph, including its subgraph."""
    from importlib.resources import files
    graph = json.loads(files('ltx_prompt_director').joinpath('assets/minimax-music-comfy-workflow.json').read_text(encoding='utf-8'))
    config = request['audio_config']; seed = resolve_video_seed(request.get('seed', -1))
    for node in graph['nodes']:
        if node['id'] == 37:
            node['widgets_values'] = [request['prompt'], request.get('lyrics', ''), request['duration'], seed,
                Path(config['model']).name, Path(config['encoder']).name, Path(config['vae']).name, True]
        elif node['id'] == 35:
            from .media import safe_media_filename
            node['widgets_values'] = ['audio/' + safe_media_filename(project_name + '-music', 'director-music'), 'flac']
    for group in graph.get('definitions', {}).get('subgraphs', []):
        for node in group.get('nodes', []):
            kind = node['type']
            if kind in ('UNETLoader', 'CLIPLoader', 'VAELoader'):
                node['widgets_values'][0] = Path(config[{'UNETLoader': 'model', 'CLIPLoader': 'encoder', 'VAELoader': 'vae'}[kind]]).name
            elif kind == 'MiniMaxMusic3TextEncode':
                node['widgets_values'] = [request['prompt'], request.get('lyrics', ''), seed, 'fixed', request['duration'], 1.7, 50]
            elif kind == 'SeedNode':
                node['widgets_values'] = [seed, 'fixed']
            elif kind == 'EmptyMiniMaxMusic3LatentAudio':
                node['widgets_values'] = [request['duration'], 1]
    return graph
