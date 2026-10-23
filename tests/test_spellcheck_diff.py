"""Spell underlines survive temporary documents and plain diff cleanup."""
from unittest.mock import patch

import pytest
from PySide6.QtGui import QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QTextEdit

from ltx_prompt_director.ai_inline_diff import LiveTypingPreview
from ltx_prompt_director.diff_review import ParagraphDiffReview, reset_plain_review_text
from ltx_prompt_director.prompt_timecodes import utf16_length
from ltx_prompt_director.spellcheck import install_spellcheck


class Dictionary:
    def check(self,word):return word.casefold() not in {'spelingg','mispelingg'}
    def suggest(self,word):return ['spelling']
    def add_to_session(self,word):pass


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def editor(app):
    text=QTextEdit();text.resize(600,300);text.show()
    with patch('ltx_prompt_director.spellcheck.system_dictionary',return_value=Dictionary()):
        install_spellcheck(text)
    yield text,app
    text.close()


def underlines(editor,app):
    app.processEvents()
    found=[];block=editor.document().firstBlock()
    while block.isValid():
        for span in block.layout().formats():
            if span.format.underlineStyle()==QTextCharFormat.UnderlineStyle.SpellCheckUnderline:
                found.append((block.position()+span.start,span.length))
        block=block.next()
    return found


def assert_spelling(editor,app):
    actual=underlines(editor,app)
    for typo in ('spelingg','mispelingg'):
        cursor=editor.document().find(typo)
        if not cursor.isNull():assert (cursor.selectionStart(),utf16_length(typo)) in actual


def test_spelling_and_directives_use_qt_positions_after_emoji(editor):
    text,app=editor
    text.setPlainText('📽 /refine spelingg\n\n🙂 mispelingg')
    assert_spelling(text,app)
    spans=text.document().firstBlock().layout().formats()
    assert any(span.start==3 and span.length==7 and span.format.background().color().name()=='#245163' for span in spans)


def test_plain_cleanup_keeps_checker_after_document_replacement(editor):
    text,app=editor
    checker=text._spell_highlighter
    replacement=QTextDocument(text);replacement.setPlainText('spelingg\n\nmispelingg')
    text.setDocument(replacement)
    notifications=QSignalSpy(text.textChanged)
    reset_plain_review_text(text,replacement.toPlainText())
    assert checker.document() is replacement
    assert_spelling(text,app)
    assert notifications.count()==0
    assert install_spellcheck(text) is checker
    cursor=text.textCursor();cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText(' spelingg');app.processEvents()
    assert (cursor.position()-8,8) in underlines(text,app)


@pytest.mark.parametrize('approve',[True,False])
def test_paragraph_review_cleanup_preserves_underlines_and_clears_diff_styling(editor,approve):
    text,app=editor
    before='📽 spelingg original.\n\nmispelingg unchanged.'
    after=before.replace('original','revised')
    text.setPlainText(after)
    source=text.document();checker=text._spell_highlighter
    review=ParagraphDiffReview(text)
    assert review.begin(text,before,after)
    app.processEvents();review.resolve_all(approve)
    assert text.document() is source and checker.document() is source
    assert text.toPlainText()==(after if approve else before)
    assert_spelling(text,app)
    cursor=text.document().find('revised' if approve else 'original')
    assert not cursor.charFormat().fontStrikeOut()
    assert cursor.charFormat().background().style().value==0
    assert not text.currentCharFormat().fontStrikeOut()


def test_streaming_projection_restores_spelling_without_saving_preview(editor):
    text,app=editor
    baseline='📽 spelingg original.\n\nmispelingg unchanged.'
    text.setPlainText(baseline);assert_spelling(text,app)
    source=text.document();checker=text._spell_highlighter
    preview=LiveTypingPreview(text)
    for _ in range(2):
        preview.start(text,baseline)
        preview.receive('output','{"prompt":"📽 spelingg revised."}')
        preview.render();app.processEvents()
        assert text.document() is not source
        preview.finish()
        assert text.document() is source and checker.document() is source
        assert text.toPlainText()==baseline
        assert_spelling(text,app)
