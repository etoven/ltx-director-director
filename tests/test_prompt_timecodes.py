"""Unified prompt timing follows manual timeline edits without provider calls."""
from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ltx_prompt_director import ui
from ltx_prompt_director.models import Segment
from ltx_prompt_director.prompt_tags import insert_note
from ltx_prompt_director.prompt_timecodes import (
    document_cues, format_timecode, retime_prompt, timecodes, utf16_length,
)
from ltx_prompt_director.workspaces import WorkspaceStore


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(tmp_path, monkeypatch, app):
    monkeypatch.setattr(ui, 'WorkspaceStore', lambda: WorkspaceStore(tmp_path / 'workspaces'))
    monkeypatch.setattr(ui, 'project_library_path', lambda: tmp_path / 'projects')
    monkeypatch.setattr(ui.MainWindow, 'restore_startup_workspace', lambda self: None)
    w = ui.MainWindow()
    w.set_project_type('minimax_base_guide')
    w.segments = [Segment('Opening', '', '', 'text', 'text', 'Walk', 2),
                  Segment('Ending', '', '', 'text', 'text', 'Turn', 3)]
    w.refresh_timeline(0)
    w.resize(1100, 800)
    w.show()
    app.processEvents()
    yield w
    w.project_dirty = False
    w._close_saves_queued = True
    w.close()
    app.processEvents()


@pytest.mark.parametrize('seconds,template,expected', [
    (59.999, '00:00.00', '01:00.00'),
    (3600, '00:00:00', '01:00:00'),
    (59.999, '00:00:00:00', '00:01:00:00'),
    (2.125, '00:00.000', '00:02.125'),
    (2.125, '00:00:00:00', '00:00:02:03'),
])
def test_format_preserves_notation_with_carry(seconds, template, expected):
    assert format_timecode(seconds, template) == expected


def test_broken_codes_are_not_partially_recognized():
    text = '00:99.00 / 00:00:99:00 / 00:00:01:30 / 00:0x.00 / 00:00:xx / 00:00:02 / 00:04.00'
    assert [m.group() for m in timecodes(text)] == ['00:00:02', '00:04.00']


def test_ranges_mixed_formats_and_broken_entries_continue_in_order(window):
    editor = window.unified_prompt
    text = ('📽 00:10.00–00:12.00: Walk.\nBroken 00:xx.xx\n'
            '00:00:20 — 00:00:23: Turn.\n00:50.00 Extra cue')
    editor.setPlainText(text)
    before_segments = [s.to_dict() for s in window.segments]
    window.global_prompt.setPlainText('Global 00:50.00')
    with patch.object(window, 'start_ai_worker') as worker:
        window.change_duration(window.segments[0].id, 4)
    assert not worker.called
    assert editor.toPlainText() == ('📽 00:00.00–00:04.00: Walk.\nBroken 00:xx.xx\n'
                                    '00:00:04 — 00:00:07: Turn.\n00:50.00 Extra cue')
    assert window.minimax_prompt_text == editor.toPlainText()
    assert window.global_prompt.toPlainText() == 'Global 00:50.00'
    assert [s.prompt for s in window.segments] == [s['prompt'] for s in before_segments]
    assert window.project_payload(include_media=False)['minimaxH3']['prompt'] == editor.toPlainText()


def test_typing_highlights_without_retiming_and_can_repair_a_code(window, app):
    editor = window.unified_prompt
    editor.setPlainText('00:50.00 First\n00:xx.xx Broken\n00:51.00 Last')
    app.processEvents()
    assert '00:50.00' in editor.toPlainText()
    formats = editor.document().firstBlock().layout().formats()
    assert any(f.start == 0 and f.length == 8 and f.format.background().color().name() == '#245163'
               for f in formats)
    window.change_duration(window.segments[0].id, 4)
    assert editor.toPlainText() == '00:00.00 First\n00:xx.xx Broken\n00:04.00 Last'
    editor.setPlainText('00:50.00 First\n00:51.00 Repaired\n00:52.00 Extra')
    window.change_duration(window.segments[0].id, 3)
    assert editor.toPlainText() == '00:00.00 First\n00:03.00 Repaired\n00:52.00 Extra'


