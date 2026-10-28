"""Manual segment typing previews only its own production passage."""
from unittest.mock import patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor, QTextCharFormat
from PySide6.QtTest import QTest

from test_prompt_timecodes import app, window
from ltx_prompt_director.prompt_sections import prompt_sections, document_sections
from ltx_prompt_director.prompt_tags import insert_note


@pytest.mark.parametrize('separator', [', ', ' ', ': ', ',  , '])
def test_shot_boundaries_preserve_prefix_and_blank_line_separator(separator):
    text = f'[Shot 1] At 00:00.00{separator}First action.\n\n[Shot 2] At 00:02.00, Last action.\n\noverall_soundscape: Wind.'
    a, b = prompt_sections(text)
    assert text[a.start:a.end] == f'00:00.00{separator}First action.'
    assert text[a.body_start:a.end] == 'First action.'
    assert text[b.body_start:b.end] == 'Last action.'
    assert text[a.end:b.start].startswith('\n\n[Shot 2] At ')


def test_untimed_first_shot_and_reference_preamble_do_not_shift_shots():
    text = ('Picture 1 (from [Shot 2]) aligns at 00:05.00.\n\n'
            'integrated_multimodal_description: [Shot 1] At dawn, a traveler walks.\n\n'
            '[Shot 2] At 00:02.00, Turn toward the light.\n\n'
            'overall_soundscape: Wind at 00:04.00.')
    a, b = prompt_sections(text)
    assert (a.index, b.index) == (0, 1)
    assert text[a.body_start:a.end] == 'At dawn, a traveler walks.'
    assert not a.codes
    assert text[b.body_start:b.end] == 'Turn toward the light.'
    assert len(b.codes) == 1


def test_ranges_and_partial_briefs_do_not_swallow_other_sections():
    text = '[TIMED ACTION]\n00:00.00 - 00:02.00: Walk.\n\n00:02.00 - 00:05.00: Turn.\n\n[SOUND]\nWind at 00:04.00.'
    a, b = prompt_sections(text)
    assert text[a.body_start:a.end] == 'Walk.'
    assert text[b.body_start:b.end] == 'Turn.'
    assert len(a.codes) == 2
    assert prompt_sections('Broken 00:xx.xx') == []


BRIEF = ('[Shot 1] At 00:00.00, Walk slowly.\n\n'
         '[Shot 2] At 00:02.00, Turn toward the light.\n\n'
         'overall_soundscape: Wind.\n\nnon_diegetic_music: N/A')


def type_segment(window, app, text, row=0):
    window.set_director_intent_expanded(True, persist=False)
    window.prompt_surface.select('segment')
    window.timeline.setCurrentRow(row)
    window.reload_clicked_segment(window.timeline.item(row))
    editor = window.intent_segment_prompt
    editor.setFocus()
    app.processEvents()
    assert editor.hasFocus()
    editor.selectAll()
    QTest.keyClicks(editor, text)
    app.processEvents()
    return editor


def test_live_diff_is_scoped_and_only_approval_changes_unified_model(window, app):
    window.unified_prompt.setPlainText(BRIEF)
    with patch.object(window, 'start_ai_worker') as worker:
        source = type_segment(window, app, 'Run quickly.')
        review = window.segment_prompt_review
        assert review.active
        assert window.minimax_prompt_text == BRIEF
        assert window.project_payload(include_media=False)['minimaxH3']['prompt'] == BRIEF
        projection = window.unified_prompt.toPlainText()
        assert '[Shot 2] At 00:02.00, Turn toward the light.' in projection
        assert projection.endswith('overall_soundscape: Wind.\n\nnon_diegetic_music: N/A')
        colors = set()
        block = window.unified_prompt.document().firstBlock()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid():
                    colors.add(fragment.charFormat().background().color().name())
                iterator += 1
            block = block.next()
        assert {'#80e991', '#9c3f4b'} <= colors
        QTest.mouseClick(review.buttons[0], Qt.MouseButton.LeftButton)
        app.processEvents()
    assert not worker.called
    assert not review.active
    expected = BRIEF.replace('Walk slowly.', 'Run quickly.')
    assert window.unified_prompt.toPlainText() == window.minimax_prompt_text == expected
    assert source.toPlainText() == window.segments[0].prompt == 'Run quickly.'
    window.unified_prompt.undo()
    assert window.minimax_prompt_text == BRIEF


@pytest.mark.parametrize('action', ['reject', 'focus_out', 'segment_switch', 'scope_switch', 'timing_edit'])
def test_unapproved_preview_is_discarded_but_segment_edit_is_kept(window, app, action):
    window.unified_prompt.setPlainText(BRIEF)
    type_segment(window, app, 'Run quickly.')
    review = window.segment_prompt_review
    assert review.active
    if action == 'reject':
        QTest.mouseClick(review.buttons[1], Qt.MouseButton.LeftButton)
    elif action == 'focus_out':
        QTest.mouseClick(window.unified_prompt.viewport(), Qt.MouseButton.LeftButton)
    elif action == 'segment_switch':
        window.timeline.setCurrentRow(1)
    elif action == 'scope_switch':
        window.prompt_scope.setCurrentIndex(window.prompt_scope.findData('global'))
    else:
        window.change_duration(window.segments[0].id, 3)
    app.processEvents()
    assert not review.active
    expected = BRIEF.replace('00:02.00', '00:03.00') if action == 'timing_edit' else BRIEF
    assert window.unified_prompt.toPlainText() == window.minimax_prompt_text == expected
    assert window.segments[0].prompt == 'Run quickly.'


