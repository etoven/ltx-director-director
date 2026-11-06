"""Small native smoke for the feature plan; no provider calls or model loading.

Run with QT_QPA_PLATFORM=offscreen, or under Xvfb with xcb for screenshots.
Pass an output directory to save the native demonstration panels.
"""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import wave
from unittest.mock import patch

if not os.environ.get('DIRECTOR_SMOKE_INSTALLED'):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    root = Path(tempfile.mkdtemp(prefix='director-feature-plan-'))
    for name in ('CONFIG', 'DATA', 'CACHE'):
        os.environ['XDG_' + name + '_HOME'] = str(root / name.lower())
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from ltx_prompt_director import ai
    from ltx_prompt_director.ui import MainWindow, SettingsDialog
    from ltx_prompt_director.models import Segment
    from ltx_prompt_director.output_templates import ACTIVE_OUTPUT, request_context, parse_rendered, root_fields
    from ltx_prompt_director.audio_generation import KEY, load_audio_settings, music_workflow
    from ltx_prompt_director.effects_widgets import EffectDialog
    from ltx_prompt_director.onboarding import OnboardingWizard
    app = QApplication([])
    window = MainWindow(); window.show(); QTest.qWait(80)
    from ltx_prompt_director.workspaces import WorkspaceStore, FACTORY_IDS
    assert set(window.workspace_definitions) == FACTORY_IDS
    assert window.workspace_definitions['minimax_prompt_refinement']['name'] == 'Generic Prompt Refine'
    assert window.unified_prompt_dock.isHidden()
    assert 'output_template' not in window.workspace_definition
    minimax = next(key for key, value in window.workspace_definitions.items() if value['engine'] == 'minimax_references')
    window.set_project_type(minimax)
    window.current_project_name = 'Evening courier'
    window.segments = [Segment('Opening', '', '', kind='text', role='text', prompt='The courier arrives.', duration=3),
                       Segment('Ending', '', '', kind='text', role='text', prompt='The beacon lights.', duration=4)]
    window.refresh_timeline(); window.mark_dirty(); QTest.qWait(20)
    panel = window.prompt_surface.structured
    first, second = window.segments
    window.intent_segment_prompt.setFocus(); window.intent_segment_prompt.setPlainText('The courier enters quietly.'); QTest.qWait(20)
    assert 'enters quietly' in window.unified_prompt.toPlainText()
    panel.rows[second.id][0].setPlainText('The beacon glows blue.'); QTest.qWait(20)
    assert second.prompt == 'The beacon glows blue.'
    panel.rows[first.id][2].setText('00:03.50'); panel.edit_time(first.id, True); QTest.qWait(20)
    assert first.duration == 3.5
    window.segments.reverse(); window.refresh_timeline(); window.mark_dirty(); QTest.qWait(20)
    output = window.unified_prompt.toPlainText()
    assert output.index('beacon') < output.index('courier')
    panel.add_subject(); QTest.qWait(20); assert len(panel.subjects()) == 1
    panel.add_segment(); QTest.qWait(20); assert len(window.segments) == 3
    window.intent.setPlainText('A quiet blue evening. Preserve natural movement and soft city ambience.'); QTest.qWait(20)
    assert 'quiet blue evening' in window.unified_prompt.toPlainText()
    original_layout = window.workspace_definition['output_template']
    custom_layout = ('[MUSIC FIRST]\n${music}\n\n{{#segments}}[Beat ${index}] ${start} – ${end}\n${prompt}\n\n{{/segments}}'
                     + '{{#subjects}}[Identity ${name}]\n${prompt}\n{{/subjects}}\n[SCENE LAST]\n${global_prompt}')
    window.workspace_definition['output_template'] = custom_layout
    panel.state()['fields']['music'] = 'A sparse piano theme.'
    window.mark_dirty(); QTest.qWait(20)
    assert list(panel.rows)[0] == 'field:music' and list(panel.rows)[-1] == 'global'
    assert window.unified_prompt.toPlainText().startswith('[MUSIC FIRST]\nA sparse piano theme.')
    window.workspace_definition['output_template'] = original_layout
    window.mark_dirty(); QTest.qWait(20)
    state = copy.deepcopy(window.prompt_template_states)
    payload = window.project_payload(include_media=False)
    assert payload['promptTemplateStates'] == state
    window.prompt_template_states = {}
    window.load_project_payload(payload); QTest.qWait(20)
    assert window.prompt_template_states == state
    settings = SettingsDialog(window.settings, window)
    assert settings.audio_models.tabs.count() == 3
    workspace_editor = settings.tabs.widget(3).widget()
    assert workspace_editor.output_template.canvas_area.isHidden()
    workspace_editor.list.setCurrentRow(next(i for i in range(workspace_editor.list.count()) if workspace_editor.list.item(i).data(Qt.ItemDataRole.UserRole) == minimax))
    assert '{{#segments}}' in workspace_editor.output_template.value()
    from PySide6.QtGui import QTextCursor
    from ltx_prompt_director.template_blocks import ContainerBlock, TextBlock
    kit = workspace_editor.output_template
    canvas = kit.editor
    original = kit.value()
    assert canvas.toPlainText() == original
    named = canvas.root.add_node(dict(type='text', text='${weather_notes}'))
    cursor=named.editor.textCursor();cursor.movePosition(QTextCursor.MoveOperation.End)
    named.editor.setTextCursor(cursor);named.editor.insertPlainText(' Keep the weather consistent.')
    assert named.editor.source() == '${weather_notes} Keep the weather consistent.'
    assert canvas.normalize_field_name('Weather notes') is None
    assert canvas.normalize_field_name('New camera notes') == 'new_camera_notes'
    groups = {block.kind: block for block in canvas.registry.values() if isinstance(block, ContainerBlock) and block.kind != 'section'}
    assert groups['segments'].zone.can_accept({'palette':'start'})
    assert not groups['subjects'].zone.can_accept({'palette':'start'})
    first_container = next(block for block in canvas.root.nodes if isinstance(block, ContainerBlock))
    assert not first_container.zone.can_accept({'move':first_container.id})
    canvas.apply_drop({'palette':'section'}, first_container.zone)
    nested = first_container.zone.nodes[-1]; nested.heading.setText('Camera')
    nested.zone.add_node(dict(type='text', text='Maintain a steady camera.'))
    assert nested.parent_zone.owner_block is first_container
    canvas.apply_drop({'move':nested.id}, canvas.root, 0)
    assert canvas.root.nodes[0] is nested and nested.zone.nodes[0].editor.source() == 'Maintain a steady camera.'
    text_block = next(block for block in groups['segments'].zone.nodes if isinstance(block,TextBlock))
    assert '${prompt}' in text_block.editor.source() and '${' not in text_block.editor.toPlainText()
    canvas.apply_drop({'palette':'duration'}, groups['segments'].zone)
    assert '${duration}' in kit.value()
    kit.preview_button.click()
    assert 'Maintain a steady camera.' in kit.preview.toPlainText()
    assert '${' not in kit.preview.toPlainText() and '{{#' not in kit.preview.toPlainText()
    kit.preview_button.click(); kit.save_button.click(); QTest.qWait(20)
    assert 'Maintain a steady camera.' in window.workspace_definition['output_template']
    saved_layout = kit.value()
    for _ in range(3):
        kit.save_button.click();QTest.qWait(20)
        assert kit.value() == saved_layout
        assert window.workspace_definition['output_template'] == saved_layout
    panel.refresh()
    rendered = window.unified_prompt.toPlainText()
    assert rendered.count('summary:') == 1, rendered
    assert rendered.count('detailed_description:') == 1, rendered
    assert named.id not in kit.editor.registry  # Saved canvases replace their old widgets.

    settings.reject(); QTest.qWait(10)
    window.provider_button.setCurrentIndex(window.provider_button.findData(('openai', 'gpt-6-mini')))
    assert window.settings.value('provider') == 'openai'
    window.session_keys['openai'] = 'smoke-test-only'
    window.session_keys['gemini'] = 'smoke-test-only'
    captured = []
    with patch.object(window, '_apply_quick_fix', side_effect=lambda *_: captured.append(window.ai_credentials()[:2])):
        window.apply_quick_fix(dict(refinement_model=dict(provider='gemini', model='example-model')), 'segment')
    assert captured == [('gemini', 'example-model')]
    assert window.settings.value('provider') == 'openai'
    dialog = EffectDialog(window); assert dialog.refinement_model.currentData() is None; dialog.reject()
    onboarding = OnboardingWizard(window); assert onboarding.audio_models.tabs.count() == 3; onboarding.reject()
    context = request_context(window.workspace_definition, window.segments, panel.subjects())
    variables = dict(variables={name: 'Scene' if name == 'global_prompt' else '' for name in root_fields(context['layout'])}, subjects=[dict(id=x['id'], prompt='Identity') for x in panel.subjects()],
                     segments=[dict(id=x.id, prompt='AI part ' + str(i)) for i, x in enumerate(window.segments)])
    contracts = []
    def transport(*_):
        contracts.append(ai.response_schemas.current())
        return json.dumps(variables)
    with patch.object(ai, '_provider_raw', side_effect=transport):
        token = ACTIVE_OUTPUT.set(context)
        try:
            output = ai._structured_raw([], 'gemini', 'demo', '', 'rules', 10, schema=ai.response_schemas.PROMPT)
        finally:
            ACTIVE_OUTPUT.reset(token)
    assert contracts[0]['title'] == 'production_template_variables'
    assert parse_rendered(json.loads(output)['prompt'], context['layout'], context['segments'], context['subjects'])
    timeline_variables = copy.deepcopy(variables)
    timeline_variables['segments'] = [dict(id=x.id, name=x.name, kind=x.kind, role=x.role, duration=x.duration,
                                          prompt='Structured beat', imagePrompt='', artworkPrompt='') for x in window.segments]
    with patch.object(ai, '_provider_raw', return_value=json.dumps(timeline_variables)):
        token = ACTIVE_OUTPUT.set(context)
        try:
            combined = ai._structured_raw([], 'gemini', 'demo', '', 'rules', 10, schema=ai.response_schemas.timeline(combined=True))
        finally:
            ACTIVE_OUTPUT.reset(token)
    assert 'Structured beat' in json.loads(combined)['prompt']

    migration = WorkspaceStore(root / 'workspace-migration')
    from importlib.resources import files
    retired = json.loads(files('ltx_prompt_director').joinpath('workspace_templates/minimax_base_guide.json').read_text())
    migration.save(retired)
    customized = json.loads(files('ltx_prompt_director').joinpath('workspace_templates/minimax_full_reference_guide.json').read_text())
    customized['name'] = 'My custom guide'; migration.save(customized)
    (migration.root / '.stock-official-compact-v1').unlink()
    upgraded = WorkspaceStore(migration.root).load()
    assert 'minimax_base_guide' not in upgraded and upgraded['minimax_full_reference_guide']['name'] == 'My custom guide'
    graph = music_workflow(window.audio_generation_panels['music'].request(), 'Evening courier')
    assert next(n for n in graph['nodes'] if n['id'] == 37)['widgets_values'][4]
    # A recorded fixture exercises playback/export and stale-result handling.
    # It is never presented as audio generated by one of the models.
    fixture = root / 'fixture.wav'
    with wave.open(str(fixture), 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000); audio.writeframes(b'\0\0' * 1600)
    config = load_audio_settings(window.settings)
    config['dialog'].update(source=str(root), python=sys.executable, model=str(root))
    window.settings.setValue(KEY, json.dumps(config))
    audio_panel = window.audio_generation_panels['dialog']
    audio_panel.prompt.setPlainText('The beacon is ready.'); audio_panel.sample.setText(str(fixture))
    def wait_queue():
        deadline = time.monotonic() + 5
        while window.render_queue.active is not None or window._pending_disk_jobs:
            assert time.monotonic() < deadline, 'Background job did not finish.'
            QTest.qWait(10)
    from ltx_prompt_director import audio_widgets
    with patch.object(audio_widgets, 'generate_audio', return_value={'path': str(fixture)}):
        audio_panel.start(); wait_queue()
    assert audio_panel.output_path == str(fixture) and audio_panel.play.isEnabled()
    window.download_directory = str(root / 'exports')
    audio_panel.export_audio(); wait_queue()
    exports = list((root / 'exports').glob('*.wav')); assert exports and exports[0].read_bytes() == fixture.read_bytes()
    audio_panel.output_path = 'previous-output'
    with patch.object(audio_widgets, 'generate_audio', return_value={'path': str(fixture)}):
        audio_panel.start(); audio_panel.prompt.insertPlainText(' changed'); wait_queue()
    assert audio_panel.output_path == 'previous-output'
    audio_panel.output_path = str(fixture)
    # Verify the model-specific workspace keeps all three panels available.
    minimax = next(key for key, value in window.workspace_definitions.items() if value['engine'] == 'minimax_references')
    window.set_project_type(minimax); window.timeline.setCurrentRow(0); QTest.qWait(20)
    window.intent_segment_prompt.setPlainText('The beacon casts a soft blue light.'); QTest.qWait(20)
    assert 'soft blue light' in window.unified_prompt.toPlainText()
    window.timeline.setCurrentRow(0); QTest.qWait(20)
    scroll = panel.scroll.verticalScrollBar(); scroll.setValue(scroll.maximum()); QTest.qWait(10)
    window.reload_clicked_segment(window.timeline.item(0)); QTest.qWait(100)
    first_row = panel.rows[window.segments[0].id][0].parentWidget()
    assert abs(scroll.value() - min(scroll.maximum(), max(0, first_row.y() - 4))) <= 5, (scroll.value(), scroll.maximum(), first_row.y(), window.timeline.currentRow(), panel.selected_id)

    assert window.timeline_dock.widget() is window.editor_root
    assert window.intent_prompt_splitter.isHidden()
    assert window.editor_root.layout().indexOf(window.intent_prompt_splitter) < 0
    window.timeline_dock.setFloating(True);QTest.qWait(20)
    assert window.timeline_dock.isFloating()
    window.timeline_dock.setFloating(False);window.timeline_dock.show();QTest.qWait(20)
    assert window.ruler_spacer.height() <= 36 and window.timeline_navigation.height() <= 36
    for dock in window.prompt_surface.docks.values():
        assert not dock.isHidden()
    if len(sys.argv) > 1:
        directory = Path(sys.argv[1]); directory.mkdir(parents=True, exist_ok=True)
        window.resize(1700, 1050); window.resizeDocks(list(window.prompt_surface.docks.values()), [450] * 3, Qt.Orientation.Vertical)
        window.render_queue_dock.hide(); window.timeline.setCurrentRow(0); QTest.qWait(60)
        window.grab().save(str(directory / 'prompt-panels.png'))
        music = window.audio_generation_panels['music']
        music.prompt.setPlainText('Instrumental acoustic guitar and warm piano, gentle pacing, growing into a hopeful final section.')
        window.audio_generation_docks['music'].show(); window.audio_generation_docks['music'].raise_(); QTest.qWait(40)
        window.grab().save(str(directory / 'audio-panels.png'))
        settings = SettingsDialog(window.settings, window)
        workspace_editor = settings.tabs.widget(3).widget()
        workspace_editor.list.setCurrentRow(next(i for i in range(workspace_editor.list.count()) if workspace_editor.list.item(i).data(Qt.ItemDataRole.UserRole) == minimax))
        settings.surface.select_workspace_section(1); settings.show(); QTest.qWait(40)
        settings.grab().save(str(directory / 'workspace-templates.png')); settings.reject()
        window.audio_generation_docks['music'].hide(); window.set_project_type('ltx'); QTest.qWait(40)
        assert window.unified_prompt_dock.isHidden()
        window.grab().save(str(directory / 'ltx-prompt-panels.png'))
    print('PASS: native prompt panels, live sections, timing, reorder/add, project persistence, AI variables, settings/onboarding, model switching, effect override, music workflow, fixture playback/export and stale audio results.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        import traceback
        traceback.print_exc(); sys.stdout.flush(); sys.stderr.flush(); os._exit(1)
    # Match CI's native process boundary; animation/media timers need no teardown.
    sys.stdout.flush(); sys.stderr.flush(); os._exit(0)
