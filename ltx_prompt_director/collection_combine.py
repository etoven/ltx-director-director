"""Portable Shot Sequence creation retaining complete editable source projects."""
import copy
import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone
from .models import Segment
from .project_archive import read_project, save_project_archive
from .shot_sequence import DEFAULTS, independent_transitions


def workspace_payload(state):
    """Serialize cached edits without changing or opening their source project."""
    return dict(app='ltx-director-director', projectVersion=10,
        projectType=state['projectType'], workspaceDefinition=copy.deepcopy(state.get('workspaceDefinition',{})),
        frames=[s.to_dict() for s in state['segments']],
        globalPrompt=state.get('globalPrompt',''),directorIntent=state.get('directorIntent',''),
        directionOptions={k:state.get(k) for k in ('requestedLength','speakerLanguage','speakerAccent')},
        magicBuild={k:state.get(k) for k in ('sfx','spokenDialog','hdr','reduceMusic')},
        minimaxH3=dict(prompt=state.get('minimaxPrompt',''),refinementInstructions=state.get('minimaxRefinementInstructions',''),
            sourceHash=state.get('minimaxPromptCacheKey',''),updatedAt=state.get('minimaxPromptUpdatedAt',''),
            mode=state.get('minimaxPromptMode','frames'),referenceImages=copy.deepcopy(state.get('minimaxReferenceImages',[])),
            drafts=copy.deepcopy(state.get('minimaxDrafts',{}))),
        output=dict(width=state.get('outputWidth',1280),height=state.get('outputHeight',704)),
        shotSequence=copy.deepcopy(state.get('shotSequence',{})),conditioningAudio=copy.deepcopy(state.get('conditioningAudio',{})),
        videoExtensions=state.get('videoExtensions',0),renderGroupOptions=copy.deepcopy(state.get('renderGroupOptions',{})),
        activeRenderGroup=state.get('activeRenderGroup',0),combinedSources=copy.deepcopy(state.get('combinedSources',[])))


def combine_projects(records, root, ident, name, snapshots=None):
    from .shot_sequence_widgets import import_shots
    root=Path(root);snapshots=snapshots or {}
    target=root/(ident+'.LTXD');meta_path=root/(ident+'.meta.json')
    if target.exists() or meta_path.exists():raise FileExistsError('Combined project already exists.')
    sources=[];shots=[]
    # Temporary source bundles are embedded before this directory is removed.
    with tempfile.TemporaryDirectory(prefix='.combine-',dir=root) as folder:
        temporary=Path(folder)
        try:
            for index,record in enumerate(records):
                source_id=str(record['id'])
                state=snapshots.get(source_id)
                payload=workspace_payload(state) if state else read_project(record['projectPath'])
                payload['library']={key:value for key,value in record.items() if key not in {'kind','members'}}
                bundle=temporary/(str(index)+'.LTXD')
                if state:
                    save_project_archive(bundle,payload,copy.deepcopy(state['segments']))
                else:
                    # Repackage even legacy JSON projects into a portable archive.
                    segments=[]
                    for row,raw in enumerate(payload.get('frames',[])):
                        segment=Segment.from_dict(raw)
                        if raw.get('_archivePath'):
                            segment.media_path=raw['_archiveMediaPath'];segment.preview_path=raw['_archivePreviewPath']
                            segment._archive_source=(raw['_archivePath'],raw['archiveSource'])
                        for key,field,suffix in [('sourceData','media_path','.mp4' if segment.kind=='video' else '.png'),('previewData','preview_path','.png')]:
                            if raw.get(key) and not Path(getattr(segment,field)).is_file():
                                from .media import write_data_url
                                path=temporary/f'{index}-{row}-{key}{suffix}'
                                write_data_url(raw[key],path);setattr(segment,field,str(path))
                        segments.append(segment)
                    payload['frames']=[s.to_dict() for s in segments]
                    save_project_archive(bundle,payload,segments)
                imported=import_shots([record['previewVideoPath']],temporary/'shots')
                shot=imported[0];shot.name=str(record.get('name') or shot.name)
                shot.source_project_id=source_id
                shot.source_prompt_type=str(payload.get('projectType',''))
                shot.prompt=str(payload.get('minimaxH3',{}).get('prompt') or payload.get('globalPrompt') or '')
                shots.append(shot)
                sources.append(dict(id=source_id,name=record.get('name',''),projectType=payload.get('projectType',''),
                                    projectPath=str(bundle),shotId=shot.id))
            options=independent_transitions([shot.id for shot in shots],DEFAULTS)
            meta=dict(id=ident,name=name,collection=records[0].get('collection',''),projectType='shot_sequence',
                      projectPath=str(target),savedAt=datetime.now(timezone.utc).isoformat(),
                      description=f'{len(shots)} grouped projects · source prompts and media preserved',
                      tags=[],status='',archived=False,notes=[],sourceProjectIds=[s['id'] for s in sources])
            payload=dict(app='ltx-director-director',projectVersion=10,projectType='shot_sequence',
                         frames=[shot.to_dict() for shot in shots],shotSequence=options,
                         combinedSources=sources,library=meta)
            save_project_archive(target,payload,shots)
            pending=temporary/'metadata.json';pending.write_text(json.dumps(meta,indent=2),encoding='utf-8')
            os.replace(pending,meta_path)
        except Exception:
            target.unlink(missing_ok=True);meta_path.unlink(missing_ok=True);raise
    return meta


def import_collection_projects(records, directory, snapshots=None):
    """Import a collection into an existing sequence with its source bundles."""
    root=Path(directory);root.mkdir(parents=True,exist_ok=True)
    from uuid import uuid4
    meta=combine_projects(records,root,uuid4().hex,'Imported collection',snapshots)
    payload=read_project(meta['projectPath'])
    shots=[]
    for raw in payload['frames']:
        shot=Segment.from_dict(raw)
        shot.media_path=raw['_archiveMediaPath'];shot.preview_path=raw['_archivePreviewPath']
        shot._archive_source=(raw['_archivePath'],raw['archiveSource'])
        shots.append(shot)
    return shots,payload['combinedSources']
