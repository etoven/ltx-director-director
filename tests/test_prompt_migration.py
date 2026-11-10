"""Saved prose migrates locally once and keeps timeline source text intact."""
from ltx_prompt_director.prompt_migration import migrate_project
from ltx_prompt_director.output_templates import default_layout


def test_all_fields_shots_and_video_definition_migrate_once():
    definition=dict(id='full',engine='minimax_references',prompt_mode='unified',output_template=default_layout('minimax_references'))
    text='''subject_definitions:
<Video 1> opening clip.
<Subject 1> courier from <Video 1>.
<Audio 1> voice reference.
summary:
Quiet evening.
retention_analysis:
Preserve the clip opening.
detailed_description:
Scene introduction. [Shot 1] At 00:00.00, Saved opening. [Shot 2] 00:02.00, Saved ending.
overall_soundscape:
Footsteps and city ambience.
non_diegetic_music: N/A'''
    payload=dict(projectVersion=10,projectType='full',workspaceDefinition=definition,
        minimaxH3=dict(prompt=text,referenceImages=[]),frames=[dict(id='a',prompt='Timeline source A',duration=2),dict(id='b',prompt='Timeline source B',duration=3)])
    migrated=migrate_project(payload,{'full':definition});state=migrated['promptTemplateStates']['full:0']
    assert state['global']=='Quiet evening.'
    assert state['fields']==dict(retention='Preserve the clip opening.',sound='Footsteps and city ambience.',music='N/A')
    assert state['prompts']==dict(a='Scene introduction.\n\n[Shot 1] At 00:00.00, Saved opening.',b='[Shot 2] 00:02.00, Saved ending.')
    assert [item['name'] for item in state['custom_subjects']]==['Video 1','Subject 1','Audio 1']
    assert migrated['frames']==payload['frames'] and 'promptTemplateStates' not in payload
    state['global']='Edited after migration'
    assert migrate_project(migrated,{'full':definition}) is migrated
    assert state['global']=='Edited after migration'


def test_legacy_workspace_aliases_and_inactive_render_groups():
    workspace = 'minimax_official_skill_reference'
    definition = dict(id=workspace, prompt_mode='unified', output_template=default_layout('minimax_references'))
    payload = dict(projectType='minimax_references', activeRenderGroup=1,
        frames=[dict(id='a',render_group=0,prompt='Timeline A'),dict(id='b',render_group=1,prompt='Timeline B')],
        minimaxH3=dict(prompt='summary:\nActive group.\ndetailed_description:\n[Shot 1] Saved B.'),
        renderGroupOptions={'0':dict(context=dict(projectType='minimax_references',
            minimaxH3=dict(prompt='summary:\nOther group.\ndetailed_description:\n[Shot 1] Saved A.')))})
    migrated = migrate_project(payload, {workspace: definition})
    states = migrated['promptTemplateStates']
    assert states[workspace+':0']['prompts']=={'a':'[Shot 1] Saved A.'}
    assert states[workspace+':1']['prompts']=={'b':'[Shot 1] Saved B.'}
    assert states[workspace+':0']['global']=='Other group.'
    assert states[workspace+':1']['global']=='Active group.'
