"""Local model profiles and a cancellable JSON-lines worker transport.

Heavy ML libraries are imported only in the separate inference interpreter.
"""
from __future__ import annotations
from contextvars import ContextVar
from pathlib import Path
import json
import os
import queue
import re
import shutil
import subprocess
import sys
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
                video_model='', video_encoder='', video_vae='', audio_vae='', video_assets='', video_variant='ref2va', video_seed=-1,
                video_width=960, video_height=544, video_duration=15, video_steps=20, video_sampler='res_multistep',
                image_model='', image_encoder='', image_vae='', image_assets='', image_variant='4b',
                llm_model='', writer_repo='Qwen/Qwen3-4B-Instruct-2507', device='cuda', offload=True,
                steps=30, preview_every=5, vram_gb=0)


def normalize_profile(profile):
    value = dict(profile)
    for key in ('python', 'models_root', 'video_model', 'video_encoder', 'video_vae', 'audio_vae',
                'video_assets', 'image_model', 'image_encoder', 'image_vae', 'image_assets', 'llm_model'):
        if value.get(key): value[key] = str(Path(value[key]).expanduser().absolute())
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
            if any(part.lower() in ('loras', 'tts') for part in config.parts): continue
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
        for key, fallback in (('video_width', 960), ('video_height', 544)):
            value = int(profile.get(key, fallback))
            if not 256 <= value <= 2048 or value % 32:
                raise ValueError('Set the video canvas to multiples of 32 between 256 and 2048 in Local models.')
        align_h3_frames(profile.get('video_duration', 15))



def align_h3_frames(duration, fps=24):
    requested = round(float(duration) * fps) + 1
    if not 4 <= float(duration) <= 15: raise ValueError('H3 local generation requires a duration between 4 and 15 seconds.')
    frames = requested + (5 - requested) % 17
    return frames


def resolve_video_seed(value):
    value = int(value)
    if value < 0:
        import secrets
        return secrets.randbelow(2**64)
    if value >= 2**64: raise ValueError('MiniMax seeds must be between 0 and 18446744073709551615.')
    return value


