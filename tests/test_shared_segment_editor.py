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

@pytest.mark.parametrize('mode', ['minimax_base_guide', 'minimax_full_reference_guide'])
def test_segment_edits_and_duration_preserve_unified_draft(window, mode):
    window.set_project_type(mode)
    window.unified_prompt.setPlainText('My manually edited production draft')
    window.segment_prompt.setPlainText('New motion')
    window.change_duration(window.segments[0].id, 4)
    assert window.segments[0].prompt == 'New motion'
    assert window.unified_prompt.toPlainText() == 'My manually edited production draft'
    assert 'Segment changes available' in window.minimax_panel.cache_state.text()

@pytest.mark.parametrize('mode', ['minimax_base_guide', 'minimax_full_reference_guide'])
def test_generated_prompt_does_not_drive_timeline(window, mode):
    window.set_project_type(mode)
    before = [s.to_dict() for s in window.segments]
    window.minimax_operation_signature = window.current_minimax_cache_key()
    window.minimax_operation_kind = 'generate_' + window.minimax_prompt_mode
    draft = '[TIMED ACTION]\n00:00:00:00 - 00:00:30:00: Thirty seconds.'
    window.minimax_h3_finished(draft)
    assert window.unified_prompt.toPlainText() == draft
    assert [s.to_dict() for s in window.segments] == before

@pytest.mark.parametrize('mode', ['ltx', 'minimax_base_guide', 'minimax_full_reference_guide'])
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

@pytest.mark.parametrize('mode', ['minimax_base_guide', 'minimax_full_reference_guide'])
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
    assert set(window.workspace_definitions) == {'ltx', 'minimax_base_guide', 'minimax_full_reference_guide'}
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

@pytest.mark.parametrize('mode', ['ltx','minimax_base_guide','minimax_full_reference_guide'])
def test_segment_refinement_resizes_timeline_in_every_mode(window, mode):
    window.set_project_type(mode)
    window.unified_prompt.setPlainText('Preserve production draft')
    window.refinement_segment_id = window.segments[0].id
    window.refine_prompt_finished({'prompt':'Slower walk', 'duration':8.5})
    assert [s.duration for s in window.segments] == [8.5,3]
    assert window.duration_spin.value() == 8.5
    assert window.timeline.itemWidget(window.timeline.item(0)).resize_handle.duration == 8.5
    if mode != 'ltx':
        assert window.unified_prompt.toPlainText() == 'Preserve production draft'

@pytest.mark.parametrize('mode', ['ltx','minimax_base_guide','minimax_full_reference_guide'])
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
    window.refine_global_finished(result)
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
    window.refine_global_finished({'globalPrompt':'Obsolete result','durations':[6,9]})
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
    assert set(window.workspace_store.load()) == {'ltx','minimax_base_guide','minimax_full_reference_guide'}


def test_proportional_rounding_preserves_exact_total(window):
    window.segments = [Segment('A','','','text','text','',1), Segment('B','','','text','text','',1), Segment('C','','','text','text','',1)]
    with patch.object(ai,'_provider_raw',return_value='{"globalPrompt":"Keep", "proportionalTotal":10}'):
        result = ai.refine_global_prompt(window.segments,'gemini','model','key','','Resize to 10 seconds proportionally')
    assert sum(result['durations']) == 10
    assert max(result['durations']) - min(result['durations']) < .011
