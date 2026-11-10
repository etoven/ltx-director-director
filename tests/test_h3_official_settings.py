import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from ltx_prompt_director import ai
from ltx_prompt_director.models import Segment
from ltx_prompt_director.prompt_templates import WORKSPACE_ENGINE, WORKSPACE_OPERATION, WORKSPACE_PROMPTS, official_h3_rules
from ltx_prompt_director.workspaces import WorkspaceStore, validate_definition
from ltx_prompt_director.output_templates import default_layout, render


@pytest.mark.parametrize('engine', ['minimax_frames', 'minimax_references'])
def test_settings_reach_every_operation(engine):
    segment = Segment('Beat', '', '', kind='text', role='text', duration=5, prompt='Walk. /refine Raise a hand.')
    before = segment.to_dict()
    operations = [
        (ai.build_prompts, ([segment], 'gemini', 'model', 'key', 'Walk', True, True, False, False), {'segments':[{'prompt':'(S1) says <d>[English] Hello.</d>', 'duration':5}], 'globalPrompt':'Walk'}),
        (ai.refine_sequence_prompts, ([segment], 'gemini', 'model', 'key', 'Walk', 'Walk', True, True, False, False), {'segments':[{'prompt':'(S1) says <d>[English] Hello.</d>', 'duration':5}], 'globalPrompt':'Walk'}),
        (ai.generate_segment_prompt, ([segment], 0, 'gemini', 'model', 'key', 'Walk', 0), {'prompt':'Walk.', 'imagePrompt':'', 'duration':5}),
        (ai.refine_segment_prompt, ([segment], 0, 'gemini', 'model', 'key', 'Walk', 0), {'prompt':'Walk.', 'imagePrompt':'', 'duration':5}),
        (ai.generate_global_direction, ([segment], 'gemini', 'model', 'key', 'Walk'), {'globalPrompt':'Walk.'}),
        (ai.refine_global_prompt, ([segment], 'gemini', 'model', 'key', '', 'Walk'), {'globalPrompt':'Walk.', 'durations':[5], 'proportionalTotal':None}),
        (ai.refine_timing, ([segment], 0, 'gemini', 'model', 'key', '', 0), {'duration':5}),
    ]
    builder = ai.build_minimax_h3_reference_prompt if engine.endswith('references') else ai.build_minimax_h3_prompt
    refiner = ai.refine_minimax_h3_reference_prompt if engine.endswith('references') else ai.refine_minimax_h3_prompt
    operations += [(builder, ([segment], 'gemini', 'model', 'key', '', '', False, False, True), {'prompt':'Walk.'}),
                   (refiner, ([segment], 'gemini', 'model', 'key', '', '', False, False, True, 'Walk.', 'Raise a hand'), {'prompt':'Walk.'})]
    for operation, args, response in operations:
        kind = 'refine' if 'refine' in operation.__name__ else 'generate'
        tokens = [(v, v.set(value)) for v,value in [(WORKSPACE_ENGINE,engine),(WORKSPACE_OPERATION,kind),(WORKSPACE_PROMPTS,{'generate':'MY GENERATION SETTINGS', 'refine':'MY REFINEMENT SETTINGS'})]]
        try:
            with patch.object(ai, '_gemini_raw', return_value=json.dumps(response)) as provider:
                operation(*args)
            rules = provider.call_args.args[3]
            assert ('MY REFINEMENT SETTINGS' if kind == 'refine' else 'MY GENERATION SETTINGS') in rules, operation.__name__
            if operation in (builder, refiner):
                assert 'Do not add a timestamp to the first shot.' in rules
            else:
                assert 'MINIMAX H3 WORK DOMAIN' in rules
                assert 'OFFICIAL MINIMAX H3 SKILL' not in rules
            assert 'LTX' not in rules
            assert 'ALWAYS include an explicit timecode' not in rules
            if engine.endswith('references') and operation in (builder, refiner):
                assert '<Audio 2>: reference' in rules
                for marker in ('fully_preserved', 'partially_preserved', 'attribute_transfer', 'weak_reference', 'fully_copy', 'partially_copy', 'reference'):
                    assert marker in rules
                assert 'Do not treat newly added actions, backgrounds, or plot events' in rules
            assert segment.to_dict() == before
        finally:
            for variable,token in reversed(tokens):variable.reset(token)


