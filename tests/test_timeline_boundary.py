"""Rolling a divider transfers time without shifting the rest of the sequence."""
import copy
from unittest.mock import patch

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ltx_prompt_director import ui
from ltx_prompt_director.models import Segment, roll_segment_boundary
from ltx_prompt_director.workspaces import WorkspaceStore
from ltx_prompt_director.timeline_timing import plan_sequence_timing, retimed_segments, TIMING_FIELDS
from ltx_prompt_director import ai


@pytest.mark.parametrize('left_kind,right_kind', [('image', 'text'), ('text', 'image'), ('text', 'text'), ('video', 'video')])
def test_boundary_preserves_pair_total_metadata_and_source_end(left_kind, right_kind):
    left = Segment('left', 'left-source', 'left-preview', left_kind, 'end', 'First beat', 3,
                   media_duration_frames=240, trim_start=24, image_prompt='Identity')
    right = Segment('right', 'right-source', 'right-preview', right_kind, 'start', 'Second beat', 2,
                    media_duration_frames=240, trim_start=48)
    left._archive_source = ('project.LTXD', 'media/left')
    right._archive_source = ('project.LTXD', 'media/right')
    before = [item.to_dict() for item in (left, right)]
    a, b = roll_segment_boundary(left, right, .75)
    assert [item.to_dict() for item in (left, right)] == before
    assert (a.duration, b.duration) == (3.75, 1.25)
    assert a.duration + b.duration == left.duration + right.duration
    for original, updated in ((left, a), (right, b)):
        for field in ('id', 'name', 'kind', 'role', 'media_path', 'preview_path', 'prompt', 'image_prompt'):
            assert getattr(original, field) == getattr(updated, field)
        assert original._archive_source == updated._archive_source
    if right_kind == 'video':
        assert a.trim_start == 24 and b.trim_start == 66
        assert b.trim_start / 24 + b.duration == right.trim_start / 24 + right.duration
        assert a.shot_trimmed and b.shot_trimmed


@pytest.mark.parametrize('offset', [-100, 100, 1 / 24, -1 / 24, 0])
def test_boundary_clamps_to_frames_without_changing_fractional_total(offset):
    a, b = roll_segment_boundary(Segment('a', '', '', duration=.5625),
                                 Segment('b', '', '', duration=1.6875), offset)
    assert a.duration >= 1 / 24 - .000001
    assert b.duration >= 1 / 24 - .000001
    assert a.duration + b.duration == pytest.approx(2.25)
    assert (a.duration - .5625) * 24 == pytest.approx(round((a.duration - .5625) * 24), abs=.00002)


def test_video_boundary_stops_at_source_handles_and_zero_does_not_change_trims():
    left = Segment('a', '', '', 'video', duration=2, trim_start=24, media_duration_frames=96)
    right = Segment('b', '', '', 'video', duration=2, trim_start=6, media_duration_frames=120)
    a, b = roll_segment_boundary(left, right, -100)
    assert (a.duration, b.duration, b.trim_start) == (1.75, 2.25, 0)
    a, b = roll_segment_boundary(left, right, 100)
    assert (a.duration, b.duration, b.trim_start) == (3, 1, 30)
    assert a.trim_start / 24 + a.duration == left.media_duration_frames / 24
    a, b = roll_segment_boundary(left, right, 0)
    assert (a.to_dict(), b.to_dict()) == (left.to_dict(), right.to_dict())


@pytest.mark.parametrize('offset', [float('nan'), float('inf'), -float('inf')])
def test_boundary_rejects_invalid_offsets(offset):
    with pytest.raises(ValueError):
        roll_segment_boundary(Segment('a', '', ''), Segment('b', '', ''), offset)


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(tmp_path, monkeypatch, app):
    store = WorkspaceStore(tmp_path / 'workspaces')
    monkeypatch.setattr(ui, 'WorkspaceStore', lambda: store)
    monkeypatch.setattr(ui, 'project_library_path', lambda: tmp_path / 'projects')
    monkeypatch.setattr(ui.MainWindow, 'restore_startup_workspace', lambda self: None)
    w = ui.MainWindow()
    w.segments = [Segment('Opening', '', '', 'text', 'text', 'Opening action', 3),
                  Segment('Next beat', '', '', 'text', 'text', 'Second action', 4),
                  Segment('Ending', '', '', 'text', 'text', 'Ending action', 2)]
    w.resize(1250, 900)
    w.refresh_timeline(1)
    w.show()
    app.processEvents()
    w.project_dirty = False
    yield w
    w._close_saves_queued = True
    w.project_dirty = False
    w.close()
    app.processEvents()


