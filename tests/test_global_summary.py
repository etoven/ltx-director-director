"""Global input reaches requests and returned Summary reaches the native editor."""
import json
from unittest.mock import patch
from PySide6.QtCore import QEvent, QTimer
from PySide6.QtGui import QFocusEvent
from PySide6.QtWidgets import QApplication, QPlainTextEdit
from ltx_prompt_director import ai, ui
from ltx_prompt_director.models import Segment
from ltx_prompt_director.output_templates import request_context, template_contract, render_response, ACTIVE_OUTPUT
from ltx_prompt_director.prompt_templates import changed_direction_request


def test_global_generate_refine_and_summary_are_separate(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    with patch.object(ui.MainWindow, 'restore_startup_workspace'):
        w = ui.MainWindow()
    try:
        w.segments = [Segment('Opening', '', '', 'text', 'text', 'The courier enters.', 3)]
        w.refresh_timeline(0); w.set_project_type('minimax_official_skill_reference')
        panel = w.prompt_surface.structured; panel.refresh()
        state = panel.state(); state['global'] = 'A courier arrives in daylight.'; panel.refresh()
        w.intent.setPlainText('Change the scene to midnight and make the courier whisper.')
        panel.refresh()
        assert panel.rows['global'][0].toPlainText() == 'A courier arrives in daylight.'
        context = request_context(w.workspace_definition, panel.segments(), panel.subjects())
        assert 'finished narrative summary' in template_contract(context)['properties']['variables']['properties']['global_prompt']['description']
        monkeypatch.setattr(w, 'ai_credentials', lambda: ('gemini', 'model', 'key'))
        calls = []
        monkeypatch.setattr(w, 'start_ai_worker', lambda operation,args,activity,finished,**kw:calls.append((operation,args,finished)))
        w.prompt_surface.actions['unified'][1].click()
        operation,args,finished = calls[-1]
        assert operation is ai.build_minimax_h3_reference_prompt and 'midnight' in args[4]
        def output(summary, prompt):
            context = request_context(w.workspace_definition, panel.segments(), panel.subjects())
            raw = json.dumps(dict(variables=dict(global_prompt=summary,retention='Keep the entrance.',sound='Quiet steps.',music='None.'),
                segments=[dict(id=s.id,prompt=prompt) for s in panel.segments()],
                subjects=[dict(id=s['id'],prompt=s['prompt']) for s in panel.subjects()]))
            token = ACTIVE_OUTPUT.set(context)
            try:return ai._extract_minimax_h3_prompt(render_response(raw, context))
            finally:ACTIVE_OUTPUT.reset(token)
        finished(output('At midnight, a courier whispers as she enters.', 'The courier enters at midnight, whispering.'))
        panel.refresh()
        assert panel.rows['global'][0].toPlainText() == 'At midnight, a courier whispers as she enters.'
        w.intent.setPlainText('Change the scene to dawn and make the courier speak clearly.')
        panel.refresh()
        assert 'midnight' in panel.rows['global'][0].toPlainText()
        w.prompt_surface.actions['unified'][2].click()
        operation,args,finished = calls[-1]
        assert operation is ai.refine_minimax_h3_reference_prompt
        assert 'CURRENT GLOBAL EDIT REQUEST' in args[10] and 'dawn' in args[10]
        finished(output('At dawn, the courier speaks clearly as she enters.', 'The courier enters at dawn and speaks clearly.'))
        panel.refresh()
        assert panel.review_snapshot()['global'] == 'At dawn, the courier speaks clearly as she enters.'
        segment_id = w.segments[0].id
        segment_editor = panel.rows[segment_id][0]
        assert segment_editor._spell_highlighter
        assert panel.reviews[segment_editor].pending_count
        assert w.intent_segment_prompt.toPlainText() == w.segments[0].prompt == state['prompts'][segment_id]
        panel.reviews[segment_editor].resolve_all(False)
        assert w.intent_segment_prompt.toPlainText() == w.segments[0].prompt == 'The courier enters at midnight, whispering.'
        panel.finish_reviews()
        w.intent_segment_prompt.setPlainText('Segment dock edit')
        assert segment_editor.toPlainText() == 'Segment dock edit'
        segment_editor.setPlainText('Unified dock edit')
        assert w.intent_segment_prompt.toPlainText() == 'Unified dock edit'
        panel.refresh()
        assert state['prompts'][segment_id] == 'Unified dock edit'
        # Consumed Global edits do not get replayed over later manual omissions.
        assert changed_direction_request(w.production_direction_sources(), state['applied_direction_sources']) == ''
        w.prompt_surface.actions['unified'][2].click()
        assert 'CURRENT GLOBAL EDIT REQUEST' not in calls[-1][1][10]
        def submit_intent():
            dialog = QApplication.activeModalWidget()
            dialog.findChild(QPlainTextEdit).setPlainText('Keep the entry.\nMake the lighting warmer.')
            dialog.accept()
        QTimer.singleShot(0, submit_intent)
        w.prompt_surface.actions['unified'][2].menu().actions()[0].trigger()
        assert 'Keep the entry.\nMake the lighting warmer.' in calls[-1][1][10]
        w.prompt_surface.actions['segment'][2].click()
        assert calls[-1][0] is ai.refine_segment_prompt
        calls[-1][2](dict(prompt='Refined in Segment', duration=3))
        assert panel.review_snapshot()[segment_id] == w.intent_segment_prompt.toPlainText() == 'Refined in Segment'
        # Both visible Global buttons route the current guidance to the sequence.
        w.intent.setPlainText('Use a rainy evening and keep the camera still.')
        w.prompt_surface.actions['global'][1].click()
        assert calls[-1][0] is ai.build_prompts and 'rainy evening' in calls[-1][1][4]
        w.prompt_surface.actions['global'][2].click()
        assert calls[-1][0] is ai.refine_sequence_prompts and 'rainy evening' in calls[-1][1][4]
        calls[-1][2](dict(globalPrompt='Rainy evening production', segments=[dict(prompt='Rainy entrance', duration=3)]))
        assert calls[-1][0] is ui.sync_combined_from_timeline
        calls[-1][2](dict(prompt=output('Rainy evening summary', 'Rainy entrance')))
        assert panel.review_snapshot()[segment_id] == w.intent_segment_prompt.toPlainText() == 'Rainy entrance'
        assert panel.review_snapshot()['global'] == 'Rainy evening summary'

        from ltx_prompt_director.minimax_reference_widgets import MiniMaxReferenceSlot
        slot = MiniMaxReferenceSlot(1)
        slot.set_value(dict(kind='audio', name='Reference.wav', notes='Old notes'))
        changes = []
        slot.changed.connect(changes.append)
        slot.notes.setText('Draft')
        slot.notes.setText('Finished reference notes')
        assert not changes
        QApplication.sendEvent(slot.notes, QFocusEvent(QEvent.Type.FocusOut))
        assert [value['notes'] for value in changes] == ['Finished reference notes']

        w.current_project_id, w.current_project_name = 'first', 'First'
        w.cache_current_workspace()
        first = w.project_sessions['first']['state']
        second_segment = Segment('Other', '', '', 'text', 'text', 'Second entrance', 3)
        second = {**first, 'segments': [second_segment], 'directorIntent': 'Second direction',
                  'globalPrompt': 'Second production', 'minimaxPrompt': '', 'minimaxDrafts': {},
                  'promptTemplateStates': {}, 'renderGroupOptions': {}}
        w.project_sessions['second'] = dict(name='Second', dirty=False, state=second)
        monkeypatch.setattr(w, 'library_records', lambda: [
            dict(id='first', name='First', projectPath=str(tmp_path/'first.LTXD')),
            dict(id='second', name='Second', projectPath=str(tmp_path/'second.LTXD')),
        ])
        for project, direction, prompt, summary in (
            ('second', 'Second direction', 'Second entrance', ''),
            ('first', 'Use a rainy evening and keep the camera still.', 'Rainy entrance', 'Rainy evening summary'),
        ):
            w.open_library_project(project_id=project)
            panel.refresh()
            assert w.current_project_id == project
            assert w.intent.toPlainText() == direction
            assert panel.review_snapshot()['global'] == summary
            assert w.intent_segment_prompt.toPlainText() == panel.review_snapshot()[w.segments[0].id] == prompt
    finally:
        w.project_dirty=False; w._close_saves_queued=True; w.close()