def test_upgrade_saved_and_embedded_definitions_preserves_custom_fields(tmp_path):
    store = WorkspaceStore(tmp_path)
    original = store.load()['ltx']
    original.update(id='my_minimax', name='Minimax Official', engine='minimax_frames', prompt_mode='unified', custom_note='Keep me')
    store.save(original)
    repaired = WorkspaceStore(tmp_path).load()['my_minimax']
    assert 'LTX' not in repaired['generation_prompts']['generate']
    assert repaired['custom_note'] == 'Keep me'
    custom = copy.deepcopy(repaired)
    custom['generation_prompts']['refine'] = 'My authored refinement'
    assert validate_definition(custom)['generation_prompts']['refine'] == 'My authored refinement'
    assert validate_definition(copy.deepcopy(original)) == repaired


def test_official_layout_does_not_invent_cuts_or_subject_labels():
    segments=[dict(id='a',prompt='[Shot 1] Walk.',duration=3),dict(id='b',prompt='Continue walking toward the final frame.',duration=3)]
    base=render(default_layout('minimax_frames'), '',segments,[],dict(sound='Steps.',music='N/A'))
    assert base.lstrip().startswith('integrated_multimodal_description:')
    assert '[Shot 2]' not in base and 'At 00:' not in base
    full=render(default_layout('minimax_references'),'[reference generation] Walk.',segments,[],dict(reference_definitions='<Picture 1> is the final frame.',retention='<Picture 1>: fully_preserved',sound='Steps.',music='N/A'))
    labels=['subject_definitions:', 'summary:', 'retention_analysis:', 'detailed_description:', 'overall_soundscape:', 'non_diegetic_music:']
    assert [full.index(label) for label in labels] == sorted(full.index(label) for label in labels)


def test_settings_native_switch_save_reopen(tmp_path):
    from PySide6.QtWidgets import QApplication, QPushButton
    from ltx_prompt_director.workspace_editor import WorkspaceEditor
    global _APP
    app=_APP=QApplication.instance() or QApplication([])
    class Owner:
        def reload_workspace_definitions(self, **kwargs):pass
    store=WorkspaceStore(tmp_path)
    editor=WorkspaceEditor(store,Owner())
    editor.populate(store.load()['ltx'])
    editor.identifier.setText('native_minimax');editor.name.setText('Native MiniMax')
    editor.current_id=None
    editor.engine.setCurrentIndex(editor.engine.findData('minimax_frames'))
    assert 'LTX' not in editor.generate.toPlainText()
    assert 'LTX' not in editor.refine.toPlainText()
    next(b for b in editor.findChildren(QPushButton) if b.text()=='Save / rename').click()
    app.processEvents()
    reopened=WorkspaceEditor(WorkspaceStore(tmp_path),Owner())
    reopened.populate(store.load()['native_minimax'])
    assert reopened.generate.toPlainText()==editor.generate.toPlainText()
    assert reopened.output_template.value()==default_layout('minimax_frames')
    editor.close();reopened.close()


@pytest.mark.parametrize('refine', [False, True])
def test_native_worker_captures_settings_and_does_not_fall_back_to_ltx(tmp_path, refine):
    from PySide6.QtWidgets import QApplication
    from ltx_prompt_director import ui
    global _APP
    app=_APP=QApplication.instance() or QApplication([])
    store=WorkspaceStore(tmp_path)
    with patch.object(ui,'WorkspaceStore',return_value=store), patch.object(ui.MainWindow,'restore_startup_workspace'), patch.object(ui.MainWindow,'keep_window_on_screen'):
        window=ui.MainWindow()
    try:
        window.set_project_type('minimax_official_skill_base')
        kind='refine' if refine else 'generate'
        window.workspace_definition['generation_prompts'][kind]='CAPTURED SETTINGS REFINEMENT'
        segment=Segment('Beat','','',kind='text',role='text',duration=5,prompt='Walk.')
        window.segments=[segment];window.refresh_timeline(0)
        assert window.workspace_engine == 'minimax_frames', ('after refresh',window.project_type)
        pending=[]
        class Pool:
            def start(self,worker):pending.append(worker)
        window.thread_pool=Pool()
        window.ai_credentials=lambda:('gemini','model','key')
        window.prompt_surface.actions['segment'][2 if refine else 1].click()
        assert len(pending)==1
        assert window.workspace_engine == 'minimax_frames', ('after click',window.project_type)
        window.workspace_definition['generation_prompts'][kind]='NEWER SETTINGS'
        with patch.object(ai,'_gemini_raw',return_value='{"prompt":"Walk and raise a hand.","duration":5,"imagePrompt":""}') as provider:
            pending[0].operation(*pending[0].args)
        rules=provider.call_args.args[3]
        assert 'CAPTURED SETTINGS REFINEMENT' in rules
        assert 'MINIMAX H3 WORK DOMAIN — SEGMENT' in rules
        assert 'OFFICIAL MINIMAX H3 SKILL' not in rules
        assert 'OUTPUT LAYOUT CONTRACT' not in rules
        assert 'NEWER SETTINGS' not in rules and 'LTX' not in rules
        assert WORKSPACE_ENGINE.get() is None
        assert segment.prompt=='Walk.'
    finally:
        window.set_ai_controls_enabled(True)
        window._close_saves_queued=True;window.project_dirty=False;window.close()


