"""Native video media inventory; timeline checkpoints retain their target times."""
from __future__ import annotations

import copy
import math


def pad_video_conditioning_frames(frames):
    """Keep every input frame; repeat only the tail to reach H3's 17n+5 grid."""
    frames=list(frames)
    if not frames:raise ValueError('Video conditioning has no frames in the selected trim.')
    count=max(5,len(frames));count+=(5-count)%17
    return frames+[frames[-1]]*(count-len(frames))


def h3_checkpoint_frame(frame, output_frames):
    """Nearest real H3 temporal-token position, bounded by the output."""
    frame=max(0,min(output_frames-1,float(frame)))
    base=math.floor(frame/17)*17
    candidates=[base+shift+offset for shift in (-17,0,17) for offset in (0,1,5,9,13)]
    return min((n for n in candidates if 0<=n<output_frames),key=lambda n:(abs(n-frame),n))


def align_conditioning_timeline(conditioning, duration):
    """Use the generated grid for saved, remote and new timeline conditions."""
    from .local_generation import align_h3_frames
    count=align_h3_frames(duration)
    result=copy.deepcopy(conditioning)
    for ref in result:
        if 'timeline_time' not in ref:continue
        time=float(ref['timeline_time'])
        if ref.get('boundary')=='outgoing':
            from .blend_timing import blend_frames
            length=blend_frames(ref.get('duration',0),count/24)
            if length:
                ref.update(timeline_time=(count-length)/24,timeline_start=(count-length)/24,
                           timeline_end=count/24,duration=length/24,blend_frames=length)
            continue
        if ref.get('kind','image')=='image':
            terminal=ref.get('role')=='end' and abs(time-float(duration))<=1/24+1e-6
            ref['timeline_time']=((count-1) if terminal else h3_checkpoint_frame(time*24,count))/24
        else:
            ref['timeline_time']=h3_checkpoint_frame(time*24,count)/24
    return result


def video_references(segments, references, duration):
    result=[];cursor=0.0
    for index,segment in enumerate(segments):
        end=cursor+segment.duration
        if segment.kind in ('image','video'):
            checkpoint=end if segment.kind=='image' and segment.role=='end' else cursor
            if checkpoint>duration+1/24 or segment.kind=='video' and end>duration+1/24:
                raise ValueError(f'Timeline conditioning "{segment.name}" extends beyond the {duration:g}s local video. Adjust its timing or the video duration in Settings.')
            result.append(dict(kind=segment.kind,name=segment.name,path=segment.media_path,role=segment.role,
                timeline_index=index,timeline_time=checkpoint,timeline_start=cursor,timeline_end=end,
                start=max(0,segment.trim_start or 0)/24,duration=segment.duration))
        cursor=end
    for reference in copy.deepcopy(references):
        if reference.get('audio_only'):
            reference['kind']='audio'
            reference['path']=reference.get('audio_path') or reference.get('path')
        result.append(reference)
    return result


def render_conditioning_inputs(conditioning, references, variant):
    """Keep Standard's visual conditioning separate from Ref2VA references."""
    if variant == 'ref2va':
        return copy.deepcopy([*conditioning, *references])
    return [{k: copy.deepcopy(v) for k, v in ref.items() if k != 'audio_path'}
            for ref in conditioning if ref.get('kind', 'image') in ('image', 'video')]


