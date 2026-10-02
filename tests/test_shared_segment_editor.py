"""Timeline-owned segment editing with independently editable production drafts."""
import copy
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from PySide6.QtWidgets import QApplication
from ltx_prompt_director import ai, ui
from ltx_prompt_director.models import Segment
from ltx_prompt_director.minimax_reference import detect_workflow
from ltx_prompt_director.workspaces import WorkspaceStore

@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = WorkspaceStore(tmp_path / 'workspaces')
    with patch.object(ui, 'WorkspaceStore', return_value=store), patch.object(ui.MainWindow, 'restore_startup_workspace'):
        w = ui.MainWindow()
    w.segments = [Segment('Opening', '', '', 'image', 'start', 'Walk', 2),
                  Segment('Ending', '', '', 'image', 'end', 'Turn', 3)]
    w.refresh_timeline(0)
    w.set_project_type('minimax_base_guide')
    yield w
    w.project_dirty = False
    w._close_saves_queued = True
    w.close()

@pytest.mark.parametrize('draft', ['[TIMED ACTION]\n00:00:00:00 - 00:00:99:00: Wrong range.',
                                    'No timing here.', '[Shot 2] At 00:04.000 dialogue.'])
def test_unified_edit_never_conforms_or_mutates_segments(window, draft):
    before = [s.to_dict() for s in window.segments]
    window.unified_prompt.setPlainText(draft)
    assert [s.to_dict() for s in window.segments] == before
    assert not hasattr(window, 'conform_timed_prompt')
    assert not window.unified_prompt.inline_cue_mode

@pytest.mark.parametrize('mode', ['minimax_base_guide', 'minimax_full_reference_guide', 'minimax_official_skill_base', 'minimax_official_skill_reference'])
def test_segment_edits_and_duration_preserve_unified_draft(window, mode):
    window.set_project_type(mode)
    window.unified_prompt.setPlainText('My manually edited production draft')
    window.segment_prompt.setPlainText('New motion')
    window.change_duration(window.segments[0].id, 4)
    assert window.segments[0].prompt == 'New motion'
    assert window.unified_prompt.toPlainText() == 'My manually edited production draft'
    assert 'Segment changes available' in window.minimax_panel.cache_state.text()

@pytest.mark.parametrize('mode', ['minimax_base_guide', 'minimax_full_reference_guide', 'minimax_official_skill_base', 'minimax_official_skill_reference'])
def test_generated_prompt_does_not_drive_timeline(window, mode):
    window.set_project_type(mode)
    before = [s.to_dict() for s in window.segments]
    window.minimax_operation_signature = window.current_minimax_cache_key()
    window.minimax_operation_kind = 'generate_' + window.minimax_prompt_mode
    draft = '[TIMED ACTION]\n00:00:00:00 - 00:00:30:00: Thirty seconds.'
    window.minimax_h3_finished(draft)
    assert window.unified_prompt.toPlainText() == draft
    assert [s.to_dict() for s in window.segments] == before

@pytest.mark.parametrize('mode', ['ltx', 'minimax_base_guide', 'minimax_full_reference_guide', 'minimax_official_skill_base', 'minimax_official_skill_reference'])
def test_same_segment_operations_across_modes(window, mode):
    editor = window.segment_prompt
    window.set_project_type(mode)
    assert window.segment_prompt is editor
    with patch.object(window, 'ai_credentials', return_value=('gemini', 'model', 'key')), patch.object(window, 'start_ai_worker') as worker:
        window.generate_project_prompt()
        assert worker.call_args.args[0] is ai.build_prompts
        window.refine_project_prompt()
        assert worker.call_args.args[0] is ai.refine_segment_prompt
    assert window.conditioning_guide.height() == 68

