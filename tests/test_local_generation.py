import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import patch
import pytest
from ltx_prompt_director import local_generation as local


def test_scanning_preserves_models_excludes_loras_and_distinguishes_encoders(tmp_path):
    names = ['diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors',
             'diffusion_models/flux-2-klein-base-9b-fp8.safetensors',
             'text_encoders/qwen_3_8b_fp8mixed.safetensors',
             'text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors',
             'text_encoders/qwen3.5_2b_bf16.safetensors',
             'vae/flux2-vae.safetensors', 'loras/flux2-lightning.safetensors']
    for name in names:
        path = tmp_path / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'untouched')
    speech = tmp_path / 'TTS/speech-emotion'; speech.mkdir(parents=True)
    (speech / 'config.json').write_text(json.dumps({'architectures': ['Qwen3ForCausalLM']}))
    (speech / 'tokenizer_config.json').write_text('{}'); (speech / 'model.safetensors').touch()
    with patch.object(local, 'gpu_memory', return_value={'total_gb': 12, 'free_gb': .4}):
        found = local.scan_models(str(tmp_path))
    assert len(found['files']['video_model']) == 1
    assert len(found['files']['image_model']) == 1
    assert found['files']['llm_model'] == []
    assert 'qwen_3_8b' in found['files']['image_encoder'][0]
    assert all((tmp_path / name).read_bytes() == b'untouched' for name in names)
    assert local.recommended_variant(12) == '4b'
    assert local.recommended_variant(24) == '9b'
    assert local.recommended_writer(4) == 'Qwen/Qwen3-1.7B'
    assert local.recommended_writer(12) == 'Qwen/Qwen3-4B-Instruct-2507'
    assert local.recommended_writer(24) == 'Qwen/Qwen3-8B'


def test_preflight_accepts_existing_h3_encoders_and_rejects_incomplete_setup(tmp_path):
    with pytest.raises(ValueError, match='Configure'): local.validate_profile({}, 'video')
    profile = {key: str(tmp_path / key) for key in ('video_model', 'video_encoder', 'video_vae', 'audio_vae', 'video_assets')}
    profile['video_model'] = str(tmp_path / 'h3_ref2va_pruned.safetensors')
    profile['video_encoder'] = str(tmp_path / 'qwen_nvfp4_awq.safetensors')
    for path in profile.values(): Path(path).touch()
    local.validate_profile(profile, 'video')
    profile['video_encoder'] = str(tmp_path / 'qwen_int8_convrot.safetensors'); Path(profile['video_encoder']).touch()
    local.validate_profile(profile, 'video')
    assert local.align_h3_frames(5) == 124
    assert local.align_h3_frames(14.3) == 345
    assert local.align_h3_frames(14.37) == 362
    assert local.align_h3_frames(15) == 362
    with pytest.raises(ValueError): local.align_h3_frames(20)


def fake_worker(monkeypatch, code):
    real = subprocess.Popen
    processes = []
    def launch(command, **kwargs):
        child = real([sys.executable, '-u', '-c', code], **kwargs)
        processes.append(child); return child
    monkeypatch.setattr(local.subprocess, 'Popen', launch)
    return processes


def test_protocol_delivers_progress_and_result_without_retaining_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    events = []
    fake_worker(monkeypatch, '''import json
print('ordinary backend log')
for x in [{'kind':'progress','percent':50,'eta_seconds':2}, {'kind':'result','path':'ready.mp4'}]:
 print('DIRECTOR_EVENT '+json.dumps(x),flush=True)
''')
    result = local.run_request({'kind': 'video', 'prompt': 'private authored content', 'profile': {'python': sys.executable}}, events.append)
    assert result['path'] == 'ready.mp4'
    assert next(e for e in events if e['kind'] == 'progress')['percent'] == 50
    assert any(e['kind'] == 'log' and 'ordinary backend log' in e['message'] for e in events)
    assert not list(local.data_root().rglob('request.json'))
    assert not list(local.data_root().rglob('worker.stderr'))