def test_resize_keeps_caret_selection_focus_scroll_and_undo(window, app):
    editor = window.unified_prompt
    text = '📽 00:50.00 First\n' + ('Untimed prose\n' * 60) + '00:51.00 Last'
    editor.setPlainText(text)
    editor.setFocus()
    cursor = editor.textCursor()
    start = utf16_length(text[:text.index('Untimed')])
    cursor.setPosition(start + 7)
    cursor.setPosition(start, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    editor.verticalScrollBar().setValue(90)
    app.processEvents()
    scroll = editor.verticalScrollBar().value()
    window.change_duration(window.segments[0].id, 4)
    assert editor.textCursor().position() == start
    assert editor.textCursor().anchor() == start + 7
    assert editor.textCursor().selectedText() == 'Untimed'
    assert editor.hasFocus()
    assert editor.verticalScrollBar().value() == scroll
    editor.undo()
    assert editor.toPlainText() == text
    assert window.minimax_prompt_text == text


def test_boundary_drag_updates_prompt_before_release_and_repeated_moves(window):
    editor = window.unified_prompt
    editor.setPlainText('00:00.00 - 00:02.00 Walk\n00:02.00 - 00:05.00 Turn')
    first = window.segments[0].id
    window.begin_timeline_edge_drag(first)
    window.resize_timeline_edge(first, 3)
    assert window._timeline_edge_drag is not None
    assert editor.toPlainText() == '00:00.00 - 00:03.00 Walk\n00:03.00 - 00:05.00 Turn'
    window.resize_timeline_edge(first, 1)
    assert editor.toPlainText() == '00:00.00 - 00:01.00 Walk\n00:01.00 - 00:05.00 Turn'
    window.finish_resize(first)


def test_click_navigates_after_start_code_even_when_already_selected(window, app):
    editor = window.unified_prompt
    editor.setPlainText('00:00.00 First\n' + 'Prose\n' * 90 + '00:02.00 - 00:05.00 Last')
    editor.verticalScrollBar().setValue(0)
    item = window.timeline.item(1)
    QTest.mouseClick(window.timeline.viewport(), Qt.MouseButton.LeftButton,
                     pos=window.timeline.visualItemRect(item).center())
    expected = list(document_cues(editor))[1][0][0] + 8
    assert editor.textCursor().position() == expected
    assert editor.verticalScrollBar().value() > 0
    assert [s.cursor.selectedText() for s in editor.extraSelections()
            if not s.format.property(int(s.format.Property.UserProperty) + 32)] == ['00:02.00', '00:05.00']
    cursor = editor.textCursor()
    cursor.setPosition(0)
    editor.setTextCursor(cursor)
    window.reload_clicked_segment(item)
    assert editor.textCursor().position() == expected
    # Missing cues are harmless and do not move the cursor.
    editor.setPlainText('No codes')
    window.reload_clicked_segment(item)
    assert editor.textCursor().position() == 0
    assert not editor.extraSelections()


def test_reorder_uses_new_cumulative_segment_durations(window):
    editor = window.unified_prompt
    editor.setPlainText('00:00.00 - 00:02.00 First\n00:02.00 - 00:05.00 Last')
    item = window.timeline.takeItem(1)
    window.timeline.insertItem(0, item)
    window.sync_order()
    assert [s.duration for s in window.segments] == [3, 2]
    assert editor.toPlainText() == '00:00.00 - 00:03.00 First\n00:03.00 - 00:05.00 Last'


def test_native_instruction_notes_and_unicode_survive(window):
    editor = window.unified_prompt
    editor.setPlainText('📽 00:50.00 First\n00:51.00 Last\n')
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    insert_note(editor, cursor, '/keep', 'Hold 00:59.00 as written')
    assert retime_prompt(editor, [4, 3])
    assert editor.toPlainText() == '📽 00:00.00 First\n00:04.00 Last\n/keep Hold 00:59.00 as written'
    assert len(list(document_cues(editor))) == 2


def test_length_changing_codes_keep_caret_attached_to_prose(window):
    editor = window.unified_prompt
    editor.setPlainText('99:59.99 First\n99:59.99 Last')
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    editor.setTextCursor(cursor)
    assert retime_prompt(editor, [6000, 1])
    assert editor.toPlainText() == '00:00.00 First\n100:00.00 Last'
    assert editor.textCursor().position() == utf16_length(editor.toPlainText())


def test_start_end_mode_uses_correct_checkpoint_without_provider(window):
    editor = window.unified_prompt
    for segment in window.segments:
        segment.kind, segment.role = 'image', 'start'
    editor.setPlainText('00:00.00 First\n00:02.00 Last')
    window.timeline.setCurrentRow(1)
    before_global = window.global_prompt.toPlainText()
    with patch.object(window, 'start_ai_worker') as worker:
        window.set_role('end')
        assert editor.toPlainText() == '00:00.00 First\n00:05.00 Last'
        window.change_duration(window.segments[1].id, 4)
        assert editor.toPlainText() == '00:00.00 First\n00:06.00 Last'
        window.set_role('start')
        assert editor.toPlainText() == '00:00.00 First\n00:02.00 Last'
    assert not worker.called
    assert window.global_prompt.toPlainText() == before_global


def test_range_keeps_both_boundaries_and_end_mode_click_follows_end(window):
    editor = window.unified_prompt
    window.segments[1].kind = 'image'
    window.timeline.setCurrentRow(1)
    editor.setPlainText('00:00.00 - 00:02.00 First\n00:02.00 - 00:05.00 Last')
    window.set_role('end')
    window.reload_clicked_segment(window.timeline.item(1))
    assert editor.toPlainText() == '00:00.00 - 00:02.00 First\n00:02.00 - 00:05.00 Last'
    assert editor.textCursor().position() == editor.toPlainText().index('00:05.00') + 8
