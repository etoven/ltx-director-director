"""Load Klein mixed-precision files without expanding whole models in RAM.

Only the active linear layer is expanded for BF16 computation. Checkpoint
shapes, scales and metadata are validated; missing weights never initialize
randomly. This module is copied into the selected private engine with workers.
"""
import json
import torch


class MixedLinear(torch.nn.Linear):
    def __init__(self, original, weight, scale, block_scale=None, pre_scale=None):
        with torch.device('meta'):
            super().__init__(original.in_features, original.out_features, original.bias is not None,
                             dtype=torch.bfloat16)
        self.weight = torch.nn.Parameter(weight, requires_grad=False)
        self.register_buffer('weight_scale', scale)
        self.register_buffer('block_scale', block_scale)
        self.register_buffer('pre_quant_scale', pre_scale)

    def forward(self, inputs):
        if self.block_scale is None:
            weight = self.weight.to(dtype=inputs.dtype)
            scale = self.weight_scale.to(weight)
            if scale.ndim == 1 and scale.numel() == weight.shape[0]: scale = scale[:, None]
            weight = weight * scale
        else:
            import comfy_kitchen as ck
            from comfy_kitchen.tensor import QuantizedTensor, get_layout_class
            if torch.version.cuda is None or int(torch.version.cuda.split('.')[0])<13:ck.registry.disable('cuda')
            params = get_layout_class('TensorCoreNVFP4Layout').Params(
                scale=self.weight_scale.to(device=inputs.device, dtype=torch.float32),
                block_scale=self.block_scale.to(device=inputs.device).view(torch.float8_e4m3fn),
                orig_dtype=inputs.dtype, orig_shape=(self.out_features, self.in_features))
            weight = QuantizedTensor(self.weight, 'TensorCoreNVFP4Layout', params).dequantize().to(inputs)
        if self.pre_quant_scale is not None: inputs = inputs * self.pre_quant_scale.to(inputs)
        return torch.nn.functional.linear(inputs, weight, self.bias)


def _replace(model, name, module):
    parent, _, child = name.rpartition('.')
    setattr(model.get_submodule(parent) if parent else model, child, module)


def assign_weights(model, state, scales=None, dtype=torch.bfloat16):
    """Assign an already converted state, preserving FP8 storage and scales."""
    scales = scales or {}
    expected = model.state_dict()
    if set(expected) != set(state):
        missing = sorted(set(expected)-set(state)); extra = sorted(set(state)-set(expected))
        raise ValueError(f'Image checkpoint keys differ from configuration: missing {missing[:5]}, extra {extra[:5]}.')
    for key, value in state.items():
        if tuple(value.shape) != tuple(expected[key].shape):
            raise ValueError(f'Image configuration mismatch at {key}: checkpoint {tuple(value.shape)}, expected {tuple(expected[key].shape)}.')
    for key, value in list(state.items()):
        if value.dtype not in (torch.float8_e4m3fn, torch.float8_e5m2):
            if value.is_floating_point():state[key]=value.to(dtype)
            continue
        if not key.endswith('.weight') or value.ndim != 2:
            state[key] = value.to(dtype); continue
        name = key.removesuffix('.weight'); original = model.get_submodule(name)
        if not isinstance(original, torch.nn.Linear): raise ValueError(f'Unsupported quantized image layer: {name}')
        scale = scales.get(value.untyped_storage().data_ptr(), torch.tensor(1.))
        _replace(model, name, MixedLinear(original, value, scale))
        state[name+'.weight_scale'] = scale
    # Assign validates every parameter and avoids a second complete model copy.
    model.load_state_dict(state, strict=True, assign=True)
    model.requires_grad_(False)
    # Pipeline conditioning uses model.dtype. Packed storage is not the compute
    # dtype; a zero-sized root parameter advertises the latter without allocation.
    model.register_parameter('_director_compute_dtype', torch.nn.Parameter(torch.empty(0,dtype=dtype),requires_grad=False))
    return model


