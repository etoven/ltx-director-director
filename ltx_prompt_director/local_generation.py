"""Local model profiles and a cancellable JSON-lines worker transport.

Heavy ML libraries are imported only in the separate inference interpreter.
"""
from __future__ import annotations
from contextvars import ContextVar
from pathlib import Path
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4

PROFILE_KEY = 'local_generation/profile_v1'
LOCAL_PROGRESS = ContextVar('local_generation_progress', default=None)
LOCAL_CANCEL = ContextVar('local_generation_cancel', default=None)
DIFFSYNTH_REVISION = '681c456a3e8cf9e17cf2cb9a35297718ed4270ca'


def data_root():
    root = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share') / 'PromptDirector' / 'local-generation'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def engine_python():
    return str(data_root() / 'engine' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python'))


def default_profile():
    return dict(python=engine_python(), models_root=str(Path.home() / 'Programs/ComfyUI/models'),
                video_model='', video_encoder='', video_vae='', audio_vae='', video_assets='', video_variant='ref2va',
                image_model='', image_encoder='', image_vae='', image_assets='', image_variant='4b',
                llm_model='', writer_repo='Qwen/Qwen3-4B-Instruct-2507', device='cuda', offload=True,
                steps=30, preview_every=5, vram_gb=0)


def normalize_profile(profile):
    value = dict(profile)
    for key in ('python', 'models_root', 'video_model', 'video_encoder', 'video_vae', 'audio_vae',
                'video_assets', 'image_model', 'image_encoder', 'image_vae', 'image_assets', 'llm_model'):
        if value.get(key): value[key] = str(Path(value[key]).expanduser())
    return value


def load_profile(settings):
    try: value = json.loads(settings.value(PROFILE_KEY, '{}'))
    except (TypeError, ValueError): value = {}
    return normalize_profile({**default_profile(), **(value if isinstance(value, dict) else {})})


def save_profile(settings, profile):
    settings.setValue(PROFILE_KEY, json.dumps(normalize_profile(profile), ensure_ascii=False))


def gpu_memory():
    try:
        result = subprocess.run(['nvidia-smi', '--query-gpu=memory.total,memory.free', '--format=csv,noheader,nounits'],
                                capture_output=True, text=True, timeout=8, check=True)
        total, free = (float(x.strip()) / 1024 for x in result.stdout.splitlines()[0].split(','))
        return {'total_gb': total, 'free_gb': free}
    except (OSError, ValueError, subprocess.SubprocessError, IndexError):
        return {'total_gb': 0, 'free_gb': 0}


def scan_models(root):
    """Read names only, excluding adapters and LoRAs; never load checkpoint tensors."""
    root = Path(root).expanduser()
    groups = {key: [] for key in ('video_model', 'video_encoder', 'video_vae', 'audio_vae',
                                  'image_model', 'image_encoder', 'image_vae', 'llm_model')}
    if root.is_dir():
        for directory in ('diffusion_models', 'text_encoders', 'vae'):
            folder = root / directory
            if not folder.is_dir(): continue
            for path in sorted(folder.rglob('*.safetensors')):
                name = path.name.lower()
                if directory == 'diffusion_models':
                    if 'minimax' in name and 'pruned' in name: groups['video_model'].append(str(path))
                    if 'flux' in name and ('klein' in name or 'flux2' in name): groups['image_model'].append(str(path))
                elif directory == 'text_encoders':
                    if 'minimax' in name: groups['video_encoder'].append(str(path))
                    elif 'qwen_3_' in name or 'qwen3_' in name: groups['image_encoder'].append(str(path))
                    # Encoder-only checkpoints are deliberately not offered as chat models.
                elif 'minimax' in name and 'video' in name: groups['video_vae'].append(str(path))
                elif 'minimax' in name and 'audio' in name: groups['audio_vae'].append(str(path))
                elif 'flux2' in name: groups['image_vae'].append(str(path))
        for config in root.rglob('config.json'):
            if 'loras' in config.parts: continue
            if (config.parent / 'tokenizer_config.json').is_file() and any(config.parent.glob('*.safetensors')):
                try: info = json.loads(config.read_text(encoding='utf-8'))
                except (OSError, ValueError): continue
                if any(str(name).endswith('ForCausalLM') for name in info.get('architectures', [])):
                    groups['llm_model'].append(str(config.parent))
    return {'files': groups, 'gpu': gpu_memory()}


def recommended_variant(vram_gb):
    # Selection aid, not a fit guarantee: activation memory and host RAM also matter.
    return '9b' if vram_gb >= 20 else '4b'


def recommended_writer(vram_gb):
    if vram_gb >= 32: return 'Qwen/Qwen3-14B'
    if vram_gb >= 16: return 'Qwen/Qwen3-8B'
    if vram_gb >= 6: return 'Qwen/Qwen3-4B-Instruct-2507'
    return 'Qwen/Qwen3-1.7B'


def validate_profile(profile, kind):
    fields = {'video': ('video_model', 'video_encoder', 'video_vae', 'audio_vae', 'video_assets'),
              'image': ('image_model', 'image_encoder', 'image_vae', 'image_assets'),
              'text': ('llm_model',)}[kind]
    missing = [key.replace('_', ' ') for key in fields if not profile.get(key) or not Path(profile[key]).expanduser().exists()]
    if missing: raise ValueError('Configure ' + ', '.join(missing) + ' in Local models first.')
    if kind == 'image':
        variant = profile.get('image_variant', '4b')
        name = Path(profile['image_model']).name.lower()
        if ('4b' in name and variant != '4b') or ('9b' in name and variant != '9b'):
            raise ValueError('The image model file and selected Klein model size do not match.')
        encoder = Path(profile['image_encoder']).name.lower()
        if ('qwen_3_4b' in encoder and variant == '9b') or ('qwen_3_8b' in encoder and variant == '4b'):
            raise ValueError('Klein 4B needs its Qwen 4B encoder; Klein 9B needs its Qwen 8B encoder.')
    if kind == 'video':
        if profile.get('device') == 'cpu':
            raise ValueError('Quantized native H3 video requires an NVIDIA GPU. CPU mode is available for images and writing.')
        if 'pruned' not in Path(profile['video_model']).name.lower():
            raise ValueError('Select a pruned H3 checkpoint. No Lightning/Turbo LoRA is used by this engine.')
        if 'nvfp4' in Path(profile['video_encoder']).name.lower() or 'awq' in Path(profile['video_encoder']).name.lower():
            raise ValueError('The discovered NVFP4/AWQ H3 encoder is not supported by this native backend. '
                             'Choose the INT8 ConvRot encoder or download it from Local models. Your checkpoint is unchanged.')


def align_h3_frames(duration, fps=24):
    import math
    requested = math.ceil(float(duration) * fps)
    if requested < 96 or requested > 360: raise ValueError('H3 local generation requires a duration between 4 and 15 seconds.')
    frames = requested + (5 - requested) % 17
    if frames > 360: raise ValueError('The requested duration exceeds 15 seconds after H3 frame alignment.')
    return frames


def run_request(request, on_event=None, cancel=None, timeout=7200):
    """Run the worker off the UI thread. Its stdout protocol contains no credentials."""
    if on_event is None: on_event = LOCAL_PROGRESS.get()
    profile = normalize_profile(request.get('profile', {}))
    python = profile.get('python') or engine_python()
    if not Path(python).is_file():
        raise RuntimeError('Prepare the local engine in Settings → Local models, or select an inference Python interpreter.')
    root = data_root()
    job = root / 'jobs' / uuid4().hex
    job.mkdir(parents=True, mode=0o700)
    request = {**request, 'profile': profile, 'job_dir': str(job)}
    path = job / 'request.json'; path.write_text(json.dumps(request), encoding='utf-8')
    if os.name != 'nt': path.chmod(0o600)
    env = dict(os.environ, PYTHONUNBUFFERED='1', TOKENIZERS_PARALLELISM='false',
               HF_HUB_DISABLE_TELEMETRY='1', DIFFSYNTH_SKIP_DOWNLOAD='True')
    # The selected interpreter need not have Director installed; use this exact package.
    env['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent) + os.pathsep + env.get('PYTHONPATH', '')
    events = queue.Queue()
    errors = job / 'worker.stderr'
    started = time.monotonic()
    result = None
    try:
        with errors.open('w', encoding='utf-8') as err:
            process = subprocess.Popen([python, '-m', 'ltx_prompt_director.local_worker', str(path)],
                                       stdout=subprocess.PIPE, stderr=err, text=True, env=env,
                                       start_new_session=(os.name != 'nt'))
            def read():
                try:
                    for line in process.stdout:
                        if line.startswith('DIRECTOR_EVENT '):
                            try: events.put(json.loads(line[len('DIRECTOR_EVENT '):]))
                            except (ValueError, TypeError): pass
                finally: events.put(None)
            reader = threading.Thread(target=read, daemon=True); reader.start()
            try:
                while True:
                    if cancel is not None and cancel.is_set(): raise InterruptedError('Local generation cancelled.')
                    if time.monotonic() - started > timeout: raise TimeoutError('Local generation timed out.')
                    try: event = events.get(timeout=.2)
                    except queue.Empty: continue
                    if event is None: break
                    if event.get('kind') == 'error': raise RuntimeError(str(event.get('message', 'Local engine failed.'))[:1500])
                    if event.get('kind') == 'result': result = event
                    if on_event is not None: on_event(event)
                process.wait(timeout=15)
                if process.returncode or result is None:
                    raise RuntimeError('Local engine stopped without a result. Check its dependencies and selected model formats in Local models.')
                return result
            finally:
                if process.poll() is None:
                    import signal
                    if os.name != 'nt': os.killpg(process.pid, signal.SIGTERM)
                    else: process.terminate()
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        if os.name != 'nt': os.killpg(process.pid, signal.SIGKILL)
                        else: subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
                        process.wait()
                if process.stdout: process.stdout.close()
                reader.join(timeout=2)
    finally:
        path.unlink(missing_ok=True)  # Prompt text is not retained in job logs.
        errors.unlink(missing_ok=True)


def generate_image(prompt, profile, source='', references=None):
    validate_profile(profile, 'image')
    event = run_request({'kind': 'image', 'profile': profile, 'prompt': prompt, 'source': source,
                         'references': references or [], 'width': 1024, 'height': 1024,
                         'steps': int(profile.get('steps', 30))}, on_event=LOCAL_PROGRESS.get(), cancel=LOCAL_CANCEL.get())
    return Path(event['path']).read_bytes()


def generate_text(images, rules, profile, schema, timeout):
    validate_profile(profile, 'text')
    from .ai_stream import send_stream
    def progress(event):
        if event.get('kind') == 'text': send_stream('output', event.get('text', ''))
        elif event.get('kind') == 'progress': send_stream('status', event.get('message', 'Local model…'))
    contexts = [{k: item[k] for k in ('name', 'prompt', 'duration', 'role', 'kind') if k in item} for item in images]
    result = run_request({'kind': 'text', 'profile': profile, 'prompt': rules,
                          'images': contexts, 'schema': schema}, on_event=progress, timeout=max(timeout, 120))
    return result['text']
