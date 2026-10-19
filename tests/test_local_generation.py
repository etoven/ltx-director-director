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


def test_preflight_rejects_incomplete_and_incompatible_h3_encoder(tmp_path):
    with pytest.raises(ValueError, match='Configure'): local.validate_profile({}, 'video')
    profile = {key: str(tmp_path / key) for key in ('video_model', 'video_encoder', 'video_vae', 'audio_vae', 'video_assets')}
    profile['video_model'] = str(tmp_path / 'h3_ref2va_pruned.safetensors')
    profile['video_encoder'] = str(tmp_path / 'qwen_nvfp4_awq.safetensors')
    for path in profile.values(): Path(path).touch()
    with pytest.raises(ValueError, match='INT8 ConvRot'): local.validate_profile(profile, 'video')
    profile['video_encoder'] = str(tmp_path / 'qwen_int8_convrot.safetensors'); Path(profile['video_encoder']).touch()
    local.validate_profile(profile, 'video')
    assert local.align_h3_frames(5) == 124
    assert local.align_h3_frames(14.37) == 345
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
    assert events[0]['percent'] == 50
    assert not list(local.data_root().rglob('request.json'))
    assert not list(local.data_root().rglob('worker.stderr'))


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


def test_real_setup_probe_returns_over_stdout_after_backend_log_redirection(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    result = local.run_request({'kind': 'probe', 'profile': {'python': sys.executable}}, timeout=45)
    assert result['kind'] == 'result'
    assert 'packages' in result['message']
    assert result['profile_changes'] == {}
    assert not list(local.data_root().rglob('request.json'))
