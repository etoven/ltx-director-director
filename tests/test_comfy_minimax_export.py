import copy
import json
import subprocess
import wave
from pathlib import Path
from unittest.mock import patch
import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication
from ltx_prompt_director import ai, ui
from ltx_prompt_director.models import Segment
from ltx_prompt_director.media import data_url
from ltx_prompt_director.comfy_export import export_minimax_workflow
from ltx_prompt_director.minimax_reference import reference_slots, reference_inventory
from ltx_prompt_director.reference_media import import_reference
from ltx_prompt_director.project_archive import save_project_archive, read_project
from ltx_prompt_director.workspaces import WorkspaceStore

@pytest.fixture
def media(tmp_path):
    image=tmp_path/'opening.png';Image.new('RGB',(64,64),'orange').save(image)
    audio=tmp_path/'sound.wav'
    with wave.open(str(audio),'wb') as w:
        w.setnchannels(2);w.setsampwidth(2);w.setframerate(48000);w.writeframes(b'\0\0'*48000*2)
    import imageio_ffmpeg
    video=tmp_path/'sample.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-v','error','-loop','1','-i',str(image),'-i',str(audio),
                    '-t','1','-r','24','-pix_fmt','yuv420p',str(video)],check=True)
    return image,audio,video


def validate_graph(w):
    nodes={n['id']:n for n in w['nodes']}
    assert len(nodes)==len(w['nodes'])
    assert len({l[0] for l in w['links']})==len(w['links'])
    for lid,origin,slot,target,tslot,kind in w['links']:
        assert origin in nodes and target in nodes
        assert lid in nodes[origin]['outputs'][slot]['links']
        assert nodes[target]['inputs'][tslot]['link']==lid
    assert w['last_node_id']>=max(nodes)
    assert w['last_link_id']>=max(l[0] for l in w['links'])
    return nodes


def connection(w,node_id,name):
    nodes={n['id']:n for n in w['nodes']}
    inp=next(x for x in nodes[node_id]['inputs'] if x['name']==name)
    if inp['link'] is None:return None
    link=next(x for x in w['links'] if x[0]==inp['link'])
    source=nodes[link[1]]
    if source['type']=='GetNode' and source['widgets_values'][0].startswith('LTX Director '):
        setter=next(n for n in nodes.values() if n['type']=='SetNode' and n.get('widgets_values')==source['widgets_values'])
        return connection(w,setter['id'],setter['inputs'][0]['name'])
    return source,link[2]


def test_four_references_and_audio_only_provider_transport(media):
    image,audio,video=media
    v=import_reference(video);a=import_reference(audio)
    refs=[{'name':'image','image':data_url(str(image)),'role':'identity'},v,a,{**v,'audio_only':True}]
    slots=reference_slots(refs)
    assert len(slots)==4
    records=reference_inventory([],slots)
    assert [r['label'] for r in records]==['Image1','Video1','Audio2','Audio3']
    assert records[1]['audio_label']=='Audio1'
    inputs=ai._minimax_reference_inputs([], 'gemini',slots)
    assert 'video' in inputs[1]
    assert inputs[2]['audio'].startswith('data:audio/wav;')
    assert 'audio' in inputs[3] and 'image' not in inputs[3] and 'video' not in inputs[3]
    with patch.object(ai.requests,'post') as http:
        http.return_value.json.return_value={'candidates':[{'content':{'parts':[{'text':'{"prompt":"Scene"}'}]}}]}
        ai._gemini_raw(inputs,'key','model','rules',10)
        parts=http.call_args.kwargs['json']['contents'][0]['parts']
        assert sum(p.get('inline_data',{}).get('mime_type')=='audio/wav' for p in parts)==2