def install_fl2va_video_conditioning(pipe, clips, emit, *, image_conditions=()):
    """Encode timed video segments into FL2VA's existing visual condition lane."""
    if not clips and not image_conditions:
        return
    import torch
    from diffsynth.pipelines.minimax_h3_audio_video import patchify_video

    encoder = next(unit for unit in pipe.units if type(unit).__name__ == 'MiniMaxH3Unit_KeyframeEncoder')
    builder = next(unit for unit in pipe.units if type(unit).__name__ == 'MiniMaxH3Unit_PackedSequenceBuilder')
    encode_images = encoder.process
    build_sequence = builder.process
    condition_times = []

    def encode(pipe, keyframes, keyframe_indices, video_latents, rand_device, seed, height, width):
        result = encode_images(pipe, keyframes, keyframe_indices, video_latents, rand_device, seed, height, width)
        condition_times.clear()
        indices = list(keyframe_indices or [])
        condition_times.extend(None for _ in indices)
        anchors = [result['keyframe_cond_anchor']] if 'keyframe_cond_anchor' in result else []
        for segment, image in image_conditions:
            encoded = encode_images(pipe, [image], [0], video_latents, rand_device, seed, height, width)
            anchors.append(encoded['keyframe_cond_anchor'])
            # Untimed End keeps the model's native last-frame placement. Timed
            # checkpoints use their authored local timestamp, irrespective of role.
            time = segment.get('timeline_time')
            indices.append(-1 if time is None and segment.get('role') == 'end' else 0)
            condition_times.append(None if time is None else float(time) * 24 * builder._FRAME_RESCALE)
            emit('log', message=f"FL2VA image conditioning: {segment.get('name', 'image')} · "
                 f"{time if time is not None else segment.get('role', 'start')}s")
        pipe.load_models_to_device(encoder.onload_model_names)
        for segment, frames in clips:
            padded = pad_video_conditioning_frames(frames)
            tensor = pipe.preprocess_video(padded, torch_dtype=torch.float32, min_value=0, device=pipe.device)
            latents = pipe.video_vae.encode_video(tensor, dtype=pipe.torch_dtype,
                                                  tiled=True, tile_size=128, tile_overlap=64)
            latents = latents.to(device=pipe.device, dtype=pipe.torch_dtype)
            rows = patchify_video(latents)
            if pipe.imgvid_cond_noise_aug != 1.0:
                noise = pipe.generate_noise(latents.shape, seed, rand_device, pipe.torch_dtype)
                amount = pipe.imgvid_cond_noise_aug
                rows = amount * rows + (1.0 - amount) * patchify_video(noise)
            anchors.append(rows)
            times = builder._video_t_grid(int(latents.shape[2]), 0)
            times.clamp_(max=(len(frames)-1)*builder._FRAME_RESCALE)
            times += float(segment.get('timeline_time', 0)) * 24 * builder._FRAME_RESCALE
            condition_times.extend(times.tolist())
            indices.extend([0] * len(times))
            emit('log', message=f"FL2VA video conditioning: {segment.get('name', 'video')} · "
                 f"{len(frames)} frames at {segment.get('timeline_time', 0):g}s")
        result['keyframe_cond_anchor'] = torch.cat(anchors, dim=0).contiguous()
        result['keyframe_indices'] = indices
        return result

    def pack(pipe, prompt_embeds, video_latents, audio_latents, **kwargs):
        result = build_sequence(pipe, prompt_embeds, video_latents, audio_latents, **kwargs)
        packed = result['packed']
        rows_per_frame = (video_latents.shape[3]//2) * (video_latents.shape[4]//2)
        for index, time in enumerate(condition_times):
            if time is not None:
                positions = packed['img_pos'][index*rows_per_frame:(index+1)*rows_per_frame]
                packed['img_position_ids'][0, positions, 0] = prompt_embeds.shape[0] + time
        return result

    encoder.process = encode
    builder.process = pack


def configure_video_conditioning_encoder(pipe, references):
    if not any(ref.get('kind')=='video' for ref in references):return
    original=pipe.video_vae.encode_video
    def encode(video,*args,**kwargs):
        if not kwargs.get('process_image',False):
            kwargs.update(tiled=True,tile_size=128,tile_overlap=64)
        return original(video,*args,**kwargs)
    pipe.video_vae.encode_video=encode
    encoder=next(unit for unit in pipe.units if type(unit).__name__=='MiniMaxH3Unit_ReferenceEncoder')
    encode_ref=encoder._encode_video_ref
    def encode_reference(pipe,frames,target_frame_count,short_edge,max_pixels):
        padded=pad_video_conditioning_frames(frames)
        result=encode_ref(pipe,padded,max(target_frame_count,len(padded)),short_edge,max_pixels)
        # The VAE sees the padded full clip. Vision/text preparation sees only
        # real frames, and timed guide positions can clamp the artificial tail.
        return (*result[:4],result[4][:len(frames)])
    encoder._encode_video_ref=encode_reference
    def trim(pipe,count):
        if count<5 or count%17!=5:raise ValueError('Video conditioning must be padded to the H3 frame grid.')
        return count
    encoder._trim_reference_video_length=trim


def reference_token_budget(references,width,height,output_frames):
    """Conservative reference allowance, including full video temporal rows."""
    scale=min(1,480/min(width,height))
    rows=max(1,round(width*scale/32))*max(1,round(height*scale/32))
    total=0
    for ref in references:
        tokens=4096
        if ref.get('kind')=='video':
            frames=max(5,math.ceil(min(15,float(ref.get('duration',15)))*24))
            if 'timeline_time' in ref:frames=min(output_frames,frames)
            temporal=math.ceil((frames-5)/17)*5+2
            tokens=max(tokens,temporal*rows)
        total+=tokens
    return total


def install_timeline_positions(pipe, references, emit):
    """Position Ref2VA timeline anchors on the target's existing rotary grid.

    Reuse the encoded reference rows: no duplicate VAE encode or condition rows.
    Untimed reference-panel inputs keep their original positions and ordering.
    """
    if not any('timeline_time' in ref for ref in references):return
    builder=next(unit for unit in pipe.units if type(unit).__name__=='MiniMaxH3Unit_PackedSequenceBuilder')
    original=builder.process
    def process(pipe,prompt_embeds,video_latents,audio_latents,**kwargs):
        result=original(pipe,prompt_embeds,video_latents,audio_latents,**kwargs)
        blocks=kwargs.get('ref_blocks')
        if not blocks:return result
        if len(blocks)!=len(references):raise ValueError('H3 conditioning inventory changed while encoding.')
        packed=result['packed'];positions=packed['img_position_ids']
        target_rows=video_latents.shape[2]*(video_latents.shape[3]//2)*(video_latents.shape[4]//2)
        origin=positions[0,packed['img_pos'][-target_rows],0].clone()
        visual_cursor=0
        for ref,block in zip(references,blocks):
            if block['kind'] not in ('image','video','video_audio'):continue
            temporal=int(block['latent_t']);frame_rows=(int(block['latent_h'])//2)*(int(block['latent_w'])//2)
            count=temporal*frame_rows
            if 'timeline_time' in ref:
                indices=packed['img_pos'][visual_cursor:visual_cursor+count]
                frame_index=round(float(ref['timeline_time'])*24)
                times=builder._video_t_grid(temporal,0).to(positions)
                if block['kind'] in ('video','video_audio') and block.get('prepared_frames'):
                    times.clamp_(max=(len(block['prepared_frames'])-1)*builder._FRAME_RESCALE)
                positions[0,indices,0]=origin+frame_index*builder._FRAME_RESCALE+times.repeat_interleave(frame_rows)
                emit('log',message=f'Timeline conditioning: {ref.get("name", "media")} · frame {frame_index} · {count} encoded visual rows')
            visual_cursor+=count
        return result
    builder.process=process


def install_ref2va_conditioning(pipe, clips, emit, *, image_conditions=(), audio_conditions=()):
    """Condition the generated AV timeline through H3's native latent masks.

    These inputs never enter ReferenceEncoder/ref_blocks. Reference-panel
    anchors and token packing remain independent of the generated timeline.
    """
    if not clips and not image_conditions and not audio_conditions:return
    import torch
    builder=next(unit for unit in pipe.units if type(unit).__name__=='MiniMaxH3Unit_PackedSequenceBuilder')
    video_unit=next(unit for unit in pipe.units if type(unit).__name__=='MiniMaxH3Unit_VideoRetakeEmbedder')
    audio_unit=next(unit for unit in pipe.units if type(unit).__name__=='MiniMaxH3Unit_AudioRetakeEmbedder')
    original_video,original_audio=video_unit.process,audio_unit.process

    def video(pipe,retake_video,frame_regions_to_retake,video_latents,height,width,num_frames,tiled,tile_size,tile_overlap):
        result=original_video(pipe,retake_video,frame_regions_to_retake,video_latents,height,width,num_frames,tiled,tile_size,tile_overlap)
        if not clips and not image_conditions:return result
        anchor=result.get('input_latents_video',torch.zeros_like(video_latents))
        mask=result.get('denoise_mask_video',torch.ones_like(video_latents))
        times=builder._video_t_grid(video_latents.shape[2],0)/builder._FRAME_RESCALE/24
        pipe.load_models_to_device(video_unit.onload_model_names)
        for ref,frames in [*clips,*[(ref,[image]) for ref,image in image_conditions]]:
            is_image=ref.get('kind')=='image'
            if is_image:
                tensor=pipe.preprocess_image(frames[0],torch_dtype=torch.float32,min_value=0)
            else:
                source_frames=pad_video_conditioning_frames(frames)
                tensor=pipe.preprocess_video(source_frames,torch_dtype=torch.float32,min_value=0,device=pipe.device)
            encoded=pipe.video_vae.encode_video(tensor,dtype=pipe.torch_dtype,**({'process_image':True} if is_image else dict(tiled=True,tile_size=128,tile_overlap=64))).to(video_latents)
            start=float(ref.get('timeline_time',0));length=float(ref.get('duration',len(frames)/24))
            if ref.get('kind')=='image':
                indices=torch.tensor([int(torch.argmin(abs(times-start)))],dtype=torch.long)
            else:indices=torch.where((times>=start-1e-7)&(times<start+length-1e-7))[0]
            source_times=builder._video_t_grid(encoded.shape[2],0)/builder._FRAME_RESCALE/24
            for target in indices.tolist():
                source=int(torch.argmin(abs(source_times-max(0,float(times[target])-start))))
                anchor[:,:,target]=encoded[:,:,source]
                mask[:,:,target]=0
            emit('log',message=f'Conditional video latents: {ref.get("name","timeline")} · {len(indices)} temporal tokens')
            del tensor,encoded
        result.update(input_latents_video=anchor,denoise_mask_video=mask)
        return result

    def audio(pipe,retake_audio,seconds_regions_to_retake,audio_latents):
        result=original_audio(pipe,retake_audio,seconds_regions_to_retake,audio_latents)
        if not audio_conditions:return result
        from diffsynth.utils.data.audio import convert_to_stereo,resample_waveform
        anchor=result.get('input_latents_audio',torch.zeros_like(audio_latents))
        mask=result.get('denoise_mask_audio',torch.ones_like(audio_latents))
        fps=pipe.audio_vae.sample_rate/pipe.audio_vae.hop_length
        pipe.load_models_to_device(audio_unit.onload_model_names)
        for ref,waveform,rate in audio_conditions:
            waveform=resample_waveform(convert_to_stereo(waveform).float(),rate,pipe.audio_vae.sample_rate)
            encoded=pipe.audio_vae.encode_audio(waveform[:2].to(pipe.device),dtype=pipe.torch_dtype).to(audio_latents)
            start=max(0,round(float(ref.get('timeline_time',0))*fps))
            count=min(encoded.shape[-1],max(0,math.ceil(float(ref.get('duration',waveform.shape[-1]/pipe.audio_vae.sample_rate))*fps)),audio_latents.shape[-1]-start)
            if count>0:
                anchor[...,start:start+count]=encoded[...,:count];mask[...,start:start+count]=0
            emit('log',message=f'Conditional audio latents: {ref.get("name","timeline")} · {max(0,count)} temporal tokens')
            del waveform,encoded
        result.update(input_latents_audio=anchor,denoise_mask_audio=mask)
        return result
    video_unit.process=video;audio_unit.process=audio
