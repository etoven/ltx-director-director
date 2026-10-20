"""Adapter profiles and small CPU-only inference checks; never load user models."""
from pathlib import Path
import json
from types import SimpleNamespace
import pytest
from ltx_prompt_director.local_lora import active_loras, generation_prompt, normalize_loras


def test_lora_profiles_triggers_and_preflight(tmp_path):
    path = tmp_path / 'style.safetensors'; path.touch()
    rows = [dict(path=str(path), weight=.65, trigger='ink lines'),
            dict(path=str(path), weight=.2, trigger='ink lines'),
            dict(path='/missing.safetensors', weight=0, trigger='unused'),
            dict(path='/missing-too.safetensors', weight=1, trigger='disabled', enabled=False)]
    profile = dict(image_loras=rows, video_loras=[dict(path=str(path), trigger='film grain')])
    authored = 'A lighthouse on the coast.'
    assert generation_prompt(authored, profile, 'image') == authored + '\n\nink lines'
    assert generation_prompt(authored, profile, 'video') == authored + '\n\nfilm grain'
    assert generation_prompt('ink lines on paper', profile, 'image') == 'ink lines on paper'
    assert authored == 'A lighthouse on the coast.'
    assert len(active_loras(profile, 'image', validate=True)) == 2
    assert normalize_loras([dict(path='')]) == []
    for invalid in (-.1, 1.1, float('nan'), float('inf')):
        with pytest.raises(ValueError, match='between 0 and 1'):
            normalize_loras([dict(path=str(path), weight=invalid)])
    with pytest.raises(ValueError, match='existing .safetensors'):
        active_loras(dict(image_loras=[dict(path='/missing.safetensors')]), 'image', validate=True)


def test_image_adapters_stack_without_changing_packed_weights_and_survive_offload(tmp_path):
    torch = pytest.importorskip('torch')
    from safetensors.torch import save_file
    from accelerate import cpu_offload
    from ltx_prompt_director.local_image_quant import MixedLinear
    from ltx_prompt_director.local_lora import load_image_loras
    class Model(torch.nn.Module):
        dtype = torch.float32
        def __init__(self):
            super().__init__()
            self.proj = MixedLinear(torch.nn.Linear(3, 2, bias=False),
                torch.tensor([[1., 2., 3.], [4., 5., 6.]]).to(torch.float8_e4m3fn), torch.tensor(.5))
        def forward(self, x): return self.proj(x)
    model = Model(); original = model.proj.weight.detach().float().clone()
    x = torch.tensor([[1., 2., 3.]])
    baseline = model(x)
    down = torch.tensor([[1., 0., 2.]])
    up = torch.tensor([[2.], [3.]])
    one = tmp_path / 'one.safetensors'; two = tmp_path / 'two.safetensors'
    save_file({'transformer.proj.lora_A.weight': down, 'transformer.proj.lora_B.weight': up,
               'transformer.proj.alpha': torch.tensor(.5)}, str(one))
    save_file({'proj.lora_A.default.weight': down, 'proj.lora_B.default.weight': up}, str(two))
    logs = []
    load_image_loras(model, dict(image_loras=[dict(path=str(one), weight=.4), dict(path=str(two), weight=.2)]), logs.append)
    expected = baseline + (x @ down.T @ up.T) * (.4 * .5 + .2)
    assert torch.allclose(model(x), expected)
    assert torch.equal(model.proj.weight.float(), original)
    assert model.proj.weight.dtype == torch.float8_e4m3fn and len(logs) == 2
    # The registered residual children participate in the same Accelerate offload
    # used by the real pipeline, including repeat forwards after meta offloading.
    cpu_offload(model, execution_device=torch.device('cpu'), offload_buffers=True)
    assert torch.allclose(model(x), expected)
    assert torch.allclose(model(x), expected)


