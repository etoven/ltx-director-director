"""Continuous production editing, protected structure and selected-section controls."""
from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt, QMimeData
from PySide6.QtGui import QTextCursor, QInputMethodEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTextEdit

from ltx_prompt_director.continuous_prompt_editor import ContinuousPromptEditor
from ltx_prompt_director.spellcheck import _replace

APP = QApplication.instance() or QApplication([])


@pytest.fixture
def editor():
    widget = ContinuousPromptEditor()
    widget.set_parts([
        dict(kind='literal', text='Summary\n'),
        dict(kind='field', key='global', text='A quiet scene.'),
        dict(kind='literal', text='\n\n[00:00–00:03]\n'),
        dict(kind='field', key='first', text='A rider 🐎 enters.'),
        dict(kind='literal', text='\n\n[00:03–00:06]\n'),
        dict(kind='field', key='second', text='The rider leaves.'),
        dict(kind='literal', text='\nEnd.'),
    ])
    widget.show()
    yield widget
    widget.close()


def select(editor, start, end=None):
    cursor = QTextCursor(editor.document())
    cursor.setPosition(start)
    cursor.setPosition(start if end is None else end, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    return cursor


def test_native_typing_unicode_paste_and_undo_preserve_structure(editor):
    field = editor.ranges[1]
    select(editor, field['end'])
    mime = QMimeData(); mime.setText('\nThe café glows 🌤.')
    editor.insertFromMimeData(mime)
    assert editor.field_text('first') == 'A rider 🐎 enters.\nThe café glows 🌤.'
    assert editor.field_text('second') == 'The rider leaves.'
    before = editor.toPlainText()
    editor.set_parts([dict(kind='literal', text='Summary\n'),
                      dict(kind='field', key='global', text=editor.field_text('global')),
                      dict(kind='literal', text='\n\n[00:00–00:03]\n'),
                      dict(kind='field', key='first', text=editor.field_text('first')),
                      dict(kind='literal', text='\n\n[00:03–00:06]\n'),
                      dict(kind='field', key='second', text=editor.field_text('second')),
                      dict(kind='literal', text='\nEnd.')])
    editor.undo()
    assert editor.field_text('first') == 'A rider 🐎 enters.'
    editor.redo()
    assert editor.toPlainText() == before


@pytest.mark.parametrize('operation', ['typing', 'backspace', 'delete', 'paste', 'cut', 'cursor', 'spell', 'ime'])
def test_protected_heading_rejects_every_mutation(editor, operation):
    before = editor.toPlainText()
    cursor = select(editor, 1, 4)
    if operation == 'typing': QTest.keyClicks(editor, 'oops')
    elif operation == 'backspace': QTest.keyClick(editor, Qt.Key.Key_Backspace)
    elif operation == 'delete': QTest.keyClick(editor, Qt.Key.Key_Delete)
    elif operation == 'paste':
        mime=QMimeData(); mime.setText('oops'); editor.insertFromMimeData(mime)
    elif operation == 'cut': editor.cut()
    elif operation == 'cursor': cursor.insertText('oops')
    elif operation == 'spell': _replace(editor, cursor, 'oops')
    elif operation == 'ime':
        event=QInputMethodEvent(); event.setCommitString('oops'); QApplication.sendEvent(editor,event)
    assert editor.toPlainText() == before


def test_mixed_selection_and_field_boundary_deletes_are_atomic(editor):
    before=editor.toPlainText()
    first,second=editor.ranges[1:]
    select(editor,first['start']+2,second['end'])
    QTest.keyClicks(editor,'replacement')
    assert editor.toPlainText()==before
    select(editor,first['start'])
    QTest.keyClick(editor,Qt.Key.Key_Backspace)
    select(editor,first['end'])
    QTest.keyClick(editor,Qt.Key.Key_Delete)
    assert editor.toPlainText()==before


def test_valid_spelling_input_method_and_empty_fields(editor):
    field=editor.ranges[1]
    cursor=select(editor,field['start'],field['end'])
    _replace(editor,cursor,'Correct spelling')
    assert editor.field_text('first')=='Correct spelling'
    field=editor.ranges[1]
    select(editor,field['start'],field['end'])
    QTest.keyClick(editor,Qt.Key.Key_Backspace)
    assert editor.field_text('first')==''
    event=QInputMethodEvent();event.setCommitString('日本語 🐎')
    QApplication.sendEvent(editor,event)
    assert editor.field_text('first')=='日本語 🐎'


@pytest.fixture
def panel():
    from ltx_prompt_director import ui
    from ltx_prompt_director.models import Segment
    with patch.object(ui.MainWindow,'restore_startup_workspace'):
        window=ui.MainWindow()
    window._close_saves_queued=True
    window.segments=[Segment('Opening','','','text','text','First action.',3),
                     Segment('Ending','','','text','text','Last action.',4)]
    window.refresh_timeline(0)
    window.set_project_type('minimax_official_skill_reference')
    from ltx_prompt_director.output_templates import default_layout
    window.workspace_definition['output_template']=default_layout('ltx')+'\nSound:\n${sound}'
    panel=window.prompt_surface.structured
    panel.refresh();panel.reveal_timer.stop()
    yield panel
    panel.finish_reviews();window.project_dirty=False;window.close()


def test_one_editor_syncs_fields_and_timeline_navigation(panel):
    window=panel.owner
    assert panel.findChildren(QTextEdit)==[panel.editor]
    assert panel.editor._spell_highlighter
    for key in list(panel.rows):
        panel.rows[key][0].setPlainText('Edited '+key)
    panel.refresh()
    assert panel.review_snapshot()=={key:'Edited '+key for key in panel.rows}
    assert window.segments[0].prompt=='Edited '+window.segments[0].id
    window.timeline.setCurrentRow(1);panel.refresh();panel.reveal_selected()
    assert panel.active_key==window.segments[1].id
    assert panel.editor.textCursor().position()==next(r['start'] for r in panel.editor.ranges if r['key']==panel.active_key)
    assert panel.editor.extraSelections()
    assert not panel.time_fields[0].isHidden()
    from ltx_prompt_director.output_templates import stamp
    assert panel.time_fields[0].text()==stamp(3)
    assert panel.remove.isEnabled()


def test_refresh_keeps_selection_scroll_and_typing_undo(panel):
    key=panel.owner.segments[0].id
    field=next(r for r in panel.editor.ranges if r['key']==key)
    select(panel.editor,field['start'])
    QTest.keyClicks(panel.editor,'New ')
    cursor=panel.editor.textCursor(); position=cursor.position()
    panel.reveal_timer.stop();panel.selected_id=key;panel.reveal_pending=False
    scroll=panel.editor.verticalScrollBar().value()
    panel.refresh()
    assert panel.editor.textCursor().position()==position
    assert panel.editor.verticalScrollBar().value()==scroll
    panel.editor.undo()
    assert panel.owner.segments[0].prompt=='First action.'


def test_refinement_reviews_are_section_bounded_and_reject_to_model(panel):
    key=panel.owner.segments[0].id
    before=panel.review_snapshot()
    panel.state()['prompts'][key]='Revised action.'
    panel.state()['sources'][key]=panel.owner.segments[0].prompt
    panel.refresh(refinement_baseline=before)
    assert panel.owner.segments[0].prompt=='Revised action.'
    panel.review_refinement(before)
    assert panel.review.pending_count
    assert panel.editor.isReadOnly()
    assert panel.review.source_document.toPlainText()==panel.owner.unified_prompt.toPlainText()
    panel.review.resolve_all(False)
    panel.refresh()
    assert panel.owner.segments[0].prompt=='First action.'
    assert panel.editor.field_text(key)=='First action.'
    assert not panel.editor.isReadOnly()
    assert panel.editor._spell_highlighter.document() is panel.editor.document()


def test_selected_controls_subjects_busy_and_save_reopen(panel):
    window=panel.owner
    panel.add_subject();panel.refresh()
    subject=next(key for key in panel.rows if key.startswith('subject:'))
    panel.rows[subject][0].setPlainText('A persistent subject definition.')
    panel.refresh()
    panel.rows[subject][0].setFocus()
    assert panel.active_key==subject
    assert panel.time_fields[0].isHidden() and not panel.remove.isHidden()
    payload=window.project_payload(include_media=False)
    window.load_project_payload(payload);panel.refresh()
    assert panel.editor.field_text(subject)=='A persistent subject definition.'
    panel.rows[subject][0].setFocus()
    window.ai_busy=True;panel.refresh()
    before=panel.editor.toPlainText()
    QTest.keyClicks(panel.editor,'blocked')
    assert panel.editor.toPlainText()==before and not panel.remove.isEnabled()
    window.ai_busy=False;panel.refresh()
    panel.active_key=subject;panel.remove.click();panel.refresh()
    assert subject not in panel.rows


def test_time_controls_follow_cursor_and_add_remove_targets(panel):
    from ltx_prompt_director.output_templates import stamp
    window=panel.owner
    second=window.segments[1].id
    panel.rows[second][0].setFocus()
    panel.time_fields[1].setText(stamp(8))
    panel.time_fields[1].textEdited.emit(stamp(8))
    panel.refresh()
    assert window.segments[1].duration==5
    panel.time_fields[0].setText(stamp(2))
    panel.time_fields[0].textEdited.emit(stamp(2))
    panel.refresh()
    assert [s.duration for s in window.segments]==[2,6]
    assert stamp(8) in panel.editor.toPlainText()
    panel.add_segment();panel.refresh()
    added=window.segments[-1].id
    panel.rows[added][0].setFocus();panel.remove.click();panel.refresh()
    assert added not in panel.rows and second in panel.rows


def test_timeline_reveal_scrolls_during_review_without_losing_diff(panel):
    window=panel.owner
    window.show();panel.editor.setFixedSize(420,180)
    first,second=window.segments
    panel.rows[first.id][0].setPlainText('The rider crosses the forest.\n'*60)
    panel.refresh()
    baseline=panel.review_snapshot()
    panel.state()['prompts'][second.id]='A revised final action.'
    panel.refresh(refinement_baseline=baseline);panel.review_refinement(baseline)
    assert panel.review.pending_count
    window.timeline.setCurrentRow(1);panel.refresh();panel.reveal_selected()
    assert panel.editor.verticalScrollBar().value()>0
    assert panel.active_key==second.id
    assert panel.review.pending_count and panel.editor.isReadOnly()
    panel.review.resolve_all(True);panel.refresh()
    assert window.segments[1].prompt=='A revised final action.'


def test_stale_refinement_snapshot_cannot_review_a_different_project(panel):
    baseline=panel.review_snapshot()
    panel.owner.current_project_id='another-project'
    panel.refresh();panel.review_refinement(baseline)
    assert panel.review.editor is None


def test_text_drop_and_effect_drop_respect_pointer_section(panel):
    from PySide6.QtCore import QPointF
    from ltx_prompt_director import ui
    from ltx_prompt_director.effects_library import MIME
    window=panel.owner
    window.show();panel.reveal_timer.stop()
    first=window.segments[0]
    section=next(r for r in panel.editor.ranges if r['key']==first.id)
    select(panel.editor,section['start'])
    panel.editor.ensureCursorVisible()
    point=panel.editor.cursorRect().center()
    mime=QMimeData();mime.setText('Dropped ')
    drop=QDropEvent(QPointF(point),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    panel.editor.dropEvent(drop)
    assert first.prompt.startswith('Dropped ')
    select(panel.editor,0)
    panel.editor.ensureCursorVisible()
    before=panel.editor.toPlainText()
    drop=QDropEvent(QPointF(panel.editor.cursorRect().center()),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    panel.editor.dropEvent(drop)
    assert panel.editor.toPlainText()==before
    select(panel.editor,next(r['start'] for r in panel.editor.ranges if r['key']==first.id))
    panel.editor.ensureCursorVisible()
    mime=QMimeData();mime.setData(MIME,b'example-effect')
    drop=QDropEvent(QPointF(panel.editor.cursorRect().center()),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    entry=dict(id='example-effect',target='segment',prompt='Move gracefully.')
    with patch.object(ui,'load_effects',return_value=[entry]),patch.object(window,'quick_fix_available',return_value=True),patch.object(window,'apply_library_effect') as apply:
        assert panel.eventFilter(panel.editor.viewport(),drop)
        apply.assert_called_once()
        assert window.current_segment().id==first.id


def test_prompt_only_keeps_unrestricted_text_and_roundtrip(panel):
    window=panel.owner
    window.set_project_type('minimax_prompt_refinement')
    assert window.prompt_only_workspace
    assert window.prompt_surface.unified_stack.currentWidget() is window.unified_prompt
    window.unified_prompt.setPlainText('Free text\n/keep Identity\n/refine Make this clearer')
    cursor=window.unified_prompt.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    window.unified_prompt.setTextCursor(cursor)
    QTest.keyClicks(window.unified_prompt,' please')
    draft=window.unified_prompt.toPlainText()
    payload=window.project_payload(include_media=False)
    window.load_project_payload(payload)
    assert window.unified_prompt.toPlainText()==draft
    window.set_project_type('ltx')
    window.set_project_type('minimax_prompt_refinement')
    assert window.unified_prompt.toPlainText()==draft