@pytest.mark.parametrize('engine', ['minimax_frames', 'minimax_references'])
def test_official_production_schema_renders_complete_sections(engine):
    from ltx_prompt_director.output_templates import ACTIVE_OUTPUT, request_context, root_fields
    segment=Segment('Beat','','',kind='text',role='text',duration=5,prompt='Walk.')
    definition=dict(engine=engine,output_template=default_layout(engine))
    context=request_context(definition,[segment],[])
    variables={name:'' for name in root_fields(context['layout'])}
    variables.update(sound='Footsteps.',music='N/A')
    if engine.endswith('references'):
        variables.update(global_prompt='[reference generation] A person walks.',retention='N/A',reference_definitions='N/A')
    response=json.dumps(dict(variables=variables,subjects=[],segments=[dict(id=segment.id,prompt='[Shot 1] A person walks.')]))
    token=ACTIVE_OUTPUT.set(context)
    try:
        with patch.object(ai,'_provider_raw',return_value=response):
            builder=ai.build_minimax_h3_reference_prompt if engine.endswith('references') else ai.build_minimax_h3_prompt
            output=builder([segment],'gemini','model','key','','',True,False,True)
        assert '[Shot 1] A person walks.' in output
        assert '[Shot 1] At' not in output
        assert '${' not in output
        assert 'overall_soundscape:\nFootsteps.' in output
        assert 'non_diegetic_music:\nN/A' in output
    finally:ACTIVE_OUTPUT.reset(token)


@pytest.mark.parametrize('engine', ['minimax_frames', 'minimax_references'])
@pytest.mark.parametrize('operation', [ai.generate_segment_prompt, ai.refine_segment_prompt])
def test_segment_scope_uses_action_prose_and_rejects_unified_document(engine,operation):
    from ltx_prompt_director.prompt_templates import stock_prompt
    segments=[Segment('First','','',kind='text',role='text',duration=3,prompt='Keep the door closed.'),
              Segment('Selected','','',kind='text',role='text',duration=4,prompt='She turns the key. /refine Slowly.'),
              Segment('Last','','',kind='text',role='text',duration=2,prompt='She walks away.')]
    before=[s.to_dict() for s in segments]
    kind='refine' if operation is ai.refine_segment_prompt else 'generate'
    tokens=[(v,v.set(value)) for v,value in [(WORKSPACE_ENGINE,engine),(WORKSPACE_OPERATION,kind),
        (WORKSPACE_PROMPTS,{k:stock_prompt(engine,k) for k in ('generate','refine')})]]
    try:
        args=(segments,1,'gemini','model','key','Keep the room quiet.',0)
        response={'prompt':'She slowly turns the key.','imagePrompt':'','duration':4}
        with patch.object(ai,'_gemini_raw',return_value=json.dumps(response)) as provider:
            assert operation(*args)==response
        rules=provider.call_args.args[3]
        assert 'MINIMAX H3 WORK DOMAIN — SEGMENT' in rules
        assert 'She turns the key.' in rules
        assert 'Final Prompt Structure' not in rules
        assert 'six sections' not in rules
        assert 'Generate complete production prose' not in rules
        assert 'DURATION INSTRUCTION' in rules or 'CURRENT DURATION' in rules
        bad={**response,'prompt':'summary: A key turns.\ndetailed_description: A complete movie.'}
        with patch.object(ai,'_gemini_raw',return_value=json.dumps(bad)):
            with pytest.raises(ai.AIResponseFormatError,match='Segment scope returned Unified'):
                operation(*args)
        assert [s.to_dict() for s in segments]==before
    finally:
        for variable,token in reversed(tokens):variable.reset(token)