def test_repeated_typing_uses_original_baseline_and_second_shot_only(window, app):
    window.unified_prompt.setPlainText(BRIEF)
    source = type_segment(window, app, 'New action', row=1)
    QTest.keyClicks(source, ' continues.')
    app.processEvents()
    review = window.segment_prompt_review
    assert review.active
    assert review.original == 'Turn toward the light.'
    review.approve()
    assert window.minimax_prompt_text == BRIEF.replace('Turn toward the light.', 'New action continues.')


def test_external_replacement_cannot_be_overwritten_by_pending_preview(window, app):
    window.unified_prompt.setPlainText(BRIEF)
    type_segment(window, app, 'Run quickly.')
    window.unified_prompt.setPlainText('An external production draft')
    assert not window.segment_prompt_review.active
    assert window.minimax_prompt_text == 'An external production draft'
    window.segment_prompt_review.approve()
    assert window.minimax_prompt_text == 'An external production draft'


def test_native_notes_outside_the_passage_survive_approval(window, app):
    editor = window.unified_prompt
    editor.setPlainText(BRIEF)
    cursor = QTextCursor(editor.document())
    insert_note(editor, cursor, '/keep', 'Protect 📽 at 00:59.00 and [Shot 9]')
    before = editor.toPlainText()
    assert [s.index for s in document_sections(editor)] == [0, 1]
    type_segment(window, app, 'Run quickly.')
    window.segment_prompt_review.approve()
    assert editor.toPlainText() == before.replace('Walk slowly.', 'Run quickly.')
    assert '/keep Protect 📽 at 00:59.00 and [Shot 9]' in editor.toPlainText()


@pytest.mark.parametrize('approve',[True,False])
def test_spelling_survives_local_diff_with_native_notes(window,app,approve):
    from test_spellcheck_diff import Dictionary,assert_spelling
    editor=window.unified_prompt
    checker=editor._spell_highlighter
    checker.dictionary=Dictionary();checker._known.clear()
    editor.setPlainText(BRIEF.replace('Walk slowly.','Walk spelingg.').replace('Turn toward the light.','🙂 mispelingg.'))
    insert_note(editor,QTextCursor(editor.document()),'/keep','Protect 📽 spelingg')
    before=editor.toPlainText()
    source=editor.document()
    checker.rehighlight();assert_spelling(editor,app)
    type_segment(window,app,'Run spelingg.')
    review=window.segment_prompt_review
    assert review.active
    (review.approve if approve else review.reject)()
    assert editor.document() is source and checker.document() is source
    assert editor._spell_highlighter is checker
    assert editor.toPlainText()==(before.replace('Walk spelingg.','Run spelingg.') if approve else before)
    assert '/keep Protect 📽 spelingg' in editor.toPlainText()
    assert_spelling(editor,app)


def test_soft_sections_exclude_newline_gaps_and_scrolling_uses_shot_numbers(window, app):
    window.unified_prompt.setPlainText('[Shot 1] Untimed opening.\n\n[Shot 2] At 00:02.00, Turn.')
    window.timeline.setCurrentRow(1)
    window.reload_clicked_segment(window.timeline.item(1))
    sections = [s.cursor.selectedText() for s in window.unified_prompt.extraSelections()
                if s.format.property(int(QTextCharFormat.Property.UserProperty) + 32)]
    assert sections == ['Untimed opening.', '00:02.00, Turn.']
    assert window.unified_prompt.textCursor().position() == len('[Shot 1] Untimed opening.\n\n[Shot 2] At 00:02.00')


def test_timecode_updates_respect_untimed_first_shot(window):
    window.unified_prompt.setPlainText('[Shot 1] Untimed opening.\n\n[Shot 2] At 00:02.00, Turn.')
    window.change_duration(window.segments[0].id, 3)
    assert window.minimax_prompt_text == '[Shot 1] Untimed opening.\n\n[Shot 2] At 00:03.00, Turn.'


def test_clicking_copy_denies_before_clipboard_action_even_without_focus_change(window, app):
    from PySide6.QtWidgets import QApplication
    window.unified_prompt.setPlainText(BRIEF)
    type_segment(window, app, 'Run quickly.')
    button = window.minimax_panel.copy_button
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert not window.segment_prompt_review.active
    assert QApplication.clipboard().text() == BRIEF


def test_ai_start_discards_pending_local_diff(window, app):
    window.unified_prompt.setPlainText(BRIEF)
    type_segment(window, app, 'Run quickly.')
    window.set_ai_controls_enabled(False)
    assert not window.segment_prompt_review.active
    assert window.unified_prompt.toPlainText() == window.minimax_prompt_text == BRIEF
    window.set_ai_controls_enabled(True)


def test_pressing_approve_survives_focus_change_until_mouse_release(window, app):
    window.unified_prompt.setPlainText(BRIEF)
    type_segment(window, app, 'Run quickly.')
    review = window.segment_prompt_review
    button = review.buttons[0]
    QTest.mousePress(button, Qt.MouseButton.LeftButton)
    QTest.qWait(60)
    assert review.active
    QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert window.minimax_prompt_text == BRIEF.replace('Walk slowly.', 'Run quickly.')
