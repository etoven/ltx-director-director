"""CPU checks for real mixed tensor formats; optional private inference runtime."""
import os
import pytest

pytestmark=pytest.mark.skipif(os.environ.get('DIRECTOR_TEST_ML')!='1',reason='Private ML runtime CPU test')


def test_mixed_qwen_load_is_strict_and_matches_dequantized_forward(tmp_path):
    import json
    import torch
    from safetensors.torch import save_file
    from transformers import Qwen3Config,Qwen3ForCausalLM
    from comfy_kitchen.tensor import get_layout_class
    from ltx_prompt_director.local_image_quant import load_mixed_qwen,MixedLinear
    config=Qwen3Config(vocab_size=64,hidden_size=64,intermediate_size=128,num_hidden_layers=2,
        num_attention_heads=4,num_key_value_heads=2,head_dim=16,tie_word_embeddings=True)
    config.save_pretrained(tmp_path)
    reference=Qwen3ForCausalLM(config).to(torch.bfloat16).eval()
    state={k:v.detach().clone() for k,v in reference.state_dict().items() if k!='lm_head.weight'}
    keys=['model.layers.0.self_attn.q_proj','model.layers.1.mlp.down_proj']
    for i,prefix in enumerate(keys):
        key=prefix+'.weight';weight=state[key]
        if i==0:
            scale=torch.tensor(.003);packed=(weight/scale).to(torch.float8_e4m3fn)
            state[key]=packed;state[prefix+'.weight_scale']=scale
            decoded=packed.to(torch.bfloat16)*scale.to(torch.bfloat16)
            format='float8_e4m3fn'
        else:
            layout=get_layout_class('TensorCoreNVFP4Layout');packed,params=layout.quantize(weight)
            state[key]=packed;state[prefix+'.weight_scale']=params.block_scale
            state[prefix+'.weight_scale_2']=params.scale
            decoded=layout.dequantize(packed,params)
            format='nvfp4'
        reference.get_submodule(prefix).weight.data.copy_(decoded)
        state[prefix+'.comfy_quant']=torch.tensor(list(json.dumps({'format':format}).encode()),dtype=torch.uint8)
    source=tmp_path/'mixed.safetensors';save_file(state,str(source))
    before=source.read_bytes()
    loaded=load_mixed_qwen(source,tmp_path,torch.bfloat16).eval()
    assert isinstance(loaded.get_submodule(keys[1]),MixedLinear)
    ids=torch.tensor([[1,2,3,4]])
    with torch.no_grad():
        assert torch.allclose(loaded(ids).logits,reference(ids).logits,atol=.002,rtol=.01)
    assert source.read_bytes()==before
    state.pop('model.layers.0.input_layernorm.weight');save_file(state,str(source))
    with pytest.raises(ValueError,match='incomplete'):load_mixed_qwen(source,tmp_path,torch.bfloat16)


def test_scaled_transformer_assignment_preserves_storage_and_dtype():
    import torch
    from accelerate import init_empty_weights
    from ltx_prompt_director.local_image_quant import assign_weights,MixedLinear
    with init_empty_weights():model=torch.nn.Sequential(torch.nn.Linear(16,16,bias=False))
    weights=torch.ones(16,16,dtype=torch.float8_e4m3fn);scale=torch.tensor(.25)
    assign_weights(model,{'0.weight':weights},{weights.untyped_storage().data_ptr():scale})
    assert isinstance(model[0],MixedLinear) and model[0].weight.dtype==torch.float8_e4m3fn
    assert next(model.parameters()).dtype==torch.bfloat16
    with torch.no_grad():assert torch.equal(model(torch.ones(1,16,dtype=torch.bfloat16)),torch.full((1,16),4.,dtype=torch.bfloat16))
