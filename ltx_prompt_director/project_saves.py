"""Immutable project-save snapshots and conservative automatic name eligibility."""
import copy
import re


def name_origin(meta):
    return meta.get('nameOrigin') or ('placeholder' if re.fullmatch(r'Untitled(?: project)?(?: \(\d+\))?',str(meta.get('name','')),re.I) else 'manual')


def payload_from_state(state):
    s=copy.deepcopy(state)
    direct=('projectType','workspaceDefinition','shotSequence','downloadDirectory','promptTemplateStates',
            'globalPrompt','directorIntent','videoRenderCache','combinedSources','videoExtensions',
            'renderGroupOptions','activeRenderGroup','groupedSequence','conditioningAudio')
    value={key:s[key] for key in direct if key in s}
    value.update(app='ltx-director-director',projectVersion=11,promptTemplateVersion=1,
        directionOptions={key:s.get(key) for key in ('requestedLength','speakerLanguage','speakerAccent')},
        magicBuild={key:s.get(key,False) for key in ('sfx','spokenDialog','hdr','reduceMusic')},
        output=dict(width=s.get('outputWidth',1280),height=s.get('outputHeight',704)),
        timelineView=dict(scale=s.get('timelineScale',65),autoFit=s.get('timelineAutoFit',False),height=s.get('timelineHeight',122)),
        minimaxH3=dict(prompt=s.get('minimaxPrompt',''),refinementInstructions=s.get('minimaxRefinementInstructions',''),
            sourceHash=s.get('minimaxPromptCacheKey',''),updatedAt=s.get('minimaxPromptUpdatedAt',''),mode=s.get('minimaxPromptMode','frames'),
            referenceImages=s.get('minimaxReferenceImages',[]),drafts=s.get('minimaxDrafts',{})),
        frames=[segment.to_dict() for segment in s.get('segments',[])])
    return value
