"""Global input reaches requests and returned Summary reaches the native editor."""
import json
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
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
        assert panel.rows['global'][0].toPlainText() == 'At dawn, the courier speaks clearly as she enters.'
        assert state['prompts'][w.segments[0].id] == 'The courier enters at dawn and speaks clearly.'
        # Consumed Global edits do not get replayed over later manual omissions.
        assert changed_direction_request(w.production_direction_sources(), state['applied_direction_sources']) == ''
        w.prompt_surface.actions['unified'][2].click()
        assert 'CURRENT GLOBAL EDIT REQUEST' not in calls[-1][1][10]
        # Both visible Global buttons route the current guidance to the sequence.
        w.intent.setPlainText('Use a rainy evening and keep the camera still.')
        w.prompt_surface.actions['global'][1].click()
        assert calls[-1][0] is ai.build_prompts and 'rainy evening' in calls[-1][1][4]
        w.prompt_surface.actions['global'][2].click()
        assert calls[-1][0] is ai.refine_sequence_prompts and 'rainy evening' in calls[-1][1][4]
    finally:
        w.project_dirty=False; w._close_saves_queued=True; w.close()