@pytest.mark.parametrize('kind',['image','video'])
def test_timeline_media_only_condition_and_reference_slots_stay_separate(media,tmp_path,kind):
    image,audio,video=media
    first=video if kind=='video' else image
    segments=[Segment(first.name,str(first),str(image),kind,'start','Opening',3),
              Segment(image.name,str(image),str(image),'image','end','Ending',7.5)]
    refs=[{'name':f'ref{i}.png','image':data_url(str(image)),'role':'identity'} for i in range(4)]
    path=tmp_path/'workflow.json'
    export_minimax_workflow(path,segments,'My actual production prompt',refs,tmp_path/'comfy',896,512)
    w=json.loads(path.read_text());nodes=validate_graph(w)
    native=nodes[136]
    assert len([x for x in native['inputs'] if x['name'].startswith('ref_images.')])==4
    assert not {451,579,580,581,585} & nodes.keys()
    assert not any(n['type']=='LazySwitchKJ' for n in nodes.values())
    if kind=='video':
        video_loader=next(n for n in nodes.values() if n['type']=='VHS_LoadVideo')
        assert not any(x['name'].startswith(('ref_videos.','ref_video_audios.')) for x in native['inputs'])
        assert video_loader['type']=='VHS_LoadVideo'
        assert not any(x['name'].startswith('ref_audios.') for x in native['inputs'])
        assert not any(n.get('title') in {'Conditioning video sample','Video opening still (fallback)'} for n in nodes.values())
    guides=w['extra']['ltxDirector']['guides']
    assert [x['seconds'] for x in guides]==[0,10.5]
    if kind=='image':
        opening_loader=connection(w,guides[0]['node_id'],'image')[0]
        assert opening_loader['type']=='LoadImage'
        assert not any(l[1]==opening_loader['id'] and nodes[l[3]]['type']=='SetNode' for l in w['links'])
        assert connection(w,guides[1]['node_id'],'positive_1')[0]['id']==guides[0]['node_id']
    else:
        assert connection(w,guides[0]['node_id'],'positive_1')[0]['id']==518
        assert connection(w,guides[0]['node_id'],'image')==(video_loader,0)
        assert connection(w,guides[0]['node_id'],'audio')==(video_loader,2)
        assert connection(w,guides[1]['node_id'],'positive_1')[0]['id']==guides[0]['node_id']
    assert connection(w,271,'CONDITIONING')[0]['id']==guides[-1]['node_id']
    assert nodes[138]['widgets_values']==['My actual production prompt']
    for node in nodes.values():
        if node['type']=='LoadImage':
            p=tmp_path/'comfy'/'input'/node['widgets_values'][0]
            if node.get('title')=='Video opening still (fallback)':
                assert Image.open(p).size==(64,64)
            else:
                assert p.read_bytes()==image.read_bytes()
        if node['type']=='VHS_LoadVideo':
            p=tmp_path/'comfy'/'input'/node['widgets_values']['video']
            assert p.read_bytes()==video.read_bytes()
    assert '15' not in [str(x['seconds']) for x in guides]


def test_end_frame_times_and_audio_only_video_load_audio(media,tmp_path):
    image,audio,video=media
    v=import_reference(video)
    refs=[{**v,'audio_only':True},import_reference(audio),v,{'name':'still','image':data_url(str(image)),'role':'identity'}]
    segments=[Segment('Opening custom name',str(image),str(image),'image','end','',3),Segment('b.png',str(image),str(image),'image','end','',7.5)]
    path=tmp_path/'w.json';export_minimax_workflow(path,segments,'Scene',refs,tmp_path/'comfy')
    w=json.loads(path.read_text());nodes=validate_graph(w)
    assert 579 not in nodes
    assert [r['seconds'] for r in w['extra']['ltxDirector']['guides']]==[3,10.5]
    assert connection(w,136,'ref_audios.ref_audio_0')[0]['type']=='LoadAudio'
    assert connection(w,136,'ref_audios.ref_audio_1')[0]['type']=='LoadAudio'
    assert connection(w,136,'ref_videos.ref_video_0')[0]['type']=='VHS_LoadVideo'
    assert connection(w,136,'ref_video_audios.ref_video_audio_0')[1]==2
    # Repeated exports must not overwrite existing input media.
    before={p.name:p.read_bytes() for p in (tmp_path/'comfy/input').iterdir()}
    export_minimax_workflow(tmp_path/'second.json',segments,'Scene',refs,tmp_path/'comfy')
    assert all((tmp_path/'comfy/input'/n).read_bytes()==v for n,v in before.items())


def test_reference_media_is_portable_after_source_files_disappear(media,tmp_path):
    image,audio,video=media
    ref=import_reference(video);ref['audio_only']=True
    payload={'app':'ltx-director-director','frames':[],'minimaxH3':{'referenceImages':[ref]}}
    original=copy.deepcopy(payload)
    archive=tmp_path/'ref.LTXD';save_project_archive(archive,payload,[])
    Path(original['minimaxH3']['referenceImages'][0]['path']).unlink()
    Path(original['minimaxH3']['referenceImages'][0]['audio_path']).unlink()
    loaded=reference_slots(read_project(archive)['minimaxH3']['referenceImages'])[0]
    assert loaded['audio_only']
    assert Path(loaded['path']).read_bytes()==video.read_bytes()
    assert Path(loaded['audio_path']).is_file()