def move_mouse_global(handle, point):
    local = handle.mapFromGlobal(point)
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(local), QPointF(point),
                        Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(handle, event)
    QApplication.processEvents()


@pytest.mark.parametrize('mode', ['ltx', 'minimax_references'])
@pytest.mark.parametrize('fit', [False, True])
def test_actual_divider_drag_updates_both_sides_and_leaves_later_start_fixed(window, mode, fit):
    window.set_project_type(mode)
    window.unified_prompt.setPlainText('Keep the unified prompt')
    window.global_prompt.setPlainText('Keep global direction')
    if fit:
        window.autofit_timeline()
    else:
        window.timeline_scale.setValue(120)
        window.timeline.horizontalScrollBar().setValue(45)
    QApplication.processEvents()
    before = [item.to_dict() for item in window.segments]
    first_card = window.timeline.itemWidget(window.timeline.item(0))
    second_card = window.timeline.itemWidget(window.timeline.item(1))
    last_card = window.timeline.itemWidget(window.timeline.item(2))
    handle = first_card.resize_handle
    assert handle.accessibleName() == 'Move segment boundary'
    start = handle.mapToGlobal(handle.rect().center())
    scale = window.pixels_per_second
    scroll = window.timeline.horizontalScrollBar().value()
    QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=handle.rect().center())
    desired = start + QPoint(round(scale * .75), 0)
    move_mouse_global(handle, desired)
    expected_offset = round(round(scale * .75) / scale * 24) / 24
    assert window.segments[0].duration == pytest.approx(3 + expected_offset, abs=.000001)
    assert window.segments[1].duration == pytest.approx(4 - expected_offset, abs=.000001)
    assert window.total_duration() == 9
    assert window.segments[2].to_dict() == before[2]
    assert last_card.start_time_label.text() == 'START 7.00s'
    assert first_card.duration_label.text() == f'{window.segments[0].duration:.2f}s'
    assert second_card.duration_label.text() == f'{window.segments[1].duration:.2f}s'
    assert first_card is window.timeline.itemWidget(window.timeline.item(0))
    assert window.timeline.horizontalScrollBar().value() == scroll
    # Move back across the original boundary using fixed screen coordinates.
    desired = start - QPoint(round(scale * .5), 0)
    move_mouse_global(handle, desired)
    assert window.segments[0].duration == pytest.approx(2.5, abs=1 / 24)
    assert window.total_duration() == 9
    QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=handle.mapFromGlobal(desired))
    assert window._timeline_edge_drag is None
    assert window.project_dirty
    assert window.global_prompt.toPlainText() == 'Keep global direction'
    if window.unified_workspace:
        assert window.unified_prompt.toPlainText() == 'Keep the unified prompt'
    assert window.duration_spin.value() == pytest.approx(window.segments[1].duration, abs=.01)
    result = [item.to_dict() for item in window.segments]
    window.load_project_payload(window.project_payload(include_media=False))
    assert [item.to_dict() for item in window.segments] == result


def test_boundary_clamp_keeps_actual_footer_and_can_reverse_after_hitting_limit(window):
    window.timeline_scale.setValue(120)
    handle = window.timeline.itemWidget(window.timeline.item(0)).resize_handle
    start = handle.mapToGlobal(handle.rect().center())
    QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=handle.rect().center())
    far = start + QPoint(3000, 0)
    move_mouse_global(handle, far)
    assert window.segments[1].duration == pytest.approx(1 / 24, abs=.000001)
    move_mouse_global(handle, far + QPoint(20, 0))
    card = window.timeline.itemWidget(window.timeline.item(0))
    assert card.duration_label.text() == f'{window.segments[0].duration:.2f}s'
    assert handle.duration == window.segments[0].duration
    move_mouse_global(handle, start)
    assert [item.duration for item in window.segments] == [3, 4, 2]
    QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=handle.mapFromGlobal(start))


