"""LoRA profiles and native adapters. ML imports stay inside the worker functions."""
from pathlib import Path
import json
import math
import re


def normalize_loras(rows):
    result = []
    if not isinstance(rows, list):
        raise ValueError('LoRAs must be a list of adapters.')
    for row in rows:
        if not isinstance(row, dict): raise ValueError('Invalid LoRA entry.')
        path = str(row.get('path') or '').strip()
        if not path: continue
        weight = float(row.get('weight', 1))
        if not math.isfinite(weight) or not 0 <= weight <= 1:
            raise ValueError('LoRA weights must be between 0 and 1.')
        result.append(dict(path=str(Path(path).expanduser().absolute()), weight=weight,
                           trigger=str(row.get('trigger') or '').strip(), enabled=bool(row.get('enabled', True))))
    return result


def active_loras(profile, kind, validate=False):
    rows = [r for r in normalize_loras(profile.get(kind + '_loras', [])) if r['enabled'] and r['weight'] > 0]
    if validate:
        for row in rows:
            path = Path(row['path'])
            if path.suffix.lower() != '.safetensors' or not path.is_file():
                raise ValueError(f'Choose an existing .safetensors {kind} LoRA in Settings: {path.name}')
    return rows


def generation_prompt(prompt, profile, kind):
    """Append active triggers once; never edit the project's authored source."""
    triggers = []
    for row in active_loras(profile, kind):
        trigger = row['trigger']
        if trigger and not re.search(r'(?<!\w)' + re.escape(trigger) + r'(?!\w)', prompt, re.IGNORECASE) and trigger not in triggers:
            triggers.append(trigger)
    return prompt + ('\n\n' + ', '.join(triggers) if triggers else '')


def _read_lora(path, kind):
    from safetensors import safe_open
    with safe_open(path, framework='pt', device='cpu') as source:
        keys = list(source.keys())
        down_keys = [k for k in keys if re.search(r'\.(?:lora_A(?:\.[^.]+)?|lora_down)\.weight$', k)]
        if not down_keys: raise ValueError('Choose a standard LoRA .safetensors adapter, rather than a base checkpoint.')
        metadata = json.loads((source.metadata() or {}).get('lora_adapter_metadata', '{}'))
        if not isinstance(metadata, dict): raise ValueError('Invalid LoRA adapter metadata.')
        # Diffusers pipeline exports prefix each config key with transformer.
        metadata = {k.removeprefix('transformer.'): v for k, v in metadata.items()}
        if any('dora' in k.lower() for k in keys) or metadata.get('use_dora'):
            raise ValueError(f'DoRA adapters are not supported; choose a standard {kind} LoRA.')
        if metadata.get('lora_bias') or metadata.get('bias', 'none') != 'none':
            raise ValueError('LoRA bias adapters are not supported; choose a standard LoRA.')
        state = {k: source.get_tensor(k) for k in keys}
    # Apply training alpha before format conversion so fused QKV/MLP converters
    # preserve per-target strengths too. Converted layers then use unit alpha.
    for key in down_keys:
        name = re.split(r'\.lora_(?:A|down)', key)[0]
        rank = state[key].shape[0]
        if rank < 1: raise ValueError('LoRA rank must be positive.')
        alpha = state.pop(name + '.alpha', metadata.get('lora_alpha', rank))
        for pattern, value in (metadata.get('alpha_pattern') or {}).items():
            if re.search(r'(?:^|\.)' + pattern + '$', name):
                alpha = value; break
        alpha = float(alpha)
        if not math.isfinite(alpha): raise ValueError('LoRA alpha must be finite.')
        divisor = math.sqrt(rank) if metadata.get('use_rslora') else rank
        state[key] = state[key] * (alpha / divisor)
    return state