def test_default_four_slot_ui_checkbox_and_export_action(media,tmp_path):
    app=QApplication.instance() or QApplication([])
    store=WorkspaceStore(tmp_path/'ws')
    with patch.object(ui,'WorkspaceStore',return_value=store),patch.object(ui.MainWindow,'restore_startup_workspace'):
        w=ui.MainWindow()
    try:
        w.set_project_type('minimax_full_reference_guide')
        assert len(w.minimax_panel.reference_targets)==4
        assert w.workspace_definition['references']['untimed_slots']==4
        ref=import_reference(media[2]);w.set_minimax_reference_image(3,ref)
        target=w.minimax_panel.reference_targets[3]
        assert not target.audio_only.isHidden()
        target.audio_only.setChecked(True)
        assert w.minimax_reference_images[3]['audio_only']
        assert w.comfy_export_action.isVisible()
        assert not w.comfy_export_action.icon().isNull()
        w.unified_prompt.setPlainText('Scene')
        with patch.object(w,'resolve_comfy_root',return_value=tmp_path/'comfy'),patch.object(w,'start_disk_job') as job:
            w.comfy_export_action.trigger()
            assert job.call_args.args[0] is export_minimax_workflow
            assert job.call_args.args[1][3][3]['audio_only']
    finally:
        w.project_dirty=False;w._close_saves_queued=True;w.close()



def test_native_limits_reject_before_copying_and_t2v_has_no_stale_media(media,tmp_path):
    image,audio,video=media
    refs=[import_reference(audio) for _ in range(4)]
    with pytest.raises(ValueError,match='standalone audio'):
        export_minimax_workflow(tmp_path/'bad.json',[],'Scene',refs,tmp_path/'comfy')
    assert not (tmp_path/'comfy/input').exists()
    p=tmp_path/'t2v.json'
    export_minimax_workflow(p,[],'My standalone prompt',[],tmp_path/'comfy')
    w=json.loads(p.read_text());nodes=validate_graph(w)
    assert not any(n['type'] in {'LoadImage','VHS_LoadVideo','LoadAudio'} for n in nodes.values())
    assert connection(w,271,'CONDITIONING')[0]['id']==518
    assert nodes[138]['widgets_values']==['My standalone prompt']


def test_video_drop_import_and_checkbox_roundtrip(media,tmp_path):
    from ltx_prompt_director.minimax_reference_widgets import MiniMaxReferenceSlot
    from PySide6.QtCore import QMimeData,QUrl
    app=QApplication.instance() or QApplication([])
    slot=MiniMaxReferenceSlot(4)
    mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(media[2]))])
    assert slot.accepts_mime(mime)
    values=[];slot.changed.connect(values.append)
    assert slot.load_mime(mime)
    assert slot.value['kind']=='video'
    slot.audio_only.setChecked(True)
    assert values[-1]['audio_only']
    assert Path(values[-1]['path']).read_bytes()==media[2].read_bytes()
    assert values[-1]['path']!=str(media[2])
    recovered=reference_slots([None,None,None,values[-1]])[3]
    slot.set_value(None);slot.set_value(recovered)
    assert slot.audio_only.isChecked()


def test_four_reference_upgrade_updates_only_untouched_stock(tmp_path):
    original=json.loads((Path(__file__).parent/'fixtures/minimax_full_reference_a31.json').read_text())
    store=WorkspaceStore(tmp_path/'workspaces')
    store.save(original)
    custom=copy.deepcopy(original);custom.update(id='my_reference_workspace',name='My custom two-slot workspace')
    store.save(custom)
    (store.root/'.stock-minimax-four-media').unlink()
    loaded=WorkspaceStore(store.root).load()
    assert loaded['minimax_full_reference_guide']['references']['untimed_slots']==4
    assert loaded['my_reference_workspace']==custom


def test_imported_reference_image_keeps_original_bytes_for_comfy(tmp_path):
    from ltx_prompt_director.minimax_reference_widgets import MiniMaxReferenceSlot
    app=QApplication.instance() or QApplication([])
    image=tmp_path/'original.jpg';Image.new('RGB',(400,300),'red').save(image,quality=91)
    slot=MiniMaxReferenceSlot(1);assert slot.load_path(str(image))
    output=tmp_path/'workflow.json'
    export_minimax_workflow(output,[],'Scene',[slot.value],tmp_path/'comfy')
    graph=json.loads(output.read_text())
    loader=next(n for n in graph['nodes'] if n['type']=='LoadImage')
    assert (tmp_path/'comfy/input'/loader['widgets_values'][0]).read_bytes()==image.read_bytes()



def test_openai_video_reference_has_consistent_ordinals_and_audio_observation_status(media):
    ref=import_reference(media[2])
    inputs=ai._minimax_reference_inputs([], 'openai',[ref])
    assert inputs[0]['video_number']==1
    assert inputs[0]['video_frames']
    assert 'not auditioned' in inputs[0]['video_analysis_mode']
    with patch.object(ai.requests,'post') as http:
        http.return_value.json.return_value={'output_text':'{"prompt":"Scene"}'}
        assert ai._openai_raw(inputs,'key','rules',10)=='{"prompt":"Scene"}'
        content=http.call_args.kwargs['json']['input'][0]['content']
        assert any(x.get('text','').startswith('VIDEO 1 OBSERVATION') for x in content)