@pytest.mark.parametrize('mode', ['minimax_base_guide', 'minimax_full_reference_guide', 'minimax_official_skill_base', 'minimax_official_skill_reference'])
def test_project_roundtrip_keeps_both_editors(window, mode):
    window.set_project_type(mode)
    window.segment_prompt.setPlainText('Selected beat edited')
    window.unified_prompt.setPlainText('Production draft edited')
    payload = window.project_payload(include_media=False)
    assert 'cueActions' not in payload['minimaxH3']
    window.load_project_payload(payload)
    window.timeline.setCurrentRow(0)
    assert window.segment_prompt.toPlainText() == 'Selected beat edited'
    assert window.unified_prompt.toPlainText() == 'Production draft edited'

@pytest.mark.parametrize('old,new,mode', [('minimax_frames','minimax_base_guide','frames'),
                                         ('minimax_references','minimax_full_reference_guide','references')])
def test_legacy_draft_migration_without_embedded_workspace(window, old, new, mode):
    payload = window.project_payload(include_media=False)
    payload.pop('workspaceDefinition', None)
    payload['projectType'] = old
    payload['minimaxH3'] = {'mode':mode, 'prompt':'Legacy production draft',
                           'drafts':{mode:{'prompt':'Legacy production draft'}},
                           'cueActions':{window.segments[0].id:'Obsolete linked action'}}
    payload['frames'][0]['promptDetached'] = True
    window.load_project_payload(payload)
    assert window.project_type == new
    assert window.unified_prompt.toPlainText() == 'Legacy production draft'
    assert not window.segments[0].prompt_detached
    window.segment_prompt.setPlainText('Editable again')
    assert window.segments[0].prompt == 'Editable again'


def test_conditioning_strip_tracks_real_checkpoints_and_source_trim(window):
    window.segments.insert(0, Segment('Lead in', '', '', 'text', 'text', '', 1))
    window.segments.append(Segment('Tail', '', '', 'text', 'text', '', 1))
    window.refresh_timeline(1)
    assert [r['checkpoint_time'] for r in window.conditioning_records if 'checkpoint_time' in r] == [1, 6]
    assert window.conditioning_fixes == ['Move opening to 0.00s', 'Move ending to 7.00s']
    window.segments = [Segment('Clip', '', '', 'video', 'start', '', 3, 240, 48)]
    window.refresh_timeline(0)
    assert window.conditioning_records[0]['source_start'] == 2
    assert window.conditioning_records[0]['source_end'] == 5
    # Prompt typing keeps the existing thumbnail widgets rather than decoding images again.
    tile = window.conditioning_row.itemAt(1).widget()
    window.segment_prompt.setPlainText('Typing')
    assert window.conditioning_row.itemAt(1).widget() is tile


def test_new_defaults_and_mode_variables(window):
    assert set(window.workspace_definitions) == {'ltx', 'minimax_official_skill_base', 'minimax_official_skill_reference', 'minimax_base_guide', 'minimax_full_reference_guide'}
    for mode in ['minimax_base_guide', 'minimax_full_reference_guide']:
        definition = window.workspace_definitions[mode]
        assert '${workflow_name}' in definition['generation_prompts']['generate']
        assert '${workflow_direction}' in definition['generation_prompts']['generate']
        token = ai.WORKSPACE_INSTRUCTIONS.set(definition['generation_prompts'])
        try:
            rules = ai._minimax_h3_rules(window.segments, '', '', False, True, True) if mode == 'minimax_base_guide' else ai._minimax_h3_reference_rules(window.segments, '', '', False, True, True)
            assert 'First / last frame (FL2V)' in rules
            assert '${' not in rules
            assert 'original spoken lines' in rules
        finally:
            ai.WORKSPACE_INSTRUCTIONS.reset(token)
    assert detect_workflow([Segment('Last', '', '', 'image', 'end', '', 3)]) == 'l2v'


def test_new_defaults_respect_deleted_stock_and_edited_legacy(window):
    store = window.workspace_store
    custom = copy.deepcopy(window.workspace_definitions['minimax_base_guide'])
    custom.update(id='minimax_frames', name='Personal MiniMax')
    store.save(custom)
    store.delete('minimax_full_reference_guide')
    values = WorkspaceStore(store.root).load()
    assert 'minimax_full_reference_guide' not in values
    assert values['minimax_frames'] == custom
    store.restore_stock()
    assert 'minimax_full_reference_guide' in store.load()