def _pairs(state, model):
    """Validate every adapter target before changing the model."""
    normalized = {}
    for key, tensor in state.items():
        key = re.sub(r'\.lora_([AB])\.[^.]+\.weight$', r'.lora_\1.weight', key)
        if key.startswith('transformer.'): key = key[len('transformer.'):]
        normalized[key] = tensor
    pairs = []
    consumed = set()
    for key in normalized:
        if not key.endswith('.lora_A.weight'): continue
        name = key.removesuffix('.lora_A.weight'); bkey = name + '.lora_B.weight'
        if bkey not in normalized: raise ValueError(f'LoRA is missing its up matrix: {name}')
        down, up = normalized[key], normalized[bkey]
        try: layer = model.get_submodule(name)
        except AttributeError: raise ValueError(f'LoRA target does not match this model: {name}') from None
        if (down.ndim != 2 or up.ndim != 2 or down.shape[0] < 1 or
            up.shape[1] != down.shape[0] or down.shape[1] != getattr(layer, 'in_features', None) or
            up.shape[0] != getattr(layer, 'out_features', None)):
            raise ValueError(f'LoRA dimensions do not match this model: {name}')
        alpha = normalized.get(name + '.alpha')
        scale = float(alpha) / down.shape[0] if alpha is not None else 1.
        if not math.isfinite(scale): raise ValueError(f'Invalid LoRA alpha: {name}')
        pairs.append((layer, down * scale, up))
        consumed.update((key, bkey, name + '.alpha'))
    extra = set(normalized) - consumed
    if extra: raise ValueError('Unsupported LoRA tensors: ' + ', '.join(sorted(extra)[:3]))
    if not pairs: raise ValueError('No compatible LoRA layers were found in this file.')
    return pairs


def load_image_loras(transformer, profile, callback):
    rows = active_loras(profile, 'image', validate=True)
    if not rows: return
    import torch
    from diffusers.loaders.lora_conversion_utils import (
        _convert_kohya_flux2_lora_to_diffusers, _convert_non_diffusers_flux2_lora_to_diffusers)

    class Residual(torch.nn.Module):
        def __init__(self, down, up, weight, dtype):
            super().__init__()
            self.down = torch.nn.Linear(down.shape[1], down.shape[0], bias=False, dtype=dtype)
            self.up = torch.nn.Linear(up.shape[1], up.shape[0], bias=False, dtype=dtype)
            self.down.weight = torch.nn.Parameter(down.to(dtype) * weight, requires_grad=False)
            self.up.weight = torch.nn.Parameter(up.to(dtype), requires_grad=False)

        def forward(self, x): return self.up(self.down(x))

    def add_residual(layer, inputs, output):
        for residual in layer._director_loras:
            output = output + residual(inputs[0].to(output))
        return output

    for row in rows:
        state = _read_lora(row['path'], 'Flux Klein')
        if any('.lora_down.weight' in k for k in state):
            state = _convert_kohya_flux2_lora_to_diffusers(state)
        else:
            state = {k.replace('base_model.model.', 'diffusion_model.', 1): v for k, v in state.items()}
            if any(k.startswith('diffusion_model.') for k in state):
                state = _convert_non_diffusers_flux2_lora_to_diffusers(state)
        pairs = _pairs(state, transformer)
        for layer, down, up in pairs:
            if not hasattr(layer, '_director_loras'):
                layer.add_module('_director_loras', torch.nn.ModuleList())
                layer.register_forward_hook(add_residual)
            # Register children before Accelerate configures offload. Packed base
            # weights remain immutable; adapters run as separate low-rank passes.
            layer._director_loras.append(Residual(down, up, row['weight'], transformer.dtype))
        callback(f'Image LoRA: {Path(row["path"]).name} · weight {row["weight"]:g} · {len(pairs)} layers')


def load_video_loras(pipe, profile, callback):
    rows = active_loras(profile, 'video', validate=True)
    if not rows: return
    pipe.dit = pipe.enable_lora_hot_loading(pipe.dit)
    for row in rows:
        state = _read_lora(row['path'], 'H3')
        state = {k.removeprefix('base_model.model.').removeprefix('diffusion_model.'): v for k, v in state.items()}
        converted = pipe.lora_loader(device='cpu', torch_dtype=pipe.torch_dtype).convert_state_dict(state)
        pairs = _pairs(converted, pipe.dit)
        if any(not hasattr(layer, 'lora_A_weights') for layer, _, _ in pairs):
            raise ValueError('This H3 layer does not support native LoRA hot-loading.')
        for layer, down, up in pairs:
            layer.lora_A_weights.append(down.to(pipe.torch_dtype) * row['weight'])
            layer.lora_B_weights.append(up.to(pipe.torch_dtype))
        callback(f'Video LoRA: {Path(row["path"]).name} · weight {row["weight"]:g} · {len(pairs)} layers')