def test_boundary_blocks_stale_busy_and_review_changes_and_final_edge_still_resizes(window):
    before = [item.to_dict() for item in window.segments]
    first_id = window.segments[0].id
    for blocked in ('busy', 'review', 'artwork'):
        window.ai_busy = blocked == 'busy'
        window.timeline_review_pending = {first_id} if blocked == 'review' else set()
        window.segments[1].artwork_status = 'generating' if blocked == 'artwork' else ''
        window.begin_timeline_edge_drag(first_id)
        window.resize_timeline_edge(first_id, 4)
        assert [item.duration for item in window.segments] == [3, 4, 2]
    window.segments[1].artwork_status = ''
    window.begin_timeline_edge_drag(first_id)
    window.workspace_revision += 1
    window.resize_timeline_edge(first_id, 4)
    assert [item.to_dict() for item in window.segments] == before
    window.begin_timeline_edge_drag(first_id)
    window.segments[1], window.segments[2] = window.segments[2], window.segments[1]
    window.resize_timeline_edge(first_id, 4)
    assert window.segments[0].duration == 3
    window.segments[1], window.segments[2] = window.segments[2], window.segments[1]
    last = window.segments[-1]
    window.begin_timeline_edge_drag(last.id)
    window.resize_timeline_edge(last.id, 3)
    window.finish_resize(last.id)
    assert window.total_duration() == 10 and window.segments[0].duration == 3


def test_rolling_video_trim_uses_original_drag_snapshot_and_keeps_source_endpoint(window):
    left, right = window.segments[:2]
    for segment, trim in ((left, 24), (right, 96)):
        segment.kind, segment.role = 'video', 'start'
        segment.trim_start, segment.media_duration_frames = trim, 240
    window.refresh_timeline(1)
    window.begin_timeline_edge_drag(left.id)
    for desired, expected_trim in ((4, 120), (3.5, 108), (3, 96), (2.5, 84)):
        window.resize_timeline_edge(left.id, desired)
        assert right.trim_start == expected_trim
        assert right.trim_start / 24 + right.duration == 8
        assert window.total_duration() == 9
    window.finish_resize(left.id)
    assert left.shot_trimmed and right.shot_trimmed


def test_sequence_timing_fits_requested_length_and_preserves_all_written_fields():
    segments = [Segment('a', '', '', 'image', 'end', 'She lifts a lantern.', 3, image_prompt='Blue lighting'),
                Segment('b', '', '', 'text', 'text', 'She says "Follow me." then walks.', 4),
                Segment('c', '', '', 'image', 'start', 'The light fades.', 2)]
    before = [item.to_dict() for item in segments]
    updated = retimed_segments(segments, [2, 7, 3], 12)
    assert [item.duration for item in updated] == [2, 7, 3]
    for original, changed in zip(segments, updated):
        assert {key: value for key, value in original.to_dict().items() if key not in TIMING_FIELDS} == {
            key: value for key, value in changed.to_dict().items() if key not in TIMING_FIELDS}
    assert [item.to_dict() for item in segments] == before


def test_sequence_timing_respects_video_capacity_and_keeps_split_sources_consecutive():
    a = Segment('a', 'same.mp4', '', 'video', duration=2, trim_start=24, media_duration_frames=240)
    b = Segment('b', 'same.mp4', '', 'video', duration=3, trim_start=72, media_duration_frames=240)
    c = Segment('c', '', '', 'text', 'text', 'Last beat.', 1)
    updated = retimed_segments([a, b, c], [10, 10, 1], 12)
    assert [item.duration for item in updated] == [4.5, 4.5, 3]
    assert updated[1].trim_start == updated[0].trim_start + 108
    assert updated[1].trim_start / 24 + updated[1].duration == 10
    assert sum(item.duration for item in updated) == 12