@pytest.mark.parametrize('mode', ['ltx','minimax_base_guide','minimax_full_reference_guide','minimax_official_skill_base','minimax_official_skill_reference'])
def test_segment_refinement_resizes_timeline_in_every_mode(window, mode):
    window.set_project_type(mode)
    window.unified_prompt.setPlainText('Preserve production draft')
    window.refinement_segment_id = window.segments[0].id
    with patch.object(window, '_generate_minimax_prompt') as generate:
        window.refine_prompt_finished({'prompt':'Slower walk', 'duration':8.5})
        assert generate.call_count == (mode != 'ltx')
    assert [s.duration for s in window.segments] == [8.5,3]
    assert window.duration_spin.value() == 8.5
    assert window.timeline.itemWidget(window.timeline.item(0)).resize_handle.duration == 8.5
    if mode != 'ltx':
        assert window.unified_prompt.toPlainText() == 'Preserve production draft'

@pytest.mark.parametrize('mode', ['ltx','minimax_base_guide','minimax_full_reference_guide','minimax_official_skill_base','minimax_official_skill_reference'])
def test_global_proportional_resize_routes_and_updates_timeline(window, mode):
    window.set_project_type(mode)
    window.unified_prompt.setPlainText('Keep unified production draft')
    window.prompt_scope.setCurrentIndex(1)
    window.segment_prompt.setPlainText('/refine-global Resize the segments proportionally to 15 seconds\nPreserve identity.')
    assert window.refine_prompt_button.isEnabled()
    with patch.object(window, 'ai_credentials', return_value=('gemini','model','key')), patch.object(window,'start_ai_worker') as worker:
        window.refine_project_prompt()
        assert worker.call_args.args[0] is ai.refine_global_prompt
        args = worker.call_args.args[1]
    with patch.object(ai, '_provider_raw', return_value=json.dumps({'globalPrompt':'Preserve identity.', 'proportionalTotal':15})):
        result = ai.refine_global_prompt(*args)
    assert result['durations'] == [6,9]
    with patch.object(window, '_generate_minimax_prompt') as generate:
        window.refine_global_finished(result)
        assert generate.call_count == (mode != 'ltx')
    assert [s.duration for s in window.segments] == [6,9]
    assert [s.prompt for s in window.segments] == ['Walk','Turn']
    assert window.segment_prompt.toPlainText() == 'Preserve identity.'
    assert window.duration_spin.value() == 6
    if mode != 'ltx':
        assert window.unified_prompt.toPlainText() == 'Keep unified production draft'


def test_global_result_rejected_if_user_edits_during_request(window):
    window.prompt_scope.setCurrentIndex(1)
    window.segment_prompt.setPlainText('/refine-global Resize to 15 seconds')
    with patch.object(window,'ai_credentials',return_value=('gemini','model','key')), patch.object(window,'start_ai_worker'):
        window.refine_project_prompt()
    window.segment_prompt.setPlainText('Changed my mind')
    with patch.object(window, '_generate_minimax_prompt') as generate:
        window.refine_global_finished({'globalPrompt':'Obsolete result','durations':[6,9]})
        generate.assert_not_called()
    assert window.global_prompt.toPlainText() == 'Changed my mind'
    assert [s.duration for s in window.segments] == [2,3]

@pytest.mark.parametrize('response', [{'globalPrompt':'Keep'}, {'globalPrompt':'Keep','durations':[3]},
                                      {'globalPrompt':'Keep','proportionalTotal':float('inf')},
                                      {'globalPrompt':'Keep','durations':[0,3]}])
def test_invalid_global_timing_is_rejected(window, response):
    with patch.object(ai,'_provider_raw',return_value=json.dumps(response)), pytest.raises(ai.AIResponseFormatError):
        ai.refine_global_prompt(window.segments,'gemini','model','key','', 'Keep')


