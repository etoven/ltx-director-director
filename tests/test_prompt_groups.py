"""Acceptance cases from the user's three-plus-one grouped prompt workflow."""
import copy
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt,QPoint
from PySide6.QtTest import QTest
from PIL import Image
from ltx_prompt_director.models import Segment
from ltx_prompt_director.collection_combine import combine_projects
from ltx_prompt_director.project_archive import save_project_archive,read_project
from ltx_prompt_director.render_groups import input_stamp
from test_video_extension_ui import window,APP


def prepare(w):
    w.set_project_type('minimax_references')
    w.segments=[Segment('Prompt '+str(i+1),'','','text','text','Original segment '+str(i+1),5,render_group=0 if i<3 else 1) for i in range(4)]
    w.video_extensions=1;w._active_render_group=0
    w.requested_length.blockSignals(True);w.requested_length.setValue(15);w.requested_length.blockSignals(False)
    w.unified_prompt.setPlainText('Group A original Unified Prompt.')
    w.sfx.setChecked(True);w.store_render_group()
    other=copy.deepcopy(w.capture_render_group());other['minimaxH3']['prompt']='Group B original Unified Prompt.'
    other['minimaxH3']['drafts']={};other['directionOptions']['requestedLength']=5;other['magicBuild']['sfx']=False
    w.render_group_options['1']=dict(requestedLength=5,context=other,blendLength=1,blend='perceptual_crossfade')
    w.refresh_timeline(0)


def test_three_plus_one_keeps_shared_bottom_controls_and_original_prompts(tmp_path):
    with window(tmp_path) as w:
        prepare(w);controls=(w.requested_length,w.sfx,w.unified_prompt,w.prompt_surface)
        w.prompt_surface.select('unified');w.audio_panel.hide()
        w.timeline.setCurrentRow(3)
        assert w.audio_panel.isHidden()
        assert controls==(w.requested_length,w.sfx,w.unified_prompt,w.prompt_surface)
        assert w.requested_length.value()==5 and not w.sfx.isChecked()
        assert w.minimax_prompt_text=='Group B original Unified Prompt.'
        assert w.prompt_surface.scope.currentData()=='unified'
        w.unified_prompt.setPlainText('Edited group B only.')
        w.timeline.setCurrentRow(0)
        assert w.requested_length.value()==15 and w.sfx.isChecked()
        assert w.minimax_prompt_text=='Group A original Unified Prompt.'
        w.timeline.setCurrentRow(3);assert w.minimax_prompt_text=='Edited group B only.'
        assert [s.prompt for s in w.segments]==['Original segment '+str(i+1) for i in range(4)]
        payload=w.project_payload(False);w.load_project_payload(copy.deepcopy(payload))
        w.timeline.setCurrentRow(0);assert w.minimax_prompt_text=='Group A original Unified Prompt.'
        w.timeline.setCurrentRow(3);assert w.minimax_prompt_text=='Edited group B only.'
        assert len(w.segments)==4 and len(w.render_groups())==2


def test_group_workflow_and_references_restore_in_existing_ui(tmp_path):
    with window(tmp_path) as w:
        prepare(w)
        w.minimax_reference_images=[dict(name='A.png',role='identity',image='data:image/png;base64,AAAA'),None,None,None]
        original_type=w.project_type;w.store_render_group()
        other=w.render_group_options['1']['context'];standard=next(key for key,value in w.workspace_definitions.items() if value['engine']=='minimax_frames')
        other['projectType']=standard
        other['workspaceDefinition']=copy.deepcopy(w.workspace_definitions[standard])
        other['minimaxH3']['referenceImages']=[dict(name='B.png',role='identity',image='data:image/png;base64,BBBB')]
        w.timeline.setCurrentRow(3)
        assert w.project_type==standard and not any(w.active_reference_images())
        assert w.minimax_reference_images[0]['name']=='B.png'
        w.timeline.setCurrentRow(0)
        assert w.project_type==original_type and w.active_reference_images()[0]['name']=='A.png'


