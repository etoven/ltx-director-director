"""Blend anchoring and backward fallback in actual Generate/Refine transport."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import base64,copy,io,json
import av,numpy as np
import pytest
from PIL import Image
from ltx_prompt_director import ai
from ltx_prompt_director.models import Segment
from ltx_prompt_director.group_prompt_context import snapshot,fingerprint,augment,GROUP_PROMPT_CONTEXT
from ltx_prompt_director.media import data_url


def owner_fixture(tmp_path):
    video=tmp_path/'preceding.mp4'
    with av.open(str(video),'w') as media:
        stream=media.add_stream('libx264',rate=24);stream.width=128;stream.height=96;stream.pix_fmt='yuv420p'
        for index in range(96):
            pixels=np.full((96,128,3),(0 if index<48 else 180,90,30),np.uint8)
            for packet in stream.encode(av.VideoFrame.from_ndarray(pixels,format='rgb24')):media.mux(packet)
        for packet in stream.encode():media.mux(packet)
    root=Segment('Root','','','text','text','Root initial appearance and action.',4,render_group=0)
    middle=Segment('Middle','','','text','text','Intervening authored transformation.',4,render_group=1)
    target=Segment('Target','','','text','text','Continue the authored motion.',4,render_group=2)
    ref=tmp_path/'reference.png';Image.new('RGB',(16,16),(20,50,210)).save(ref)
    options={'0':dict(clip=dict(path=str(video),duration=4,trim_start=0),context=dict(minimaxH3=dict(referenceImages=[dict(image=data_url(str(ref)),role='identity',name='Root identity',notes='Preserve this appearance.')])))}
    groups={0:[root],1:[middle],2:[target]}
    return SimpleNamespace(prompt_only_workspace=False,workspace_engine='minimax_frames',video_extensions=2,
        _active_render_group=2,minimax_reference_images=[None]*4,render_group_options=options,
        render_groups=lambda:groups,extension_transition=lambda group:dict(blend='perceptual_crossfade',overlap=22/24)),groups,video


def pixel(encoded):return Image.open(io.BytesIO(base64.b64decode(encoded.split(',',1)[1]))).convert('RGB').getpixel((0,0))


def test_backward_search_prefers_closest_visual_then_root_prompt_and_saved_references(tmp_path):
    owner,groups,video=owner_fixture(tmp_path);original=[s.to_dict() for items in groups.values() for s in items]
    records=snapshot(owner,[2]);token=GROUP_PROMPT_CONTEXT.set(records)
    try:
        inputs,rules=augment([], 'Authoring rules','openai',ai._mixed_reference_input)
        anchor=next(item for item in inputs if item.get('group_start_anchor'))
        assert anchor['source_group']==0 and anchor['source_time']==pytest.approx(4-22/24)
        assert pixel(anchor['image'])[0]>150 and 'Intervening authored transformation.' in rules
        assert 'prompt text itself' in rules and 'local time zero' in rules
    finally:GROUP_PROMPT_CONTEXT.reset(token)
    green=tmp_path/'nearer.png';Image.new('RGB',(16,16),(10,180,20)).save(green)
    groups[1].append(Segment('Nearby',str(green),str(green),'image','end','A later known state.',1,render_group=1))
    token=GROUP_PROMPT_CONTEXT.set(snapshot(owner,[2]))
    try:
        inputs,_=augment([],'Rules','gemini',ai._mixed_reference_input)
        assert inputs[0]['source_group']==1 and pixel(inputs[0]['image'])[1]>150
    finally:GROUP_PROMPT_CONTEXT.reset(token)
    groups[1].pop();video.unlink()
    token=GROUP_PROMPT_CONTEXT.set(snapshot(owner,[2]))
    try:
        inputs,rules=augment([],'Rules','openai',ai._mixed_reference_input)
        assert not any(item.get('group_start_anchor') for item in inputs)
        assert 'Root initial appearance and action.' in rules
        fallback=next(item for item in inputs if item.get('group_continuity_fallback'))
        assert pixel(fallback['image'])[2]>180 and 'Preserve this appearance.' in json.dumps(fallback)
    finally:GROUP_PROMPT_CONTEXT.reset(token)
    assert [s.to_dict() for items in groups.values() for s in items]==original


@pytest.mark.parametrize('provider',['gemini','openai','chatgpt'])
@pytest.mark.parametrize('operation',['generate','refine'])
def test_generate_and_refine_send_blend_image_and_explicit_first_text_instruction(tmp_path,monkeypatch,provider,operation):
    owner,groups,video=owner_fixture(tmp_path);request=[]
    monkeypatch.setattr(ai,'streaming_enabled',lambda:False)
    output=json.dumps(dict(prompt='The subject begins in the visible established state and continues its authored movement.'))
    def post(url,**kwargs):
        request.append(kwargs['json'])
        data=({'output':[dict(type='message',content=[dict(type='output_text',text=output)])]}
              if '/responses' in url else {'candidates':[dict(content=dict(parts=[dict(text=output)]))]})
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:data)
    monkeypatch.setattr(ai.requests,'post',post)
    def plan_stream(key,payload,timeout):request.append(payload);return output
    monkeypatch.setattr(ai,'_chatgpt_stream',plan_stream)
    token=GROUP_PROMPT_CONTEXT.set(snapshot(owner,[2]))
    try:
        if operation=='generate':ai.build_minimax_h3_prompt(groups[2],provider,'fixture','fixture','Continue','',False,False,False,10)
        else:ai.refine_minimax_h3_prompt(groups[2],provider,'fixture','fixture','Continue','',False,False,False,'Current authored draft.','Refine this draft.',10)
    finally:GROUP_PROMPT_CONTEXT.reset(token)
    assert len(request)==1
    payload=json.dumps(request[0],ensure_ascii=False)
    assert 'GROUP STARTING AND ENDING CONTINUITY' in payload and 'prompt text itself' in payload
    assert 'Incoming continuity frame' in payload and ('inline_data' in payload if provider=='gemini' else 'input_image' in payload)
    assert groups[2][0].prompt=='Continue the authored motion.'


def test_context_fingerprint_changes_for_replaced_anchor_and_root_fallback(tmp_path):
    owner,groups,video=owner_fixture(tmp_path);old=fingerprint(snapshot(owner,[2]))
    owner.render_group_options['0']['attachmentRevision']=1
    assert fingerprint(snapshot(owner,[2]))!=old
    old=fingerprint(snapshot(owner,[2]));groups[0][0].prompt='Newer source wording.'
    assert fingerprint(snapshot(owner,[2]))!=old


def test_existing_pair_uses_sequence_compositor_for_first_blend_frame(tmp_path,monkeypatch):
    from ltx_prompt_director import shot_sequence
    owner,groups,source=owner_fixture(tmp_path)
    destination=tmp_path/'destination.mp4'
    with av.open(str(destination),'w') as movie:
        stream=movie.add_stream('libx264',rate=24);stream.width=128;stream.height=96;stream.pix_fmt='yuv420p'
        for index in range(96):
            for packet in stream.encode(av.VideoFrame.from_ndarray(np.full((96,128,3),(20,190,60),np.uint8),format='rgb24')):movie.mux(packet)
        for packet in stream.encode():movie.mux(packet)
    owner.render_group_options['1']=dict(clip=dict(path=str(source),duration=4,trim_start=0))
    owner.render_group_options['2']=dict(clip=dict(path=str(destination),duration=4,trim_start=0))
    owner.shot_sequence_settings=dict(width=128,height=96)
    calls=[];blend=shot_sequence.blend_frame
    def composite(a,b,alpha,mode,side):calls.append((alpha,mode,side));return blend(a,b,alpha,mode,side)
    monkeypatch.setattr(shot_sequence,'blend_frame',composite)
    token=GROUP_PROMPT_CONTEXT.set(snapshot(owner,[2]))
    try:inputs,_=augment([],'Rules','openai',ai._mixed_reference_input)
    finally:GROUP_PROMPT_CONTEXT.reset(token)
    assert inputs[0]['anchor_source']=='composited blend start'
    assert calls==[(1/23,'perceptual_crossfade','source')]
    assert inputs[0]['source_time']==pytest.approx(4-22/24)


@pytest.mark.parametrize('operation',['generate','refine'])
def test_both_continuity_images_have_relational_labels_and_segment_distances(tmp_path,monkeypatch,operation):
    owner,groups,video=owner_fixture(tmp_path)
    groups[1]=[Segment('A','','','text','text','First action.',1,render_group=1),
               Segment('B','','','text','text','Second action.',1,render_group=1),
               Segment('C','','','text','text','Third action.',2,render_group=1)]
    owner.render_group_options['2']=dict(clip=dict(path=str(video),duration=4,trim_start=0))
    before=[s.to_dict() for items in groups.values() for s in items]
    records=snapshot(owner,[1]);original_fingerprint=fingerprint(records)
    token=GROUP_PROMPT_CONTEXT.set(records)
    try:
        images,rules=augment([],'Rules','openai',ai._mixed_reference_input)
    finally:GROUP_PROMPT_CONTEXT.reset(token)
    incoming=next(i for i in images if i.get('group_start_anchor'))
    outgoing=next(i for i in images if i.get('group_end_anchor'))
    assert [r['intervening_segment_count'] for r in incoming['segment_relationships']]==[0,1,2]
    assert [r['intervening_segment_count'] for r in outgoing['segment_relationships']]==[2,1,0]
    assert incoming['segment_relationships'][0]['relationship']=='Immediately before this segment; no intervening segments'
    assert outgoing['segment_relationships'][-1]['relationship']=='Immediately after this segment; no intervening segments'
    assert outgoing['segment_relationships'][0]['intervening_segments'][0]['prompt']=='Second action.'
    # A missing immediate output searches farther forward and counts every
    # authored segment across the intervening group boundary.
    groups[3]=[Segment('Later','','','text','text','Later action.',4,render_group=3)]
    owner.render_group_options['3']=owner.render_group_options.pop('2')
    token=GROUP_PROMPT_CONTEXT.set(snapshot(owner,[1],[groups[1][-1].id]))
    try:later,_=augment([],'Rules','openai',ai._mixed_reference_input)
    finally:GROUP_PROMPT_CONTEXT.reset(token)
    tail=next(i for i in later if i.get('group_end_anchor'))
    assert tail['context_label']=='Later visual context'
    assert tail['segment_relationships'][0]['intervening_segment_count']==1
    groups.pop(3);owner.render_group_options['2']=owner.render_group_options.pop('3')
    owner.render_group_options['2']['attachmentRevision']=3
    assert fingerprint(snapshot(owner,[1]))!=original_fingerprint
    baseline=fingerprint(snapshot(owner,[1]));groups[1][1].prompt='A newer intervening action.'
    assert fingerprint(snapshot(owner,[1]))!=baseline
    groups[1][1].prompt='Second action.'
    request=[];monkeypatch.setattr(ai,'streaming_enabled',lambda:False)
    output=json.dumps(dict(prompt='Preserve the action while connecting the two visual states.'))
    def post(url,**kwargs):
        request.append(kwargs['json'])
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'output':[dict(type='message',content=[dict(type='output_text',text=output)])]})
    monkeypatch.setattr(ai.requests,'post',post)
    token=GROUP_PROMPT_CONTEXT.set(snapshot(owner,[1],[groups[1][1].id]))
    try:
        if operation=='generate':ai.build_minimax_h3_prompt(groups[1],'openai','fixture','fixture','Continue','',False,False,False,10)
        else:ai.refine_minimax_h3_prompt(groups[1],'openai','fixture','fixture','Continue','',False,False,False,'Current authored draft.','Refine only the selected segment.',10)
    finally:GROUP_PROMPT_CONTEXT.reset(token)
    body=json.dumps(request[0],ensure_ascii=False)
    assert 'Incoming continuity frame' in body and 'Outgoing continuity frame' in body
    assert '1 intervening segments before this segment' in body and '1 intervening segments after this segment' in body
    assert 'source_time' not in body and 'group_local_time' not in body
    content=request[0]['input'][0]['content']
    assert sum(i['type']=='input_image' for i in content)>=2
    assert [s.to_dict() for items in groups.values() for s in items]==before
