"""Grouping projects after real library metadata has been saved."""
import copy
import json
from pathlib import Path
from ltx_prompt_director.collection_combine import combine_projects
from ltx_prompt_director.models import Segment
from ltx_prompt_director.project_archive import read_project,save_project_archive


def saved_projects(tmp_path,legacy=True):
    from ltx_prompt_director.ui import _persist_library_archive
    records=[]
    for index in range(2):
        segment=Segment(f'Prompt {index}','','','text','text',f'Original segment {index}',15)
        source=tmp_path/f'source-{index}.LTXD'
        payload=dict(app='ltx-director-director',projectType='minimax_official_skill_reference',frames=[segment.to_dict()],
            videoRenderCache={},minimaxH3=dict(prompt=f'Original unified {index}',referenceImages=[]))
        save_project_archive(source,payload,[segment])
        record=dict(id=str(index),name=f'Source {index}',projectPath=str(source),collection='Collection')
        # This is the same persistence path used by library metadata edits.
        _persist_library_archive(record,tmp_path/f'{index}.meta.json')
        stored=read_project(source)
        if legacy:
            # Reproduce archives already written by the prior metadata writer.
            stored['library']['videoRenderCache']=''
            save_project_archive(source,stored,[segment])
        else:assert stored['library']['videoRenderCache']=={}
        records.append(record)
    return records


def test_grouping_normal_saved_library_projects_with_empty_string_cache(tmp_path):
    records=saved_projects(tmp_path)
    originals={r['projectPath']:Path(r['projectPath']).read_bytes() for r in records}
    meta=combine_projects(records,tmp_path/'library','combined','Grouped prompts')
    payload=read_project(meta['projectPath'])
    assert [s['prompt'] for s in payload['frames']]==['Original segment 0','Original segment 1']
    assert [s['render_group'] for s in payload['frames']]==[0,1]
    assert sum(s['duration'] for s in payload['frames'])==30
    assert [payload['renderGroupOptions'][str(i)]['context']['minimaxH3']['prompt'] for i in range(2)]==['Original unified 0','Original unified 1']
    for path,original in originals.items():assert Path(path).read_bytes()==original


def test_metadata_writer_stores_empty_cache_as_object_for_archive_and_json(tmp_path):
    from ltx_prompt_director.ui import _persist_library_archive
    saved_projects(tmp_path,legacy=False)
    path=tmp_path/'legacy.json';path.write_text(json.dumps(dict(frames=[],library={})))
    _persist_library_archive(dict(projectPath=str(path)),tmp_path/'unused.meta.json')
    assert json.loads(path.read_text())['library']['videoRenderCache']=={}


def test_optional_cache_formats_preserve_a_valid_fallback():
    from ltx_prompt_director.render_cache import render_cache_from
    entry=dict(signature='kept',identity=[3,4],result=dict(path='/kept.mp4'))
    valid={'0':entry}
    for legacy in ('',None,[],0,'invalid JSON','[]','null',{'0':'invalid record'}):
        assert render_cache_from(legacy,valid)==valid
    assert render_cache_from(json.dumps(valid))==valid
    empty={};assert render_cache_from('',empty) is empty


def test_grouping_keeps_saved_cache_when_legacy_metadata_cache_is_blank(tmp_path):
    records=saved_projects(tmp_path)
    entry=dict(signature='kept',identity=[3,4],result=dict(path='/completed-first.mp4'))
    record=records[0];source=Path(record['projectPath']);payload=read_project(source)
    payload['videoRenderCache']={'0':entry}
    save_project_archive(source,payload,[Segment.from_dict(payload['frames'][0])])
    record['videoRenderCache']=''  # an old metadata file can have the same value
    meta=combine_projects(records,tmp_path/'library','combined','Grouped prompts')
    assert read_project(meta['projectPath'])['videoRenderCache']['0']==entry


def test_collection_group_action_opens_saved_video_groups_without_changing_sources(tmp_path):
    from unittest.mock import patch
    from PIL import Image
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QListWidgetItem
    from test_video_extension_ui import window,APP
    from test_video_extensions import clip
    from ltx_prompt_director import ui
    from ltx_prompt_director.media import data_url
    root=tmp_path/'library';root.mkdir();records=[];originals={}
    definition=json.loads((Path(ui.__file__).parent/'workspace_templates/minimax_official_skill_reference.json').read_text())
    for index in range(2):
        movie=clip(root,f'source-video-{index}',15,'blue' if index else 'red',False)
        preview=root/f'preview-{index}.png';Image.new('RGB',(32,32),'navy').save(preview)
        segment=Segment(f'Video {index}',movie,str(preview),'video','start',f'Video direction {index}',15,360,0)
        source=root/f'source-{index}.LTXD'
        payload=dict(app='ltx-director-director',projectType=definition['id'],workspaceDefinition=definition,frames=[segment.to_dict()],
            videoRenderCache={},library={'videoRenderCache':''},minimaxH3=dict(prompt=f'Unified video {index}',
            referenceImages=[dict(role='identity',name=f'Identity_{index}.png',image=data_url(str(preview)))]))
        save_project_archive(source,copy.deepcopy(payload),[segment])
        record=dict(id=str(index),name=f'Source {index}',projectPath=str(source),collection='Collection')
        (root/f'{index}.meta.json').write_text(json.dumps(record))
        records.append(record);originals[str(source)]=source.read_bytes()
    with patch.object(ui,'project_library_path',return_value=root),window(tmp_path) as w:
        w.current_collection='Collection';w.project_list.clear()
        for record in records:
            item=QListWidgetItem(record['name']);item.setData(Qt.ItemDataRole.UserRole,{**record,'kind':'project'})
            w.project_list.addItem(item);item.setSelected(True)
        def disk(op,args,ready,failed,**kwargs):
            try:result=op(*args)
            except Exception as error:failed(str(error))
            else:ready(result)
        with patch.object(w,'start_disk_job',side_effect=disk),patch.object(ui.QMessageBox,'warning') as warning,patch.object(ui.QMessageBox,'critical') as critical:
            w.group_selected_projects();APP.processEvents()
            assert not warning.called and not critical.called,(warning.call_args,critical.call_args)
        assert w.current_project_name=='Grouped prompts'
        assert [s.kind for s in w.segments]==['video','video']
        assert [s.render_group for s in w.segments]==[0,1]
        assert w.total_duration()==30
        w.timeline.setCurrentRow(0);assert w.minimax_prompt_text=='Unified video 0'
        assert w.minimax_reference_images[0]['name']=='Identity_0.png'
        assert w.minimax_reference_images[0]['image']==data_url(str(root/'preview-0.png'))
        w.timeline.setCurrentRow(1);assert w.minimax_prompt_text=='Unified video 1'
        assert w.minimax_reference_images[0]['name']=='Identity_1.png'
        assert w.minimax_reference_images[0]['image']==data_url(str(root/'preview-1.png'))
    for source,original in originals.items():assert Path(source).read_bytes()==original