@pytest.mark.parametrize('length', [1.01, 7.33, 12, 15])
def test_sequence_timing_rounding_retains_exact_box_value_and_one_frame_per_part(length):
    source = [Segment(str(i), '', '', 'text', 'text', f'Action {i}', 1) for i in range(9)]
    updated = retimed_segments(source, [.01, 2, 3, .02, 10, 5, .03, 1, 8], length)
    assert sum(item.duration for item in updated) == pytest.approx(length, abs=.000001)
    assert all(item.duration >= 1 / 24 - .000001 for item in updated)


@pytest.mark.parametrize('durations', [None, [], [1], [1, 2, float('nan')], [1, 2, -1], [1, 2, True], [1, 2, '3']])
def test_sequence_timing_rejects_bad_proposals_without_mutating_source(durations):
    segments = [Segment(str(i), '', '', 'text', 'text', 'Keep this prompt.', 3) for i in range(3)]
    original = [item.to_dict() for item in segments]
    with pytest.raises(ValueError):
        retimed_segments(segments, durations, 9)
    assert [item.to_dict() for item in segments] == original


def test_sequence_timing_rejects_unavailable_footage_and_too_short_target():
    source = [Segment(str(i), f'{i}.mp4', '', 'video', duration=1, trim_start=0,
                      media_duration_frames=24) for i in range(2)]
    with pytest.raises(ValueError):
        retimed_segments(source, [1, 1], 3)
    with pytest.raises(ValueError):
        retimed_segments(source, [1, 1], .04)


def test_timing_provider_sees_dialogue_camera_frame_roles_and_only_returns_durations():
    import json
    segments = [Segment('start', '', '', 'image', 'start', 'Push toward the door.', 3),
                Segment('speech', '', '', 'text', 'text', 'She says "Wait for me." in a slow whisper.', 4),
                Segment('end', '', '', 'image', 'end', 'Her hand reaches the latch.', 2)]
    with patch.object(ai, '_provider_raw', return_value=json.dumps(dict(durations=[2, 7, 3], prompt='Do not use this'))) as provider:
        result = plan_sequence_timing(segments, 'gemini', 'model', 'key', 12,
                                      'Keep deliberate pacing.', 'A slower scene.', [None] * 4)
    assert result == dict(durations=[2, 7, 3])
    inputs, _, _, _, rules, _ = provider.call_args.args
    assert [item['prompt'] for item in inputs] == [item.prompt for item in segments]
    supplied = json.loads(rules.split('AUTHORITATIVE TIMELINE AND REFERENCES:\n', 1)[1].split('\nDIRECTOR GUIDANCE:', 1)[0])
    assert [item['prompt'] for item in supplied] == [item.prompt for item in segments]
    assert '12.000000' in rules and 'lip sync' in rules and 'camera movement' in rules
    assert 'All segment and unified prompt text is immutable' in rules
    assert 'No prompt fields' in rules