def test_old_factory_embedded_in_project_does_not_return_to_defaults(window):
    import subprocess
    payload = window.project_payload(include_media=False)
    old = json.loads(subprocess.check_output(['git','show','9c180c1:ltx_prompt_director/workspace_templates/minimax_references.json']))
    payload['workspaceDefinition'] = old
    payload['projectType'] = old['id']
    payload['minimaxH3'].update(mode='references', prompt='Retain my old draft')
    window.load_project_payload(payload)
    assert window.project_type == 'minimax_full_reference_guide'
    assert window.unified_prompt.toPlainText() == 'Retain my old draft'
    assert set(window.workspace_store.load()) == {'ltx','minimax_official_skill_base','minimax_official_skill_reference','minimax_base_guide','minimax_full_reference_guide'}


def test_proportional_rounding_preserves_exact_total(window):
    window.segments = [Segment('A','','','text','text','',1), Segment('B','','','text','text','',1), Segment('C','','','text','text','',1)]
    with patch.object(ai,'_provider_raw',return_value='{"globalPrompt":"Keep", "proportionalTotal":10}'):
        result = ai.refine_global_prompt(window.segments,'gemini','model','key','','Resize to 10 seconds proportionally')
    assert sum(result['durations']) == 10
    assert max(result['durations']) - min(result['durations']) < .011

@pytest.mark.parametrize('mode,build,refine', [
    ('minimax_official_skill_base', ai.build_minimax_h3_prompt, ai.refine_minimax_h3_prompt),
    ('minimax_official_skill_reference', ai.build_minimax_h3_reference_prompt, ai.refine_minimax_h3_reference_prompt)])
def test_official_skill_transport_resolves_variables_and_refinement(window, mode, build, refine):
    window.set_project_type(mode)
    definition = window.workspace_definition
    token = ai.WORKSPACE_INSTRUCTIONS.set(definition['generation_prompts'])
    try:
        with patch.object(ai,'_provider_raw',return_value='{"prompt":"Finished production prompt"}') as provider:
            assert build(window.segments, 'gemini','model','key','My creative direction','Shared identity',True,True,True) == 'Finished production prompt'
            rules = provider.call_args.args[4]
            assert '${' not in rules
            assert 'First / last frame (FL2V)' in rules
            assert 'Effective target duration: 5.000 seconds' in rules
            assert 'Image1' in rules and 'Image2' in rules
            assert 'My creative direction' in rules and 'Shared identity' in rules
            assert 'non_diegetic_music: N/A' in rules
            assert 'never copy their sample subjects' in rules
            assert '"prompt" string' in rules
            refine(window.segments,'gemini','model','key','','',True,True,True,
                   'My manually edited scene\n/refine-global Add one spoken line','Keep the camera still')
            rules = provider.call_args.args[4]
            assert '${' not in rules
            assert 'My manually edited scene' in rules
            assert 'Keep the camera still' in rules
            assert 'return actual spoken lines' in rules
    finally:
        ai.WORKSPACE_INSTRUCTIONS.reset(token)


def test_official_skill_upgrade_is_additive_and_preserves_customizations(window):
    store = window.workspace_store
    # Simulate a26: its migration markers exist but the new skill release hasn't run.
    store.delete('minimax_official_skill_base')
    edited = copy.deepcopy(store.load()['minimax_official_skill_reference'])
    edited['name'] = 'My edited skill'
    store.save(edited)
    store.delete('minimax_full_reference_guide')
    (store.root / '.stock-minimax-official-skills').unlink()
    values = WorkspaceStore(store.root).load()
    assert 'minimax_official_skill_base' in values
    assert values['minimax_official_skill_reference'] == edited
    assert 'minimax_full_reference_guide' not in values
    # Once installed, intentionally deleted skill defaults stay deleted too.
    store.delete('minimax_official_skill_base')
    assert 'minimax_official_skill_base' not in WorkspaceStore(store.root).load()