def load_klein_transformer(path, assets, dtype, callback=lambda *_:None):
    from accelerate import init_empty_weights
    from safetensors import safe_open
    from diffusers import Flux2Transformer2DModel
    from diffusers.loaders.single_file_utils import convert_flux2_transformer_checkpoint_to_diffusers
    state={}
    with safe_open(str(path),framework='pt',device='cpu') as source:
        names=list(source.keys())
        for index,key in enumerate(names):
            state[key]=source.get_tensor(key)
            if index%20==0:callback(f'Loading packed image transformer · tensor {index+1}/{len(names)}')
    scales = {}
    for key, value in state.items():
        if key.endswith('.weight') and value.dtype in (torch.float8_e4m3fn, torch.float8_e5m2):
            scales[value.untyped_storage().data_ptr()] = state.get(key.removesuffix('weight')+'weight_scale', torch.tensor(1.))
    state = {k:v for k,v in state.items() if not k.endswith(('.comfy_quant','.weight_scale','.weight_scale_2','.input_scale'))}
    state = convert_flux2_transformer_checkpoint_to_diffusers(state)
    with init_empty_weights():
        model = Flux2Transformer2DModel.from_config(str(assets), subfolder='transformer', torch_dtype=dtype)
    return assign_weights(model, state, scales, dtype)


def load_mixed_qwen(path, config_path, dtype, callback=lambda *_:None):
    from accelerate import init_empty_weights
    from accelerate.utils import set_module_tensor_to_device
    from safetensors import safe_open
    from transformers import AutoConfig, AutoModelForCausalLM
    config = AutoConfig.from_pretrained(str(config_path), local_files_only=True)
    config.tie_word_embeddings = True
    with init_empty_weights(): model = AutoModelForCausalLM.from_config(config, torch_dtype=dtype)
    with safe_open(str(path), framework='pt', device='cpu') as source:
        names = set(source.keys()); consumed = set()
        for key in sorted(names):
            if not key.endswith('.comfy_quant'): continue
            prefix = key.removesuffix('.comfy_quant')
            marker = json.loads(bytes(source.get_tensor(key).to(torch.uint8).tolist()).decode())
            format = marker.get('format')
            if format not in ('nvfp4','float8_e4m3fn','float8_e5m2'):
                raise ValueError(f'Unsupported image encoder quantization: {format}')
            original = model.get_submodule(prefix)
            weight = source.get_tensor(prefix+'.weight')
            block = None
            scale_key = prefix+('.weight_scale_2' if format=='nvfp4' else '.weight_scale')
            scale = source.get_tensor(scale_key)
            if format=='nvfp4':
                block = source.get_tensor(prefix+'.weight_scale')
                if tuple(weight.shape)!=(original.out_features,original.in_features//2):
                    raise ValueError(f'Invalid packed encoder dimensions: {prefix}')
                consumed.add(prefix+'.weight_scale')
            elif tuple(weight.shape)!=tuple(original.weight.shape):
                raise ValueError(f'Invalid encoder dimensions: {prefix}')
            pre_key = prefix+'.pre_quant_scale'
            pre = source.get_tensor(pre_key) if pre_key in names else None
            _replace(model,prefix,MixedLinear(original,weight,scale,block,pre))
            consumed.update((key,prefix+'.weight',scale_key))
            consumed.update(k for k in (prefix+'.input_scale',pre_key) if k in names)
        expected = set(model.state_dict())
        # lm_head shares embeddings and is intentionally absent in encoder-only files.
        supplied = names-consumed
        mixed = [n+'.' for n,m in model.named_modules() if isinstance(m,MixedLinear)]
        remaining = {k for k in expected if not any(k.startswith(n) for n in mixed)}
        missing = remaining-supplied-{'lm_head.weight'}; extra = supplied-remaining-{'lm_head.weight'}
        if missing or extra: raise ValueError(f'Encoder checkpoint is incomplete: missing {sorted(missing)[:5]}, extra {sorted(extra)[:5]}.')
        for index,key in enumerate(sorted(supplied)):
            if key=='lm_head.weight': continue
            value = source.get_tensor(key)
            if tuple(value.shape)!=tuple(model.state_dict()[key].shape): raise ValueError(f'Encoder configuration mismatch: {key}')
            set_module_tensor_to_device(model,key,'cpu',value=value,dtype=dtype)
            if index%20==0:callback(f'Loading mixed image encoder · tensor {index+1}/{len(supplied)}')
    model.tie_weights();model.requires_grad_(False)
    if any(p.is_meta for p in model.parameters()):raise ValueError('Image encoder has missing weights.')
    return model
