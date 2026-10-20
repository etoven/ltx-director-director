"""Native H3 checkpoint, quantization and convolution compatibility adapters.

Uses comfy-kitchen's tensor format only; no ComfyUI process or source is loaded.
On pre-Blackwell GPUs the packed matrix is dequantized per layer for BF16 GEMM.
"""
import json
from dataclasses import dataclass
import torch
from diffsynth.core.quant.base import BackendConfig, QuantBackend, register_quant_backend
from diffsynth.core.quant.config import register_quant_method
from diffsynth.models.minimax_h3_text_encoder import MiniMaxH3TextEncoder
from diffsynth.utils.state_dict_converters.minimax_h3_text_encoder import MiniMaxH3TextEncoderComfyOrgStateDictConverter


class ScaledEmbedding(torch.nn.Embedding):
    def __init__(self, count, width):
        super().__init__(count, width)
        # Parameters, rather than buffers, participate in DiffSynth's disk map.
        self.weight_scale = torch.nn.Parameter(torch.ones(count, 1), requires_grad=False)

    def forward(self, ids):
        return super().forward(ids) * torch.nn.functional.embedding(ids, self.weight_scale).to(self.weight.dtype)


class H3NVFP4Encoder(MiniMaxH3TextEncoder):
    def __init__(self):
        super().__init__()
        old = self.model.language_model.embed_tokens
        self.model.language_model.embed_tokens = ScaledEmbedding(old.num_embeddings, old.embedding_dim)


def encoder_keys(state):
    return {key: value for key, value in MiniMaxH3TextEncoderComfyOrgStateDictConverter(state).items()
            if key != 'model.language_model.embed_tokens.comfy_quant'}


class NVFP4Linear(torch.nn.Linear):
    def __init__(self, original, dtype):
        with torch.device('meta'):
            super().__init__(original.in_features, original.out_features, original.bias is not None, dtype=dtype)
        self.weight.requires_grad_(False)
        if self.bias is not None: self.bias.requires_grad_(False)
        self.register_buffer('pre_quant_scale', None)

    def _load_from_state_dict(self, state, prefix, *args):
        scale = state.pop(prefix + 'pre_quant_scale', None)
        super()._load_from_state_dict(state, prefix, *args)
        if scale is not None: self.pre_quant_scale = scale

    def forward(self, x):
        if self.pre_quant_scale is not None: x = x * self.pre_quant_scale.to(x)
        # Explicit BF16 GEMM also works on Ampere; NVFP4 tensor cores are not required.
        weight = self.weight.dequantize().to(dtype=x.dtype, device=x.device)
        return torch.nn.functional.linear(x, weight, self.bias)


@dataclass
class NVFP4Config(BackendConfig):
    orig_dtype: torch.dtype = torch.bfloat16


@register_quant_backend('director_h3_nvfp4')
class H3NVFP4Backend(QuantBackend):
    def validate_environment(self):
        import comfy_kitchen as ck
        from comfy_kitchen.tensor import get_layout_class
        get_layout_class('TensorCoreNVFP4Layout')
        if torch.version.cuda is None or int(torch.version.cuda.split('.')[0]) < 13:
            ck.registry.disable('cuda')

    def capabilities(self): return dict(is_serializable=True, is_differentiable=False)
    def quantized_linear_classes(self): return (NVFP4Linear,)
    def create_quantized_linear_shell(self, linear, compute_dtype): return NVFP4Linear(linear, compute_dtype)

    def unflatten_state_dict(self, state, metadata):
        from comfy_kitchen.tensor import QuantizedTensor, get_layout_class
        result = dict(state)
        for key in list(state):
            if not key.endswith('.comfy_quant'): continue
            prefix = key.removesuffix('comfy_quant')
            marker = json.loads(bytes(result.pop(key).to(torch.uint8).tolist()).decode())
            if marker.get('format') != 'nvfp4': raise ValueError(f'Unexpected H3 layer format: {marker.get("format")}')
            weight = result[prefix + 'weight']
            params = get_layout_class('TensorCoreNVFP4Layout').Params(
                scale=result.pop(prefix + 'weight_scale_2').float(),
                block_scale=result.pop(prefix + 'weight_scale').view(torch.float8_e4m3fn),
                orig_dtype=self.config.orig_dtype, orig_shape=(weight.shape[0], weight.shape[1] * 2))
            result.pop(prefix + 'input_scale', None)
            result[prefix + 'weight'] = QuantizedTensor(weight, 'TensorCoreNVFP4Layout', params)
        return result


register_quant_method('director_h3_nvfp4', 'director_h3_nvfp4', NVFP4Config.from_kwargs)