def test_official_reference_skill_includes_companion_conventions(window):
    rules = window.workspace_definitions['minimax_official_skill_reference']['generation_prompts']['generate']
    for phrase in ['350-500 English words', 'attribute_transfer', 'partially_copy',
                   'not independently assigned or renumbered', 'says in an off-screen voiceover',
                   '<scenetrans>', '<cutoff>', 'Zoom In / Zoom Out', '[unclear]']:
        assert phrase in rules
    assert 'Full-reference rules below take priority over companion base conventions' in rules
    assert '## 7. Complete Example' not in rules


def test_shown_condition_frame_values_are_cumulative_end_times(window):
    from PySide6.QtWidgets import QLabel
    window.segments = [Segment('First','','','image','end','',3), Segment('Second','','','image','end','',7.5)]
    window.refresh_timeline(0)
    assert [r['checkpoint_time'] for r in window.conditioning_records] == [3,10.5]
    text = ' '.join(label.text() for label in window.conditioning_content.findChildren(QLabel))
    assert 'Image1 · value 3.00' in text
    assert 'Image2 · value 10.50' in text
    window.segments[0].role = 'start'
    window.mark_dirty()
    assert window.conditioning_records[0]['checkpoint_time'] == 0


def test_intent_uses_same_native_splitter_and_resizes_text_area(window):
    window.resize(1400,1400)
    for dock in [window.project_dock,window.project_properties_dock,window.project_files_dock,window.project_preview_dock]:
        dock.hide()
    window.set_director_intent_expanded(True)
    window.show()
    QApplication.processEvents()
    assert window.intent_prompt_splitter.handle(1).metaObject().className() == window.prompt_splitter.handle(1).metaObject().className()
    before = window.intent.height()
    total = sum(window.intent_prompt_splitter.sizes())
    window.intent_prompt_splitter.setSizes([300,total-300])
    window.intent_prompt_splitter.splitterMoved.emit(300,1)
    QApplication.processEvents()
    assert window.intent.height() > before
    saved = window.intent_prompt_splitter.sizes()[0]
    assert window.settings.value('director_intent_height',0,int) == saved
    for mode in ['ltx','minimax_official_skill_reference','minimax_base_guide']:
        window.set_project_type(mode)
        QApplication.processEvents()
        assert abs(window.intent_prompt_splitter.sizes()[0]-saved) < 3
    window.set_director_intent_expanded(False)
    assert window.intent_prompt_splitter.sizes()[0] == 0
    assert window.intent_prompt_splitter.handle(1).isVisible()
    window.set_director_intent_expanded(True)
    assert window.intent_prompt_splitter.sizes()[0] == saved


def test_intent_collapse_survives_new_window(window):
    window.set_director_intent_expanded(False)
    with patch.object(ui,'WorkspaceStore',return_value=window.workspace_store), patch.object(ui.MainWindow,'restore_startup_workspace'):
        reopened = ui.MainWindow()
    try:
        reopened.show()
        QApplication.processEvents()
        assert reopened.intent_prompt_splitter.sizes()[0] == 0
        assert not hasattr(reopened,'direction_toggle')
    finally:
        reopened._close_saves_queued=True
        reopened.project_dirty=False
        reopened.close()
        window.set_director_intent_expanded(True)


def test_redundant_lower_generation_and_refinement_buttons_are_removed(window):
    from PySide6.QtWidgets import QPushButton
    labels=[b.text() for b in window.minimax_panel.findChildren(QPushButton)]
    assert not any('Generate Unified' in text or 'Refine Unified' in text for text in labels)
    assert not hasattr(window.minimax_panel,'generate_button')
    assert not hasattr(window.minimax_panel,'refine_button')
    assert window.refine_prompt_button.text() == '✎ Refine Prompt'


@pytest.mark.parametrize('mode', ['minimax_base_guide', 'minimax_full_reference_guide',
                                  'minimax_official_skill_base', 'minimax_official_skill_reference'])
