"""A combined project is portable and keeps source prompts and workflow media."""
import base64
import copy
from pathlib import Path
import shutil
import zipfile
from PIL import Image
from ltx_prompt_director.collection_combine import combine_projects
from ltx_prompt_director.models import Segment
from ltx_prompt_director.project_archive import read_project,save_project_archive
from ltx_prompt_director.media import data_url
from test_video_extensions import clip


def test_combine_keeps_order_types_segment_unified_and_reference_images(tmp_path):
    records=[];expected={}
    for index,kind in enumerate(['minimax_references','minimax_frames']):
        image=tmp_path/f'image{index}.png';Image.new('RGB',(32,32),('navy' if index else 'gold')).save(image)
        segment=Segment(f'Image {index}',str(image),str(image),'image','start',f'Segment wording {index}',5)
        payload=dict(app='ltx-director-director',projectType=kind,frames=[segment.to_dict()],
                     globalPrompt=f'Global {index}',minimaxH3=dict(prompt=f'Unified {index}',
                     referenceImages=[dict(name='Identity.png',image=data_url(str(image)))],drafts={'other':{'prompt':'Other draft'}}))
        source=tmp_path/f'source{index}.LTXD';save_project_archive(source,copy.deepcopy(payload),[segment])
        records.append(dict(id=str(index),name=f'Project {index}',projectPath=str(source),collection='Collection',
                            previewVideoPath=clip(tmp_path,str(index),4,'red' if index else 'blue',False)))
        expected[str(index)]=(kind,source.read_bytes(),image.read_bytes())
    root=tmp_path/'library';root.mkdir()
    meta=combine_projects(list(reversed(records)),root,'combined','Grouped shots')
    packed=Path(meta['projectPath']);payload=read_project(packed)
    assert [s['id'] for s in payload['combinedSources']]==['1','0']
    assert [s['source_project_id'] for s in payload['frames']]==['1','0']
    # Move the project and remove every original; its embedded source projects remain usable.
    portable=tmp_path/'portable.LTXD';shutil.copy2(packed,portable)
    for record in records:Path(record['projectPath']).unlink();Path(record['previewVideoPath']).unlink()
    for image in tmp_path.glob('image*.png'):image.unlink()
    restored=read_project(portable)
    for source in restored['combinedSources']:
        index=source['id'];original=read_project(source['projectPath'])
        assert original['projectType']==expected[index][0]
        assert original['frames'][0]['prompt']==f'Segment wording {index}'
        assert original['minimaxH3']['prompt']==f'Unified {index}'
        assert original['globalPrompt']==f'Global {index}'
        assert original['minimaxH3']['drafts']['other']['prompt']=='Other draft'
        assert original['minimaxH3']['referenceImages'][0]['image']=='data:image/png;base64,'+base64.b64encode(expected[index][2]).decode()
        with zipfile.ZipFile(source['projectPath']) as archive:
            assert archive.read(original['frames'][0]['archiveSource'])==expected[index][2]
    shots=[]
    for raw in restored['frames']:
        shot=Segment.from_dict(raw);shot.media_path=raw['_archiveMediaPath'];shot.preview_path=raw['_archivePreviewPath']
        shot._archive_source=(raw['_archivePath'],raw['archiveSource']);shots.append(shot)
    second=tmp_path/'resaved.LTXD';save_project_archive(second,restored,shots)
    assert len(read_project(second)['combinedSources'])==2


def test_cached_edits_are_embedded_without_rewriting_original(tmp_path):
    image=tmp_path/'reference.png';Image.new('RGB',(16,16),'green').save(image)
    segment=Segment('Image',str(image),str(image),'image','start','Saved segment',5)
    payload=dict(app='ltx-director-director',projectType='minimax_references',frames=[segment.to_dict()],
                 minimaxH3=dict(prompt='Saved unified',referenceImages=[dict(name='Reference.png',image=data_url(str(image)))]))
    archive=tmp_path/'original.LTXD';save_project_archive(archive,copy.deepcopy(payload),[segment]);before=archive.read_bytes()
    preview=clip(tmp_path,'rendered',4,'red',False)
    records=[dict(id=str(i),name=f'Project {i}',collection='Collection',projectPath=str(archive),previewVideoPath=preview) for i in range(2)]
    edited=copy.deepcopy(segment);edited.prompt='Unsaved segment wording'
    state=dict(projectType='minimax_references',segments=[edited],minimaxPrompt='Unsaved unified wording',
               minimaxReferenceImages=[dict(name='Reference.png',image=data_url(str(image)))],
               workspaceDefinition={'id':'custom-guide','engine':'minimax_references'})
    root=tmp_path/'combined';root.mkdir()
    meta=combine_projects(records,root,'new','Grouped shots',{'0':state})
    restored=read_project(meta['projectPath']);source=read_project(restored['combinedSources'][0]['projectPath'])
    assert source['frames'][0]['prompt']=='Unsaved segment wording'
    assert source['minimaxH3']['prompt']=='Unsaved unified wording'
    assert source['workspaceDefinition']['id']=='custom-guide'
    assert source['minimaxH3']['referenceImages'][0]['image']==data_url(str(image))
    assert archive.read_bytes()==before
