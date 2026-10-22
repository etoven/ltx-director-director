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
