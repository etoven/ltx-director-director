"""Splitting keeps the timeline and source intervals intact across workspaces."""
from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from ltx_prompt_director import ui
from ltx_prompt_director.models import Segment, split_segment, split_segment_parts
from ltx_prompt_director.segment_split import divide_segment_prompt
from ltx_prompt_director import ai
from ltx_prompt_director.workspaces import WorkspaceStore


@pytest.mark.parametrize("kind,role", [("image", "start"), ("image", "end"), ("text", "text"), ("video", "start")])
def test_split_preserves_content_source_and_following_timing(kind, role):
    original = Segment("Source", "original", "preview", kind, role, "Keep this action", 3.75,
                       media_duration_frames=240, trim_start=36, image_prompt="Keep identity")
    original._archive_source = ("project.LTXD", "media/original")
    before = original.to_dict()
    left, right = split_segment(original, 1.53)
    assert original.to_dict() == before
    assert left.id == original.id and right.id != original.id
    assert left.duration == pytest.approx(37 / 24, abs=0.000001)
    assert left.duration + right.duration == pytest.approx(original.duration)
    for half in (left, right):
        assert (half.prompt, half.image_prompt, half.kind, half.role) == (original.prompt, original.image_prompt, kind, role)
        assert (half.media_path, half.preview_path, half._archive_source) == ("original", "preview", original._archive_source)
        assert half.media_duration_frames == 240
    if kind == "video":
        assert (left.trim_start, right.trim_start) == (36, 73)
        assert left.shot_trimmed and right.shot_trimmed
    else:
        assert not left.shot_trimmed and not right.shot_trimmed


@pytest.mark.parametrize("offset", [-1, 0, 0.01, 2.99, 3, 4, float("nan"), float("inf")])
def test_split_rejects_empty_or_outside_intervals(offset):
    with pytest.raises(ValueError):
        split_segment(Segment("Beat", "", "", duration=3), offset)


@pytest.fixture
def window(tmp_path, monkeypatch, app):
    store = WorkspaceStore(tmp_path / "workspaces")
    monkeypatch.setattr(ui, "WorkspaceStore", lambda: store)
    monkeypatch.setattr(ui, "project_library_path", lambda: tmp_path / "projects")
    monkeypatch.setattr(ui.MainWindow, "restore_startup_workspace", lambda self: None)
    w = ui.MainWindow()
    w.segments = [Segment("Lead", "", "", "text", "text", "Opening", 1),
                  Segment("Beat", "", "", "text", "text", "Continue", 3),
                  Segment("Tail", "", "", "text", "text", "Ending", 2)]
    w.refresh_timeline(1)
    yield w
    w.project_dirty = False
    w._close_saves_queued = True
    w.close()
    app.processEvents()


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("mode", ["ltx", "minimax_base_guide", "minimax_official_skill_reference"])
def test_split_selects_right_half_and_roundtrips_without_rewriting_draft(window, mode):
    window.set_project_type(mode)
    window.unified_prompt.setPlainText("Keep my production draft")
    window.global_prompt.setPlainText("Keep my global direction")
    draft = window.unified_prompt.toPlainText()
    before = window.segments[-1].to_dict()
    segment_id = window.segments[1].id
    window.split_timeline_segment(segment_id, 1.25)
    assert [s.duration for s in window.segments] == [1, 1.25, 1.75, 2]
    assert window.segments[-1].to_dict() == before
    assert window.timeline.currentRow() == 2 and window.project_dirty
    if window.unified_workspace:
        assert window.unified_prompt.toPlainText() == draft
    assert window.global_prompt.toPlainText() == "Keep my global direction"
    assert window.timeline.item(2).data(Qt.ItemDataRole.UserRole) == window.segments[2].id
    payload = window.project_payload(include_media=False)
    window.load_project_payload(payload)
    assert [s.duration for s in window.segments] == [1, 1.25, 1.75, 2]
    assert window.segments[1].prompt == window.segments[2].prompt == "Continue"


