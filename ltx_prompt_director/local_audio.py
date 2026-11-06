"""Audio inference inside disposable workers; never import model runtimes in Qt."""
from pathlib import Path
import os
import sys
from .local_protocol import emit, progress
from .local_model_cache import model as cached_model


def generate_audio(request):
    import torch
    with torch.inference_mode():
        _generate_audio(request)


def _generate_audio(request):
    kind = request['audio_kind']; config = request['audio_config']; output = Path(request['job_dir']) / 'audio.wav'
    progress('Loading ' + {'dialog': 'IndexTTS 2.5', 'foley': 'Stable Audio 3', 'music': 'MiniMax Music 3'}[kind] + '…')
    if kind == 'dialog':
        from .local_dialogue_controls import inference_options
        options = inference_options(request)
        root = Path(config['source']).expanduser()
        if str(root) not in sys.path:sys.path.insert(0,str(root))
        os.chdir(root)
        from indextts.infer_v2_5 import IndexTTS2
        model_dir = str(Path(config['model']).expanduser())
        tts = cached_model(request,lambda:IndexTTS2(cfg_path=str(Path(model_dir) / 'config.yaml'), model_dir=model_dir,
                        use_bf16=request['profile'].get('device') != 'cpu',
                        device=request['profile'].get('device', 'cuda')))
        progress('Sampling dialogue…')
        try:
            tts.infer(spk_audio_prompt=request['sample'],text=request['prompt'],
                      lang=request.get('language','EN'),output_path=str(output),verbose=False,**options)
        finally:
            # Retain weights, not a preceding job's voice/emotion conditioning.
            for name in ('cache_spk_cond','cache_spk_audio_prompt','cache_emo_cond','cache_emo_audio_prompt',
                         'cache_s2mel_style','cache_s2mel_prompt','cache_spk_mel'):
                if hasattr(tts,name):setattr(tts,name,None)
    elif kind == 'foley':
        from stable_audio_3 import StableAudioModel
        import soundfile as sf
        source = config.get('source', '')
        if source:
            os.environ['HF_HOME'] = str(Path(source) / '.cache/huggingface')
        model = cached_model(request,lambda:StableAudioModel.from_pretrained(config.get('model','small-sfx'),
                                                 device=request['profile'].get('device','cuda')))
        progress('Sampling sound effects…')
        audio = model.generate(prompt=request['prompt'], duration=request['duration'], seed=request['seed'],
                               steps=request.get('steps', 8))
        sf.write(str(output), audio[0].detach().float().cpu().transpose(0, 1).numpy(), model.model.sample_rate, subtype='PCM_16')
    elif kind == 'music':
        import torch
        root = Path(config['source']).expanduser()
        if str(root) not in sys.path:sys.path.insert(0,str(root))
        os.chdir(root)
        sys.argv = ['director-audio', '--disable-auto-launch', '--disable-all-custom-nodes']
        if request['profile'].get('device') == 'cpu':
            sys.argv.append('--cpu')
        import comfy.options
        comfy.options.enable_args_parsing()
        import comfy.sd
        import comfy.utils
        import comfy.model_management
        import folder_paths
        import nodes
        from comfy_extras.nodes_minimax_music import MiniMaxMusic3TextEncode, EmptyMiniMaxMusic3LatentAudio
        from comfy_extras.nodes_audio import vae_decode_audio
        import soundfile as sf
        def load_music():
            model=comfy.sd.load_diffusion_model(config['model'])
            clip=comfy.sd.load_clip([config['encoder']],clip_type=comfy.sd.CLIPType.MINIMAX,
                                   embedding_directory=folder_paths.get_folder_paths('embeddings'))
            vae=comfy.sd.VAE(sd=comfy.utils.load_torch_file(config['vae']))
            return model,clip,vae
        model,clip,vae=cached_model(request,load_music)
        progress('Composing music and vocals…')
        positive, duration = MiniMaxMusic3TextEncode.execute(clip, request['prompt'], request.get('lyrics', ''),
                                        request['seed'], request['duration'], 1.7, 50)
        latent = EmptyMiniMaxMusic3LatentAudio.execute(duration, 1)[0]
        negative = nodes.ConditioningZeroOut().zero_out(positive)[0]
        progress('Sampling music audio…')
        samples = nodes.common_ksampler(model, request['seed'], request.get('steps', 30), 1.7,
                                        'euler', 'simple', positive, negative, latent)[0]
        audio = vae_decode_audio(vae, samples, tile=1536, overlap=64)
        sf.write(str(output), audio['waveform'][0].detach().float().cpu().transpose(0, 1).numpy(),
                 audio['sample_rate'], subtype='PCM_16')
    else:
        raise ValueError('Unknown audio generator.')
    if not output.is_file() or output.stat().st_size < 44:
        raise RuntimeError('The audio model did not produce a playable WAV file.')
    emit('result', path=str(output))