def test_image_lora_conversion_and_incompatible_adapter_fail_before_patch(tmp_path):
    torch = pytest.importorskip('torch')
    from safetensors.torch import save_file
    from ltx_prompt_director.local_lora import load_image_loras
    class Model(torch.nn.Module):
        dtype = torch.float32
        def __init__(self):
            super().__init__()
            self.transformer_blocks = torch.nn.ModuleList([torch.nn.Linear(3, 2, bias=False)])
    model = Model(); path = tmp_path / 'adapter.safetensors'
    save_file({'diffusion_model.transformer_blocks.0.lora_A.weight': torch.ones(1, 3),
               'diffusion_model.transformer_blocks.0.lora_B.weight': torch.ones(2, 1)}, str(path))
    load_image_loras(model, dict(image_loras=[dict(path=str(path), weight=1)]), lambda _: None)
    assert len(model.transformer_blocks[0]._director_loras) == 1
    fresh = Model()
    save_file({'transformer_blocks.0.lora_A.weight': torch.ones(1, 99),
               'transformer_blocks.0.lora_B.weight': torch.ones(2, 1)}, str(path))
    with pytest.raises(ValueError, match='dimensions'):
        load_image_loras(fresh, dict(image_loras=[dict(path=str(path))]), lambda _: None)
    assert not hasattr(fresh.transformer_blocks[0], '_director_loras')


def test_h3_native_hotload_stack_uses_weights_and_alpha_without_fusion(tmp_path):
    torch = pytest.importorskip('torch')
    from safetensors.torch import save_file
    from diffsynth.utils.lora.minimax_h3 import MiniMaxH3LoRALoader
    from diffsynth.core.vram.layers import AutoWrappedLinear
    from ltx_prompt_director.local_lora import load_video_loras
    original = torch.nn.Linear(3, 2, bias=False)
    config = {k + '_device': 'cpu' for k in ('offload', 'onload', 'preparing', 'computation')}
    config.update({k + '_dtype': torch.float32 for k in ('offload', 'onload', 'preparing', 'computation')})
    layer = AutoWrappedLinear(original, name='proj', **config)
    model = torch.nn.Module(); model.add_module('proj', layer)
    pipe = SimpleNamespace(dit=model, torch_dtype=torch.float32, lora_loader=MiniMaxH3LoRALoader,
                           enable_lora_hot_loading=lambda m: m)
    path = tmp_path / 'h3.safetensors'
    down, up = torch.ones(1, 3), torch.tensor([[2.], [3.]])
    save_file({'proj.lora_A.weight': down, 'proj.lora_B.weight': up, 'proj.alpha': torch.tensor(.5)}, str(path))
    x = torch.ones(1, 3); baseline = layer(x); weights = layer.weight.detach().clone()
    logs = []
    load_video_loras(pipe, dict(video_loras=[dict(path=str(path), weight=.6), dict(path=str(path), weight=.2)]), logs.append)
    assert torch.allclose(layer(x), baseline + (x @ down.T @ up.T) * .4)
    assert torch.equal(layer.weight, weights)
    assert len(layer.lora_A_weights) == len(logs) == 2


def test_lora_reader_honors_training_metadata_and_rejects_base_files(tmp_path):
    torch = pytest.importorskip('torch')
    from safetensors.torch import save_file
    from ltx_prompt_director.local_lora import _read_lora
    path = tmp_path / 'adapter.safetensors'
    weights = {'proj.lora_A.weight': torch.ones(4, 3), 'proj.lora_B.weight': torch.ones(2, 4)}
    save_file(weights, str(path), metadata={'lora_adapter_metadata': json.dumps({'transformer.lora_alpha': 2})})
    state = _read_lora(str(path), 'Flux Klein')
    assert torch.equal(state['proj.lora_A.weight'], weights['proj.lora_A.weight'] * .5)
    save_file(weights, str(path), metadata={'lora_adapter_metadata': json.dumps({'lora_alpha': 2, 'use_rslora': True})})
    assert torch.equal(_read_lora(str(path), 'Flux Klein')['proj.lora_A.weight'], weights['proj.lora_A.weight'])
    save_file({'proj.weight': torch.ones(2, 3)}, str(path))
    with pytest.raises(ValueError, match='base checkpoint'): _read_lora(str(path), 'Flux Klein')