def test_context_menu_splits_clicked_segment_at_scrolled_card_position(window):
    window.show()
    QApplication.processEvents()
    window.pixels_per_second = 160
    window.update_timeline_layout()
    window.timeline.horizontalScrollBar().setValue(70)
    QApplication.processEvents()
    rect = window.timeline.visualItemRect(window.timeline.item(1))
    point = QPoint(rect.left() + rect.width() // 3, rect.center().y())
    def choose(menu, *args):
        splits = next(a.menu() for a in menu.actions() if a.text() == "Split segment")
        action = next(a for a in splits.actions() if a.text() == "At clicked position")
        assert action.isEnabled()
        action.trigger()
    class TestMenu(ui.QMenu):
        def exec(self, *args):
            return choose(self, *args)
    with patch.object(ui, "QMenu", TestMenu):
        window.timeline_menu(point)
    assert len(window.segments) == 4
    assert window.segments[1].duration == pytest.approx(1, abs=1 / 24)
    assert sum(s.duration for s in window.segments) == pytest.approx(6)


@pytest.mark.parametrize("count", [2, 3])
def test_equal_split_keeps_source_trims_ids_and_total_duration(count):
    source = Segment('clip', 'original.mp4', 'preview.jpg', 'video', 'end',
                     'Lift the box, then set it down.', 3.79, trim_start=37)
    source._archive_source = ('project.LTXD', 'media/0000.mp4')
    before = source.to_dict()
    parts = split_segment_parts(source, count)
    assert source.to_dict() == before
    assert len(parts) == len({part.id for part in parts}) == count
    assert parts[0].id == source.id
    assert sum(part.duration for part in parts) == pytest.approx(source.duration)
    for index, part in enumerate(parts):
        assert part._archive_source == source._archive_source
        assert (part.media_path, part.preview_path, part.role, part.prompt) == (
            source.media_path, source.preview_path, source.role, source.prompt)
        assert part.trim_start == 37 + round(sum(p.duration for p in parts[:index]) * 24)
        assert part.shot_trimmed


@pytest.mark.parametrize("count,duration", [(2, .05), (3, .08), (4, 4)])
def test_equal_split_rejects_too_short_or_unsupported_count(count, duration):
    with pytest.raises(ValueError):
        split_segment_parts(Segment('tiny', '', '', duration=duration), count)


@pytest.mark.parametrize("count", [2, 3])
@pytest.mark.parametrize("style", ['divide', 'cuts'])
def test_prompt_split_sends_selected_beats_roles_and_neighbors(count, style):
    import json
    segments = [Segment('previous', '', '', 'text', prompt='Earlier context.'),
                Segment('target', '', '', 'image', 'end', 'Raises a hand. Says "Go!". Then runs.', 6),
                Segment('next', '', '', 'text', prompt='Later context.')]
    before = [part.to_dict() for part in segments]
    proposed = ['Raises a hand.', 'Says "Go!" and runs.'] if count == 2 else ['Raises a hand.', 'Says "Go!".', 'Runs.']
    with patch.object(ai, '_provider_raw', return_value=json.dumps(dict(prompts=proposed))) as provider:
        prompts = divide_segment_prompt(segments, 1, count, style, 'gemini', 'model', 'key')
    assert len(prompts) == count
    inputs, name, model, key, rules, timeout = provider.call_args.args
    assert [item['selected'] for item in inputs] == [False, True, False]
    assert inputs[1]['role'] == 'end'
    assert 'Earlier context.' in rules and 'Later context.' in rules
    assert segments[1].prompt in rules
    assert 'exact dialogue' in rules and 'duration is fixed' in rules
    if style == 'cuts':
        assert all(prompt.startswith('Hard cut to a close-up') for prompt in prompts[1:])
        assert 'hard shot cuts between action beats' in rules
    else:
        assert prompts == proposed
        assert 'Keep the action continuous' in rules
    assert [part.to_dict() for part in segments] == before


@pytest.mark.parametrize('reply', ['{}', '{"prompts":["One"]}', '{"prompts":["One",""]}',
                                  '{"prompts":["One",3]}', '{"prompts":["One","Two","Three"]}'])
def test_prompt_split_rejects_incomplete_output_without_changing_source(reply):
    source = Segment('beat', '', '', 'text', prompt='One then two.')
    before = source.to_dict()
    with patch.object(ai, '_provider_raw', return_value=reply), pytest.raises(ai.AIResponseFormatError):
        divide_segment_prompt([source], 0, 2, 'divide', 'openai', 'model', 'key')
    assert source.to_dict() == before


@pytest.mark.parametrize('count,style', [(2, 'keep'), (3, 'keep'), (2, 'divide'), (3, 'cuts')])
def test_split_menu_routes_counts_and_styles(window, count, style):
    window.show()
    QApplication.processEvents()
    rect = window.timeline.visualItemRect(window.timeline.item(1))
    def choose(menu, *args):
        splits = next(a.menu() for a in menu.actions() if a.text() == 'Split segment')
        choices = next(a.menu() for a in splits.actions() if a.text() == f'{count}-way split')
        title = dict(keep='Keep prompt', divide='Divide prompt with AI', cuts='Hard cuts + close-ups with AI')[style]
        action = next(a for a in choices.actions() if a.text() == title)
        assert action.isEnabled()
        action.trigger()
    class TestMenu(ui.QMenu):
        def exec(self, *args):
            return choose(self, *args)
    with patch.object(ui, 'QMenu', TestMenu), patch.object(window, 'split_timeline_segment_parts') as split:
        window.timeline_menu(rect.center())
    split.assert_called_once_with(window.segments[1].id, count, style)


@pytest.mark.parametrize('count', [2, 3])
@pytest.mark.parametrize('approved', [True, False])
def test_ai_split_is_atomic_review_and_preserves_other_editors(window, count, approved):
    window.set_project_type('minimax_references')
    window.unified_prompt.setPlainText('Keep unified draft')
    window.global_prompt.setPlainText('Keep direction')
    before = [s.to_dict() for s in window.segments]
    source_id = window.segments[1].id
    with patch.object(window, 'ai_credentials', return_value=('gemini', 'model', 'key')), patch.object(window, 'start_ai_worker') as worker:
        window.split_timeline_segment_parts(source_id, count, 'cuts')
    assert [s.to_dict() for s in window.segments] == before
    assert worker.call_args.kwargs['review_text'] is False
    assert worker.call_args.args[0] is divide_segment_prompt
    callback = worker.call_args.args[3]
    callback([f'Part {i + 1}' for i in range(count)])
    assert len(window.segments) == 2 + count
    assert sum(s.duration for s in window.segments) == pytest.approx(6)
    assert window.segments[0].to_dict() == before[0]
    assert window.segments[-1].to_dict() == before[-1]
    assert len(window.timeline_review_pending) == count
    assert window.unified_prompt.toPlainText() == 'Keep unified draft'
    assert window.global_prompt.toPlainText() == 'Keep direction'
    window.resolve_segment_review(window.segments[2].id, approved)
    assert not getattr(window, 'timeline_review_pending', set())
    if not approved:
        assert [s.to_dict() for s in window.segments] == before
    else:
        assert len(window.segments) == 2 + count
        assert [s.prompt for s in window.segments[1:count + 1]] == [f'Part {i + 1}' for i in range(count)]


def test_ai_split_discards_result_if_timeline_changes_and_missing_credentials_leave_it_intact(window):
    source_id = window.segments[1].id
    before = [s.to_dict() for s in window.segments]
    with patch.object(window, 'ai_credentials', return_value=None), patch.object(window, 'start_ai_worker') as worker:
        window.split_timeline_segment_parts(source_id, 2, 'divide')
    worker.assert_not_called()
    assert [s.to_dict() for s in window.segments] == before
    with patch.object(window, 'ai_credentials', return_value=('gemini', 'model', 'key')), patch.object(window, 'start_ai_worker') as worker:
        window.split_timeline_segment_parts(source_id, 2, 'divide')
    window.segments[1].prompt = 'New manual edit'
    worker.call_args.args[3](['Old first', 'Old second'])
    assert len(window.segments) == 3 and window.segments[1].prompt == 'New manual edit'
    assert not getattr(window, 'timeline_review_pending', set())


@pytest.mark.parametrize('count', [2, 3])
def test_equal_split_in_editor_is_immediate_without_provider(window, count):
    source = window.segments[1]
    with patch.object(window, 'start_ai_worker') as worker:
        window.split_timeline_segment_parts(source.id, count)
    worker.assert_not_called()
    assert len(window.segments) == 2 + count
    assert [s.prompt for s in window.segments[1:count + 1]] == ['Continue'] * count
    assert sum(s.duration for s in window.segments) == pytest.approx(6)


@pytest.mark.parametrize('outcome', ['success', 'failure', 'project_switch'])
def test_actual_split_worker_keeps_progress_and_review_in_segment(window, outcome):
    window.set_project_type('minimax_references')
    window.show()
    QApplication.processEvents()
    window.unified_prompt.setPlainText('Keep combined prompt')
    before = [s.to_dict() for s in window.segments]
    with patch.object(window, 'ai_credentials', return_value=('gemini', 'model', 'key')), patch.object(window.thread_pool, 'start') as pool:
        window.split_timeline_segment_parts(window.segments[1].id, 3, 'cuts')
    worker = pool.call_args.args[0]
    assert window.ai_request_editor is window.intent_segment_prompt
    assert window.ai_busy and not window.inline_typing.active
    worker.signals.stream.emit('output', '{"prompts":["First",')
    assert [s.to_dict() for s in window.segments] == before
    assert window.unified_prompt.toPlainText() == 'Keep combined prompt'

    if outcome == 'project_switch':
        window.workspace_revision += 1
        worker.signals.finished.emit(['First', 'Second', 'Third'])
    elif outcome == 'failure':
        with patch.object(ui.QMessageBox, 'critical'):
            worker.signals.failed.emit('Provider unavailable')
    else:
        with patch.object(ai, '_provider_raw', return_value='{"prompts":["First","Second","Third"]}'):
            worker.signals.finished.emit(worker.operation(*worker.args))
    assert not window.ai_busy
    if outcome == 'success':
        assert len(window.segments) == 5
        assert len(window.timeline_review_pending) == 3
        assert window.paragraph_review.editor is None
        window.resolve_all_segment_reviews(False)
    assert [s.to_dict() for s in window.segments] == before
    assert window.unified_prompt.toPlainText() == 'Keep combined prompt'


@pytest.mark.parametrize('kind', ['image', 'video'])
def test_convert_to_text_preserves_writing_timing_identity_and_drops_media(window, tmp_path, kind):
    source = tmp_path / 'original.mp4'
    source.write_bytes(b'original media')
    selected = window.segments[1]
    selected.kind, selected.role = kind, 'end'
    selected.media_path = str(source)
    selected.preview_path = 'preview.jpg'
    selected.trim_start, selected.media_duration_frames, selected.shot_trimmed = 24, 240, True
    selected.image_prompt = 'Keep visual direction'
    selected.artwork_status, selected.artwork_prompt = 'complete', 'Old image request'
    selected._archive_source = ('archive.LTXD', 'media/0001.mp4')
    selected._media_digest = 'digest'
    before = [s.to_dict() for s in window.segments]
    window.refresh_timeline(1)
    window.convert_selected_segment_to_text()
    converted = window.segments[1]
    assert converted.id == before[1]['id']
    assert (converted.kind, converted.role) == ('text', 'text')
    assert (converted.prompt, converted.image_prompt, converted.duration) == ('Continue', 'Keep visual direction', 3)
    assert not converted.media_path and not converted.preview_path
    assert converted.trim_start is None and converted.media_duration_frames is None
    assert not converted.shot_trimmed and not converted.artwork_status and not converted.artwork_prompt
    assert not hasattr(converted, '_archive_source') and not hasattr(converted, '_media_digest')
    assert source.read_bytes() == b'original media'
    assert window.segments[0].to_dict() == before[0] and window.segments[-1].to_dict() == before[-1]
    payload = window.project_payload(include_media=False)
    window.load_project_payload(payload)
    assert window.segments[1].to_dict() == converted.to_dict()


def test_workspace_shot_limit_blocks_adds_and_splits_and_allows_unlimited(window):
    window.workspace_definition['max_shots'] = 3
    before = [s.to_dict() for s in window.segments]
    window.add_text_segment()
    window.split_timeline_segment(window.segments[1].id, 1)
    with patch.object(window, 'start_ai_worker') as worker:
        window.split_timeline_segment_parts(window.segments[1].id, 3, 'cuts')
    worker.assert_not_called()
    assert [s.to_dict() for s in window.segments] == before
    window.workspace_definition['max_shots'] = 4
    window.split_timeline_segment_parts(window.segments[1].id, 2)
    assert len(window.segments) == 4
    window.workspace_definition['max_shots'] = 0
    assert window.timeline_count_allowed(150)
    window.add_text_segment()
    assert len(window.segments) == 5
    window.workspace_definition['max_shots'] = 2
    assert len(window.segments) == 5
    assert window.timeline_count_allowed(5) and not window.timeline_count_allowed(6)


def test_deny_split_preserves_neighbor_edits_made_during_review(window):
    import copy
    before = copy.deepcopy(window.segments)
    window.finish_segment_split(before, 1, 3, ['First', 'Second', 'Third'])
    window.segments[-1].prompt = 'A later manual neighbor edit'
    window.resolve_all_segment_reviews(False)
    assert len(window.segments) == 3
    assert window.segments[1].to_dict() == before[1].to_dict()
    assert window.segments[-1].prompt == 'A later manual neighbor edit'


def test_shot_limit_editor_persists_and_portable_project_keeps_unlimited(window):
    from ltx_prompt_director.workspace_editor import WorkspaceEditor
    editor = WorkspaceEditor(window.workspace_store, window)
    definition = dict(window.workspace_definitions['minimax_references'])
    assert definition['max_shots'] == 9
    editor.populate(definition)
    assert editor.max_shots.value() == 9
    editor.current_id = None
    editor.identifier.setText('unlimited_reference')
    editor.name.setText('Unlimited Reference')
    editor.max_shots.setValue(0)
    editor.save()
    assert window.workspace_store.load()['unlimited_reference']['max_shots'] == 0
    window.set_project_type('unlimited_reference')
    assert window.timeline_count_allowed(120)
    payload = window.project_payload(include_media=False)
    assert payload['workspaceDefinition']['max_shots'] == 0
    window.set_project_type('minimax_references')
    window.load_project_payload(payload)
    assert window.workspace_definition['max_shots'] == 0


def test_shot_limit_rechecks_background_media_completion(window, tmp_path):
    source = tmp_path / 'source.png'
    from PIL import Image
    Image.new('RGB', (24, 16), 'blue').save(source)
    window.workspace_definition['max_shots'] = 4
    with patch.object(window, 'start_disk_job') as job:
        window.add_media_paths([str(source)])
    assert job.call_count == 1
    window.add_text_segment()
    job.call_args.args[2](([(str(source), 'image', str(source), None, None)], []))
    assert len(window.segments) == 4


def test_split_waits_for_review_and_artwork_and_rejects_edges(window):
    original = [s.to_dict() for s in window.segments]
    segment_id = window.segments[1].id
    window.timeline_review_pending = {segment_id}
    window.split_timeline_segment(segment_id, 1)
    window.timeline_review_pending.clear()
    window.segments[1].artwork_status = "generating"
    window.split_timeline_segment(segment_id, 1)
    window.segments[1].artwork_status = ""
    window.split_timeline_segment(segment_id, 0)
    assert [s.to_dict() for s in window.segments] == original


def test_fit_control_is_visible_by_timeline_and_fits_the_complete_sequence(window):
    from PySide6.QtTest import QTest
    window.resize(1150, 820)
    window.show()
    QApplication.processEvents()
    window.timeline_scale.setValue(160)
    window.timeline.horizontalScrollBar().setValue(70)
    QTest.mouseClick(window.timeline_fit_button, Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert window.timeline_fit_mode
    assert window.timeline.horizontalScrollBar().value() == 0
    assert sum(window.timeline_item_widths()) <= window.timeline.viewport().width()
    assert window.timeline_fit_button.parent() is window.timeline_navigation
    assert window.timeline_navigation.parent() is window.timeline_shell
    assert window.timeline_fit_button.accessibleName() == "Fit timeline"
    assert window.timeline_fit_button.isVisible()
    window.timeline_scale.setValue(120)
    assert not window.timeline_fit_mode