def run_request(request, on_event=None, cancel=None, timeout=None):
    """Run the worker off the UI thread. Its stdout protocol contains no credentials."""
    if on_event is None: on_event = LOCAL_PROGRESS.get()
    if timeout is None: timeout = 86400 if request.get('kind') == 'video' else 7200
    profile = normalize_profile(request.get('profile', {}))
    python = profile.get('python') or engine_python()
    if not Path(python).is_file():
        raise RuntimeError('Prepare the local engine in Settings → Local models, or select an inference Python interpreter.')
    root = data_root()
    job = root / 'jobs' / uuid4().hex
    job.mkdir(parents=True, mode=0o700)
    request = {**request, 'profile': profile, 'job_dir': str(job)}
    path = job / 'request.json'
    env = dict(os.environ, PYTHONUNBUFFERED='1', TOKENIZERS_PARALLELISM='false',
               HF_HUB_DISABLE_TELEMETRY='1', DIFFSYNTH_SKIP_DOWNLOAD='True')
    env.setdefault('PYTORCH_ALLOC_CONF', env.get('PYTORCH_CUDA_ALLOC_CONF', 'expandable_segments:True'))
    interpreter_root = Path(python).parent.parent
    cache_root = interpreter_root if request.get('kind') != 'install' and (interpreter_root / 'pyvenv.cfg').is_file() else root / 'engine'
    cache = cache_root / '.cache'
    for name, directory in (('TRITON_CACHE_DIR', 'triton'), ('TORCHINDUCTOR_CACHE_DIR', 'inductor'),
                            ('CUDA_CACHE_PATH', 'cuda'), ('TORCH_HOME', 'torch')):
        folder = cache / directory; folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        env.setdefault(name, str(folder))
    temporary = cache / 'tmp'; temporary.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ('TMPDIR', 'TMP', 'TEMP'): env[name] = str(temporary)
    # Expose only Director's backend source, never the GUI interpreter's entire
    # site-packages directory or its distribution metadata. ML dependencies
    # must resolve from the selected private interpreter.
    runtime = job / 'runtime'
    package = runtime / 'ltx_prompt_director'; package.mkdir(parents=True)
    source = Path(__file__).parent
    for module in (source / '__init__.py', *source.glob('local_*.py')):
        shutil.copy2(module, package / module.name)
    env['PYTHONPATH'] = str(runtime)
    events = queue.Queue()
    started = time.monotonic()
    tail = []
    secrets = [request.get('hf_token', '')]
    secrets.extend(value for key, value in os.environ.items()
                   if any(name in key.upper() for name in ('API_KEY', 'ACCESS_TOKEN', 'HF_TOKEN')) and len(value) > 6)
    def clean(value):
        value = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', str(value))
        for secret in secrets:
            if secret: value = value.replace(secret, '[redacted]')
        return re.sub(r'(?i)(bearer\s+|(?:api[_-]?key|token)\s*[=:]\s*)[^\s,;]+', r'\1[redacted]', value)
    def deliver(event):
        event = dict(event)
        if isinstance(event.get('message'), str): event['message'] = clean(event['message'])
        # Diagnostic patterns such as "bearer …" and "token=…" can also be
        # ordinary authored prose. Preserve production text; redact exact known
        # credentials if one somehow appears in it.
        if isinstance(event.get('text'), str):
            for secret in secrets:
                if secret: event['text'] = event['text'].replace(secret, '[redacted]')
        if on_event is not None: on_event(event)
        return event
    process = None
    readers = []
    try:
        path.write_text(json.dumps(request), encoding='utf-8')
        if os.name != 'nt': path.chmod(0o600)
        process = subprocess.Popen([python, '-m', 'ltx_prompt_director.local_worker', str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors='replace', env=env,
            cwd=str(job),
            start_new_session=(os.name != 'nt'))
        def read(stream, protocol=False):
            try:
                for line in stream:
                    if protocol and line.startswith('DIRECTOR_EVENT '):
                        try: events.put(json.loads(line[len('DIRECTOR_EVENT '):])); continue
                        except (ValueError, TypeError): pass
                    line = clean(line.rstrip())
                    if line: events.put({'kind': 'log', 'message': line[-4000:]})
            finally: events.put(None)
        for stream, protocol in ((process.stdout, True), (process.stderr, False)):
            reader = threading.Thread(target=read, args=(stream, protocol), daemon=True)
            reader.start(); readers.append(reader)
        ended = 0
        while ended < 2:
            if cancel is not None and cancel.is_set(): raise InterruptedError('Local generation cancelled.')
            if time.monotonic() - started > timeout: raise TimeoutError('Local generation timed out.')
            try: event = events.get(timeout=.2)
            except queue.Empty: continue
            if event is None:
                ended += 1; continue
            event = deliver(event)
            if event.get('kind') == 'log':
                tail.append(event.get('message', '')); tail = tail[-12:]
            if event.get('kind') == 'error': raise RuntimeError(event.get('message', 'Local engine failed.'))
            if event.get('kind') == 'result': return event
        process.wait(timeout=15)
        details = '\n'.join(tail)[-2500:]
        raise RuntimeError(f'Local engine exited with code {process.returncode}.\n{details}')
    finally:
        if process is not None:
            if process.poll() is None:
                import signal
                if os.name != 'nt': os.killpg(process.pid, signal.SIGTERM)
                else: process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if os.name != 'nt': os.killpg(process.pid, signal.SIGKILL)
                    else: subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
                    process.wait()
            for reader in readers: reader.join(timeout=2)
            # Terminal events mean all output files are closed. Release the private
            # process promptly instead of waiting for large-model Python teardown.
            while not events.empty():
                pending = events.get_nowait()
                if isinstance(pending, dict) and pending.get('kind') == 'log': deliver(pending)
            for stream in (process.stdout, process.stderr):
                if stream: stream.close()
        path.unlink(missing_ok=True)  # Prompt text and raw logs are not retained on disk.
        shutil.rmtree(runtime, ignore_errors=True)


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