def test_worker_exposes_only_backend_source_and_preserves_venv_interpreter(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    monkeypatch.setenv('PYTHONPATH', '/unrelated/gui/site-packages')
    fake_worker(monkeypatch, '''import json,os
from ltx_prompt_director import local_generation
print('DIRECTOR_EVENT '+json.dumps({'kind':'result','module':local_generation.__file__,
 'cwd':os.getcwd(),'search':os.environ['PYTHONPATH']}),flush=True)
''')
    result = local.run_request({'kind': 'probe', 'profile': {'python': sys.executable}})
    assert result['module'].startswith(result['cwd'] + '/runtime/ltx_prompt_director/')
    assert result['search'] == result['cwd'] + '/runtime'
    assert not list(local.data_root().rglob('runtime'))
    python = tmp_path / 'engine/bin/python'; python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    assert local.normalize_profile({'python': str(python)})['python'] == str(python)


def test_diagnostic_redaction_preserves_ordinary_generated_prose(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    text = 'The bearer of the lantern reveals a sign reading "token=gold".'
    fake_worker(monkeypatch, 'import json; print("DIRECTOR_EVENT "+json.dumps({"kind":"result","text":' + repr(text) + '}),flush=True)')
    result = local.run_request({'kind': 'text', 'profile': {'python': sys.executable}})
    assert result['text'] == text


def test_cancellation_terminates_only_its_worker_and_cleans_request(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    processes = fake_worker(monkeypatch, 'import time;time.sleep(30)')
    cancel = threading.Event(); timer = threading.Timer(.3, cancel.set); timer.start()
    try:
        with pytest.raises(InterruptedError):
            local.run_request({'kind': 'video', 'profile': {'python': sys.executable}}, cancel=cancel)
    finally: timer.cancel()
    assert len(processes) == 1 and processes[0].poll() is not None
    assert not list(local.data_root().rglob('request.json'))


def test_local_artwork_and_text_use_separate_native_adapters(monkeypatch):
    from ltx_prompt_director.artwork_tools import available_artwork_tools, ArtworkTool
    from ltx_prompt_director import ai, response_schemas
    profile = {'image_model': '/mine/flux.safetensors'}
    tools = available_artwork_tools({'local': profile}, selected='local:flux2')
    assert tools == [ArtworkTool('local', 'flux2', '', profile)]
    assert tools[0].label == 'Local Flux 2 Klein'
    with patch.object(local, 'generate_text', return_value='{"prompt":"Keep the hand in frame."}') as generate:
        with response_schemas.using(response_schemas.PROMPT):
            assert json.loads(ai._provider_raw([], 'local', '/local/writer', {}, 'Refine.', 400))['prompt'] == 'Keep the hand in frame.'
        assert generate.call_args.args[3]['title'] == 'production_prompt'


def test_reference_video_decode_uses_trim_and_stops_at_requested_span(tmp_path):
    import av
    from PIL import Image
    from hashlib import sha256
    from ltx_prompt_director.local_worker import read_reference_video
    path = tmp_path / 'source.mp4'
    with av.open(str(path), 'w') as container:
        stream = container.add_stream('libx264', rate=24); stream.width = stream.height = 32; stream.pix_fmt = 'yuv420p'
        for index in range(24):
            frame = av.VideoFrame.from_image(Image.new('RGB', (32, 32), (index * 10, 0, 0)))
            for packet in stream.encode(frame): container.mux(packet)
        for packet in stream.encode(): container.mux(packet)
    original = sha256(path.read_bytes()).hexdigest()
    frames = read_reference_video(path, start=.5, duration=.25, width=32, height=32)
    assert len(frames) == 6
    assert 110 <= frames[0].getpixel((16, 16))[0] <= 130
    assert frames[-1].getpixel((16, 16))[0] > frames[0].getpixel((16, 16))[0]
    assert sha256(path.read_bytes()).hexdigest() == original


def test_reference_audio_uses_bundled_decoder_trim_and_resample(tmp_path):
    import wave
    import numpy as np
    from ltx_prompt_director.local_worker import read_reference_audio
    source = tmp_path / 'reference.wav'
    samples = (np.sin(np.arange(16000) * 2 * np.pi * 440 / 16000) * 16000).astype('<i2')
    with wave.open(str(source), 'wb') as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(16000); output.writeframes(samples.tobytes())
    original = source.read_bytes()
    audio, rate = read_reference_audio(source, start=.5, duration=.25)
    assert rate == 32000 and audio.shape == (2, 8000)
    assert audio.dtype == np.float32 and np.isfinite(audio).all()
    assert audio.std() > .1
    assert source.read_bytes() == original


@pytest.mark.skipif(os.environ.get('DIRECTOR_TEST_ML') != '1', reason='Optional CPU tensor-format verification; no GPU is used.')
def test_scaled_fp8_compatibility_cache_preserves_original_weights(tmp_path):
    torch = pytest.importorskip('torch')
    safe = pytest.importorskip('safetensors.torch')
    if not hasattr(torch, 'float8_e4m3fn'): pytest.skip('FP8 requires a newer PyTorch.')
    from ltx_prompt_director.local_worker import prepare_float_checkpoint
    source = tmp_path / 'source.safetensors'
    weights = torch.tensor([[1., 2.], [3., 4.]], dtype=torch.float32).to(torch.float8_e4m3fn)
    safe.save_file({'layer.weight': weights, 'layer.weight_scale': torch.tensor([2., 3.]),
                    'layer.input_scale': torch.tensor(1.), 'counter': torch.tensor(7)}, str(source))
    before = source.read_bytes()
    target = prepare_float_checkpoint(source, tmp_path / 'cache', lambda *_: None)
    loaded = safe.load_file(target)
    assert loaded['layer.weight'].dtype == torch.bfloat16
    assert torch.equal(loaded['layer.weight'], torch.tensor([[2., 4.], [9., 12.]], dtype=torch.bfloat16))
    assert loaded['counter'].item() == 7
    assert 'layer.weight_scale' not in loaded
    assert source.read_bytes() == before
    assert prepare_float_checkpoint(source, tmp_path / 'cache') == target


@pytest.mark.skipif(os.environ.get('DIRECTOR_TEST_ML') != '1', reason='Requires the fully installed inference runtime.')
def test_real_setup_probe_returns_over_stdout_after_backend_log_redirection(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    result = local.run_request({'kind': 'probe', 'profile': {'python': sys.executable}}, timeout=45)
    assert result['kind'] == 'result'
    assert 'verified' in result['message']
    assert result['profile_changes'] == {}
    assert not list(local.data_root().rglob('request.json'))


def test_worker_streams_stderr_and_reports_crash_details_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    monkeypatch.setenv('HF_TOKEN', 'secret-test-token-123')
    events = []
    fake_worker(monkeypatch, "import sys;print('loading checkpoint',file=sys.stderr);print('HF_TOKEN=secret-test-token-123',file=sys.stderr);sys.exit(3)")
    with pytest.raises(RuntimeError, match='loading checkpoint') as error:
        local.run_request({'kind': 'video', 'profile': {'python': sys.executable}}, events.append)
    assert 'secret-test-token-123' not in str(error.value)
    assert any('loading checkpoint' in e.get('message', '') for e in events)
    assert all('secret-test-token-123' not in json.dumps(e) for e in events)
    assert not list(local.data_root().rglob('request.json'))


def test_setup_does_not_claim_success_when_dependencies_fail(monkeypatch):
    from ltx_prompt_director import local_setup
    events = []
    monkeypatch.setattr(local_setup, 'check_engine', lambda *_: (_ for _ in ()).throw(ImportError('missing dependency')))
    monkeypatch.setattr(local_setup, 'emit', lambda *args, **kw: events.append((args, kw)))
    with pytest.raises(ImportError, match='missing dependency'):
        local_setup.setup_request({'kind': 'probe', 'profile': {}})
    assert not events


def test_engine_runtime_matches_driver_and_gpu_without_changing_other_apps(monkeypatch):
    from ltx_prompt_director import local_setup
    monkeypatch.setattr(local_setup.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 0, '615.71.09, 8.6\n', ''))
    assert local_setup.pytorch_wheel_variant('cuda') == 'cu130'
    assert local_setup.pytorch_wheel_variant('cpu') == 'cpu'
    monkeypatch.setattr(local_setup.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 0, '570.00, 8.6\n', ''))
    assert local_setup.pytorch_wheel_variant('cuda') == 'cu128'


def test_engine_upgrade_replaces_cuda_suffix_inside_its_private_venv(tmp_path, monkeypatch):
    from ltx_prompt_director import local_setup
    python = str(tmp_path / 'engine/bin/python')
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps({'torch': '2.9.1+cu128',
            'torchvision': '0.24.1+cu128', 'nvidia-cublas-cu12': '12.8', 'Pillow': '13.0'}), '')
    monkeypatch.setattr(local_setup.subprocess, 'run', run)
    local_setup.install_pytorch(python, tmp_path / 'engine', 'cu130')
    assert all(command[0] == python for command in calls)
    remove = next(command for command in calls if 'uninstall' in command)
    assert 'nvidia-cublas-cu12' in remove and 'Pillow' not in remove
    install = calls[-1]
    assert 'torch==2.9.1+cu130' in install
    assert str(tmp_path / 'engine/.cache/pip') in install
    assert '--user' not in install
    monkeypatch.setattr(local_setup.subprocess, 'run', lambda *a, **kw: subprocess.CompletedProcess(a, 0, '580.88, 6.1\n', ''))
    assert local_setup.pytorch_wheel_variant('cuda') == 'cu128'


def test_minimax_random_and_static_seed_are_resolved_without_changing_profile(monkeypatch):
    import secrets
    monkeypatch.setattr(secrets, 'randbelow', lambda upper: upper - 1)
    assert local.default_profile()['video_seed'] == -1
    assert local.resolve_video_seed(-1) == 18446744073709551615
    assert local.resolve_video_seed(0) == 0
    assert local.resolve_video_seed(12345) == 12345
    assert local.resolve_video_seed(931351171509952) == 931351171509952
    with pytest.raises(ValueError): local.resolve_video_seed(2**64)


@pytest.mark.skipif(os.environ.get('DIRECTOR_TEST_ML') != '1', reason='Optional native sampler tensor verification.')
def test_h3_joint_res_sampler_matches_reference_trajectory_and_reset():
    import torch
    from ltx_prompt_director.local_h3_sampling import H3ResMultistepScheduler
    video = H3ResMultistepScheduler(); audio = H3ResMultistepScheduler(audio=True)
    def render():
        video.set_timesteps(20); audio.set_timesteps(20)
        v = torch.tensor([.1, .4, -.5]); a = torch.tensor([.3, -.6, .7])
        for tv, ta in zip(video.timesteps, audio.timesteps):
            v = video.step(.2 * v + float(tv) / 1000 * .3, tv, v)
            a = audio.step(.2 * a + float(ta) / 1000 * .3, ta, a)
        return torch.stack((v, a))
    # Independent upstream joint-packed RES reference, with audio scaled back.
    expected = torch.tensor([[-.069295913, .175672352, -.559232414],
                             [.117338724, -.619823277, .444966197]])
    actual = render()
    torch.testing.assert_close(actual, expected, rtol=2e-5, atol=2e-6)
    torch.testing.assert_close(render(), actual, rtol=0, atol=0)


def test_completed_output_releases_worker_without_waiting_for_model_teardown(tmp_path, monkeypatch):
    import time
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    processes = fake_worker(monkeypatch, 'import time;print(\'DIRECTOR_EVENT {"kind":"result","path":"done.png"}\',flush=True);time.sleep(30)')
    start = time.monotonic()
    assert local.run_request({'kind': 'image', 'profile': {'python': sys.executable}})['path'] == 'done.png'
    assert time.monotonic() - start < 8
    assert processes[0].poll() is not None


@pytest.mark.skipif(os.environ.get('DIRECTOR_TEST_ML') != '1', reason='Optional inference-runtime numeric verification.')
def test_h3_convolution_compatibility_is_numerically_equivalent():
    import torch
    from ltx_prompt_director.local_h3_quant import LinearPatchProjection, ImageSpatialConv
    from diffsynth.models.minimax_h3_video_vae import BaseConv3d
    with torch.inference_mode():
        conv = torch.nn.Conv3d(3, 5, (2, 4, 4), stride=(2, 4, 4))
        pixels = torch.randn(3, 3, 2, 4, 4); expected = conv(pixels)
        conv.__class__ = LinearPatchProjection
        torch.testing.assert_close(conv(pixels), expected, atol=1e-6, rtol=1e-5)
        for mode in ('zeros', 'reflect'):
            for temporal in ('zeros', 'replicate'):
                for kernel in (1, 3):
                    conv = BaseConv3d(3, 5, kernel, padding=kernel // 2, padding_mode=mode, padding_mode_t=temporal)
                    pixels = torch.randn(2, 3, 1, 8, 8); expected = conv(pixels)
                    conv.__class__ = ImageSpatialConv
                    torch.testing.assert_close(conv(pixels), expected, atol=1e-6, rtol=1e-5)


@pytest.mark.skipif(os.environ.get('DIRECTOR_TEST_ML') != '1', reason='Optional decoder postprocessing tensor verification.')
def test_h3_video_pixel_chunks_match_original_normalization():
    import torch
    from torchvision.transforms import Normalize
    from diffsynth.models.minimax_h3_video_vae import VAEProcessor
    from ltx_prompt_director.local_h3_quant import video_pixel_chunks
    processor = VAEProcessor(None, Normalize((-.4, -.3, -.2), (4., 3., 2.)))
    with torch.inference_mode():
        pixels = torch.randn(2, 3, 19, 8, 8)
        expected = processor.revert_tensor(pixels)
        actual = video_pixel_chunks(processor.revert_tensor, pixels.clone())
        torch.testing.assert_close(actual, expected, rtol=0, atol=0)


def test_portable_embedded_reference_survives_a_missing_original_path():
    from PIL import Image
    from io import BytesIO
    import base64
    from ltx_prompt_director.local_worker import _pil_reference
    output = BytesIO(); Image.new('RGB', (8, 8), 'red').save(output, format='PNG')
    reference = {'path': '/missing/original.png', 'image': 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()}
    assert _pil_reference(reference).getpixel((0, 0)) == (255, 0, 0)


def test_video_preview_defaults_and_optional_denoised_schedule():
    from ltx_prompt_director.local_previews import denoised_preview_due
    profile = local.default_profile()
    assert profile['video_preview_mode'] == 'noisy'
    assert profile['video_preview_every'] == 5
    assert not any(denoised_preview_due(profile, n, 20) for n in range(21))
    profile['video_preview_mode'] = 'denoised'
    assert [n for n in range(21) if denoised_preview_due(profile, n, 20)] == [5, 10, 15]
    profile['video_preview_every'] = 3
    assert [n for n in range(21) if denoised_preview_due(profile, n, 20)] == [3, 6, 9, 12, 15, 18]