@pytest.mark.parametrize('approved', [True, False])
def test_retime_button_uses_length_box_slides_tiles_and_reviews_only_timing(window, approved):
    window.set_project_type('minimax_references')
    window.requested_length.setValue(12)
    window.unified_prompt.setPlainText('Keep the unified prompt exactly.')
    window.global_prompt.setPlainText('Keep director guidance exactly.')
    window.intent.setPlainText('Keep the scene and every spoken line.')
    before = [item.to_dict() for item in window.segments]
    cards = [window.timeline.itemWidget(window.timeline.item(i)) for i in range(3)]
    selected = window.timeline.currentRow()
    assert window.retime_all_button.parent() is window.add_tile_wrap
    assert window.add_tile_layout.indexOf(window.retime_all_button) == 3
    assert window.retime_all_button.isVisible() and not window.retime_all_button.icon().isNull()
    with patch.object(window, 'ai_credentials', return_value=('gemini', 'model', 'key')), patch.object(window.thread_pool, 'start') as pool:
        QTest.mouseClick(window.retime_all_button, Qt.MouseButton.LeftButton)
    worker = pool.call_args.args[0]
    assert worker.args[4] == 12
    assert window.ai_request_editor is None and not window.inline_typing.active
    worker.signals.stream.emit('output', '{"durations":[2,')
    assert [item.to_dict() for item in window.segments] == before
    with patch.object(ai, '_provider_raw', return_value='{"durations":[2,7,3],"prompt":"Never apply this"}'):
        worker.signals.finished.emit(worker.operation(*worker.args))
    assert not window.ai_busy and window.paragraph_review.editor is None
    assert [item.duration for item in window.segments] == [2, 7, 3]
    assert window.total_duration() == 12
    assert window.timeline_timing_review_ids == {item.id for item in window.segments}
    assert cards == [window.timeline.itemWidget(window.timeline.item(i)) for i in range(3)]
    assert all(not card.resize_handle.isEnabled() for card in cards)
    assert window.duration_animation is not None
    # A review decision during the slide must stop the animation safely.
    window.resolve_segment_review(window.segments[1].id, approved)
    assert cards == [window.timeline.itemWidget(window.timeline.item(i)) for i in range(3)]
    assert window.timeline.currentRow() == selected
    assert not window.timeline_review_pending
    assert [item.duration for item in window.segments] == ([2, 7, 3] if approved else [3, 4, 2])
    assert window.requested_length.value() == 12
    assert window.unified_prompt.toPlainText() == 'Keep the unified prompt exactly.'
    assert window.global_prompt.toPlainText() == 'Keep director guidance exactly.'
    assert window.intent.toPlainText() == 'Keep the scene and every spoken line.'
    for old, current in zip(before, window.segments):
        assert {key: value for key, value in old.items() if key not in TIMING_FIELDS} == {
            key: value for key, value in current.to_dict().items() if key not in TIMING_FIELDS}


def test_timing_review_reject_keeps_later_manual_prompt_edits(window):
    before = copy.deepcopy(window.segments)
    window.finish_sequence_timing(before, window.active_reference_images(), 12, dict(durations=[2, 7, 3]))
    window.segments[1].prompt = 'Edited manually after the timing proposal.'
    window.resolve_all_segment_reviews(False)
    assert [item.duration for item in window.segments] == [3, 4, 2]
    assert window.segments[1].prompt == 'Edited manually after the timing proposal.'


@pytest.mark.parametrize('outcome', ['failure', 'project_switch', 'timeline_change'])
def test_timing_failure_or_stale_result_preserves_the_sequence(window, outcome):
    original = [item.to_dict() for item in window.segments]
    window.requested_length.setValue(12)
    with patch.object(window, 'ai_credentials', return_value=('gemini', 'model', 'key')), patch.object(window.thread_pool, 'start') as pool:
        window.retime_all_segments()
    worker = pool.call_args.args[0]
    if outcome == 'failure':
        with patch.object(ui.QMessageBox, 'critical'):
            worker.signals.failed.emit('Provider unavailable')
    else:
        if outcome == 'project_switch':
            window.workspace_revision += 1
        else:
            window.segments[1].prompt = 'Latest manual edit'
            original[1]['prompt'] = 'Latest manual edit'
        worker.signals.finished.emit(dict(durations=[2, 7, 3]))
    assert [item.to_dict() for item in window.segments] == original
    assert not window.ai_busy and not getattr(window, 'timeline_review_pending', set())


def test_timing_zero_length_uses_current_total_and_workspace_limit_is_enforced(window):
    window.requested_length.setValue(0)
    with patch.object(window, 'ai_credentials', return_value=('gemini', 'model', 'key')), patch.object(window, 'start_ai_worker') as worker:
        window.retime_all_segments()
    assert worker.call_args.args[1][4] == 9
    window.requested_length.setValue(21)
    with patch.object(window, 'ai_credentials') as credentials, patch.object(window, 'start_ai_worker') as worker:
        window.retime_all_segments()
    credentials.assert_not_called()
    worker.assert_not_called()
    assert window.total_duration() == 9