@pytest.mark.parametrize('action', ['prompt', 'timing'])
def test_refine_buttons_generate_unified_output_after_applying_segment_result(window, mode, action):
    window.set_project_type(mode)
    window.unified_prompt.setPlainText('Previous production draft')
    window.segment_prompt.setPlainText('User edited motion')
    window.settings.setValue('provider', 'gemini')
    window.settings.setValue('gemini_model', 'model')
    button = window.refine_prompt_button if action == 'prompt' else window.refine_timing_button
    with patch.object(window, 'ai_credentials', return_value=('gemini', 'model', 'key')), patch.object(window, 'start_ai_worker') as worker:
        button.click()
        assert worker.call_args.args[0] is (ai.refine_segment_prompt if action == 'prompt' else ai.refine_timing)
        callback = worker.call_args.args[3]
        callback({'prompt': 'Refined motion', 'duration': 7})
        expected = ai.build_minimax_h3_reference_prompt if window.minimax_prompt_mode == 'references' else ai.build_minimax_h3_prompt
        assert worker.call_args.args[0] is expected
        assert worker.call_args.args[1][0][0].duration == 7
        assert worker.call_args.args[1][0][0].prompt == ('Refined motion' if action == 'prompt' else 'User edited motion')
        before = [s.to_dict() for s in window.segments]
        worker.call_args.args[3]('Generated unified output')
    assert window.unified_prompt.toPlainText() == 'Generated unified output'
    assert [s.to_dict() for s in window.segments] == before


def test_missing_refinement_target_does_not_generate_unified_output(window):
    window.refinement_segment_id = 'deleted-segment'
    with patch.object(window, '_generate_minimax_prompt') as generate:
        window.refine_prompt_finished({'prompt': 'Obsolete', 'duration': 4})
        window.refine_timing_finished({'duration': 4})
    generate.assert_not_called()


@pytest.mark.parametrize('mode', ['ltx', 'minimax_base_guide', 'minimax_full_reference_guide',
                                  'minimax_official_skill_base', 'minimax_official_skill_reference'])
@pytest.mark.parametrize('kind', ['timing', 'prompt', 'global'])
def test_refinement_buttons_generate_unified_from_updated_segments(window, mode, kind):
    window.set_project_type(mode)
    window.unified_prompt.setPlainText('Previous unified draft')
    if kind == 'global':
        window.prompt_scope.setCurrentIndex(1)
        window.segment_prompt.setPlainText('Keep identity. /refine-global Resize to 15 seconds')
    calls = []
    def start(operation, args, activity, finished, **kwargs):
        assert not window.ai_busy
        window.set_ai_controls_enabled(False)
        calls.append((operation, args, finished))
    with patch.object(window, 'ai_credentials', return_value=('gemini', 'model', 'key')), patch.object(window, 'start_ai_worker', side_effect=start):
        button = window.refine_timing_button if kind == 'timing' else window.refine_prompt_button
        button.click()
        assert len(calls) == 1
        result = ({'duration':8.5} if kind == 'timing' else
                  {'prompt':'Slower walk', 'duration':8.5} if kind == 'prompt' else
                  {'globalPrompt':'Keep identity.', 'durations':[6,9]})
        calls[0][2](result)
        if mode == 'ltx':
            assert len(calls) == 1
            assert not window.ai_busy
            return
        assert len(calls) == 2
        operation, args, finish = calls[1]
        assert operation is (ai.build_minimax_h3_reference_prompt if 'reference' in mode else ai.build_minimax_h3_prompt)
        assert [s.duration for s in args[0]] == ([6,9] if kind == 'global' else [8.5,3])
        assert args[0][0].prompt == ('Slower walk' if kind == 'prompt' else 'Walk')
        assert window.ai_busy
        assert window.unified_prompt.toPlainText() == 'Previous unified draft'
        finish('Fresh unified production prompt')
        assert not window.ai_busy
        assert window.unified_prompt.toPlainText() == 'Fresh unified production prompt'


def test_missing_refinement_target_does_not_generate_unified(window):
    window.refinement_segment_id = 'deleted-segment'
    with patch.object(window, '_generate_minimax_prompt') as generate:
        window.refine_timing_finished({'duration':5})
        window.refine_prompt_finished({'duration':5, 'prompt':'Obsolete'})
        generate.assert_not_called()