def test_each_generation_request_uses_its_group_unified_prompt(tmp_path):
    with window(tmp_path) as w:
        prepare(w);runs=[];requests=[]
        w.render_queue.executor=lambda op,args,ready,failed,**kw:runs.append((op,args,ready,failed))
        def render(request,**kwargs):requests.append(copy.deepcopy(request));return {'path':str(tmp_path/f'{len(requests)}.mp4')}
        with patch('ltx_prompt_director.ui.validate_profile'),patch('ltx_prompt_director.ui.run_request',side_effect=render),patch('ltx_prompt_director.video_extensions.continuation_reference',return_value=dict(kind='video',path='previous.mp4',timeline_time=0,duration=1)),patch('ltx_prompt_director.video_extensions.blend_blocks',return_value={'path':str(tmp_path/'final.mp4')}),patch.object(w,'attach_local_generated_video'),patch.object(w.project_preview_panel,'set_project'):
            w.generate_local_video()
            for i in range(3):
                for _ in range(5):APP.processEvents()
                op,args,ready,failed=runs[i];ready(op(*args))
        assert [r['prompt'] for r in requests]==['Group A original Unified Prompt.','Group B original Unified Prompt.']
        assert [r['duration'] for r in requests]==[15,5]
        assert requests[1]['references'][0]['timeline_time']==0
        assert len(w.segments)==4


def test_merge_three_plus_one_is_portable_editable_groups_without_rendered_movies(tmp_path):
    records=[]
    for project,count in [(0,3),(1,1)]:
        image=tmp_path/f'{project}.png';Image.new('RGB',(32,32),'gold' if project else 'navy').save(image)
        segments=[Segment('Original '+str(i),str(image),str(image),'image','start',f'P{project} segment {i}',5) for i in range(count)]
        from ltx_prompt_director.media import data_url
        payload=dict(app='ltx-director-director',projectType='minimax_references',frames=[s.to_dict() for s in segments],minimaxH3=dict(prompt=f'Unified {project}',referenceImages=[dict(name=f'Ref {project}.png',role='identity',image=data_url(str(image)))]),magicBuild=dict(sfx=bool(project)))
        source=tmp_path/f'{project}.LTXD';save_project_archive(source,payload,segments)
        records.append(dict(id=str(project),name=f'Project {project}',projectPath=str(source),collection='Collection'))
    meta=combine_projects(records,tmp_path/'library','combined','Grouped prompts')
    for record in records:Path(record['projectPath']).unlink()
    for image in tmp_path.glob('*.png'):image.unlink()
    payload=read_project(meta['projectPath'])
    assert payload['projectType']!='shot_sequence'
    assert [s['render_group'] for s in payload['frames']]==[0,0,0,1]
    assert [s['prompt'] for s in payload['frames']]==['P0 segment 0','P0 segment 1','P0 segment 2','P1 segment 0']
    assert [payload['renderGroupOptions'][str(g)]['context']['minimaxH3']['prompt'] for g in (0,1)]==['Unified 0','Unified 1']
    for g in (0,1):assert payload['renderGroupOptions'][str(g)]['context']['minimaxH3']['referenceImages'][0]['image'].startswith('data:image/')
    from ltx_prompt_director.collection_combine import _segments
    segments=_segments(payload,tmp_path)
    resaved=tmp_path/'portable.LTXD';save_project_archive(resaved,payload,segments)
    assert len(read_project(resaved)['frames'])==4


def test_blend_matches_ruler_and_keeps_ten_pixel_padding(tmp_path):
    from ltx_prompt_director.extension_timeline import preview_bounds
    with window(tmp_path) as w:
        prepare(w);w.resize(1500,1000);w.show();APP.processEvents()
        w.set_timeline_scale(100);APP.processEvents();w.position_extension_boxes()
        box=w._extension_boxes[1]
        assert not w.extension_brackets.isHidden()
        assert box.width()==100
        bounds=[preview_bounds(w.timeline.itemWidget(w.timeline.item(i)),w.timeline.viewport()) for i in (2,3)]
        assert box.y()==max(b[0] for b in bounds)+10
        assert box.y()+box.height()==min(b[1] for b in bounds)-10
        assert box.mode.geometry().bottom()<box.height() and box.side.geometry().bottom()<box.height()
        boundary=w.timeline.visualItemRect(w.timeline.item(3)).left()
        assert abs(box.geometry().center().x()-boundary)<=1
        box.length.setValue(.25);APP.processEvents();assert box.width()==25
        assert abs(box.geometry().center().x()-boundary)<=1
        spans=w.extension_brackets.spans();x=(spans[1][1]+spans[1][2])//2
        QTest.mouseClick(w.extension_brackets,Qt.MouseButton.LeftButton,pos=QPoint(x,12))
        assert w._active_render_group==1 and w.requested_length.value()==5
        box.mode.setCurrentIndex(box.mode.findData('cut'));APP.processEvents();assert box.width()==1


