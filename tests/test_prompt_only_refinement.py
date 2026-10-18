import copy
import json
from unittest.mock import patch

import pytest
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

from ltx_prompt_director import ai, ui
from ltx_prompt_director.models import Segment
from ltx_prompt_director.prompt_tags import TAGS, insert_note, render_prompt_notes
from ltx_prompt_director.workspaces import WorkspaceStore, validate_definition
from ltx_prompt_director.workspace_editor import WorkspaceEditor


@pytest.fixture
def window(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = WorkspaceStore(tmp_path / 'workspaces')
    with patch.object(ui, 'WorkspaceStore', return_value=store), patch.object(ui.MainWindow, 'restore_startup_workspace'):
        w = ui.MainWindow()
    w.set_project_type('minimax_prompt_refinement')
    w.show()
    app.processEvents()
    yield w
    w._close_saves_queued = True
    w.project_dirty = False
    w.close()


def test_prompt_only_layout_and_empty_timeline_refinement(window):
    assert window.segments == []
    for control in [window.timeline_shell, window.conditioning_guide, window.segment_panel,
                    window.director_panel, window.refine_timing_button, window.magic_button,
                    window.references_button, window.segment_meta]:
        assert control.isHidden()
    assert window.unified_prompt.isVisible()
    assert window.refine_prompt_button.isVisible()
    assert not window.refine_prompt_button.isEnabled()
    window.unified_prompt.setPlainText('Static shot of a lamp.\n/refine-global Add a brief spoken line.')
    assert window.refine_prompt_button.isEnabled()
    with patch.object(window, 'ai_credentials', return_value=('gemini','model','key')), patch.object(window, 'start_ai_worker') as worker:
        window.refine_prompt_button.click()
        assert worker.call_args.args[0] is ai.refine_minimax_text_prompt
        assert worker.call_args.args[1][3] == window.unified_prompt.toPlainText()
        worker.call_args.args[3]('Static shot of a lamp. A woman says: <d>[English] Still here.</d>')
    assert 'Still here.' in window.unified_prompt.toPlainText()
    assert window.segments == []
    assert window.refine_prompt_button.isEnabled()


def test_existing_segment_context_never_sent_or_mutated(window):
    window.segments = [Segment('SECRET ASSET','','','text','text','SECRET SEGMENT',8)]
    window.intent.setPlainText('SECRET INTENT')
    window.global_prompt.setPlainText('SECRET GLOBAL')
    window.refresh_timeline(0)
    window.unified_prompt.setPlainText('Keep this independent scene.\n/keep Static camera')
    before = [s.to_dict() for s in window.segments]
    with patch.object(window,'ai_credentials', return_value=('gemini','model','key')), patch.object(window,'start_ai_worker') as worker:
        window.refine_prompt_button.click()
        args = worker.call_args.args[1]
        token = ai.WORKSPACE_INSTRUCTIONS.set(window.workspace_definition['generation_prompts'])
        try:
            with patch.object(ai,'_provider_raw',return_value=json.dumps({'prompt':'A clearer independent scene.\n/keep Static camera'})) as provider:
                text = worker.call_args.args[0](*args)
                inputs, _, _, _, rules, _ = provider.call_args.args
                assert inputs == []
                assert 'SECRET' not in rules
                assert '${' not in rules
                assert 'Keep this independent scene.' in rules
                assert '/keep Static camera' in rules
            worker.call_args.args[3](text)
        finally:
            ai.WORKSPACE_INSTRUCTIONS.reset(token)
    assert [s.to_dict() for s in window.segments] == before
    assert 'Segment changes available' not in window.minimax_panel.cache_state.text()
    window.add_media_paths(['ignored.png'])
    window.add_text_segment()
    assert [s.to_dict() for s in window.segments] == before


@pytest.mark.parametrize('tag', TAGS)
def test_all_annotations_are_native_editable_notes(window, tag):
    editor = window.unified_prompt
    assert hasattr(editor, '_prompt_tag_suggestions')
    assert not editor.inline_cue_mode
    insert_note(editor, editor.textCursor(), tag, 'Keep the scene grounded')
    assert tag in editor.toPlainText()
    assert 'Keep the scene grounded' in editor.toPlainText()
    cursor = editor.textCursor()
    assert cursor.currentTable() is not None
    cursor.insertText(' and clear')
    assert 'and clear' in editor.toPlainText()


def test_draft_roundtrip_and_mode_switch_restore_annotation_only_editor(window):
    window.unified_prompt.setPlainText('My scene\n/keep Identity\n/refine Make this clearer')
    render_prompt_notes(window.unified_prompt)
    draft = window.unified_prompt.toPlainText()
    window.set_project_type('ltx')
    assert window.segment_panel.isVisible()
    assert window.timeline_shell.isVisible()
    window.set_project_type('minimax_prompt_refinement')
    assert window.unified_prompt.toPlainText() == draft
    payload = window.project_payload(include_media=False)
    assert payload['projectType'] == 'minimax_prompt_refinement'
    assert payload['frames'] == []
    window.load_project_payload(payload)
    assert window.prompt_only_workspace
    assert window.unified_prompt.toPlainText() == draft
    assert window.timeline_shell.isHidden()


def test_edit_during_refinement_keeps_new_draft_and_retry_routes_text_only(window):
    window.unified_prompt.setPlainText('First version')
    with patch.object(window,'ai_credentials',return_value=('gemini','model','key')), patch.object(window,'start_ai_worker') as worker:
        window.refine_prompt_button.click()
        window.unified_prompt.setPlainText('New version')
        worker.call_args.args[3]('Stale result')
        assert window.unified_prompt.toPlainText() == 'New version'
        window.ai_activity_title = 'MiniMax Prompt Refinement'
        window.magic_failed('Test failure')
        assert window.minimax_panel.retry_button.isVisible()
        window.minimax_panel.retry_button.click()
        assert worker.call_args.args[0] is ai.refine_minimax_text_prompt
        assert worker.call_args.args[1][3] == 'New version'


def test_new_default_migration_preserves_customization_and_deletion(window):
    store = window.workspace_store
    value = copy.deepcopy(store.load()['minimax_prompt_refinement'])
    value['name'] = 'My text workspace'
    store.save(value)
    (store.root / '.stock-minimax-prompt-only').unlink()
    assert WorkspaceStore(store.root).load()['minimax_prompt_refinement']['name'] == 'My text workspace'
    store.delete('minimax_prompt_refinement')
    assert 'minimax_prompt_refinement' not in WorkspaceStore(store.root).load()
    store.restore_stock()
    assert 'minimax_prompt_refinement' in store.load()


def test_workspace_editor_roundtrip_and_schema_rejects_segment_features(window):
    editor = WorkspaceEditor(window.workspace_store, window)
    value = window.workspace_definition
    editor.populate(value)
    editor.current_id = value['id']
    assert editor.value() == value
    for change in [{'prompt_mode':'unified'}, {'global_prompt':True}, {'audio_generation':True},
                   {'references':{'enabled':True,'kinds':['image'],'untimed_slots':1}}]:
        with pytest.raises(ValueError):
            validate_definition({**value, **change})



def test_prompt_only_project_exports_and_saves_without_segments(window, tmp_path):
    from ltx_prompt_director.project_archive import read_project
    window.unified_prompt.setPlainText('Standalone scene\n/keep Camera still')
    path = tmp_path / 'prompt.LTXD'
    def disk(operation, args, finished, failed, **kwargs):
        finished(operation(*args))
    with patch.object(window,'next_export_path',return_value=path), patch.object(window,'record_export'), patch.object(window,'start_disk_job',side_effect=disk):
        window.export_project()
    assert path.is_file()
    saved = read_project(path)
    assert saved['frames'] == []
    window.load_project_payload(saved)
    assert '/keep Camera still' in window.unified_prompt.toPlainText()
    window.current_project_id = None
    window.mark_dirty()
    window.stage_current_workspace()
    assert window.current_project_id
    with patch.object(ui,'project_library_path',return_value=tmp_path / 'projects'), patch.object(window,'start_disk_job') as disk:
        window.save_library_project(automatic=True)
        assert disk.call_args.args[1][1]['frames'] == []
        assert disk.call_args.args[1][1]['minimaxH3']['prompt'] == window.unified_prompt.toPlainText()
        assert disk.call_args.args[1][2] == []


def test_leading_annotation_is_sent_as_explicit_whole_prompt_request(window):
    from ltx_prompt_director.prompt_tags import prompt_annotations
    text = "/refine make all this one continuous shot.\nFor the target video, <Picture 1> aligns with 0 seconds.\nintegrated_multimodal_description: [Shot 1] A traveler enters. [Shot 2] Cut to the lantern."
    window.unified_prompt.setPlainText(text)
    render_prompt_notes(window.unified_prompt)
    token = ai.WORKSPACE_INSTRUCTIONS.set(window.workspace_definition['generation_prompts'])
    try:
        with patch.object(ai,'_provider_raw',return_value='{"prompt":"One uninterrupted shot follows the traveler toward the lantern."}') as provider:
            result = ai.refine_minimax_text_prompt('gemini','model','key',window.unified_prompt.toPlainText())
            rules = provider.call_args.args[4]
            assert 'EXPLICIT ANNOTATION EDIT REQUESTS' in rules
            assert '/refine [whole prompt (leading annotation)]: make all this one continuous shot.' in rules
            assert 'Preservation rules do not block changes' in rules
            assert result.startswith('One uninterrupted shot')
    finally:
        ai.WORKSPACE_INSTRUCTIONS.reset(token)


def test_unchanged_model_response_has_explicit_feedback(window):
    window.unified_prompt.setPlainText('/refine-global Make the motion clearer\nOne continuous shot follows the traveler.')
    with patch.object(window,'ai_credentials',return_value=('gemini','model','key')), patch.object(window,'start_ai_worker') as worker:
        window.refine_prompt_button.click()
        worker.call_args.args[3]('One continuous shot follows the traveler.')
    assert '/refine-global Make the motion clearer' in window.unified_prompt.toPlainText()
    assert 'no text changes' in window.minimax_panel.cache_state.text()
    assert 'unchanged' in window.minimax_panel.message_banner.text()
    assert window.minimax_panel.message_panel.property('level') == 'warning'


def test_real_worker_and_gemini_transport_apply_refinement(window):
    import time
    from PySide6.QtCore import QEventLoop
    window.unified_prompt.setPlainText('/refine Make this a continuous shot\nA traveler enters. Cut to the lantern.')
    render_prompt_notes(window.unified_prompt)
    response = {'candidates':[{'content':{'parts':[{'text':json.dumps({'prompt':'A continuous tracking shot follows the traveler toward the lantern.\n/refine Make this a continuous shot'})}]}}]}
    with patch.object(window,'ai_credentials',return_value=('gemini','model','key')), patch.object(ai.requests,'post') as request:
        request.return_value.status_code = 200
        request.return_value.__enter__.return_value = request.return_value
        request.return_value.iter_lines.return_value = ['data: ' + json.dumps(response)]
        window.refine_prompt_button.click()
        deadline = time.monotonic() + 5
        while window.ai_busy and time.monotonic() < deadline:
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
            time.sleep(.01)
        assert not window.ai_busy
        assert request.call_count == 1
        sent = request.call_args.kwargs['json']['contents'][0]['parts'][0]['text']
        assert '/refine [whole prompt (leading annotation)]' in sent
        assert 'A traveler enters. Cut to the lantern.' in sent
        assert 'continuous tracking shot' in window.unified_prompt.toPlainText()
        assert window.minimax_panel.message_panel.property('level') == 'success'
