"""Splitting keeps the timeline and source intervals intact across workspaces."""
from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from ltx_prompt_director import ui
from ltx_prompt_director.models import Segment, split_segment
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
    actions = []
    def add_action(text, *args):
        action = ui.QAction(text, window)
        actions.append(action)
        return action
    def choose(*args):
        action = next(a for a in actions if a.text() == "Split segment here")
        assert action.isEnabled()
        action.trigger()
    with patch.object(ui, "QMenu") as menu:
        menu.return_value.addAction.side_effect = add_action
        menu.return_value.exec.side_effect = choose
        window.timeline_menu(point)
    assert len(window.segments) == 4
    assert window.segments[1].duration == pytest.approx(1, abs=1 / 24)
    assert sum(s.duration for s in window.segments) == pytest.approx(6)


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