def test_changed_group_render_is_not_used_for_scrubbing(tmp_path):
    from ltx_prompt_director.timeline_preview import resolve_preview
    from test_video_extensions import clip
    segments=[Segment('First','','','text','text','First',5),Segment('Next','','','text','text','Next',5,render_group=1)]
    contexts={'0':dict(minimaxH3=dict(prompt='A')),'1':dict(minimaxH3=dict(prompt='B'))}
    stamps={str(g):input_stamp([segments[g]],contexts[str(g)]) for g in (0,1)}
    first=clip(tmp_path,'first',5,'red',False);second=clip(tmp_path,'second',5,'blue',False)
    cache={'0':dict(inputStamp=stamps['0'],result=dict(path=first)),'1':dict(inputStamp=stamps['1'],result=dict(path=second))}
    segments[1].prompt='Changed later group'
    provenance=dict(contexts=contexts,finalStamps=stamps,finalBlends=[],blends=[])
    assert resolve_preview(segments,2,'',cache,provenance)['kind']=='rendered'
    assert resolve_preview(segments,7,'',cache,provenance)['text']=='Changed later group'


def test_merging_preserves_reuse_of_the_completed_first_group(tmp_path):
    with window(tmp_path) as w:
        prepare(w)
        w.segments=w.segments[:3];w.video_extensions=0;w.render_group_options={'0':w.render_group_options['0']};w.refresh_timeline(0)
        requests=[];runs=[]
        def render(request,**kwargs):
            requests.append(copy.deepcopy(request));path=tmp_path/f'block-{len(requests)}.mp4';path.write_bytes(str(len(requests)).encode());return {'path':str(path)}
        w.render_queue.executor=lambda op,args,ready,failed,**kw:runs.append((op,args,ready))
        def finish_jobs(start,count):
            for index in range(start,start+count):
                for _ in range(5):APP.processEvents()
                op,args,ready=runs[index];ready(op(*args))
        with patch('ltx_prompt_director.ui.validate_profile'),patch('ltx_prompt_director.ui.run_request',side_effect=render),patch('ltx_prompt_director.video_extensions.add_block_thumbnails',side_effect=lambda result,*args:result),patch('ltx_prompt_director.video_extensions.continuation_reference',side_effect=lambda path,marked:dict(kind='video',path=path,start=marked-1,duration=1,timeline_time=0)),patch('ltx_prompt_director.video_extensions.blend_blocks',return_value={'path':str(tmp_path/'final.mp4')}),patch.object(w.project_preview_panel,'set_project'):
            w.generate_local_video();finish_jobs(0,1);assert len(requests)==1
            records=[]
            first=w.project_payload(False);source=tmp_path/'first.LTXD';save_project_archive(source,copy.deepcopy(first),w.segments)
            records.append(dict(id='a',name='First',projectPath=str(source),collection='Collection'))
            segment=Segment('Prompt 4','','','text','text','Continue',5)
            second=copy.deepcopy(first);second['frames']=[segment.to_dict()];second['minimaxH3']['prompt']='Group B exact prompt'
            second['directionOptions']['requestedLength']=5;second['renderGroupOptions']={};second['videoRenderCache']={}
            source=tmp_path/'second.LTXD';save_project_archive(source,second,[segment])
            records.append(dict(id='b',name='Second',projectPath=str(source),collection='Collection'))
            merged=combine_projects(records,tmp_path/'merged','combined','Grouped prompts')
            w.load_project_payload(read_project(merged['projectPath']))
            start=len(runs);w.generate_local_video();finish_jobs(start,3)
            assert len(requests)==2
            assert requests[-1]['prompt']=='Group B exact prompt'
            assert len(w.segments)==4