def register_encoder(path):
    """Register only a structurally matching checkpoint; filenames are not proof."""
    from safetensors import safe_open
    from diffsynth.core.loader import hash_model_file
    from diffsynth.configs import MODEL_CONFIGS, VRAM_MANAGEMENT_MODULE_MAPS
    with safe_open(str(path), framework='pt') as source:
        names = set(source.keys())
        if 'model.layers.0.self_attn.q_proj.weight_scale_2' not in names: return False
        required = {'model.layers.49.self_attn.q_proj.comfy_quant', 'model.embed_tokens.weight_scale'}
        if not required <= names: raise ValueError('Incomplete H3 NVFP4 encoder checkpoint.')
    original = 'diffsynth.models.minimax_h3_text_encoder.MiniMaxH3TextEncoder'
    custom = __name__ + '.H3NVFP4Encoder'
    VRAM_MANAGEMENT_MODULE_MAPS[custom] = dict(VRAM_MANAGEMENT_MODULE_MAPS[original])
    MODEL_CONFIGS.append(dict(model_hash=hash_model_file(str(path)), model_name='minimax_h3_text_encoder',
        model_class=custom, state_dict_converter=__name__ + '.encoder_keys',
        quant_config=dict(method='director_h3_nvfp4', load_prequantized=True,
                          target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj'])))
    return True


def video_vae_keys(state):
    from diffsynth.utils.state_dict_converters.minimax_h3_video_vae import MiniMaxH3VideoVAEStateDictConverter
    return MiniMaxH3VideoVAEStateDictConverter({k: v for k, v in state.items() if k not in ('latents_mean', 'latents_std')})


def audio_vae_keys(state):
    from diffsynth.utils.state_dict_converters.minimax_h3_audio_vae import MiniMaxH3AudioVAEStateDictConverter
    return MiniMaxH3AudioVAEStateDictConverter({k: v for k, v in state.items() if k not in ('latents_mean', 'latents_std')})


from diffsynth.models.minimax_h3_audio_vae import MiniMaxH3AudioVAE, WeightNormedConv1d, WeightNormedConvTranspose1d


class H3PackagedAudioVAE(MiniMaxH3AudioVAE):
    """The packaged VAE stores already-normalized convolution weights."""
    def __init__(self):
        super().__init__()
        def replace(parent):
            for name, child in list(parent.named_children()):
                if isinstance(child, (WeightNormedConv1d, WeightNormedConvTranspose1d)):
                    shape = child.weight_v.shape
                    if isinstance(child, WeightNormedConvTranspose1d):
                        layer = torch.nn.ConvTranspose1d(shape[0], shape[1] * child.groups, shape[2],
                            stride=child.stride, padding=child.padding, output_padding=child.output_padding,
                            groups=child.groups, bias=child.bias is not None, dilation=child.dilation)
                    else:
                        layer = torch.nn.Conv1d(shape[1] * child.groups, shape[0], shape[2],
                            stride=child.stride, padding=child.padding, dilation=child.dilation,
                            groups=child.groups, bias=child.bias is not None, padding_mode='zeros')
                    setattr(parent, name, layer)
                else: replace(child)
        replace(self)


def register_packaged_vaes():
    from diffsynth.configs import MODEL_CONFIGS, VRAM_MANAGEMENT_MODULE_MAPS
    original = 'diffsynth.models.minimax_h3_audio_vae.MiniMaxH3AudioVAE'
    custom = __name__ + '.H3PackagedAudioVAE'
    VRAM_MANAGEMENT_MODULE_MAPS[custom] = dict(VRAM_MANAGEMENT_MODULE_MAPS[original])
    VRAM_MANAGEMENT_MODULE_MAPS[custom]['torch.nn.ConvTranspose1d'] = 'diffsynth.core.vram.layers.AutoWrappedModule'
    # Hashes describe keys and shapes, independent of dtype or filename.
    records = [dict(model_hash='e2b67d8eefc99c75ae97a27006ac3ebc', model_name='minimax_h3_video_vae',
                    model_class='diffsynth.models.minimax_h3_video_vae.MiniMaxH3VideoVAE',
                    state_dict_converter=__name__ + '.video_vae_keys'),
               dict(model_hash='462f6c5f9781bf9e47016dbaf46ac0fb', model_name='minimax_h3_audio_vae', model_class=custom,
                    state_dict_converter=__name__ + '.audio_vae_keys')]
    for record in records:
        if not any(item['model_hash'] == record['model_hash'] for item in MODEL_CONFIGS): MODEL_CONFIGS.append(record)


class LinearPatchProjection(torch.nn.Conv3d):
    """A patch-sized 3D convolution is exactly a flattened linear projection."""
    def forward(self, x):
        if tuple(x.shape[-3:]) == tuple(self.kernel_size) and self.groups == 1 and not any(self.padding) and all(d == 1 for d in self.dilation):
            return torch.nn.functional.linear(x.flatten(1), self.weight.flatten(1), self.bias)[:, :, None, None, None]
        return super().forward(x)


from diffsynth.models.minimax_h3_video_vae import BaseConv3d


class ImageSpatialConv(BaseConv3d):
    """Evaluate a single-frame temporal convolution as the equivalent 2D kernel."""
    def forward(self, x):
        if x.shape[2] != 1 or self.dilation[0] != 1 or (not any(self.padding) and self.kernel_size[0] != 1):
            return super().forward(x)
        if self.padding[2] or self.padding[1]:
            x = torch.nn.functional.pad(x, (self.padding[2], self.padding[2], self.padding[1], self.padding[1], 0, 0), mode=self.pad_mode)
        # BaseConv3d pads a single frame with zeros on the left, or repeats it.
        if self.pad_mode_t == 'constant':
            weight = self.weight[:, :, -1]; image = x[:, :, 0]
        else:
            image = x.expand(-1, -1, self.kernel_size[0], -1, -1).reshape(x.shape[0], -1, *x.shape[-2:])
            weight = self.weight.reshape(self.out_channels, -1, *self.kernel_size[1:])
        return torch.nn.functional.conv2d(image, weight, self.bias, stride=self.stride[1:],
            dilation=self.dilation[1:], groups=self.groups).unsqueeze(2)


def optimize_h3_convolutions(pipe):
    patch = pipe.text_encoder.model.visual.patch_embed
    patch = getattr(patch, 'module', patch)
    patch.proj.__class__ = LinearPatchProjection
    for module in pipe.video_vae.modules():
        if isinstance(module, BaseConv3d): module.__class__ = ImageSpatialConv


def video_pixel_chunks(transform, pixels, frames=8):
    """Consume the private decoder buffer without full-clip normalization copies."""
    if pixels.ndim != 5 or pixels.shape[2] <= frames: return transform(pixels)
    for start in range(0, pixels.shape[2], frames):
        part = pixels[:, :, start:start + frames]
        part.copy_(transform(part))
    return pixels


def stream_h3_video_pixels(pipe):
    reverse = pipe.video_vae.processor.revert_tensor
    pipe.video_vae.processor.revert_tensor = lambda pixels: video_pixel_chunks(reverse, pixels)


def use_cublas_int8():
    """Bound projection buffers, using native CUDA 13 or PyTorch/cuBLAS on 12."""
    if torch.version.cuda is None: return
    native = int(torch.version.cuda.split('.')[0]) >= 13
    rows = 2048 if native else 512
    from diffsynth.core.quant.backends.comfy_kitchen import FORMATS, ComfyKitchenLinear
    import comfy_kitchen as ck
    class CuBlasInt8Linear(ComfyKitchenLinear):
        def forward(self, x):
            flat = x.reshape(-1, x.shape[-1])
            with ck.use_backend('cuda' if native else 'eager'):
                if flat.shape[0] <= rows: return super().forward(x)
                output = torch.empty((flat.shape[0], self.out_features), dtype=x.dtype, device=x.device)
                for start in range(0, flat.shape[0], rows):
                    output[start:start + rows] = super().forward(flat[start:start + rows])
                return output.reshape(*x.shape[:-1], self.out_features)
    FORMATS['int8_tensorwise'].linear_cls = CuBlasInt8Linear


from diffsynth.models.minimax_h3_dit import MiniMaxH3MLP


class ChunkedH3MLP(MiniMaxH3MLP):
    def forward(self, x):
        flat = x.reshape(-1, x.shape[-1])
        rows = 2048 if torch.version.cuda and int(torch.version.cuda.split('.')[0]) >= 13 else 512
        if flat.shape[0] <= rows: return super().forward(x)
        staged = []
        try:
            # Hold this MLP's two matrices for its row batches, then restore the
            # offload state. Otherwise disk offload would reread them per batch.
            for layer in (self.fc1, self.fc2):
                if hasattr(layer, 'preparing') and layer.state != 2:
                    staged.append((layer, layer.state)); layer.preparing()
            output = torch.empty_like(flat)
            for start in range(0, flat.shape[0], rows):
                output[start:start + rows] = super().forward(flat[start:start + rows])
            return output.reshape_as(x)
        finally:
            for layer, state in staged:
                layer.offload()
                if state == 1: layer.onload()


def stream_h3_feed_forward(pipe):
    for module in pipe.dit.modules():
        if isinstance(module, MiniMaxH3MLP): module.__class__ = ChunkedH3MLP
